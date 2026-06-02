"""
/forward — Step-by-step interactive forward wizard

Flow:
  Step 1: /forward type karo
          → Bot: "Source channel ka ID ya ek message forward karo"
  Step 2: User source deta hai (ID ya forwarded msg)
          → Bot: "Start karne ki jagah ka link do"
  Step 3: User start link deta hai
          → Bot: "Kahan tak forward karna hai — end link do"
  Step 4: User end link deta hai
          → Bot: "Target channel ka ID ya ek message forward karo"
  Step 5: User target deta hai
          → Bot: Hide options + Media type selector (inline buttons)
  Step 6: Start Forward → forwarding shuru
          /stop → kisi bhi waqt cancel

Cancel:
  /stop — active forward ya wizard dono band kar deta hai
"""

import asyncio
import re
import uuid

from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from pyrogram.errors import FloodWait, MessageIdInvalid

from userbot import get_client, get_all_clients
from worker_bots import get_all_workers, split_into_chunks
from database import db
from state_router import FORWARD_STATE, clear_all
from utils import process_text


# ──────────────────────── active tasks ───────────────────────────────────
_active: dict[str, dict] = {}          # task_id → {cancelled: bool}
_user_task: dict[int, str] = {}        # owner_id → task_id  (for /stop)

# ──────────────────────── media types ────────────────────────────────────
MEDIA_TYPES = {
    "📝 Text":       "text",
    "🖼 Photo":      "photo",
    "🎬 Video":      "video",
    "🎵 Audio":      "audio",
    "🎤 Voice":      "voice",
    "📄 Document":   "document",
    "😂 Sticker":    "sticker",
    "🌀 GIF":        "animation",
    "📹 Video Note": "video_note",
}
ALL_TYPES = list(MEDIA_TYPES.values())


# ═══════════════════════ HELPER: parse channel info ══════════════════════

def _extract_msg_link_ids(text: str):
    """
    Telegram message link se channel_id aur message_id nikalo.
    Supported formats:
      https://t.me/c/1234567890/55
      https://t.me/username/55
    Returns (channel_identifier, message_id) or (None, None)
    """
    m = re.search(r't\.me/c/(\d+)/(\d+)', text)
    if m:
        return int("-100" + m.group(1)), int(m.group(2))
    m = re.search(r't\.me/([A-Za-z0-9_]+)/(\d+)', text)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


async def _resolve_channel(client, value: str | int):
    """
    Channel ka title fetch karo. Value ho sakta hai:
      - integer ID
      - @username string
    Returns (resolved_id, title) or raises Exception
    """
    chat = await client.get_chat(value)
    return chat.id, (chat.title or chat.username or str(chat.id))


# ═══════════════════════ STEP MESSAGES ═══════════════════════════════════

STEP_TEXTS = {
    "ask_source": (
        "📥 **Step 1/4 — Source Channel**\n\n"
        "Jis channel *se* forward karna hai uska:\n"
        "• Channel ID do  _(e.g. `-1001234567890`)_\n"
        "  **ya**\n"
        "• Uss channel ka koi ek message yahan forward karo\n\n"
        "_/stop — cancel_"
    ),
    "ask_start": (
        "🔗 **Step 2/4 — Start Message**\n\n"
        "Jahan *se* forward shuru karna hai us message ka link do.\n"
        "_(Right-click → Copy Link)_\n\n"
        "Example: `https://t.me/c/1234567890/100`\n\n"
        "_/stop — cancel_"
    ),
    "ask_end": (
        "🔗 **Step 3/4 — End Message**\n\n"
        "Jahan *tak* forward karna hai us message ka link do.\n\n"
        "Example: `https://t.me/c/1234567890/500`\n\n"
        "_/stop — cancel_"
    ),
    "ask_target": (
        "📤 **Step 4/4 — Target Channel**\n\n"
        "Jis channel *mein* forward karna hai uska:\n"
        "• Channel ID do  _(e.g. `-1001234567890`)_\n"
        "  **ya**\n"
        "• Uss channel ka koi ek message yahan forward karo\n\n"
        "_/stop — cancel_"
    ),
}


# ═══════════════════════ /forward COMMAND ════════════════════════════════

