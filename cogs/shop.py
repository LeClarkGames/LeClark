import discord
from discord import app_commands
from discord.ext import commands
import logging
import re

import database
import config
import utils

log = logging.getLogger(__name__)

# This modal is still needed for the initial purchase
class CustomRoleModal(discord.ui.Modal):
    def __init__(self, title: str, current_name: str = "", current_color: str = "#99aab5"):
        super().__init__(title=title)
        self.role_name = discord.ui.TextInput(label="Role Name", placeholder="Enter a name for your role.", default=current_name, max_length=50, required=True)
        self.role_color = discord.ui.TextInput(label="Role Color (Hex Code)", placeholder="e.g., #ff00ff or ff00ff", default=current_color, max_length=7, required=True)
        self.add_item(self.role_name)
        self.add_item(self.role_color)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        hex_color_pattern = re.compile(r'^#?([A-Fa-f0-9]{6})$')
        match = hex_color_pattern.match(self.role_color.value)
        if not match:
            return await interaction.followup.send("❌ Invalid hex color code.", ephemeral=True)
        
        color = discord.Color(int(match.group(1), 16))

        # --- MODIFICATION: Fetch cost from DB ---
        cost = await database.get_setting(interaction.guild.id, 'custom_role_cost') or 100
        balance = await database.get_koth_points(interaction.guild.id, interaction.user.id)
        if balance < cost:
            return await interaction.followup.send(f"You don't have enough points. You need {cost} but only have {balance}.", ephemeral=True)

        try:
            new_role = await interaction.guild.create_role(name=self.role_name.value, color=color)
            await interaction.user.add_roles(new_role)
            await database.set_user_custom_role(interaction.guild.id, interaction.user.id, new_role.id)
            await database.adjust_koth_points(interaction.guild.id, interaction.user.id, -cost)
            await interaction.followup.send(f"🎉 Success! You can now manage your new role with the `/customize` command.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to create roles.", ephemeral=True)

class ShopView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Buy Custom Role", style=discord.ButtonStyle.success, emoji="🎨", row=0)
    async def buy_custom_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await database.get_user_custom_role(interaction.guild.id, interaction.user.id):
            return await interaction.response.send_message("You already own a custom role! Use `/customize` to manage it.", ephemeral=True)
        await interaction.response.send_modal(CustomRoleModal(title="Purchase a Custom Role"))

    @discord.ui.button(label="Buy 1hr XP Boost", style=discord.ButtonStyle.primary, emoji="🚀", row=1)
    async def buy_xp_boost(self, interaction: discord.Interaction, button: discord.ui.Button):
        # --- MODIFICATION: Fetch cost from DB ---
        cost = await database.get_setting(interaction.guild.id, 'xp_boost_cost') or 25
        balance = await database.get_koth_points(interaction.guild.id, interaction.user.id)
        if balance < cost:
            return await interaction.response.send_message(f"You don't have enough points. You need {cost} but only have {balance}.", ephemeral=True)
        if await database.get_user_buff(interaction.guild.id, interaction.user.id, "xp_boost"):
            return await interaction.response.send_message("You already have an active XP boost!", ephemeral=True)
        await database.adjust_koth_points(interaction.guild.id, interaction.user.id, -cost)
        await database.add_user_buff(interaction.guild.id, interaction.user.id, "xp_boost", 3600)
        await interaction.response.send_message("🚀 **1-Hour 2x XP Boost** purchased and activated!", ephemeral=True)

    @discord.ui.button(label="Buy Priority Pass", style=discord.ButtonStyle.success, emoji="🎟️", row=1)
    async def buy_priority_pass(self, interaction: discord.Interaction, button: discord.ui.Button):
        # --- MODIFICATION: Fetch cost from DB ---
        cost = await database.get_setting(interaction.guild.id, 'priority_pass_cost') or 50
        balance = await database.get_koth_points(interaction.guild.id, interaction.user.id)
        if balance < cost:
            return await interaction.response.send_message(f"You don't have enough points. You need {cost} but only have {balance}.", ephemeral=True)
        await database.adjust_koth_points(interaction.guild.id, interaction.user.id, -cost)
        await database.add_to_inventory(interaction.guild.id, interaction.user.id, "priority_pass")
        await interaction.response.send_message("🎟️ **Submission Priority Pass** purchased! Use `/use pass` to redeem it.", ephemeral=True)

    @discord.ui.button(label="Buy Leaderboard Emoji", style=discord.ButtonStyle.secondary, emoji="✨", row=2)
    async def buy_leaderboard_emoji(self, interaction: discord.Interaction, button: discord.ui.Button):
        # --- MODIFICATION: Fetch cost from DB ---
        cost = await database.get_setting(interaction.guild.id, 'emoji_unlock_cost') or 100
        if await database.get_all_user_cosmetics(interaction.guild.id, [interaction.user.id]):
            return await interaction.response.send_message("You've already unlocked this! Use `/customize` to set your emoji.", ephemeral=True)
        balance = await database.get_koth_points(interaction.guild.id, interaction.user.id)
        if balance < cost:
            return await interaction.response.send_message(f"You don't have enough points. You need {cost} but only have {balance}.", ephemeral=True)
        await database.adjust_koth_points(interaction.guild.id, interaction.user.id, -cost)
        await database.unlock_cosmetic(interaction.guild.id, interaction.user.id, "leaderboard_emoji")
        await interaction.response.send_message("✨ **Leaderboard Emoji** unlocked! Use the `/customize` command to set it.", ephemeral=True)

class ShopCog(commands.Cog, name="Shop"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shop", description="View items you can purchase with your KOTH points.")
    async def shop(self, interaction: discord.Interaction):
        balance = await database.get_koth_points(interaction.guild.id, interaction.user.id)
        settings = await database.get_all_settings(interaction.guild.id)
        
        embed = discord.Embed(
            title="⚔️ KOTH Points Shop",
            description="Spend your hard-earned points on unique perks! Use `/customize` to manage your purchases.",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        embed.add_field(name=f"🎨 Custom Role - {settings.get('custom_role_cost', 100)} Points", value="A unique role with a custom name and color.", inline=False)
        embed.add_field(name=f"🚀 1-Hour 2x XP Boost - {settings.get('xp_boost_cost', 25)} Points", value="Double your XP gain for one hour.", inline=False)
        embed.add_field(name=f"🎟️ Submission Priority Pass - {settings.get('priority_pass_cost', 50)} Points", value="Move your track to the front of the queue.", inline=False)
        embed.add_field(name=f"✨ Leaderboard Emoji - {settings.get('emoji_unlock_cost', 100)} Points", value="Set a custom emoji next to your name on the leaderboards.", inline=False)
        embed.set_footer(text=f"You currently have {balance} points.")
        await interaction.response.send_message(embed=embed, view=ShopView(self.bot))

async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))