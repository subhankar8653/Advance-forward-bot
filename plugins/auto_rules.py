"""
/auto    → Create auto-forward rule
/rules   → List / delete rules

Rule fields:
  source_id      : channel to watch
  targets        : list of target chat_ids
  affiliate_bot  : @bot username (optional) — bot se message leke send hoga
  hide_links     : bool
  hide_usernames : bool
  replace_map    : {old: new}
"""
import uuid
import json
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from database import db
from userbot import get_client
from config import Config

# wizard state per user
# { owner_id: { step, ... } }
_wiz: dict[int, dict] = {}


# ─────────────────────────────── /auto ───────────────────────────────────

@Client.on_message(filters.private & filters.command("auto"))
async def cmd_auto(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    client   = await get_client(owner_id)
    if not client:
        return await msg.reply("❌ Pehle /login karo.")

    _wiz[owner_id] = {"step": "source"}
    await msg.reply(
        "🔁 **New Auto-Forward Rule**\n\n"
        "**Step 1/5** — Source channel ka koi bhi message forward karo\n"
        "ya channel link bhejo (e.g. `https://t.me/sourcechannel`)\n\n"
        "/cancel — cancel"
    )


# ─────────────────────── Wizard message handler ──────────────────────────

@Client.on_message(filters.private & ~filters.command([
    "start","login","logout","cancel","forward","auto","rules","settings","status"
]))
async def auto_wizard_handler(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    state    = _wiz.get(owner_id)
    if not state:
        return

    if msg.text and msg.text.strip() == "/cancel":
        _wiz.pop(owner_id, None)
        return await msg.reply("❌ Cancelled.")

    step = state.get("step")
    text = (msg.text or "").strip()

    # ── Step 1: Source ────────────────────────────────────────
    if step == "source":
        import re
        source_id = None

        if msg.forward_from_chat:
            source_id = msg.forward_from_chat.id
        elif text:
            m = re.search(r't\.me/(?:c/)?(\-?\d+|[A-Za-z0-9_]+)', text)
            if m:
                raw = m.group(1)
                if raw.lstrip("-").isdigit():
                    source_id = int(("-100" + raw) if not raw.startswith("-") else raw)
                else:
                    client = await get_client(owner_id)
                    try:
                        chat = await client.get_chat(raw)
                        source_id = chat.id
                    except Exception:
                        pass

        if not source_id:
            return await msg.reply("❌ Samajh nahi aaya. Channel ka message forward karo ya link do.")

        state["source_id"] = source_id
        state["step"]      = "targets"
        await msg.reply(
            "✅ Source set!\n\n"
            "**Step 2/5** — Target channel(s) ke IDs bhejo (space se alag karo):\n"
            "Example: `-1001234567890 -1009876543210`\n\n"
            "💡 Channel ID pane ke liye @userinfobot se check karo."
        )

    # ── Step 2: Targets ───────────────────────────────────────
    elif step == "targets":
        ids = []
        for part in text.split():
            part = part.strip()
            if part.lstrip("-").isdigit():
                ids.append(int(part))
        if not ids:
            return await msg.reply("❌ Valid channel IDs do (e.g. `-1001234567890`)")

        state["targets"] = ids
        state["step"]    = "affiliate"
        await msg.reply(
            f"✅ {len(ids)} target(s) set!\n\n"
            "**Step 3/5** — Affiliate bot ka @username do\n"
            "(agar nahi chahiye toh `skip` bhejo)\n\n"
            "💡 Yeh bot source post leke apna affiliate link wala post dega."
        )

    # ── Step 3: Affiliate bot ─────────────────────────────────
    elif step == "affiliate":
        if text.lower() == "skip":
            state["affiliate_bot"] = None
        else:
            state["affiliate_bot"] = text.lstrip("@")
        state["step"] = "hide_settings"

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔗 Hide Links: OFF", callback_data=f"aflag#{owner_id}#hide_links"),
                InlineKeyboardButton("👤 Hide @user: OFF", callback_data=f"aflag#{owner_id}#hide_usernames"),
            ],
            [InlineKeyboardButton("✅ Done", callback_data=f"aflag#{owner_id}#done")]
        ])
        state["hide_links"]     = False
        state["hide_usernames"] = False
        await msg.reply(
            "**Step 4/5** — Hide settings choose karo:",
            reply_markup=buttons
        )

    # ── Step 5: Replace map ───────────────────────────────────
    elif step == "replace":
        replace_map = {}
        if text.lower() != "skip":
            for line in text.split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    replace_map[parts[0].strip()] = parts[1].strip()
        state["replace_map"] = replace_map
        await _save_rule(msg, owner_id, state)


