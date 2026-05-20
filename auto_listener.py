"""
Auto-Listener — v3 Complete Fixed
===================================
Bug fixes confirmed from logs:
1. "Invalid parse mode html" — Pyrogram 2.0.106 mein parse_mode="html" invalid hai
   Fix: pyrogram.enums.ParseMode.HTML use karo
2. "Expected DOCUMENT, got PHOTO" — remove_thumbnail ke liye photo file_id document mein nahi jaata
   Fix: photo download karke bytes bhejo ya send_photo use karo bina thumbnail ke
3. "Peer id invalid" — userbot ne target channel join nahi kiya
   Fix: error clearly log karo, crash mat karo
4. Redeploy ke baad rules kaam nahi karte — start_listeners() mein userbot handlers theek se attach nahi hote
   Fix: register_rule mein SARE clients pe handler lagao, sirf owner_id wale nahi
"""
import asyncio
import logging
import unicodedata

from pyrogram import Client, enums
from pyrogram.handlers import MessageHandler
from pyrogram.filters import channel
from pyrogram.types import Message
from pyrogram.errors import FloodWait, PeerIdInvalid

from database import db
from userbot import _clients
from utils import process_text

logger = logging.getLogger(__name__)

_handlers:   dict[str, MessageHandler] = {}
_rule_map:   dict[str, dict]           = {}
_source_map: dict[int, list[str]]      = {}

# ─────────────────────── Unicode Normalizer ──────────────────────────────

_UNICODE_TO_ASCII = {}

def _build_unicode_map():
    ranges = [
        (0x1D400, 0x1D419, 'A'), (0x1D434, 0x1D44D, 'A'),
        (0x1D468, 0x1D481, 'A'), (0x1D4D0, 0x1D4E9, 'A'),
        (0x1D56C, 0x1D585, 'A'), (0x1D5A0, 0x1D5B9, 'A'),
        (0x1D5D4, 0x1D5ED, 'A'), (0x1D608, 0x1D621, 'A'),
        (0x1D63C, 0x1D655, 'A'), (0x1D670, 0x1D689, 'A'),
        (0x1D41A, 0x1D433, 'a'), (0x1D44E, 0x1D467, 'a'),
        (0x1D482, 0x1D49B, 'a'), (0x1D4EA, 0x1D503, 'a'),
        (0x1D586, 0x1D59F, 'a'), (0x1D5BA, 0x1D5D3, 'a'),
        (0x1D5EE, 0x1D607, 'a'), (0x1D622, 0x1D63B, 'a'),
        (0x1D656, 0x1D66F, 'a'), (0x1D68A, 0x1D6A3, 'a'),
    ]
    for start, end, base in ranges:
        for i in range(end - start + 1):
            char = chr(start + i)
            ascii_char = chr(ord(base) + i)
            if ascii_char.isalpha():
                _UNICODE_TO_ASCII[char] = ascii_char

_build_unicode_map()

def normalize_unicode_text(text: str) -> str:
    result = [_UNICODE_TO_ASCII.get(c, c) for c in text]
    text = "".join(result)
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != 'Mn')

def _check_trigger_words(text: str, trigger_words: list) -> bool:
    if not trigger_words:
        return True
    normalized = normalize_unicode_text(text).lower()
    for word in trigger_words:
        w = word.lower().strip()
        if w and w in normalized:
            return True
    return False

def _should_forward(raw_text: str, rule: dict) -> bool:
    mode          = rule.get("specific_forward_mode")
    trigger_words = rule.get("trigger_words", [])
    if not mode and not trigger_words:
        return True
    matched = _check_trigger_words(raw_text, trigger_words)
    if mode == "forward":
        return matched
    elif mode == "not_forward":
        return not matched
    return matched


# ─────────────────────── Lifecycle ───────────────────────────────────────

async def start_listeners():
    all_rules = await db.get_all_rules()
    for rule in all_rules:
        owner_id = rule["owner_id"]
        await register_rule(owner_id, rule, _startup=True)
    logger.info(f"Auto-listeners started for {len(all_rules)} rules.")


async def register_rule(owner_id: int, rule: dict, _startup: bool = False):
    rule_id   = rule["rule_id"]
    source_id = rule["source_id"]

    # Source change hone par purana handler cleanup
    if rule_id in _rule_map:
        old_source = _rule_map[rule_id].get("source_id")
        if old_source and old_source != source_id:
            old_lst = _source_map.get(old_source, [])
            if rule_id in old_lst:
                old_lst.remove(rule_id)
            if not old_lst:
                h_old = _handlers.pop(f"src_{old_source}", None)
                if h_old:
                    for slots in _clients.values():
                        for c in slots.values():
                            try:
                                c.remove_handler(h_old)
                            except Exception:
                                pass

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

        # ✅ FIX 4 (redeploy bug): SARE clients pe handler lagao
        # Sirf owner_id wale nahi — kyunki restart pe sabka client fresh hai
        for oid, slots in _clients.items():
            for c in slots.values():
                try:
                    c.add_handler(h)
                except Exception:
                    pass
    else:
        h = _handlers[handler_key]
        for oid, slots in _clients.items():
            for c in slots.values():
                try:
                    c.add_handler(h)
                except Exception:
                    pass

    logger.info(f"Rule {rule_id} registered (source={source_id}, owner={owner_id})")


