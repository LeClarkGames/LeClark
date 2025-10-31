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

# --- Helper Function to Build the Panel ---
async def get_panel_embed_and_view(guild: discord.Guild, bot: commands.Bot):
    """Generates the panel embed and view based on the database state."""
    status = await database.get_setting(guild.id, 'submission_status') or 'closed'
    
    embed_color = config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
    
    if status.startswith('koth'):
        title = "⚔️ King of the Hill Panel"
        koth_queue_count = await database.get_submission_queue_count(guild.id, submission_type='koth')
        
        desc = f"**Mode:** King of the Hill\n**Submissions:** `{'OPEN' if status == 'koth_open' else 'CLOSED'}`\n**Queue:** `{koth_queue_count}` challengers pending."

        if status == 'koth_tiebreaker':
            desc = "**Mode:** King of the Hill\n**Submissions:** `TIEBREAKER DUEL`"
            tiebreaker_users_str = await database.get_setting(guild.id, 'koth_tiebreaker_users') or ""
            tiebreaker_user_ids = [int(uid) for uid in tiebreaker_users_str.split(',') if uid]
            
            mentions = []
            for user_id in tiebreaker_user_ids:
                if user := guild.get_member(user_id):
                    mentions.append(user.mention)
            if mentions:
                desc += f"\n\nWaiting for final submissions from {', '.join(mentions)}."
        
        if king_user_id := await database.get_setting(guild.id, 'koth_king_id'):
            if king_user := guild.get_member(king_user_id):
                desc += f"\n\n**Current King:** {king_user.mention}"
        
        cog = bot.get_cog("Submissions")
        session_stats = cog.current_koth_session.get(guild.id, {})
        if status == 'koth_open' and session_stats:
            desc += "\n\n**Leaderboard (Current Battle):**\n"
            sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)
            for i, (user_id, stats) in enumerate(sorted_session[:5]):
                user = guild.get_member(user_id)
                desc += f"`{i+1}.` {user.display_name if user else 'Unknown'}: `{stats['points']}` pts (`{stats['wins']}` wins)\n"
    else:
        title = "🎵 Music Submission Control Panel"
        is_open = status == 'open'
        queue_count = await database.get_submission_queue_count(guild.id)
        desc = f"Submissions are currently **{'OPEN' if is_open else 'CLOSED'}**.\n\n**Queue:** `{queue_count}` tracks pending."
        embed_color = config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"] if is_open else config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]

    embed = discord.Embed(title=title, description=desc, color=embed_color)
    
    # Switch to the correct persistent view based on the status
    view_map = {
        'closed': SubmissionViewClosed,
        'open': SubmissionViewOpen,
        'koth_closed': SubmissionViewKothClosed,
        'koth_open': SubmissionViewKothOpen,
        'koth_tiebreaker': SubmissionViewKothTiebreaker,
    }
    view_class = view_map.get(status, SubmissionViewClosed)
    view = view_class(bot)
    
    return embed, view

