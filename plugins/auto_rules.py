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
        "**Step 1/7** — Source channel ka koi bhi ek message forward karo\n"
        "ya channel id bhejo\n\n"
        "Example: `-1001234567890`\n\n"
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

    # ── Step 1: Source ──────────────────────────────────────────────────
    if step == "source":
        import re
        source_id = None

        if msg.forward_from_chat:
            source_id = msg.forward_from_chat.id

        if not source_id and hasattr(msg, "forward_origin") and msg.forward_origin:
            origin = msg.forward_origin
            if hasattr(origin, "chat") and origin.chat:
                source_id = origin.chat.id

        if not source_id and text:
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
                clean = text.strip()
                if clean.lstrip("-").isdigit():
                    source_id = int(clean)

        if not source_id:
            hint = ""
            if msg.forward_sender_name or (hasattr(msg, "forward_origin") and msg.forward_origin):
                hint = (
                    "\n\n⚠️ **Restricted channel detected!**\n"
                    "Is channel ne forwarding restrict ki hui hai.\n"
                    "Channel ID manually bhejo:\n"
                    "1. @userinfobot pe jaao\n"
                    "2. Us channel ka koi message forward karo wahan\n"
                    "3. Jo ID mile woh yahan bhejo\n"
                    "Example: `-1001556054753`"
                )
            return await msg.reply(
                "❌ Channel detect nahi hua." + hint +
                "\n\nYa seedha channel link bhejo:\n`https://t.me/channelname`\n\n"
                "/cancel — cancel"
            )

        state["source_id"] = source_id
        state["step"]      = "targets"
        await msg.reply(
            "✅ Source set!\n\n"
            "**Step 2/7** — Target channel(s) ke IDs bhejo (space se alag):\n"
            "Example: `-1001234567890 -1009876543210`\n\n"
            "💡 @userinfobot se channel ID pata karo."
        )

    # ── Step 2: Targets ─────────────────────────────────────────────────
    elif step == "targets":
        ids = [int(p) for p in text.split() if p.lstrip("-").isdigit()]
        if not ids:
            return await msg.reply("❌ Valid channel IDs do (e.g. `-1001234567890`)")
        state["targets"] = ids
        state["step"]    = "affiliate"
        await msg.reply(
            f"✅ {len(ids)} target(s) set!\n\n"
            "**Step 3/7** — Affiliate bot ka @username do\n"
            "(`/skip` — agar nahi chahiye)"
        )

    # ── Step 3: Affiliate ───────────────────────────────────────────────
    elif step == "affiliate":
        if text.lower() in ("skip", "/skip"):
            state["affiliate_bot"] = None
        else:
            uname = text.lstrip("@")
            try:
                client = await get_client(owner_id)
                bot_chat = await client.get_chat(uname)
                state["affiliate_bot"] = bot_chat.id
            except Exception:
                return await msg.reply(
                    f"❌ Bot `@{uname}` resolve nahi hua.\n"
                    "Make sure:\n"
                    "• Username sahi hai\n"
                    "• Userbot ne us bot se pehle ek baar baat ki ho\n\n"
                    "Dobara try karo:"
                )

        # Init hide settings defaults
        state["hide_links"]        = False
        state["hide_usernames"]    = False
        state["remove_thumbnail"]  = False
        state["bold_text"]         = False
        state["step"]              = "hide_settings"

        buttons = _hide_settings_keyboard(owner_id, state)
        await msg.reply("**Step 4/7** — Hide settings choose karo:", reply_markup=buttons)

    # ── Step 5: Text Replacements ────────────────────────────────────────
    elif step == "replace":
        replace_map = {}
        if text.lower() not in ("skip", "/skip"):
            for line in text.split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    old_t = parts[0].strip()
                    new_t = parts[1].strip()
                    if old_t:
                        replace_map[old_t] = new_t
        state["replace_map"] = replace_map
        state["step"]        = "specific_forward"

        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Only Specific Forward: OFF", callback_data=f"sfwd#{owner_id}#forward"),
            ],
            [
                InlineKeyboardButton("🚫 Specific NOT Forward: OFF", callback_data=f"sfwd#{owner_id}#not_forward"),
            ],
            [InlineKeyboardButton("⏭ Skip → Step 7/7", callback_data=f"sfwd#{owner_id}#skip")],
        ])
        await msg.reply(
            "**Step 6/7** — Specific message forward setting:\n\n"
            "• **Only Specific Forward** — sirf wahi messages forward honge jinka trigger word match karega\n"
            "• **Specific NOT Forward** — jinka trigger word match karega woh forward NAHI honge\n\n"
            "Ek option choose karo ya skip karo:",
            reply_markup=buttons
        )

    # ── Step 7: Trigger Words ────────────────────────────────────────────
    elif step == "trigger_words":
        if text.lower() in ("skip", "/skip"):
            state["trigger_words"] = []
        else:
            words = [w.strip().lower() for w in text.split(",") if w.strip()]
            state["trigger_words"] = words

        AUTO_STATE.pop(owner_id, None)
        await _save_rule(msg, owner_id, state)

    else:
        pass


