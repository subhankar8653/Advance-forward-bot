"""
/auto    → Create auto-forward rule (8 steps)
/rules   → List / delete / EDIT rules

Changes:
- Step 1: Multiple source channels support
- Step 8: Remove Words step added
- Edit Rule: Remove Words edit support
"""
import uuid
import re
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
    AUTO_STATE[owner_id] = {"step": "source", "source_ids": []}
    await msg.reply(
        "🔁 **New Auto-Forward Rule**\n\n"
        "**Step 1/8** — Source channel ka koi bhi ek message forward karo\n"
        "ya channel ID/link bhejo\n\n"
        "💡 Multiple sources add kar sakte ho — ek ek karke bhejo\n"
        "Jab sab sources add ho jayein tab `/done` bhejo\n\n"
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

    # ── Step 1: Source (Multiple) ────────────────────────────────────────
    if step == "source":
        # /done — sources confirm karo
        if text.lower() in ("/done", "done"):
            source_ids = state.get("source_ids", [])
            if not source_ids:
                return await msg.reply(
                    "❌ Pehle kam se kam ek source add karo!\n\n"
                    "Source channel ka message forward karo ya ID bhejo.\n"
                    "/cancel — cancel"
                )
            state["step"] = "targets"
            AUTO_STATE[owner_id] = state
            return await msg.reply(
                f"✅ {len(source_ids)} source(s) set!\n\n"
                "**Step 2/8** — Target channel(s) ke IDs bhejo (space se alag):\n\n"
                "💡 @userinfobot se channel ID pata karo."
            )

        # Source detect karo
        source_id = None

        if msg.forward_from_chat:
            source_id = msg.forward_from_chat.id

        if not source_id and hasattr(msg, "forward_origin") and msg.forward_origin:
            origin = msg.forward_origin
            if hasattr(origin, "chat") and origin.chat:
                source_id = origin.chat.id
            # MessageOriginChannel: sender_chat bhi check karo (newer Pyrogram versions)
            elif hasattr(origin, "sender_chat") and origin.sender_chat:
                source_id = origin.sender_chat.id

        # Restricted channel: forward_sender_name se message aata hai (no chat info)
        # Lekin agar userbot us channel mein hai, toh msg.sender_chat se ID milti hai
        if not source_id and hasattr(msg, "sender_chat") and msg.sender_chat:
            source_id = msg.sender_chat.id

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
                    "\n\n⚠️ **Forward-Restricted Channel Detected!**\n"
                    "Is channel ne forwarding restrict ki hui hai — ID auto-detect nahi ho sakti.\n\n"
                    "**Option 1** — Seedha ID bhejo:\n"
                    "`-1001234567890` (numeric ID)\n\n"
                    "**Option 2** — @userinfobot se pata karo:\n"
                    "1. @userinfobot pe jaao\n"
                    "2. Us channel ka koi message forward karo wahan\n"
                    "3. Jo ID mile woh yahan bhejo\n\n"
                    "**Option 3** — Channel link bhejo:\n"
                    "`https://t.me/channelname` ya `https://t.me/c/1234567890/1`\n"
                )
            return await msg.reply(
                "❌ Channel detect nahi hua." + hint +
                "\n\n/cancel — cancel"
            )

        # Duplicate check
        source_ids = state.get("source_ids", [])
        if source_id in source_ids:
            return await msg.reply(
                f"⚠️ Yeh source (`{source_id}`) already add hai!\n\n"
                f"Ab tak {len(source_ids)} source(s) add hain.\n"
                "Aur bhejo ya `/done` karo."
            )

        source_ids.append(source_id)
        state["source_ids"] = source_ids
        AUTO_STATE[owner_id] = state

        await msg.reply(
            f"✅ Source `{source_id}` add ho gaya! ({len(source_ids)} total)\n\n"
            "Aur source add karo **ya** `/done` bhejo aage badhne ke liye."
        )

    # ── Step 2: Targets ──────────────────────────────────────────────────
    elif step == "targets":
        ids = [int(p) for p in text.split() if p.lstrip("-").isdigit()]
        if not ids:
            return await msg.reply("❌ Valid channel IDs do (e.g. `-1001234567890`)")
        state["targets"] = ids
        state["step"]    = "affiliate"
        await msg.reply(
            f"✅ {len(ids)} target(s) set!\n\n"
            "**Step 3/8** — Affiliate bot ka @username do\n"
            "(`/skip` — agar nahi chahiye)"
        )

    # ── Step 3: Affiliate ────────────────────────────────────────────────
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

        state["hide_links"]       = False
        state["hide_usernames"]   = False
        state["remove_thumbnail"] = False
        state["bold_text"]        = False
        state["step"]             = "hide_settings"

        buttons = _hide_settings_keyboard(owner_id, state)
        await msg.reply("**Step 4/8** — Hide settings choose karo:", reply_markup=buttons)

    # ── Step 5: Text Replacements ─────────────────────────────────────────
    elif step == "replace":
        replace_map = {}
        if text.lower() not in ("skip", "/skip"):
            for pair in text.split("|"):
                pair = pair.strip()
                if " : " not in pair:
                    continue
                parts = pair.split(" : ", 1)
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
            [InlineKeyboardButton("⏭ Skip → Step 7/8", callback_data=f"sfwd#{owner_id}#skip")],
        ])
        await msg.reply(
            "**Step 6/8** — Specific message forward setting:\n\n"
            "• **Only Specific Forward** — sirf wahi messages forward honge jinka trigger word match karega\n"
            "• **Specific NOT Forward** — jinka trigger word match karega woh forward NAHI honge\n\n"
            "Ek option choose karo ya skip karo:",
            reply_markup=buttons
        )

    # ── Step 6: Specific Forward (button wait) ────────────────────────────
    elif step == "specific_forward":
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Only Specific Forward: OFF", callback_data=f"sfwd#{owner_id}#forward"),
            ],
            [
                InlineKeyboardButton("🚫 Specific NOT Forward: OFF", callback_data=f"sfwd#{owner_id}#not_forward"),
            ],
            [InlineKeyboardButton("⏭ Skip → Step 7/8", callback_data=f"sfwd#{owner_id}#skip")],
        ])
        return await msg.reply(
            "⚠️ Text nahi — **button tap karo:**\n\n"
            "**Step 6/8** — Specific message forward setting:",
            reply_markup=buttons
        )

    # ── Step 7: Trigger Words ─────────────────────────────────────────────
    elif step == "trigger_words":
        if text.lower() in ("skip", "/skip"):
            state["trigger_words"] = []
        else:
            words = [w.strip().lower() for w in text.split(":") if w.strip()]
            state["trigger_words"] = words

        # Ab remove_words step pe jao
        state["step"] = "remove_words"
        AUTO_STATE[owner_id] = state
        await msg.reply(
            "**Step 8/8** — Post mein se koi specific text remove karna chahte ho?\n\n"
            "Jo text/words hatane hain woh bhejo\n"
            "Format: `blue | red | hello` (| se alag karo)\n\n"
            "💡 Jab forward hoga tab yeh words automatically hat jayenge\n\n"
            "`/skip` — koi word remove nahi karna\n\n"
            "/cancel — cancel"
        )

    # ── Step 8: Remove Words ──────────────────────────────────────────────
    elif step == "remove_words":
        if text.lower() in ("skip", "/skip"):
            state["remove_words"] = []
        else:
            words = [w.strip() for w in text.split("|") if w.strip()]
            state["remove_words"] = words

        AUTO_STATE.pop(owner_id, None)
        await _save_rule(msg, owner_id, state)

    else:
        pass


