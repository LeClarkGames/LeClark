import aiosqlite
import logging
from datetime import datetime
import secrets
from typing import Optional

log = logging.getLogger(__name__)
DB_FILE = "bot_database.db"
db_conn = None

async def get_db_connection():
    """Gets a connection to the SQLite database."""
    global db_conn
    if db_conn:
        return db_conn
    try:
        db_conn = await aiosqlite.connect(DB_FILE)
        await db_conn.execute("PRAGMA journal_mode=WAL;")
        log.info("Successfully connected to the SQLite database.")
        return db_conn
    except Exception as e:
        log.critical(f"Could not connect to the SQLite database: {e}")
        return None

async def initialize_database():
    """Initializes and updates the database schema if needed."""
    conn = await get_db_connection()
    if not conn: return
    async with conn.cursor() as cursor:
        # --- Main Tables ---
        await cursor.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY, log_channel_id INTEGER, report_channel_id INTEGER,
                verification_channel_id INTEGER, unverified_role_id INTEGER, member_role_id INTEGER,
                verification_message_id INTEGER, admin_role_ids TEXT, mod_role_ids TEXT,
                mod_chat_channel_id INTEGER, temp_vc_hub_id INTEGER, temp_vc_category_id INTEGER,
                submission_channel_id INTEGER, review_channel_id INTEGER, submission_status TEXT DEFAULT 'closed',
                review_panel_message_id INTEGER, announcement_channel_id INTEGER, last_milestone_count INTEGER DEFAULT 0,
                ranking_system_enabled INTEGER DEFAULT 1, submissions_system_enabled INTEGER DEFAULT 1,
                temp_vc_system_enabled INTEGER DEFAULT 1, reporting_system_enabled INTEGER DEFAULT 1
            )
        """)
        await cursor.execute("""CREATE TABLE IF NOT EXISTS channel_activity (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,message_count INTEGER DEFAULT 0, voice_seconds INTEGER DEFAULT 0,PRIMARY KEY (guild_id, user_id, channel_id))""")
        await cursor.execute("CREATE TABLE IF NOT EXISTS warnings (warning_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL, reason TEXT, issued_at TIMESTAMP NOT NULL, log_message_id INTEGER)")
        await cursor.execute("CREATE TABLE IF NOT EXISTS temporary_vcs (channel_id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, text_channel_id INTEGER)")
        await cursor.execute("CREATE TABLE IF NOT EXISTS music_submissions ( submission_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, track_url TEXT NOT NULL, status TEXT NOT NULL, submitted_at TIMESTAMP NOT NULL, reviewer_id INTEGER, submission_type TEXT DEFAULT 'regular' )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS ranking ( user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, xp INTEGER DEFAULT 0, PRIMARY KEY (user_id, guild_id) )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS verification_links ( state TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, status TEXT DEFAULT 'pending', verified_account TEXT, server_name TEXT, bot_avatar_url TEXT )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS gmail_verification ( user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, verification_code TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, guild_id) )")
        await cursor.execute("CREATE TABLE IF NOT EXISTS rank_rewards (guild_id INTEGER NOT NULL, rank_level INTEGER NOT NULL, role_id INTEGER NOT NULL, PRIMARY KEY (guild_id, rank_level))")
        await cursor.execute("CREATE TABLE IF NOT EXISTS widget_tokens (token TEXT PRIMARY KEY, guild_id INTEGER NOT NULL UNIQUE)")

        # --- Schema Updates ---
        await cursor.execute("PRAGMA table_info(guild_settings)")
        settings_columns = [row[1] for row in await cursor.fetchall()]

        await cursor.execute("PRAGMA table_info(channel_activity)")
        activity_columns = [row[1] for row in await cursor.fetchall()]
        if 'last_updated' not in activity_columns:
            await cursor.execute("ALTER TABLE channel_activity ADD COLUMN last_updated TIMESTAMP")

        if 'warning_limit' not in settings_columns: await cursor.execute("ALTER TABLE guild_settings ADD COLUMN warning_limit INTEGER DEFAULT 3")
        if 'warning_action' not in settings_columns: await cursor.execute("ALTER TABLE guild_settings ADD COLUMN warning_action TEXT DEFAULT 'mute'")
        if 'warning_action_duration' not in settings_columns: await cursor.execute("ALTER TABLE guild_settings ADD COLUMN warning_action_duration INTEGER DEFAULT 60")
        if 'submissions_system_enabled' not in settings_columns: 
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN submissions_system_enabled INTEGER DEFAULT 1")
        if 'temp_vc_system_enabled' not in settings_columns: 
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN temp_vc_system_enabled INTEGER DEFAULT 1")
        if 'reporting_system_enabled' not in settings_columns: 
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN reporting_system_enabled INTEGER DEFAULT 1")
        if 'ranking_system_enabled' not in settings_columns: 
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN ranking_system_enabled INTEGER DEFAULT 1")
        if 'free_verification_modes' not in settings_columns: 
            await cursor.execute("ALTER TABLE guild_settings ADD COLUMN free_verification_modes TEXT DEFAULT 'captcha,twitch,youtube,gmail'")

        await cursor.execute("PRAGMA table_info(warnings)")
        warnings_columns = [row[1] for row in await cursor.fetchall()]
        if 'moderator_id' not in warnings_columns: await cursor.execute("ALTER TABLE warnings ADD COLUMN moderator_id INTEGER NOT NULL DEFAULT 0")
        if 'reason' not in warnings_columns: await cursor.execute("ALTER TABLE warnings ADD COLUMN reason TEXT")
        if 'issued_at' not in warnings_columns: await cursor.execute("ALTER TABLE warnings ADD COLUMN issued_at TIMESTAMP")

    await conn.commit()
    log.info("Database tables initialized/updated successfully.")

# --- SETTINGS FUNCTIONS ---
async def get_setting(guild_id, setting_name):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute(f"SELECT {setting_name} FROM guild_settings WHERE guild_id = ?", (guild_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_setting(guild_id, setting_name, value):
    conn = await get_db_connection()
    sql = f"INSERT INTO guild_settings (guild_id, {setting_name}) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET {setting_name} = excluded.{setting_name}"
    await conn.execute(sql, (guild_id, value))
    await conn.commit()

async def get_all_settings(guild_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = await cursor.fetchone()
        if not row: return {}
        columns = [description[0] for description in cursor.description]
        return dict(zip(columns, row))

# --- RANK REWARD FUNCTIONS ---
async def set_rank_reward(guild_id: int, rank_level: int, role_id: int):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO rank_rewards (guild_id, rank_level, role_id) VALUES (?, ?, ?) ON CONFLICT(guild_id, rank_level) DO UPDATE SET role_id = excluded.role_id", (guild_id, rank_level, role_id))
    await conn.commit()

async def remove_rank_reward(guild_id: int, rank_level: int):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM rank_rewards WHERE guild_id = ? AND rank_level = ?", (guild_id, rank_level))
    await conn.commit()

async def get_rank_reward(guild_id: int, rank_level: int):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT role_id FROM rank_rewards WHERE guild_id = ? AND rank_level = ?", (guild_id, rank_level))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_all_rank_rewards(guild_id: int):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT rank_level, role_id FROM rank_rewards WHERE guild_id = ?", (guild_id,))
        return await cursor.fetchall()

# --- WARNINGS FUNCTIONS ---
async def add_warning(guild_id, user_id, moderator_id, reason, log_message_id):
    conn = await get_db_connection()
    await conn.execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, issued_at, log_message_id) VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, moderator_id, reason, datetime.utcnow(), log_message_id)
    )
    await conn.commit()

async def get_warnings(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute(
            "SELECT moderator_id, reason, issued_at, warning_id FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY issued_at ASC",
            (guild_id, user_id)
        )
        return await cursor.fetchall()

async def get_warnings_count(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def clear_warnings(guild_id, user_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    await conn.commit()

# --- TEMP VC FUNCTIONS ---
async def add_temp_vc(channel_id, owner_id, text_channel_id=None):
    conn = await get_db_connection()
    await conn.execute("INSERT OR REPLACE INTO temporary_vcs (channel_id, owner_id, text_channel_id) VALUES (?, ?, ?)", (channel_id, owner_id, text_channel_id))
    await conn.commit()

async def remove_temp_vc(channel_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
    await conn.commit()

async def get_temp_vc_owner(channel_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT owner_id FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_temp_vc_text_channel_id(channel_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT text_channel_id FROM temporary_vcs WHERE channel_id = ?", (channel_id,))
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_temp_vc_owner(channel_id, new_owner_id):
    conn = await get_db_connection()
    await conn.execute("UPDATE temporary_vcs SET owner_id = ? WHERE channel_id = ?", (new_owner_id, channel_id))

# --- SUBMISSION FUNCTIONS ---
async def add_submission(guild_id, user_id, track_url, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("INSERT INTO music_submissions (guild_id, user_id, track_url, status, submitted_at, submission_type) VALUES (?, ?, ?, ?, ?, ?)",(guild_id, user_id, track_url, "pending", datetime.utcnow(), submission_type))
        submission_id = cursor.lastrowid
    await conn.commit()
    return submission_id

async def get_user_submission_count(guild_id, user_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM music_submissions WHERE guild_id = ? AND user_id = ? AND submission_type = ?", (guild_id, user_id, submission_type))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def get_submission_queue_count(guild_id, submission_type='regular', status="pending"):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(*) FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status = ?", (guild_id, submission_type, status))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def get_total_reviewed_count(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT COUNT(DISTINCT submission_id) FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status = 'reviewed'", (guild_id, submission_type))
        result = await cursor.fetchone()
        return result[0] if result else 0
        
async def get_next_submission(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT submission_id, user_id, track_url FROM music_submissions WHERE guild_id = ? AND status = 'pending' AND submission_type = ? ORDER BY submitted_at ASC LIMIT 1", (guild_id, submission_type))
        return await cursor.fetchone()

async def update_submission_status(submission_id, status, reviewer_id=None):
    conn = await get_db_connection()
    await conn.execute("UPDATE music_submissions SET status = ?, reviewer_id = ? WHERE submission_id = ?", (status, reviewer_id, submission_id))
    await conn.commit()

async def clear_session_submissions(guild_id, submission_type='regular'):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM music_submissions WHERE guild_id = ? AND submission_type = ? AND status != 'reviewed'", (guild_id, submission_type))
    await conn.commit()

async def prioritize_submission(submission_id):
    conn = await get_db_connection()
    await conn.execute("UPDATE music_submissions SET submitted_at = '1970-01-01 00:00:00' WHERE submission_id = ?", (submission_id,))
    await conn.commit()

async def get_latest_pending_submission_id(guild_id: int, user_id: int, submission_type: str = 'regular') -> int | None:
    """Gets the ID of a user's most recent pending submission."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute(
            "SELECT submission_id FROM music_submissions WHERE guild_id = ? AND user_id = ? AND status = 'pending' AND submission_type = ? ORDER BY submitted_at DESC LIMIT 1",
            (guild_id, user_id, submission_type)
        )
        result = await cursor.fetchone()
        return result[0] if result else None

