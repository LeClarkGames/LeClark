import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
import database
import config
import utils
import random
from datetime import datetime, timezone

log = logging.getLogger(__name__)

class GiveawayView(discord.ui.View):
    def __init__(self, bot: commands.Bot, giveaway_id: int, youtube_channel_url: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.giveaway_id = giveaway_id

        # Add buttons if the URLs are available
        if youtube_channel_url:
            self.add_item(discord.ui.Button(label="Follow YouTube", style=discord.ButtonStyle.link, url=youtube_channel_url, emoji="▶️"))
        
        # Add the entry button with a custom ID
        entry_button = discord.ui.Button(label="Enter Giveaway", style=discord.ButtonStyle.success, custom_id=f"giveaway_enter_{giveaway_id}", emoji="🎉")
        self.add_item(entry_button)


class GiveawayCog(commands.Cog, name="Giveaway"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.giveaway_end_checker.start()

    def cog_unload(self):
        self.giveaway_end_checker.cancel()

    @tasks.loop(minutes=1)
    async def giveaway_end_checker(self):
        """Periodically checks for giveaways that have ended."""
        active_giveaways = await database.get_all_active_giveaways()
        now = datetime.now(timezone.utc)

        for giveaway_data in active_giveaways:
            # The end_time from the web panel is in ISO format, but might not have timezone info.
            # We assume it's UTC for consistency.
            end_time = datetime.fromisoformat(giveaway_data['end_time']).replace(tzinfo=timezone.utc)
            
            if now >= end_time:
                log.info(f"Giveaway #{giveaway_data['id']} in guild {giveaway_data['guild_id']} has ended. Processing winner.")
                await self.process_giveaway_end(giveaway_data)

    async def process_giveaway_end(self, giveaway_data: dict):
        """Handles the logic for ending a giveaway and announcing the winner."""
        guild_id = giveaway_data['guild_id']
        giveaway_id = giveaway_data['id']
        channel_id = giveaway_data['channel_id']
        message_id = giveaway_data['message_id']

        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"Cannot process giveaway end: Guild {guild_id} not found.")
            await database.end_giveaway(guild_id, giveaway_id, winner_id=None)
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            log.error(f"Cannot process giveaway end: Channel {channel_id} not found.")
            await database.end_giveaway(guild_id, giveaway_id, winner_id=None)
            return

        entrants = await database.get_giveaway_entrants(guild_id, giveaway_id)
        winner_id = None
        
        if not entrants:
            await channel.send(f"The giveaway for **{giveaway_data['name']}** has ended, but no one entered! 😕")
        else:
            winner_id = random.choice(entrants)
            winner_member = guild.get_member(winner_id)
            winner_mention = f"<@{winner_id}>" if winner_member else f"User ID: {winner_id} (not found in server)"

            embed = discord.Embed(
                title="🎉 Giveaway Winner! 🎉",
                description=f"Congratulations to {winner_mention} for winning the **{giveaway_data['name']}** giveaway!",
                color=discord.Color.gold()
            )
            embed.set_footer(text=f"A total of {len(entrants)} people entered.")
            
            try:
                # Try to reply to the original giveaway message
                original_message = await channel.fetch_message(message_id)
                await original_message.reply(embed=embed)
            except (discord.NotFound, discord.Forbidden):
                # If message is deleted or we lack perms, just send to channel
                await channel.send(embed=embed)

        await database.end_giveaway(guild_id, giveaway_id, winner_id)

    @giveaway_end_checker.before_loop
    async def before_giveaway_end_checker(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component and interaction.data["custom_id"].startswith("giveaway_enter_"):
            giveaway_id = int(interaction.data["custom_id"].split("_")[-1])
            await self.handle_giveaway_entry(interaction, giveaway_id)

    async def handle_giveaway_entry(self, interaction: discord.Interaction, giveaway_id: int):
        """Handles a user's attempt to enter a giveaway."""
        await interaction.response.defer(ephemeral=True)
        
        giveaway = await database.get_giveaway(interaction.guild.id, giveaway_id)
        if not giveaway or not giveaway['is_active']:
            return await interaction.followup.send("This giveaway is no longer active.", ephemeral=True)
        
        # Requirement 1: Submission check (if required)
        if giveaway.get('submission_required', False):
            # The start_time in the database is a string, convert it
            start_time_dt = datetime.fromisoformat(giveaway['start_time'])
            has_submitted = await database.has_user_submitted_since(interaction.guild.id, interaction.user.id, start_time_dt.isoformat())
            if not has_submitted:
                submission_channel_id = await database.get_setting(interaction.guild.id, 'submission_channel_id')
                submission_channel = self.bot.get_channel(submission_channel_id) if submission_channel_id else None
                start_timestamp = int(start_time_dt.timestamp())
                
                error_message = (
                    f"You must submit a track to the submissions channel "
                    f"({submission_channel.mention if submission_channel else '#submissions'}) "
                    f"after the giveaway started to be eligible.\n\n"
                    f"**Giveaway started at:** <t:{start_timestamp}:F>"
                )
                return await interaction.followup.send(error_message, ephemeral=True)

        # Requirement 2: YouTube "subscription" check
        if giveaway.get('youtube_url'):
            is_verified = await database.has_verified_google_account(interaction.guild.id, interaction.user.id)
            if not is_verified:
                # Ideally, you'd link them to your verification page/command
                return await interaction.followup.send("You must have a verified Google (YouTube) account linked to the bot to enter this giveaway. Please verify your account first.", ephemeral=True)

        # If all checks pass, add the user to the giveaway
        success = await database.add_giveaway_entrant(interaction.guild.id, giveaway_id, interaction.user.id)
        if success:
            await interaction.followup.send("🎉 You have successfully entered the giveaway! Good luck!", ephemeral=True)
        else:
            await interaction.followup.send("You have already entered this giveaway.", ephemeral=True)

async def setup(bot: commands.Bot):
    # We need a new function in database.py to get all active giveaways
    database.get_all_active_giveaways = get_all_active_giveaways_for_cog
    await bot.add_cog(GiveawayCog(bot))

async def get_all_active_giveaways_for_cog():
    """Gets all active giveaways from the database for the cog's task loop."""
    conn = await database.get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT * FROM giveaways WHERE is_active = 1")
        rows = await cursor.fetchall()
        if not rows: return []
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in rows]