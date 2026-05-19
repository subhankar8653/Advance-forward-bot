"""
Auto-Listener:
- Userbot har rule ke source channel ko monitor karta hai
- Naya message aaya → trigger words check → specific_forward_mode check → text process karo → targets pe forward karo
- Affiliate bot option: pehle bot ko message bhejo, reply aaye toh forward karo
- Stylish Unicode font support: 𝐌𝐎𝐍𝐃𝐀𝐘 → MONDAY detect hoga
- NEW: remove_thumbnail — media ka thumbnail hatana
- NEW: bold_text — text/caption ko bold karna
- NEW: specific_forward_mode — sirf specific words wale messages forward karo ya skip karo
"""
import asyncio
import logging
import unicodedata
import re

from pyrogram import Client
from pyrogram.handlers import MessageHandler
from pyrogram.filters import channel
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from pyrogram.errors import FloodWait

from database import db
from userbot import get_client, _clients
from utils import process_text

logger = logging.getLogger(__name__)

_handlers: dict[str, MessageHandler] = {}
_rule_map: dict[str, dict] = {}
_source_map: dict[int, list[str]] = {}


# ─────────────────────── Unicode Stylish Font Normalizer ─────────────────
# Telegram mein stylish fonts asal mein Unicode Mathematical letters hain
# e.g. 𝐌 (U+1D400) → M, 𝕄 (U+1D544) → M, 𝓜 (U+1D4DC) → M

_UNICODE_TO_ASCII = {}

def _build_unicode_map():
    """Mathematical Unicode block characters ko normal ASCII mein map karo."""
    ranges = [
        (0x1D400, 0x1D419, 'A'),  # Bold
        (0x1D434, 0x1D44D, 'A'),  # Italic
        (0x1D468, 0x1D481, 'A'),  # Bold Italic
        (0x1D49C, None,    'A'),  # Script (sparse)
        (0x1D4D0, 0x1D4E9, 'A'),  # Bold Script
        (0x1D504, 0x1D51C, 'A'),  # Fraktur (sparse)
        (0x1D538, 0x1D551, 'A'),  # Double-struck (sparse)
        (0x1D56C, 0x1D585, 'A'),  # Bold Fraktur
        (0x1D5A0, 0x1D5B9, 'A'),  # Sans-serif
        (0x1D5D4, 0x1D5ED, 'A'),  # Sans-serif Bold
        (0x1D608, 0x1D621, 'A'),  # Sans-serif Italic
        (0x1D63C, 0x1D655, 'A'),  # Sans-serif Bold Italic
        (0x1D670, 0x1D689, 'A'),  # Monospace
    ]
    for start, end, base in ranges:
        if end is None:
            continue
        for i in range(end - start + 1):
            char = chr(start + i)
            ascii_char = chr(ord(base) + i)
            if ascii_char.isalpha():
                _UNICODE_TO_ASCII[char] = ascii_char

    lower_ranges = [
        (0x1D41A, 0x1D433, 'a'),  # Bold
        (0x1D44E, 0x1D467, 'a'),  # Italic
        (0x1D482, 0x1D49B, 'a'),  # Bold Italic
        (0x1D4EA, 0x1D503, 'a'),  # Bold Script
        (0x1D586, 0x1D59F, 'a'),  # Bold Fraktur
        (0x1D5BA, 0x1D5D3, 'a'),  # Sans-serif
        (0x1D5EE, 0x1D607, 'a'),  # Sans-serif Bold
        (0x1D622, 0x1D63B, 'a'),  # Sans-serif Italic
        (0x1D656, 0x1D66F, 'a'),  # Sans-serif Bold Italic
        (0x1D68A, 0x1D6A3, 'a'),  # Monospace
    ]
    for start, end, base in lower_ranges:
        for i in range(end - start + 1):
            char = chr(start + i)
            ascii_char = chr(ord(base) + i)
            if ascii_char.isalpha():
                _UNICODE_TO_ASCII[char] = ascii_char

_build_unicode_map()