# ── Hide Settings Keyboard ────────────────────────────────────────────────────

def _hide_settings_keyboard(owner_id: int, state: dict) -> InlineKeyboardMarkup:
    hl = "ON ✅" if state.get("hide_links")       else "OFF"
    hu = "ON ✅" if state.get("hide_usernames")   else "OFF"
    rt = "ON ✅" if state.get("remove_thumbnail") else "OFF"
    bt = "ON ✅" if state.get("bold_text")        else "OFF"
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
        [InlineKeyboardButton("✅ Done → Step 5/8", callback_data=f"aflag#{owner_id}#done")],
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
            "**Step 5/8** — Text replacements do\n\n"
            "Format: `WatcherGuru : MyCryptoChannel | dark : white`\n"
            "(| se multiple pairs, : se old aur new alag karo)\n\n"
            "⚠️ Case-insensitive: @WatcherGuru, watcherguru sab match hoga\n\n"
            "`/skip` — koi replacement nahi\n\n"
            "/cancel — cancel"
        )
        await query.answer()
        return

    state[flag] = not state.get(flag, False)
    AUTO_STATE[owner_id] = state
    buttons = _hide_settings_keyboard(owner_id, state)
    await query.message.edit_reply_markup(buttons)
    await query.answer()


# ── Specific Forward Callback ─────────────────────────────────────────────────

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
        state["step"]                  = "remove_words"
        AUTO_STATE[owner_id]           = state
        await query.message.edit_text(
            "**Step 8/8** — Post mein se koi specific text remove karna chahte ho?\n\n"
            "Jo text/words hatane hain woh bhejo\n"
            "Format: `blue | red | hello` (| se alag karo)\n\n"
            "💡 Jab forward hoga tab yeh words automatically hat jayenge\n\n"
            "`/skip` — koi word remove nahi karna\n\n"
            "/cancel — cancel"
        )
        await query.answer()
        return

    state["specific_forward_mode"] = choice
    state["step"]                  = "trigger_words"
    AUTO_STATE[owner_id]           = state

    mode_label = "sirf yahi forward honge" if choice == "forward" else "yeh forward NAHI honge"
    await query.message.edit_text(
        f"**Step 7/8** — Trigger words bhejo\n\n"
        f"Forward Mode: **{'Only Forward' if choice == 'forward' else 'Not Forward'}**\n"
        f"→ {mode_label}\n\n"
        "Format: `blue : ᖴᖇIᗪᗩY : black : hanuman`\n"
        "(: se multiple words alag karo)\n\n"
        "✅ Stylish font bhi detect hoga automatically\n\n"
        "`/skip` — koi trigger word nahi (sab messages forward honge)\n\n"
        "/cancel — cancel"
    )
    await query.answer()


