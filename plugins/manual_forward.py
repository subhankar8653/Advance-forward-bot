"""
/forward  →  Manual bulk forward
Flow:
  1. /forward
  2. Bot target channels dikhata hai → user select kare
  3. Source channel link ya forwarded message bhejo
  4. Start message ID aur End message ID btao
  5. Confirm → forwarding shuru
  6. /cancel  → rok do

Userbot use hota hai (agar logged in hai) warna bot use hoga.
"""
import asyncio
import uuid
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery
)
from pyrogram.errors import FloodWait, MessageIdInvalid
from userbot import get_client
from database import db
from config import Config

# in-memory state for active forward tasks
# { task_id: { cancelled: bool } }
_active: dict[str, dict] = {}

# pending wizard state per user
# { owner_id: { step, target_id, source_id, start_id, end_id } }
_wizard: dict[int, dict] = {}


# ─────────────────────────────── /forward ────────────────────────────────

@Client.on_message(filters.private & filters.command("forward"))
async def cmd_forward(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    await db.ensure_user(owner_id, msg.from_user.first_name)

    # Check userbot
    client = await get_client(owner_id)
    if not client:
        return await msg.reply(
            "❌ Pehle userbot login karo: /login"
        )

    # Target channels dikhao (auto rules wale targets + manual)
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
            chat = await client.get_chat(chat_id)
            label = chat.title or str(chat_id)
        except Exception:
            label = str(chat_id)
        buttons.append([InlineKeyboardButton(label, callback_data=f"fwd_target#{owner_id}#{chat_id}")])

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"fwd_cancel#{owner_id}")])

    _wizard[owner_id] = {"step": "pick_target"}
    await msg.reply(
        "📤 **Manual Forward**\n\nKis channel mein forward karna hai?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# ─────────── Callback: target selected ────────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_target#"))
async def cb_fwd_target(bot: Client, query: CallbackQuery):
    _, owner_id_str, chat_id_str = query.data.split("#")
    owner_id = int(owner_id_str)

    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    _wizard[owner_id]["target_id"] = int(chat_id_str)
    _wizard[owner_id]["step"]      = "source"

    await query.message.edit_text(
        "✅ Target set!\n\n"
        "Ab **source channel** ka koi bhi ek message forward karo\n"
        "ya channel ka link bhejo (e.g. `https://t.me/channelname/1`)\n\n"
        "/cancel — cancel"
    )


@Client.on_callback_query(filters.regex(r"^fwd_cancel#"))
async def cb_fwd_cancel(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    _wizard.pop(owner_id, None)
    await query.message.edit_text("❌ Cancelled.")


# ─────────── Message handler for wizard steps ─────────────────

@Client.on_message(filters.private & ~filters.command([
    "start","login","logout","cancel","forward","auto","rules","settings","status"
]))
async def forward_wizard_handler(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    state    = _wizard.get(owner_id)
    if not state:
        return

    step = state.get("step")

    if msg.text and msg.text.strip() == "/cancel":
        _wizard.pop(owner_id, None)
        return await msg.reply("❌ Forward cancelled.")

    # ── Step: get source ─────────────────────────────
    if step == "source":
        import re
        source_id = None

        if msg.forward_from_chat:
            source_id = msg.forward_from_chat.id
        elif msg.text:
            m = re.search(r't\.me/(?:c/)?(\-?\d+|[A-Za-z0-9_]+)', msg.text)
            if m:
                raw = m.group(1)
                if raw.lstrip("-").isdigit():
                    source_id = int(("-100" + raw) if not raw.startswith("-") else raw)
                else:
                    try:
                        chat = await (await get_client(owner_id)).get_chat(raw)
                        source_id = chat.id
                    except Exception:
                        pass

        if not source_id:
            return await msg.reply("❌ Source samajh nahi aaya. Channel ka message forward karo ya link bhejo.")

        state["source_id"] = source_id
        state["step"]      = "start_id"
        await msg.reply(
            "✅ Source set!\n\n"
            "Ab **start message ID** bhejo\n(jahan se forward shuru karna hai, e.g. `1`)"
        )

    # ── Step: start message ID ───────────────────────
    elif step == "start_id":
        if not msg.text or not msg.text.strip().isdigit():
            return await msg.reply("❌ Sirf number bhejo, e.g. `1`")
        state["start_id"] = int(msg.text.strip())
        state["step"]     = "end_id"
        await msg.reply("Ab **end message ID** bhejo (jahan tak forward karna hai, e.g. `10000`)")

    # ── Step: end message ID → confirm ──────────────
    elif step == "end_id":
        if not msg.text or not msg.text.strip().isdigit():
            return await msg.reply("❌ Sirf number bhejo, e.g. `10000`")
        state["end_id"] = int(msg.text.strip())
        state["step"]   = "confirm"

        total = state["end_id"] - state["start_id"] + 1
        try:
            client = await get_client(owner_id)
            src  = await client.get_chat(state["source_id"])
            tgt  = await client.get_chat(state["target_id"])
            src_name = src.title or str(state["source_id"])
            tgt_name = tgt.title or str(state["target_id"])
        except Exception:
            src_name = str(state["source_id"])
            tgt_name = str(state["target_id"])

        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Start", callback_data=f"fwd_start#{owner_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"fwd_cancel#{owner_id}")
        ]])

        await msg.reply(
            f"📋 **Confirm Forward**\n\n"
            f"• Source: `{src_name}`\n"
            f"• Target: `{tgt_name}`\n"
            f"• Messages: {state['start_id']} → {state['end_id']} ({total} msgs)\n\n"
            "Start karein?",
            reply_markup=buttons
        )


