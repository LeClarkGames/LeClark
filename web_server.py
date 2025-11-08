from quart import Quart, request, render_template, abort, websocket, flash, redirect, url_for, jsonify, make_response, session
import discord
import os
import httpx
import aiosqlite
from dotenv import load_dotenv
import asyncio
import logging
import json
import secrets
import utils
from collections import defaultdict
from urllib.parse import urlencode
import time
import config

import database
from cogs.ranking import get_rank_info
from cogs.verification import VerificationButton
from cogs.submissions import get_panel_embed_and_view

load_dotenv()

app = Quart(__name__, static_folder='static', static_url_path='/static')
log = logging.getLogger(__name__)

app.secret_key = os.getenv("QUART_SECRET_KEY")

user_cache = {}
cache_lock = asyncio.Lock()
CACHE_DURATION_SECONDS = 300

web_cache = {}
CACHE_EXPIRATION = 120

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:5000")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")
DB_FILE = "bot_database.db"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = f"{APP_BASE_URL}/callback"
DISCORD_API_BASE_URL = "https://discord.com/api"

TWITCH_REDIRECT_URI = f"{APP_BASE_URL}/callback/twitch"
YOUTUBE_REDIRECT_URI = f"{APP_BASE_URL}/callback/youtube"

from functools import wraps

def login_required(f):
    @wraps(f)
    async def decorated_function(guild_id: int, *args, **kwargs):
        user_id = session.get('user_id')
        authorized_guilds = session.get('authorized_guilds', [])

        if not user_id or guild_id not in authorized_guilds:
            session['login_redirect_guild_id'] = guild_id
            return redirect(url_for('panel_login_page', guild_id=guild_id))
        
        return await f(guild_id, *args, **kwargs)
    return decorated_function

class WebSocketManager:
    def __init__(self):
        self.active_connections: dict[int, set] = defaultdict(set)
        log.info("WebSocketManager initialized.")

    async def register(self, guild_id: int, ws_conn):
        self.active_connections[guild_id].add(ws_conn)
        log.info(f"New WebSocket connection registered for Guild ID: {guild_id}. Total: {len(self.active_connections[guild_id])}")

    async def unregister(self, guild_id: int, ws_conn):
        if ws_conn in self.active_connections[guild_id]:
            self.active_connections[guild_id].remove(ws_conn)
            log.info(f"WebSocket connection unregistered for Guild ID: {guild_id}. Remaining: {len(self.active_connections[guild_id])}")

    async def broadcast(self, guild_id: int, message: dict):
        if guild_id in self.active_connections:
            message_json = json.dumps(message)
            connections = list(self.active_connections[guild_id])
            for ws_conn in connections:
                try:
                    await ws_conn.send(message_json)
                except Exception:
                    pass
    
ws_manager = WebSocketManager()
app.ws_manager = ws_manager

# --- HELPER FUNCTIONS ---
async def get_verification_data(state: str):
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            async with conn.execute("SELECT server_name, bot_avatar_url FROM verification_links WHERE state = ?", (state,)) as cursor:
                data = await cursor.fetchone()
                if data: return {"server_name": data[0], "bot_avatar_url": data[1]}
    except Exception as e:
        print(f"Error fetching verification data: {e}")
    return {"server_name": "your Discord server", "bot_avatar_url": ""}

async def fetch_user_data(user_id: int):
    """Fetches user data from Discord API with caching."""
    async with cache_lock:
        current_time = asyncio.get_event_loop().time()
        if user_id in user_cache and (current_time - user_cache[user_id]['timestamp']) < CACHE_DURATION_SECONDS:
            return user_cache[user_id]['data']

    bot = app.bot_instance
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if user:
            data = {"name": user.display_name, "avatar_url": user.display_avatar.url}
            async with cache_lock:
                user_cache[user_id] = {
                    'data': data,
                    'timestamp': asyncio.get_event_loop().time()
                }
            return data
    except Exception as e:
        log.warning(f"Could not fetch user data for {user_id}: {e}")

    return {"name": "Unknown User", "avatar_url": "https://cdn.discordapp.com/embed/avatars/0.png"}
    