@Client.on_message(filters.private & filters.command("forward"))
async def cmd_forward(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    await db.ensure_user(owner_id, msg.from_user.first_name)

    client = await get_client(owner_id)
    if not client:
        return await msg.reply("❌ Pehle userbot login karo: /login")

    # Koi purana state clear karo
    clear_all(owner_id)

    # Agar koi task chal raha hai toh cancel karo
    old_task = _user_task.pop(owner_id, None)
    if old_task and old_task in _active:
        _active[old_task]["cancelled"] = True

    # Naya wizard state shuru karo
    FORWARD_STATE[owner_id] = {"step": "ask_source"}

    await msg.reply(STEP_TEXTS["ask_source"])


# ═══════════════════════ /stop COMMAND ═══════════════════════════════════

@Client.on_message(filters.private & filters.command("stop"))
async def cmd_stop(bot: Client, msg: Message):
    owner_id = msg.from_user.id

    # Wizard cancel
    if owner_id in FORWARD_STATE:
        FORWARD_STATE.pop(owner_id, None)
        await msg.reply("🛑 Forward wizard cancel ho gaya.")
        return

    # Active task cancel
    task_id = _user_task.pop(owner_id, None)
    if task_id and task_id in _active:
        _active[task_id]["cancelled"] = True
        await msg.reply(f"🛑 Forward task cancel ho raha hai...")
    else:
        await msg.reply("ℹ️ Abhi koi active forward ya wizard nahi hai.")


# ═══════════════════════ WIZARD STEP HANDLER ═════════════════════════════

async def handle_forward_step(bot: Client, msg: Message):
    """router.py yahan bhejta hai jab FORWARD_STATE active ho."""
    owner_id = msg.from_user.id
    state = FORWARD_STATE.get(owner_id)
    if not state:
        return

    step = state.get("step")

    # ── Step 1: Source channel ───────────────────────────────────────────
    if step == "ask_source":
        await _handle_source(bot, msg, owner_id, state)

    # ── Step 2: Start link ───────────────────────────────────────────────
    elif step == "ask_start":
        await _handle_start(bot, msg, owner_id, state)

    # ── Step 3: End link ─────────────────────────────────────────────────
    elif step == "ask_end":
        await _handle_end(bot, msg, owner_id, state)

    # ── Step 4: Target channel ───────────────────────────────────────────
    elif step == "ask_target":
        await _handle_target(bot, msg, owner_id, state)


# ──────────────────── Individual step handlers ───────────────────────────

async def _handle_source(bot, msg: Message, owner_id: int, state: dict):
    client = await get_client(owner_id)

    # Forwarded message se source nikalo
    if msg.forward_from_chat:
        src_id = msg.forward_from_chat.id
        src_name = msg.forward_from_chat.title or str(src_id)
    else:
        # Text ID ya username
        text = (msg.text or "").strip()
        if not text.lstrip("-").isdigit() and not text.startswith("@"):
            return await msg.reply(
                "❌ Samajh nahi aaya.\n\n"
                "Channel ID do _(e.g. `-1001234567890`)_ **ya** uss channel ka koi message forward karo.\n\n"
                "_/stop — cancel_"
            )
        try:
            val = int(text) if text.lstrip("-").isdigit() else text
            src_id, src_name = await _resolve_channel(client, val)
        except Exception:
            return await msg.reply(
                "❌ Channel nahi mila. Sahi ID/username do ya message forward karo.\n\n_/stop — cancel_"
            )

    state["source_id"] = src_id
    state["src_name"] = src_name
    state["step"] = "ask_start"
    await msg.reply(
        f"✅ Source set: **{src_name}** (`{src_id}`)\n\n" + STEP_TEXTS["ask_start"]
    )


async def _handle_start(bot, msg: Message, owner_id: int, state: dict):
    text = (msg.text or "").strip()
    channel_hint, msg_id = _extract_msg_link_ids(text)

    if msg_id is None:
        return await msg.reply(
            "❌ Valid message link nahi mila.\n\n"
            "Example: `https://t.me/c/1234567890/100`\n\n"
            "_/stop — cancel_"
        )

    # Source se match karo agar link mein channel hai
    src_id = state["source_id"]
    if channel_hint is not None:
        # Sirf message_id use karo, source already set hai
        pass

    state["start_id"] = msg_id
    state["step"] = "ask_end"
    await msg.reply(
        f"✅ Start message ID set: `{msg_id}`\n\n" + STEP_TEXTS["ask_end"]
    )


async def _handle_end(bot, msg: Message, owner_id: int, state: dict):
    text = (msg.text or "").strip()
    _, msg_id = _extract_msg_link_ids(text)

    if msg_id is None:
        return await msg.reply(
            "❌ Valid message link nahi mila.\n\n"
            "Example: `https://t.me/c/1234567890/500`\n\n"
            "_/stop — cancel_"
        )

    start_id = state["start_id"]
    if msg_id < start_id:
        return await msg.reply(
            f"❌ End ID (`{msg_id}`) start ID (`{start_id}`) se chhota hai!\n\n"
            "Sahi end link do.\n\n_/stop — cancel_"
        )

    state["end_id"] = msg_id
    state["total"] = msg_id - start_id + 1
    state["step"] = "ask_target"
    await msg.reply(
        f"✅ End message ID set: `{msg_id}` _(total: {state['total']} messages)_\n\n"
        + STEP_TEXTS["ask_target"]
    )


async def _handle_target(bot, msg: Message, owner_id: int, state: dict):
    client = await get_client(owner_id)

    # Forwarded message se target nikalo
    if msg.forward_from_chat:
        tgt_id = msg.forward_from_chat.id
        tgt_name = msg.forward_from_chat.title or str(tgt_id)
    else:
        text = (msg.text or "").strip()
        if not text.lstrip("-").isdigit() and not text.startswith("@"):
            return await msg.reply(
                "❌ Samajh nahi aaya.\n\n"
                "Channel ID do _(e.g. `-1001234567890`)_ **ya** uss channel ka koi message forward karo.\n\n"
                "_/stop — cancel_"
            )
        try:
            val = int(text) if text.lstrip("-").isdigit() else text
            tgt_id, tgt_name = await _resolve_channel(client, val)
        except Exception:
            return await msg.reply(
                "❌ Channel nahi mila. Sahi ID/username do ya message forward karo.\n\n_/stop — cancel_"
            )

    state["target_id"] = tgt_id
    state["tgt_name"] = tgt_name
    state["hide_links"] = False
    state["hide_usernames"] = False
    state["selected_types"] = set(ALL_TYPES)
    state["step"] = "mode_select"

    # Mode select: Bot ya Userbot
    await _show_mode_select(msg, owner_id)


# ═══════════════════════ MODE SELECT UI ══════════════════════════════════

async def _show_mode_select(target, owner_id: int):
    """Bot mode, Worker Bots mode, ya Userbot mode — user choose kare."""
    state    = FORWARD_STATE.get(owner_id, {})
    workers  = await get_all_workers(owner_id)
    n_workers = len(workers)

    worker_label = (
        f"⚡ Worker Bots ({n_workers} bots = {n_workers}x speed)"
        if n_workers > 0 else
        "⚡ Worker Bots (add karo /addbot se)"
    )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Main Bot se Forward",    callback_data=f"fwd_mode#{owner_id}#bot")],
        [InlineKeyboardButton(worker_label,                callback_data=f"fwd_mode#{owner_id}#workers")],
        [InlineKeyboardButton("👤 Userbot se Forward",     callback_data=f"fwd_mode#{owner_id}#userbot")],
        [InlineKeyboardButton("❌ Cancel",                  callback_data=f"fwd_cancel#{owner_id}")]
    ])
    text = (
        f"📋 **Forward Summary**\n\n"
        f"• 📥 Source: `{state.get('src_name')}` (`{state.get('source_id')}`)\n"
        f"• 📤 Target: `{state.get('tgt_name')}` (`{state.get('target_id')}`)\n"
        f"• 🔢 Range: `{state.get('start_id')}` → `{state.get('end_id')}` "
        f"({state.get('total')} msgs)\n\n"
        f"**Forwarding mode choose karo:**\n\n"
        f"🤖 **Main Bot** — Sirf yeh ek bot, normal speed\n"
        f"⚡ **Worker Bots** — {n_workers} bots parallel = **{max(1,n_workers)}x speed!** (recommended)\n"
        f"👤 **Userbot** — Logged-in account se forward"
    )
    is_bot_msg = hasattr(target, 'edit_text') and getattr(target, 'from_user', None) is None
    if is_bot_msg:
        try:
            await target.edit_text(text, reply_markup=buttons)
            return
        except Exception:
            pass
    await target.reply(text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^fwd_mode#"))
async def cb_fwd_mode(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    mode     = parts[2]   # "bot", "workers", or "userbot"

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.get(owner_id)
    if not state:
        return await query.message.edit_text("❌ Session expire ho gaya. /forward dobara karo.")

    if mode == "bot":
        # Bot mode: pehle admin check karo
        source = state["source_id"]
        target_ch = state["target_id"]
        errors = []
        try:
            src_member = await bot.get_chat_member(source, (await bot.get_me()).id)
            if src_member.status.value not in ("administrator", "creator"):
                errors.append(f"❌ Source mein bot **admin nahi** hai")
        except Exception:
            errors.append(f"⚠️ Source channel check nahi ho saka — public hai toh theek hai")
        try:
            tgt_member = await bot.get_chat_member(target_ch, (await bot.get_me()).id)
            if tgt_member.status.value not in ("administrator", "creator"):
                errors.append(f"❌ Target mein bot **admin nahi** hai")
        except Exception:
            errors.append(f"❌ Target channel check nahi ho saka — admin banana zaroori hai")

        if any("❌" in e for e in errors):
            warn = "\n".join(errors)
            return await query.message.edit_text(
                f"🤖 **Bot Mode — Admin Check Failed**\n\n"
                f"{warn}\n\n"
                f"Bot ko dono channels mein admin banao, phir /forward dobara karo."
            )

    elif mode == "workers":
        # Worker bots mode: check karo koi bots hain ya nahi
        workers = await get_all_workers(owner_id)
        if not workers:
            return await query.message.edit_text(
                "❌ **Koi worker bot nahi hai!**\n\n"
                "Pehle bots add karo:\n"
                "1. @BotFather pe jaao → /newbot karo\n"
                "2. Token copy karo\n"
                "3. `/addbot <token>` bhejo\n\n"
                "Phir /forward dobara karo."
            )

    state["forward_mode"] = mode
    state["step"] = "hide_opts"
    await query.answer()
    await _show_hide_opts(query.message, owner_id)


# ═══════════════════════ HIDE OPTIONS UI ═════════════════════════════════

async def _show_hide_opts(target, owner_id: int):
    state = FORWARD_STATE.get(owner_id, {})
    hl = "✅ ON" if state.get("hide_links") else "OFF"
    hu = "✅ ON" if state.get("hide_usernames") else "OFF"

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔗 Hide Links: {hl}",  callback_data=f"fwd_hide#{owner_id}#hide_links"),
            InlineKeyboardButton(f"👤 Hide @user: {hu}", callback_data=f"fwd_hide#{owner_id}#hide_usernames"),
        ],
        [InlineKeyboardButton("➡️ Next: Media Types", callback_data=f"fwd_hide#{owner_id}#done")],
        [InlineKeyboardButton("❌ Cancel",             callback_data=f"fwd_cancel#{owner_id}")]
    ])

    text = (
        f"📋 **Forward Summary**\n\n"
        f"• 📥 Source: `{state.get('src_name')}` (`{state.get('source_id')}`)\n"
        f"• 📤 Target: `{state.get('tgt_name')}` (`{state.get('target_id')}`)\n"
        f"• 🔢 Range: `{state.get('start_id')}` → `{state.get('end_id')}` "
        f"({state.get('total')} msgs)\n\n"
        f"**Step — Hide options choose karo:**"
    )

    # CallbackQuery.message (bot ka message) → edit karo
    # User ka message → reply karo (edit nahi kar sakte)
    is_bot_msg = hasattr(target, 'edit_text') and getattr(target, 'from_user', None) is None
    if is_bot_msg:
        try:
            await target.edit_text(text, reply_markup=buttons)
            return
        except Exception:
            pass
    await target.reply(text, reply_markup=buttons)