# ─────────── Callback: start forwarding ──────────────────────

@Client.on_callback_query(filters.regex(r"^fwd_start#"))
async def cb_fwd_start(bot: Client, query: CallbackQuery):
    owner_id = int(query.data.split("#")[1])
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara button nahi.", show_alert=True)

    state = _wizard.pop(owner_id, None)
    if not state:
        return await query.message.edit_text("❌ State expire ho gaya. /forward dobara karo.")

    task_id  = str(uuid.uuid4())[:8]
    source   = state["source_id"]
    target   = state["target_id"]
    start_id = state["start_id"]
    end_id   = state["end_id"]
    total    = end_id - start_id + 1

    _active[task_id] = {"cancelled": False}

    status_msg = await query.message.edit_text(
        f"⏳ Forwarding shuru...\n"
        f"Task ID: `{task_id}`\n"
        f"0 / {total} done\n\n"
        f"/cancel_{task_id} — stop"
    )

    client = await get_client(owner_id)
    asyncio.create_task(
        _do_forward(client, bot, query.from_user.id, task_id,
                    source, target, start_id, end_id, total, status_msg)
    )


async def _do_forward(client, bot, owner_id, task_id,
                      source, target, start_id, end_id,
                      total, status_msg):
    done = skipped = 0
    UPDATE_EVERY = 50   # har 50 messages pe status update

    for msg_id in range(start_id, end_id + 1):
        if _active.get(task_id, {}).get("cancelled"):
            await _edit_safe(status_msg, f"🛑 Cancelled!\n✅ Done: {done} | ⏭ Skipped: {skipped}")
            _active.pop(task_id, None)
            return

        try:
            await client.forward_messages(
                chat_id     = target,
                from_chat_id= source,
                message_ids = msg_id
            )
            done += 1
        except MessageIdInvalid:
            skipped += 1
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
            try:
                await client.forward_messages(target, source, msg_id)
                done += 1
            except Exception:
                skipped += 1
        except Exception:
            skipped += 1

        # Sleep to avoid flood
        await asyncio.sleep(0.7)

        if (done + skipped) % UPDATE_EVERY == 0:
            pct = int((done + skipped) / total * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            await _edit_safe(
                status_msg,
                f"📤 Forwarding...\n"
                f"{bar} {pct}%\n"
                f"✅ {done} / {total} done | ⏭ {skipped} skipped\n\n"
                f"/cancel_{task_id} — stop"
            )

    await _edit_safe(
        status_msg,
        f"🎉 **Done!**\n✅ Forwarded: {done}\n⏭ Skipped: {skipped}\nTotal: {total}"
    )
    _active.pop(task_id, None)


# ─────────── /cancel_<task_id> ───────────────────────────────

@Client.on_message(filters.private & filters.regex(r"^/cancel_([a-f0-9]+)$"))
async def cmd_cancel_task(bot: Client, msg: Message):
    task_id = msg.text.split("_")[1]
    if task_id in _active:
        _active[task_id]["cancelled"] = True
        await msg.reply(f"🛑 Task `{task_id}` cancel ho raha hai...")
    else:
        await msg.reply("❌ Aisa koi task nahi mila.")


# ─────────── helper ──────────────────────────────────────────

async def _edit_safe(message, text: str):
    try:
        await message.edit_text(text)
    except Exception:
        pass
