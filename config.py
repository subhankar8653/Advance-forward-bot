from os import environ

class Config:
    # ── Telegram API ──────────────────────────────────────────
    API_ID      = int(environ.get("API_ID", "0"))
    API_HASH    = environ.get("API_HASH", "")
    BOT_TOKEN   = environ.get("BOT_TOKEN", "")

    # ── Owner ─────────────────────────────────────────────────
    OWNER_ID    = int(environ.get("OWNER_ID", "0"))

    # ── MongoDB ───────────────────────────────────────────────
    DATABASE_URI  = environ.get("DATABASE_URI", "")
    DATABASE_NAME = environ.get("DATABASE_NAME", "forwardbot")

    # ── Session names (Railway volume ya local folder) ────────
    SESSION_DIR   = environ.get("SESSION_DIR", "./sessions")
