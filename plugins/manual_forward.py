"""
/forward <source_id> <start_id> <end_id> <target_id>

Example:
  /forward -1002341494850 102 1898 -1001973147493

Flow:
  1. Command parse → confirm summary
  2. Hide links / Hide @username toggle
  3. Media type filter (multi-select)
  4. Start forwarding with delay + batch break
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

_active: dict[str, dict] = {}

# Media type label → pyrogram message attribute
MEDIA_TYPES = {
    "📝 Text":      "text",
    "🖼 Photo":     "photo",
    "🎬 Video":     "video",
    "🎵 Audio":     "audio",
    "🎤 Voice":     "voice",
    "📄 Document":  "document",
    "😂 Sticker":   "sticker",
    "🌀 GIF":       "animation",
    "📹 Video Note":"video_note",
}
ALL_TYPES = list(MEDIA_TYPES.values())


# ─────────────────────────── /forward command ────────────────────────────

@Client.on_message(filters.private & filters.command("forward"))
async def cmd_forward(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    await db.ensure_user(owner_id, msg.from_user.first_name)

    client = await get_client(owner_id)
    if not client:
        return await msg.reply("❌ Pehle userbot login karo: /login")

    args = msg.command[1:]  # command ke baad ke words

    # ── Args validate ────────────────────────────────────────
    if len(args) != 4:
        return await msg.reply(
            "❌ **Format galat hai!**\n\n"
            "✅ Sahi format:\n"
            "`/forward <source_id> <start> <end> <target_id>`\n\n"
            "**Example:**\n"
            "`/forward -1002341494850 102 1898 -1001973147493`\n\n"
            "• `source_id` — jahan se forward karna hai\n"
            "• `start` — pehla message number\n"
            "• `end` — aakhri message number\n"
            "• `target_id` — jahan forward karna hai"
        )

    source_raw, start_raw, end_raw, target_raw = args

    # Validate numbers
    for val, name in [(source_raw, "source_id"), (start_raw, "start"),
                      (end_raw, "end"), (target_raw, "target_id")]:
        if not val.lstrip("-").isdigit():
            return await msg.reply(f"❌ `{name}` valid number nahi hai: `{val}`")

    source_id = int(source_raw)
    start_id  = int(start_raw)
    end_id    = int(end_raw)
    target_id = int(target_raw)

    if start_id > end_id:
        return await msg.reply(f"❌ Start (`{start_id}`) end (`{end_id}`) se bada hai!")

    # Channel names fetch karo
    try:
        src_chat = await client.get_chat(source_id)
        src_name = src_chat.title or str(source_id)
    except Exception:
        src_name = str(source_id)

    try:
        tgt_chat = await client.get_chat(target_id)
        tgt_name = tgt_chat.title or str(target_id)
    except Exception:
        tgt_name = str(target_id)

    total = end_id - start_id + 1

    # State save karo
    clear_all(owner_id)
    FORWARD_STATE[owner_id] = {
        "step"           : "hide_opts",
        "source_id"      : source_id,
        "target_id"      : target_id,
        "start_id"       : start_id,
        "end_id"         : end_id,
        "src_name"       : src_name,
        "tgt_name"       : tgt_name,
        "total"          : total,
        "hide_links"     : False,
        "hide_usernames" : False,
        "selected_types" : set(ALL_TYPES),
    }

    # Step 1: Hide options
    await _show_hide_opts(msg, owner_id, src_name, tgt_name, start_id, end_id, total)


async def _show_hide_opts(target, owner_id, src_name, tgt_name, start_id, end_id, total):
    state = FORWARD_STATE.get(owner_id, {})
    hl = "✅ ON" if state.get("hide_links") else "OFF"
    hu = "✅ ON" if state.get("hide_usernames") else "OFF"

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔗 Hide Links: {hl}",   callback_data=f"fwd_hide#{owner_id}#hide_links"),
            InlineKeyboardButton(f"👤 Hide @user: {hu}", callback_data=f"fwd_hide#{owner_id}#hide_usernames"),
        ],
        [InlineKeyboardButton("➡️ Next: Media Types", callback_data=f"fwd_hide#{owner_id}#done")],
        [InlineKeyboardButton("❌ Cancel",             callback_data=f"fwd_cancel#{owner_id}")]
    ])

    text = (
        f"📋 **Forward Summary**\n\n"
        f"• 📥 Source: `{src_name}`\n"
        f"• 📤 Target: `{tgt_name}`\n"
        f"• 🔢 Range: `{start_id}` → `{end_id}` ({total} msgs)\n\n"
        f"**Step 1/2 — Hide options choose karo:**"
    )

    if hasattr(target, 'edit_text'):
        await target.edit_text(text, reply_markup=buttons)
    else:
        await target.reply(text, reply_markup=buttons)


# ─────────────────────── Hide options callback ───────────────────────────

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
        # Step 2: Media type selector
        state["step"] = "pick_types"
        await _show_type_selector(query.message, owner_id)
        return

    # Toggle
    state[field] = not state.get(field, False)
    await query.answer()
    await _show_hide_opts(
        query.message, owner_id,
        state["src_name"], state["tgt_name"],
        state["start_id"], state["end_id"], state["total"]
    )


async def _show_type_selector(message, owner_id):
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
        f"**Step 2/2 — Kaunse types forward karein?**\n"
        f"_(Untick karo jo skip karna hai)_\n\n"
        f"🔗 Links hidden: {hl}  |  👤 @user hidden: {hu}",
        reply_markup=InlineKeyboardMarkup(rows)
    )


# ─────────────────────── Media type callback ─────────────────────────────

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
        if mtype in selected:
            selected.discard(mtype)
        else:
            selected.add(mtype)

    state["selected_types"] = selected
    await query.answer()
    await _show_type_selector(query.message, owner_id)


# ─────────────────────── Cancel callback ─────────────────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_cancel#"))
async def cb_fwd_cancel(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    FORWARD_STATE.pop(owner_id, None)
    await query.message.edit_text("❌ Forward cancelled.")


# ─────────────────────── Start callback ──────────────────────────────────

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

    type_labels = [k for k, v in MEDIA_TYPES.items() if v in selected]
    type_str = ", ".join(type_labels) if len(type_labels) < len(MEDIA_TYPES) else "All"

    status_msg = await query.message.edit_text(
        f"⏳ **Forwarding shuru...**\n"
        f"Task ID: `{task_id}`\n"
        f"Types: {type_str}\n"
        f"0 / {total} done\n\n"
        f"/cancel_{task_id} — stop karo"
    )

    client = await get_client(owner_id)
    asyncio.create_task(
        _do_forward(
            client, task_id, source, target,
            start_id, end_id, total,
            list(selected), hide_lnk, hide_usr, status_msg
        )
    )


# ─────────────────────── Core forward loop ───────────────────────────────

def _msg_matches_type(message: Message, selected_types: list) -> bool:
    if not message or message.empty:
        return False
    if "text" in selected_types and message.text and not message.media:
        return True
    if "photo" in selected_types and message.photo:
        return True
    if "video" in selected_types and message.video:
        return True
    if "audio" in selected_types and message.audio:
        return True
    if "voice" in selected_types and message.voice:
        return True
    if "document" in selected_types and message.document:
        return True
    if "sticker" in selected_types and message.sticker:
        return True
    if "animation" in selected_types and message.animation:
        return True
    if "video_note" in selected_types and message.video_note:
        return True
    return False


async def _do_forward(
    client, task_id, source, target,
    start_id, end_id, total,
    sel_types, hide_lnk, hide_usr, status_msg
):
    done = skipped = filtered = 0
    BATCH = 15   # har 15 messages pe 3s extra break

    for msg_id in range(start_id, end_id + 1):
        if _active.get(task_id, {}).get("cancelled"):
            await _edit_safe(status_msg,
                f"🛑 **Cancelled!**\n"
                f"✅ Forwarded: {done}\n"
                f"⏭ Skipped: {skipped}\n"
                f"🔽 Filtered: {filtered}"
            )
            _active.pop(task_id, None)
            return

        try:
            fetched = await client.get_messages(source, msg_id)

            if not fetched or fetched.empty:
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
                await client.forward_messages(
                    chat_id=target, from_chat_id=source, message_ids=msg_id
                )
            done += 1

        except MessageIdInvalid:
            skipped += 1
        except FloodWait as e:
            wait = e.value + 2
            await _edit_safe(status_msg,
                f"⏳ FloodWait {wait}s...\n✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                f"/cancel_{task_id} — stop"
            )
            await asyncio.sleep(wait)
            try:
                await client.forward_messages(target, source, msg_id)
                done += 1
            except Exception:
                skipped += 1
        except Exception:
            skipped += 1

        await asyncio.sleep(1)  # 1s delay per message

        processed = done + skipped + filtered
        if processed % BATCH == 0 and processed > 0:
            await asyncio.sleep(3)  # batch break
            pct = int(processed / total * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await _edit_safe(status_msg,
                f"📤 **Forwarding...**\n"
                f"{bar} {pct}%\n"
                f"✅ {done} | ⏭ {skipped} | 🔽 {filtered}\n\n"
                f"/cancel_{task_id} — stop"
            )

    await _edit_safe(status_msg,
        f"🎉 **Done!**\n\n"
        f"✅ Forwarded: {done}\n"
        f"⏭ Skipped (deleted/empty): {skipped}\n"
        f"🔽 Filtered (type): {filtered}\n"
        f"📊 Total checked: {total}"
    )
    _active.pop(task_id, None)


# ─────────────────────── Cancel task command ─────────────────────────────

@Client.on_message(filters.private & filters.regex(r"^/cancel_([a-f0-9]+)$"))
async def cmd_cancel_task(bot: Client, msg: Message):
    task_id = msg.text.split("_")[1]
    if task_id in _active:
        _active[task_id]["cancelled"] = True
        await msg.reply(f"🛑 Task `{task_id}` cancel ho raha hai...")
    else:
        await msg.reply("❌ Aisa koi task nahi mila.")


async def _edit_safe(message, text: str):
    try:
        await message.edit_text(text)
    except Exception:
        pass


# ── Ye router.py ke liye hai (agar koi FORWARD_STATE step active ho) ─────
async def handle_forward_step(bot: Client, msg: Message):
    """Abhi koi text step nahi hai — sab inline buttons se hota hai."""
    pass