async def is_valid_staff(guild_id, approver_name):
    return approver_name is not None and approver_name != ""

async def get_full_widget_data(guild_id: int) -> dict:
    bot = app.bot_instance
    guild = bot.get_guild(guild_id)
    if not guild: return {}

    status = await database.get_setting(guild_id, 'submission_status')
    regular_queue_count = await database.get_submission_queue_count(guild_id, 'regular')
    reviewing_user_id = await database.get_current_review(guild_id, 'regular')

    user_ids_to_fetch = set()
    if reviewing_user_id: user_ids_to_fetch.add(reviewing_user_id)

    user_fetch_tasks = [fetch_user_data(uid) for uid in user_ids_to_fetch]
    fetched_users_list = await asyncio.gather(*user_fetch_tasks)
    
    user_data_map = {uid: data for uid, data in zip(user_ids_to_fetch, fetched_users_list)}
    default_user = {"name": "None", "avatar_url": ""}

    reviewing_user_name = user_data_map.get(reviewing_user_id, default_user)['name']

    return {
        "type": "full_update",
        "regular_data": {
            "queue": regular_queue_count,
            "reviewing": reviewing_user_name
        }
    }

# --- WEB ROUTES ---

# --- Staff Panel Authentication Routes ---

@app.route('/panel/login/<int:guild_id>')
async def panel_login_page(guild_id: int):
    """Renders the login page for a specific guild."""
    guild = app.bot_instance.get_guild(guild_id)
    if not guild: return "<h1>Guild not found.</h1>", 404
    return await render_template(
        "panel_login.html",
        guild_name=guild.name,
        guild_icon_url=guild.icon.url if guild.icon else None
    )

async def get_user_access_level(guild: discord.Guild, user_id: int) -> str:
    """Checks if a user is an Admin or a Mod."""
    member = guild.get_member(user_id)
    if not member:
        return "Unknown"
    
    if await utils.has_admin_role(member):
        return "Admin"
    if await utils.has_mod_role(member):
        return "Moderator"
    return "Member"

@app.route('/panel/<int:guild_id>')
@login_required
async def panel_home(guild_id: int):
    """Renders the main dashboard page."""
    guild = app.bot_instance.get_guild(guild_id)
    user_info = await fetch_user_data(int(session.get('user_id')))
    access_level = await get_user_access_level(guild, int(session.get('user_id')))

    # Fetch data for dashboard cards
    last_member_joined = sorted(guild.members, key=lambda m: m.joined_at, reverse=True)[0]
    online_members = sum(1 for m in guild.members if m.status != discord.Status.offline)
    
    excluded_ids = set(config.BOT_CONFIG.get("MILESTONE_EXCLUDED_IDS", []))
    total_bots = sum(1 for m in guild.members if m.bot)
    true_member_count = guild.member_count - total_bots - len(excluded_ids)
    
    return await render_template(
        "panel_dashboard.html",
        guild_id=guild_id, guild_name=guild.name, guild_icon_url=guild.icon.url if guild.icon else None,
        user_name=user_info['name'], user_avatar_url=user_info['avatar_url'],
        last_member=last_member_joined.display_name, online_count=online_members, member_count=true_member_count,
        access_level=access_level
    )

@app.route('/panel/<int:guild_id>/widgets')
@login_required
async def panel_widgets(guild_id: int):
    """Renders the widgets page."""
    guild = app.bot_instance.get_guild(guild_id)
    user_info = await fetch_user_data(int(session.get('user_id')))
    access_level = await get_user_access_level(guild, int(session.get('user_id')))

    # Get the unique token for the guild's widgets
    token = await database.get_or_create_widget_token(guild_id)
    widget_url_base = f"{APP_BASE_URL}/widget/view/{token}"

    return await render_template(
        "panel_widgets.html",
        guild_id=guild_id, guild_name=guild.name, guild_icon_url=guild.icon.url if guild.icon else None,
        user_name=user_info['name'], user_avatar_url=user_info['avatar_url'],
        regular_widget_url=f"{widget_url_base}?type=regular",
        koth_widget_url=f"{widget_url_base}?type=koth",
        access_level=access_level
    )

