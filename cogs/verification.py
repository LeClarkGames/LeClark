import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import string
import logging
import secrets
from urllib.parse import urlencode
import os
import aiosmtplib

import database
import config
import utils

log = logging.getLogger(__name__)

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")

# --- Helper function for sending emails ---
async def send_verification_email(recipient_email: str, code: str):
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        log.error("Gmail credentials are not set in .env file.")
        return False
    
    message = f"""Subject: Your Discord Verification Code

    Hello,

    Your verification code is: {code}

    This code will expire in 10 minutes. Please enter it in the modal on Discord to complete your verification.
    """
    try:
        await aiosmtplib.send(
            message,
            sender=sender,
            recipients=[recipient_email],
            hostname="smtp.gmail.com",
            port=465,
            use_tls=True,
            username=sender,
            password=password,
        )
        return True
    except Exception as e:
        log.error(f"Failed to send verification email: {e}")
        return False

async def send_log_message(guild: discord.Guild, member: discord.Member, method: str):
    log_channel_id = await database.get_setting(guild.id, 'log_channel_id')
    if log_channel_id:
        log_channel = guild.get_channel(log_channel_id)
        if log_channel:
            embed = discord.Embed(
                title="✅ Member Verified",
                description=f"{member.mention} has been successfully verified via **{method}**.",
                color=discord.Color.green()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await log_channel.send(embed=embed)

class EmailInputModal(discord.ui.Modal, title="Gmail Verification"):
    email = discord.ui.TextInput(label="Please enter your Gmail address", style=discord.TextStyle.short, required=True, placeholder="example@gmail.com")

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = str(random.randint(100000, 999999))
        
        await database.store_gmail_code(interaction.guild.id, interaction.user.id, code)
        
        success = await send_verification_email(self.email.value, code)
        if success:
            await interaction.followup.send(
                "✅ An email with your verification code has been sent. Please check your inbox (and spam folder), then **send the 6-digit code to me in a direct message (DM)** to complete verification.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ Failed to send verification email. Please contact an admin.", ephemeral=True)

class CaptchaModal(discord.ui.Modal, title="Server Verification"):
    def __init__(self, captcha_text: str):
        super().__init__()
        self.captcha_text = captcha_text
        self.add_item(discord.ui.TextInput(label=f"Please type the following text:", placeholder=self.captcha_text, style=discord.TextStyle.short, required=True, max_length=len(captcha_text)))
        
    async def on_submit(self, interaction: discord.Interaction):
        member_role_id = await database.get_setting(interaction.guild.id, 'member_role_id')
        unverified_role_id = await database.get_setting(interaction.guild.id, 'unverified_role_id')
        member_role = interaction.guild.get_role(member_role_id)
        unverified_role = interaction.guild.get_role(unverified_role_id)
        if not member_role or not unverified_role:
            return await interaction.response.send_message("❌ Verification roles not configured correctly.", ephemeral=True)
        
        # Check if the bot has permission to manage these roles
        if interaction.guild.me.top_role <= member_role:
            return await interaction.response.send_message("❌ I do not have permission to assign the member role. Please make sure my role is higher than the member role.", ephemeral=True)
        if interaction.guild.me.top_role <= unverified_role:
            return await interaction.response.send_message("❌ I do not have permission to remove the unverified role. Please make sure my role is higher than the unverified role.", ephemeral=True)

        if self.children[0].value.lower() == self.captcha_text.lower():
            await interaction.user.add_roles(member_role, reason="Captcha success.")
            await interaction.user.remove_roles(unverified_role, reason="Captcha success.")
            await interaction.response.send_message("✅ Verification successful!", ephemeral=True)
            await send_log_message(interaction.guild, interaction.user, "Captcha")
        else:
            await interaction.response.send_message("❌ Incorrect captcha. Please try again.", ephemeral=True)

class FreeVerificationSelect(discord.ui.Select):
    def __init__(self, options: list):
        super().__init__(placeholder="Choose a verification method...", options=options, min_values=1, max_values=1)
        
    async def callback(self, interaction: discord.Interaction):
        mode = self.values[0]

        if mode == 'captcha':
            captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            await interaction.response.send_modal(CaptchaModal(captcha_text))
        
        elif mode in ['twitch', 'youtube']:
            await interaction.response.defer(ephemeral=True) # Defer only for follow-up responses
            state = secrets.token_urlsafe(16)
            
            await database.create_verification_link(
                state,
                interaction.guild.id,
                interaction.user.id,
                interaction.guild.name,
                interaction.client.user.display_avatar.url
            )

            base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")

            if mode == 'twitch':
                client_id = os.getenv("TWITCH_CLIENT_ID")
                redirect_uri = f"{base_url}/callback/twitch"
                params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "user:read:email", "state": state}
                auth_url = f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"
                button_label = "Verify with Twitch"
            else: # mode == 'youtube'
                client_id = os.getenv("YOUTUBE_CLIENT_ID")
                redirect_uri = f"{base_url}/callback/youtube"
                params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "https://www.googleapis.com/auth/userinfo.profile", "state": state}
                auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
                button_label = "Verify with YouTube/Google"

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label=button_label, url=auth_url))
            await interaction.followup.send(f"Please click the button below to verify with your {mode.capitalize()} account.", view=view, ephemeral=True)
        
        elif mode == 'gmail':
            await interaction.response.send_modal(EmailInputModal())
            
