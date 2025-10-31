import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Union

import database
import config 
import utils

log = logging.getLogger(__name__)

# --- View for claiming ownership ---
class ClaimOwnershipView(discord.ui.View):
    def __init__(self, channel: discord.VoiceChannel):
        super().__init__(timeout=120) # 2 minute timeout
        self.channel = channel
        self.message = None

    @discord.ui.button(label="Claim Ownership", style=discord.ButtonStyle.success, emoji="👑")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):  
        # --- Start of new/modified code ---
        # Disable the button to prevent multiple claims
        button.disabled = True
        button.label = "Claiming..."
        await interaction.response.edit_message(view=self)
        # --- End of new/modified code ---

        # Check if the user is in the voice channel
        if not interaction.user.voice or interaction.user.voice.channel != self.channel:
            # Re-enable the button if the user is not in the VC
            button.disabled = False
            button.label = "Claim Ownership"
            await interaction.edit_original_response(view=self)
            # Send an ephemeral message instead of editing the response
            return await interaction.followup.send("You must be in the voice channel to claim it.", ephemeral=True)

        # Update the owner in the database to the new user
        await database.update_temp_vc_owner(self.channel.id, interaction.user.id)

        # Give the new owner channel management permissions
        await self.channel.set_permissions(interaction.user, manage_channels=True, move_members=True)

        # Edit the original message to show the new owner
        await interaction.edit_original_response(content=f"👑 **{interaction.user.mention}** has claimed ownership of this channel!", view=None)
        log.info(f"User {interaction.user.id} claimed ownership of VC {self.channel.id}")
        self.stop()

    async def on_timeout(self):
        if self.message:
            try:
                await self.message.edit(content="*Ownership claim has expired.*", view=None)
            except discord.NotFound:
                pass # Message might have been deleted if channel was deleted