def normalize_unicode_text(text: str) -> str:
    """
    Stylish Unicode mathematical fonts ko normal ASCII mein convert karo.
    e.g. "𝐌𝐎𝐍𝐃𝐀𝐘" → "MONDAY", "𝓜𝓸𝓷𝓭𝓪𝔂" → "Monday"
    """
    result = []
    for char in text:
        if char in _UNICODE_TO_ASCII:
            result.append(_UNICODE_TO_ASCII[char])
        else:
            result.append(char)
    text = "".join(result)

    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(
        c for c in normalized
        if unicodedata.category(c) != 'Mn'
    )
    return ascii_text


def _check_trigger_words(text: str, trigger_words: list) -> bool:
    """
    Message mein trigger words check karo.
    - Case insensitive
    - Stylish Unicode font support
    - Partial match bhi chalega
    """
    if not trigger_words:
        return True  # Koi trigger set nahi → sab messages forward karo

    normalized = normalize_unicode_text(text).lower()

    for word in trigger_words:
        word_lower = word.lower().strip()
        if word_lower and word_lower in normalized:
            return True

    return False


def _should_forward(raw_text: str, rule: dict) -> bool:
    """
    Step 6 + Step 7 combined check:

    specific_forward_mode:
      - None         → trigger_words check se decide hoga (Step 7)
      - "forward"    → sirf trigger_words wale messages forward karo
      - "not_forward"→ trigger_words wale messages SKIP karo, baaki forward karo

    trigger_words:
      - [] (empty)   → sabke liye True (koi filter nahi)
      - [words...]   → match check karo
    """
    mode         = rule.get("specific_forward_mode")  # None / "forward" / "not_forward"
    trigger_words = rule.get("trigger_words", [])

    # Agar koi bhi filter set nahi → sab forward karo
    if not mode and not trigger_words:
        return True

    trigger_matched = _check_trigger_words(raw_text, trigger_words)

    if mode == "forward":
        # Sirf trigger match wale forward karo
        return trigger_matched

    elif mode == "not_forward":
        # Trigger match wale SKIP karo, baaki forward karo
        return not trigger_matched

    else:
        # mode = None, lekin trigger_words set hain → old behavior
        return trigger_matched


# ─────────────────────────────────────────────────────────────────────────

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

        for oid, slots in _clients.items():
            for client in slots.values():
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
            for slots in _clients.values():
                for client in slots.values():
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
    targets           = rule["targets"]
    affiliate_bot     = rule.get("affiliate_bot")
    hide_links        = rule.get("hide_links", False)
    hide_usernames    = rule.get("hide_usernames", False)
    replace_map       = rule.get("replace_map", {})
    remove_thumbnail  = rule.get("remove_thumbnail", False)   # NEW
    bold_text         = rule.get("bold_text", False)           # NEW

    # ── Step 6 + 7: Forward filter check ────────────────────
    raw_text = message.text or message.caption or ""

    if not _should_forward(raw_text, rule):
        logger.debug(f"Message skipped by forward filter. Rule: {rule['rule_id']}")
        return

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

    # ── Text process karo ────────────────────────────────────
    new_text = process_text(
        raw_text,
        hide_links=hide_links,
        hide_usernames=hide_usernames,
        replace_map=replace_map,
        bold_text=bold_text,        # NEW
    )

    # ── Forward to each target ───────────────────────────────
    for target in targets:
        await _forward_one(
            client, message, target, new_text,
            remove_thumbnail=remove_thumbnail,
            bold_text=bold_text,
        )
        await asyncio.sleep(1)


def _has_media(message: Message) -> bool:
    """Check karo ki message mein koi bhi media hai ya nahi."""
    return bool(
        message.media
        or message.photo
        or message.video
        or message.document
        or message.animation
        or message.sticker
        or message.audio
        or message.voice
        or message.video_note
    )


