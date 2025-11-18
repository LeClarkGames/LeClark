import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from collections import defaultdict

import database
import config
import utils

log = logging.getLogger(__name__)

async def get_panel_embed_and_view(guild: discord.Guild, bot: commands.Bot):
    """Generates the panel embed and view based on the database state."""
    status = await database.get_setting(guild.id, 'submission_status') or 'closed'

    title="🎵 Music Submission Control Panel"
    is_open = status == 'open'
    queue_count = await database.get_submission_queue_count(guild.id)
    desc = f"Submissions are currently **{'OPEN' if is_open else 'CLOSED'}**.\n\n**Queue:** `{queue_count}` tracks pending."
    embed_color = config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"] if is_open else config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]

    embed = discord.Embed(title=title, description=desc, color=embed_color)
    
    view = SubmissionViewOpen(bot) if is_open else SubmissionViewClosed(bot)
    
    return embed, view

class ReviewItemView(discord.ui.View):
    """View for a single track being reviewed in regular mode."""
    def __init__(self, bot: commands.Bot, submission_id: int, guild_id: int):
        super().__init__(timeout=18000)
        self.bot = bot
        self.submission_id = submission_id
        self.guild_id = guild_id
        self.cog = bot.get_cog("Submissions")

    async def on_timeout(self):
        """Called when the view's 5-hour timer expires."""
        log.warning(f"Review for submission {self.submission_id} timed out.")
        await database.update_submission_status(self.submission_id, "pending", None)
        
        guild = self.bot.get_guild(self.guild_id)
        if guild:
            log.info(f"Updating panels for guild {guild.id} after review timeout.")
            await self.cog._update_panel_after_submission(guild)
            await self.cog._broadcast_full_update(self.guild_id)

    @discord.ui.button(label="✔️ Mark as Reviewed", style=discord.ButtonStyle.success)
    async def mark_reviewed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission to review tracks.", ephemeral=True)
        
        if not self.cog: self.cog = self.bot.get_cog("Submissions")
        self.cog.regular_session_reviewed_count[interaction.guild.id] += 1

        await database.update_submission_status(self.submission_id, "reviewed", interaction.user.id)

        self.stop()

        await interaction.message.delete()
        await interaction.response.send_message("✅ Track marked as reviewed.", ephemeral=True)
        
        panel_message = await self.cog.get_panel_message(interaction.guild)
        if panel_message:
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)
        await self.cog._broadcast_full_update(interaction.guild.id)


class SubmissionBaseView(discord.ui.View):

    class StartSubmissionsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Start Submissions", style=discord.ButtonStyle.success, custom_id="sub_start_regular")

        async def callback(self, interaction: discord.Interaction):
            if not self.view.cog: self.view.cog = self.view.bot.get_cog("Submissions")
            if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            await interaction.response.defer()
            self.view.cog.regular_session_reviewed_count[interaction.guild.id] = 0
            await database.update_setting(interaction.guild.id, 'submission_status', 'open')
            await self.view._update_panel(interaction)
            sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
            if sub_channel_id and (channel := self.view.bot.get_channel(sub_channel_id)):
                await channel.send("📢 @everyone Submissions are now **OPEN**! Please send your audio files here.\n📌 **ONLY MP3/WAV | DO NOT SEND ANY LINKS**")
            await interaction.followup.send("✅ Submissions are now open.", ephemeral=True)

    class PlayQueueButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="▶️ Play the Queue", style=discord.ButtonStyle.primary, custom_id="sub_play_regular")

        async def callback(self, interaction: discord.Interaction):
            if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
            next_track = await database.get_next_submission(interaction.guild.id, submission_type='regular')
            if not next_track: return await interaction.response.send_message("The submission queue is empty!", ephemeral=True)
            
            sub_id, user_id, url = next_track
            await database.update_submission_status(sub_id, "reviewing", interaction.user.id)
            user = interaction.guild.get_member(user_id)
            embed = discord.Embed(title="🎵 Track for Review", description=f"Submitted by: {user.mention if user else 'N/A'}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
            view = ReviewItemView(self.view.bot, sub_id, interaction.guild.id)
            await interaction.response.send_message(embed=embed, content=url, view=view)

    class StopSubmissionsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="⏹️ Stop Submissions", style=discord.ButtonStyle.danger, custom_id="sub_stop_regular")

        async def callback(self, interaction: discord.Interaction):
            if not self.view.cog: self.view.cog = self.view.bot.get_cog("Submissions")
            if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
            await interaction.response.defer()
            session_reviewed_count = self.view.cog.regular_session_reviewed_count.get(interaction.guild.id, 0)
            await database.clear_session_submissions(interaction.guild.id, 'regular')
            await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
            await self.view._update_panel(interaction)
            sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
            if sub_channel_id and (channel := self.view.bot.get_channel(sub_channel_id)):
                await channel.send("Submissions are now **CLOSED**! Thanks to everyone who sent in their tracks.")
            await interaction.followup.send(f"✅ Session closed. A total of **{session_reviewed_count}** tracks were reviewed in this session.", ephemeral=True)

    class StatisticsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="📊 Statistics", style=discord.ButtonStyle.secondary, custom_id="sub_stats_regular")

        async def callback(self, interaction: discord.Interaction):
            if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
            await interaction.response.defer(ephemeral=True)
            reviewed_count = await database.get_total_reviewed_count(interaction.guild.id, 'regular')
            embed = discord.Embed(title="📊 Regular Submission Statistics (All-Time)", description=f"A total of **{reviewed_count}** tracks have been permanently reviewed in this server.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
            await interaction.followup.send(embed=embed)

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = bot.get_cog("Submissions")
        if not self.cog:
            pass

    async def _update_panel(self, interaction: discord.Interaction):
        """Updates the panel with the latest embed and view."""
        if not self.cog: self.cog = self.bot.get_cog("Submissions")
        async with self.cog.panel_update_locks[interaction.guild.id]:
            panel_message = await self.cog.get_panel_message(interaction.guild)
            if panel_message:
                embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
                try:
                    await panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    log.warning(f"Failed to update panel for guild {interaction.guild.id}, message not found.")

class SubmissionViewOpen(SubmissionBaseView):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.add_item(self.PlayQueueButton())
        self.add_item(self.StopSubmissionsButton())
        self.add_item(self.StatisticsButton())

class SubmissionViewClosed(SubmissionBaseView):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)
        self.add_item(self.StartSubmissionsButton())
        self.add_item(self.StatisticsButton())