# ── Save Rule ─────────────────────────────────────────────────────────────────

async def _save_rule(msg: Message, owner_id: int, state: dict):
    rule_id = str(uuid.uuid4())[:8]

    # source_ids (new multiple) support
    source_ids = state.get("source_ids") or []
    if not source_ids and state.get("source_id"):
        source_ids = [state["source_id"]]

    rule = {
        "rule_id"              : rule_id,
        "source_ids"           : source_ids,
        "targets"              : state["targets"],
        "affiliate_bot"        : state.get("affiliate_bot"),
        "hide_links"           : state.get("hide_links", False),
        "hide_usernames"       : state.get("hide_usernames", False),
        "remove_thumbnail"     : state.get("remove_thumbnail", False),
        "bold_text"            : state.get("bold_text", False),
        "replace_map"          : state.get("replace_map", {}),
        "specific_forward_mode": state.get("specific_forward_mode"),
        "trigger_words"        : state.get("trigger_words", []),
        "remove_words"         : state.get("remove_words", []),
        "keep_buttons"         : state.get("keep_buttons", True),
    }
    await db.add_auto_rule(owner_id, rule)
    from auto_listener import register_rule
    await register_rule(owner_id, rule)

    mode_str = "None (sab messages)"
    if rule["specific_forward_mode"] == "forward":
        mode_str = f"✅ Only Forward (triggers: {len(rule['trigger_words'])})"
    elif rule["specific_forward_mode"] == "not_forward":
        mode_str = f"🚫 Not Forward (triggers: {len(rule['trigger_words'])})"

    remove_words_str = ", ".join(f"`{w}`" for w in rule["remove_words"]) if rule["remove_words"] else "None"

    await msg.reply(
        f"✅ **Rule Saved!** ID: `{rule_id}`\n\n"
        f"• Sources: {len(source_ids)} channel(s)\n"
        f"• Targets: {len(state['targets'])} channel(s)\n"
        f"• Affiliate: {state.get('affiliate_bot') or 'None'}\n"
        f"• Hide links: {rule['hide_links']}\n"
        f"• Hide @users: {rule['hide_usernames']}\n"
        f"• Remove thumbnail: {rule['remove_thumbnail']}\n"
        f"• Bold text: {rule['bold_text']}\n"
        f"• Replacements: {len(rule['replace_map'])}\n"
        f"• Forward mode: {mode_str}\n"
        f"• Remove words: {remove_words_str}\n"
        f"• Buttons: {'ON ✅' if rule['keep_buttons'] else 'OFF'}\n\n"
        "Use /rules to manage."
    )