def _has_thumbnail_support(message: Message) -> bool:
    """
    Sirf in media types ka thumbnail remove ho sakta hai:
    photo, video, document.
    Baaki (sticker, voice, video_note, animation) mein thumbnail nahi hota.
    """
    return bool(message.photo or message.video or message.document)


async def _forward_one(
    client: Client,
    message: Message,
    target: int,
    new_text: str,
    remove_thumbnail: bool = False,
    bold_text: bool = False,
):
    """
    Ek target pe message forward karo.

    remove_thumbnail=True:
      - Photo → send_photo with thumb=None (no thumbnail)
      - Video → copy_message with no_video_preview (Pyrogram limitation se
                seedha file download + re-upload karna padega for full thumb remove)
      - Document → copy_message (thumb already nahi hota usually)
      - Text-only → normal send

    Bold text:
      - process_text() se already <b>...</b> wrap ho chuka hota hai
      - parse_mode="html" pass karna zaroori hai
    """
    parse_mode = "html" if bold_text else None

    try:
        if not _has_media(message):
            # Pure text message
            if new_text:
                await client.send_message(target, new_text, parse_mode=parse_mode)
            return

        # Media message
        if remove_thumbnail and _has_thumbnail_support(message):
            # Thumbnail remove flow
            caption = new_text or None

            if message.photo:
                # Photo = hi ek tarah ka thumbnail hota hai
                # Photo ko as document send karo (thumbnail remove)
                await client.send_document(
                    chat_id=target,
                    document=message.photo.file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    force_document=True,
                )

            elif message.video:
                # Video thumbnail remove: copy + thumb=None
                # Pyrogram copy_message mein thumb override nahi hota
                # Isliye send_video with file_id use karo
                await client.send_video(
                    chat_id=target,
                    video=message.video.file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                    thumb=None,          # thumbnail nahi bheja
                    supports_streaming=True,
                )

            elif message.document:
                # Document mein thumb override karna possible nahi Pyrogram mein
                # Isliye normal copy karo, sirf caption update karo
                await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                    caption=caption,
                    parse_mode=parse_mode,
                )

        else:
            # Normal copy (thumbnail as-is)
            caption = new_text or None

            if message.photo:
                # Photo ke liye send_photo use karo — copy_message kabhi kabhi
                # sirf caption forward karta hai, photo nahi. Yeh fix hai.
                await client.send_photo(
                    chat_id=target,
                    photo=message.photo.file_id,
                    caption=caption,
                    parse_mode=parse_mode,
                )
            else:
                await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                    caption=caption,
                    parse_mode=parse_mode,
                )

    except FloodWait as e:
        await asyncio.sleep(e.value + 1)
        await _forward_one(client, message, target, new_text, remove_thumbnail, bold_text)
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
    bot_id ab integer hai (username nahi).
    Channel messages ko directly forward nahi kar sakte private bot pe.
    Solution: Message ka text/caption seedha send karo.
    """
    if isinstance(bot_id, str):
        try:
            bot_chat = await client.get_chat(bot_id)
            bot_id   = bot_chat.id
        except Exception as e:
            logger.error(f"Affiliate bot resolve failed: {e}")
            return None

    replied = asyncio.get_event_loop().create_future()

    async def _wait_reply(_: Client, msg: Message):
        if msg.chat.id == bot_id and not replied.done():
            replied.set_result(msg)

    from pyrogram.handlers import MessageHandler as MH
    h = MH(_wait_reply)
    client.add_handler(h)

    try:
        text_to_send = original.text or original.caption or ""

        if original.photo:
            # Photo+caption dono bhejo — pehle sirf text jaata tha, photo nahi
            await client.send_photo(
                chat_id=bot_id,
                photo=original.photo.file_id,
                caption=text_to_send or None,
            )
        elif text_to_send:
            await client.send_message(bot_id, text_to_send)
        elif original.media:
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
        logger.warning(f"Affiliate bot {bot_id} did not reply in time.")
        return None
    finally:
        client.remove_handler(h)


def attach_client_to_all_handlers(client: Client):
    for h in _handlers.values():
        client.add_handler(h)
