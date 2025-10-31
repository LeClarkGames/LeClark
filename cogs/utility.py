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

# --- Forward declaration for type hinting ---
class EmbedBuilderView(discord.ui.View):
    pass

# --- Modals for Editing Embed Components ---
class EditEmbedTextModal(discord.ui.Modal):
    def __init__(self, view: EmbedBuilderView, component: str):
        super().__init__(title=f"Set Embed {component.capitalize()}")
        self.view = view
        self.component = component
        
        self.text_input = discord.ui.TextInput(
            label=f"Embed {component}",
            style=discord.TextStyle.paragraph if component == 'description' else discord.TextStyle.short,
            required=True,
            max_length=4000 if component == 'description' else 256
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        setattr(self.view, self.component, self.text_input.value)
        await self.view.update_preview(interaction)

class EditEmbedAuthorModal(discord.ui.Modal, title="Set Embed Author"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.author_name = discord.ui.TextInput(label="Author Name", required=True)
        self.author_icon_url = discord.ui.TextInput(label="Author Icon URL (Optional)", required=False)
        self.add_item(self.author_name)
        self.add_item(self.author_icon_url)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.author_name = self.author_name.value
        self.view.author_icon_url = self.author_icon_url.value
        await self.view.update_preview(interaction)

class EditEmbedImageModal(discord.ui.Modal, title="Set Embed Image/Thumbnail"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.image_url = discord.ui.TextInput(label="Image URL (Main Image)", required=False)
        self.thumbnail_url = discord.ui.TextInput(label="Thumbnail URL (Top Right)", required=False)
        self.add_item(self.image_url)
        self.add_item(self.thumbnail_url)

    async def on_submit(self, interaction: discord.Interaction):
        self.view.image_url = self.image_url.value
        self.view.thumbnail_url = self.thumbnail_url.value
        await self.view.update_preview(interaction)

class EditEmbedColorModal(discord.ui.Modal, title="Set Embed Color"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.color_hex = discord.ui.TextInput(label="Hex Color Code", placeholder="#RRGGBB format", required=True)
        self.add_item(self.color_hex)
        
    async def on_submit(self, interaction: discord.Interaction):
        color_val = is_valid_hex_color(self.color_hex.value)
        if color_val is not None:
            self.view.color = discord.Color(color_val)
            await self.view.update_preview(interaction)
        else:
            await interaction.response.send_message("Invalid Hex Code format.", ephemeral=True)


# --- Main View for the Embed Builder ---
class EmbedBuilderView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=600)
        self.bot = bot
        self.title: Optional[str] = "Your Title Here"
        self.description: Optional[str] = "Your description here."
        self.author_name: Optional[str] = None
        self.author_icon_url: Optional[str] = None
        self.footer: Optional[str] = None
        self.image_url: Optional[str] = None
        self.thumbnail_url: Optional[str] = None
        self.color: discord.Color = config.BOT_CONFIG["EMBED_COLORS"]["INFO"]

    async def build_embed(self) -> discord.Embed:
        """Constructs the embed from the view's current state."""
        embed = discord.Embed(title=self.title, description=self.description, color=self.color)
        if self.author_name:
            embed.set_author(name=self.author_name, icon_url=self.author_icon_url or None)
        if self.footer:
            embed.set_footer(text=self.footer)
        if self.image_url:
            embed.set_image(url=self.image_url)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        return embed
    
    async def update_preview(self, interaction: discord.Interaction):
        """Edits the original message to show the new embed preview."""
        embed = await self.build_embed()
        await interaction.response.edit_message(content="*Live Preview:*", embed=embed, view=self)

    @discord.ui.button(label="Title", style=discord.ButtonStyle.secondary, row=0)
    async def edit_title(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedTextModal(self, 'title'))

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, row=0)
    async def edit_description(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedTextModal(self, 'description'))

    @discord.ui.button(label="Author", style=discord.ButtonStyle.secondary, row=0)
    async def edit_author(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedAuthorModal(self))

    @discord.ui.button(label="Footer", style=discord.ButtonStyle.secondary, row=1)
    async def edit_footer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedTextModal(self, 'footer'))
        
    @discord.ui.button(label="Images", style=discord.ButtonStyle.secondary, row=1)
    async def edit_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedImageModal(self))

    @discord.ui.button(label="Color", style=discord.ButtonStyle.secondary, row=1)
    async def edit_color(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditEmbedColorModal(self))
    
    @discord.ui.button(label="Send Embed", style=discord.ButtonStyle.success, row=2)
    async def send_embed(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        view.add_item(ChannelSelectForSend(self))
        await interaction.response.send_message("Select a channel to send this embed to:", view=view, ephemeral=True)
        self.stop()

class ChannelSelectForSend(discord.ui.ChannelSelect):
    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(placeholder="Select a channel...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text])
        self.builder_view = builder_view

    async def callback(self, interaction: discord.Interaction):
        # This is the simplified 'AppCommandChannel'
        selected_channel_proxy = self.values[0]
        
        # Use the bot instance to fetch the full, usable channel object
        channel_to_send = self.builder_view.bot.get_channel(selected_channel_proxy.id)

        if not channel_to_send:
            await interaction.response.edit_message(content=f"❌ Error: Could not find the channel {selected_channel_proxy.mention}. It may have been deleted.", view=None)
            return

        embed = await self.builder_view.build_embed()
        try:
            # Now, this .send() call will work correctly
            await channel_to_send.send(embed=embed)
            
            await interaction.response.edit_message(content=f"✅ Embed successfully sent to {channel_to_send.mention}!", view=None)
            
            original_builder_message = await interaction.original_response()
            await original_builder_message.edit(content="*This embed has been sent and the builder is now inactive.*", view=None)
            
        except discord.Forbidden:
            await interaction.response.edit_message(content=f"❌ I don't have permission to send messages in {channel_to_send.mention}.", view=None)
        except Exception as e:
            log.error(f"Error in embed sending: {e}")
            await interaction.response.edit_message(content=f"An unexpected error occurred: {e}", view=None)


# --- Main Cog ---
class UtilityCog(commands.Cog, name="Utility"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="embed", description="Create a custom embed message.")
    @utils.has_permission("mod")
    async def embed(self, interaction: discord.Interaction):
        view = EmbedBuilderView(self.bot)
        initial_embed = await view.build_embed()
        await interaction.response.send_message("*Live Preview:*", embed=initial_embed, view=view, ephemeral=True)

    @app_commands.command(name="widget", description="Get the secure link page for your stream widgets.")
    @utils.has_permission("admin")
    async def widget(self, interaction: discord.Interaction):
        base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
        link = f"{base_url}/widget/{interaction.guild.id}"
        
        embed = discord.Embed(
            title="🔴 Your Stream Widget Link Page",
            description=f"Click the button below to get your unique, secure URLs for the stream widgets. **Do not share the final URLs with anyone.**",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Get My Links", url=link, emoji="🔗"))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

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