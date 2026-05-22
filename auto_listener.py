"""
Auto-Listener — v5
===================
Fixes:
1. Media group (album) — saari photos ek saath bhejo, alag alag nahi
2. Replace map — processed text hamesha use hoga
3. Bold text — HTML parse_mode
4. Remove thumbnail — sirf text bhejo
5. Hide links / hide usernames
"""
import asyncio
import logging
import unicodedata

from pyrogram import Client, enums
from pyrogram.handlers import MessageHandler
from pyrogram.filters import channel
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from pyrogram.errors import FloodWait, PeerIdInvalid

from database import db
from userbot import _clients
from utils import process_text

logger = logging.getLogger(__name__)

_handlers:      dict[str, MessageHandler] = {}
_handler_groups: dict[str, int]          = {}   # handler_key → group number
_rule_map:      dict[str, dict]           = {}
_source_map:    dict[int, list[str]]      = {}
_next_group:    int                       = 10   # group 0-9 reserved, hum 10 se shuru karein

# Media group buffer: { "chat_id:media_group_id": [Message, ...] }
_media_group_buffer: dict[str, list[Message]] = {}
_media_group_tasks:  dict[str, asyncio.Task]  = {}
MEDIA_GROUP_WAIT = 1.5  # seconds — saare photos aane ka wait


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
    """
    Forward Mode logic:
    - None        : koi limitation nahi, sab forward hoga
    - only_forward: sirf wahi post forward hogi jisme trigger word ho
    - not_forward : jis post mein trigger word ho woh forward NAHI hogi
    """
    mode          = rule.get("specific_forward_mode")
    trigger_words = rule.get("trigger_words", [])

    # None mode — koi filter nahi, sab forward karo
    if not mode:
        return True

    # Trigger words nahi hain toh mode ka koi matlab nahi — sab forward karo
    if not trigger_words:
        return True

    matched = _check_trigger_words(raw_text, trigger_words)

    if mode == "forward":
        # Only Forward: sirf tab forward karo jab trigger word match kare
        return matched
    elif mode == "not_forward":
        # Not Forward: jab trigger word match kare tab forward MAT karo
        return not matched

    return True


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

    if rule_id in _rule_map:
        old_source = _rule_map[rule_id].get("source_id")
        if old_source and old_source != source_id:
            old_lst = _source_map.get(old_source, [])
            if rule_id in old_lst:
                old_lst.remove(rule_id)
            if not old_lst:
                old_key = f"src_{old_source}"
                h_old = _handlers.pop(old_key, None)
                old_grp = _handler_groups.pop(old_key, 10)
                if h_old:
                    for slots in _clients.values():
                        for c in slots.values():
                            try:
                                c.remove_handler(h_old, old_grp)
                            except Exception:
                                pass

    _rule_map[rule_id] = {**rule, "owner_id": owner_id}

    if source_id not in _source_map:
        _source_map[source_id] = []
    if rule_id not in _source_map[source_id]:
        _source_map[source_id].append(rule_id)

    handler_key = f"src_{source_id}"
    if handler_key not in _handlers:
        global _next_group
        grp = _next_group
        _next_group += 1
        _handler_groups[handler_key] = grp

        async def _make_handler(src_id: int):
            async def handler(client: Client, message: Message):
                if message.chat.id != src_id:
                    return
                await _on_new_message(client, message, src_id)
            return handler

        fn = await _make_handler(source_id)
        h  = MessageHandler(fn, filters=channel)
        _handlers[handler_key] = h
        for oid, slots in _clients.items():
            for c in slots.values():
                try:
                    c.add_handler(h, grp)
                except Exception:
                    pass
    # Handler already exist karta hai — dobara add mat karo (duplicate fire hoga)

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
        handler_key = f"src_{source_id}"
        h = _handlers.pop(handler_key, None)
        grp = _handler_groups.pop(handler_key, 10)
        if h:
            for slots in _clients.values():
                for c in slots.values():
                    try:
                        c.remove_handler(h, grp)
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

        # ── Media group check ──────────────────────────────────
        if message.media_group_id:
            buf_key = f"{message.chat.id}:{message.media_group_id}:{rule_id}"
            _media_group_buffer.setdefault(buf_key, []).append(message)

            # Agar pehle se timer chal raha hai toh cancel karo (naya message aaya)
            old_task = _media_group_tasks.get(buf_key)
            if old_task and not old_task.done():
                old_task.cancel()

            # Naya timer lagao — MEDIA_GROUP_WAIT seconds baad flush
            task = asyncio.create_task(
                _flush_media_group(use_client, buf_key, rule)
            )
            _media_group_tasks[buf_key] = task
        else:
            asyncio.create_task(_process_and_forward(use_client, message, rule))