# ───────────────────────── /rules ─────────────────────────────────────────────

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
        rid        = r["rule_id"]
        source_ids = r.get("source_ids") or ([r["source_id"]] if r.get("source_id") else [])
        src_str    = f"{len(source_ids)} source(s)"
        lines.append(
            f"🔹 `{rid}` — {src_str} → {len(r['targets'])} target(s)\n"
            f"   Links: {r.get('hide_links',False)} | @hide: {r.get('hide_usernames',False)} | "
            f"Thumb: {r.get('remove_thumbnail',False)} | Bold: {r.get('bold_text',False)} | "
            f"RemoveWords: {len(r.get('remove_words',[]))}"
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

    rules = await db.get_auto_rules(owner_id)
    if not rules:
        await query.message.edit_text(f"✅ Rule `{rule_id}` delete ho gaya.\n\n📭 Ab koi rule nahi hai. Banao: /auto")
        return

    lines   = []
    buttons = []
    for r in rules:
        rid        = r["rule_id"]
        source_ids = r.get("source_ids") or ([r["source_id"]] if r.get("source_id") else [])
        lines.append(
            f"🔹 `{rid}` — {len(source_ids)} source(s) → {len(r['targets'])} target(s)\n"
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


# ───────────────────────── EDIT RULE ──────────────────────────────────────────

def _edit_rule_keyboard(owner_id: int, rule_id: str, rule: dict) -> InlineKeyboardMarkup:
    hl   = "ON ✅" if rule.get("hide_links")        else "OFF"
    hu   = "ON ✅" if rule.get("hide_usernames")    else "OFF"
    rt   = "ON ✅" if rule.get("remove_thumbnail")  else "OFF"
    bt   = "ON ✅" if rule.get("bold_text")         else "OFF"
    aff  = rule.get("affiliate_bot") or "None"
    mode = rule.get("specific_forward_mode")
    mode_label = {
        "forward"    : "Only Forward ✅",
        "not_forward": "Not Forward 🚫",
        None         : "None",
    }.get(mode, "None")
    rw_count = len(rule.get("remove_words", []))
    kb       = "ON ✅" if rule.get("keep_buttons", True) else "OFF"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Sources Change",   callback_data=f"redit#{owner_id}#{rule_id}#source")],
        [InlineKeyboardButton("📤 Targets Change",   callback_data=f"redit#{owner_id}#{rule_id}#targets")],
        [InlineKeyboardButton(f"🤖 Affiliate: {aff}", callback_data=f"redit#{owner_id}#{rule_id}#affiliate")],
        [
            InlineKeyboardButton(f"🔗 Links: {hl}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_links"),
            InlineKeyboardButton(f"👤 @user: {hu}",  callback_data=f"redit#{owner_id}#{rule_id}#hide_usernames"),
        ],
        [
            InlineKeyboardButton(f"🖼 Thumb: {rt}",  callback_data=f"redit#{owner_id}#{rule_id}#remove_thumbnail"),
            InlineKeyboardButton(f"𝐁 Bold: {bt}",   callback_data=f"redit#{owner_id}#{rule_id}#bold_text"),
        ],
        [InlineKeyboardButton(f"🔘 Buttons: {kb}",   callback_data=f"redit#{owner_id}#{rule_id}#keep_buttons")],
        [InlineKeyboardButton("🔄 Replace Map",      callback_data=f"redit#{owner_id}#{rule_id}#replace")],
        [InlineKeyboardButton(f"🗑 Remove Words ({rw_count})", callback_data=f"redit#{owner_id}#{rule_id}#remove_words")],
        [InlineKeyboardButton(f"📌 Forward Mode: {mode_label}", callback_data=f"redit#{owner_id}#{rule_id}#specific_forward_mode")],
        [InlineKeyboardButton("⚡ Trigger Words",    callback_data=f"redit#{owner_id}#{rule_id}#triggers")],
        [InlineKeyboardButton("❌ Close",            callback_data=f"redit#{owner_id}#{rule_id}#close")],
    ])


