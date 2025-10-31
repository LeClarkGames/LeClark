import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import logging
import re

import database
import config
import utils

log = logging.getLogger(__name__)

class ReportActionsView(discord.ui.View):
    def __init__(self, *, message_link: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Jump to Message", style=discord.ButtonStyle.link, url=message_link))

    async def _update_embed(self, interaction: discord.Interaction, decision: str, color: discord.Color):
        embed = interaction.message.embeds[0]
        embed.title = f"Report {decision}"
        embed.color = color
        embed.add_field(name="Handled by", value=interaction.user.mention, inline=False)
        embed.timestamp = datetime.now(timezone.utc)
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.style != discord.ButtonStyle.link:
                item.disabled = True
        await interaction.message.edit(content=None, embed=embed, view=self)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="report_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("You don't have permission to handle reports.", ephemeral=True)
        await self._update_embed(interaction, "Accepted", config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"])
        await interaction.response.send_message("Report marked as **Accepted**.", ephemeral=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="report_decline")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await utils.has_mod_role(interaction.user):
            return await interaction.response.send_message("You don't have permission to handle reports.", ephemeral=True)
        await self._update_embed(interaction, "Declined", config.BOT_CONFIG["EMBED_COLORS"]["ERROR"])
        await interaction.response.send_message("Report marked as **Declined**.", ephemeral=True)

class ReportModal(discord.ui.Modal, title="Submit a Report"):
    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
    
    problem_description = discord.ui.TextInput(label="Describe the problem", style=discord.TextStyle.paragraph, required=True, max_length=1024)
    message_link = discord.ui.TextInput(label="Paste the message link here", style=discord.TextStyle.short, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        link_pattern = re.compile(r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)")
        match = link_pattern.match(self.message_link.value)
        if not match or int(match.group(1)) != interaction.guild.id: return await interaction.response.send_message("❌ Invalid Message Link from this server.", ephemeral=True)
        guild_id, channel_id, message_id = map(int, match.groups())
        log_channel_id = await database.get_setting(interaction.guild.id, 'log_channel_id')
        if not log_channel_id: return await interaction.response.send_message("⚠️ Report system not configured on this server.", ephemeral=True)
        log_channel = self.bot.get_channel(log_channel_id)
        if not log_channel: return
        try:
            reported_channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            reported_message = await reported_channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return await interaction.response.send_message("❌ Could not find that message.", ephemeral=True)
        embed = discord.Embed(title="New Report", color=config.BOT_CONFIG["EMBED_COLORS"]["WARNING"], timestamp=datetime.now(timezone.utc))
        embed.set_author(name=f"Reported by {interaction.user}", icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Reported User", value=reported_message.author.mention)
        embed.add_field(name="In Channel", value=reported_channel.mention)
        embed.add_field(name="Reason", value=self.problem_description.value, inline=False)
        view = ReportActionsView(message_link=self.message_link.value)
        mentions = await utils.get_log_mentions(interaction.guild.id)
        await log_channel.send(content=mentions, embed=embed, view=view)
        await interaction.response.send_message("✅ Report sent successfully!", ephemeral=True)

class ReportTriggerView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Report a Message", style=discord.ButtonStyle.primary, custom_id="report_message_button")
    async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await database.get_setting(interaction.guild.id, 'reporting_system_enabled'):
            await interaction.response.send_message("The reporting system is currently disabled on this server.", ephemeral=True)
            return
        
        await interaction.response.send_modal(ReportModal(bot=self.bot))

class ReportingCog(commands.Cog, name="Reporting"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the reporting system is enabled for this guild."""
        is_enabled = await database.get_setting(interaction.guild.id, 'reporting_system_enabled')
        if not is_enabled:
            await interaction.response.send_message("The reporting system is disabled on this server.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="setup_report", description="Sends the 'Report a Message' button.")
    @app_commands.guild_only()
    @utils.has_permission("admin")
    async def setup_report(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        report_channel_id = await database.get_setting(interaction.guild.id, 'report_channel_id')
        if not report_channel_id:
            return await interaction.followup.send("❌ Report channel not set. Use `/settings report_channel` first.", ephemeral=True)
        
        report_channel = self.bot.get_channel(report_channel_id)
        if not report_channel:
            return await interaction.followup.send("❌ Could not find the configured report channel.", ephemeral=True)
            
        embed = discord.Embed(title="📝 Report a Rule-Breaker", description="If you see a message that violates our server rules, click the button below.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        view = ReportTriggerView(bot=self.bot)
        
        try:
            await report_channel.send(embed=embed, view=view)
            await interaction.followup.send(f"✅ Report button sent to {report_channel.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {report_channel.mention}.", ephemeral=True)
async def setup(bot: commands.Bot):
    await bot.add_cog(ReportingCog(bot))