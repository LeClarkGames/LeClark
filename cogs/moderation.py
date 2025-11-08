# cogs/moderation.py

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

@app_commands.guild_only()
class ModerationCog(commands.Cog, name="Moderation"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def mute_member(self, interaction_or_message: discord.Interaction | discord.Message, target: discord.Member, duration_minutes: int, reason: str, moderator: discord.Member):
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

    async def ban_member(self, interaction_or_message: discord.Interaction | discord.Message, target: discord.Member, reason: str, moderator: discord.Member):
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

    async def issue_warning(self, target: discord.Member, moderator: discord.Member, reason: str, interaction: discord.Interaction = None, original_message: discord.Message = None):
        """A central function to issue a warning and check for automated actions."""
        guild = target.guild
        log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
        if not log_channel_id:
            if interaction and not interaction.response.is_done(): await interaction.response.send_message("⚠️ Log channel not set. Cannot issue warning.", ephemeral=True)
            return

        log_channel = self.bot.get_channel(log_channel_id)
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
                await self.mute_member(ctx, target, duration, action_reason, self.bot.user) # Used self.bot.user for the moderator
                action_log_embed.title = f"User Auto-Muted ({new_warnings_count}/{warning_limit})"
            elif action_type == 'kick':
                await target.kick(reason=action_reason)
                action_log_embed.title = f"User Auto-Kicked ({new_warnings_count}/{warning_limit})"
            elif action_type == 'ban':
                await self.ban_member(ctx, target, action_reason, self.bot.user) # Used self.bot.user for the moderator
                action_log_embed.title = f"User Auto-Banned ({new_warnings_count}/{warning_limit})"

            await log_msg.edit(embed=action_log_embed)
            await database.clear_warnings(guild.id, target.id)
        else:
            log_embed.title = f"User Warned ({new_warnings_count}/{warning_limit})"
            await log_msg.edit(embed=action_log_embed)

        if interaction and not interaction.response.is_done():
             await interaction.response.send_message(f"✅ **{target.display_name}** has been warned. They now have **{new_warnings_count}** warning(s).", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))