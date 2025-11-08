import discord
from discord import app_commands
from discord.ext import commands, tasks
import logging
import random
from collections import defaultdict
import time
import os

import database
import config 

log = logging.getLogger(__name__)

# Rank requirements. The 'xp' is the total XP needed to achieve that rank.
RANKS = {
    1: {"name": "Rank 1", "xp": 50},
    2: {"name": "Rank 2", "xp": 150},
    3: {"name": "Rank 3", "xp": 300},
    4: {"name": "Rank 4", "xp": 500},
    5: {"name": "Rank 5", "xp": 750},
    6: {"name": "Rank 6", "xp": 900},
    7: {"name": "Rank 7", "xp": 1000},
    8: {"name": "Rank 8", "xp": 1200},
    9: {"name": "Rank 9", "xp": 1500},
    10: {"name": "The Legend", "xp": 2000}, # Given a finite number for max rank
}

def get_rank_from_xp(xp):
    """Helper function to get only the rank number from XP."""
    current_rank_num = 0
    for rank_num, rank_data in RANKS.items():
        if xp >= rank_data['xp']:
            current_rank_num = rank_num
        else:
            break
    return current_rank_num

def get_rank_info(xp):
    """Helper function to determine a user's current rank and progress."""
    current_rank_num = 0
    xp_for_next_rank = RANKS[1]['xp']
    for rank_num, rank_data in RANKS.items():
        if xp < rank_data['xp']:
            xp_for_next_rank = rank_data['xp']
            break
        current_rank_num = rank_num
    current_rank_name = RANKS.get(current_rank_num, {}).get('name', "Unranked")
    if current_rank_num == 0:
        xp_for_current_rank_start = 0
    else:
        xp_for_current_rank_start = RANKS[current_rank_num]['xp']
        if current_rank_num == 10:
             xp_for_next_rank = RANKS[current_rank_num]['xp']
    return current_rank_name, xp_for_current_rank_start, xp_for_next_rank


class RankingCog(commands.Cog, name="Ranking"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.xp_cooldowns = defaultdict(int)
        self.cooldown_seconds = 60
        self.voice_xp_loop.start()

    async def cog_check(self, interaction: discord.Interaction) -> bool:
        is_enabled = await database.get_setting(interaction.guild.id, 'ranking_system_enabled')
        if not is_enabled:
            await interaction.response.send_message("The ranking system is disabled on this server.", ephemeral=True)
            return False
        return True

    def cog_unload(self):
        self.voice_xp_loop.cancel()

    async def _handle_xp_gain(self, guild: discord.Guild, member: discord.Member, xp_to_add: int):
        """A central function to handle adding XP and checking for rank rewards."""
        old_xp = await database.get_user_xp(guild.id, member.id)
        old_rank = get_rank_from_xp(old_xp)
        
        await database.update_user_xp(guild.id, member.id, xp_to_add)
        new_xp = old_xp + xp_to_add
        new_rank = get_rank_from_xp(new_xp)
        
        if new_rank > old_rank:
            log.info(f"User {member.id} in guild {guild.id} ranked up from {old_rank} to {new_rank}.")
            
            reward_role_id = await database.get_rank_reward(guild.id, new_rank)
            if reward_role_id:
                role = guild.get_role(reward_role_id)
                if role:
                    try:
                        await member.add_roles(role, reason=f"Reached Rank {new_rank}")
                        log.info(f"Awarded rank-up role {role.name} to {member.id}.")
                    except discord.Forbidden:
                        log.error(f"Failed to add rank-up role to {member.id}. Missing permissions.")
                    except discord.HTTPException as e:
                        log.error(f"An HTTP error occurred while adding rank-up role: {e}")

    @tasks.loop(minutes=5)
    async def voice_xp_loop(self):
        """Grants XP to active members in voice channels."""
        for guild in self.bot.guilds:
            if not await database.get_setting(guild.id, 'ranking_system_enabled'):
                continue
            for channel in guild.voice_channels:
                active_members = [m for m in channel.members if not m.bot and not m.voice.deaf and not m.voice.mute]
                if len(active_members) >= 2:
                    for member in active_members:
                        xp_to_add = random.randint(5, 10)
                        await self._handle_xp_gain(guild, member, xp_to_add)

    @voice_xp_loop.before_loop
    async def before_voice_xp_loop(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or (message.author.bot and message.author.id == self.bot.user.id):
            return
        
        if not await database.get_setting(message.guild.id, 'ranking_system_enabled'):
            return
            
        user_key = (message.guild.id, message.author.id)
        last_message_time = self.xp_cooldowns.get(user_key, 0)
        current_time = time.time()

        if current_time - last_message_time > self.cooldown_seconds:
            self.xp_cooldowns[user_key] = current_time
            xp_to_add = random.randint(15, 25)
            await self._handle_xp_gain(message.guild, message.author, xp_to_add)

    @app_commands.command(name="rank", description="Check your or another member's rank and XP.")
    @app_commands.describe(member="The member to check the rank of (optional).")
    async def rank(self, interaction: discord.Interaction, member: discord.Member = None):
        target_member = member or interaction.user
        user_xp, rank_pos = await database.get_user_rank(interaction.guild.id, target_member.id)

        if user_xp is None:
            await interaction.response.send_message(f"{target_member.display_name} is not yet ranked.", ephemeral=True)
            return

        rank_name, prev_xp, next_xp = get_rank_info(user_xp)
        progress_needed = next_xp - prev_xp
        progress_made = user_xp - prev_xp
        progress_percent = (progress_made / progress_needed) * 100 if progress_needed > 0 else 100
        
        bar_length = 10
        filled_blocks = int(bar_length * progress_percent / 100)
        empty_blocks = bar_length - filled_blocks
        progress_bar = '▓' * filled_blocks + '░' * empty_blocks
        
        embed = discord.Embed(title=f"Rank for {target_member.display_name}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        embed.set_thumbnail(url=target_member.display_avatar.url)

        embed.add_field(name="Server Rank", value=f"#{rank_pos}", inline=True)
        embed.add_field(name="Level", value=rank_name, inline=True)
        embed.add_field(name="Total XP", value=f"{user_xp}", inline=True)
        
        if rank_name != "The Legend":
             embed.add_field(name="Progress to Next Rank", value=f"`{progress_bar}`\n{user_xp} / {next_xp} XP", inline=False)
        else:
             embed.add_field(name="Progress", value="**Max Rank Reached!** 🌟", inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Shows the server's top 10 most active members.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        top_users = await database.get_leaderboard(interaction.guild.id, limit=10)

        if not top_users:
            await interaction.followup.send("There is no one on the leaderboard yet!")
            return
            
        base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
        link = f"{base_url}/leaderboard/{interaction.guild.id}"
        
        embed = discord.Embed(title=f"🏆 Leaderboard for {interaction.guild.name}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        description_lines = []
        for i, (user_id, xp) in enumerate(top_users):
            member = interaction.guild.get_member(user_id)
            rank_name, _, _ = get_rank_info(xp)
            rank_icon = "👑" if i == 0 else f"**{i+1}.**"
            if member:
                description_lines.append(f"{rank_icon} {member.mention} - `{xp}` XP ({rank_name})")
            else:
                description_lines.append(f"{rank_icon} *Unknown User ({user_id})* - `{xp}` XP ({rank_name})")
        embed.description = "\n".join(description_lines)
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="View Full Leaderboard", url=link, emoji="🌐"))
        
        await interaction.followup.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(RankingCog(bot))