# --- RANKING SYSTEM FUNCTIONS ---
async def get_user_xp(guild_id, user_id):
    """Gets just the user's XP."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT xp FROM ranking WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else 0

async def update_user_xp(guild_id, user_id, xp_to_add):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO ranking (guild_id, user_id, xp) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = xp + excluded.xp", (guild_id, user_id, xp_to_add))
    await conn.commit()

async def get_user_rank(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT xp FROM ranking WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        if not result: return None, None
        user_xp = result[0]
        await cursor.execute("SELECT COUNT(*) FROM ranking WHERE guild_id = ? AND xp > ?", (guild_id, user_xp))
        rank_result = await cursor.fetchone()
        rank = rank_result[0] + 1
        return user_xp, rank

async def get_leaderboard(guild_id, limit=10):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT user_id, xp FROM ranking WHERE guild_id = ? ORDER BY xp DESC LIMIT ?", (guild_id, limit))
        return await cursor.fetchall()

# --- OAUTH & GMAIL VERIFICATION FUNCTIONS ---
async def create_verification_link(state, guild_id, user_id, server_name, bot_avatar_url):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO verification_links (state, guild_id, user_id, server_name, bot_avatar_url) VALUES (?, ?, ?, ?, ?)", (state, guild_id, user_id, server_name, bot_avatar_url))
    await conn.commit()

async def complete_verification(state, account_name):
    conn = await get_db_connection()
    await conn.execute("UPDATE verification_links SET status = 'verified', verified_account = ? WHERE state = ?", (account_name, state))
    await conn.commit()

async def get_completed_verifications():
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT state, guild_id, user_id, verified_account FROM verification_links WHERE status = 'verified'")
        return await cursor.fetchall()

async def delete_verification_link(state):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM verification_links WHERE state = ?", (state,))
    await conn.commit()

async def store_gmail_code(guild_id, user_id, code):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO gmail_verification (guild_id, user_id, verification_code) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET verification_code = excluded.verification_code, created_at = CURRENT_TIMESTAMP", (guild_id, user_id, code))
    await conn.commit()

async def get_gmail_code(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT verification_code FROM gmail_verification WHERE guild_id = ? AND user_id = ? AND created_at > datetime('now', '-10 minutes')", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else None

async def delete_gmail_code(guild_id, user_id):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM gmail_verification WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    await conn.commit()

async def get_or_create_widget_token(guild_id: int) -> str:
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT token FROM widget_tokens WHERE guild_id = ?", (guild_id,))
        result = await cursor.fetchone()
        if result:
            return result[0]
        else:
            token = secrets.token_urlsafe(32)
            await cursor.execute("INSERT INTO widget_tokens (token, guild_id) VALUES (?, ?)", (token, guild_id))
            await conn.commit()
            return token

async def get_guild_from_token(token: str) -> Optional[int]:
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT guild_id FROM widget_tokens WHERE token = ?", (token,))
        result = await cursor.fetchone()
        return result[0] if result else None
    
async def get_current_review(guild_id: int, submission_type: str = 'regular'):
    """Gets the user_id of the submission currently being reviewed."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute(
            "SELECT user_id FROM music_submissions WHERE guild_id = ? AND status = 'reviewing' AND submission_type = ? ORDER BY submitted_at ASC LIMIT 1",
            (guild_id, submission_type)
        )
        result = await cursor.fetchone()
        return result[0] if result else None

