import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
import logging
import re

import database
import config
import utils

log = logging.getLogger(__name__)

async def _mute_member(interaction_or_message: discord.Interaction | discord.Message, target: discord.Member, duration_minutes: int, reason: str, moderator: discord.Member):
    guild = target.guild
    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
    log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
    duration = timedelta(minutes=duration_minutes)
    try:
        await target.timeout(duration, reason=f"{reason} - by {moderator}")
        try:
            dm_embed = discord.Embed(title="You have been muted", description=f"You were muted in **{guild.name}**.", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"])
            dm_embed.add_field(name="Duration", value=f"{duration_minutes} minutes")
            dm_embed.add_field(name="Reason", value=reason)
            await target.send(embed=dm_embed)
        except discord.Forbidden:
            log.warning(f"Could not DM user {target.id} about their mute.")
        if log_channel:
            log_embed = discord.Embed(title="🔇 User Muted", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
            log_embed.add_field(name="User", value=target.mention, inline=False)
            log_embed.add_field(name="Moderator", value=moderator.mention, inline=False)
            log_embed.add_field(name="Duration", value=f"{duration_minutes} minutes", inline=False)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_channel.send(embed=log_embed)
        return True
    except discord.Forbidden:
        return False

async def _ban_member(interaction_or_message: discord.Interaction | discord.Message, target: discord.Member, reason: str, moderator: discord.Member):
    guild = target.guild
    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
    log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

    try:
        await target.ban(reason=f"{reason} - by {moderator}", delete_message_days=1)
        if log_channel:
            log_embed = discord.Embed(title="🔨 User Banned", color=config.BOT_CONFIG["EMBED_COLORS"]["ERROR"], timestamp=datetime.now(timezone.utc))
            log_embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
            log_embed.add_field(name="Moderator", value=moderator.mention, inline=False)
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_channel.send(embed=log_embed)
        return True
    except discord.Forbidden:
        return False

# ----------------- MOVED TO TOP LEVEL -----------------
async def _issue_warning(bot: commands.Bot, target: discord.Member, moderator: discord.Member, reason: str, interaction: discord.Interaction = None, original_message: discord.Message = None):
    """A central function to issue a warning and check for automated actions."""
    guild = target.guild
    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
    if not log_channel_id:
        if interaction and not interaction.response.is_done(): await interaction.response.send_message("⚠️ Log channel not set. Cannot issue warning.", ephemeral=True)
        return

    log_channel = bot.get_channel(log_channel_id)
    if not log_channel: return

    # Log the warning in the log channel
    log_embed = discord.Embed(title="User Warned", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
    log_embed.set_author(name=str(target), icon_url=target.display_avatar.url)
    log_embed.add_field(name="User", value=target.mention, inline=True)
    log_embed.add_field(name="Moderator", value=moderator.mention, inline=True)
    log_embed.add_field(name="Reason", value=reason, inline=False)
    if original_message:
        log_embed.add_field(name="Original Message", value=f"```{original_message.content[:1000]}```", inline=False)

    log_msg = await log_channel.send(embed=log_embed)

    await database.add_warning(guild.id, target.id, moderator.id, reason, log_msg.id)
    new_warnings_count = await database.get_warnings_count(guild.id, target.id)

    warning_limit = await database.get_setting(guild.id, 'warning_limit') or 3

    if new_warnings_count >= warning_limit:
        action_type = await database.get_setting(guild.id, 'warning_action') or 'mute'
        duration = await database.get_setting(guild.id, 'warning_action_duration') or 60
        action_reason = f"Automatic action: Reached {new_warnings_count}/{warning_limit} warnings."

        action_log_embed = log_embed
        action_log_embed.color = config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]

        ctx = interaction or original_message
        if action_type == 'mute':
            await _mute_member(ctx, target, duration, action_reason, bot.user) # Used bot.user for the moderator
            action_log_embed.title = f"User Auto-Muted ({new_warnings_count}/{warning_limit})"
        elif action_type == 'kick':
            await target.kick(reason=action_reason)
            action_log_embed.title = f"User Auto-Kicked ({new_warnings_count}/{warning_limit})"
        elif action_type == 'ban':
            await _ban_member(ctx, target, action_reason, bot.user) # Used bot.user for the moderator
            action_log_embed.title = f"User Auto-Banned ({new_warnings_count}/{warning_limit})"

        await log_msg.edit(embed=action_log_embed)
        await database.clear_warnings(guild.id, target.id)
    else:
        log_embed.title = f"User Warned ({new_warnings_count}/{warning_limit})"
        await log_msg.edit(embed=log_embed)

    if interaction and not interaction.response.is_done():
         await interaction.response.send_message(f"✅ **{target.display_name}** has been warned. They now have **{new_warnings_count}** warning(s).", ephemeral=True)
# ----------------- END MOVED SECTION -----------------

class MuteApprovalView(discord.ui.View):
# ... (MuteApprovalView content is unchanged)
    def __init__(self, moderator: discord.Member, target: discord.Member, duration: int, reason: str):
        super().__init__(timeout=config.BOT_CONFIG["APPROVAL_TIMEOUT_SECONDS"])
        self.moderator = moderator
        self.target = target
        self.duration = duration
        self.reason = reason
        self.message = None

    async def on_timeout(self):
        if self.message:
            await _mute_member(self.message, self.target, config.BOT_CONFIG["DEFAULT_MUTE_MINS"], f"(Auto-Mute) {self.reason}", self.moderator)
            timeout_embed = discord.Embed(title="⌛ Mute Request Timed Out", description=f"Mute request was not approved.\n**User has been auto-muted for {config.BOT_CONFIG['DEFAULT_MUTE_MINS']} minutes.**", color=discord.Color.gray())
            await self.message.edit(content=None, embed=timeout_embed, view=None)

    async def _update_message(self, interaction: discord.Interaction, approved: bool):
        self.stop()
        embed = interaction.message.embeds[0]
        outcome = "Approved" if approved else "Declined"
        color = config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"] if approved else config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]
        embed.title = f"Mute Request {outcome}"
        embed.color = color
        embed.set_field_at(0, name=f"Moderator ({outcome})", value=self.moderator.mention, inline=True)
        embed.add_field(name="Decision By", value=interaction.user.mention, inline=True)
        await interaction.message.edit(content=None, embed=embed, view=None)

    @discord.ui.button(label="✅ Approve Mute", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user):
            return await interaction.response.send_message("Only Bot Admins can approve this action.", ephemeral=True)
        success = await _mute_member(interaction, self.target, self.duration, self.reason, self.moderator)
        if success: await self._update_message(interaction, approved=True)
        else: await interaction.response.send_message("❌ Failed to mute user. My role might be too low.", ephemeral=True)

    @discord.ui.button(label="🚫 Decline Mute", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user):
            return await interaction.response.send_message("Only Bot Admins can decline this action.", ephemeral=True)
        await self._update_message(interaction, approved=False)

class BanApprovalView(discord.ui.View):
# ... (BanApprovalView content is unchanged)
    def __init__(self, moderator: discord.Member, target: discord.Member, reason: str):
        super().__init__(timeout=config.BOT_CONFIG["APPROVAL_TIMEOUT_SECONDS"])
        self.moderator = moderator
        self.target = target
        self.reason = reason
        self.message = None

    async def on_timeout(self):
        if self.message:
            timeout_embed = discord.Embed(title="⌛ Ban Request Timed Out", description=f"The ban request for {self.target.mention} was not actioned in time and has expired.", color=discord.Color.gray())
            await self.message.edit(content=None, embed=timeout_embed, view=None)

    async def _update_message(self, interaction: discord.Interaction, approved: bool):
        self.stop()
        embed = interaction.message.embeds[0]
        outcome = "Approved" if approved else "Declined"
        color = config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"] if approved else config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]
        embed.title = f"Ban Request {outcome}"
        embed.color = color
        embed.set_field_at(0, name=f"Moderator ({outcome})", value=self.moderator.mention, inline=True)
        embed.add_field(name="Decision By", value=interaction.user.mention, inline=True)
        await interaction.message.edit(content=None, embed=embed, view=None)

    @discord.ui.button(label="✅ Approve Ban", style=discord.ButtonStyle.danger)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user):
            return await interaction.response.send_message("Only Bot Admins can approve this action.", ephemeral=True)
        success = await _ban_member(interaction, self.target, self.reason, self.moderator)
        if success:
            await self._update_message(interaction, approved=True)
        else:
            await interaction.response.send_message("❌ Failed to ban user. My role might be too low.", ephemeral=True)

    @discord.ui.button(label="🚫 Decline Ban", style=discord.ButtonStyle.secondary)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_admin_role(interaction.user):
            return await interaction.response.send_message("Only Bot Admins can decline this action.", ephemeral=True)
        await self._update_message(interaction, approved=False)

class BanDecisionView(discord.ui.View):
# ... (BanDecisionView content is unchanged)
    def __init__(self, member: discord.Member):
        super().__init__(timeout=config.BOT_CONFIG["APPROVAL_TIMEOUT_SECONDS"])
        self.member = member
        self.message = None

    async def on_timeout(self):
        if self.message:
            timeout_embed = discord.Embed(title="⚠️ Ban Request Expired", description=f"Ban request for {self.member.mention} expired.", color=discord.Color.gray())
            await self.message.edit(content=None, embed=timeout_embed, view=None)

    @discord.ui.button(label="🚫 Ban", style=discord.ButtonStyle.danger)
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("You don't have permission.", ephemeral=True)
        try:
            await self.member.ban(reason=f"Banned by {interaction.user} for repeated infractions.", delete_message_days=1)
            final_embed = discord.Embed(title="🔨 User Banned", description=f"{self.member.mention} was banned by {interaction.user.mention}.", color=config.BOT_CONFIG["EMBED_COLORS"]["ERROR"])
            await database.clear_warnings(self.member.guild.id, self.member.id)
            self.stop()
            await interaction.response.edit_message(content=None, embed=final_embed, view=None)
        except discord.Forbidden: return await interaction.response.send_message("❌ Failed to ban user.", ephemeral=True)

    @discord.ui.button(label="✅ Forgive", style=discord.ButtonStyle.secondary)
    async def dont_ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user): return await interaction.response.send_message("You don't have permission.", ephemeral=True)
        final_embed = discord.Embed(title="✅ User Forgiven", description=f"All warnings for {self.member.mention} have been cleared by {interaction.user.mention}.", color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"])
        await database.clear_warnings(self.member.guild.id, self.member.id)
        self.stop()
        await interaction.response.edit_message(content=None, embed=final_embed, view=None)

@app_commands.guild_only()
class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bad_words_cache = {}

    async def _update_bad_words_cache(self, guild_id: int):
        """Fetches bad words from the database and updates the cache for a single guild."""
        self.bad_words_cache[guild_id] = await database.get_bad_words(guild_id)
        log.info(f"Updated bad words cache for guild {guild_id}.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Populates the cache when the bot starts."""
        log.info("Populating bad words cache for all guilds...")
        for guild in self.bot.guilds:
            await self._update_bad_words_cache(guild.id)
        log.info("Bad words cache populated.")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        """Adds a new guild to the cache when the bot joins."""
        await self._update_bad_words_cache(guild.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        """Removes a guild from the cache when the bot leaves."""
        self.bad_words_cache.pop(guild.id, None)
        log.info(f"Removed guild {guild.id} from bad words cache.")
    
    # *** _issue_warning was here, now it's a top-level function ***

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not message.content or (message.author.bot and message.author.id == self.bot.user.id):
            return

        # --- MODIFICATION: Use the cache ---
        bad_words_list = self.bad_words_cache.get(message.guild.id)
        if not bad_words_list:
            return
        # --- END MODIFICATION ---

        for bad_word in bad_words_list:
            # Using a regex for whole-word matching
            if re.search(r'\\b' + re.escape(bad_word) + r'\\b', message.content.lower()):
                try:
                    await message.delete()
                    # DM the user that their message was deleted
                    await message.author.send(f"Your message in **{message.guild.name}** was deleted for containing a forbidden word: `||{bad_word}||`.")
                except (discord.Forbidden, discord.HTTPException):
                    # Can't delete the message or DM the user, but we can still warn them
                    pass

                # Issue the warning - CALLING THE TOP-LEVEL FUNCTION
                reason = f"Automatic warning for using a forbidden word: ||{bad_word}||"
                await _issue_warning(self.bot, message.author, self.bot.user, reason, original_message=message)
                return # Stop after the first bad word is found

    async def process_bad_word(self, message: discord.Message, bad_word: str):
        log_channel_id = await database.get_setting(message.guild.id, 'log_channel_id')
        if not log_channel_id: return
        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel: return
        try:
            await message.delete()
            await message.author.send(f"Your message in **{message.guild.name}** was deleted for containing a forbidden word: `||{bad_word}||`.")
        except (discord.Forbidden, discord.HTTPException): pass
        log_embed = discord.Embed(title="User Warned", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
        log_embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        log_embed.add_field(name="User", value=message.author.mention)
        log_embed.add_field(name="Channel", value=message.channel.mention)
        log_embed.add_field(name="Forbidden Word", value=f"||{bad_word}||", inline=False)
        log_embed.add_field(name="Original Message", value=f"```{message.content[:1000]}```", inline=False)
        warning_msg = await log_channel.send(embed=log_embed)
        await database.add_warning(message.guild.id, message.author.id, warning_msg.id)
        new_warnings_count = await database.get_warnings_count(message.guild.id, message.author.id)
        log_embed.title = f"User Warned ({new_warnings_count}/2)"
        if new_warnings_count >= 2:
            log_embed.color = config.BOT_CONFIG["EMBED_COLORS"]["ERROR"]
            log_embed.add_field(name="ACTION REQUIRED", value="This user has reached the warning limit.", inline=False)
            view = BanDecisionView(member=message.author)
            mentions = await utils.get_log_mentions(message.guild.id)
            await warning_msg.edit(content=mentions, embed=log_embed, view=view)
            view.message = warning_msg
        else:
            await warning_msg.edit(embed=log_embed)

    filter_group = app_commands.Group(name="filter", description="Manage the server's bad word filter.")
# ... (filter_group commands are unchanged)

    @filter_group.command(name="add", description="Adds a word to the filter.")
    @utils.has_permission("mod")
    async def filter_add(self, interaction: discord.Interaction, word: str):
        success = await database.add_bad_word(interaction.guild.id, word)
        if success:
            # --- IMPROVED: Update the cache after changing the database ---
            await self._update_bad_words_cache(interaction.guild.id)
            await interaction.response.send_message(f"✅ The word `||{word}||` has been added to the filter.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ The word `||{word}||` is already in the filter.", ephemeral=True)

    @filter_group.command(name="remove", description="Removes a word from the filter.")
    @utils.has_permission("admin")
    async def filter_remove(self, interaction: discord.Interaction, word: str):
        success = await database.remove_bad_word(interaction.guild.id, word)
        if success:
            await self._update_bad_words_cache(interaction.guild.id)
            await interaction.response.send_message(f"✅ The word `||{word}||` has been removed from the filter.", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ The word `||{word}||` was not found in the filter.", ephemeral=True)

    @filter_group.command(name="list", description="Lists all words in the filter.")
    @utils.has_permission("mod")
    async def filter_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        words = self.bad_words_cache.get(interaction.guild.id, [])
        if not words:
            return await interaction.followup.send("The filter list is currently empty.", ephemeral=True)
        
        formatted_list = ", ".join(f"`{word}`" for word in words)
        embed = discord.Embed(title=f"Filtered Words for {interaction.guild.name}", description=formatted_list, color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        await interaction.followup.send(embed=embed, ephemeral=True)

    mod_group = app_commands.Group(name="mod", description="Moderation commands.")

    @mod_group.command(name="mute", description="Mutes a user for a specified duration.")
    @app_commands.describe(member="The user to mute", minutes="How many minutes to mute for", reason="The reason for the mute")
    @utils.has_permission("mod")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str):
# ... (mute content is unchanged)
        if member.id == interaction.user.id: return await interaction.response.send_message("You cannot mute yourself.", ephemeral=True)
        if member.top_role >= interaction.user.top_role: return await interaction.response.send_message("You cannot mute this member.", ephemeral=True)
        if member.is_timed_out(): return await interaction.response.send_message("This user is already muted.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
        log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None
        if not log_channel: return await interaction.followup.send("❌ The log channel is not configured.", ephemeral=True)
        
        is_admin = await utils.has_admin_role(interaction.user)
        if is_admin:
            success = await _mute_member(interaction, member, minutes, reason, interaction.user)
            if success: await interaction.followup.send(f"🔇 **{member.display_name}** has been muted for {minutes} minutes.", ephemeral=True)
            else: await interaction.followup.send("❌ Failed to mute user. My role might be too low.", ephemeral=True)
        else:
            embed = discord.Embed(title="Mute Request Requires Approval", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Target User", value=member.mention, inline=True)
            embed.add_field(name="Requested Duration", value=f"{minutes} minutes")
            embed.add_field(name="Reason", value=reason, inline=False)
            view = MuteApprovalView(interaction.user, member, minutes, reason)
            mentions = await utils.get_log_mentions(interaction.guild.id)
            msg = await log_channel.send(content=mentions, embed=embed, view=view)
            view.message = msg
            await interaction.followup.send(f"✅ Your mute request has been sent for approval.", ephemeral=True)

    @mod_group.command(name="unmute", description="Removes a user's timeout.")
    @app_commands.describe(member="The user to unmute", reason="The reason for unmuting")
    @utils.has_permission("mod")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: str = None):
# ... (unmute content is unchanged)
        if not member.is_timed_out():
            return await interaction.response.send_message("This user is not currently muted.", ephemeral=True)
        try:
            await member.timeout(None, reason=f"{reason or 'No reason'} - Unmuted by {interaction.user}")
            log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                embed = discord.Embed(title="🔊 User Unmuted", color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"], timestamp=datetime.now(timezone.utc))
                embed.add_field(name="User", value=member.mention)
                embed.add_field(name="Moderator", value=interaction.user.mention)
                embed.add_field(name="Reason", value=reason or "No reason provided")
                await log_channel.send(embed=embed)
            await interaction.response.send_message(f"🔊 **{member.display_name}** has been unmuted.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to unmute this user.", ephemeral=True)

    @mod_group.command(name="kick", description="Kicks a user from the server.")
    @app_commands.describe(member="The user to kick", reason="The reason for kicking")
    @utils.has_permission("mod")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str):
# ... (kick content is unchanged)
        if member.top_role >= interaction.user.top_role: return await interaction.response.send_message("You cannot kick this member.", ephemeral=True)
        try:
            await member.kick(reason=f"{reason} - Kicked by {interaction.user}")
            
            log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
            if log_channel_id:
                log_channel = self.bot.get_channel(log_channel_id)
                embed = discord.Embed(title="👢 User Kicked", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
                embed.add_field(name="User", value=f"{member} ({member.id})")
                embed.add_field(name="Moderator", value=interaction.user.mention)
                embed.add_field(name="Reason", value=reason)
                await log_channel.send(embed=embed)

            await interaction.response.send_message(f"👢 **{member}** has been kicked.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to kick this user.", ephemeral=True)

    @mod_group.command(name="ban", description="Bans a user from the server.")
    @app_commands.describe(member="The user to ban", reason="The reason for banning")
    @utils.has_permission("mod")
    async def ban(self, interaction: discord.Interaction, member: discord.Member, reason: str):
# ... (ban content is unchanged)
        if member.top_role >= interaction.user.top_role: return await interaction.response.send_message("You cannot ban this member.", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
        log_channel = self.bot.get_channel(log_channel_id) if log_channel_id else None
        if not log_channel: return await interaction.followup.send("❌ The log channel is not configured.", ephemeral=True)

        is_admin = await utils.has_admin_role(interaction.user)
        if is_admin:
            success = await _ban_member(interaction, member, reason, interaction.user)
            if success:
                await interaction.followup.send(f"🔨 **{member}** has been banned.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Failed to ban user. My role might be too low.", ephemeral=True)
        else:
            embed = discord.Embed(title="Ban Request Requires Approval", color=config.BOT_CONFIG["EMBED_COLORS"]["ERROR"], timestamp=datetime.now(timezone.utc))
            embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
            embed.add_field(name="Target User", value=member.mention, inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            view = BanApprovalView(interaction.user, member, reason)
            mentions = await utils.get_log_mentions(interaction.guild.id)
            msg = await log_channel.send(content=mentions, embed=embed, view=view)
            view.message = msg
            await interaction.followup.send(f"✅ Your ban request has been sent for approval.", ephemeral=True)

    @mod_group.command(name="announce", description="Sends a message to the moderator chat channel.")
    @app_commands.describe(message="The message you want to send.")
    @utils.has_permission("admin")
    async def announce(self, interaction: discord.Interaction, message: str):
# ... (announce content is unchanged)
        await interaction.response.defer(ephemeral=True)

        mod_chat_channel_id = await database.get_setting(interaction.guild.id, 'mod_chat_channel_id')
        if not mod_chat_channel_id:
            return await interaction.followup.send("❌ The mod chat channel is not configured. Please use `/settings mod_chat_channel` first.", ephemeral=True)

        mod_chat_channel = self.bot.get_channel(mod_chat_channel_id)
        if not mod_chat_channel:
            return await interaction.followup.send("❌ I could not find the configured mod chat channel.", ephemeral=True)

        embed = discord.Embed(
            title="📢 Staff Announcement",
            description=message,
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"],
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"Sent by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        try:
            await mod_chat_channel.send(embed=embed)
            await interaction.followup.send("✅ Your message has been sent to the mod chat channel.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to send messages in the mod chat channel.", ephemeral=True)

    @app_commands.command(name="warn", description="Warns a member for a specific reason.")
    @utils.has_permission("mod")
    @app_commands.describe(member="The member to warn.", reason="The reason for the warning.")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if member.bot: return await interaction.response.send_message("You cannot warn a bot.", ephemeral=True)
        if member.id == interaction.user.id: return await interaction.response.send_message("You cannot warn yourself.", ephemeral=True)
        if member.top_role >= interaction.user.top_role: return await interaction.response.send_message("You cannot warn this member due to their role hierarchy.", ephemeral=True)
        # CALLING THE TOP-LEVEL FUNCTION
        await _issue_warning(self.bot, member, interaction.user, reason, interaction=interaction)

    @app_commands.command(name="warnings", description="Check the warnings for a specific member.")
    @utils.has_permission("mod")
    @app_commands.describe(member="The member whose warnings you want to see.")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
# ... (warnings content is unchanged)
        await interaction.response.defer(ephemeral=True)
        user_warnings = await database.get_warnings(interaction.guild.id, member.id)
        if not user_warnings:
            return await interaction.followup.send(f"**{member.display_name}** has no warnings.", ephemeral=True)
        embed = discord.Embed(title=f"Warnings for {member.display_name}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        embed.set_thumbnail(url=member.display_avatar.url)
        for mod_id, reason, issued_at_str, warn_id in user_warnings:
            mod = interaction.guild.get_member(mod_id)
            issued_at = datetime.fromisoformat(issued_at_str)
            embed.add_field(name=f"Warning #{warn_id} - <t:{int(issued_at.timestamp())}:R>", value=f"**Moderator:** {mod.mention if mod else f'ID: {mod_id}'}\n**Reason:** {reason}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="clearwarnings", description="Clears all warnings for a specific member.")
    @utils.has_permission("mod")
    @app_commands.describe(member="The member whose warnings you want to clear.")
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
# ... (clearwarnings content is unchanged)
        await database.clear_warnings(interaction.guild.id, member.id)
        await interaction.response.send_message(f"✅ All warnings for **{member.display_name}** have been cleared.", ephemeral=True)
        log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
        if log_channel_id:
            log_channel = self.bot.get_channel(log_channel_id)
            await log_channel.send(f"ℹ️ All warnings for {member.mention} were cleared by {interaction.user.mention}.")

    @mod_group.command(name="guide", description="Posts the moderation team command guide to the log channel.")
    @utils.has_permission("admin")
    async def guide(self, interaction: discord.Interaction):
# ... (guide content is unchanged)
        await interaction.response.defer(ephemeral=True)

        log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
        if not log_channel_id:
            return await interaction.followup.send("❌ The log channel is not configured.", ephemeral=True)
        
        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel:
            return await interaction.followup.send("❌ I could not find the configured log channel.", ephemeral=True)

        guide_text = (
            "Moderator Only Commands:\n"
            "/mod mute **member** **minutes** **reason**\n"
            "/mod ban **member** **reason**\n"
            "/mod kick **member** **reason**\n"
            "/warn **member** **reason**\n"
            "/warnings **member**\n"
            "/clearwarnings **member**\n"
            "/filter add **word**\n"
            "/filter list\n"
            "/unmute **member** **reason**\n"
            "/embed\n"
            ""
            "Admin Only Commands:\n"
            "/settings\n"
            "/create_reaction_role_message **channel** **message_content**\n"
            "/filter remove **word**\n"
            "/widget\n"
            "/koth addpoint **member** **scope** **points**\n"
            "/koth removepoint **member** **scope** **points**\n"
            "/mod announce **message**\n"
            "/mod guide - send this guide\n"
            "/setup_report\n"
            "/setup_submission_panel\n"
            "/setup_verification\n"
        )

        embed = discord.Embed(
            title="📜 Moderation Team Guide",
            description=guide_text,
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"],
            timestamp=datetime.now(timezone.utc)
        )
        
        mentions = await utils.get_log_mentions(interaction.guild.id)
        
        try:
            await log_channel.send(content=mentions, embed=embed)
            await interaction.followup.send("✅ The moderator guide has been sent to the log channel.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to send messages in the log channel.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))