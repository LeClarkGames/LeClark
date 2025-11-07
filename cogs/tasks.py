import discord
from discord.ext import commands, tasks
import logging
import json
import os
from datetime import datetime
import asyncio
import re

import database 

log = logging.getLogger(__name__)

class TasksCog(commands.Cog, name="Background Tasks"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.daily_backup.start()

    def cog_unload(self):
        self.daily_backup.cancel()

    @tasks.loop(hours=24)
    async def daily_backup(self):
        # Ensure the main backup directory exists
        if not os.path.exists("BackUps"):
            os.makedirs("BackUps")
            
        log.info("Starting daily server data backup...")
        
        for guild in self.bot.guilds:
            backup_data = {}
            
            # Fetch all settings and data for the guild
            # For a complete backup, you would fetch from every relevant table
            backup_data['guild_settings'] = await database.get_all_settings(guild.id)
            backup_data['ranking_leaderboard'] = await database.get_leaderboard(guild.id, limit=999)
            # You could add more fetches for warnings, reaction roles, etc.
            
            # Sanitize the guild name to remove characters that are illegal in folder names
            sanitized_name = re.sub(r'[\\/*?:"<>|]', "", guild.name)
            guild_dir = f"BackUps/{sanitized_name.replace(' ', '_')}_{guild.id}"
            
            if not os.path.exists(guild_dir):
                os.makedirs(guild_dir)
            
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            file_path = f"{guild_dir}/{sanitized_name.replace(' ', '_')}_{timestamp}_backup.json"
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    # A custom converter is needed to handle discord.py objects if you back them up directly
                    # For simple data from the DB, this is fine.
                    json.dump(backup_data, f, ensure_ascii=False, indent=4)
                log.info(f"Successfully backed up data for guild: {guild.name} ({guild.id})")
            except Exception as e:
                log.error(f"Failed to write backup for guild {guild.name}: {e}")

    @daily_backup.before_loop
    async def before_daily_backup(self):
        await self.bot.wait_until_ready()
        log.info("Backup task is ready.")

async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCog(bot))