# ─────────────── Callbacks for toggle buttons ────────────────

@Client.on_callback_query(filters.regex(r"^aflag#"))
async def cb_aflag(bot: Client, query: CallbackQuery):
    _, owner_id_str, flag = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    state = _wiz.get(owner_id, {})

    if flag == "done":
        state["step"] = "replace"
        await query.message.edit_text(
            "**Step 5/5** — Text replacements do\n\n"
            "Format: `purana_text | naya_text` (har line mein ek)\n"
            "Example:\n"
            "`t.me/sourcechannel | t.me/mychannel`\n\n"
            "`skip` — koi replacement nahi"
        )
        return

    current = state.get(flag, False)
    state[flag] = not current

    hl = "ON ✅" if state.get("hide_links") else "OFF"
    hu = "ON ✅" if state.get("hide_usernames") else "OFF"
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🔗 Hide Links: {hl}", callback_data=f"aflag#{owner_id}#hide_links"),
            InlineKeyboardButton(f"👤 Hide @user: {hu}", callback_data=f"aflag#{owner_id}#hide_usernames"),
        ],
        [InlineKeyboardButton("✅ Done", callback_data=f"aflag#{owner_id}#done")]
    ])
    await query.message.edit_reply_markup(buttons)
    await query.answer()


# ─────────────── Save & register rule ────────────────────────

async def _save_rule(msg: Message, owner_id: int, state: dict):
    rule_id = str(uuid.uuid4())[:8]
    rule = {
        "rule_id"      : rule_id,
        "source_id"    : state["source_id"],
        "targets"      : state["targets"],
        "affiliate_bot": state.get("affiliate_bot"),
        "hide_links"   : state.get("hide_links", False),
        "hide_usernames": state.get("hide_usernames", False),
        "replace_map"  : state.get("replace_map", {}),
    }
    await db.add_auto_rule(owner_id, rule)
    _wiz.pop(owner_id, None)

    # Register listener on userbot
    from auto_listener import register_rule
    await register_rule(owner_id, rule)

    await msg.reply(
        f"✅ **Rule Saved!** ID: `{rule_id}`\n\n"
        f"• Source: `{state['source_id']}`\n"
        f"• Targets: {len(state['targets'])} channel(s)\n"
        f"• Affiliate bot: {state.get('affiliate_bot') or 'None'}\n"
        f"• Hide links: {rule['hide_links']}\n"
        f"• Hide @users: {rule['hide_usernames']}\n"
        f"• Replacements: {len(rule['replace_map'])}\n\n"
        "Use /rules to manage."
    )


# ─────────────────────────────── /rules ──────────────────────────────────

@Client.on_message(filters.private & filters.command("rules"))
async def cmd_rules(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    rules    = await db.get_auto_rules(owner_id)

    if not rules:
        return await msg.reply("📭 Koi auto-forward rule nahi hai.\nBanao: /auto")

    client = await get_client(owner_id)
    lines  = []
    buttons = []

    for r in rules:
        rid = r["rule_id"]
        try:
            src  = await client.get_chat(r["source_id"])
            name = src.title or str(r["source_id"])
        except Exception:
            name = str(r["source_id"])

        lines.append(
            f"🔹 `{rid}` — {name} → {len(r['targets'])} target(s)\n"
            f"   Links hide: {r.get('hide_links',False)} | @hide: {r.get('hide_usernames',False)}"
        )
        buttons.append([InlineKeyboardButton(f"🗑 Delete {rid}", callback_data=f"del_rule#{owner_id}#{rid}")])

    await msg.reply(
        "📋 **Auto-Forward Rules:**\n\n" + "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
    )


@Client.on_callback_query(filters.regex(r"^del_rule#"))
async def cb_del_rule(bot: Client, query: CallbackQuery):
    _, owner_id_str, rule_id = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    await db.delete_auto_rule(owner_id, rule_id)
    from auto_listener import unregister_rule
    unregister_rule(rule_id)

    await query.message.edit_text(f"✅ Rule `{rule_id}` delete ho gaya.")
