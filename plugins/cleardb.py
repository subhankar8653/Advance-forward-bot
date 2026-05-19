"""
/cleardb   — OWNER ONLY: Pura database wipe karo (rules, userbots, workers, tasks)
/clearrules — OWNER ONLY: Sirf auto-forward rules delete karo (listeners bhi reset)
"""
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import Config
from database import db


def _is_owner(user_id: int) -> bool:
    return user_id == Config.OWNER_ID


# ─────────────────────── /clearrules ─────────────────────────────────────

@Client.on_message(filters.private & filters.command("clearrules"))
async def cmd_clearrules(bot: Client, msg: Message):
    if not _is_owner(msg.from_user.id):
        return await msg.reply("❌ Sirf owner use kar sakta hai.")

    count = len(await db.get_all_rules())
    if count == 0:
        return await msg.reply("📭 Koi rule nahi hai DB mein.")

    await msg.reply(
        f"⚠️ **{count} auto-forward rule(s) delete honge!**\n\n"
        "In-memory listeners bhi reset ho jaayenge.\n\n"
        "Confirm karo?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Haan, Delete Karo", callback_data="confirm_clearrules"),
            InlineKeyboardButton("❌ Cancel",            callback_data="cancel_clear"),
        ]])
    )


@Client.on_callback_query(filters.regex("^confirm_clearrules$"))
async def cb_clearrules(bot: Client, query: CallbackQuery):
    if not _is_owner(query.from_user.id):
        return await query.answer("❌ Sirf owner.", show_alert=True)

    # DB se saare rules delete karo
    await db.auto_rules.delete_many({})

    # In-memory cache reset karo
    from auto_listener import _rule_map, _source_map, _handlers
    _rule_map.clear()
    _source_map.clear()
    _handlers.clear()

    await query.message.edit_text(
        "✅ **Saare auto-forward rules delete ho gaye!**\n\n"
        "In-memory listeners bhi reset ho gaye.\n\n"
        "Nayi rules banao: /auto"
    )


# ─────────────────────── /cleardb ────────────────────────────────────────

@Client.on_message(filters.private & filters.command("cleardb"))
async def cmd_cleardb(bot: Client, msg: Message):
    if not _is_owner(msg.from_user.id):
        return await msg.reply("❌ Sirf owner use kar sakta hai.")

    rules   = len(await db.get_all_rules())
    users_c = await db.users.count_documents({})
    ub_c    = await db.userbots.count_documents({})
    wb_c    = await db.worker_bots.count_documents({})

    await msg.reply(
        f"⚠️ **PURA DATABASE DELETE HOGA!**\n\n"
        f"• Auto-rules: {rules}\n"
        f"• Users: {users_c}\n"
        f"• Userbots: {ub_c}\n"
        f"• Worker bots: {wb_c}\n\n"
        "**Yeh action undo nahi hoga!**\n"
        "Confirm karo?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💣 Haan, Sab Delete Karo", callback_data="confirm_cleardb"),
            InlineKeyboardButton("❌ Cancel",                 callback_data="cancel_clear"),
        ]])
    )


@Client.on_callback_query(filters.regex("^confirm_cleardb$"))
async def cb_cleardb(bot: Client, query: CallbackQuery):
    if not _is_owner(query.from_user.id):
        return await query.answer("❌ Sirf owner.", show_alert=True)

    # Saab collections wipe
    await db.auto_rules.delete_many({})
    await db.userbots.delete_many({})
    await db.worker_bots.delete_many({})
    await db.tasks.delete_many({})
    # users table rehne do (bas reset)

    # In-memory listeners bhi clear karo
    from auto_listener import _rule_map, _source_map, _handlers
    _rule_map.clear()
    _source_map.clear()
    _handlers.clear()

    await query.message.edit_text(
        "💣 **Pura database wipe ho gaya!**\n\n"
        "• Auto rules: ✅ Deleted\n"
        "• Userbots: ✅ Deleted\n"
        "• Worker bots: ✅ Deleted\n"
        "• In-memory cache: ✅ Reset\n\n"
        "Ab fresh start karo:\n"
        "1. /login — userbot login\n"
        "2. /addbot — worker bots add karo\n"
        "3. /auto — nayi rules banao"
    )


# ─────────────────────── Cancel ──────────────────────────────────────────

@Client.on_callback_query(filters.regex("^cancel_clear$"))
async def cb_cancel_clear(bot: Client, query: CallbackQuery):
    await query.message.edit_text("❌ Cancel. Kuch delete nahi hua.")
