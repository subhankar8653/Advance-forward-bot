"""
main.py — Bot ka entry point
Railway pe:  python main.py
"""
import asyncio
import logging
import os
from pyrogram import Client
from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def main():
    os.makedirs(Config.SESSION_DIR, exist_ok=True)

    # ── Start the main Bot ──────────────────────────────────
    bot = Client(
        name        = "ForwardBot",
        api_id      = Config.API_ID,
        api_hash    = Config.API_HASH,
        bot_token   = Config.BOT_TOKEN,
        plugins     = dict(root="plugins"),
        in_memory   = True
    )

    await bot.start()
    me = await bot.get_me()
    logger.info(f"Bot started: @{me.username}")

    # ── Start saved userbots ────────────────────────────────
    from userbot import start_all_clients
    await start_all_clients()

    # ── Attach auto-listeners to userbots ───────────────────
    from auto_listener import start_listeners
    await start_listeners()

    logger.info("All systems ready ✅")

    await bot.idle()
    await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