# ── Hide Settings Keyboard Builder ──────────────────────────────────────────

def _hide_settings_keyboard(owner_id: int, state: dict) -> InlineKeyboardMarkup:
    hl  = "ON ✅" if state.get("hide_links")       else "OFF"
    hu  = "ON ✅" if state.get("hide_usernames")   else "OFF"
    rt  = "ON ✅" if state.get("remove_thumbnail") else "OFF"
    bt  = "ON ✅" if state.get("bold_text")        else "OFF"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"👤 Remove @username: {hu}", callback_data=f"aflag#{owner_id}#hide_usernames"),
        ],
        [
            InlineKeyboardButton(f"🔗 Remove Link: {hl}", callback_data=f"aflag#{owner_id}#hide_links"),
        ],
        [
            InlineKeyboardButton(f"🖼 Remove Thumbnail: {rt}", callback_data=f"aflag#{owner_id}#remove_thumbnail"),
        ],
        [
            InlineKeyboardButton(f"𝐁 Text Bold: {bt}", callback_data=f"aflag#{owner_id}#bold_text"),
        ],
        [InlineKeyboardButton("✅ Done → Step 5/7", callback_data=f"aflag#{owner_id}#done")],
    ])


@Client.on_callback_query(filters.regex(r"^aflag#"))
async def cb_aflag(bot: Client, query: CallbackQuery):
    _, owner_id_str, flag = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    state = AUTO_STATE.get(owner_id, {})
    if not state:
        return await query.answer("❌ Session expire ho gaya. /auto dobara karo.", show_alert=True)

    if flag == "done":
        state["step"] = "replace"
        AUTO_STATE[owner_id] = state
        await query.message.edit_text(
            "**Step 5/7** — Text replacements do\n\n"
            "Format (har line):\n`purana_text | naya_text`\n\n"
            "Example:\n`@WatcherGuru | @MyCryptoChannel`\n\n"
            "`/skip` — koi replacement nahi\n\n"
            "/cancel — cancel"
        )
        await query.answer()
        return

    # Toggle the flag
    state[flag] = not state.get(flag, False)
    AUTO_STATE[owner_id] = state

    buttons = _hide_settings_keyboard(owner_id, state)
    await query.message.edit_reply_markup(buttons)
    await query.answer()


# ── Specific Forward Callback ────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r"^sfwd#"))
async def cb_sfwd(bot: Client, query: CallbackQuery):
    _, owner_id_str, choice = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    state = AUTO_STATE.get(owner_id, {})
    if not state:
        return await query.answer("❌ Session expire ho gaya. /auto dobara karo.", show_alert=True)

    if choice == "skip":
        state["specific_forward_mode"] = None
        state["trigger_words"]         = []
        AUTO_STATE.pop(owner_id, None)
        await query.message.edit_text("⏭ Specific forward skip kar diya.")
        await _save_rule(query.message, owner_id, state)
        await query.answer()
        return

    state["specific_forward_mode"] = choice  # "forward" or "not_forward"
    state["step"]                  = "trigger_words"
    AUTO_STATE[owner_id]           = state

    mode_label = "sirf yahi forward honge" if choice == "forward" else "yeh forward NAHI honge"
    await query.message.edit_text(
        f"**Step 7/7** — Trigger words bhejo (comma se alag)\n\n"
        f"Jab message mein yeh word hoga → {mode_label}\n"
        f"(Stylish font bhi detect hoga automatically)\n\n"
        "Example: `Sunday, Monday, crypto`\n\n"
        "`/skip` — koi trigger word nahi (sab messages forward honge)\n\n"
        "/cancel — cancel"
    )
    await query.answer()


