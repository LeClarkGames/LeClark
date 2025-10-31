import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
import database
import config
import utils
import asyncio
from collections import defaultdict
import time
import secrets
import os

log = logging.getLogger(__name__)

# --- View for the Tier-Up Approval Message ---

class TierApprovalView(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, user_id: int, next_tier: int, approval_token: str):
        super().__init__(timeout=None) # Persistent view
        self.bot = bot
        self.guild_id = guild_id
        self.user_id = user_id
        self.next_tier = next_tier
        
        base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
        activity_url = f"{base_url}/user_activity/{guild_id}/{user_id}?token={approval_token}"
        
        self.add_item(discord.ui.Button(label="Check User Activity", style=discord.ButtonStyle.link, url=activity_url, emoji="📊"))

# --- Main Cog ---

class TierSystemCog(commands.Cog, name="Tier System"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_activity = defaultdict(lambda: defaultdict(int)) # guild_id -> user_id -> start_time
        
        # This queue is used for the web server to communicate back to the bot
        if not hasattr(bot, 'tier_approval_queue'):
            bot.tier_approval_queue = asyncio.Queue()

        self.activity_check_loop.start()
        self.process_web_approvals.start()

    def cog_unload(self):
        self.activity_check_loop.cancel()
        self.process_web_approvals.cancel()

    # --- Activity Tracking ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or (message.author.bot and message.author.id == self.bot.user.id):
            return
            
        await database.update_channel_activity(message.guild.id, message.author.id, message.channel.id, message_count=1)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        guild_id = member.guild.id
        user_id = member.id

        # User joins a voice channel
        if after.channel and not before.channel:
            self.voice_activity[guild_id][user_id] = time.time()
        
        # User leaves a voice channel
        elif before.channel and not after.channel:
            if self.voice_activity[guild_id][user_id]:
                duration_seconds = int(time.time() - self.voice_activity[guild_id][user_id])
                await database.update_channel_activity(guild_id, user_id, before.channel.id, voice_seconds=duration_seconds)
                del self.voice_activity[guild_id][user_id]

    @tasks.loop(minutes=1)
    async def activity_check_loop(self):
        log.info("Running periodic activity check for tier upgrades...")
        for guild in self.bot.guilds:
            all_requirements = await database.get_all_tier_requirements(guild.id)
            if not all_requirements:
                continue

            tier_roles = await database.get_all_tier_roles(guild.id)
            if not tier_roles: continue # Skip if no tier roles are configured

            for member in guild.members:
                if member.bot: continue

                current_tier = await database.get_user_tier(guild.id, member.id)

                # --- MODIFIED LOGIC FOR ENROLLMENT ---
                if current_tier is None:
                    # Check if the member has any of the configured tier roles
                    member_role_ids = {role.id for role in member.roles}
                    for tier_level, role_id in tier_roles.items():
                        if role_id in member_role_ids:
                            await database.set_user_tier(guild.id, member.id, tier_level)
                            current_tier = tier_level
                            log.info(f"Automatically enrolled existing member {member.name} into the tier system at Tier {tier_level}.")
                            break # Enroll at the highest tier they have and stop checking
                    
                    # If after checking all tier roles, they still have no tier, skip them
                    if current_tier is None:
                        continue
                # --- END OF MODIFIED LOGIC ---

                # Check if user is already at the highest tier (Tier 1)
                if current_tier <= 1:
                    continue
                
                # Promote downwards in number (e.g., from Tier 3 to Tier 2)
                next_tier = current_tier - 1
                reqs = all_requirements.get(next_tier)
                if not reqs:
                    continue

                activity = await database.get_user_activity(guild.id, member.id)
                if not activity:
                    continue

                if (activity['message_count'] >= reqs['messages_req'] and 
                    (activity['voice_seconds'] / 3600) >= reqs['voice_hours_req']):
                    
                    if await database.get_tier_approval_request(guild.id, member.id):
                        continue

                    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
                    if not log_channel_id: continue
                    log_channel = guild.get_channel(log_channel_id)
                    if not log_channel: continue

                    token = secrets.token_urlsafe(16)
                    embed = discord.Embed(
                        title="✨ Tier Upgrade Recommendation",
                        description=f"{member.mention} is eligible to be promoted to **Tier {next_tier}**.",
                        color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
                    )
                    embed.add_field(name="Current Tier", value=f"`{current_tier}`", inline=True)
                    embed.add_field(name="Recommended Tier", value=f"`{next_tier}`", inline=True)
                    embed.set_footer(text=f"User ID: {member.id}")
                    view = TierApprovalView(self.bot, guild.id, member.id, next_tier, token)
                    message = await log_channel.send(embed=embed, view=view)
                    await database.create_or_update_tier_approval_request(guild.id, member.id, next_tier, token, message.id)
                    log.info(f"Created/updated tier upgrade request for {member.name} in {guild.name}.")
    
    @activity_check_loop.before_loop
    async def before_activity_check_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=5)
    async def process_web_approvals(self):
        try:
            approval_data = self.bot.tier_approval_queue.get_nowait()
            
            guild_id = approval_data['guild_id']
            user_id = approval_data['user_id']
            new_tier = approval_data['new_tier']
            message_id = approval_data['message_id']
            approver_name = approval_data['approver_name']

            guild = self.bot.get_guild(guild_id)
            if not guild: return

            member = guild.get_member(user_id)
            if not member: return

            # --- START OF MODIFICATION ---
            # Update the user's tier in the database immediately
            await database.set_user_tier(guild.id, member.id, new_tier)
            # --- END OF MODIFICATION ---

            # Get and assign new role, remove old one
            tier_roles = await database.get_all_tier_roles(guild.id)
            new_role_id = tier_roles.get(new_tier)
            old_role_id = tier_roles.get(new_tier - 1)

            if new_role_id:
                new_role = guild.get_role(new_role_id)
                if new_role: await member.add_roles(new_role, reason=f"Tier {new_tier} approved by {approver_name}")
            
            if old_role_id:
                old_role = guild.get_role(old_role_id)
                if old_role: await member.remove_roles(old_role, reason=f"Tier {new_tier} approved.")

            # Update log message
            log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    try:
                        message = await log_channel.fetch_message(message_id)
                        original_embed = message.embeds[0]
                        approved_embed = discord.Embed(
                            title="✅ Tier Upgrade Approved",
                            description=f"{member.mention} has been promoted to **Tier {new_tier}**.",
                            color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"]
                        )
                        approved_embed.add_field(name="Approved By", value=approver_name, inline=True)
                        await message.edit(embed=approved_embed, view=None)
                    except (discord.NotFound, discord.Forbidden):
                        pass # Message might have been deleted

            # DM the user
            try:
                dm_embed = discord.Embed(
                    title="🎉 Congratulations! 🎉",
                    description=f"You've been promoted to **Tier {new_tier}** in the **{guild.name}** server!\n\nThis grants you access to new channels and perks. Thank you for being an active member of our community!",
                    color=discord.Color.gold()
                )
                await member.send(embed=dm_embed)
            except discord.Forbidden:
                pass # Can't DM the user
            
            self.bot.tier_approval_queue.task_done()

        except asyncio.QueueEmpty:
            pass
        except Exception as e:
            log.error(f"Error processing web approval: {e}")

    @process_web_approvals.before_loop
    async def before_process_web_approvals(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(TierSystemCog(bot))