def unregister_rule(rule_id: str):
    rule = _rule_map.pop(rule_id, None)
    if not rule:
        return
    source_id = rule["source_id"]
    lst = _source_map.get(source_id, [])
    if rule_id in lst:
        lst.remove(rule_id)
    if not lst:
        h = _handlers.pop(f"src_{source_id}", None)
        if h:
            for slots in _clients.values():
                for c in slots.values():
                    try:
                        c.remove_handler(h)
                    except Exception:
                        pass
    logger.info(f"Rule {rule_id} unregistered")


# ─────────────────────── Message Handler ─────────────────────────────────

async def _on_new_message(client: Client, message: Message, source_id: int):
    rule_ids = list(_source_map.get(source_id, []))
    for rule_id in rule_ids:
        rule = _rule_map.get(rule_id)
        if not rule:
            stale = _source_map.get(source_id, [])
            if rule_id in stale:
                stale.remove(rule_id)
            continue

        # DB se fresh rule fetch karo — edit changes turant apply hon
        try:
            fresh_rule = await db.get_rule_by_id(rule_id)
            if fresh_rule:
                fresh_rule["owner_id"] = rule.get("owner_id", fresh_rule.get("owner_id"))
                _rule_map[rule_id] = fresh_rule
                rule = fresh_rule
        except Exception as e:
            logger.warning(f"DB fetch failed for rule {rule_id}, using cached: {e}")

        owner_id    = rule.get("owner_id")
        owner_slots = _clients.get(owner_id, {})
        use_client  = next(iter(owner_slots.values()), client)

        asyncio.create_task(_process_and_forward(use_client, message, rule))


# ─────────────────────── Core Processing ─────────────────────────────────

async def _process_and_forward(client: Client, message: Message, rule: dict):
    targets          = rule["targets"]
    affiliate_bot    = rule.get("affiliate_bot")
    hide_links       = rule.get("hide_links", False)
    hide_usernames   = rule.get("hide_usernames", False)
    replace_map      = rule.get("replace_map") or {}
    remove_thumbnail = rule.get("remove_thumbnail", False)
    bold_text        = rule.get("bold_text", False)

    raw_text = message.text or message.caption or ""

    if not _should_forward(raw_text, rule):
        logger.debug(f"Message skipped by trigger filter. Rule: {rule['rule_id']}")
        return

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

    new_text = process_text(
        raw_text,
        hide_links=hide_links,
        hide_usernames=hide_usernames,
        replace_map=replace_map,
        bold_text=bold_text,
    )

    for target in targets:
        await _forward_one(
            client, message, target,
            original_text=raw_text,
            new_text=new_text,
            remove_thumbnail=remove_thumbnail,
            bold_text=bold_text,
        )
        await asyncio.sleep(1)


# ─────────────────────── Media Helpers ───────────────────────────────────

def _has_media(message: Message) -> bool:
    return bool(
        message.photo or message.video or message.document
        or message.audio or message.animation or message.sticker
        or message.voice or message.video_note or message.media
    )

def _has_thumbnail(message: Message) -> bool:
    return bool(message.photo or message.video or message.document)


# ─────────────────────── Forward Logic ───────────────────────────────────

