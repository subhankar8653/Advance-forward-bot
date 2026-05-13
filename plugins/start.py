from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from database import db
from userbot import get_client
from worker_bots import get_worker_count

HELP_TEXT = """
🤖 **Forward Bot — Commands**

**Userbot Login**
/login — Phone se login karo
/addaccount — Naya account add karo
/accounts — Saare accounts dekho
/logout — Session hatao

**⚡ Worker Bots (Parallel Speed)**
/addbot <token> — Naya worker bot add karo
/bots — Saare worker bots list karo
/removebot <slot> — Ek bot remove karo
/removebots — Saare bots remove karo

**Manual Forward**
/forward — Bulk forward (source → target)

**Auto Forward**
/auto — Naya auto-forward rule banao
/rules — Saare rules dekho / delete karo

**Other**
/status — Bot stats
/help — Yeh message
"""


@Client.on_message(filters.private & filters.command("start"))
async def cmd_start(bot: Client, msg: Message):
    await db.ensure_user(msg.from_user.id, msg.from_user.first_name)
    buttons = InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 Help", callback_data="help"),
        InlineKeyboardButton("📊 Status", callback_data="status")
    ]])
    await msg.reply(
        f"👋 Hello **{msg.from_user.first_name}**!\n\n"
        "Main ek powerful **Forward Bot** hoon.\n\n"
        "⚡ **Multiple bots se parallel forwarding** support karta hoon!\n\n"
        "Shuru karne ke liye /login karo.",
        reply_markup=buttons
    )


@Client.on_message(filters.private & filters.command("help"))
async def cmd_help(bot: Client, msg: Message):
    await msg.reply(HELP_TEXT)


@Client.on_callback_query(filters.regex("^help$"))
async def cb_help(bot: Client, query):
    await query.message.edit_text(HELP_TEXT)


@Client.on_message(filters.private & filters.command("status"))
async def cmd_status(bot: Client, msg: Message):
    owner_id    = msg.from_user.id
    rules       = await db.get_auto_rules(owner_id)
    client      = await get_client(owner_id)
    n_workers   = await get_worker_count(owner_id)

    ub_text = "❌ Not logged in"
    if client:
        try:
            me = await client.get_me()
            ub_text = f"✅ {me.first_name} (`{me.phone_number}`)"
        except Exception:
            ub_text = "⚠️ Session issue"

    speed_text = f"⚡ ~{max(1, n_workers)}x" if n_workers > 0 else "Normal (add bots for speed)"

    await msg.reply(
        f"📊 **Your Stats**\n\n"
        f"• Userbot: {ub_text}\n"
        f"• Worker Bots: **{n_workers}** bot(s)\n"
        f"• Forward Speed: {speed_text}\n"
        f"• Auto rules: {len(rules)}\n\n"
        f"💡 `/addbot <token>` se speed badhao!"
    )


@Client.on_callback_query(filters.regex("^status$"))
async def cb_status(bot: Client, query):
    owner_id    = query.from_user.id
    rules       = await db.get_auto_rules(owner_id)
    client      = await get_client(owner_id)
    n_workers   = await get_worker_count(owner_id)

    ub_text = "❌ Not logged in"
    if client:
        try:
            me = await client.get_me()
            ub_text = f"✅ {me.first_name}"
        except Exception:
            ub_text = "⚠️ Session issue"

    await query.message.edit_text(
        f"📊 **Your Stats**\n\n"
        f"• Userbot: {ub_text}\n"
        f"• Worker Bots: **{n_workers}** bot(s)\n"
        f"• Forward Speed: ~{max(1, n_workers)}x\n"
        f"• Auto rules: {len(rules)}\n"
    )