# ─────────────────────── Media Group Flush ───────────────────────────────

async def _flush_media_group(client: Client, buf_key: str, rule: dict):
    """
    MEDIA_GROUP_WAIT seconds baad saari buffered photos ek saath bhejo.
    """
    try:
        await asyncio.sleep(MEDIA_GROUP_WAIT)
    except asyncio.CancelledError:
        return  # Naya message aaya — cancel theek hai

    messages = _media_group_buffer.pop(buf_key, [])
    _media_group_tasks.pop(buf_key, None)

    if not messages:
        return

    # Message ID ke order mein sort karo
    messages.sort(key=lambda m: m.id)

    # Caption wala message dhundo (usually last one)
    caption_msg = None
    for m in messages:
        if m.caption or m.text:
            caption_msg = m
            break
    if caption_msg is None:
        caption_msg = messages[-1]

    raw_text = caption_msg.caption or caption_msg.text or ""

    # Trigger filter check
    if not _should_forward(raw_text, rule):
        return

    hide_links       = rule.get("hide_links", False)
    hide_usernames   = rule.get("hide_usernames", False)
    replace_map      = rule.get("replace_map") or {}
    remove_thumbnail = rule.get("remove_thumbnail", False)
    bold_text        = rule.get("bold_text", False)
    targets          = rule["targets"]

    new_text = process_text(
        raw_text,
        hide_links=hide_links,
        hide_usernames=hide_usernames,
        replace_map=replace_map,
        bold_text=bold_text,
    )

    parse_mode = enums.ParseMode.HTML if bold_text else enums.ParseMode.DISABLED
    has_text   = bool(new_text and new_text.strip())

    for target in targets:
        try:
            if remove_thumbnail:
                # Sirf caption text bhejo — koi media nahi
                if has_text:
                    await client.send_message(target, new_text, parse_mode=parse_mode)
            else:
                # Saari photos ek album mein bhejo
                media_list = []
                for i, msg in enumerate(messages):
                    # Sirf last (ya caption wale) message mein caption lagao
                    cap = (new_text if has_text else None) if msg.id == caption_msg.id else None
                    cap_mode = parse_mode if cap else enums.ParseMode.DISABLED

                    if msg.photo:
                        media_list.append(InputMediaPhoto(
                            media=msg.photo.file_id,
                            caption=cap,
                            parse_mode=cap_mode,
                        ))
                    elif msg.video:
                        media_list.append(InputMediaVideo(
                            media=msg.video.file_id,
                            caption=cap,
                            parse_mode=cap_mode,
                            supports_streaming=True,
                        ))
                    elif msg.document:
                        media_list.append(InputMediaDocument(
                            media=msg.document.file_id,
                            caption=cap,
                            parse_mode=cap_mode,
                        ))
                    elif msg.audio:
                        media_list.append(InputMediaAudio(
                            media=msg.audio.file_id,
                            caption=cap,
                            parse_mode=cap_mode,
                        ))

                if media_list:
                    await client.send_media_group(chat_id=target, media=media_list)
                elif has_text:
                    # Koi media resolve nahi hua — sirf text
                    await client.send_message(target, new_text, parse_mode=parse_mode)

        except PeerIdInvalid:
            logger.error(f"❌ Peer invalid: {target} — Userbot ne channel join nahi kiya.")
        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s for target {target}")
            await asyncio.sleep(e.value + 1)
            # Simple retry without recursion for media group
            try:
                if remove_thumbnail and has_text:
                    await client.send_message(target, new_text, parse_mode=parse_mode)
                elif media_list:
                    await client.send_media_group(chat_id=target, media=media_list)
            except Exception as e2:
                logger.error(f"Media group retry failed for {target}: {e2}")
        except Exception as e:
            logger.error(f"Media group forward to {target} failed: {e}")

        await asyncio.sleep(1)


# ─────────────────────── Core Processing (single message) ────────────────

def _needs_processing(rule: dict) -> bool:
    return bool(
        rule.get("bold_text") or
        rule.get("hide_links") or
        rule.get("hide_usernames") or
        rule.get("replace_map") or
        rule.get("remove_thumbnail")
    )


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
            new_text=new_text,
            remove_thumbnail=remove_thumbnail,
            bold_text=bold_text,
            needs_processing=_needs_processing(rule),
        )
        await asyncio.sleep(1)


