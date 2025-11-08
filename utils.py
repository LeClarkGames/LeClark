import discord
from discord import app_commands
import database

async def get_admin_roles(guild_id: int) -> list[int]:
    """Gets a list of admin role IDs for a guild."""
    roles_str = await database.get_setting(guild_id, 'admin_role_ids') or ""
    return [int(role_id) for role_id in roles_str.split(',') if role_id]

async def get_mod_roles(guild_id: int) -> list[int]:
    """Gets a list of moderator role IDs for a guild."""
    roles_str = await database.get_setting(guild_id, 'mod_role_ids') or ""
    return [int(role_id) for role_id in roles_str.split(',') if role_id]

async def has_admin_role(user: discord.Member) -> bool:
    """Checks if a user has an admin role or server admin permissions."""
    if user.guild_permissions.administrator:
        return True
    user_role_ids = {role.id for role in user.roles}
    admin_role_ids = await get_admin_roles(user.guild.id)
    return any(role_id in user_role_ids for role_id in admin_role_ids)

async def has_mod_role(user: discord.Member) -> bool:
    """Checks if a user has a moderator role (or is an admin)."""
    if await has_admin_role(user):
        return True
    user_role_ids = {role.id for role in user.roles}
    mod_role_ids = await get_mod_roles(user.guild.id)
    return any(role_id in user_role_ids for role_id in mod_role_ids)

async def get_log_mentions(guild_id: int) -> str:
    """Gets a string of role mentions for logging purposes."""
    admin_roles_str = await database.get_setting(guild_id, 'admin_role_ids') or ""
    mod_roles_str = await database.get_setting(guild_id, 'mod_role_ids') or ""
    admin_ids = {role_id for role_id in admin_roles_str.split(',') if role_id}
    mod_ids = {role_id for role_id in mod_roles_str.split(',') if role_id}
    all_role_ids = admin_ids.union(mod_ids)
    if not all_role_ids: return ""
    return " ".join([f"<@&{role_id}>" for role_id in all_role_ids])

def has_permission(level="mod"):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False

        if level == "admin":
            if not await has_admin_role(interaction.user):
                await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
                return False
        elif level == "mod":
            if not await has_mod_role(interaction.user):
                await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
                return False
        return True
    return app_commands.check(predicate)