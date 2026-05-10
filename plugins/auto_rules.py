"""
/auto    → Create auto-forward rule
/rules   → List / delete rules
NOTE: Generic messages → plugins/router.py handle karta hai
"""
import uuid
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from database import db
from userbot import get_client
from state_router import AUTO_STATE, clear_all


@Client.on_message(filters.private & filters.command("auto"))
async def cmd_auto(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    client   = await get_client(owner_id)
    if not client:
        return await msg.reply("❌ Pehle /login karo.")
    clear_all(owner_id)
    AUTO_STATE[owner_id] = {"step": "source"}
    await msg.reply(
        "🔁 **New Auto-Forward Rule**\n\n"
        "**Step 1/5** — Source channel ka koi bhi message forward karo\n"
        "ya channel link bhejo\n\n"
        "/cancel — cancel"
    )


async def handle_auto_step(bot: Client, msg: Message):
    """router.py se call hota hai"""
    owner_id = msg.from_user.id
    state    = AUTO_STATE.get(owner_id)
    if not state:
        return
    step = state.get("step")
    text = (msg.text or "").strip()

    if text.lower() in ("/cancel", "cancel"):
        AUTO_STATE.pop(owner_id, None)
        return await msg.reply("❌ Cancelled.")

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
                    try:
                        client = await get_client(owner_id)
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
            "**Step 2/5** — Target channel(s) ke IDs bhejo (space se alag):\n"
            "Example: `-1001234567890 -1009876543210`\n\n"
            "💡 @userinfobot se channel ID pata karo."
        )

    elif step == "targets":
        ids = [int(p) for p in text.split() if p.lstrip("-").isdigit()]
        if not ids:
            return await msg.reply("❌ Valid channel IDs do (e.g. `-1001234567890`)")
        state["targets"] = ids
        state["step"]    = "affiliate"
        await msg.reply(
            f"✅ {len(ids)} target(s) set!\n\n"
            "**Step 3/5** — Affiliate bot ka @username do\n"
            "(`skip` — agar nahi chahiye)"
        )

    elif step == "affiliate":
        state["affiliate_bot"] = None if text.lower() == "skip" else text.lstrip("@")
        state["hide_links"]    = False
        state["hide_usernames"]= False
        state["step"]          = "hide_settings"
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔗 Hide Links: OFF", callback_data=f"aflag#{owner_id}#hide_links"),
                InlineKeyboardButton("👤 Hide @user: OFF", callback_data=f"aflag#{owner_id}#hide_usernames"),
            ],
            [InlineKeyboardButton("✅ Done", callback_data=f"aflag#{owner_id}#done")]
        ])
        await msg.reply("**Step 4/5** — Hide settings choose karo:", reply_markup=buttons)

    elif step == "replace":
        replace_map = {}
        if text.lower() != "skip":
            for line in text.split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    replace_map[parts[0].strip()] = parts[1].strip()
        state["replace_map"] = replace_map
        await _save_rule(msg, owner_id, state)


@Client.on_callback_query(filters.regex(r"^aflag#"))
async def cb_aflag(bot: Client, query: CallbackQuery):
    _, owner_id_str, flag = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    state = AUTO_STATE.get(owner_id, {})
    if flag == "done":
        state["step"] = "replace"
        await query.message.edit_text(
            "**Step 5/5** — Text replacements do\n\n"
            "Format (har line):\n`purana_text | naya_text`\n\n"
            "`skip` — koi replacement nahi"
        )
        return

    state[flag] = not state.get(flag, False)
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


async def _save_rule(msg: Message, owner_id: int, state: dict):
    rule_id = str(uuid.uuid4())[:8]
    rule = {
        "rule_id"       : rule_id,
        "source_id"     : state["source_id"],
        "targets"       : state["targets"],
        "affiliate_bot" : state.get("affiliate_bot"),
        "hide_links"    : state.get("hide_links", False),
        "hide_usernames": state.get("hide_usernames", False),
        "replace_map"   : state.get("replace_map", {}),
    }
    await db.add_auto_rule(owner_id, rule)
    AUTO_STATE.pop(owner_id, None)
    from auto_listener import register_rule
    await register_rule(owner_id, rule)
    await msg.reply(
        f"✅ **Rule Saved!** ID: `{rule_id}`\n\n"
        f"• Source: `{state['source_id']}`\n"
        f"• Targets: {len(state['targets'])} channel(s)\n"
        f"• Affiliate: {state.get('affiliate_bot') or 'None'}\n"
        f"• Hide links: {rule['hide_links']}\n"
        f"• Hide @users: {rule['hide_usernames']}\n"
        f"• Replacements: {len(rule['replace_map'])}\n\n"
        "Use /rules to manage."
    )


@Client.on_message(filters.private & filters.command("rules"))
async def cmd_rules(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    rules    = await db.get_auto_rules(owner_id)
    if not rules:
        return await msg.reply("📭 Koi auto-forward rule nahi hai.\nBanao: /auto")

    client  = await get_client(owner_id)
    lines   = []
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
            f"   Links: {r.get('hide_links',False)} | @hide: {r.get('hide_usernames',False)}"
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