class TempVCCog(commands.Cog, name="Temp VCs"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the Temp VC system is enabled for this guild."""
        is_enabled = await database.get_setting(interaction.guild.id, 'temp_vc_system_enabled')
        if not is_enabled:
            await interaction.response.send_message("The temporary voice channel system is disabled on this server.", ephemeral=True)
            return False
        return True

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
        
        if not await database.get_setting(member.guild.id, 'temp_vc_system_enabled'):
            return

        hub_channel_id = await database.get_setting(member.guild.id, 'temp_vc_hub_id')
        category_id = await database.get_setting(member.guild.id, 'temp_vc_category_id')

        # --- CHANNEL CREATION LOGIC ---
        if after.channel and after.channel.id == hub_channel_id:
            category = member.guild.get_channel(category_id) if category_id else None
            if not category:
                log.warning(f"User {member} joined hub VC in guild {member.guild.id}, but no category is configured.")
                return

            overwrites = {
                member: discord.PermissionOverwrite(connect=True, manage_channels=True, move_members=True, view_channel=True)
            }

            try:
                # Set a bitrate that's reasonable, e.g., 64kbps
                new_channel = await member.guild.create_voice_channel(
                    name=f"{member.display_name}'s Room",
                    category=category,
                    overwrites=overwrites,
                    bitrate=64000,
                    reason=f"Temporary channel created by {member}"
                )
                await member.move_to(new_channel)
                await database.add_temp_vc(new_channel.id, member.id)
                log.info(f"Created temporary VC {new_channel.id} for member {member.id}.")

                guide_embed = discord.Embed(
                    title="🔒 Your Private Voice Channel",
                    description=f"Welcome, {member.mention}! You are the owner of this channel.\nUse these commands in any text channel to manage it:",
                    color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
                )
                guide_embed.add_field(name="`/vc lock` / `/vc unlock`", value="Lock or unlock the channel.", inline=True)
                guide_embed.add_field(name="`/vc rename <name>`", value="Change the channel name.", inline=True)
                guide_embed.add_field(name="`/vc limit <number>`", value="Set a user limit (0=inf).", inline=True)
                guide_embed.add_field(name="`/vc permit <user/role>`", value="Allow a user/role to join.", inline=False)
                guide_embed.add_field(name="`/vc deny <user/role>`", value="Block a user/role from joining.", inline=False)
                guide_embed.set_footer(text="This channel will be deleted when everyone leaves.")
                
                # Try sending the guide message, but don't fail if permissions are missing
                try:
                    await new_channel.send(embed=guide_embed)
                except discord.Forbidden:
                    log.warning(f"Could not send guide to new temp VC {new_channel.id}. Missing Send Messages permission.")

            except discord.Forbidden:
                log.error(f"Failed to create/move member to temp VC in guild {member.guild.id}. Missing permissions.")
            except Exception as e:
                log.error(f"An error occurred creating a temp VC: {e}")

        # --- CHANNEL DELETION & OWNERSHIP TRANSFER LOGIC ---
        if before.channel:
            owner_id = await database.get_temp_vc_owner(before.channel.id)
            # If the channel is not a temp VC, do nothing
            if not owner_id:
                return

            # --- FIXED LOGIC ---
            # If the person leaving is the owner AND there are still people left in the channel
            if member.id == owner_id and len(before.channel.members) > 0:
                # 1. Remove the old owner's permissions
                await before.channel.set_permissions(member, overwrite=None)
                # 2. Update the database to show the channel is now ownerless (owner_id = 0)
                await database.update_temp_vc_owner(before.channel.id, 0)
                # 3. Post the claim button
                view = ClaimOwnershipView(before.channel)
                message = await before.channel.send("The channel owner has left. Click the button to claim ownership.", view=view)
                view.message = message
                log.info(f"Owner {member.id} left VC {before.channel.id}. Channel is now ownerless.")
                return

            # If the channel is now empty, delete it
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Temporary channel empty.")
                    await database.remove_temp_vc(before.channel.id)
                    log.info(f"Deleted empty temporary VC {before.channel.id}.")
                except discord.NotFound:
                    # If the channel is already gone, just clean up the database
                    await database.remove_temp_vc(before.channel.id)
                except Exception as e:
                    log.error(f"An error occurred deleting a temp VC: {e}")

    vc_group = app_commands.Group(name="vc", description="Manage your temporary voice channel.")

    async def vc_owner_check(self, interaction: discord.Interaction):
        """A check to ensure the user is the owner of the temp VC they are in."""
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ You must be in your temporary voice channel to use this command.", ephemeral=True)
            return None
        
        channel = interaction.user.voice.channel
        owner_id = await database.get_temp_vc_owner(channel.id)
        
        if not owner_id:
            await interaction.response.send_message("❌ This is not a temporary voice channel.", ephemeral=True)
            return None
        
        if owner_id == 0:
            await interaction.response.send_message("This channel is currently ownerless. Use the 'Claim Ownership' button inside the channel to take control.", ephemeral=True)
            return None

        if interaction.user.id != owner_id:
            await interaction.response.send_message(f"❌ You are not the owner of this channel. Only <@{owner_id}> can manage it.", ephemeral=True)
            return None
        
        return channel

    @vc_group.command(name="lock", description="Lock your voice channel to prevent new users from joining.")
    async def lock(self, interaction: discord.Interaction):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        await channel.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.response.send_message("🔒 Your channel is now locked.", ephemeral=True)

    @vc_group.command(name="unlock", description="Unlock your voice channel to allow anyone to join.")
    async def unlock(self, interaction: discord.Interaction):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        # Using None resets the permission to the category/server default
        await channel.set_permissions(interaction.guild.default_role, overwrite=None)
        await interaction.response.send_message("🔓 Your channel is now unlocked.", ephemeral=True)

    @vc_group.command(name="permit", description="Allow a specific user or role to join your channel.")
    @app_commands.describe(target="The user or role to permit.")
    async def permit(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        await channel.set_permissions(target, connect=True)
        await interaction.response.send_message(f"✅ {target.mention} can now join your channel.", ephemeral=True)

    @vc_group.command(name="deny", description="Deny a specific user or role from joining your channel.")
    @app_commands.describe(target="The user or role to deny.")
    async def deny(self, interaction: discord.Interaction, target: Union[discord.Member, discord.Role]):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        await channel.set_permissions(target, connect=False, view_channel=False)
        if isinstance(target, discord.Member) and target in channel.members:
            await target.move_to(None, reason="Denied from VC by owner")
        await interaction.response.send_message(f"🚫 {target.mention} can no longer join your channel.", ephemeral=True)

    @vc_group.command(name="limit", description="Set a user limit for your channel (0 for unlimited).")
    @app_commands.describe(limit="The maximum number of users (0-99).")
    async def limit(self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        await channel.edit(user_limit=limit)
        await interaction.response.send_message(f"👥 User limit set to **{limit if limit > 0 else 'unlimited'}**.", ephemeral=True)

    @vc_group.command(name="rename", description="Change the name of your voice channel.")
    @app_commands.describe(name="The new name for your channel.")
    async def rename(self, interaction: discord.Interaction, name: str):
        channel = await self.vc_owner_check(interaction)
        if not channel: return
        
        # Prevent users from setting inappropriate names
        bad_words = await database.get_bad_words(interaction.guild.id)
        if any(word in name.lower() for word in bad_words):
            return await interaction.response.send_message("❌ That name contains a forbidden word.", ephemeral=True)
            
        await channel.edit(name=name)
        await interaction.response.send_message(f"📝 Your channel has been renamed to **{name}**.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TempVCCog(bot))