async def update_user_activity(guild_id: int, user_id: int, message_count: int = 0, voice_seconds: int = 0):
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO user_activity (guild_id, user_id, message_count, voice_seconds, last_updated)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
        message_count = message_count + excluded.message_count,
        voice_seconds = voice_seconds + excluded.voice_seconds,
        last_updated = CURRENT_TIMESTAMP
    """, (guild_id, user_id, message_count, voice_seconds))
    await conn.commit()

async def set_tier_requirement(guild_id: int, tier_level: int, messages: int, voice_hours: int):
    conn = await get_db_connection()
    await conn.execute("INSERT INTO tier_requirements (guild_id, tier_level, messages_req, voice_hours_req) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, tier_level) DO UPDATE SET messages_req=excluded.messages_req, voice_hours_req=excluded.voice_hours_req", (guild_id, tier_level, messages, voice_hours))
    await conn.commit()

# REPLACE this function
async def get_user_activity(guild_id: int, user_id: int):
    """Calculates a user's total activity by summing their channel_activity."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT SUM(message_count), SUM(voice_seconds) 
            FROM channel_activity 
            WHERE guild_id = ? AND user_id = ?
        """, (guild_id, user_id))
        result = await cursor.fetchone()
        if not result or result[0] is None: 
            return {'message_count': 0, 'voice_seconds': 0}
        return {'message_count': result[0], 'voice_seconds': result[1]}

async def get_all_tier_requirements(guild_id: int):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT tier_level, messages_req, voice_hours_req FROM tier_requirements WHERE guild_id = ?", (guild_id,))
        rows = await cursor.fetchall()
        return {row[0]: {'messages_req': row[1], 'voice_hours_req': row[2]} for row in rows}

async def create_or_update_tier_approval_request(guild_id, user_id, next_tier, token, message_id):
    """Creates a new tier approval request or updates an existing one for a user."""
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO tier_approval_requests (guild_id, user_id, next_tier, token, message_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET
            next_tier = excluded.next_tier,
            token = excluded.token,
            message_id = excluded.message_id,
            created_at = CURRENT_TIMESTAMP
    """, (guild_id, user_id, next_tier, token, message_id))
    await conn.commit()

