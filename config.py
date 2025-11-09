import os

APP_ENV = os.getenv("APP_ENV", "development")

if APP_ENV == "production":
    APP_BASE_URL = os.getenv("APP_BASE_URL")
    DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
    DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
    QUART_SECRET_KEY = os.getenv("QUART_SECRET_KEY")
else:
    APP_BASE_URL = os.getenv("APP_BASE_URL_TEST", "http://127.0.0.1:5000")
    DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID_TEST")
    DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET_TEST")
    QUART_SECRET_KEY = os.getenv("QUART_SECRET_KEY_TEST")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

BOT_CONFIG = {
    "ACTIVITY_NAME": "Wave Factory",
    "ACTIVITY_TYPE": "watching",

    "EMBED_COLORS": {
        "INFO": 0x5865F2,
        "SUCCESS": 0x57F287,
        "WARNING": 0xFEE75C,
        "ERROR": 0xED4245,
    }
}