class SubmissionsCog(commands.Cog, name="Submissions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_update_locks = defaultdict(asyncio.Lock)
        self.regular_session_reviewed_count = defaultdict(int)

    async def _update_panel_after_submission(self, guild: discord.Guild):
        """A helper to specifically update the panel after a submission is made."""
        async with self.panel_update_locks[guild.id]:
            panel_message = await self.get_panel_message(guild)
            if panel_message:
                embed, view = await get_panel_embed_and_view(guild, self.bot)
                try:
                    await panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    log.warning(f"Failed to update panel for guild {guild.id}, message not found.")

    async def _broadcast_full_update(self, guild_id: int):
        """Helper to construct and broadcast a full widget update."""
        if hasattr(self.bot, 'app') and hasattr(self.bot.app, 'ws_manager'):
            # Need to get the cog properly
            if not self.bot.get_cog("Submissions"): return
            if not hasattr(self.bot.app, 'get_full_widget_data'):
                log.warning("ws_manager is present, but get_full_widget_data is not on app.")
                return
                
            full_data = await self.bot.app.get_full_widget_data(guild_id)
            await self.bot.app.ws_manager.broadcast(guild_id, full_data)

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the submissions system is enabled for this guild."""
        is_enabled = await database.get_setting(interaction.guild.id, 'submissions_system_enabled')
        if not is_enabled:
            await interaction.response.send_message("The submissions system is disabled on this server.", ephemeral=True)
            return False
        return True

    async def get_panel_message(self, guild: discord.Guild) -> discord.Message | None:
        panel_id = await database.get_setting(guild.id, 'review_panel_message_id')
        channel_id = await database.get_setting(guild.id, 'review_channel_id')
        if not panel_id or not channel_id: return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if channel: return await channel.fetch_message(panel_id)
        except (discord.NotFound, discord.Forbidden): return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild: return

        if not await database.get_setting(message.guild.id, 'submissions_system_enabled'):
            return

        status = await database.get_setting(message.guild.id, 'submission_status')
        submission_channel_id = await database.get_setting(message.guild.id, 'submission_channel_id')

        submission_type = None
        if status == 'open' and message.channel.id == submission_channel_id:
            submission_type = 'regular'

        if submission_type and message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("audio/"):
                    submission_id = await database.add_submission(message.guild.id, message.author.id, attachment.url, submission_type)
                    await message.add_reaction("✅")

                    queue_count = await database.get_submission_queue_count(message.guild.id, submission_type)
                    await message.channel.send(
                    f"✅ **{message.author.mention}**, your track has been submitted! You are currently position **#{queue_count}** in the queue.",
                    delete_after=10
                    )       

                    await self._update_panel_after_submission(message.guild)

                    if submission_type == 'regular':
                        total_user_subs = await database.get_user_submission_count(message.guild.id, message.author.id, 'regular')
                        if total_user_subs == 1:
                            await database.prioritize_submission(submission_id)
                            log.info(f"Prioritized first-time submission from {message.author.id}")
                            try:
                                await message.author.send(f"✅ Since it's your first time submitting in **{message.guild.name}**, your track has been moved to the front of the queue!")
                            except discord.Forbidden:
                                pass

                    if hasattr(self.bot, 'app') and hasattr(self.bot.app, 'ws_manager'):
                        # Need to check for fetch_user_data on app
                        if hasattr(self.bot.app, 'fetch_user_data'):
                            user_data = await self.bot.app.fetch_user_data(message.author.id)
                            await self.bot.app.ws_manager.broadcast(message.guild.id, {
                                "type": "new_submission",
                                "username": user_data['name'],
                                "avatar_url": user_data['avatar_url']
                            })
                            await self._broadcast_full_update(message.guild.id)
                        else:
                            log.warning("Cannot broadcast new submission, 'fetch_user_data' not on app.")

                    break
    
    async def post_panel(self, channel: discord.TextChannel):
        """Posts the submissions panel to the specified channel."""
        try:
            if old_panel := await self.get_panel_message(channel.guild):
                try: await old_panel.delete()
                except (discord.Forbidden, discord.NotFound): pass
            
            embed, view = await get_panel_embed_and_view(channel.guild, self.bot)
            panel_message = await channel.send(embed=embed, view=view)
            
            await database.update_setting(channel.guild.id, 'review_panel_message_id', panel_message.id)
            await database.update_setting(channel.guild.id, 'submission_status', 'closed')

            return True, f"Submission panel has been posted in {channel.mention}."
        except discord.Forbidden:
            return False, f"Bot lacks permission to send messages in {channel.mention}."
        except Exception as e:
            log.error(f"Error in panel setup (submission): {e}")
            return False, "An internal error occurred."

async def setup(bot: commands.Bot):
    await bot.add_cog(SubmissionsCog(bot))