@app.route('/panel/<int:guild_id>/mod-menu')
@login_required
async def panel_mod_menu(guild_id: int):
    """Renders the moderation menu page."""
    guild = app.bot_instance.get_guild(guild_id)
    user_info = await fetch_user_data(int(session.get('user_id')))
    access_level = await get_user_access_level(guild, int(session.get('user_id')))

    admin_role_ids = await utils.get_admin_roles(guild_id)
    mod_role_ids = await utils.get_mod_roles(guild_id)

    admin_members = []
    mod_members = []
    for member in guild.members:
        if member.bot: continue
        member_role_ids = {role.id for role in member.roles}
        if any(role_id in member_role_ids for role_id in admin_role_ids):
            admin_members.append({"name": member.display_name, "avatar_url": member.display_avatar.url})
        elif any(role_id in member_role_ids for role_id in mod_role_ids):
            mod_members.append({"name": member.display_name, "avatar_url": member.display_avatar.url})

    return await render_template(
        "panel_mod_menu.html",
        guild_id=guild_id, guild_name=guild.name, guild_icon_url=guild.icon.url if guild.icon else None,
        user_name=user_info['name'], user_avatar_url=user_info['avatar_url'],
        access_level=access_level,
        admin_members=admin_members,
        mod_members=mod_members
    )

@app.route('/api/v1/actions/moderate/<int:guild_id>', methods=['POST'])
@login_required
async def api_moderate_user(guild_id: int):
    """API endpoint to queue a moderation action."""
    form = await request.form
    task = {
        "action": "moderate_user",
        "guild_id": guild_id,
        "moderator_id": int(session.get('user_id')),
        "target_id": form.get('target_id'),
        "mod_action": form.get('mod_action'),
        "reason": form.get('reason', 'No reason provided')
    }

    # Basic validation
    if not task['target_id'] or not task['mod_action']:
        return jsonify({"error": "User ID and Action are required."}), 400

    try:
        # Put the task into the bot's queue
        app.bot_instance.action_queue.put_nowait(task)
        return jsonify({"message": f"Action '{task['mod_action'].capitalize()}' has been successfully queued."}), 200
    except Exception as e:
        log.error(f"Failed to queue moderation action: {e}")
        return jsonify({"error": "Failed to queue the action. Please try again later."}), 500

@app.route('/login')
async def login():
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify guilds"
    }
    return redirect(f"{DISCORD_API_BASE_URL}/oauth2/authorize?{urlencode(params)}")

@app.route('/logout/<int:guild_id>')
async def logout(guild_id: int):
    session.clear() # Clears all session data
    return redirect(url_for('panel_home', guild_id=guild_id))

@app.route('/callback')
async def callback():
    code = request.args.get('code')
    guild_id_to_check = session.get('login_redirect_guild_id')

    if not code or not guild_id_to_check:
        return redirect(url_for('home'))

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=data, headers=headers)
    
    token_data = token_response.json()
    access_token = token_data.get("access_token")

    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        user_response = await client.get(f"{DISCORD_API_BASE_URL}/users/@me", headers=headers)
    
    user_data = user_response.json()
    user_id = int(user_data['id'])

    # --- Start of Authorization Check ---
    guild = app.bot_instance.get_guild(guild_id_to_check)
    if not guild:
        return "<h1>Error: The bot is not in the guild you're trying to access.</h1>", 403

    member = guild.get_member(user_id)
    if not member:
        # The user is in the guild, but the bot's member cache might be incomplete.
        # It's safer to deny access than to grant it incorrectly.
        return await render_template("access_denied.html", guild_name=guild.name)

    is_staff = await utils.has_mod_role(member)

    if not is_staff:
        return await render_template("access_denied.html", guild_name=guild.name)
    # --- End of Authorization Check ---

    # Store user info and authorization status in the session
    session['user_id'] = user_data['id']
    authorized_guilds = session.get('authorized_guilds', [])
    if guild_id_to_check not in authorized_guilds:
        authorized_guilds.append(guild_id_to_check)
    session['authorized_guilds'] = authorized_guilds
    
    return redirect(url_for('panel_home', guild_id=guild_id_to_check))