class KOTHBattleView(discord.ui.View):
    """View for when a KOTH battle is actively happening."""
    def __init__(self, bot: commands.Bot, king_data: dict, challenger_data: dict, is_tiebreaker: bool = False):
        super().__init__(timeout=None)
        self.bot = bot
        self.king_data = king_data
        self.challenger_data = challenger_data
        self.is_tiebreaker = is_tiebreaker
        self.cog = bot.get_cog("Submissions")

    @discord.ui.button(label="👑 Vote for the King", style=discord.ButtonStyle.primary)
    async def vote_king_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, 'king')

    @discord.ui.button(label="⚔️ Vote for the Challenger", style=discord.ButtonStyle.secondary)
    async def vote_challenger_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, 'challenger')

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.danger, row=1)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission to skip.", ephemeral=True)

        await interaction.response.defer()
        await database.update_submission_status(self.king_data['submission_id'], 'reviewed', interaction.user.id)
        await database.update_submission_status(self.challenger_data['submission_id'], 'reviewed', interaction.user.id)

        await interaction.message.delete()
        skip_message = await interaction.followup.send(f"⏭️ The battle was skipped by {interaction.user.mention}. No points awarded.")
        self.cog.koth_battle_messages[interaction.guild.id].append(skip_message.id)
        await self.cog._broadcast_full_update(interaction.guild.id)

    async def _handle_vote(self, interaction: discord.Interaction, winner: str):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ You do not have permission to vote.", ephemeral=True)

        await interaction.response.defer()

        winner_data = self.king_data if winner == 'king' else self.challenger_data
        loser_data = self.challenger_data if winner == 'king' else self.king_data

        if self.is_tiebreaker:
            await interaction.message.delete()
            await self.cog.finalize_koth_battle(interaction, winner_data['user_id'])
            return

        await database.update_koth_battle_results(interaction.guild.id, winner_data['user_id'], loser_data['user_id'])

        session_stats = self.cog.current_koth_session[interaction.guild.id]
        winner_id = winner_data['user_id']
        session_stats.setdefault(winner_id, {'points': 0, 'wins': 0})['points'] += 1
        session_stats.setdefault(winner_id, {'points': 0, 'wins': 0})['wins'] += 1

        await database.update_submission_status(self.king_data['submission_id'], 'reviewed', interaction.user.id)
        await database.update_submission_status(self.challenger_data['submission_id'], 'reviewed', interaction.user.id)

        await database.update_setting(interaction.guild.id, 'koth_king_id', winner_data['user_id'])
        await database.update_setting(interaction.guild.id, 'koth_king_submission_id', winner_data['submission_id'])

        await interaction.message.delete()

        if panel_message := await self.cog.get_panel_message(interaction.guild):
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)

        winner_user = interaction.guild.get_member(winner_id)
        winner_message = await interaction.followup.send(f"👑 **{winner_user.display_name if winner_user else 'Someone'}** wins the round and remains King!")
        self.cog.koth_battle_messages[interaction.guild.id].append(winner_message.id)
        await self.cog._broadcast_full_update(interaction.guild.id)

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

