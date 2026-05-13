"""
Account management commands:
  /addaccount  — naya Telegram account add karo (login flow)
  /accounts    — saare logged-in accounts dikhao
  /removeaccount <slot> — ek account remove karo
  /logout      — saare accounts remove karo

/login → backward compat ke liye /addaccount pe redirect karta hai
"""
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from userbot import LoginSession, logout, get_all_clients, get_client
from database import db
from state_router import LOGIN_STATE, clear_all


# ─────────────────────── /login (backward compat) ────────────────────────

@Client.on_message(filters.private & filters.command("login"))
async def cmd_login(bot: Client, msg: Message):
    """Purane /login ko /addaccount pe redirect karo."""
    await _start_add_account(bot, msg)


# ─────────────────────── /addaccount ─────────────────────────────────────

@Client.on_message(filters.private & filters.command("addaccount"))
async def cmd_add_account(bot: Client, msg: Message):
    await _start_add_account(bot, msg)


async def _start_add_account(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    accounts = await db.get_all_userbots(owner_id)

    if len(accounts) >= 5:
        return await msg.reply(
            f"❌ Maximum 5 accounts tak hi add ho sakte hain.\n"
            f"Abhi {len(accounts)} accounts hain.\n\n"
            f"/accounts — list dekho\n"
            f"/removeaccount <slot> — koi hatao"
        )

    clear_all(owner_id)
    LOGIN_STATE[owner_id] = {"step": "phone"}

    count = len(accounts)
    await msg.reply(
        f"📱 **Account Add Karo** (Slot {count})\n\n"
        f"Abhi **{count}** account(s) hain.\n\n"
        f"Naye account ka phone number bhejo (country code ke saath):\n"
        f"Example: `+919876543210`\n\n"
        f"/cancel — cancel"
    )


# ─────────────────────── /accounts ───────────────────────────────────────

@Client.on_message(filters.private & filters.command("accounts"))
async def cmd_accounts(bot: Client, msg: Message):
    owner_id  = msg.from_user.id
    accounts  = await db.get_all_userbots(owner_id)
    clients   = await get_all_clients(owner_id)
    active    = {slot for slot, _ in clients}

    if not accounts:
        return await msg.reply(
            "❌ Koi account login nahi hai.\n\n"
            "/addaccount — account add karo"
        )

    lines = ["👥 **Aapke Logged-in Accounts:**\n"]
    for acc in accounts:
        slot   = acc.get("slot", 0)
        phone  = acc.get("phone", "?")
        status = "🟢 Online" if slot in active else "🔴 Offline"
        lines.append(f"**Slot {slot}** — `{phone}` — {status}")

    lines.append(
        f"\n📊 Total: **{len(accounts)}** account(s)\n\n"
        f"/addaccount — naya add karo\n"
        f"`/removeaccount <slot>` — koi hatao\n"
        f"/logout — saare hatao"
    )

    await msg.reply("\n".join(lines))


# ─────────────────────── /removeaccount ──────────────────────────────────

@Client.on_message(filters.private & filters.command("removeaccount"))
async def cmd_remove_account(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    args     = msg.text.split()

    if len(args) < 2 or not args[1].isdigit():
        accounts = await db.get_all_userbots(owner_id)
        if not accounts:
            return await msg.reply("❌ Koi account nahi hai.")

        # Buttons se choose karo
        buttons = []
        for acc in accounts:
            slot  = acc.get("slot", 0)
            phone = acc.get("phone", "?")
            buttons.append([InlineKeyboardButton(
                f"❌ Slot {slot} — {phone}",
                callback_data=f"removeacc#{owner_id}#{slot}"
            )])
        buttons.append([InlineKeyboardButton("🚫 Cancel", callback_data=f"removeacc#{owner_id}#cancel")])

        return await msg.reply(
            "🗑 **Kaunsa account remove karna hai?**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    slot   = int(args[1])
    result = await logout(owner_id, slot=slot)
    await msg.reply(result)


@Client.on_callback_query(filters.regex(r"^removeacc#"))
async def cb_remove_account(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    slot_str = parts[2]

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    if slot_str == "cancel":
        return await query.message.edit_text("❌ Cancel.")

    result = await logout(owner_id, slot=int(slot_str))
    await query.message.edit_text(result)


# ─────────────────────── /logout ─────────────────────────────────────────

@Client.on_message(filters.private & filters.command("logout"))
async def cmd_logout(bot: Client, msg: Message):
    owner_id  = msg.from_user.id
    accounts  = await db.get_all_userbots(owner_id)
    if not accounts:
        return await msg.reply("❌ Koi account nahi hai.")

    # Confirm button
    await msg.reply(
        f"⚠️ **Saare {len(accounts)} account(s) remove karne hain?**",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Haan, saare hatao", callback_data=f"logoutall#{owner_id}"),
            InlineKeyboardButton("❌ Cancel",            callback_data=f"removeacc#{owner_id}#cancel")
        ]])
    )


@Client.on_callback_query(filters.regex(r"^logoutall#"))
async def cb_logout_all(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)
    result = await logout(owner_id, slot=None)
    await query.message.edit_text(result)


# ─────────────────────── Login step handler ──────────────────────────────

async def handle_login_step(bot: Client, msg: Message):
    """router.py se call hota hai jab LOGIN_STATE active ho."""
    owner_id = msg.from_user.id
    state    = LOGIN_STATE.get(owner_id, {})
    step     = state.get("step")
    text     = (msg.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        LOGIN_STATE.pop(owner_id, None)
        LoginSession._pending.pop(owner_id, None)
        return await msg.reply("❌ Login cancel ho gaya.")

    pending = LoginSession._pending.get(owner_id)
    actual  = pending.get("step") if pending else step

    if actual in ("phone", None) and step == "phone":
        reply = await LoginSession.start_login(owner_id, text)
        await msg.reply(reply)
        if "✅" in reply:
            LOGIN_STATE[owner_id]["step"] = "otp"
        elif "❌" in reply:
            LOGIN_STATE.pop(owner_id, None)

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
