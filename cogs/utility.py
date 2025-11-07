import discord
from discord import app_commands
from discord.ext import commands
import logging
import re
from typing import Optional
import os

import config
import utils

log = logging.getLogger(__name__)

def is_valid_hex_color(hex_string: str) -> Optional[int]:
    match = re.compile(r'^#?([A-Fa-f0-9]{6})$').match(hex_string)
    if match:
        return int(match.group(1), 16)
    return None

class UtilityCog(commands.Cog, name="Utility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))