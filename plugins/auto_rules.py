"""
/auto    → Create auto-forward rule
/rules   → List / delete / EDIT rules
NOTE: Generic messages → plugins/router.py handle karta hai
"""
import uuid
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from database import db
from userbot import get_client
from state_router import AUTO_STATE, EDIT_STATE, clear_all


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
        "**Step 1/5** — Source channel ka koi bhi ek message forward karo\n"
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
        if text.lower() == "skip":
            state["affiliate_bot"] = None
        else:
            # Username ya user_id — resolve karke integer ID store karo
            uname = text.lstrip("@")
            try:
                client = await get_client(owner_id)
                bot_chat = await client.get_chat(uname)
                state["affiliate_bot"] = bot_chat.id  # integer ID store
            except Exception:
                return await msg.reply(
                    f"❌ Bot `@{uname}` resolve nahi hua.\n"
                    "Make sure:\n"
                    "• Username sahi hai\n"
                    "• Userbot ne us bot se pehle ek baar baat ki ho\n\n"
                    "Dobara try karo:"
                )
        state["hide_links"]     = False
        state["hide_usernames"] = False
        state["step"]           = "hide_settings"
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


# ───────────────────────── /rules ─────────────────────────────────────────

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
        buttons.append([
            InlineKeyboardButton(f"✏️ Edit {rid}", callback_data=f"edit_rule#{owner_id}#{rid}"),
            InlineKeyboardButton(f"🗑 Delete {rid}", callback_data=f"del_rule#{owner_id}#{rid}")
        ])

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


