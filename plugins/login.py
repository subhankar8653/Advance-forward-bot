"""
/login  → phone number maango
/logout → session hata do
"""
from pyrogram import Client, filters
from pyrogram.types import Message
from userbot import LoginSession, logout, get_client
from database import db
from config import Config


# ─────────────────────────── /login ───────────────────────────

@Client.on_message(filters.private & filters.command("login"))
async def cmd_login(bot: Client, msg: Message):
    owner_id = msg.from_user.id

    # Check karo existing session hai kya
    existing = await get_client(owner_id)
    if existing:
        me = await existing.get_me()
        return await msg.reply(
            f"✅ Already logged in as **{me.first_name}**\n"
            "Use /logout to remove."
        )

    await msg.reply(
        "📱 **Userbot Login**\n\n"
        "Apna phone number bhejo (with country code):\n"
        "Example: `+919876543210`\n\n"
        "/cancel — cancel"
    )
    LoginSession._pending[owner_id] = {"step": "phone"}


# ─────────────────────── Message handler for login flow ───────────────────

@Client.on_message(filters.private & ~filters.command([
    "start","login","logout","cancel","forward","auto","rules","settings","status"
]))
async def login_flow_handler(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    pending  = LoginSession._pending.get(owner_id)
    if not pending:
        return   # koi login pending nahi, ignore

    step = pending.get("step")
    text = msg.text.strip() if msg.text else ""

    if text == "/cancel":
        LoginSession._pending.pop(owner_id, None)
        return await msg.reply("❌ Login cancelled.")

    # Step 1 — Phone number
    if step == "phone":
        reply = await LoginSession.start_login(owner_id, text)
        await msg.reply(reply)

    # Step 2 — OTP
    elif step == "otp":
        reply = await LoginSession.submit_otp(owner_id, text)
        await msg.reply(reply)

    # Step 3 — 2FA password (agar laga ho)
    elif step == "2fa":
        reply = await LoginSession.submit_2fa(owner_id, text)
        await msg.reply(reply)


# ─────────────────────────── /logout ──────────────────────────

@Client.on_message(filters.private & filters.command("logout"))
async def cmd_logout(bot: Client, msg: Message):
    result = await logout(msg.from_user.id)
    await msg.reply(result)
