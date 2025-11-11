import os
import logging
import asyncio
from dotenv import load_dotenv

load_dotenv()

import discord
from discord.ext import commands

import database
import config
from web_server import app
from cogs.verification import VerificationButton
from cogs.submissions import (SubmissionViewOpen, SubmissionViewClosed)
from cogs.role_giver import RoleGiverView

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)-8s] %(name)-12s: %(message)s", datefmt="%Y-m-d %H:%M:%S")
log = logging.getLogger(__name__)

APP_ENV = os.getenv("APP_ENV", "development")
log.info(f"Running in {APP_ENV} mode.")

if APP_ENV == "production":
    TOKEN = os.getenv("BOT_TOKEN_MAIN")
else:
    TOKEN = os.getenv("BOT_TOKEN_TEST")

class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents):
        super().__init__(command_prefix="!", intents=intents)
        self.action_queue = asyncio.Queue()

    async def setup_hook(self):

        app.bot_instance = self

        port = int(os.getenv("SERVER_PORT", os.getenv("PORT", 8080)))
        self.loop.create_task(app.run_task(host='0.0.0.0', port=port))
        log.info(f"Started background web server task on port {port}.")
        
        await database.initialize_database()
        
        self.add_view(VerificationButton(bot=self))
        self.add_view(RoleGiverView(bot=self))
        self.add_view(SubmissionViewOpen(bot=self))
        self.add_view(SubmissionViewClosed(bot=self))
        log.info("Registered persistent UI views.")

        cogs_to_load = [
            "cogs.settings", "cogs.moderation",
            "cogs.verification", "cogs.temp_vc", 
            "cogs.submissions", "cogs.panel_handler",
            "cogs.ranking", "cogs.role_giver", "cogs.panel_watcher"
        ]
        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                log.info(f"Successfully loaded extension: {cog}")
            except Exception as e:
                log.error(f"Failed to load extension {cog}: {e}", exc_info=True)
        
        log.info("Syncing application commands...")
        synced = await self.tree.sync()
        log.info(f"Synced {len(synced)} commands globally.")
        
    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        log.info("Bot is ready! 🚀")
        activity = discord.Activity(name=config.BOT_CONFIG["ACTIVITY_NAME"], type=discord.ActivityType.watching)
        await self.change_presence(activity=activity)
        log.info(f"Set activity to: Watching {config.BOT_CONFIG['ACTIVITY_NAME']}")

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.voice_states = True
    intents.presences = True
    
    bot = MyBot(intents=intents)
    bot.run(TOKEN)