class VerificationButton(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot
    
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="persistent_verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        member_role_id = await database.get_setting(interaction.guild.id, 'member_role_id')
        if member_role_id and member_role_id in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message("You are already verified.", ephemeral=True)

        mode = await database.get_setting(interaction.guild.id, 'verification_mode') or 'free'
        
        if mode == 'free':
            enabled_modes_str = await database.get_setting(interaction.guild.id, 'free_verification_modes') or 'captcha,twitch,youtube,gmail'
            enabled_modes = enabled_modes_str.split(',')
            
            options = []
            for m in enabled_modes:
                if m == 'captcha': options.append(discord.SelectOption(label="Captcha Verification", value="captcha", emoji="⌨️"))
                elif m == 'twitch': options.append(discord.SelectOption(label="Twitch Verification", value="twitch", emoji="🟣"))
                elif m == 'youtube': options.append(discord.SelectOption(label="YouTube Verification", value="youtube", emoji="▶️"))
                elif m == 'gmail': options.append(discord.SelectOption(label="Gmail Verification", value="gmail", emoji="✉️"))
            
            if not options:
                return await interaction.response.send_message("❌ No verification methods are currently enabled. Please contact an admin.", ephemeral=True)
            
            view = discord.ui.View()
            view.add_item(FreeVerificationSelect(options))
            await interaction.response.send_message("Please select a verification method from the menu:", view=view, ephemeral=True)

        elif mode in ['captcha', 'twitch', 'youtube', 'gmail']:
            # This is the original forced verification logic
            if mode == 'captcha':
                captcha_text = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                await interaction.response.send_modal(CaptchaModal(captcha_text))
            
            elif mode in ['twitch', 'youtube']:
                state = secrets.token_urlsafe(16)
                await database.create_verification_link(state, interaction.guild.id, interaction.user.id, interaction.guild.name, self.bot.user.display_avatar.url)

                base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")

                if mode == 'twitch':
                    client_id = os.getenv("TWITCH_CLIENT_ID")
                    redirect_uri = f"{base_url}/callback/twitch"
                    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "user:read:email", "state": state}
                    auth_url = f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"
                    button_label = "Verify with Twitch"
                else:
                    client_id = os.getenv("YOUTUBE_CLIENT_ID")
                    redirect_uri = f"{base_url}/callback/youtube"
                    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": "https://www.googleapis.com/auth/userinfo.profile", "state": state}
                    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
                    button_label = "Verify with YouTube/Google"

                view = discord.ui.View()
                view.add_item(discord.ui.Button(label=button_label, url=auth_url))
                await interaction.response.send_message(f"Please click the button below to verify with your {mode.capitalize()} account.", view=view, ephemeral=True)

            elif mode == 'gmail':
                await interaction.response.send_modal(EmailInputModal())
            
            else:
                await interaction.response.send_message("❌ Unknown verification mode.", ephemeral=True)

