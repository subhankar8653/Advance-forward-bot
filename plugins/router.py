"""
router.py — Ek hi message handler jo sab wizard states route karta hai.
"""
from pyrogram import Client, filters
from pyrogram.types import Message
from state_router import active_state


@Client.on_message(filters.private & ~filters.command(
    ["start", "login", "logout", "auto", "rules", "forward"]
), group=1)
async def route_message(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    state    = active_state(owner_id)

    if state == "login":
        from plugins.login import handle_login_step
        await handle_login_step(bot, msg)

    elif state == "forward":
        from plugins.manual_forward import handle_forward_step
        await handle_forward_step(bot, msg)

    elif state == "auto":
        from plugins.auto_rules import handle_auto_step
        await handle_auto_step(bot, msg)

    elif state == "edit":
        from plugins.auto_rules import handle_edit_step
        await handle_edit_step(bot, msg)
