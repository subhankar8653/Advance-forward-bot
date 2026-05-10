"""
router.py — EK hi generic message handler
Koi conflict nahi.
Login / Forward / Auto wizard ko yahan se route karo.
"""
from pyrogram import Client, filters
from pyrogram.types import Message
from state_router import active_state


@Client.on_message(
    filters.private
    & ~filters.command([
        "start","login","logout","cancel","forward",
        "auto","rules","settings","status","help"
    ])
    & ~filters.regex(r"^/cancel_[a-f0-9]+$")
)
async def central_router(bot: Client, msg: Message):
    owner_id = msg.from_user.id
    which    = active_state(owner_id)

    if which == "login":
        from plugins.login import handle_login_step
        await handle_login_step(bot, msg)

    elif which == "forward":
        from plugins.manual_forward import handle_forward_step
        await handle_forward_step(bot, msg)

    elif which == "auto":
        from plugins.auto_rules import handle_auto_step
        await handle_auto_step(bot, msg)

    # koi wizard active nahi — ignore karo (ya help bhejo)
