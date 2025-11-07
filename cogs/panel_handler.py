import discord
from discord.ext import commands, tasks
import logging
from cogs.moderation import _mute_member, _issue_warning, _ban_member
import config
import database

log = logging.getLogger(__name__)

class PanelHandlerCog(commands.Cog, name="Panel Handler"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.process_action_queue.start()

    def cog_unload(self):
        self.process_action_queue.cancel()

    @tasks.loop(seconds=2)
    async def process_action_queue(self):
        try:
            task = await self.bot.action_queue.get()
            guild = self.bot.get_guild(task.get('guild_id'))
            if not guild:
                log.error(f"Action queue: Guild not found.")
                self.bot.action_queue.task_done()
                return
            
            # Moderator isn't always needed, so we fetch it later if required.
            moderator_id = task.get('moderator_id')

            action = task.get('action')
            log.info(f"Processing panel action '{action}' for guild {guild.name}")

            if action == 'moderate_user':
                moderator = guild.get_member(moderator_id)
                if not moderator: 
                    log.warning(f"Moderator {moderator_id} not found for moderate_user action.")
                    self.bot.action_queue.task_done()
                    return

                target = guild.get_member(int(task.get('target_id')))
                reason = task.get('reason', 'No reason.') + f" - By {moderator.display_name} via Web Panel"
                mod_action = task.get('mod_action')
                
                if not target:
                    log.warning(f"Could not find member {task.get('target_id')} in guild {guild.id}.")
                else:
                    try:
                        if mod_action == 'ban': await _ban_member(None, target, reason, moderator)
                        elif mod_action == 'kick': await guild.kick(target, reason=reason)
                        elif mod_action == 'warn': await _issue_warning(self.bot, target, moderator, reason)
                        elif mod_action == 'timeout':
                            duration = int(task.get('duration', 10))
                            await _mute_member(None, target, duration, reason, moderator)
                    except discord.Forbidden:
                        log.error(f"Missing permissions to {mod_action} member {target.id} in guild {guild.id}.")

            elif action == 'run_setup':
                setup_map = {
                    'verification': 'setup_verification', 'submission': 'setup_submission_panel', 'report': 'setup_report'
                }
                command_name = setup_map.get(task.get('setup_type'))
                if command := self.bot.tree.get_command(command_name):
                    # We can't properly run setup commands from here.
                    # A better approach is to notify a channel that a setup is requested.
                    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
                    if log_channel_id and (log_channel := guild.get_channel(log_channel_id)):
                        moderator = guild.get_member(moderator_id)
                        await log_channel.send(f"**Panel Action:** {moderator.mention if moderator else 'A staff member'} requested to run `/{command_name}` from the web panel.")

            elif action == 'send_message':
                # --- CORRECTED LOGIC ---
                try:
                    channel_id = int(task.get('channel_id'))
                    channel = guild.get_channel(channel_id)
                    if channel:
                        if task.get('is_embed') == 'true':
                            parts = task.get('content').split('|', 1)
                            title = parts[0]
                            desc = parts[1] if len(parts) > 1 else " "
                            embed = discord.Embed(title=title, description=desc, color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
                            await channel.send(embed=embed)
                        else:
                            await channel.send(task.get('content'))
                    else:
                        log.error(f"Could not find channel {channel_id} to send message.")
                except (discord.Forbidden, ValueError):
                    log.error(f"Missing permissions or invalid channel ID for send_message.")
                # --- END CORRECTION ---

            # --- START OF REPLACEMENT BLOCK ---
            elif action == 'manage_staff':
                try:
                    moderator = guild.get_member(moderator_id)
                    if not moderator:
                        log.warning("Could not find moderator for staff management action.")
                        self.bot.action_queue.task_done()
                        return

                    target = guild.get_member(int(task.get('target_id')))
                    role_type = task.get('role_type')
                    role_action = task.get('role_action')
                    
                    if not target:
                        log.warning(f"Could not find target member for staff management.")
                        self.bot.action_queue.task_done()
                        return

                    role_ids_str = await database.get_setting(guild.id, f'{role_type}_role_ids') or ""
                    role_ids = [int(r) for r in role_ids_str.split(',') if r]
                    if not role_ids:
                        log.warning(f"No {role_type} roles are configured for this server.")
                        self.bot.action_queue.task_done()
                        return
                    
                    role_to_manage = guild.get_role(role_ids[0])
                    if not role_to_manage:
                        log.error(f"Could not find the role with ID {role_ids[0]}")
                        self.bot.action_queue.task_done()
                        return

                    # --- NEW HIERARCHY CHECKS ---
                    if guild.me.top_role <= role_to_manage:
                        log.error(f"Cannot manage role '{role_to_manage.name}' because it is higher than or equal to my own top role.")
                        self.bot.action_queue.task_done()
                        return
                    if target.top_role >= guild.me.top_role:
                        log.error(f"Cannot manage roles for {target.display_name} because they have a higher or equal role than me.")
                        self.bot.action_queue.task_done()
                        return
                    # --- END HIERARCHY CHECKS ---

                    if role_action == 'add':
                        await target.add_roles(role_to_manage, reason=f"Added by {moderator.display_name} via Web Panel")
                        log.info(f"Added '{role_to_manage.name}' to {target.display_name} via Web Panel.")
                    elif role_action == 'remove':
                        await target.remove_roles(role_to_manage, reason=f"Removed by {moderator.display_name} via Web Panel")
                        log.info(f"Removed '{role_to_manage.name}' from {target.display_name} via Web Panel.")

                except discord.Forbidden as e:
                    # Catch the error just in case and log it clearly
                    log.error(f"A permissions error occurred while managing staff role: {e}")
                except Exception as e:
                    log.error(f"An unexpected error occurred in staff management: {e}")
            # --- END OF REPLACEMENT BLOCK ---

            self.bot.action_queue.task_done()

        except Exception as e:
            log.error(f"Error processing action queue: {e}", exc_info=True)
            if 'task' in locals() and hasattr(self.bot, 'action_queue') and not self.bot.action_queue.empty():
                self.bot.action_queue.task_done()

    @process_action_queue.before_loop
    async def before_process_action_queue(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(PanelHandlerCog(bot))