# ── Save Rule ────────────────────────────────────────────────────────────────

async def _save_rule(msg: Message, owner_id: int, state: dict):
    rule_id = str(uuid.uuid4())[:8]
    rule = {
        "rule_id"              : rule_id,
        "source_id"            : state["source_id"],
        "targets"              : state["targets"],
        "affiliate_bot"        : state.get("affiliate_bot"),
        "hide_links"           : state.get("hide_links", False),
        "hide_usernames"       : state.get("hide_usernames", False),
        "remove_thumbnail"     : state.get("remove_thumbnail", False),
        "bold_text"            : state.get("bold_text", False),
        "replace_map"          : state.get("replace_map", {}),
        "specific_forward_mode": state.get("specific_forward_mode"),
        "trigger_words"        : state.get("trigger_words", []),
    }
    await db.add_auto_rule(owner_id, rule)
    from auto_listener import register_rule
    await register_rule(owner_id, rule)

    mode_str = "None (sab messages)"
    if rule["specific_forward_mode"] == "forward":
        mode_str = f"✅ Only Forward (triggers: {len(rule['trigger_words'])})"
    elif rule["specific_forward_mode"] == "not_forward":
        mode_str = f"🚫 Not Forward (triggers: {len(rule['trigger_words'])})"

    await msg.reply(
        f"✅ **Rule Saved!** ID: `{rule_id}`\n\n"
        f"• Source: `{state['source_id']}`\n"
        f"• Targets: {len(state['targets'])} channel(s)\n"
        f"• Affiliate: {state.get('affiliate_bot') or 'None'}\n"
        f"• Hide links: {rule['hide_links']}\n"
        f"• Hide @users: {rule['hide_usernames']}\n"
        f"• Remove thumbnail: {rule['remove_thumbnail']}\n"
        f"• Bold text: {rule['bold_text']}\n"
        f"• Replacements: {len(rule['replace_map'])}\n"
        f"• Forward mode: {mode_str}\n\n"
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
            f"   Links: {r.get('hide_links',False)} | @hide: {r.get('hide_usernames',False)} | "
            f"Thumb: {r.get('remove_thumbnail',False)} | Bold: {r.get('bold_text',False)}"
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

    # Delete ke baad updated rules list show karo
    rules = await db.get_auto_rules(owner_id)
    if not rules:
        await query.message.edit_text(f"✅ Rule `{rule_id}` delete ho gaya.\n\n📭 Ab koi rule nahi hai. Banao: /auto")
        return

    client = await get_client(owner_id)
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
            f"   Links: {r.get('hide_links',False)} | @hide: {r.get('hide_usernames',False)} | "
            f"Thumb: {r.get('remove_thumbnail',False)} | Bold: {r.get('bold_text',False)}"
        )
        buttons.append([
            InlineKeyboardButton(f"✏️ Edit {rid}", callback_data=f"edit_rule#{owner_id}#{rid}"),
            InlineKeyboardButton(f"🗑 Delete {rid}", callback_data=f"del_rule#{owner_id}#{rid}")
        ])

    await query.message.edit_text(
        f"✅ Rule `{rule_id}` delete ho gaya.\n\n📋 **Baaki Rules:**\n\n" + "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons)
    )


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

    hl   = "ON ✅" if rule.get("hide_links")        else "OFF"
    hu   = "ON ✅" if rule.get("hide_usernames")    else "OFF"
    rt   = "ON ✅" if rule.get("remove_thumbnail")  else "OFF"
    bt   = "ON ✅" if rule.get("bold_text")         else "OFF"
    aff  = rule.get("affiliate_bot") or "None"
    rmap = rule.get("replace_map", {})
    rmap_preview = ", ".join(f"`{k}`→`{v}`" for k, v in list(rmap.items())[:3])
    if len(rmap) > 3:
        rmap_preview += f" (+{len(rmap)-3} more)"

    mode = rule.get("specific_forward_mode")
    mode_label = {
        "forward"    : "Only Forward ✅",
        "not_forward": "Not Forward 🚫",
        None         : "None",
    }.get(mode, "None")

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Source Change",   callback_data=f"redit#{owner_id}#{rule_id}#source")],
        [InlineKeyboardButton("📤 Targets Change",  callback_data=f"redit#{owner_id}#{rule_id}#targets")],
        [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
        [
            InlineKeyboardButton(f"🔗 Links: {hl}",   callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
            InlineKeyboardButton(f"👤 @user: {hu}",   callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
        ],
        [
            InlineKeyboardButton(f"🖼 Thumb: {rt}",   callback_data=f"redit#{owner_id}#{rule_id}#remove_thumbnail"),
            InlineKeyboardButton(f"𝐁 Bold: {bt}",    callback_data=f"redit#{owner_id}#{rule_id}#bold_text"),
        ],
        [InlineKeyboardButton("🔄 Replace Map",      callback_data=f"redit#{owner_id}#{rule_id}#replace")],
        [InlineKeyboardButton(f"📌 Forward Mode: {mode_label}", callback_data=f"redit#{owner_id}#{rule_id}#specific_forward_mode")],
        [InlineKeyboardButton("⚡ Trigger Words",    callback_data=f"redit#{owner_id}#{rule_id}#triggers")],
        [InlineKeyboardButton("❌ Close",            callback_data=f"redit#{owner_id}#{rule_id}#close")],
    ])
    await query.message.edit_text(
        f"✏️ **Edit Rule** `{rule_id}`\n\n"
        f"• Source: `{rule['source_id']}`\n"
        f"• Targets: {len(rule['targets'])} channel(s)\n"
        f"• Affiliate: {aff}\n"
        f"• Hide links: {rule.get('hide_links', False)}\n"
        f"• Hide @users: {rule.get('hide_usernames', False)}\n"
        f"• Remove thumbnail: {rule.get('remove_thumbnail', False)}\n"
        f"• Bold text: {rule.get('bold_text', False)}\n"
        f"• Replacements: {len(rmap)}" + (f" — {rmap_preview}" if rmap_preview else "") + "\n"
        f"• Forward mode: {mode_label}\n"
        f"• Trigger words: {len(rule.get('trigger_words', []))} set\n\n"
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

    # Toggle boolean fields
    if field in ("hide_links", "hide_usernames", "remove_thumbnail", "bold_text"):
        new_val = not rule.get(field, False)
        await db.update_rule_field(owner_id, rule_id, field, new_val)
        from auto_listener import _rule_map
        if rule_id in _rule_map:
            _rule_map[rule_id][field] = new_val
        await query.answer(f"{'ON' if new_val else 'OFF'} kar diya!", show_alert=False)
        rule[field] = new_val

        hl  = "ON ✅" if rule.get("hide_links")        else "OFF"
        hu  = "ON ✅" if rule.get("hide_usernames")    else "OFF"
        rt  = "ON ✅" if rule.get("remove_thumbnail")  else "OFF"
        bt  = "ON ✅" if rule.get("bold_text")         else "OFF"
        aff = rule.get("affiliate_bot") or "None"
        mode = rule.get("specific_forward_mode")
        mode_label = {
            "forward"    : "Only Forward ✅",
            "not_forward": "Not Forward 🚫",
            None         : "None",
        }.get(mode, "None")

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Source Change",   callback_data=f"redit#{owner_id}#{rule_id}#source")],
            [InlineKeyboardButton("📤 Targets Change",  callback_data=f"redit#{owner_id}#{rule_id}#targets")],
            [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
            [
                InlineKeyboardButton(f"🔗 Links: {hl}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
                InlineKeyboardButton(f"👤 @user: {hu}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
            ],
            [
                InlineKeyboardButton(f"🖼 Thumb: {rt}",  callback_data=f"redit#{owner_id}#{rule_id}#remove_thumbnail"),
                InlineKeyboardButton(f"𝐁 Bold: {bt}",   callback_data=f"redit#{owner_id}#{rule_id}#bold_text"),
            ],
            [InlineKeyboardButton("🔄 Replace Map",     callback_data=f"redit#{owner_id}#{rule_id}#replace")],
            [InlineKeyboardButton(f"📌 Forward Mode: {mode_label}", callback_data=f"redit#{owner_id}#{rule_id}#specific_forward_mode")],
            [InlineKeyboardButton("⚡ Trigger Words",   callback_data=f"redit#{owner_id}#{rule_id}#triggers")],
            [InlineKeyboardButton("❌ Close",           callback_data=f"redit#{owner_id}#{rule_id}#close")],
        ])
        return await query.message.edit_reply_markup(buttons)

    # Toggle specific_forward_mode (cycle: None → forward → not_forward → None)
    if field == "specific_forward_mode":
        current = rule.get("specific_forward_mode")
        cycle = {None: "forward", "forward": "not_forward", "not_forward": None}
        new_val = cycle.get(current)
        await db.update_rule_field(owner_id, rule_id, field, new_val)
        from auto_listener import _rule_map
        if rule_id in _rule_map:
            _rule_map[rule_id][field] = new_val
        label = {None: "None", "forward": "Only Forward ✅", "not_forward": "Not Forward 🚫"}.get(new_val, "None")
        await query.answer(f"Mode: {label}", show_alert=False)
        rule[field] = new_val

        hl  = "ON ✅" if rule.get("hide_links")        else "OFF"
        hu  = "ON ✅" if rule.get("hide_usernames")    else "OFF"
        rt  = "ON ✅" if rule.get("remove_thumbnail")  else "OFF"
        bt  = "ON ✅" if rule.get("bold_text")         else "OFF"
        aff = rule.get("affiliate_bot") or "None"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Source Change",   callback_data=f"redit#{owner_id}#{rule_id}#source")],
            [InlineKeyboardButton("📤 Targets Change",  callback_data=f"redit#{owner_id}#{rule_id}#targets")],
            [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
            [
                InlineKeyboardButton(f"🔗 Links: {hl}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
                InlineKeyboardButton(f"👤 @user: {hu}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
            ],
            [
                InlineKeyboardButton(f"🖼 Thumb: {rt}",  callback_data=f"redit#{owner_id}#{rule_id}#remove_thumbnail"),
                InlineKeyboardButton(f"𝐁 Bold: {bt}",   callback_data=f"redit#{owner_id}#{rule_id}#bold_text"),
            ],
            [InlineKeyboardButton("🔄 Replace Map",     callback_data=f"redit#{owner_id}#{rule_id}#replace")],
            [InlineKeyboardButton(f"📌 Forward Mode: {label}", callback_data=f"redit#{owner_id}#{rule_id}#specific_forward_mode")],
            [InlineKeyboardButton("⚡ Trigger Words",   callback_data=f"redit#{owner_id}#{rule_id}#triggers")],
            [InlineKeyboardButton("❌ Close",           callback_data=f"redit#{owner_id}#{rule_id}#close")],
        ])
        return await query.message.edit_reply_markup(buttons)

    # Fields jo text input maangein
    clear_all(owner_id)
    EDIT_STATE[owner_id] = {"rule_id": rule_id, "field": field}

    prompts = {
        "source"   : "📥 Naya **source channel** bhejo:\nForward karo ya link do (e.g. `https://t.me/c/123/1`)",
        "targets"  : "📤 Naye **target channel IDs** bhejo (space se alag):\nExample: `-1001234 -1005678`",
        "affiliate": "🤖 Naya **affiliate bot** @username bhejo\n(`/skip` — remove karne ke liye)",
        "replace"  : (
            "🔄 Naya **replace map** bhejo\n"
            "Format: `purana | naya` (har line)\n\n"
            "Example:\n`@WatcherGuru | @MyCryptoChannel`\n\n"
            "`/skip` — replace hata do\n\n/cancel — cancel"
        ),
        "triggers" : (
            "⚡ **Trigger words** bhejo (comma se alag)\n\n"
            "Example: `monday, tuesday, crypto`\n\n"
            "Jab bhi source channel mein koi word match karega\n"
            "(stylish font bhi detect hoga automatically)\n\n"
            "`/skip` — trigger words hata do (sab messages forward honge)\n\n"
            "/cancel — cancel"
        ),
    }
    await query.message.edit_text(prompts.get(field, "Value bhejo:") + "\n\n/cancel — cancel")
    await query.answer()


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

        if not source_id and hasattr(msg, "forward_origin") and msg.forward_origin:
            origin = msg.forward_origin
            if hasattr(origin, "chat") and origin.chat:
                source_id = origin.chat.id

        if not source_id and text:
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
            if not source_id and text.strip().lstrip("-").isdigit():
                source_id = int(text.strip())

        if not source_id:
            hint = ""
            if msg.forward_sender_name or (hasattr(msg, "forward_origin") and msg.forward_origin):
                hint = "\n\n⚠️ Restricted channel — ID manually bhejo (e.g. `-1001234567890`)"
            return await msg.reply("❌ Source samajh nahi aaya. Dobara koshish karo." + hint)
        new_val = source_id

    elif field == "targets":
        ids = [int(p) for p in text.split() if p.lstrip("-").isdigit()]
        if not ids:
            return await msg.reply("❌ Valid IDs do.")
        new_val = ids

    elif field == "affiliate":
        if text.lower() in ("skip", "/skip"):
            new_val = None
        else:
            uname = text.lstrip("@")
            try:
                client = await get_client(owner_id)
                bot_chat = await client.get_chat(uname)
                new_val = bot_chat.id
            except Exception:
                EDIT_STATE.pop(owner_id, None)
                return await msg.reply(
                    f"❌ Bot `@{uname}` resolve nahi hua. Dobara /rules se edit karo."
                )

    elif field == "replace":
        replace_map = {}
        if text.lower() not in ("skip", "/skip"):
            for line in text.split("\n"):
                if "|" in line:
                    parts = line.split("|", 1)
                    old_t = parts[0].strip()
                    new_t = parts[1].strip()
                    if old_t:
                        replace_map[old_t] = new_t
        new_val = replace_map

    elif field == "triggers":
        if text.lower() in ("skip", "/skip"):
            new_val = []
        else:
            words = [w.strip().lower() for w in text.split(",") if w.strip()]
            new_val = words

    # DB update
    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        EDIT_STATE.pop(owner_id, None)
        return await msg.reply("❌ Rule nahi mila.")

    owner_check = rule.get("owner_id")
    await db.update_rule_field(owner_check, rule_id, field, new_val)

    from auto_listener import _rule_map, unregister_rule, register_rule
    if field == "source":
        unregister_rule(rule_id)
        updated = {**rule, field: new_val}
        await register_rule(owner_check, updated)
    elif rule_id in _rule_map:
        _rule_map[rule_id][field] = new_val

    EDIT_STATE.pop(owner_id, None)

    if field == "triggers":
        words_display = ", ".join(new_val) if new_val else "None (sab messages forward honge)"
        await msg.reply(
            f"✅ Rule `{rule_id}` ke trigger words update ho gaye!\n\n"
            f"Active triggers: `{words_display}`"
        )
    elif field == "replace":
        await msg.reply(
            f"✅ Rule `{rule_id}` ka replace map update ho gaya!\n"
            f"Replacements: {len(new_val)}"
        )
    else:
        await msg.reply(f"✅ Rule `{rule_id}` ka **{field}** update ho gaya!")