# ─────────────────────── Forward Single Message ───────────────────────────

async def _forward_one(
    client: Client,
    message: Message,
    target: int,
    new_text: str,
    remove_thumbnail: bool = False,
    bold_text: bool = False,
    needs_processing: bool = False,
):
    has_text   = bool(new_text and new_text.strip())
    parse_mode = enums.ParseMode.HTML if bold_text else enums.ParseMode.DISABLED

    try:
        # CASE 1: Remove Thumbnail — sirf text bhejo
        if remove_thumbnail:
            if has_text:
                await client.send_message(target, new_text, parse_mode=parse_mode)
            return

        # CASE 2: Pure text message
        if not _has_media(message):
            if has_text:
                await client.send_message(target, new_text, parse_mode=parse_mode)
            return

        # CASE 3: Media — koi processing nahi toh copy as-is
        if not needs_processing:
            await client.copy_message(
                chat_id=target,
                from_chat_id=message.chat.id,
                message_id=message.id,
            )
            return

        # CASE 4: Media + processing — processed caption ke saath bhejo
        caption  = new_text if has_text else None
        cap_mode = parse_mode if caption else enums.ParseMode.DISABLED

        if message.photo:
            await client.send_photo(target, message.photo.file_id,
                                    caption=caption, parse_mode=cap_mode)
        elif message.video:
            await client.send_video(target, message.video.file_id,
                                    caption=caption, parse_mode=cap_mode,
                                    supports_streaming=True)
        elif message.audio:
            await client.send_audio(target, message.audio.file_id,
                                    caption=caption, parse_mode=cap_mode)
        elif message.document:
            await client.send_document(target, message.document.file_id,
                                       caption=caption, parse_mode=cap_mode)
        elif message.animation:
            await client.send_animation(target, message.animation.file_id,
                                        caption=caption, parse_mode=cap_mode)
        elif message.voice:
            await client.send_voice(target, message.voice.file_id,
                                    caption=caption, parse_mode=cap_mode)
        else:
            await client.copy_message(target, message.chat.id, message.id)

    except PeerIdInvalid:
        logger.error(f"❌ Peer invalid: {target} — Userbot ne channel join nahi kiya.")
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s for target {target}")
        await asyncio.sleep(e.value + 1)
        await _forward_one(client, message, target,
                           new_text=new_text,
                           remove_thumbnail=remove_thumbnail,
                           bold_text=bold_text,
                           needs_processing=needs_processing)
    except Exception as e:
        logger.error(f"Forward to {target} failed: {e}")


def _has_media(message: Message) -> bool:
    return bool(
        message.photo or message.video or message.document
        or message.audio or message.animation or message.sticker
        or message.voice or message.video_note or message.media
    )


# ─────────────────────── Affiliate ───────────────────────────────────────

async def _send_message(client: Client, message: Message, target: int):
    try:
        await client.copy_message(target, message.chat.id, message.id)
    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        await _send_message(client, message, target)
    except Exception as e:
        logger.warning(f"copy_message failed for affiliate ({e}), trying direct...")
        try:
            text = message.text or message.caption or ""
            if message.photo:
                await client.send_photo(target, message.photo.file_id, caption=text or None)
            elif message.video:
                await client.send_video(target, message.video.file_id,
                                        caption=text or None, supports_streaming=True)
            elif message.document:
                await client.send_document(target, message.document.file_id, caption=text or None)
            elif text:
                await client.send_message(target, text)
        except Exception as e2:
            logger.error(f"Affiliate fallback failed: {e2}")


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
            await client.send_photo(bot_id, original.photo.file_id,
                                    caption=text_to_send or None)
        elif text_to_send:
            await client.send_message(bot_id, text_to_send)
        elif original.media:
            await client.copy_message(bot_id, original.chat.id, original.id)
        else:
            return None
        return await asyncio.wait_for(replied, timeout=20)
    except asyncio.TimeoutError:
        logger.warning(f"Affiliate bot {bot_id} timeout")
        return None
    except Exception as e:
        logger.error(f"Affiliate process error: {e}")
        return None
    finally:
        client.remove_handler(h)


# ─────────────────────── Client Attach ───────────────────────────────────

def attach_client_to_all_handlers(client: Client):
    for handler_key, h in _handlers.items():
        grp = _handler_groups.get(handler_key, 10)
        try:
            client.add_handler(h, grp)
        except Exception:
            pass