async def _forward_one(
    client: Client,
    message: Message,
    target: int,
    original_text: str,
    new_text: str,
    remove_thumbnail: bool = False,
    bold_text: bool = False,
):
    has_text = bool(new_text and new_text.strip())

    # ✅ FIX 1: "Invalid parse mode html" — enums.ParseMode.HTML use karo string nahi
    parse_mode = enums.ParseMode.HTML if (bold_text and has_text) else enums.ParseMode.DEFAULT
    send_text  = new_text if has_text else None

    try:
        # ── Pure text ──────────────────────────────────────────
        if not _has_media(message):
            if send_text:
                await client.send_message(target, send_text, parse_mode=parse_mode)
            elif message.text:
                await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                )
            return

        # ── Remove thumbnail ────────────────────────────────────
        if remove_thumbnail and _has_thumbnail(message):

            if message.photo:
                # ✅ FIX 2: "Expected DOCUMENT got PHOTO" — photo ko bytes download karke bhejo
                # file_id directly document mein nahi jaata
                photo_bytes = await client.download_media(message.photo, in_memory=True)
                await client.send_document(
                    chat_id=target,
                    document=photo_bytes,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                    force_document=True,
                )

            elif message.video:
                video_bytes = await client.download_media(message.video, in_memory=True)
                await client.send_document(
                    chat_id=target,
                    document=video_bytes,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                    force_document=True,
                )

            elif message.document:
                # Document mein thumb override nahi hota — caption update karo sirf
                await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )

        # ── Normal forward ──────────────────────────────────────
        else:
            if message.photo:
                await client.send_photo(
                    chat_id=target,
                    photo=message.photo.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )
            elif message.video:
                await client.send_video(
                    chat_id=target,
                    video=message.video.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                    supports_streaming=True,
                )
            elif message.audio:
                await client.send_audio(
                    chat_id=target,
                    audio=message.audio.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )
            elif message.document:
                await client.send_document(
                    chat_id=target,
                    document=message.document.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )
            elif message.animation:
                await client.send_animation(
                    chat_id=target,
                    animation=message.animation.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )
            elif message.voice:
                await client.send_voice(
                    chat_id=target,
                    voice=message.voice.file_id,
                    caption=send_text,
                    parse_mode=parse_mode if send_text else enums.ParseMode.DEFAULT,
                )
            else:
                # sticker, video_note — as-is
                await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                )

    except PeerIdInvalid:
        # ✅ FIX 3: Peer invalid — userbot ne channel join nahi kiya
        logger.error(
            f"❌ Peer invalid: {target} — Userbot ne yeh channel join nahi kiya hai. "
            f"Pehle userbot se us channel mein join karo."
        )
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s for target {target}")
        await asyncio.sleep(e.value + 1)
        await _forward_one(
            client, message, target,
            original_text=original_text,
            new_text=new_text,
            remove_thumbnail=remove_thumbnail,
            bold_text=bold_text,
        )
    except Exception as e:
        logger.error(f"Forward to {target} failed: {e}")


# ─────────────────────── Affiliate ───────────────────────────────────────

async def _send_message(client: Client, message: Message, target: int):
    try:
        await client.copy_message(
            chat_id=target,
            from_chat_id=message.chat.id,
            message_id=message.id,
        )
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        await _send_message(client, message, target)
    except Exception as e:
        logger.warning(f"copy_message failed for affiliate ({e}), trying direct...")
        try:
            text = message.text or message.caption or ""
            if message.photo:
                await client.send_photo(chat_id=target, photo=message.photo.file_id,
                                        caption=text or None)
            elif message.video:
                await client.send_video(chat_id=target, video=message.video.file_id,
                                        caption=text or None, supports_streaming=True)
            elif message.document:
                await client.send_document(chat_id=target, document=message.document.file_id,
                                           caption=text or None)
            elif text:
                await client.send_message(target, text)
        except Exception as e2:
            logger.error(f"Affiliate forward fallback failed: {e2}")


async def _affiliate_process(client: Client, original: Message, bot_id) -> Message | None:
    if isinstance(bot_id, str):
        try:
            bot_chat = await client.get_chat(bot_id)
            bot_id   = bot_chat.id
        except Exception as e:
            logger.error(f"Affiliate bot resolve failed: {e}")
            return None

    loop    = asyncio.get_event_loop()
    replied = loop.create_future()

    async def _wait_reply(_: Client, msg: Message):
        if msg.chat.id == bot_id and not replied.done():
            replied.set_result(msg)

    from pyrogram.handlers import MessageHandler as MH
    from pyrogram.filters import private as private_filter
    h = MH(_wait_reply, filters=private_filter)
    client.add_handler(h)

    try:
        text_to_send = original.text or original.caption or ""
        if original.photo:
            await client.send_photo(chat_id=bot_id, photo=original.photo.file_id,
                                    caption=text_to_send or None)
        elif text_to_send:
            await client.send_message(bot_id, text_to_send)
        elif original.media:
            await client.copy_message(chat_id=bot_id, from_chat_id=original.chat.id,
                                      message_id=original.id)
        else:
            return None
        return await asyncio.wait_for(replied, timeout=20)
    except asyncio.TimeoutError:
        logger.warning(f"Affiliate bot {bot_id} timeout (20s)")
        return None
    except Exception as e:
        logger.error(f"Affiliate process error: {e}")
        return None
    finally:
        client.remove_handler(h)


# ─────────────────────── Client Attach ───────────────────────────────────

def attach_client_to_all_handlers(client: Client, owner_id: int = None):
    """Naya client login → usse sab handlers attach karo."""
    for h in _handlers.values():
        try:
            client.add_handler(h)
        except Exception:
            pass