@app.route('/')
async def home():
    return "Web server for LeClark Bot is active."

@app.route('/leaderboard/<int:guild_id>')
async def xp_leaderboard(guild_id: int):
    cache_key = f"leaderboard_{guild_id}"
    current_time = time.time()
    if cache_key in web_cache and (current_time - web_cache[cache_key]['timestamp']) < CACHE_EXPIRATION:
        return web_cache[cache_key]['data']
        
    bot = app.bot_instance
    guild = bot.get_guild(guild_id)
    if not guild: 
        return await render_template("leaderboard.html", title="Error", guild_name="Unknown Server", users=[])

    raw_leaderboard = await database.get_leaderboard(guild_id, limit=100)
    
    # --- FIX STARTS HERE ---
    
    users = [] # Initialize the list first
    if raw_leaderboard: # Only proceed if there's data
        user_ids = [user_id for user_id, xp in raw_leaderboard]
        user_data_task = asyncio.gather(*[fetch_user_data(uid) for uid in user_ids])
        fetched_users = await asyncio.gather(user_data_task)
        
        for i, (user_id, xp) in enumerate(raw_leaderboard):
            user_info = fetched_users[i]
            rank_name, _, _ = get_rank_info(xp)
            users.append({
                "name": user_info['name'],
                "avatar_url": user_info['avatar_url'],
                "score": xp,
                "details": f"Level: {rank_name}"
            })

    # Now, we can safely render the template
    rendered_template = await render_template(
        "leaderboard.html", 
        title=f"XP Leaderboard - {guild.name}", 
        guild_name=guild.name, 
        guild_icon_url=guild.icon.url if guild.icon else None, 
        users=users, 
        score_name="XP"
    )

    web_cache[cache_key] = {
        'data': rendered_template,
        'timestamp': current_time
    }
    
    return rendered_template

@app.route('/widget/<int:guild_id>')
async def widget_link_page(guild_id: int):
    token = await database.get_or_create_widget_token(guild_id)
    widget_url_base = f"{APP_BASE_URL}/widget/view/{token}"
    return await render_template("widget_link.html", widget_url_base=widget_url_base, guild_id=guild_id)

@app.route('/widget/view/<token>')
async def view_widget(token: str):
    guild_id = await database.get_guild_from_token(token)
    if not guild_id:
        return "<h1>Invalid or expired token. Please regenerate your link.</h1>", 403
    return await render_template("widget.html", token=token)

@app.websocket('/ws')
async def websocket_endpoint():
    ws_conn = websocket._get_current_object()
    token = websocket.args.get('token')
    if not token:
        await ws_conn.close(1008, "Token is required"); return

    guild_id = await database.get_guild_from_token(token)
    if not guild_id:
        await ws_conn.close(1008, "Invalid token"); return

    await ws_manager.register(guild_id, ws_conn)
    try:
        initial_data = await get_full_widget_data(guild_id)
        await ws_conn.send(json.dumps(initial_data))
        while True:
            await ws_conn.receive()
    except asyncio.CancelledError:
        log.info(f"WebSocket task for guild {guild_id} cancelled.")
    finally:
        await ws_manager.unregister(guild_id, ws_conn)

