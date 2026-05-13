"""
Worker Bot Manager Commands:

/addbot <token>      — Naya worker bot add karo
/bots                — Saare worker bots list karo
/removebot <slot>    — Ek bot remove karo
/removebots          — Saare bots remove karo
"""
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from worker_bots import add_worker, remove_worker, remove_all_workers, get_worker_count
from database import db


# ─────────────────────── /addbot ─────────────────────────────────────────

@Client.on_message(filters.private & filters.command("addbot"))
async def cmd_add_bot(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    await db.ensure_user(owner_id, msg.from_user.first_name)

    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        return await msg.reply(
            "❌ **Token nahi diya!**\n\n"
            "Usage: `/addbot 123456789:AAFxxx...`\n\n"
            "📌 @BotFather se naya bot banao:\n"
            "1. @BotFather pe jaao\n"
            "2. /newbot karo\n"
            "3. Token copy karo\n"
            "4. `/addbot <token>` yahan bhejo"
        )

    token = args[1].strip()
    wait_msg = await msg.reply("⏳ Bot verify ho raha hai...")
    result = await add_worker(owner_id, token)
    await wait_msg.edit_text(result)


# ─────────────────────── /bots ───────────────────────────────────────────

@Client.on_message(filters.private & filters.command("bots"))
async def cmd_list_bots(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    workers  = await db.get_all_workers(owner_id)

    if not workers:
        return await msg.reply(
            "❌ Koi worker bot nahi hai.\n\n"
            "Bot add karo: `/addbot <token>`\n\n"
            "💡 **Jitne zyada bots, utni zyada speed!**\n"
            "3 bots = 3x speed\n"
            "10 bots = 10x speed"
        )

    lines = ["🤖 **Aapke Worker Bots:**\n"]
    for w in workers:
        slot     = w.get("slot", 0)
        name     = w.get("name", "Unknown")
        username = w.get("username", "")
        lines.append(f"**Slot {slot}** — {name} (@{username})")

    lines.append(
        f"\n📊 Total: **{len(workers)}** bot(s)\n\n"
        f"⚡ Forward speed: **~{len(workers)}x faster** (parallel)\n\n"
        f"`/addbot <token>` — naya add karo\n"
        f"`/removebot <slot>` — koi hatao"
    )

    await msg.reply("\n".join(lines))


# ─────────────────────── /removebot ──────────────────────────────────────

@Client.on_message(filters.private & filters.command("removebot"))
async def cmd_remove_bot(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    args     = msg.text.split()
    workers  = await db.get_all_workers(owner_id)

    if not workers:
        return await msg.reply("❌ Koi worker bot nahi hai.")

    if len(args) < 2 or not args[1].isdigit():
        # Buttons se choose karo
        buttons = []
        for w in workers:
            slot     = w.get("slot", 0)
            username = w.get("username", "?")
            buttons.append([InlineKeyboardButton(
                f"❌ Slot {slot} — @{username}",
                callback_data=f"rmbot#{owner_id}#{slot}"
            )])
        buttons.append([InlineKeyboardButton("🚫 Cancel", callback_data=f"rmbot#{owner_id}#cancel")])
        return await msg.reply(
            "🗑 **Kaunsa bot remove karna hai?**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    slot   = int(args[1])
    result = await remove_worker(owner_id, slot)
    await msg.reply(result)


@Client.on_callback_query(filters.regex(r"^rmbot#"))
async def cb_remove_bot(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    slot_str = parts[2]

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    if slot_str == "cancel":
        return await query.message.edit_text("❌ Cancel.")

    result = await remove_worker(owner_id, int(slot_str))
    await query.message.edit_text(result)


# ─────────────────────── /removebots ─────────────────────────────────────

@Client.on_message(filters.private & filters.command("removebots"))
async def cmd_remove_all_bots(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    workers  = await db.get_all_workers(owner_id)

    if not workers:
        return await msg.reply("❌ Koi worker bot nahi hai.")

    await msg.reply(
        f"⚠️ **Saare {len(workers)} worker bot(s) remove karne hain?**",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Haan, saare hatao", callback_data=f"rmbots#{owner_id}"),
            InlineKeyboardButton("❌ Cancel",            callback_data=f"rmbot#{owner_id}#cancel")
        ]])
    )


@Client.on_callback_query(filters.regex(r"^rmbots#"))
async def cb_remove_all_bots(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)
    result = await remove_all_workers(owner_id)
    await query.message.edit_text(result)