async def get_tier_approval_request(guild_id, user_id):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT token FROM tier_approval_requests WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        result = await cursor.fetchone()
        return result[0] if result else None

async def get_tier_request_by_token(token: str):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT guild_id, user_id, next_tier, message_id FROM tier_approval_requests WHERE token = ?", (token,))
        result = await cursor.fetchone()
        if not result: return None
        return {'guild_id': result[0], 'user_id': result[1], 'next_tier': result[2], 'message_id': result[3], 'token': token}

async def delete_tier_request(token: str):
    conn = await get_db_connection()
    await conn.execute("DELETE FROM tier_approval_requests WHERE token = ?", (token,))
    await conn.commit()

async def get_all_tier_roles(guild_id: int):
    settings = await get_all_settings(guild_id)
    return {
        1: settings.get('tier1_role_id'), 2: settings.get('tier2_role_id'),
        3: settings.get('tier3_role_id'), 4: settings.get('tier4_role_id')
    }

async def update_channel_activity(guild_id: int, user_id: int, channel_id: int, message_count: int = 0, voice_seconds: int = 0):
    conn = await get_db_connection()
    await conn.execute("""
        INSERT INTO channel_activity (guild_id, user_id, channel_id, message_count, voice_seconds, last_updated)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(guild_id, user_id, channel_id) DO UPDATE SET
        message_count = message_count + excluded.message_count,
        voice_seconds = voice_seconds + excluded.voice_seconds,
        last_updated = CURRENT_TIMESTAMP
    """, (guild_id, user_id, channel_id, message_count, voice_seconds))
    await conn.commit()

