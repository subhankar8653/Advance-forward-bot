"""
/login  → phone number maango
/logout → session hata do
NOTE: Generic messages → plugins/router.py handle karta hai
"""
from pyrogram import Client, filters
from pyrogram.types import Message
from userbot import LoginSession, logout, get_client
from state_router import LOGIN_STATE, clear_all


@Client.on_message(filters.private & filters.command("login"))
async def cmd_login(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    existing = await get_client(owner_id)
    if existing:
        me = await existing.get_me()
        return await msg.reply(
            f"✅ Already logged in as **{me.first_name}**\n"
            "Use /logout to remove."
        )
    clear_all(owner_id)
    LOGIN_STATE[owner_id] = {"step": "phone"}
    await msg.reply(
        "📱 **Userbot Login**\n\n"
        "Apna phone number bhejo (with country code):\n"
        "Example: `+919876543210`\n\n"
        "/cancel — cancel"
    )


@Client.on_message(filters.private & filters.command("logout"))
async def cmd_logout(bot: Client, msg: Message):
    result = await logout(msg.from_user.id)
    await msg.reply(result)


async def handle_login_step(bot: Client, msg: Message):
    """router.py se call hota hai"""
    owner_id = msg.from_user.id
    state    = LOGIN_STATE.get(owner_id, {})
    step     = state.get("step")
    text     = (msg.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        LOGIN_STATE.pop(owner_id, None)
        LoginSession._pending.pop(owner_id, None)
        return await msg.reply("❌ Login cancelled.")

    pending = LoginSession._pending.get(owner_id)
    actual  = pending.get("step") if pending else step

    if actual in ("phone", None) and step == "phone":
        reply = await LoginSession.start_login(owner_id, text)
        await msg.reply(reply)
        if "✅" in reply:
            LOGIN_STATE[owner_id]["step"] = "otp"
    elif actual == "otp":
        reply = await LoginSession.submit_otp(owner_id, text)
        await msg.reply(reply)
        if "✅" in reply:
            LOGIN_STATE.pop(owner_id, None)
        elif "2FA" in reply or "password" in reply.lower():
            LOGIN_STATE[owner_id]["step"] = "2fa"
    elif actual == "2fa":
        reply = await LoginSession.submit_2fa(owner_id, text)
        await msg.reply(reply)
        if "✅" in reply:
            LOGIN_STATE.pop(owner_id, None)
