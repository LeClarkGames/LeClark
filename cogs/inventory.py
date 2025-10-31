import discord
from discord import app_commands
from discord.ext import commands

import database
import config

class InventoryCog(commands.Cog, name="Inventory"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="inventory", description="Check the items you own.")
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        pass_count = await database.get_inventory_item_count(interaction.guild.id, interaction.user.id, "priority_pass")

        embed = discord.Embed(
            title=f"Inventory for {interaction.user.display_name}",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        # Add items to the embed
        embed.add_field(
            name="🎟️ Submission Priority Passes",
            value=f"**Quantity:** {pass_count}",
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(InventoryCog(bot))