# ───────────────────────── EDIT RULE ──────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^edit_rule#"))
async def cb_edit_rule(bot: Client, query: CallbackQuery):
    _, owner_id_str, rule_id = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        return await query.message.edit_text("❌ Rule nahi mila.")

    hl = "ON ✅" if rule.get("hide_links") else "OFF"
    hu = "ON ✅" if rule.get("hide_usernames") else "OFF"
    aff = rule.get("affiliate_bot") or "None"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Source Change", callback_data=f"redit#{owner_id}#{rule_id}#source")],
        [InlineKeyboardButton("📤 Targets Change", callback_data=f"redit#{owner_id}#{rule_id}#targets")],
        [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
        [
            InlineKeyboardButton(f"🔗 Links: {hl}", callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
            InlineKeyboardButton(f"👤 @user: {hu}", callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
        ],
        [InlineKeyboardButton("🔄 Replace Map", callback_data=f"redit#{owner_id}#{rule_id}#replace")],
        [InlineKeyboardButton("❌ Close", callback_data=f"redit#{owner_id}#{rule_id}#close")],
    ])
    await query.message.edit_text(
        f"✏️ **Edit Rule** `{rule_id}`\n\n"
        f"• Source: `{rule['source_id']}`\n"
        f"• Targets: {len(rule['targets'])} channel(s)\n"
        f"• Affiliate: {aff}\n"
        f"• Hide links: {rule.get('hide_links', False)}\n"
        f"• Hide @users: {rule.get('hide_usernames', False)}\n"
        f"• Replacements: {len(rule.get('replace_map', {}))}\n\n"
        "Kya change karna hai?",
        reply_markup=buttons
    )


@Client.on_callback_query(filters.regex(r"^redit#"))
async def cb_redit(bot: Client, query: CallbackQuery):
    parts = query.data.split("#")
    _, owner_id_str, rule_id, field = parts
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        return await query.message.edit_text("❌ Rule nahi mila.")

    if field == "close":
        return await query.message.edit_text("✅ Edit band kar diya.")

    # Toggle fields jo seedha change hote hain
    if field in ("hide_links", "hide_usernames"):
        new_val = not rule.get(field, False)
        await db.update_rule_field(owner_id, rule_id, field, new_val)
        from auto_listener import _rule_map
        if rule_id in _rule_map:
            _rule_map[rule_id][field] = new_val
        await query.answer(f"{'ON' if new_val else 'OFF'} kar diya!", show_alert=False)
        # Refresh edit panel
        rule[field] = new_val
        hl  = "ON ✅" if rule.get("hide_links") else "OFF"
        hu  = "ON ✅" if rule.get("hide_usernames") else "OFF"
        aff = rule.get("affiliate_bot") or "None"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Source Change", callback_data=f"redit#{owner_id}#{rule_id}#source")],
            [InlineKeyboardButton("📤 Targets Change", callback_data=f"redit#{owner_id}#{rule_id}#targets")],
            [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
            [
                InlineKeyboardButton(f"🔗 Links: {hl}", callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
                InlineKeyboardButton(f"👤 @user: {hu}", callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
            ],
            [InlineKeyboardButton("🔄 Replace Map", callback_data=f"redit#{owner_id}#{rule_id}#replace")],
            [InlineKeyboardButton("❌ Close", callback_data=f"redit#{owner_id}#{rule_id}#close")],
        ])
        return await query.message.edit_reply_markup(buttons)

    # Fields jo text input maangein
    clear_all(owner_id)
    EDIT_STATE[owner_id] = {"rule_id": rule_id, "field": field}

    prompts = {
        "source"   : "📥 Naya **source channel** bhejo:\nForward karo ya link do (e.g. `https://t.me/c/123/1`)",
        "targets"  : "📤 Naye **target channel IDs** bhejo (space se alag):\nExample: `-1001234 -1005678`",
        "affiliate": "🤖 Naya **affiliate bot** @username bhejo\n(`skip` — remove karne ke liye)",
        "replace"  : "🔄 Naya **replace map** bhejo\nFormat: `purana | naya` (har line)\n`skip` — replace hata do",
    }
    await query.message.edit_text(prompts.get(field, "Value bhejo:") + "\n\n/cancel — cancel")


async def handle_edit_step(bot: Client, msg: Message):
    """router.py se call hota hai"""
    owner_id = msg.from_user.id
    state    = EDIT_STATE.get(owner_id)
    if not state:
        return
    text     = (msg.text or "").strip()
    rule_id  = state["rule_id"]
    field    = state["field"]

    if text.lower() in ("/cancel", "cancel"):
        EDIT_STATE.pop(owner_id, None)
        return await msg.reply("❌ Edit cancel ho gaya.")

    import re
    new_val = None

    if field == "source":
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
            return await msg.reply("❌ Source samajh nahi aaya. Dobara koshish karo.")
        new_val = source_id

    elif field == "targets":
        ids = [int(p) for p in text.split() if p.lstrip("-").isdigit()]
        if not ids:
            return await msg.reply("❌ Valid IDs do.")
        new_val = ids

    elif field == "affiliate":
        if text.lower() == "skip":
            new_val = None
        else:
            uname = text.lstrip("@")
            try:
                client = await get_client(owner_id)
                bot_chat = await client.get_chat(uname)
                new_val = bot_chat.id  # integer ID store
            except Exception:
                EDIT_STATE.pop(owner_id, None)
                return await msg.reply(
                    f"❌ Bot `@{uname}` resolve nahi hua. Dobara /rules se edit karo."
                )

    elif field == "replace":
        replace_map = {}
        if text.lower() != "skip":
            for line in text.split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    replace_map[parts[0].strip()] = parts[1].strip()
        new_val = replace_map

    # DB update
    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        EDIT_STATE.pop(owner_id, None)
        return await msg.reply("❌ Rule nahi mila.")

    owner_check = rule.get("owner_id")
    await db.update_rule_field(owner_check, rule_id, field, new_val)

    # Live _rule_map update
    from auto_listener import _rule_map, unregister_rule, register_rule
    if field == "source":
        # Source change: old listener hata do, naya lagao
        unregister_rule(rule_id)
        updated = {**rule, field: new_val}
        await register_rule(owner_check, updated)
    elif rule_id in _rule_map:
        _rule_map[rule_id][field] = new_val

    EDIT_STATE.pop(owner_id, None)
    await msg.reply(f"✅ Rule `{rule_id}` ka **{field}** update ho gaya!")
