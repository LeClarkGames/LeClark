import discord
from discord.ext import commands
import logging
from typing import List

import database
import config
import utils

log = logging.getLogger(__name__)

class RoleToggleSelect(discord.ui.Select):
    """
    A select menu that shows a list of roles.
    It pre-selects roles the user already has.
    When the user confirms, it adds roles they selected and
    removes roles they deselected.
    """
    def __init__(self, options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Select roles to add or remove...",
            min_values=0,
            max_values=len(options),
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        member = interaction.user
        guild = interaction.guild
        
        added_roles = []
        removed_roles = []

        selected_role_ids = set(self.values)
        
        available_role_ids = {opt.value for opt in self.options}
        
        for role_id_str in available_role_ids:
            role_id = int(role_id_str)
            role = guild.get_role(role_id)
            
            if not role:
                log.warning(f"Role {role_id} not found in guild {guild.id}")
                continue
                
            if role >= guild.me.top_role:
                log.warning(f"Bot cannot manage role {role.name} in guild {guild.id}")
                continue
                
            has_role = role in member.roles
            wants_role = role_id_str in selected_role_ids

            try:
                if wants_role and not has_role:
                    await member.add_roles(role, reason="Role Giver Panel")
                    added_roles.append(role.name)
                elif not wants_role and has_role:
                    await member.remove_roles(role, reason="Role Giver Panel")
                    removed_roles.append(role.name)
            except discord.Forbidden:
                log.error(f"Failed to toggle role {role.name} for {member.name} in {guild.name}")
            except Exception as e:
                log.error(f"Error toggling role: {e}")

        if not added_roles and not removed_roles:
            message = "Your roles have not changed."
        else:
            message_parts = []
            if added_roles:
                message_parts.append(f"Added: **{', '.join(added_roles)}**")
            if removed_roles:
                message_parts.append(f"Removed: **{', '.join(removed_roles)}**")
            message = "\n".join(message_parts)
            
        await interaction.followup.send(message, ephemeral=True)

class RoleGiverView(discord.ui.View):
    """
    The persistent view with the single "Choose Your Roles" button.
    This view is attached to the panel message.
    """
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @discord.ui.button(label="Choose Your Roles", style=discord.ButtonStyle.secondary, custom_id="persistent_role_giver_button")
    async def role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        roles_str = await database.get_setting(interaction.guild.id, 'role_giver_role_ids') or ""
        role_ids = [r for r in roles_str.split(',') if r]
        
        if not role_ids:
            await interaction.followup.send("❌ No roles are configured for this panel. Please contact an admin.", ephemeral=True)
            return
            
        options = []
        member_role_ids = {str(r.id) for r in interaction.user.roles}
        
        for role_id_str in role_ids:
            role = interaction.guild.get_role(int(role_id_str))
            if role and role < interaction.guild.me.top_role:
                
                role_emoji = role.display_icon if isinstance(role.display_icon, str) else None

                options.append(
                    discord.SelectOption(
                        label=role.name,
                        value=str(role.id),
                        emoji=role_emoji,
                        default=(role_id_str in member_role_ids)
                    )
                )
        
        if not options:
            await interaction.followup.send("❌ Configured roles could not be found or I don't have permission to assign them. Please contact an admin.", ephemeral=True)
            return

        dropdown_view = discord.ui.View()
        dropdown_view.add_item(RoleToggleSelect(options))
        
        await interaction.followup.send(
            "Select the roles you'd like to have. Roles you already have are pre-selected. Deselecting a role will remove it.", 
            view=dropdown_view, 
            ephemeral=True
        )

class RoleGiverCog(commands.Cog, name="Role Giver"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def get_panel_message(self, guild: discord.Guild) -> discord.Message | None:
        """Gets the panel message from the database."""
        panel_id = await database.get_setting(guild.id, 'role_giver_message_id')
        channel_id = await database.get_setting(guild.id, 'role_giver_channel_id')
        if not panel_id or not channel_id: return None
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if channel: return await channel.fetch_message(panel_id)
        except (discord.NotFound, discord.Forbidden): return None

    async def post_panel(self, channel: discord.TextChannel):
        """Posts the role giver panel to the specified channel."""
        try:
            if old_panel := await self.get_panel_message(channel.guild):
                try: await old_panel.delete()
                except (discord.Forbidden, discord.NotFound): pass

            embed = discord.Embed(
                title="Role Assignment", 
                description="Click the button below to open the role selection menu. You can add or remove roles from yourself.", 
                color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
            )
            view = RoleGiverView(self.bot)

            panel_message = await channel.send(embed=embed, view=view)
            await database.update_setting(channel.guild.id, 'role_giver_message_id', panel_message.id)
            return True, f"Role Giver panel sent to {channel.mention}!"
        
        except discord.Forbidden:
            return False, f"Bot lacks permission to send messages in {channel.mention}."
        except Exception as e:
            log.error(f"Error in panel setup (role_giver): {e}")
            return False, "An internal error occurred."

async def setup(bot: commands.Bot):
    await bot.add_cog(RoleGiverCog(bot))