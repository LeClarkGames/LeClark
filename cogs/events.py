import discord
from discord.ext import commands
import logging
import database
import config 

log = logging.getLogger(__name__)

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _check_milestones(self, guild: discord.Guild):
        # Use a more descriptive database key to store the last count
        last_announced_milestone = await database.get_setting(guild.id, 'last_milestone_count') or 0

        increment = 50
        
        # Determine the next milestone goal
        if last_announced_milestone < 100:
            next_milestone = 100
        else:
            # Calculate the next multiple of 50
            next_milestone = ((last_announced_milestone // increment) + 1) * increment

        # Get the list of user IDs to exclude from the member count
        excluded_ids = set(config.BOT_CONFIG.get("MILESTONE_EXCLUDED_IDS", []))
        
        # Calculate the current member count, excluding bots and specified IDs
        eligible_member_count = sum(1 for member in guild.members if not member.bot and member.id not in excluded_ids)
        
        while eligible_member_count >= next_milestone:
            announcement_channel_id = await database.get_setting(guild.id, 'announcement_channel_id')
            if not announcement_channel_id:
                log.warning(f"Guild {guild.id} reached {next_milestone} members, but no announcement channel is set.")
                # Update the database even if we can't announce, to prevent spam
                await database.update_setting(guild.id, 'last_milestone_count', next_milestone)
                next_milestone += increment # Check for the next one
                continue

            announcement_channel = guild.get_channel(announcement_channel_id)
            if announcement_channel:
                log.info(f"Guild {guild.id} reached {next_milestone} member milestone. Announcing.")
                embed = discord.Embed(
                    title="🎉 Server Milestone Reached! 🎉",
                    description=f"**Congratulations!** Our community has just reached **{next_milestone}** members!\n\nThank you to everyone for being a part of our journey. Here's to many more milestones to come! 🚀",
                    color=config.BOT_CONFIG["EMBED_COLORS"]["SUCCESS"]
                )
                if guild.icon:
                    embed.set_thumbnail(url=guild.icon.url)
                
                try:
                    await announcement_channel.send(embed=embed)
                    # Update the database with the new milestone number we just announced
                    await database.update_setting(guild.id, 'last_milestone_count', next_milestone)
                except discord.Forbidden:
                    log.error(f"Failed to send milestone announcement in guild {guild.id}. Missing permissions.")
                    break # Stop if we can't send messages
            else:
                log.error(f"Could not find announcement channel {announcement_channel_id} in guild {guild.id}.")
                break # Stop if the channel doesn't exist

            # Increment to check for the next milestone in the same run
            next_milestone += increment

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        
        # Assign the unverified role upon joining
        unverified_role_id = await database.get_setting(member.guild.id, 'unverified_role_id')
        if unverified_role_id:
            unverified_role = member.guild.get_role(unverified_role_id)
            if unverified_role:
                try:
                    await member.add_roles(unverified_role, reason="New member join")
                    log.info(f"Assigned unverified role to {member} in guild {member.guild.id}.")
                except discord.Forbidden:
                    log.error(f"Failed to assign unverified role to {member} in guild {member.guild.id}. Missing permissions.")
            else:
                log.error(f"Could not find the configured unverified role ({unverified_role_id}) in guild {member.guild.id}.")

        # Check for new milestones after a member joins
        await self._check_milestones(member.guild)

async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))