# --- Persistent Views for each Status ---

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

    @discord.ui.button(label="Switch to KOTH Mode", style=discord.ButtonStyle.secondary, custom_id="sub_switch_to_koth")
    async def switch_to_koth(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        await database.update_setting(interaction.guild.id, 'submission_status', 'koth_closed')
        await self._update_panel(interaction)
        await interaction.followup.send("✅ Switched to King of the Hill mode.", ephemeral=True)

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

class SubmissionViewKothClosed(SubmissionBaseView):
    @discord.ui.button(label="Start KOTH Battle", style=discord.ButtonStyle.success, custom_id="sub_start_koth")
    async def start_koth_battle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        self.cog.current_koth_session.pop(interaction.guild.id, None)
        if winner_role_id := await database.get_setting(interaction.guild.id, 'koth_winner_role_id'):
            if role := interaction.guild.get_role(winner_role_id):
                for member in role.members:
                    await member.remove_roles(role, reason="New KOTH battle started.")
        await database.update_setting(interaction.guild.id, 'submission_status', 'koth_open')
        await self._update_panel(interaction)
        if koth_channel_id := await database.get_setting(interaction.guild.id, 'koth_submission_channel_id'):
            if channel := self.bot.get_channel(koth_channel_id):
                await channel.send("📢 @everyone King of the Hill submissions are **OPEN**! Submit your best track to enter the battle and win the battle!\n📌 **ONLY MP3/WAV | DO NOT SEND ANY LINKS**")
        await interaction.followup.send("✅ King of the Hill battle has started!", ephemeral=True)

    @discord.ui.button(label="📊 KOTH Stats", style=discord.ButtonStyle.secondary, custom_id="sub_stats_koth")
    async def koth_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        leaderboard = await database.get_koth_leaderboard(interaction.guild.id)
        if not leaderboard: return await interaction.followup.send("No KOTH statistics found yet.", ephemeral=True)
        desc = "All-time points for King of the Hill battles:\n\n"
        for i, (user_id, points, wins, losses, streak) in enumerate(leaderboard[:10]):
            user = interaction.guild.get_member(user_id)
            user_display = user.display_name if user else f'Unknown User ({user_id})'
            desc += f"`{i+1}.` **{user_display}**: `{points}` pts (**W/L:** `{wins}/{losses}`, **Streak:** `{streak}`)\n"
        embed = discord.Embed(title="⚔️ KOTH Leaderboard (All-Time)", description=desc, color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

    @discord.ui.button(label="Switch to Regular Mode", style=discord.ButtonStyle.secondary, custom_id="sub_switch_to_regular")
    async def switch_to_regular(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        await database.update_setting(interaction.guild.id, 'submission_status', 'closed')
        await self._update_panel(interaction)
        await interaction.followup.send("✅ Switched back to regular submission mode.", ephemeral=True)

class SubmissionViewKothOpen(SubmissionBaseView):
    @discord.ui.button(label="▶️ Play KOTH Queue", style=discord.ButtonStyle.primary, custom_id="sub_play_koth")
    async def play_koth_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods only.", ephemeral=True)
        guild_id = interaction.guild.id
        king_id = await database.get_setting(guild_id, 'koth_king_id')
        challenger_track = await database.get_next_submission(guild_id, 'koth')
        if not king_id:
            if not challenger_track: return await interaction.response.send_message("The KOTH queue is empty! Need at least one challenger.", ephemeral=True)
            sub_id, user_id, url = challenger_track
            await database.update_setting(guild_id, 'koth_king_id', user_id)
            await database.update_setting(guild_id, 'koth_king_submission_id', sub_id)
            await database.update_submission_status(sub_id, 'reviewing', interaction.user.id)
            king_user = interaction.guild.get_member(user_id)
            embed = discord.Embed(title="👑 New King of the Hill!", description=f"**{king_user.display_name}** is the new King!", color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"])
            await interaction.response.send_message(content=url, embed=embed)
            self.cog.koth_battle_messages[guild_id].append((await interaction.original_response()).id)
            await self._update_panel(interaction)
        else:
            if not challenger_track: return await interaction.response.send_message("No more challengers in the queue!", ephemeral=True)
            c_sub_id, c_user_id, c_url = challenger_track
            await database.update_submission_status(c_sub_id, 'reviewing', interaction.user.id)
            king_sub_id = await database.get_setting(guild_id, 'koth_king_submission_id')
            conn = await database.get_db_connection()
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT track_url FROM music_submissions WHERE submission_id = ?", (king_sub_id,))
                king_url_result = await cursor.fetchone()
                king_url = king_url_result[0] if king_url_result else "Track URL not found"
            king_data = {"user_id": king_id, "submission_id": king_sub_id, "track_url": king_url}
            challenger_data = {"user_id": c_user_id, "submission_id": c_sub_id, "track_url": c_url}
            king_user = interaction.guild.get_member(king_id)
            challenger_user = interaction.guild.get_member(c_user_id)
            embed = discord.Embed(title="⚔️ BATTLE TIME! ⚔️", color=discord.Color.gold())
            embed.add_field(name=f"👑 The King: {king_user.display_name if king_user else 'Unknown'}", value=f"Track: {king_url}", inline=False)
            embed.add_field(name=f"⚔️ The Challenger: {challenger_user.display_name if challenger_user else 'Unknown'}", value=f"Track: {c_url}", inline=False)
            await interaction.response.send_message(embed=embed, view=KOTHBattleView(self.bot, king_data, challenger_data))

    @discord.ui.button(label="⏹️ Stop KOTH Battle", style=discord.ButtonStyle.danger, custom_id="sub_stop_koth")
    async def stop_koth_battle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await interaction.response.defer()
        guild_id = interaction.guild.id
        session_stats = self.cog.current_koth_session.get(guild_id, {})
        sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)
        is_tie = len(sorted_session) > 1 and sorted_session[0][1]['points'] > 0 and sorted_session[0][1]['points'] == sorted_session[1][1]['points']
        if is_tie:
            user1_id, user2_id = sorted_session[0][0], sorted_session[1][0]
            await database.update_setting(guild_id, 'koth_tiebreaker_users', f"{user1_id},{user2_id}")
            self.cog.tiebreaker_submissions.pop(guild_id, None)
            await database.update_setting(guild_id, 'submission_status', 'koth_tiebreaker')
            await self._update_panel(interaction)
            user1 = interaction.guild.get_member(user1_id)
            user2 = interaction.guild.get_member(user2_id)
            if (koth_channel_id := await database.get_setting(guild_id, 'koth_submission_channel_id')) and (channel := self.bot.get_channel(koth_channel_id)):
                await channel.send(f"**⚔️ TIEBREAKER! ⚔️**\n{user1.mention} and {user2.mention}, submit one final track!")
            await interaction.followup.send("A tiebreaker has been initiated!", ephemeral=True)
        else:
            winner_id = sorted_session[0][0] if sorted_session else await database.get_setting(guild_id, 'koth_king_id')
            await self.cog.finalize_koth_battle(interaction, winner_id)

    @discord.ui.button(label="📊 KOTH Stats", style=discord.ButtonStyle.secondary, custom_id="sub_stats_koth_open")
    async def koth_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("❌ Mods/Admins only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        leaderboard = await database.get_koth_leaderboard(interaction.guild.id)
        if not leaderboard: return await interaction.followup.send("No KOTH statistics found yet.", ephemeral=True)
        desc = "All-time points for King of the Hill battles:\n\n"
        for i, (user_id, points, wins, losses, streak) in enumerate(leaderboard[:10]):
            user = interaction.guild.get_member(user_id)
            user_display = user.display_name if user else f'Unknown User ({user_id})'
            desc += f"`{i+1}.` **{user_display}**: `{points}` pts (**W/L:** `{wins}/{losses}`, **Streak:** `{streak}`)\n"
        embed = discord.Embed(title="⚔️ KOTH Leaderboard (All-Time)", description=desc, color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed)

class SubmissionViewKothTiebreaker(SubmissionBaseView):
    @discord.ui.button(label="🛑 Cancel Tiebreaker", style=discord.ButtonStyle.danger, custom_id="sub_cancel_tiebreaker")
    async def cancel_tiebreaker(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user): return await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        await self.cog.finalize_koth_battle(interaction, None)

class SubmissionsCog(commands.Cog, name="Submissions"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panel_update_locks = defaultdict(asyncio.Lock)
        self.koth_battle_messages = defaultdict(list)
        self.current_koth_session = defaultdict(dict)
        self.tiebreaker_submissions = defaultdict(dict)
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

    async def finalize_koth_battle(self, interaction: discord.Interaction, winner_id: int | None):
        guild_id = interaction.guild.id

        if review_channel_id := await database.get_setting(guild_id, 'review_channel_id'):
            if review_channel := self.bot.get_channel(review_channel_id):
                message_ids_to_delete = self.koth_battle_messages.pop(guild_id, [])
                for msg_id in message_ids_to_delete:
                    try:
                        await (await review_channel.fetch_message(msg_id)).delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
        await self._broadcast_full_update(interaction.guild.id)

        session_stats = self.current_koth_session.get(guild_id, {})
        sorted_session = sorted(session_stats.items(), key=lambda item: item[1]['points'], reverse=True)
        public_desc = "**Final Battle Leaderboard:**\n"
        if sorted_session:
            for i, (user_id, stats) in enumerate(sorted_session):
                user = interaction.guild.get_member(user_id)
                public_desc += f"`{i+1}.` {user.display_name if user else 'Unknown User'}: `{stats['points']}` points (`{stats['wins']}` wins)\n"
        else:
            public_desc += "No points were scored in this battle."

        public_embed = discord.Embed(title="🏆 King of the Hill Results 🏆", description=public_desc, color=discord.Color.gold())

        if winner_id and (winner := interaction.guild.get_member(winner_id)):
            public_embed.description = f"Congratulations to the battle winner, {winner.mention}!\n\n" + public_desc
            if winner_role_id := await database.get_setting(guild_id, 'koth_winner_role_id'):
                if role := interaction.guild.get_role(winner_role_id):
                    await winner.add_roles(role, reason="KOTH Winner")

        if koth_channel_id := await database.get_setting(guild_id, 'koth_submission_channel_id'):
            if channel := self.bot.get_channel(koth_channel_id):
                await channel.send(embed=public_embed)

        await database.clear_session_submissions(guild_id, 'koth')
        await database.update_setting(guild_id, 'submission_status', 'koth_closed')
        await database.update_setting(guild_id, 'koth_king_id', None)
        await database.update_setting(guild_id, 'koth_king_submission_id', None)
        await database.update_setting(guild_id, 'koth_tiebreaker_users', None)

        self.current_koth_session.pop(guild_id, None)
        self.tiebreaker_submissions.pop(guild_id, None)

        panel_message = await self.get_panel_message(interaction.guild)
        if panel_message:
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)

        if interaction.response.is_done():
            await interaction.followup.send("✅ KOTH battle stopped. Results posted.", ephemeral=True)
        else:
            await interaction.response.send_message("✅ KOTH battle stopped. Results posted.", ephemeral=True)

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
        koth_submission_channel_id = await database.get_setting(message.guild.id, 'koth_submission_channel_id')

        submission_type = None
        if status == 'open' and message.channel.id == submission_channel_id:
            submission_type = 'regular'
        elif status == 'koth_open' and message.channel.id == koth_submission_channel_id:
            submission_type = 'koth'
        elif status == 'koth_tiebreaker' and message.channel.id == koth_submission_channel_id:
            tiebreaker_users_str = await database.get_setting(message.guild.id, 'koth_tiebreaker_users') or ""
            if str(message.author.id) in tiebreaker_users_str:
                if message.author.id not in self.tiebreaker_submissions.get(message.guild.id, {}):
                    if message.attachments and any(att.content_type and att.content_type.startswith("audio/") for att in message.attachments):
                        self.tiebreaker_submissions[message.guild.id][message.author.id] = message.attachments[0].url
                        await message.add_reaction("⚔️")

                        if len(self.tiebreaker_submissions[message.guild.id]) == 2:
                            user_ids = list(self.tiebreaker_submissions[message.guild.id].keys())
                            track_urls = list(self.tiebreaker_submissions[message.guild.id].values())

                            p1_data = {"user_id": user_ids[0], "submission_id": -1, "track_url": track_urls[0]}
                            p2_data = {"user_id": user_ids[1], "submission_id": -1, "track_url": track_urls[1]}

                            p1_user = message.guild.get_member(user_ids[0])
                            p2_user = message.guild.get_member(user_ids[1])

                            embed = discord.Embed(title="⚔️ FINAL BATTLE! ⚔️", color=discord.Color.red())
                            embed.add_field(name=f"Duelist 1: {p1_user.display_name if p1_user else 'Unknown'}", value=f"Track: {track_urls[0]}", inline=False)
                            embed.add_field(name=f"Duelist 2: {p2_user.display_name if p2_user else 'Unknown'}", value=f"Track: {track_urls[1]}", inline=False)

                            if (review_channel_id := await database.get_setting(message.guild.id, 'review_channel_id')) and (review_channel := self.bot.get_channel(review_channel_id)):
                                await review_channel.send(embed=embed, view=KOTHBattleView(self.bot, p1_data, p2_data, is_tiebreaker=True))
            return

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

                    if submission_type == 'koth':
                        session_stats = self.current_koth_session[message.guild.id]
                        user_id = message.author.id
                        session_stats.setdefault(user_id, {'points': 0, 'wins': 0, 'submissions': 0})['submissions'] += 1

                    if hasattr(self.bot, 'app') and hasattr(self.bot.app, 'ws_manager'):
                        user_data = await self.bot.app.fetch_user_data(message.author.id)
                        await self.bot.app.ws_manager.broadcast(message.guild.id, {
                            "type": "new_submission",
                            "username": user_data['name'],
                            "avatar_url": user_data['avatar_url']
                        })
                        await self._broadcast_full_update(message.guild.id)

                    break

    koth_group = app_commands.Group(name="koth", description="Admin commands for King of the Hill.")

    @koth_group.command(name="addpoint", description="Manually adds points to a user.")
    @utils.has_permission("admin")
    @app_commands.describe(
        member="The member to give points to.",
        points="The number of points to add (defaults to 1).",
        scope="Where to add the points: the current battle or the all-time leaderboard."
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="Battle", value="battle"),
        app_commands.Choice(name="Leaderboard", value="leaderboard")
    ])
    async def koth_add_point(self, interaction: discord.Interaction, member: discord.Member, scope: str, points: int = 1):
        if scope == "battle":
            session_stats = self.current_koth_session[interaction.guild.id]
            session_stats.setdefault(member.id, {'points': 0, 'wins': 0})['points'] += points
            await interaction.response.send_message(f"✅ Added **{points}** battle point(s) to {member.mention}.", ephemeral=True)
        else:
            await database.adjust_koth_points(interaction.guild.id, member.id, points)
            await interaction.response.send_message(f"✅ Added **{points}** leaderboard point(s) to {member.mention}.", ephemeral=True)

        if panel_message := await self.get_panel_message(interaction.guild):
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)


    @koth_group.command(name="removepoint", description="Manually removes points from a user.")
    @utils.has_permission("admin")
    @app_commands.describe(
        member="The member to remove points from.",
        points="The number of points to remove (defaults to 1).",
        scope="Where to remove the points: the current battle or the all-time leaderboard."
    )
    @app_commands.choices(scope=[
        app_commands.Choice(name="Battle", value="battle"),
        app_commands.Choice(name="Leaderboard", value="leaderboard")
    ])
    async def koth_remove_point(self, interaction: discord.Interaction, member: discord.Member, scope: str, points: int = 1):
        if scope == "battle":
            session_stats = self.current_koth_session[interaction.guild.id]
            if member.id in session_stats:
                session_stats[member.id]['points'] -= points
            await interaction.response.send_message(f"✅ Removed **{points}** battle point(s) from {member.mention}.", ephemeral=True)
        else:
            await database.adjust_koth_points(interaction.guild.id, member.id, -points)
            await interaction.response.send_message(f"✅ Removed **{points}** leaderboard point(s) from {member.mention}.", ephemeral=True)

        if panel_message := await self.get_panel_message(interaction.guild):
            embed, view = await get_panel_embed_and_view(interaction.guild, self.bot)
            await panel_message.edit(embed=embed, view=view)

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

    use_group = app_commands.Group(name="use", description="Use an item from your inventory.")

    @use_group.command(name="pass", description="Use a Priority Pass to skip the submission queue.")
    async def use_pass(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        pass_count = await database.get_inventory_item_count(interaction.guild.id, interaction.user.id, "priority_pass")
        if pass_count <= 0:
            await interaction.followup.send("You don't have any Priority Passes to use! You can buy one from the `/shop`.", ephemeral=True)
            return

        submission_id = await database.get_latest_pending_submission_id(interaction.guild.id, interaction.user.id)
        if not submission_id:
            await interaction.followup.send("You don't have a track pending in the regular queue. Submit a track first, then use your pass!", ephemeral=True)
            return

        await database.use_inventory_item(interaction.guild.id, interaction.user.id, "priority_pass")
        await database.prioritize_submission(submission_id)

        log.info(f"User {interaction.user.id} used a priority pass on submission {submission_id}.")
        await interaction.followup.send("✅ Success! Your Priority Pass has been used, and your track has been moved to the front of the queue.", ephemeral=True)

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