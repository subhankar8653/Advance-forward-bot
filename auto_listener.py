"""
Auto-Listener:
- Userbot har rule ke source channel ko monitor karta hai
- Naya message aaya → text process karo → targets pe forward karo
- Affiliate bot option: pehle bot ko message bhejo, reply aaye toh forward karo

FIX: Affiliate bot flow - channel se forward_messages bot pe kaam nahi karta.
     Ab text/caption seedha send hoga bot ko.
"""
import asyncio
import logging
from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram.filters import channel
from pyrogram.types import Message
from pyrogram.errors import FloodWait

from database import db
from userbot import get_client, _clients
from utils import process_text

logger = logging.getLogger(__name__)

_handlers: dict[str, MessageHandler] = {}
_rule_map: dict[str, dict] = {}
_source_map: dict[int, list[str]] = {}


async def start_listeners():
    all_rules = await db.get_all_rules()
    for rule in all_rules:
        owner_id = rule["owner_id"]
        await register_rule(owner_id, rule, _startup=True)
    logger.info(f"Auto-listeners started for {len(all_rules)} rules.")


async def register_rule(owner_id: int, rule: dict, _startup: bool = False):
    rule_id   = rule["rule_id"]
    source_id = rule["source_id"]

    _rule_map[rule_id] = {**rule, "owner_id": owner_id}

    if source_id not in _source_map:
        _source_map[source_id] = []
    if rule_id not in _source_map[source_id]:
        _source_map[source_id].append(rule_id)

    handler_key = f"src_{source_id}"
    if handler_key not in _handlers:
        async def _make_handler(src_id: int):
            async def handler(client: Client, message: Message):
                if message.chat.id != src_id:
                    return
                await _on_new_message(client, message, src_id)
            return handler

        fn = await _make_handler(source_id)
        h  = MessageHandler(fn, filters=channel)
        _handlers[handler_key] = h

        for oid, client in _clients.items():
            client.add_handler(h)

    logger.info(f"Rule {rule_id} registered (source={source_id})")


def unregister_rule(rule_id: str):
    rule = _rule_map.pop(rule_id, None)
    if not rule:
        return

    source_id = rule["source_id"]
    lst = _source_map.get(source_id, [])
    if rule_id in lst:
        lst.remove(rule_id)

    if not lst:
        handler_key = f"src_{source_id}"
        h = _handlers.pop(handler_key, None)
        if h:
            for client in _clients.values():
                client.remove_handler(h)

    logger.info(f"Rule {rule_id} unregistered")


async def _on_new_message(client: Client, message: Message, source_id: int):
    rule_ids = _source_map.get(source_id, [])
    for rule_id in rule_ids:
        rule = _rule_map.get(rule_id)
        if not rule:
            continue
        asyncio.create_task(_process_and_forward(client, message, rule))


async def _process_and_forward(client: Client, message: Message, rule: dict):
    targets        = rule["targets"]
    affiliate_bot  = rule.get("affiliate_bot")
    hide_links     = rule.get("hide_links", False)
    hide_usernames = rule.get("hide_usernames", False)
    replace_map    = rule.get("replace_map", {})

    # ── Affiliate bot flow ───────────────────────────────────
    if affiliate_bot:
        try:
            processed = await _affiliate_process(client, message, affiliate_bot)
        except Exception as e:
            logger.error(f"Affiliate bot error: {e}")
            processed = None

        if processed:
            for target in targets:
                await _send_message(client, processed, target)
            return
        # fallback: normal forward

    # ── Normal forward ───────────────────────────────────────
    raw_text = message.text or message.caption or ""
    new_text = process_text(
        raw_text,
        hide_links=hide_links,
        hide_usernames=hide_usernames,
        replace_map=replace_map
    )

    for target in targets:
        await _forward_one(client, message, target, new_text)
        await asyncio.sleep(1)


async def _forward_one(client: Client, message: Message, target: int, new_text: str):
    try:
        if message.media:
            await client.copy_message(
                chat_id=target, from_chat_id=message.chat.id,
                message_id=message.id, caption=new_text or None
            )
        else:
            await client.send_message(target, new_text or ".")
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        await _forward_one(client, message, target, new_text)
    except Exception as e:
        logger.error(f"Forward to {target} failed: {e}")


async def _send_message(client: Client, message: Message, target: int):
    try:
        await client.copy_message(
            chat_id=target, from_chat_id=message.chat.id, message_id=message.id
        )
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        await _send_message(client, message, target)
    except Exception as e:
        logger.error(f"Affiliate forward to {target} failed: {e}")


async def _affiliate_process(client: Client, original: Message, bot_id) -> Message | None:
    """
    ✅ FIX: bot_id ab integer hai (username nahi).
    Channel messages ko directly forward nahi kar sakte private bot pe.
    Solution: Message ka text/caption seedha send karo.
    """
    # bot_id integer ya string dono handle karo (backward compat)
    if isinstance(bot_id, str):
        try:
            bot_chat = await client.get_chat(bot_id)
            bot_id   = bot_chat.id
        except Exception as e:
            logger.error(f"Affiliate bot resolve failed: {e}")
            return None

    replied  = asyncio.get_event_loop().create_future()

    async def _wait_reply(_: Client, msg: Message):
        if msg.chat.id == bot_id and not replied.done():
            replied.set_result(msg)

    from pyrogram.handlers import MessageHandler as MH
    h = MH(_wait_reply)
    client.add_handler(h)

    try:
        text_to_send = original.text or original.caption or ""

        if text_to_send:
            # Text/caption seedha bhejo
            await client.send_message(bot_id, text_to_send)
        elif original.media:
            # Pure media: copy_message try karo
            await client.copy_message(
                chat_id=bot_id,
                from_chat_id=original.chat.id,
                message_id=original.id
            )
        else:
            return None

        response = await asyncio.wait_for(replied, timeout=15)
        return response
    except asyncio.TimeoutError:
        logger.warning(f"Affiliate bot {bot_username} did not reply in time.")
        return None
    finally:
        client.remove_handler(h)


def attach_client_to_all_handlers(client: Client):
    for h in _handlers.values():
        client.add_handler(h)