@app.route('/callback/twitch')
async def callback_twitch():
    auth_code, state = request.args.get('code'), request.args.get('state')
    if not auth_code or not state: return "Error: Missing authorization code or state.", 400
    token_url = "https://id.twitch.tv/oauth2/token"
    token_params = {"client_id": TWITCH_CLIENT_ID, "client_secret": TWITCH_CLIENT_SECRET, "code": auth_code, "grant_type": "authorization_code", "redirect_uri": TWITCH_REDIRECT_URI}
    async with httpx.AsyncClient() as client: response = await client.post(token_url, params=token_params)
    token_data = response.json()
    if 'access_token' not in token_data: return "Error: Could not retrieve access token from Twitch.", 400
    access_token = token_data['access_token']
    user_url = "https://api.twitch.tv/helix/users"
    headers = {"Authorization": f"Bearer {access_token}", "Client-Id": TWITCH_CLIENT_ID}
    async with httpx.AsyncClient() as client: user_response = await client.get(user_url, headers=headers)
    user_data = user_response.json()
    if not user_data.get('data'): return "Error: Could not retrieve user data from Twitch.", 400
    account_name = user_data['data'][0]['login']
    try:
        template_data = await get_verification_data(state)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ? AND status = 'pending'", (account_name, state))
            await conn.commit()
        return await render_template("success.html", account_name=account_name, **template_data)
    except Exception as e:
        print(f"Database error during Twitch callback: {e}"); return "An internal server error occurred.", 500

@app.route('/callback/youtube')
async def callback_youtube():
    auth_code, state = request.args.get('code'), request.args.get('state')
    if not auth_code or not state: return "Error: Missing authorization code or state.", 400
    token_url = "https://oauth2.googleapis.com/token"
    token_params = {"client_id": YOUTUBE_CLIENT_ID, "client_secret": YOUTUBE_CLIENT_SECRET, "code": auth_code, "grant_type": "authorization_code", "redirect_uri": YOUTUBE_REDIRECT_URI}
    async with httpx.AsyncClient() as client: response = await client.post(token_url, data=token_params)
    token_data = response.json()
    if 'access_token' not in token_data: return "Error: Could not retrieve access token from Google.", 400
    access_token = token_data['access_token']
    user_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client: user_response = await client.get(user_url, headers=headers)
    user_data = user_response.json()
    if 'name' not in user_data: return "Error: Could not retrieve user data from Google.", 400
    account_name = user_data['name']
    try:
        template_data = await get_verification_data(state)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ? AND status = 'pending'", (account_name, state))
            await conn.commit()
        return await render_template("success.html", account_name=account_name, **template_data)
    except Exception as e:
        print(f"Database error during YouTube callback: {e}"); return "An internal server error occurred.", 500

@app.route('/api/v1/actions/run-setup/<int:guild_id>', methods=['POST'])
@login_required
async def api_run_setup(guild_id: int):
    """API endpoint to *directly* run setup commands."""
    form = await request.form
    setup_type = form.get('setup_type')
    bot = app.bot_instance
    guild = bot.get_guild(guild_id)
    
    if not guild:
        return jsonify({"error": "Bot could not find this guild."}), 404

    if setup_type == 'verification':
        channel_id = await database.get_setting(guild.id, 'verification_channel_id')
        if not channel_id:
            return jsonify({"error": "Verification channel not set in bot settings."}), 400
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return jsonify({"error": "Verification channel not found."}), 404
        
        try:
            embed = discord.Embed(title="Server Verification", description="To gain access to the server, click the button below and complete the required action.", color=config.BOT_CONFIG["EMBED_COLORS"]["INFO"])
            view = VerificationButton(bot)
            await channel.send(embed=embed, view=view)
            return jsonify({"message": f"Verification message sent to {channel.mention}!"}), 200
        except discord.Forbidden:
            return jsonify({"error": f"Bot lacks permission to send messages in {channel.mention}."}), 403
        except Exception as e:
            log.error(f"Error in panel setup (verification): {e}")
            return jsonify({"error": "An internal error occurred."}), 500

    elif setup_type == 'submission':
        channel_id = await database.get_setting(guild.id, 'review_channel_id')
        if not channel_id:
            return jsonify({"error": "Review channel not set in bot settings."}), 400
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return jsonify({"error": "Review channel not found."}), 404

        try:
            # Try to delete the old panel message if it exists
            if submissions_cog := bot.get_cog("Submissions"):
                if old_panel := await submissions_cog.get_panel_message(guild):
                    try: await old_panel.delete()
                    except (discord.Forbidden, discord.NotFound): pass
            
            embed, view = await get_panel_embed_and_view(guild, bot)
            panel_message = await channel.send(embed=embed, view=view)
            
            await database.update_setting(guild.id, 'review_panel_message_id', panel_message.id)
            await database.update_setting(guild.id, 'submission_status', 'closed')

            return jsonify({"message": f"Submission panel has been posted in {channel.mention}."}), 200
        except discord.Forbidden:
            return jsonify({"error": f"Bot lacks permission to send messages in {channel.mention}."}), 403
        except Exception as e:
            log.error(f"Error in panel setup (submission): {e}")
            return jsonify({"error": "An internal error occurred."}), 500

    else:
        return jsonify({"error": "Invalid setup type."}), 400