# --- The Main Cog for Verification ---
class VerificationCog(commands.Cog, name="Verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_verifications.start()

    def cog_unload(self):
        self.check_verifications.cancel()

    @tasks.loop(seconds=15)
    async def check_verifications(self):
        completed_users = await database.get_completed_verifications()
        for state, guild_id, user_id, verified_account in completed_users:
            guild = self.bot.get_guild(guild_id)
            if not guild: continue
            
            member = guild.get_member(user_id)
            if not member: continue
            
            member_role_id = await database.get_setting(guild.id, 'member_role_id')
            unverified_role_id = await database.get_setting(guild.id, 'unverified_role_id')

            if member_role_id and unverified_role_id:
                try:
                    member_role = guild.get_role(member_role_id)
                    unverified_role = guild.get_role(unverified_role_id)
                    
                    if member_role and unverified_role:
                        # Check if the bot has permission to manage roles
                        if guild.me.top_role > member_role and guild.me.top_role > unverified_role:
                            await member.add_roles(member_role, reason="OAuth Verification Success")
                            await member.remove_roles(unverified_role, reason="OAuth Verification Success")
                            await database.delete_verification_link(state)
                            await send_log_message(guild, member, f"OAuth ({verified_account})")
                        else:
                            log.error(f"Bot lacks permissions to manage roles in guild {guild.id}.")
                            await database.delete_verification_link(state)
                except Exception as e:
                    log.error(f"Error granting roles via verification: {e}")

    @check_verifications.before_loop
    async def before_check_verifications(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is not None or message.author.bot or not message.content.isdigit() or len(message.content) != 6:
            return

        user = message.author
        code = message.content

        for guild in user.mutual_guilds:
            stored_code = await database.get_gmail_code(guild.id, user.id)
            if stored_code and stored_code == code:
                log.info(f"Found matching Gmail code for user {user.id} in guild {guild.id}")
                
                member_role_id = await database.get_setting(guild.id, 'member_role_id')
                unverified_role_id = await database.get_setting(guild.id, 'unverified_role_id')
                member_role = guild.get_role(member_role_id)
                unverified_role = guild.get_role(unverified_role_id)
                member = guild.get_member(user.id)

                if not member_role or not unverified_role or not member:
                    await message.channel.send("❌ Verification failed. Roles may not be configured correctly in the server.")
                    return

                try:
                    # Check if the bot has permission to manage roles
                    if guild.me.top_role > member_role and guild.me.top_role > unverified_role:
                        await member.add_roles(member_role, reason="Gmail DM verification success.")
                        await member.remove_roles(unverified_role, reason="Gmail DM verification success.")
                        await database.delete_gmail_code(guild.id, user.id)
                        await message.channel.send(f"✅ You have been successfully verified in **{guild.name}**!")
                        await send_log_message(guild, member, "Gmail")
                    else:
                        await message.channel.send(f"❌ Verification failed in **{guild.name}**. I don't have permission to manage your roles there.")
                        
                    return
                except discord.Forbidden:
                    await message.channel.send(f"❌ Verification failed in **{guild.name}**. I don't have permission to manage your roles there.")
                    return
        
        await message.channel.send("❌ That code is incorrect or has expired. Please start the verification process again in your server.")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot: return
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

async def setup(bot: commands.Bot):
    await bot.add_cog(VerificationCog(bot))