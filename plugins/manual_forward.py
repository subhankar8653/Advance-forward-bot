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

from userbot import get_client
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
    state["step"] = "hide_opts"

    # Summary + Hide options
    await _show_hide_opts(msg, owner_id)


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

    task_id  = str(uuid.uuid4())[:8]
    source   = state["source_id"]
    target   = state["target_id"]
    start_id = state["start_id"]
    end_id   = state["end_id"]
    total    = state["total"]
    hide_lnk = state.get("hide_links", False)
    hide_usr = state.get("hide_usernames", False)

    _active[task_id] = {"cancelled": False}
    _user_task[owner_id] = task_id  # /stop ke liye

    type_labels = [k for k, v in MEDIA_TYPES.items() if v in selected]
    type_str = ", ".join(type_labels) if len(type_labels) < len(MEDIA_TYPES) else "All"

    status_msg = await query.message.edit_text(
        f"⏳ **Forwarding shuru...**\n"
        f"Task ID: `{task_id}`\n"
        f"Types: {type_str}\n"
        f"0 / {total} done\n\n"
        f"/stop — forward band karo"
    )

    client = await get_client(owner_id)
    asyncio.create_task(
        _do_forward(
            client, task_id, owner_id,
            source, target, start_id, end_id, total,
            list(selected), hide_lnk, hide_usr, status_msg
        )
    )


# ═══════════════════════ CORE FORWARD LOOP ═══════════════════════════════

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


async def _do_forward(
    client, task_id, owner_id,
    source, target, start_id, end_id, total,
    sel_types, hide_lnk, hide_usr, status_msg
):
    done = skipped = filtered = 0
    last_errors = []   # debug ke liye last 3 errors track karo
    BATCH = 15

    for msg_id in range(start_id, end_id + 1):
        if _active.get(task_id, {}).get("cancelled"):
            await _edit_safe(status_msg,
                f"🛑 **Forward Stopped!**\n\n"
                f"✅ Forwarded: {done}\n"
                f"⏭ Skipped: {skipped}\n"
                f"🔽 Filtered: {filtered}"
            )
            _active.pop(task_id, None)
            _user_task.pop(owner_id, None)
            return

        try:
            # ✅ FIX 1: list mein dena zaroori hai
            # single int dene pe Pyrogram restricted/private channels mein
            # empty Message object return karta hai — yahi sab skip hone ki wajah thi
            fetched_list = await client.get_messages(source, [msg_id])
            fetched = fetched_list[0] if fetched_list else None

            if not fetched or fetched.empty or fetched.id == 0:
                skipped += 1
                continue

            if not _msg_matches_type(fetched, sel_types):
                filtered += 1
                continue

            if hide_lnk or hide_usr:
                raw = fetched.text or fetched.caption or ""
                new_text = process_text(raw, hide_links=hide_lnk, hide_usernames=hide_usr)
                if fetched.media:
                    await client.copy_message(
                        chat_id=target, from_chat_id=source,
                        message_id=msg_id, caption=new_text or None
                    )
                else:
                    if new_text:
                        await client.send_message(target, new_text)
                    else:
                        skipped += 1
                        continue
            else:
                # ✅ FIX 2: copy_message use karo — forward_messages pe
                # kuch channels "forwards restricted" hote hain, copy_message kaam karta hai
                await client.copy_message(
                    chat_id=target, from_chat_id=source, message_id=msg_id
                )
            done += 1

        except MessageIdInvalid:
            skipped += 1
        except FloodWait as e:
            wait = e.value + 2
            await _edit_safe(status_msg,
                f"⏳ FloodWait {wait}s...\n✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                f"/stop — cancel"
            )
            await asyncio.sleep(wait)
            try:
                await client.copy_message(
                    chat_id=target, from_chat_id=source, message_id=msg_id
                )
                done += 1
            except Exception:
                skipped += 1
        except Exception as e:
            skipped += 1
            # Last 3 errors track karo taaki Done message mein dikh sake
            last_errors = (last_errors + [f"#{msg_id}: {str(e)[:50]}"])[-3:]

        await asyncio.sleep(1)

        processed = done + skipped + filtered
        if processed % BATCH == 0 and processed > 0:
            await asyncio.sleep(3)
            pct = int(processed / total * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await _edit_safe(status_msg,
                f"📤 **Forwarding...**\n"
                f"{bar} {pct}%\n"
                f"✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                f"/stop — cancel"
            )

    err_text = ("\n\n⚠️ Last errors:\n" + "\n".join(last_errors)) if last_errors else ""
    await _edit_safe(status_msg,
        f"🎉 **Done!**\n\n"
        f"✅ Forwarded: {done}\n"
        f"⏭ Skipped (deleted/empty): {skipped}\n"
        f"🔽 Filtered (type): {filtered}\n"
        f"📊 Total checked: {total}"
        f"{err_text}"
    )
    _active.pop(task_id, None)
    _user_task.pop(owner_id, None)


# ─────────────────────────────────────────────────────────────────────────

async def _edit_safe(message, text: str):
    try:
        await message.edit_text(text)
    except Exception:
        pass