@app.route('/api/v1/actions/send-message/<int:guild_id>', methods=['POST'])
@login_required
async def api_send_message(guild_id: int):
    """API endpoint to queue sending a message."""
    form = await request.form
    task = {
        "action": "send_message", "guild_id": guild_id,
        "moderator_id": int(session.get('user_id')),
        "channel_id": form.get('channel_id'),
        "content": form.get('message_content'),
        "is_embed": form.get('is_embed') == 'true'
    }
    if not task['channel_id'] or not task['content']:
        return jsonify({"error": "Channel ID and Content are required."}), 400

    app.bot_instance.action_queue.put_nowait(task)
    return jsonify({"message": "Message queued successfully."}), 200

@app.route('/api/v1/audit-log/<int:guild_id>')
@login_required
async def api_get_audit_log(guild_id: int):
    """API endpoint to fetch the audit log."""
    guild = app.bot_instance.get_guild(guild_id)
    logs = []
    try:
        async for entry in guild.audit_logs(limit=25):
            logs.append({
                "user": str(entry.user),
                "action": entry.action.name.replace('_', ' ').title(),
                "target": str(entry.target) if entry.target else "N/A",
                "reason": str(entry.reason) if entry.reason else "No reason provided."
            })
        return jsonify(logs)
    except discord.Forbidden:
        return jsonify({"error": "Bot lacks permission to view audit logs."}), 403
    except Exception as e:
        log.error(f"Failed to fetch audit log for guild {guild_id}: {e}")
        return jsonify({"error": "An internal error occurred."}), 500
    
@app.route('/api/v1/actions/manage-staff/<int:guild_id>', methods=['POST'])
@login_required
async def api_manage_staff(guild_id: int):
    """API endpoint to add or remove a staff role from a user."""
    form = await request.form
    # Ensure the user making the request is an Admin
    guild = app.bot_instance.get_guild(guild_id)
    moderator = guild.get_member(int(session.get('user_id')))
    if not await utils.has_admin_role(moderator):
        return jsonify({"error": "You must be a Bot Admin to perform this action."}), 403

    task = {
        "action": "manage_staff",
        "guild_id": guild_id,
        "moderator_id": int(session.get('user_id')),
        "target_id": form.get('target_id'),
        "role_type": form.get('role_type'), # 'admin' or 'mod'
        "role_action": form.get('role_action') # 'add' or 'remove'
    }

    if not all(k in task for k in ['target_id', 'role_type', 'role_action']):
        return jsonify({"error": "Missing required fields."}), 400

    app.bot_instance.action_queue.put_nowait(task)
    return jsonify({"message": f"Staff role {task['role_action']} action queued successfully."}), 200

@app.route('/api/v1/actions/reset-stuck-review/<int:guild_id>', methods=['POST'])
@login_required
async def api_reset_stuck_review(guild_id: int):
    """API endpoint to queue resetting a stuck submission."""
    task = {
        "action": "reset_stuck_review",
        "guild_id": guild_id,
        "moderator_id": int(session.get('user_id')),
    }
    app.bot_instance.action_queue.put_nowait(task)
    return jsonify({"message": "Reset command queued successfully."}), 200