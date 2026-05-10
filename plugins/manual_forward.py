"""
/forward  →  Manual bulk forward
- Range by link (e.g. https://t.me/c/xxx/1 to https://t.me/c/xxx/1394)
- Hide links / usernames option
- Delay/break setting (flood protection)
- Media type filter (text, video, photo, voice, document, audio, sticker, animation)
- Username/link remove before forwarding
NOTE: Generic messages → plugins/router.py handle karta hai
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

# Media type → pyrogram attribute
MEDIA_TYPES = {
    "📝 Text":      "text",
    "🖼 Photo":     "photo",
    "🎬 Video":     "video",
    "🎵 Audio":     "audio",
    "🎤 Voice":     "voice",
    "📄 Document":  "document",
    "😂 Sticker":   "sticker",
    "🌀 GIF":       "animation",
}
ALL_TYPES = list(MEDIA_TYPES.values())


def _parse_tg_link(text: str):
    """t.me link se chat_id aur message_id nikalo."""
    # https://t.me/c/1234567890/99  → private channel
    m = re.search(r't\.me/c/(\d+)/(\d+)', text)
    if m:
        chat_id = int("-100" + m.group(1))
        msg_id  = int(m.group(2))
        return chat_id, msg_id
    # https://t.me/username/99  → public channel
    m = re.search(r't\.me/([A-Za-z0-9_]+)/(\d+)', text)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


@Client.on_message(filters.private & filters.command("forward"))
async def cmd_forward(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    await db.ensure_user(owner_id, msg.from_user.first_name)
    client = await get_client(owner_id)
    if not client:
        return await msg.reply("❌ Pehle userbot login karo: /login")

    rules = await db.get_auto_rules(owner_id)
    all_targets = set()
    for r in rules:
        for t in r.get("targets", []):
            all_targets.add(t)

    if not all_targets:
        return await msg.reply(
            "❌ Koi target channel set nahi hai.\n"
            "Pehle /auto se ek rule banao jisme target channel ho."
        )

    buttons = []
    for chat_id in all_targets:
        try:
            chat  = await client.get_chat(chat_id)
            label = chat.title or str(chat_id)
        except Exception:
            label = str(chat_id)
        buttons.append([InlineKeyboardButton(label, callback_data=f"fwd_target#{owner_id}#{chat_id}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"fwd_cancel#{owner_id}")])

    clear_all(owner_id)
    FORWARD_STATE[owner_id] = {"step": "pick_target"}
    await msg.reply(
        "📤 **Manual Forward**\n\nKis channel mein forward karna hai?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@Client.on_callback_query(filters.regex(r"^fwd_target#"))
async def cb_fwd_target(bot: Client, query: CallbackQuery):
    _, owner_id_str, chat_id_str = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)
    FORWARD_STATE[owner_id]["target_id"] = int(chat_id_str)
    FORWARD_STATE[owner_id]["step"]      = "source_link"
    await query.message.edit_text(
        "✅ Target set!\n\n"
        "**Start link** bhejo — source channel ka pehla message:\n"
        "`https://t.me/c/3064179808/1`\n\n"
        "/cancel — cancel"
    )


@Client.on_callback_query(filters.regex(r"^fwd_cancel#"))
async def cb_fwd_cancel(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    FORWARD_STATE.pop(owner_id, None)
    await query.message.edit_text("❌ Cancelled.")


@Client.on_callback_query(filters.regex(r"^fwd_mtype#"))
async def cb_fwd_mtype(bot: Client, query: CallbackQuery):
    parts     = query.data.split("#")
    owner_id  = int(parts[1])
    mtype     = parts[2]   # media type string or "all" or "done"
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.get(owner_id, {})
    selected: set = state.get("selected_types", set(ALL_TYPES))

    if mtype == "done":
        if not selected:
            return await query.answer("Kuch toh select karo!", show_alert=True)
        state["selected_types"] = list(selected)
        state["step"] = "hide_opts"
        # Show hide options
        hl = "ON ✅" if state.get("hide_links") else "OFF"
        hu = "ON ✅" if state.get("hide_usernames") else "OFF"
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"🔗 Links: {hl}", callback_data=f"fwd_hide#{owner_id}#hide_links"),
                InlineKeyboardButton(f"👤 @user: {hu}", callback_data=f"fwd_hide#{owner_id}#hide_usernames"),
            ],
            [InlineKeyboardButton("✅ Next → Delay", callback_data=f"fwd_hide#{owner_id}#done")]
        ])
        await query.message.edit_text("**Hide Options** choose karo:", reply_markup=buttons)
        return

    if mtype == "all":
        if selected == set(ALL_TYPES):
            selected.clear()
        else:
            selected = set(ALL_TYPES)
    else:
        if mtype in selected:
            selected.discard(mtype)
        else:
            selected.add(mtype)

    state["selected_types"] = selected
    await query.answer()
    await _render_mtype_buttons(query.message, owner_id, selected)


async def _render_mtype_buttons(message, owner_id, selected):
    rows = []
    items = list(MEDIA_TYPES.items())
    for i in range(0, len(items), 2):
        row = []
        for label, val in items[i:i+2]:
            tick = "✅" if val in selected else "☐"
            row.append(InlineKeyboardButton(f"{tick} {label}", callback_data=f"fwd_mtype#{owner_id}#{val}"))
        rows.append(row)
    all_tick = "✅" if set(selected) == set(ALL_TYPES) else "☐"
    rows.append([InlineKeyboardButton(f"{all_tick} All Types", callback_data=f"fwd_mtype#{owner_id}#all")])
    rows.append([InlineKeyboardButton("➡️ Done", callback_data=f"fwd_mtype#{owner_id}#done")])
    await message.edit_reply_markup(InlineKeyboardMarkup(rows))


@Client.on_callback_query(filters.regex(r"^fwd_hide#"))
async def cb_fwd_hide(bot: Client, query: CallbackQuery):
    parts    = query.data.split("#")
    owner_id = int(parts[1])
    field    = parts[2]
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = FORWARD_STATE.get(owner_id, {})
    if field == "done":
        state["step"] = "delay"
        await query.message.edit_text(
            "⏱ **Delay** karo messages ke beech:\n\n"
            "Format: `1.5` (seconds)\n"
            "Recommended: `1` to `3`\n\n"
            "Default use karna hai toh `1` bhejo."
        )
        return

    state[field] = not state.get(field, False)
    hl = "ON ✅" if state.get("hide_links") else "OFF"
    hu = "ON ✅" if state.get("hide_usernames") else "OFF"
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔗 Links: {hl}", callback_data=f"fwd_hide#{owner_id}#hide_links"),
            InlineKeyboardButton(f"👤 @user: {hu}", callback_data=f"fwd_hide#{owner_id}#hide_usernames"),
        ],
        [InlineKeyboardButton("✅ Next → Delay", callback_data=f"fwd_hide#{owner_id}#done")]
    ])
    await query.message.edit_reply_markup(buttons)
    await query.answer()


async def handle_forward_step(bot: Client, msg: Message):
    """router.py se call hota hai"""
    owner_id = msg.from_user.id
    state    = FORWARD_STATE.get(owner_id)
    if not state:
        return
    step = state.get("step")
    text = (msg.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        FORWARD_STATE.pop(owner_id, None)
        return await msg.reply("❌ Forward cancelled.")

    # ── Step: source start link ──────────────────────────────
    if step == "source_link":
        chat_id, msg_id = _parse_tg_link(text)
        if not chat_id or not msg_id:
            return await msg.reply(
                "❌ Link samajh nahi aaya.\n"
                "Format: `https://t.me/c/3064179808/1`\n"
                "ya `https://t.me/channelname/1`"
            )
        # Resolve username to id if needed
        if isinstance(chat_id, str):
            try:
                client = await get_client(owner_id)
                chat   = await client.get_chat(chat_id)
                chat_id = chat.id
            except Exception:
                return await msg.reply("❌ Channel nahi mila. Check karo link.")
        state["source_id"] = chat_id
        state["start_id"]  = msg_id
        state["step"]      = "end_link"
        await msg.reply(
            "✅ Start set!\n\n"
            "Ab **end link** bhejo — aakhri message tak:\n"
            "`https://t.me/c/3064179808/1394`"
        )

    # ── Step: end link ───────────────────────────────────────
    elif step == "end_link":
        chat_id, msg_id = _parse_tg_link(text)
        if not msg_id:
            # Maybe they typed just a number
            if text.isdigit():
                msg_id = int(text)
            else:
                return await msg.reply("❌ End link/ID samajh nahi aaya.")
        state["end_id"] = msg_id
        state["step"]   = "pick_types"
        state["selected_types"] = set(ALL_TYPES)  # default: all

        rows = []
        items = list(MEDIA_TYPES.items())
        for i in range(0, len(items), 2):
            row = []
            for label, val in items[i:i+2]:
                row.append(InlineKeyboardButton(f"✅ {label}", callback_data=f"fwd_mtype#{owner_id}#{val}"))
            rows.append(row)
        rows.append([InlineKeyboardButton("✅ All Types", callback_data=f"fwd_mtype#{owner_id}#all")])
        rows.append([InlineKeyboardButton("➡️ Done", callback_data=f"fwd_mtype#{owner_id}#done")])

        await msg.reply(
            "📂 **Kaunse message types forward karne hain?**\n"
            "(Sab select hain by default, untick karo jo nahi chahiye)",
            reply_markup=InlineKeyboardMarkup(rows)
        )

    # ── Step: delay input ────────────────────────────────────
    elif step == "delay":
        try:
            delay = float(text)
            if delay < 0.5:
                delay = 0.5
        except ValueError:
            return await msg.reply("❌ Number bhejo (e.g. `1.5`)")

        state["delay"] = delay
        state["step"]  = "confirm"

        total = state["end_id"] - state["start_id"] + 1
        try:
            client   = await get_client(owner_id)
            src      = await client.get_chat(state["source_id"])
            tgt      = await client.get_chat(state["target_id"])
            src_name = src.title or str(state["source_id"])
            tgt_name = tgt.title or str(state["target_id"])
        except Exception:
            src_name = str(state["source_id"])
            tgt_name = str(state["target_id"])

        sel_types = state.get("selected_types", ALL_TYPES)
        type_labels = [k for k, v in MEDIA_TYPES.items() if v in sel_types]

        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Start", callback_data=f"fwd_start#{owner_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"fwd_cancel#{owner_id}")
        ]])
        await msg.reply(
            f"📋 **Confirm Forward**\n\n"
            f"• Source: `{src_name}`\n"
            f"• Target: `{tgt_name}`\n"
            f"• Range: {state['start_id']} → {state['end_id']} ({total} msgs)\n"
            f"• Types: {', '.join(type_labels) if len(type_labels) < 8 else 'All'}\n"
            f"• Hide links: {state.get('hide_links', False)}\n"
            f"• Hide @users: {state.get('hide_usernames', False)}\n"
            f"• Delay: {delay}s per message\n\n"
            "Start karein?",
            reply_markup=buttons
        )


@Client.on_callback_query(filters.regex(r"^fwd_start#"))
async def cb_fwd_start(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)
    state = FORWARD_STATE.pop(owner_id, None)
    if not state:
        return await query.message.edit_text("❌ State expire ho gaya. /forward dobara karo.")

    task_id  = str(uuid.uuid4())[:8]
    source   = state["source_id"]
    target   = state["target_id"]
    start_id = state["start_id"]
    end_id   = state["end_id"]
    total    = end_id - start_id + 1
    delay    = state.get("delay", 1.0)
    sel      = state.get("selected_types", ALL_TYPES)
    hide_lnk = state.get("hide_links", False)
    hide_usr = state.get("hide_usernames", False)

    _active[task_id] = {"cancelled": False}

    status_msg = await query.message.edit_text(
        f"⏳ Forwarding shuru...\nTask ID: `{task_id}`\n0 / {total} done\n\n"
        f"/cancel_{task_id} — stop"
    )
    client = await get_client(owner_id)
    asyncio.create_task(
        _do_forward(client, owner_id, task_id, source, target,
                    start_id, end_id, total, delay, sel,
                    hide_lnk, hide_usr, status_msg)
    )


def _msg_matches_type(message: Message, selected_types: list) -> bool:
    """Check karo ki message ka type selected hai ya nahi."""
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
    return False


async def _do_forward(client, owner_id, task_id, source, target,
                      start_id, end_id, total, delay, sel_types,
                      hide_lnk, hide_usr, status_msg):
    done = skipped = filtered = 0
    BATCH = 10  # har 10 messages ke baad thoda extra break

    for msg_id in range(start_id, end_id + 1):
        if _active.get(task_id, {}).get("cancelled"):
            await _edit_safe(status_msg,
                f"🛑 Cancelled!\n✅ Done: {done} | ⏭ Skipped: {skipped} | 🔽 Filtered: {filtered}")
            _active.pop(task_id, None)
            return

        try:
            # Message fetch karo to check type
            fetched = await client.get_messages(source, msg_id)
            if fetched.empty:
                skipped += 1
                continue

            # Type filter
            if not _msg_matches_type(fetched, sel_types):
                filtered += 1
                continue

            # Hide links/usernames processing
            if hide_lnk or hide_usr:
                raw = fetched.text or fetched.caption or ""
                new_text = process_text(raw, hide_links=hide_lnk, hide_usernames=hide_usr)
                if fetched.media:
                    await client.copy_message(
                        chat_id=target, from_chat_id=source,
                        message_id=msg_id, caption=new_text or None
                    )
                else:
                    await client.send_message(target, new_text or ".")
            else:
                await client.forward_messages(
                    chat_id=target, from_chat_id=source, message_ids=msg_id
                )
            done += 1

        except MessageIdInvalid:
            skipped += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 2)
            try:
                await client.forward_messages(target, source, msg_id)
                done += 1
            except Exception:
                skipped += 1
        except Exception:
            skipped += 1

        await asyncio.sleep(delay)

        # Har BATCH messages ke baad extra 3s break (flood protection)
        if (done + skipped + filtered) % BATCH == 0:
            await asyncio.sleep(3)
            pct = int((done + skipped + filtered) / total * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await _edit_safe(status_msg,
                f"📤 Forwarding...\n{bar} {pct}%\n"
                f"✅ {done} | ⏭ {skipped} | 🔽 {filtered} filtered\n\n"
                f"/cancel_{task_id} — stop")

    await _edit_safe(status_msg,
        f"🎉 **Done!**\n"
        f"✅ Forwarded: {done}\n"
        f"⏭ Skipped: {skipped}\n"
        f"🔽 Filtered (type): {filtered}\n"
        f"Total checked: {total}"
    )
    _active.pop(task_id, None)


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
