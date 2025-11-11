import discord
from discord.ext import commands, tasks
import logging
import asyncio

import database

log = logging.getLogger(__name__)

class PanelWatcherCog(commands.Cog, name="Panel Watcher"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_panels.start()

    def cog_unload(self):
        self.check_panels.cancel()

    async def check_single_panel(self, guild: discord.Guild, panel_type: str):
        """Helper function to check and repost a single panel type."""
        
        cog_name = ""
        channel_id_key = ""
        message_id_key = ""

        if panel_type == "verification":
            cog_name = "Verification"
            channel_id_key = "verification_channel_id"
            message_id_key = "verification_message_id"
        elif panel_type == "submissions":
            cog_name = "Submissions"
            channel_id_key = "review_channel_id"
            message_id_key = "review_panel_message_id"
        elif panel_type == "role_giver":
            cog_name = "Role Giver"
            channel_id_key = "role_giver_channel_id"
            message_id_key = "role_giver_message_id"
        else:
            return

        settings = await database.get_all_settings(guild.id)
        channel_id = settings.get(channel_id_key)
        message_id = settings.get(message_id_key)

        if not channel_id or not message_id:
            return

        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if channel:
                await channel.fetch_message(message_id)
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning(f"Panel '{panel_type}' missing in guild {guild.id}. Attempting to repost. Error: {e}")
        
        try:
            cog = self.bot.get_cog(cog_name)
            channel = self.bot.get_channel(channel_id)
            if cog and channel:
                success, msg = await cog.post_panel(channel)
                if success:
                    log.info(f"Successfully reposted '{panel_type}' panel in guild {guild.id}.")
                else:
                    log.error(f"Failed to repost '{panel_type}' panel in guild {guild.id}: {msg}")
        except Exception as e:
            log.error(f"Critical error while trying to repost '{panel_type}' panel in guild {guild.id}: {e}", exc_info=True)


    @tasks.loop(minutes=5)
    async def check_panels(self):
        """Periodically checks if all configured panels still exist."""
        for guild in self.bot.guilds:
            await self.check_single_panel(guild, "verification")
            await asyncio.sleep(1)
            await self.check_single_panel(guild, "submissions")
            await asyncio.sleep(1)
            await self.check_single_panel(guild, "role_giver")
            await asyncio.sleep(1)

    @check_panels.before_loop
    async def before_check_panels(self):
        await self.bot.wait_until_ready()
        log.info("Starting persistent panel watcher...")

async def setup(bot: commands.Bot):
    await bot.add_cog(PanelWatcherCog(bot))