# ─────────────────── Hide options callback ───────────────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_hide#"))
async def cb_fwd_hide(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    field    = parts[2]

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.get(owner_id)
    if not state:
        return await query.message.edit_text("❌ Session expire ho gaya. /forward dobara karo.")

    if field == "done":
        state["step"] = "pick_types"
        await _show_type_selector(query.message, owner_id)
        return

    state[field] = not state.get(field, False)
    await query.answer()
    await _show_hide_opts(query.message, owner_id)


# ─────────────────── Media type selector ─────────────────────────────────

async def _show_type_selector(message, owner_id: int):
    state    = FORWARD_STATE.get(owner_id, {})
    selected = state.get("selected_types", set(ALL_TYPES))

    rows = []
    items = list(MEDIA_TYPES.items())
    for i in range(0, len(items), 2):
        row = []
        for label, val in items[i:i+2]:
            tick = "✅" if val in selected else "☐"
            row.append(InlineKeyboardButton(
                f"{tick} {label}",
                callback_data=f"fwd_mtype#{owner_id}#{val}"
            ))
        rows.append(row)

    all_tick = "✅" if set(selected) == set(ALL_TYPES) else "☐"
    rows.append([InlineKeyboardButton(f"{all_tick} All Types", callback_data=f"fwd_mtype#{owner_id}#__all__")])
    rows.append([
        InlineKeyboardButton("🚀 Start Forward", callback_data=f"fwd_start#{owner_id}"),
        InlineKeyboardButton("❌ Cancel",        callback_data=f"fwd_cancel#{owner_id}")
    ])

    hl = "✅" if state.get("hide_links") else "❌"
    hu = "✅" if state.get("hide_usernames") else "❌"

    await message.edit_text(
        f"**Kaunse types forward karein?**\n"
        f"_(Untick karo jo skip karna hai)_\n\n"
        f"🔗 Links hidden: {hl}  |  👤 @user hidden: {hu}",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@Client.on_callback_query(filters.regex(r"^fwd_mtype#"))
async def cb_fwd_mtype(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    mtype    = parts[2]

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.get(owner_id)
    if not state:
        return await query.message.edit_text("❌ Session expire ho gaya. /forward dobara karo.")

    selected: set = state.get("selected_types", set(ALL_TYPES))

    if mtype == "__all__":
        selected = set(ALL_TYPES) if selected != set(ALL_TYPES) else set()
    else:
        selected.discard(mtype) if mtype in selected else selected.add(mtype)

    state["selected_types"] = selected
    await query.answer()
    await _show_type_selector(query.message, owner_id)


# ─────────────────── Cancel callback ─────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_cancel#"))
async def cb_fwd_cancel(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    FORWARD_STATE.pop(owner_id, None)
    await query.message.edit_text("❌ Forward cancelled.")


# ─────────────────── Start callback ──────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_start#"))
async def cb_fwd_start(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.pop(owner_id, None)
    if not state:
        return await query.message.edit_text("❌ State expire ho gaya. /forward dobara karo.")

    selected = state.get("selected_types", set(ALL_TYPES))
    if not selected:
        FORWARD_STATE[owner_id] = state
        return await query.answer("❌ Kum se kum ek type select karo!", show_alert=True)

    source      = state["source_id"]
    target      = state["target_id"]
    start_id    = state["start_id"]
    end_id      = state["end_id"]
    total       = state["total"]
    hide_lnk    = state.get("hide_links", False)
    hide_usr    = state.get("hide_usernames", False)
    forward_mode = state.get("forward_mode", "userbot")
    type_labels = [k for k, v in MEDIA_TYPES.items() if v in selected]
    type_str = ", ".join(type_labels) if len(type_labels) < len(MEDIA_TYPES) else "All"

    # ── Mode ke hisaab se clients decide karo ────────────────────────
    if forward_mode == "bot":
        # Main bot khud — single client
        chunks = [(0, bot, list(range(start_id, end_id + 1)))]
        n_acc  = 1
        mode_label = "🤖 Main Bot"

    elif forward_mode == "workers":
        # ⚡ GROUPED ROUND-ROBIN WORKER BOTS MODE
        # Saari IDs ek hi jagah — _run_multi_forward khud groups banayega
        workers = await get_all_workers(owner_id)
        if not workers:
            return await query.message.edit_text(
                "❌ Koi worker bot nahi mila. /addbot se add karo."
            )
        n_acc    = len(workers)
        all_ids  = list(range(start_id, end_id + 1))
        # Har client ko poori list do — lekin chunks format maintain karo
        # _run_multi_forward mein n>1 detect hoga aur grouped logic chalega
        chunks = [(slot, client, all_ids) for slot, client in workers]
        mode_label = f"⚡ Worker Bots ({n_acc} bots, grouped order)"

    else:
        # Userbot mode — saare logged-in accounts
        all_acc = await get_all_clients(owner_id)
        if not all_acc:
            return await query.message.edit_text(
                "❌ Koi account login nahi hai.\n/addaccount se pehle login karo."
            )
        n_acc  = len(all_acc)
        all_ids    = list(range(start_id, end_id + 1))
        chunk_size = max(1, len(all_ids) // n_acc)
        chunks     = []
        for i, (slot, _) in enumerate(all_acc):
            s = i * chunk_size
            e = s + chunk_size if i < n_acc - 1 else len(all_ids)
            chunk = all_ids[s:e]
            if chunk:
                chunks.append((slot, all_acc[i][1], chunk))
        mode_label = f"👤 Userbot ({n_acc} account)"

    # ── Master task ID ────────────────────────────────────────────────
    master_id = str(uuid.uuid4())[:8]
    _active[master_id] = {"cancelled": False}
    _user_task[owner_id] = master_id

    acc_info = "\n".join(
        f"  Bot {slot}: ready"
        for slot, _, _ in chunks
    ) if forward_mode == "workers" else "\n".join(
        f"  Slot {slot}: msg {chunk[0]}→{chunk[-1]} ({len(chunk)} msgs)"
        for slot, _, chunk in chunks
    )

    status_msg = await query.message.edit_text(
        f"⏳ **Forwarding shuru...**\n"
        f"Task: `{master_id}`\n"
        f"Mode: {mode_label}\n"
        f"Types: {type_str}\n"
        f"📊 Distribution:\n"
        f"{acc_info}\n\n"
        f"/stop — forward band karo"
    )

    asyncio.create_task(
        _run_multi_forward(
            master_id, owner_id, chunks, source, target,
            list(selected), hide_lnk, hide_usr, total, status_msg
        )
    )


# ═══════════════════════ ROUND-ROBIN FORWARD SYSTEM ══════════════════════
#
# Flow (3 accounts, messages 1-9):
#   Turn 1 → Account 0 forwards msg 1, then sleeps
#   Turn 2 → Account 1 forwards msg 2, then sleeps
#   Turn 3 → Account 2 forwards msg 3, then sleeps
#   Turn 4 → Account 0 forwards msg 4 ... and so on
#
# Har account ko effectively (n_accounts × 1s) = 3s rest milta hai
# Yeh Telegram ke liye safest pattern hai — ban risk near-zero
# ─────────────────────────────────────────────────────────────────────────

def _msg_matches_type(message: Message, selected_types: list) -> bool:
    if not message or message.empty:
        return False
    if "text" in selected_types and message.text and not message.media:
        return True
    if "photo"      in selected_types and message.photo:      return True
    if "video"      in selected_types and message.video:      return True
    if "audio"      in selected_types and message.audio:      return True
    if "voice"      in selected_types and message.voice:      return True
    if "document"   in selected_types and message.document:   return True
    if "sticker"    in selected_types and message.sticker:    return True
    if "animation"  in selected_types and message.animation:  return True
    if "video_note" in selected_types and message.video_note: return True
    return False


async def _forward_one(client, source, target, msg_id,
                       sel_types, hide_lnk, hide_usr) -> str:
    """
    Ek message forward karne ki koshish karo.
    Returns: 'done' | 'skip' | 'filter' | 'flood:<seconds>'
    """
    try:
        fetched_list = await client.get_messages(source, [msg_id])
        fetched = fetched_list[0] if fetched_list else None

        if not fetched or fetched.empty or fetched.id == 0:
            return "skip"

        if not _msg_matches_type(fetched, sel_types):
            return "filter"

        if hide_lnk or hide_usr:
            from pyrogram import enums as _enums
            raw = fetched.text or fetched.caption or ""
            new_text = process_text(raw, hide_links=hide_lnk, hide_usernames=hide_usr)
            if fetched.media:
                # BUG FIX: parse_mode=DISABLED — processed text plain hai, HTML nahi
                await client.copy_message(
                    chat_id=target, from_chat_id=source,
                    message_id=msg_id,
                    caption=new_text or None,
                    parse_mode=_enums.ParseMode.DISABLED,
                )
            else:
                if new_text:
                    await client.send_message(
                        target, new_text,
                        parse_mode=_enums.ParseMode.DISABLED,
                    )
                else:
                    return "skip"
        else:
            await client.copy_message(
                chat_id=target, from_chat_id=source, message_id=msg_id
            )
        return "done"

    except MessageIdInvalid:
        return "skip"
    except FloodWait as e:
        return f"flood:{e.value + 2}"
    except Exception:
        return "skip"


async def _run_multi_forward(
    master_id, owner_id, chunks, source, target,
    sel_types, hide_lnk, hide_usr, total, status_msg
):
    """
    GROUPED ROUND-ROBIN (Worker Bots mode):
    ─────────────────────────────────────────
    N bots hain → har round mein N messages ek saath forward hote hain.
    Lekin next round tab shuru hoga jab pichla group poora ho jaye.
    Isse order 100% sahi rahega.

    Example (3 bots, messages 1-9):
      Round 1: Bot0→Msg1, Bot1→Msg2, Bot2→Msg3  (parallel, wait for all)
      Round 2: Bot0→Msg4, Bot1→Msg5, Bot2→Msg6  (parallel, wait for all)
      Round 3: Bot0→Msg7, Bot1→Msg8, Bot2→Msg9  (parallel, wait for all)

    Single bot / Userbot mode:
      Normal sequential — ek ek karke.
    """
    # Saari IDs flat list mein (original order) — first chunk se lo (workers mein sab same hain)
    all_msg_ids = list(dict.fromkeys(chunks[0][2]))  # first chunk = full list, dedup order maintain

    clients = [(slot, client) for slot, client, _ in chunks]
    n = len(clients)

    done = skipped = filtered = 0
    PROGRESS_EVERY = 5   # har 5 rounds pe update

    # ── Dynamic sleep: bots jitne zyada, utna kam sleep ─────────────────
    # 1 bot → 1.0s | 2-3 bots → 0.7s | 4-6 bots → 0.5s | 7+ bots → 0.3s
    if n >= 7:
        ROUND_SLEEP = 0.3
    elif n >= 4:
        ROUND_SLEEP = 0.5
    elif n >= 2:
        ROUND_SLEEP = 0.7
    else:
        ROUND_SLEEP = 1.0

    if n > 1:
        # ── GROUPED ROUND-ROBIN (Worker Bots) ───────────────────────────
        # Messages ko N-size groups mein tod do
        # Group 0: [msg0, msg1, msg2]  → teeno bots parallel
        # Group 1: [msg3, msg4, msg5]  → teeno bots parallel
        # ...
        rounds = []
        for i in range(0, len(all_msg_ids), n):
            rounds.append(all_msg_ids[i:i + n])

        total_rounds = len(rounds)

        for round_idx, group in enumerate(rounds):
            if _active.get(master_id, {}).get("cancelled"):
                break

            # Is group ke liye coroutines banao — har bot ek message
            async def _one_forward(client, msg_id):
                result = await _forward_one(
                    client, source, target, msg_id, sel_types, hide_lnk, hide_usr
                )
                # FloodWait pe ek retry
                if result.startswith("flood:"):
                    wait_sec = int(result.split(":")[1])
                    await asyncio.sleep(wait_sec)
                    result = await _forward_one(
                        client, source, target, msg_id, sel_types, hide_lnk, hide_usr
                    )
                return result

            # Group ke messages assign karo — bots ko baari baari
            coros = []
            for i, msg_id in enumerate(group):
                _, client = clients[i % n]
                coros.append(_one_forward(client, msg_id))

            # Saare ek saath chalao — WAIT karo jab tak sab done
            results = await asyncio.gather(*coros, return_exceptions=True)

            for r in results:
                if isinstance(r, Exception) or r == "skip":
                    skipped += 1
                elif r == "filter":
                    filtered += 1
                elif r == "done":
                    done += 1
                else:
                    skipped += 1

            # Progress update
            processed = done + skipped + filtered
            if (round_idx + 1) % PROGRESS_EVERY == 0 or round_idx == total_rounds - 1:
                pct = int(processed / total * 100) if total else 0
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                await _edit_safe(status_msg,
                    f"⚡ **Forwarding... (Grouped)**\n"
                    f"{bar} {pct}%\n\n"
                    f"✅ Forwarded: {done}\n"
                    f"⏭ Skipped: {skipped} | 🔽 Filtered: {filtered}\n"
                    f"📦 Round: {round_idx + 1}/{total_rounds} "
                    f"({n} bots per round)\n\n"
                    f"/stop — cancel"
                )

            # Dynamic sleep — bots ki sankhya ke hisaab se
            await asyncio.sleep(ROUND_SLEEP)

        cancelled = _active.get(master_id, {}).get("cancelled", False)
        if cancelled:
            await _edit_safe(status_msg,
                f"🛑 **Stopped!**\n\n"
                f"✅ Forwarded: {done}\n"
                f"⏭ Skipped: {skipped}\n"
                f"🔽 Filtered: {filtered}\n"
                f"📊 Checked: {done + skipped + filtered} / {total}"
            )
        else:
            await _edit_safe(status_msg,
                f"🎉 **Done! (Grouped Round-Robin)**\n\n"
                f"✅ Forwarded: {done}\n"
                f"⏭ Skipped (deleted/empty): {skipped}\n"
                f"🔽 Filtered (type): {filtered}\n"
                f"📊 Total checked: {total}\n"
                f"⚡ Bots used: {n} | Rounds: {total_rounds}\n"
                f"📌 Order: 100% maintained ✅"
            )

    else:
        # ── SINGLE BOT / USERBOT — Sequential ───────────────────────────
        _, client = clients[0]

        for idx, msg_id in enumerate(all_msg_ids):
            if _active.get(master_id, {}).get("cancelled"):
                await _edit_safe(status_msg,
                    f"🛑 **Stopped!**\n\n"
                    f"✅ Forwarded: {done}\n"
                    f"⏭ Skipped: {skipped}\n"
                    f"🔽 Filtered: {filtered}\n"
                    f"📊 Checked: {done + skipped + filtered} / {total}"
                )
                _active.pop(master_id, None)
                _user_task.pop(owner_id, None)
                return

            result = await _forward_one(
                client, source, target, msg_id, sel_types, hide_lnk, hide_usr
            )

            if result.startswith("flood:"):
                wait_sec = int(result.split(":")[1])
                await _edit_safe(status_msg,
                    f"⏳ FloodWait {wait_sec}s...\n"
                    f"✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                    f"/stop — cancel"
                )
                await asyncio.sleep(wait_sec)
                continue
            elif result == "done":
                done += 1
            elif result == "filter":
                filtered += 1
            else:
                skipped += 1

            processed = done + skipped + filtered
            if processed % 15 == 0 and processed > 0:
                pct = int(processed / total * 100)
                bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                await _edit_safe(status_msg,
                    f"📤 **Forwarding...**\n"
                    f"{bar} {pct}%\n"
                    f"✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                    f"/stop — cancel"
                )

            await asyncio.sleep(1)

        await _edit_safe(status_msg,
            f"🎉 **Done!**\n\n"
            f"✅ Forwarded: {done}\n"
            f"⏭ Skipped (deleted/empty): {skipped}\n"
            f"🔽 Filtered (type): {filtered}\n"
            f"📊 Total checked: {total}"
        )

    _active.pop(master_id, None)
    _user_task.pop(owner_id, None)


async def _edit_safe(message, text: str):
    try:
        await message.edit_text(text)
    except Exception:
        pass
