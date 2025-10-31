import discord
from discord import app_commands
from discord.ext import commands
import logging
import re

import database
import config
import utils

log = logging.getLogger(__name__)

# --- Reusable Modals ---

class CustomRoleModal(discord.ui.Modal, title="Edit Your Custom Role"):
    def __init__(self, current_name: str, current_color: str):
        super().__init__()
        self.role_name = discord.ui.TextInput(
            label="Role Name",
            placeholder="Enter a name for your role.",
            default=current_name,
            max_length=50,
            required=True
        )
        self.role_color = discord.ui.TextInput(
            label="Role Color (Hex Code)",
            placeholder="e.g., #ff00ff or ff00ff",
            default=current_color,
            max_length=7,
            required=True
        )
        self.add_item(self.role_name)
        self.add_item(self.role_color)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        hex_color_pattern = re.compile(r'^#?([A-Fa-f0-9]{6})$')
        match = hex_color_pattern.match(self.role_color.value)
        if not match:
            return await interaction.followup.send("❌ Invalid hex color code.", ephemeral=True)
        
        color = discord.Color(int(match.group(1), 16))
        
        role_id = await database.get_user_custom_role(interaction.guild.id, interaction.user.id)
        role = interaction.guild.get_role(role_id)
        if role:
            try:
                await role.edit(name=self.role_name.value, color=color, reason="User updated custom role.")
                await interaction.followup.send("✅ Your custom role has been updated.", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send("❌ I don't have permission to edit roles.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Could not find your custom role.", ephemeral=True)

class EmojiModal(discord.ui.Modal, title="Set Your Leaderboard Emoji"):
    def __init__(self, current_emoji: str):
        super().__init__()
        self.emoji_input = discord.ui.TextInput(
            label="Your Emoji",
            default=current_emoji,
            max_length=5,
            required=True
        )
        self.add_item(self.emoji_input)

    async def on_submit(self, interaction: discord.Interaction):
        emoji = self.emoji_input.value
        await database.set_user_cosmetic(interaction.guild.id, interaction.user.id, "leaderboard_emoji", emoji)
        await interaction.response.send_message(f"✅ Your leaderboard emoji has been set to: {emoji}", ephemeral=True)

# --- Main Customization View ---

class CustomizeView(discord.ui.View):
    def __init__(self, has_role: bool, has_emoji: bool, role: discord.Role, current_emoji: str):
        super().__init__(timeout=300)
        self.role = role
        self.current_emoji = current_emoji
        
        if not has_role and not has_emoji:
            self.clear_items() # Remove all buttons if user has nothing to customize
            return

        if not has_role:
            self.remove_item(self.edit_role_button)
            self.remove_item(self.delete_role_button)

        if not has_emoji:
            self.remove_item(self.set_emoji_button)

    @discord.ui.button(label="Edit Custom Role", style=discord.ButtonStyle.primary, emoji="🎨", row=0)
    async def edit_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomRoleModal(self.role.name, str(self.role.color)))

    @discord.ui.button(label="Set Leaderboard Emoji", style=discord.ButtonStyle.secondary, emoji="✨", row=0)
    async def set_emoji_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EmojiModal(self.current_emoji))
        
    @discord.ui.button(label="Delete Custom Role", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def delete_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ConfirmDeleteView()
        await interaction.response.send_message("Are you sure? Your points will **not** be refunded.", view=view, ephemeral=True)

class ConfirmDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes, Delete It", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        role_id = await database.get_user_custom_role(interaction.guild.id, interaction.user.id)
        role = interaction.guild.get_role(role_id)
        if role:
            try:
                await role.delete(reason=f"Custom role deleted by user.")
            except discord.Forbidden:
                return await interaction.response.edit_message(content="❌ I don't have permission.", view=None)
        
        await database.delete_user_custom_role(interaction.guild.id, interaction.user.id)
        await interaction.response.edit_message(content="✅ Your custom role has been deleted.", view=None)

    @discord.ui.button(label="No, Keep It", style=discord.ButtonStyle.secondary)
    async def cancel_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Deletion cancelled.", view=None)


class CustomizeCog(commands.Cog, name="Customize"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="customize", description="Manage your purchased cosmetic items.")
    async def customize(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        role_id = await database.get_user_custom_role(interaction.guild.id, interaction.user.id)
        cosmetics = await database.get_all_user_cosmetics(interaction.guild.id, [interaction.user.id])
        
        has_role = role_id is not None
        has_emoji = bool(cosmetics)
        
        role = interaction.guild.get_role(role_id) if has_role else None
        current_emoji = cosmetics.get(interaction.user.id, "")

        if not has_role and not has_emoji:
            return await interaction.followup.send("You don't own any customizable items yet! Visit the `/shop` to see what's available.", ephemeral=True)

        embed = discord.Embed(
            title="✨ Your Customizations",
            description="Here you can manage all the cosmetic items you've purchased from the shop.",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        if role:
            embed.add_field(name="🎨 Custom Role", value=f"**Name:** {role.name}\n**Color:** {str(role.color)}", inline=False)
        else:
            embed.add_field(name="🎨 Custom Role", value="*Not owned. Purchase from the `/shop`!*", inline=False)
            
        if has_emoji:
            embed.add_field(name="✨ Leaderboard Emoji", value=f"**Current:** {current_emoji if current_emoji else 'Not Set'}", inline=False)
        else:
            embed.add_field(name="✨ Leaderboard Emoji", value="*Not owned. Purchase from the `/shop`!*", inline=False)

        view = CustomizeView(has_role, has_emoji, role, current_emoji)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CustomizeCog(bot))