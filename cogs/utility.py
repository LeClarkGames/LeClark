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

# --- Helper to validate Hex Color ---
def is_valid_hex_color(hex_string: str) -> Optional[int]:
    match = re.compile(r'^#?([A-Fa-f0-9]{6})$').match(hex_string)
    if match:
        return int(match.group(1), 16)
    return None

# --- Main Cog ---
class UtilityCog(commands.Cog, name="Utility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="dashboard", description="Get the link to the server activity dashboard.")
    @utils.has_permission("mod")
    async def dashboard(self, interaction: discord.Interaction):
        base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
        link = f"{base_url}/dashboard/{interaction.guild.id}"
        
        embed = discord.Embed(
            title="📊 Server Activity Dashboard",
            description=f"Click the button below to view detailed server and member statistics. This link is for staff members only.",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Dashboard", url=link, emoji="🔗"))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))