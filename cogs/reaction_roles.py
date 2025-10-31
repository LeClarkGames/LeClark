import discord
from discord import app_commands
from discord.ext import commands
import logging
import database
import utils

log = logging.getLogger(__name__)

class ReactionRolesCog(commands.Cog, name="Reaction Roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create_reaction_role_message", description="Creates a new message for reaction roles.")
    @app_commands.guild_only()
    @utils.has_permission("admin")
    @app_commands.describe(channel="The channel to send the message in.", message_content="The text for the message")
    async def create_rr_message(self, interaction: discord.Interaction, channel: discord.TextChannel, message_content: str):
        await interaction.response.defer(ephemeral=True)
        try:
            message = await channel.send(message_content)
            await interaction.followup.send(f"✅ Message created with ID: `{message.id}`. Now use `/set_reaction_role`.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to send messages in that channel.", ephemeral=True)

    @app_commands.command(name="set_reaction_role", description="Adds a reaction-role mapping to a message.")
    @app_commands.guild_only()
    @utils.has_permission("admin")
    @app_commands.describe(message_id="The ID of the message to add the reaction role to.", emoji="The emoji for the reaction.", role="The role to assign.")
    async def set_reaction_role(self, interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        if role >= interaction.guild.me.top_role:
            return await interaction.followup.send(f"I cannot assign **{role.name}** as it's higher than my own role.", ephemeral=True)
        try:
            msg = None
            for channel in interaction.guild.text_channels:
                try:
                    msg = await channel.fetch_message(int(message_id))
                    break
                except (discord.NotFound, discord.Forbidden): continue
            if not msg: return await interaction.followup.send("Could not find a message with that ID.", ephemeral=True)
            await msg.add_reaction(emoji)
            await database.add_reaction_role(interaction.guild.id, msg.id, emoji, role.id)
            await interaction.followup.send(f"✅ Reaction role set for {emoji} to give {role.mention}.", ephemeral=True)
        except (ValueError, discord.HTTPException):
            await interaction.followup.send("Invalid message ID or emoji.", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or (payload.member and payload.member.bot): return
        role_id = await database.get_reaction_role(payload.message_id, str(payload.emoji))
        if role_id:
            guild = self.bot.get_guild(payload.guild_id)
            role = guild.get_role(role_id)
            if role and payload.member:
                try:
                    await payload.member.add_roles(role, reason="Reaction Role")
                except discord.Forbidden: pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id: return
        role_id = await database.get_reaction_role(payload.message_id, str(payload.emoji))
        if role_id:
            guild = self.bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role = guild.get_role(role_id)
            if role and member:
                try:
                    await member.remove_roles(role, reason="Reaction Role Removed")
                except discord.Forbidden: pass

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRolesCog(bot))