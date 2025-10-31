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
    
    view_map = {
        'closed': SubmissionViewClosed,
        'open': SubmissionViewOpen,
    }
    view_class = view_map.get(status, SubmissionViewClosed)
    view = view_class(bot)
    
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
        
        self.cog.regular_session_reviewed_count[interaction.guild.id] += 1

        await database.update_submission_status(self.submission_id, "reviewed", interaction.user.id)
        await interaction.message.delete()
        await interaction.response.send_message("✅ Track marked as reviewed.", ephemeral=True)
        
        panel_message = await self.cog.get_panel_message(interaction.guild)
        if panel_message:
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)
        await self.cog._broadcast_full_update(interaction.guild.id)


# --- Base View for Shared Logic ---
class SubmissionBaseView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
        self.cog = bot.get_cog("Submissions")

    async def _update_panel(self, interaction: discord.Interaction):
        """Updates the panel with the latest embed and view."""
        async with self.cog.panel_update_locks[interaction.guild.id]:
            panel_message = await self.cog.get_panel_message(interaction.guild)
            if panel_message:
                embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
                try:
                    await panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    log.warning(f"Failed to update panel for guild {interaction.guild.id}, message not found.")

class SubmissionViewClosed(SubmissionBaseView):
    @discord.ui.button(label="Start Submissions", style=discord.ButtonStyle.success, custom_id="sub_start_regular")
    async def start_submissions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        self.cog.regular_session_reviewed_count[interaction.guild.id] = 0
        await database.update_setting(interaction.guild.id, 'submission_status', 'open')
        await self._update_panel(interaction)
        sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
        if sub_channel_id and (channel := self.bot.get_channel(sub_channel_id)):
            await channel.send("📢 @everyone Submissions are now **OPEN**! Please send your audio files here.\n📌 **ONLY MP3/WAV | DO NOT SEND ANY LINKS**")
        await interaction.followup.send("✅ Submissions are now open.", ephemeral=True)

    @discord.ui.button(label="📊 Statistics", style=discord.ButtonStyle.secondary, custom_id="sub_stats_regular")
    async def statistics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        reviewed_count = await database.get_total_reviewed_count(interaction.guild.id, 'regular')
        embed = discord.Embed(title="📊 Regular Submission Statistics (All-Time)", description=f"A total of **{reviewed_count}** tracks have been permanently reviewed in this server.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

class SubmissionViewOpen(SubmissionBaseView):
    @discord.ui.button(label="▶️ Play the Queue", style=discord.ButtonStyle.primary, custom_id="sub_play_regular")
    async def play_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        next_track = await database.get_next_submission(interaction.guild.id, submission_type='regular')
        if not next_track: return await interaction.response.send_message("The submission queue is empty!", ephemeral=True)
        
        sub_id, user_id, url = next_track
        await database.update_submission_status(sub_id, "reviewing", interaction.user.id)
        user = interaction.guild.get_member(user_id)
        embed = discord.Embed(title="🎵 Track for Review", description=f"Submitted by: {user.mention if user else 'N/A'}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        view = ReviewItemView(self.bot, sub_id, interaction.guild.id)
        await interaction.response.send_message(embed=embed, content=url, view=view)

    @discord.ui.button(label="⏹️ Stop Submissions", style=discord.ButtonStyle.danger, custom_id="sub_stop_regular")
    async def stop_submissions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        session_reviewed_count = self.cog.regular_session_reviewed_count.get(interaction.guild.id, 0)
        await database.clear_session_submissions(interaction.guild.id, 'regular')
        await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
        await self._update_panel(interaction)
        sub_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
        if sub_channel_id and (channel := self.bot.get_channel(sub_channel_id)):
            await channel.send("Submissions are now **CLOSED**! Thanks to everyone who sent in their tracks.")
        await interaction.followup.send(f"✅ Session closed. A total of **{session_reviewed_count}** tracks were reviewed in this session.", ephemeral=True)

    @discord.ui.button(label="📊 Statistics", style=discord.ButtonStyle.secondary, custom_id="sub_stats_regular_open")
    async def statistics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        reviewed_count = await database.get_total_reviewed_count(interaction.guild.id, 'regular')
        embed = discord.Embed(title="📊 Regular Submission Statistics (All-Time)", description=f"A total of **{reviewed_count}** tracks have been permanently reviewed in this server.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

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
                    delete_after=10  # Deletes the message after 10 seconds
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
                        user_data = await self.bot.app.fetch_user_data(message.author.id)
                        await self.bot.app.ws_manager.broadcast(message.guild.id, {
                            "type": "new_submission",
                            "username": user_data['name'],
                            "avatar_url": user_data['avatar_url']
                        })
                        await self._broadcast_full_update(message.guild.id)

                    break

    @app_commands.command(name="setup_submission_panel", description="Posts the interactive panel for managing music submissions.")
    @utils.has_permission("admin")
    async def setup_submission_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not (review_channel_id := await database.get_setting(interaction.guild.id, 'review_channel_id')):
            return await interaction.followup.send("❌ The review channel is not set. Use `/settings submission_system` first.")
        if not (review_channel := self.bot.get_channel(review_channel_id)):
            return await interaction.followup.send("❌ Could not find the configured review channel.")

        if old_panel := await self.get_panel_message(interaction.guild):
            try:
                await old_panel.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
        await self._broadcast_full_update(interaction.guild.id)

        embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)

        try:
            panel_message = await review_channel.send(embed=embed, view=view)
            await database.update_setting(interaction.guild.id, 'review_panel_message_id', panel_message.id)
            await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
            await interaction.followup.send(f"✅ Submission panel has been posted in {review_channel.mention}.")
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {review_channel.mention}.")

    @app_commands.command(name="reset_stuck_review", description="Manually resets a track that is stuck in the 'reviewing' state.")
    @utils.has_permission("admin")
    async def reset_stuck_review(self, interaction: discord.Interaction):
        """Manually finds and resets any submission stuck in 'reviewing' status."""
        await interaction.response.defer(ephemeral=True)

        conn = await database.get_db_connection()
        async with conn.cursor() as cursor:
            await cursor.execute(
                "SELECT submission_id, user_id FROM music_submissions WHERE guild_id = ? AND status = 'reviewing' AND submission_type = 'regular' LIMIT 1",
                (interaction.guild.id,)
            )
            stuck_submission = await cursor.fetchone()

        if not stuck_submission:
            return await interaction.followup.send("✅ No stuck submissions found in the regular queue.", ephemeral=True)

        submission_id, user_id = stuck_submission
        
        await database.update_submission_status(submission_id, "pending", None)
        
        log.info(f"Admin {interaction.user.id} manually reset stuck submission {submission_id}.")

        await self._update_panel_after_submission(interaction.guild)
        await self._broadcast_full_update(interaction.guild.id)

        user = interaction.guild.get_member(user_id)
        await interaction.followup.send(
            f"✅ Successfully reset the stuck submission from **{user.display_name if user else 'Unknown User'}**. It has been returned to the queue.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(SubmissionsCog(bot))