@Client.on_callback_query(filters.regex(r"^edit_rule#"))
async def cb_edit_rule(bot: Client, query: CallbackQuery):
    _, owner_id_str, rule_id = query.data.split("#")
    owner_id = int(owner_id_str)
    if query.from_user.id != owner_id:
        return await query.answer("Yeh tumhara nahi.", show_alert=True)

    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        return await query.message.edit_text("❌ Rule nahi mila.")

    source_ids = rule.get("source_ids") or ([rule["source_id"]] if rule.get("source_id") else [])
    rmap       = rule.get("replace_map", {})
    rmap_prev  = ", ".join(f"`{k}`→`{v}`" for k, v in list(rmap.items())[:3])
    if len(rmap) > 3:
        rmap_prev += f" (+{len(rmap)-3} more)"
    mode       = rule.get("specific_forward_mode")
    mode_label = {"forward": "Only Forward ✅", "not_forward": "Not Forward 🚫", None: "None"}.get(mode, "None")
    rw         = rule.get("remove_words", [])
    rw_prev    = ", ".join(f"`{w}`" for w in rw[:5])
    if len(rw) > 5:
        rw_prev += f" (+{len(rw)-5} more)"

    buttons = _edit_rule_keyboard(owner_id, rule_id, rule)
    await query.message.edit_text(
        f"✏️ **Edit Rule** `{rule_id}`\n\n"
        f"• Sources: {len(source_ids)} channel(s)\n"
        f"• Targets: {len(rule['targets'])} channel(s)\n"
        f"• Affiliate: {rule.get('affiliate_bot') or 'None'}\n"
        f"• Hide links: {rule.get('hide_links', False)}\n"
        f"• Hide @users: {rule.get('hide_usernames', False)}\n"
        f"• Remove thumbnail: {rule.get('remove_thumbnail', False)}\n"
        f"• Bold text: {rule.get('bold_text', False)}\n"
        f"• Replacements: {len(rmap)}" + (f" — {rmap_prev}" if rmap_prev else "") + "\n"
        f"• Remove words: {len(rw)}" + (f" — {rw_prev}" if rw_prev else "") + "\n"
        f"• Forward mode: {mode_label}\n"
        f"• Trigger words: {len(rule.get('trigger_words', []))} set\n"
        f"• Buttons: {'ON ✅' if rule.get('keep_buttons', True) else 'OFF'}\n\n"
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
        return await query.message.edit_reply_markup(_edit_rule_keyboard(owner_id, rule_id, rule))

    # Toggle keep_buttons (default True, isliye pehli baar bhi flip karna hai)
    if field == "keep_buttons":
        new_val = not rule.get("keep_buttons", True)
        await db.update_rule_field(owner_id, rule_id, field, new_val)
        from auto_listener import _rule_map
        if rule_id in _rule_map:
            _rule_map[rule_id][field] = new_val
        msg_txt = (
            "🔘 Buttons: ON — button ke saath forward hoga (us post pe \"Forwarded from\" tag aayega)"
            if new_val else
            "🔘 Buttons: OFF — button hata ke normal forward hoga"
        )
        await query.answer(msg_txt, show_alert=False)
        rule[field] = new_val
        return await query.message.edit_reply_markup(_edit_rule_keyboard(owner_id, rule_id, rule))

    # Toggle specific_forward_mode
    if field == "specific_forward_mode":
        current = rule.get("specific_forward_mode")
        cycle   = {None: "forward", "forward": "not_forward", "not_forward": None}
        new_val = cycle.get(current)
        await db.update_rule_field(owner_id, rule_id, field, new_val)
        from auto_listener import _rule_map
        if rule_id in _rule_map:
            _rule_map[rule_id][field] = new_val
        label = {None: "None", "forward": "Only Forward ✅", "not_forward": "Not Forward 🚫"}.get(new_val, "None")
        await query.answer(f"Mode: {label}", show_alert=False)
        rule[field] = new_val
        return await query.message.edit_reply_markup(_edit_rule_keyboard(owner_id, rule_id, rule))

    # Text input fields
    clear_all(owner_id)
    EDIT_STATE[owner_id] = {"rule_id": rule_id, "field": field}

    prompts = {
        "source": (
            "📥 Naye **source channels** bhejo:\n\n"
            "Ek ek karke forward karo ya ID bhejo\n"
            "Jab sab add ho jayein tab `/done` bhejo\n\n"
            "/cancel — cancel"
        ),
        "targets": "📤 Naye **target channel IDs** bhejo (space se alag):\nExample: `-1001234 -1005678`",
        "affiliate": "🤖 Naya **affiliate bot** @username bhejo\n(`/skip` — remove karne ke liye)",
        "replace": (
            "🔄 Naya **replace map** bhejo\n\n"
            "Format: `WatcherGuru : MyCryptoChannel | dark : white`\n"
            "(| se multiple pairs, : se old aur new alag karo)\n\n"
            "⚠️ Case-insensitive\n\n"
            "`/skip` — replace hata do\n\n/cancel — cancel"
        ),
        "remove_words": (
            "🗑 **Remove Words** bhejo\n\n"
            "Jo words hatane hain woh | se alag karo:\n"
            "Format: `blue | red | hello`\n\n"
            "💡 Jab forward hoga tab yeh words hat jayenge\n\n"
            "`/skip` — remove words hata do (koi word nahi hatega)\n\n"
            "/cancel — cancel"
        ),
        "triggers": (
            "⚡ **Trigger words** bhejo\n\n"
            "Format: `blue : ᖴᖇIᗪᗩY : black : hanuman`\n"
            "(: se multiple words alag karo)\n\n"
            "✅ Stylish font bhi detect hoga automatically\n\n"
            "`/skip` — trigger words hata do\n\n"
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
    text    = (msg.text or "").strip()
    rule_id = state["rule_id"]
    field   = state["field"]

    if text.lower() in ("/cancel", "cancel"):
        EDIT_STATE.pop(owner_id, None)
        return await msg.reply("❌ Edit cancel ho gaya.")

    new_val = None

    # Source field — multiple sources support
    if field == "source":
        # /done — confirm sources
        if text.lower() in ("/done", "done"):
            source_ids = state.get("new_source_ids", [])
            if not source_ids:
                return await msg.reply(
                    "❌ Pehle kam se kam ek source add karo!\n\n"
                    "Source channel ka message forward karo ya ID bhejo."
                )
            new_val = source_ids
            # Aage jaane ke liye (save karna hai)
        else:
            # Source detect karo
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
                return await msg.reply("❌ Source samajh nahi aaya. Dobara koshish karo.")

            new_source_ids = state.get("new_source_ids", [])
            if source_id in new_source_ids:
                return await msg.reply(
                    f"⚠️ `{source_id}` already add hai!\n"
                    f"Ab tak {len(new_source_ids)} source(s).\n"
                    "Aur bhejo ya `/done` karo."
                )
            new_source_ids.append(source_id)
            state["new_source_ids"] = new_source_ids
            EDIT_STATE[owner_id] = state
            return await msg.reply(
                f"✅ Source `{source_id}` add ho gaya! ({len(new_source_ids)} total)\n\n"
                "Aur source bhejo ya `/done` karo."
            )

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
                return await msg.reply(f"❌ Bot `@{uname}` resolve nahi hua. Dobara /rules se edit karo.")

    elif field == "replace":
        replace_map = {}
        if text.lower() not in ("skip", "/skip"):
            for pair in text.split("|"):
                pair = pair.strip()
                if " : " not in pair:
                    continue
                parts = pair.split(" : ", 1)
                old_t = parts[0].strip()
                new_t = parts[1].strip()
                if old_t:
                    replace_map[old_t] = new_t
        new_val = replace_map

    elif field == "remove_words":
        if text.lower() in ("skip", "/skip"):
            new_val = []
        else:
            words = [w.strip() for w in text.split("|") if w.strip()]
            new_val = words

    elif field == "triggers":
        if text.lower() in ("skip", "/skip"):
            new_val = []
        else:
            words = [w.strip().lower() for w in text.split(":") if w.strip()]
            new_val = words

    # DB field name mapping
    _field_map = {
        "replace"     : "replace_map",
        "triggers"    : "trigger_words",
        "source"      : "source_ids",
        "remove_words": "remove_words",
        "affiliate"   : "affiliate_bot",   # BUG FIX: DB field "affiliate_bot" hai
        "targets"     : "targets",
    }
    db_field = _field_map.get(field, field)

    rule = await db.get_rule_by_id(rule_id)
    if not rule:
        EDIT_STATE.pop(owner_id, None)
        return await msg.reply("❌ Rule nahi mila.")

    owner_check = rule.get("owner_id")
    await db.update_rule_field(owner_check, rule_id, db_field, new_val)

    from auto_listener import _rule_map, unregister_rule, register_rule
    if field == "source":
        unregister_rule(rule_id)
        updated = {**rule, db_field: new_val}
        await register_rule(owner_check, updated)
    elif rule_id in _rule_map:
        _rule_map[rule_id][db_field] = new_val

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
    elif field == "remove_words":
        words_display = " | ".join(new_val) if new_val else "None"
        await msg.reply(
            f"✅ Rule `{rule_id}` ke remove words update ho gaye!\n\n"
            f"Remove words: {words_display}"
        )
    elif field == "source":
        await msg.reply(
            f"✅ Rule `{rule_id}` ke sources update ho gaye!\n"
            f"Total sources: {len(new_val)}"
        )
    else:
        await msg.reply(f"✅ Rule `{rule_id}` ka **{field}** update ho gaya!")
