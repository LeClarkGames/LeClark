import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Optional

import database
import config
import utils

# --- Reusable Dropdown Components ---
class ChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, setting_name: str, placeholder: str, parent_view: discord.ui.View, channel_types: List[discord.ChannelType]):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types)
        self.setting_name = setting_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        channel = self.values[0]
        await database.update_setting(interaction.guild.id, self.setting_name, channel.id)
        await interaction.response.send_message(f"✅ {self.placeholder.replace('Select', 'Set')} to {channel.mention}", ephemeral=True)

class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, setting_name: str, placeholder: str, parent_view: discord.ui.View):
        super().__init__(placeholder=placeholder, min_values=1, max_values=1)
        self.setting_name = setting_name
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await database.update_setting(interaction.guild.id, self.setting_name, role.id)
        await interaction.response.send_message(f"✅ {self.placeholder.replace('Select', 'Set')} to {role.mention}", ephemeral=True)

class VerificationModeSelect(discord.ui.Select):
    def __init__(self, parent_view: discord.ui.View):
        options = [
            discord.SelectOption(label="Captcha Verification", value="captcha", emoji="⌨️"),
            discord.SelectOption(label="Google Verification", value="google"),
            discord.SelectOption(label="Mail Verification", value="mail", emoji="✉️"),
        ]
        super().__init__(placeholder="Select the one and only forced verification method...", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view
    
    async def callback(self, interaction: discord.Interaction):
        selected_mode = self.values[0]
        await database.update_setting(interaction.guild.id, "verification_mode", selected_mode)
        await interaction.response.send_message(f"✅ Forced verification method set to **{selected_mode.capitalize()}**.", ephemeral=True)

class RoleManagementSelect(discord.ui.RoleSelect):
    def __init__(self, action: str, role_type: str, parent_view: discord.ui.View):
        super().__init__(placeholder=f"Select a role to {action} as a Bot {role_type.capitalize()}")
        self.action = action
        self.role_type = role_type
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        setting_name = f"{self.role_type}_role_ids"
        roles_str = await database.get_setting(interaction.guild.id, setting_name) or ""
        role_ids = [r for r in roles_str.split(',') if r]

        if self.action == "add":
            if str(role.id) not in role_ids:
                role_ids.append(str(role.id))
                await interaction.response.send_message(f"✅ {role.mention} added as a bot {self.role_type}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {role.mention} is already a bot {self.role_type}.", ephemeral=True)
        
        elif self.action == "remove":
            if str(role.id) in role_ids:
                role_ids.remove(str(role.id))
                await interaction.response.send_message(f"✅ {role.mention} removed as a bot {self.role_type}.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ {role.mention} is not a bot {self.role_type}.", ephemeral=True)
        
        await database.update_setting(interaction.guild.id, setting_name, ",".join(role_ids))
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

# --- Base View & Back Button ---
class BaseSettingsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, parent_view: discord.ui.View = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.parent_view = parent_view
        if parent_view:
            self.add_item(self.BackButton())

    async def refresh_and_show(self, interaction: discord.Interaction, edit_original: bool = False):
        target_view = self.parent_view if edit_original else self
        
        embed = discord.Embed(title="Settings", description="An error occurred while refreshing the panel.")
        
        if isinstance(target_view, SettingsMainView):
             embed = await target_view.get_settings_embed(interaction.guild)
        elif isinstance(target_view, ModuleSettingsView):
             embed = await target_view.get_modules_embed(interaction.guild)
        elif isinstance(target_view, RankRewardsSettingsView):
             embed = await target_view.get_rewards_embed(interaction.guild)
        elif isinstance(target_view, WarningSettingsView):
             embed = await target_view.get_warnings_embed(interaction.guild)

        try:
            if interaction.response.is_done():
                message = await interaction.original_response()
                await message.edit(embed=embed, view=target_view)
            else:
                await interaction.response.edit_message(embed=embed, view=target_view)
        except (discord.NotFound, discord.InteractionResponded):
            try:
                await interaction.followup.send(content="The previous panel expired. Here is an updated one:", embed=embed, view=target_view, ephemeral=True)
            except discord.InteractionResponded:
                pass

    class BackButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Back", style=discord.ButtonStyle.grey, emoji="⬅️", row=4)
        
        async def callback(self, interaction: discord.Interaction):
            embed = await self.view.parent_view.get_settings_embed(interaction.guild)
            await interaction.response.edit_message(content=None, embed=embed, view=self.view.parent_view)

class SettingsMainView(BaseSettingsView):
    @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, emoji="📺", row=0)
    async def channel_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = [
            ChannelSelect("log_channel_id", "Set Log Channel", self, [discord.ChannelType.text]),
            ChannelSelect("announcement_channel_id", "Set Announcement Channel", self, [discord.ChannelType.text])
        ]
        view = GenericSettingsView(self.bot, self, "Channel Settings", items)
        embed = await view.get_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Temp VCs", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def temp_vc_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = [
            ChannelSelect("temp_vc_hub_id", "Set 'Join to Create' Hub Channel", self, [discord.ChannelType.voice]),
            ChannelSelect("temp_vc_category_id", "Set Category for New VCs", self, [discord.ChannelType.category])
        ]
        view = GenericSettingsView(self.bot, self, "Temp VC Settings", items)
        embed = await view.get_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)


class ChannelSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("log_channel_id", "Set Log Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("announcement_channel_id", "Set Announcement Channel", self, [discord.ChannelType.text]))

class RoleManagementView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)

    @discord.ui.button(label="Add Admin Role", style=discord.ButtonStyle.success)
    async def add_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("add", "admin", self.parent_view))
        await interaction.response.send_message("Select a role to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Remove Admin Role", style=discord.ButtonStyle.danger)
    async def remove_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("remove", "admin", self.parent_view))
        await interaction.response.send_message("Select a role to remove:", view=view, ephemeral=True)
    
    @discord.ui.button(label="Add Mod Role", style=discord.ButtonStyle.success, row=1)
    async def add_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("add", "mod", self.parent_view))
        await interaction.response.send_message("Select a role to add:", view=view, ephemeral=True)

    @discord.ui.button(label="Remove Mod Role", style=discord.ButtonStyle.danger, row=1)
    async def remove_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleManagementSelect("remove", "mod", self.parent_view))
        await interaction.response.send_message("Select a role to remove:", view=view, ephemeral=True)

class VerificationSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        
    async def get_verification_embed(self, guild: discord.Guild):
        settings = await database.get_all_settings(guild.id)
        mode = settings.get('verification_mode', 'free')
        enabled_methods = settings.get('free_verification_modes', 'captcha,google,mail').split(',')
        
        embed = discord.Embed(title="✅ Verification Settings", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        embed.add_field(name="Verification Type", value=f"`{'Free Verification' if mode == 'free' else 'Forced Verification'}`", inline=False)
        if mode == 'free':
            method_list = [f"**{m.capitalize()}**" for m in enabled_methods]
            embed.add_field(name="Available Methods", value=", ".join(method_list) or "None", inline=False)
        else:
            embed.add_field(name="Forced Method", value=f"`{mode.capitalize()}`", inline=False)
        embed.add_field(name="Channel", value=f"<#{settings.get('verification_channel_id', 'Not Set')}>", inline=False)
        embed.add_field(name="Unverified Role", value=f"<@&{settings.get('unverified_role_id', 'Not Set')}>", inline=True)
        embed.add_field(name="Member Role", value=f"<@&{settings.get('member_role_id', 'Not Set')}>", inline=True)
        return embed

    @discord.ui.button(label="Set Verification Channel", style=discord.ButtonStyle.secondary, row=0)
    async def set_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(ChannelSelect("verification_channel_id", "Set Verification Channel", self.parent_view, [discord.ChannelType.text]))
        await interaction.response.send_message("Select the channel for verification:", view=view, ephemeral=True)
    
    @discord.ui.button(label="Set Unverified Role", style=discord.ButtonStyle.secondary, row=0)
    async def set_unverified_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleSelect("unverified_role_id", "Set Unverified Role", self.parent_view))
        await interaction.response.send_message("Select the role for unverified members:", view=view, ephemeral=True)

    @discord.ui.button(label="Set Member Role", style=discord.ButtonStyle.secondary, row=0)
    async def set_member_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(RoleSelect("member_role_id", "Set Member Role", self.parent_view))
        await interaction.response.send_message("Select the role for verified members:", view=view, ephemeral=True)

    @discord.ui.button(label="Free Verification", style=discord.ButtonStyle.success, row=1)
    async def set_free(self, interaction: discord.Interaction, button: discord.ui.Button):
        await database.update_setting(interaction.guild.id, 'verification_mode', 'free')
        await interaction.response.send_message("✅ Verification set to **Free Verification**. Members can now choose their verification method.", ephemeral=True)
        await self.refresh_and_show(interaction, edit_original=True)
        
    @discord.ui.button(label="Forced Verification", style=discord.ButtonStyle.secondary, row=1)
    async def set_forced(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(VerificationModeSelect(self.parent_view))
        await interaction.response.send_message("Select a single verification method to force:", view=view, ephemeral=True)

    @discord.ui.button(label="Toggle Method", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_method(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View(); view.add_item(FreeVerificationToggleSelect(self))
        await interaction.response.send_message("Select a method to toggle:", view=view, ephemeral=True)

class FreeVerificationToggleSelect(discord.ui.Select):
    def __init__(self, parent_view: "VerificationSettingsView"):
        options = [
            discord.SelectOption(label="Google Verification", value="google"),
            discord.SelectOption(label="Mail Verification", value="mail", emoji="✉️"),
            discord.SelectOption(label="Captcha Verification", value="captcha", emoji="⌨️"),
        ]
        super().__init__(placeholder="Select a method to toggle...", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view
        
    async def callback(self, interaction: discord.Interaction):
        mode = self.values[0]
        modes_str = await database.get_setting(interaction.guild.id, 'free_verification_modes') or "captcha,google,mail"
        modes = modes_str.split(',')
        
        if mode in modes:
            modes.remove(mode)
            await interaction.response.send_message(f"✅ **{mode.capitalize()}** has been disabled.", ephemeral=True)
        else:
            modes.append(mode)
            await interaction.response.send_message(f"✅ **{mode.capitalize()}** has been enabled.", ephemeral=True)
        
        await database.update_setting(interaction.guild.id, 'free_verification_modes', ",".join(modes))
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

class TempVCSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("temp_vc_hub_id", "Set 'Join to Create' Hub Channel", self, [discord.ChannelType.voice]))
        self.add_item(ChannelSelect("temp_vc_category_id", "Set Category for New VCs", self, [discord.ChannelType.category]))

class SubmissionsSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        self.add_item(ChannelSelect("submission_channel_id", "Set Regular Submission Channel", self, [discord.ChannelType.text]))
        self.add_item(ChannelSelect("review_channel_id", "Set Review Channel", self, [discord.ChannelType.text]))

class ModuleSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)

    async def get_modules_embed(self, guild: discord.Guild):
        settings_data = await database.get_all_settings(guild.id)
        def get_status(key): return "✅ Enabled" if settings_data.get(key, 1) else "❌ Disabled"
        embed = discord.Embed(title="⚙️ Bot Modules", description="Enable or disable major bot features for this server.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        embed.add_field(name="🎵 Submissions System", value=get_status('submissions_system_enabled'), inline=False)
        embed.add_field(name="🏆 Ranking System", value=get_status('ranking_system_enabled'), inline=False)
        embed.add_field(name="🔊 Temporary VCs", value=get_status('temp_vc_system_enabled'), inline=False)
        return embed

    async def toggle_module(self, interaction: discord.Interaction, module_name: str):
        await interaction.response.defer()
        current_status = await database.get_setting(interaction.guild.id, module_name)
        if current_status is None: current_status = 1
        new_status = 0 if current_status else 1
        await database.update_setting(interaction.guild.id, module_name, new_status)
        await self.refresh_and_show(interaction, edit_original=True)

    @discord.ui.button(label="Toggle Submissions", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_submissions(self, interaction: discord.Interaction, button: discord.ui.Button): await self.toggle_module(interaction, "submissions_system_enabled")
    @discord.ui.button(label="Toggle Ranking", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_ranking(self, interaction: discord.Interaction, button: discord.ui.Button): await self.toggle_module(interaction, "ranking_system_enabled")
    @discord.ui.button(label="Toggle Temp VCs", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_temp_vcs(self, interaction: discord.Interaction, button: discord.ui.Button): await self.toggle_module(interaction, "temp_vc_system_enabled")

class RankRewardsSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        self.message: Optional[discord.Message] = None

    async def get_rewards_embed(self, guild: discord.Guild):
        embed = discord.Embed(title="🏆 Rank Reward Settings", description="Configure roles to be automatically awarded when a member reaches a new rank.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        rewards = await database.get_all_rank_rewards(guild.id)
        if not rewards:
            embed.description += "\n\n*No rank rewards are currently configured.*"
        else:
            sorted_rewards = sorted(rewards, key=lambda r: r[0])
            reward_list = [f"**Rank {level}** -> <@&{role_id}>" for level, role_id in sorted_rewards]
            embed.add_field(name="Current Rewards", value="\n".join(reward_list), inline=False)
        return embed

    @discord.ui.button(label="Set Reward", style=discord.ButtonStyle.success)
    async def set_reward(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.send_modal(RankLevelModal(self))
    @discord.ui.button(label="Remove Reward", style=discord.ButtonStyle.danger)
    async def remove_reward(self, interaction: discord.Interaction, button: discord.ui.Button):
        options, rewards = [], await database.get_all_rank_rewards(interaction.guild.id)
        if not rewards: return await interaction.response.send_message("There are no rewards to remove.", ephemeral=True)
        for level, role_id in rewards:
            role = interaction.guild.get_role(role_id)
            options.append(discord.SelectOption(label=f"Rank {level} -> {role.name if role else 'Unknown Role'}", value=str(level)))
        view = discord.ui.View(); view.add_item(RemoveRewardSelect(options, self))
        await interaction.response.send_message("Select a reward to remove:", view=view, ephemeral=True)

class RankLevelModal(discord.ui.Modal, title="Set Rank Reward"):
    def __init__(self, parent_view: RankRewardsSettingsView):
        super().__init__()
        self.parent_view = parent_view
        self.level_input = discord.ui.TextInput(label="Rank Level (1-10)", placeholder="Enter the rank number (e.g., 5).", min_length=1, max_length=2, required=True)
        self.add_item(self.level_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            level = int(self.level_input.value); assert 1 <= level <= 10
        except (ValueError, AssertionError):
            return await interaction.response.send_message("Invalid input. Please enter a number between 1 and 10.", ephemeral=True)
        view = discord.ui.View(); view.add_item(RewardRoleSelect(level, self.parent_view))
        await interaction.response.send_message(f"Now, select the role to award for **Rank {level}**:", view=view, ephemeral=True)

class RewardRoleSelect(discord.ui.RoleSelect):
    def __init__(self, rank_level: int, parent_view: RankRewardsSettingsView):
        super().__init__(placeholder="Select the reward role...")
        self.rank_level, self.parent_view = rank_level, parent_view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        if role.is_bot_managed() or role.is_premium_subscriber() or role.is_integration(): return await interaction.response.send_message(f"❌ I cannot assign the role `{role.name}` as it is managed by Discord or an integration.", ephemeral=True)
        if role >= interaction.guild.me.top_role: return await interaction.response.send_message(f"❌ I cannot assign the role `{role.name}` because it is higher than my own role in the server's hierarchy.", ephemeral=True)
        await database.set_rank_reward(interaction.guild.id, self.rank_level, role.id)
        await interaction.response.send_message(f"✅ Reward for **Rank {self.rank_level}** set to {role.mention}.", ephemeral=True)
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

class RemoveRewardSelect(discord.ui.Select):
    def __init__(self, options: list, parent_view: RankRewardsSettingsView):
        super().__init__(placeholder="Select a reward to remove...", options=options)
        self.parent_view = parent_view
        
    async def callback(self, interaction: discord.Interaction):
        level_to_remove = int(self.values[0])
        await database.remove_rank_reward(interaction.guild.id, level_to_remove)
        await interaction.response.send_message(f"✅ Reward for **Rank {level_to_remove}** has been removed.", ephemeral=True)
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

class WarningSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView):
        super().__init__(bot, parent_view)
        self.message: Optional[discord.Message] = None
        self.add_item(WarningActionSelect(self))

    async def get_warnings_embed(self, guild: discord.Guild):
        settings = await database.get_all_settings(guild.id)
        limit = settings.get('warning_limit', 3)
        action = settings.get('warning_action', 'mute').capitalize()
        duration = settings.get('warning_action_duration', 60)
        
        embed = discord.Embed(title="⚖️ Warning System Settings", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        desc = f"Configure the automatic punishment for members who receive too many warnings.\n\n"
        desc += f"**Current Limit:** `{limit}` warnings.\n"
        desc += f"**Action Taken:** `{action}`"
        if action == 'Mute':
            desc += f" for `{duration}` minutes."
        
        embed.description = desc
        return embed

    @discord.ui.button(label="Set Warning Limit", style=discord.ButtonStyle.secondary)
    async def set_warning_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WarningLimitModal(self))

class WarningLimitModal(discord.ui.Modal, title="Set Warning Limit"):
    def __init__(self, parent_view: WarningSettingsView):
        super().__init__()
        self.parent_view = parent_view
        self.limit_input = discord.ui.TextInput(label="Number of warnings before action", placeholder="e.g., 3", min_length=1, max_length=2)
        self.add_item(self.limit_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.limit_input.value); assert 1 < limit <= 100
        except (ValueError, AssertionError):
            return await interaction.response.send_message("Please enter a valid number between 2 and 100.", ephemeral=True)
        
        await database.update_setting(interaction.guild.id, 'warning_limit', limit)
        await interaction.response.send_message(f"✅ Warning limit set to **{limit}**.", ephemeral=True)
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

class WarningActionSelect(discord.ui.Select):
    def __init__(self, parent_view: WarningSettingsView):
        options = [
            discord.SelectOption(label="Mute", value="mute", emoji="🔇"),
            discord.SelectOption(label="Kick", value="kick", emoji="👢"),
            discord.SelectOption(label="Ban", value="ban", emoji="🔨")
        ]
        super().__init__(placeholder="Select the action to take at the warning limit...", options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        await database.update_setting(interaction.guild.id, 'warning_action', action)

        if action == 'mute':
            await interaction.response.send_modal(MuteDurationModal(self.parent_view))
        else:
            await interaction.response.send_message(f"✅ Automatic action set to **{action.capitalize()}**.", ephemeral=True)
            await self.parent_view.refresh_and_show(interaction, edit_original=True)

class MuteDurationModal(discord.ui.Modal, title="Set Mute Duration"):
    def __init__(self, parent_view: WarningSettingsView):
        super().__init__()
        self.parent_view = parent_view
        self.duration_input = discord.ui.TextInput(label="Mute duration in minutes", placeholder="e.g., 60")
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration = int(self.duration_input.value); assert duration > 0
        except (ValueError, AssertionError):
            return await interaction.response.send_message("Please enter a valid number of minutes.", ephemeral=True)
        
        await database.update_setting(interaction.guild.id, 'warning_action_duration', duration)
        await interaction.response.send_message(f"✅ Automatic action set to **Mute** for **{duration}** minutes.", ephemeral=True)
        await self.parent_view.refresh_and_show(interaction, edit_original=True)

class SettingsMainView(BaseSettingsView):
    def __init__(self, bot: commands.Bot):
        super().__init__(bot)

    async def get_settings_embed(self, guild: discord.Guild):
        settings_data = await database.get_all_settings(guild.id)
        embed = discord.Embed(title=f"Settings for {guild.name}", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
        def f_ch(k): return f"<#{settings_data.get(k)}>" if settings_data.get(k) else "Not Set"
        def f_rl(k): return f"<@&{settings_data.get(k)}>" if settings_data.get(k) else "Not Set"
        def f_rls(k): return ", ".join([f"<@&{r}>" for r in (settings_data.get(k) or "").split(',') if r]) or "Not Set"
        embed.add_field(name="General Channels", value=f"**Log:** {f_ch('log_channel_id')}\n**Announce:** {f_ch('announcement_channel_id')}", inline=False)
        embed.add_field(name="Role Permissions", value=f"**Admins:** {f_rls('admin_role_ids')}\n**Mods:** {f_rls('mod_role_ids')}", inline=False)
        
        # New Verification section summary
        verification_mode = settings_data.get('verification_mode', 'free')
        if verification_mode == 'free':
            modes = settings_data.get('free_verification_modes', 'captcha,google,mail').split(',')
            mode_text = "Free"
            methods = ", ".join([m.capitalize() for m in modes])
            embed.add_field(name="Verification", value=f"**Type:** `{mode_text}`\n**Methods:** `{methods}`\n**Channel:** {f_ch('verification_channel_id')}", inline=False)
        else:
            mode_text = verification_mode.capitalize()
            embed.add_field(name="Verification", value=f"**Type:** `{mode_text}`\n**Channel:** {f_ch('verification_channel_id')}\n**Roles:** {f_rl('unverified_role_id')} -> {f_rl('member_role_id')}", inline=False)
            
        embed.add_field(name="Temporary VCs", value=f"**Hub:** {f_ch('temp_vc_hub_id')}\n**Category:** {f_ch('temp_vc_category_id')}", inline=False)
        embed.add_field(name="Submissions", value=f"**Regular:** {f_ch('submission_channel_id')} -> {f_ch('review_channel_id')}", inline=False)
        return embed

    @discord.ui.button(label="Channels", style=discord.ButtonStyle.secondary, emoji="📺", row=0)
    async def channel_settings(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.edit_message(content="Configure general bot channels.", view=ChannelSettingsView(self.bot, self))
    @discord.ui.button(label="Roles", style=discord.ButtonStyle.secondary, emoji="🛡️", row=0)
    async def role_settings(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.edit_message(content="Manage Bot Admin/Moderator roles.", view=RoleManagementView(self.bot, self))
    @discord.ui.button(label="Verification", style=discord.ButtonStyle.secondary, emoji="✅", row=1)
    async def verification_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = VerificationSettingsView(self.bot, self)
        embed = await view.get_verification_embed(interaction.guild)
        await interaction.response.edit_message(content="Configure the member verification system.", embed=embed, view=view)

    @discord.ui.button(label="Temp VCs", style=discord.ButtonStyle.secondary, emoji="🔊", row=1)
    async def temp_vc_settings(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.edit_message(content="Configure the temporary voice channel system.", view=TempVCSettingsView(self.bot, self))
    @discord.ui.button(label="Submissions", style=discord.ButtonStyle.secondary, emoji="🎵", row=1)
    async def submissions_settings(self, interaction: discord.Interaction, button: discord.ui.Button): await interaction.response.edit_message(content="Configure the music submission systems.", view=SubmissionsSettingsView(self.bot, self))
    
    @discord.ui.button(label="Warnings", style=discord.ButtonStyle.secondary, emoji="⚖️", row=2)
    async def warning_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = WarningSettingsView(self.bot, self)
        embed = await view.get_warnings_embed(interaction.guild)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @discord.ui.button(label="Modules", style=discord.ButtonStyle.primary, emoji="⚙️", row=2)
    async def module_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ModuleSettingsView(self.bot, self)
        embed = await view.get_modules_embed(interaction.guild)
        await interaction.response.edit_message(content="Enable or disable major bot features.", embed=embed, view=view)

    @discord.ui.button(label="Rank Rewards", style=discord.ButtonStyle.secondary, emoji="🏆", row=2)
    async def rank_rewards_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RankRewardsSettingsView(self.bot, self)
        embed = await view.get_rewards_embed(interaction.guild)
        await interaction.response.edit_message(content="Configure automatic role rewards for the ranking system.", embed=embed, view=view)
        view.message = await interaction.original_response()

class GenericSettingsView(BaseSettingsView):
    def __init__(self, bot: commands.Bot, parent_view: SettingsMainView, title: str, items: list):
        super().__init__(bot, parent_view)
        self.title = title
        for item in items:
            self.add_item(item)

    async def get_embed(self, guild: discord.Guild):
        embed = discord.Embed(
            title=self.title,
            description=f"Configure the settings for {self.title.lower()}.",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        return embed

class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="settings", description="Opens the interactive settings panel for the bot.")
    @utils.has_permission("admin")
    async def settings(self, interaction: discord.Interaction):
        view = SettingsMainView(self.bot)
        embed = await view.get_settings_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="panel", description="Get the link to the web control panel.")
    @utils.has_permission("admin")
    async def panel_link(self, interaction: discord.Interaction):
        """Sends an ephemeral link to the web panel."""
        
        # We get the APP_BASE_URL from your config.py
        link = f"{config.APP_BASE_URL}/panel/{interaction.guild.id}"
        
        embed = discord.Embed(
            title="🌐 Web Panel Link",
            description=f"Click the button below to open the staff control panel for **{interaction.guild.name}**.",
            color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"]
        )
        
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Panel", url=link, emoji="🔗"))
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(SettingsCog(bot))