async def get_user_channel_activity(guild_id: int, user_id: int):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT channel_id, message_count, voice_seconds FROM channel_activity WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
        return await cursor.fetchall()

# REPLACE this function
async def get_top_users_overall(guild_id: int, limit: int = 5):
    """Gets top users by summing their activity across all channels."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT user_id, SUM(message_count), SUM(voice_seconds) 
            FROM channel_activity 
            WHERE guild_id = ? 
            GROUP BY user_id 
            ORDER BY SUM(message_count) DESC, SUM(voice_seconds) DESC 
            LIMIT ?
        """, (guild_id, limit))
        return await cursor.fetchall()

async def get_top_text_channels(guild_id: int, limit: int = 5):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT channel_id, SUM(message_count) as total_msgs FROM channel_activity WHERE guild_id = ? GROUP BY channel_id ORDER BY total_msgs DESC LIMIT ?", (guild_id, limit))
        return await cursor.fetchall()

async def get_top_voice_channels(guild_id: int, limit: int = 5):
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("SELECT channel_id, SUM(voice_seconds) as total_voice FROM channel_activity WHERE guild_id = ? GROUP BY channel_id ORDER BY total_voice DESC LIMIT ?", (guild_id, limit))
        return await cursor.fetchall()
    
async def get_top_users_today(guild_id: int, limit: int = 5):
    """Gets top users by activity in the last 24 hours."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT user_id, SUM(message_count), SUM(voice_seconds) 
            FROM channel_activity 
            WHERE guild_id = ? AND last_updated >= datetime('now', '-1 day')
            GROUP BY user_id 
            ORDER BY SUM(message_count) DESC, SUM(voice_seconds) DESC 
            LIMIT ?
        """, (guild_id, limit))
        return await cursor.fetchall()
    
async def get_all_pending_tier_requests(guild_id: int):
    """Gets all pending tier approval requests for a guild."""
    conn = await get_db_connection()
    async with conn.cursor() as cursor:
        await cursor.execute("""
            SELECT user_id, next_tier, token, message_id 
            FROM tier_approval_requests 
            WHERE guild_id = ? 
            ORDER BY created_at ASC
        """, (guild_id,))
        rows = await cursor.fetchall()
        return [{'user_id': r[0], 'next_tier': r[1], 'token': r[2], 'message_id': r[3]} for r in rows]