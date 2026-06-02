"""
Auto-Listener — v8
===================
Changes from v7:
1. FIX: `_next_group` ab 100 se start — router/plugin groups (0,1) se
   koi conflict nahi hoga.
2. FIX: `_on_new_message` mein fresh rule se sirf mutable fields update
   hote hain — `owner_id` aur `source_ids` preserved rehte hain.
3. FIX: `unregister_rule` — agar ek source pe multiple rules hain toh
   handler sirf tab remove hoga jab source ke SAARE rules remove ho jayein.
4. FIX: `_warmup_all_clients` — source resolve delay kam kiya, errors
   silently ignore hote hain (non-critical).
5. FIX: `attach_client_to_all_handlers` — har key ka apna group use karta
   hai (no default=10 collision).
"""
import asyncio
import logging
import unicodedata
import time

from pyrogram import Client, enums, filters
from pyrogram.handlers import MessageHandler, EditedMessageHandler
from pyrogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from pyrogram.errors import FloodWait, PeerIdInvalid, ChatForwardsRestricted

from database import db
from userbot import _clients
from utils import process_text

logger = logging.getLogger(__name__)

_handlers:       dict[str, MessageHandler]        = {}
_edit_handlers:  dict[str, EditedMessageHandler]  = {}
_handler_groups: dict[str, int]                   = {}
_rule_map:       dict[str, dict]                  = {}
_source_map:     dict[int, list[str]]             = {}

# FIX #1: 100 se start — plugin/router groups (0,1,2) se door
_next_group: int = 100

# Media group buffer: { "chat_id:media_group_id:rule_id": [Message, ...] }
_media_group_buffer: dict[str, list[Message]] = {}
_media_group_tasks:  dict[str, asyncio.Task]  = {}
MEDIA_GROUP_WAIT = 1.5

# Edit tracking:
# { "source_chat_id:source_msg_id": [{rule_id, target_chat_id, target_msg_id, sent_at}, ...] }
_edit_tracker: dict[str, list[dict]] = {}
EDIT_TRACK_WINDOW = 30 * 60  # 30 minutes


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

    if not mode:
        return True
    if not trigger_words:
        return True

    matched = _check_trigger_words(raw_text, trigger_words)

    if mode == "forward":
        return matched
    elif mode == "not_forward":
        return not matched

    return True


# ─────────────────────── Edit Tracker Helpers ────────────────────────────

def _register_edit_track(source_chat_id: int, source_msg_id: int,
                          rule_id: str, target_chat_id: int, target_msg_id: int):
    key = f"{source_chat_id}:{source_msg_id}"
    if key not in _edit_tracker:
        _edit_tracker[key] = []
    _edit_tracker[key].append({
        "rule_id":        rule_id,
        "target_chat_id": target_chat_id,
        "target_msg_id":  target_msg_id,
        "sent_at":        time.time(),
    })

def _cleanup_edit_tracker():
    now = time.time()
    expired_keys = []
    for key, entries in _edit_tracker.items():
        fresh = [e for e in entries if now - e["sent_at"] < EDIT_TRACK_WINDOW]
        if fresh:
            _edit_tracker[key] = fresh
        else:
            expired_keys.append(key)
    for k in expired_keys:
        del _edit_tracker[k]


# ─────────────────────── Custom Filter ───────────────────────────────────

def _make_source_filter(src_id: int):
    """
    Custom filter: sirf uss specific source channel ke messages pass kare.
    Admin restriction nahi — non-admin channels bhi kaam karte hain.
    """
    async def _filter_fn(_, __, message: Message) -> bool:
        return message.chat is not None and message.chat.id == src_id

    return filters.create(_filter_fn, name=f"SourceFilter_{src_id}")


# ─────────────────────── Lifecycle ───────────────────────────────────────

async def start_listeners():
    all_rules = await db.get_all_rules()
    for rule in all_rules:
        owner_id = rule["owner_id"]
        await register_rule(owner_id, rule, _startup=True)
    logger.info(f"Auto-listeners started for {len(all_rules)} rules.")
    await _warmup_all_clients()


async def _warmup_all_clients():
    """
    Redeploy fix: Telegram ko signal karo ki client ready hai.
    Source IDs snapshot PEHLE liya — register_rule ke baad.
    """
    from userbot import _clients as all_clients
    all_source_ids = list(_source_map.keys())  # snapshot after register

    for owner_id, slots in all_clients.items():
        for slot, client in slots.items():
            try:
                count = 0
                async for _ in client.get_dialogs(limit=10):
                    count += 1
                    if count >= 10:
                        break

                for src_id in all_source_ids:
                    try:
                        await client.get_chat(src_id)
                    except Exception:
                        pass
                    await asyncio.sleep(0.2)

                logger.info(f"Warmup done: owner={owner_id} slot={slot}")
            except Exception as e:
                logger.warning(f"Warmup failed for owner={owner_id} slot={slot}: {e}")


async def register_rule(owner_id: int, rule: dict, _startup: bool = False):
    rule_id    = rule["rule_id"]

    source_ids = rule.get("source_ids") or []
    if not source_ids and rule.get("source_id"):
        source_ids = [rule["source_id"]]

    # Purane source se handlers clean karo agar source change hua
    if rule_id in _rule_map:
        old_source_ids = _rule_map[rule_id].get("source_ids") or []
        if not old_source_ids and _rule_map[rule_id].get("source_id"):
            old_source_ids = [_rule_map[rule_id]["source_id"]]
        for old_src in old_source_ids:
            if old_src not in source_ids:
                old_lst = _source_map.get(old_src, [])
                if rule_id in old_lst:
                    old_lst.remove(rule_id)
                # FIX #3: Sirf tab handler remove karo jab source ke SAARE rules gone hain
                if not old_lst:
                    old_key = f"src_{old_src}"
                    h_old  = _handlers.pop(old_key, None)
                    he_old = _edit_handlers.pop(old_key, None)
                    old_grp = _handler_groups.pop(old_key, None)
                    if old_grp is not None:
                        for slots in _clients.values():
                            for c in slots.values():
                                try:
                                    if h_old:  c.remove_handler(h_old, old_grp)
                                    if he_old: c.remove_handler(he_old, old_grp)
                                except Exception:
                                    pass

    _rule_map[rule_id] = {**rule, "owner_id": owner_id, "source_ids": source_ids}

    for source_id in source_ids:
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

            src_filter = _make_source_filter(source_id)

            async def _make_handlers(src_id: int, s_filter):
                async def new_msg_handler(client: Client, message: Message):
                    await _on_new_message(client, message, src_id)

                async def edit_msg_handler(client: Client, message: Message):
                    await _on_edited_message(client, message, src_id)

                return (
                    MessageHandler(new_msg_handler, filters=s_filter),
                    EditedMessageHandler(edit_msg_handler, filters=s_filter),
                )

            h, he = await _make_handlers(source_id, src_filter)
            _handlers[handler_key]      = h
            _edit_handlers[handler_key] = he

            for oid, slots in _clients.items():
                for c in slots.values():
                    try:
                        c.add_handler(h, grp)
                        c.add_handler(he, grp)
                    except Exception:
                        pass

    logger.info(f"Rule {rule_id} registered (sources={source_ids}, owner={owner_id})")


def unregister_rule(rule_id: str):
    rule = _rule_map.pop(rule_id, None)
    if not rule:
        return
    source_ids = rule.get("source_ids") or []
    if not source_ids and rule.get("source_id"):
        source_ids = [rule["source_id"]]

    for source_id in source_ids:
        lst = _source_map.get(source_id, [])
        if rule_id in lst:
            lst.remove(rule_id)
        # FIX #3: Sirf tab handler remove karo jab is source ke koi rule nahi bacha
        if not lst:
            handler_key = f"src_{source_id}"
            h   = _handlers.pop(handler_key, None)
            he  = _edit_handlers.pop(handler_key, None)
            grp = _handler_groups.pop(handler_key, None)
            if grp is not None:
                for slots in _clients.values():
                    for c in slots.values():
                        try:
                            if h:  c.remove_handler(h, grp)
                            if he: c.remove_handler(he, grp)
                        except Exception:
                            pass
    logger.info(f"Rule {rule_id} unregistered")


# ─────────────────────── New Message Handler ─────────────────────────────

async def _on_new_message(client: Client, message: Message, source_id: int):
    rule_ids = list(_source_map.get(source_id, []))
    for rule_id in rule_ids:
        rule = _rule_map.get(rule_id)
        if not rule:
            stale = _source_map.get(source_id, [])
            if rule_id in stale:
                stale.remove(rule_id)
            continue

        # FIX #2: Fresh DB se sirf config fields update karo
        # owner_id aur source_ids cached rule se preserve karo
        try:
            fresh_rule = await db.get_rule_by_id(rule_id)
            if fresh_rule:
                # Critical fields cached se preserve karo
                fresh_rule["owner_id"]   = rule["owner_id"]
                fresh_rule["source_ids"] = rule["source_ids"]
                _rule_map[rule_id] = fresh_rule
                rule = fresh_rule
        except Exception as e:
            logger.warning(f"DB fetch failed for rule {rule_id}, using cached: {e}")

        owner_id    = rule["owner_id"]
        owner_slots = _clients.get(owner_id, {})
        use_client  = next(iter(owner_slots.values()), client)

        if message.media_group_id:
            buf_key = f"{message.chat.id}:{message.media_group_id}:{rule_id}"
            _media_group_buffer.setdefault(buf_key, []).append(message)
            old_task = _media_group_tasks.get(buf_key)
            if old_task and not old_task.done():
                old_task.cancel()
            task = asyncio.create_task(
                _flush_media_group(use_client, buf_key, rule)
            )
            _media_group_tasks[buf_key] = task
        else:
            asyncio.create_task(_process_and_forward(use_client, message, rule))


# ─────────────────────── Edited Message Handler ──────────────────────────

async def _on_edited_message(client: Client, message: Message, source_id: int):
    _cleanup_edit_tracker()

    key = f"{source_id}:{message.id}"
    entries = _edit_tracker.get(key)
    if not entries:
        return

    now = time.time()
    for entry in entries:
        if now - entry["sent_at"] > EDIT_TRACK_WINDOW:
            continue

        rule_id        = entry["rule_id"]
        target_chat_id = entry["target_chat_id"]
        target_msg_id  = entry["target_msg_id"]

        rule = _rule_map.get(rule_id)
        if not rule:
            continue

        owner_id    = rule.get("owner_id")
        owner_slots = _clients.get(owner_id, {})
        use_client  = next(iter(owner_slots.values()), client)

        raw_text = message.text or message.caption or ""
        new_text = process_text(
            raw_text,
            hide_links=rule.get("hide_links", False),
            hide_usernames=rule.get("hide_usernames", False),
            replace_map=rule.get("replace_map") or {},
            bold_text=rule.get("bold_text", False),
            remove_words=rule.get("remove_words") or [],
        )

        parse_mode = enums.ParseMode.HTML if rule.get("bold_text") else enums.ParseMode.DISABLED

        try:
            if message.photo or message.video or message.document or message.audio:
                await use_client.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    caption=new_text or "",
                    parse_mode=parse_mode,
                )
            else:
                await use_client.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    text=new_text or raw_text,
                    parse_mode=parse_mode,
                )
            logger.info(f"Edit synced: source {source_id}:{message.id} → target {target_chat_id}:{target_msg_id}")
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.error(f"Edit sync failed for target {target_chat_id}:{target_msg_id}: {e}")


# ─────────────────────── Media Group Flush ───────────────────────────────

async def _flush_media_group(client: Client, buf_key: str, rule: dict):
    try:
        await asyncio.sleep(MEDIA_GROUP_WAIT)
    except asyncio.CancelledError:
        return

    messages = _media_group_buffer.pop(buf_key, [])
    _media_group_tasks.pop(buf_key, None)

    if not messages:
        return

    messages.sort(key=lambda m: m.id)

    caption_msg = None
    for m in messages:
        if m.caption or m.text:
            caption_msg = m
            break
    if caption_msg is None:
        caption_msg = messages[-1]

    raw_text = caption_msg.caption or caption_msg.text or ""

    if not _should_forward(raw_text, rule):
        return

    hide_links       = rule.get("hide_links", False)
    hide_usernames   = rule.get("hide_usernames", False)
    replace_map      = rule.get("replace_map") or {}
    remove_thumbnail = rule.get("remove_thumbnail", False)
    bold_text        = rule.get("bold_text", False)
    remove_words     = rule.get("remove_words") or []
    targets          = rule["targets"]

    new_text = process_text(
        raw_text,
        hide_links=hide_links,
        hide_usernames=hide_usernames,
        replace_map=replace_map,
        bold_text=bold_text,
        remove_words=remove_words,
    )

    parse_mode = enums.ParseMode.HTML if bold_text else enums.ParseMode.DISABLED
    has_text   = bool(new_text and new_text.strip())

    for target in targets:
        try:
            if remove_thumbnail:
                if has_text:
                    await client.send_message(target, new_text, parse_mode=parse_mode)
            else:
                media_list = []
                for i, msg in enumerate(messages):
                    cap      = (new_text if has_text else None) if msg.id == caption_msg.id else None
                    cap_mode = parse_mode if cap else enums.ParseMode.DISABLED

                    if msg.photo:
                        media_list.append(InputMediaPhoto(
                            media=msg.photo.file_id, caption=cap, parse_mode=cap_mode))
                    elif msg.video:
                        media_list.append(InputMediaVideo(
                            media=msg.video.file_id, caption=cap, parse_mode=cap_mode,
                            supports_streaming=True))
                    elif msg.document:
                        media_list.append(InputMediaDocument(
                            media=msg.document.file_id, caption=cap, parse_mode=cap_mode))
                    elif msg.audio:
                        media_list.append(InputMediaAudio(
                            media=msg.audio.file_id, caption=cap, parse_mode=cap_mode))

                if media_list:
                    await client.send_media_group(chat_id=target, media=media_list)
                elif has_text:
                    await client.send_message(target, new_text, parse_mode=parse_mode)

        except PeerIdInvalid:
            logger.error(f"❌ Peer invalid: {target}")
        except ChatForwardsRestricted:
            # Media group mein file_id se hi send hota hai — yeh error nahi aana chahiye
            # Agar aaya toh individual messages ko _send_restricted_media se bhejo
            logger.info(f"Media group forward-restricted for target {target} — individual fallback")
            parse_mode_mg = enums.ParseMode.HTML if bold_text else enums.ParseMode.DISABLED
            for msg in messages:
                cap = (new_text if has_text else None) if msg.id == caption_msg.id else None
                await _send_restricted_media(client, msg, target,
                                             caption=cap, parse_mode=parse_mode_mg)
                await asyncio.sleep(0.5)
        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s for target {target}")
            await asyncio.sleep(e.value + 1)
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
        rule.get("remove_thumbnail") or
        rule.get("remove_words")
    )


async def _process_and_forward(client: Client, message: Message, rule: dict):
    targets          = rule["targets"]
    affiliate_bot    = rule.get("affiliate_bot")
    hide_links       = rule.get("hide_links", False)
    hide_usernames   = rule.get("hide_usernames", False)
    replace_map      = rule.get("replace_map") or {}
    remove_thumbnail = rule.get("remove_thumbnail", False)
    bold_text        = rule.get("bold_text", False)
    remove_words     = rule.get("remove_words") or []

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
        remove_words=remove_words,
    )

    rule_id = rule.get("rule_id", "")
    is_only_forward_mode = rule.get("specific_forward_mode") == "forward"

    for target in targets:
        sent_msg = await _forward_one(
            client, message, target,
            new_text=new_text,
            remove_thumbnail=remove_thumbnail,
            bold_text=bold_text,
            needs_processing=_needs_processing(rule),
        )
        if sent_msg and is_only_forward_mode and rule_id:
            _register_edit_track(
                source_chat_id=message.chat.id,
                source_msg_id=message.id,
                rule_id=rule_id,
                target_chat_id=target,
                target_msg_id=sent_msg.id,
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
) -> Message | None:
    has_text   = bool(new_text and new_text.strip())
    parse_mode = enums.ParseMode.HTML if bold_text else enums.ParseMode.DISABLED

    try:
        if remove_thumbnail:
            if has_text:
                return await client.send_message(target, new_text, parse_mode=parse_mode)
            return None

        if not _has_media(message):
            if has_text:
                return await client.send_message(target, new_text, parse_mode=parse_mode)
            return None

        if not needs_processing:
            try:
                return await client.copy_message(
                    chat_id=target,
                    from_chat_id=message.chat.id,
                    message_id=message.id,
                )
            except ChatForwardsRestricted:
                logger.info(f"Source forward-restricted — file_id fallback for target {target}")
                return await _send_restricted_media(
                    client, message, target,
                    caption=new_text if has_text else None,
                    parse_mode=parse_mode,
                )

        caption  = new_text if has_text else None
        cap_mode = parse_mode if caption else enums.ParseMode.DISABLED

        if message.photo:
            return await client.send_photo(target, message.photo.file_id,
                                           caption=caption, parse_mode=cap_mode)
        elif message.video:
            return await client.send_video(target, message.video.file_id,
                                           caption=caption, parse_mode=cap_mode,
                                           supports_streaming=True)
        elif message.audio:
            return await client.send_audio(target, message.audio.file_id,
                                           caption=caption, parse_mode=cap_mode)
        elif message.document:
            return await client.send_document(target, message.document.file_id,
                                              caption=caption, parse_mode=cap_mode)
        elif message.animation:
            return await client.send_animation(target, message.animation.file_id,
                                               caption=caption, parse_mode=cap_mode)
        elif message.voice:
            return await client.send_voice(target, message.voice.file_id,
                                           caption=caption, parse_mode=cap_mode)
        else:
            try:
                return await client.copy_message(target, message.chat.id, message.id)
            except ChatForwardsRestricted:
                return await _send_restricted_media(
                    client, message, target,
                    caption=new_text if has_text else None,
                    parse_mode=parse_mode,
                )

    except PeerIdInvalid:
        logger.error(f"❌ Peer invalid: {target} — Userbot ne channel join nahi kiya.")
    except ChatForwardsRestricted:
        # Outer safety net
        logger.info(f"Source forward-restricted (outer catch) — file_id fallback for target {target}")
        return await _send_restricted_media(
            client, message, target,
            caption=new_text if has_text else None,
            parse_mode=parse_mode,
        )
    except FloodWait as e:
        logger.warning(f"FloodWait {e.value}s for target {target}")
        await asyncio.sleep(e.value + 1)
        return await _forward_one(client, message, target,
                                  new_text=new_text,
                                  remove_thumbnail=remove_thumbnail,
                                  bold_text=bold_text,
                                  needs_processing=needs_processing)
    except Exception as e:
        logger.error(f"Forward to {target} failed: {e}")
    return None


async def _send_restricted_media(
    client: Client,
    message: Message,
    target: int,
    caption: str | None = None,
    parse_mode=enums.ParseMode.DISABLED,
) -> "Message | None":
    """
    Forward-restricted source ke liye fallback:
    file_id se direct send karta hai (no copy/forward).
    Agar file_id bhi kaam na kare to download karke re-upload.
    """
    cap_mode = parse_mode if caption else enums.ParseMode.DISABLED
    try:
        if message.photo:
            return await client.send_photo(target, message.photo.file_id,
                                           caption=caption, parse_mode=cap_mode)
        elif message.video:
            return await client.send_video(target, message.video.file_id,
                                           caption=caption, parse_mode=cap_mode,
                                           supports_streaming=True)
        elif message.audio:
            return await client.send_audio(target, message.audio.file_id,
                                           caption=caption, parse_mode=cap_mode)
        elif message.document:
            return await client.send_document(target, message.document.file_id,
                                              caption=caption, parse_mode=cap_mode)
        elif message.animation:
            return await client.send_animation(target, message.animation.file_id,
                                               caption=caption, parse_mode=cap_mode)
        elif message.voice:
            return await client.send_voice(target, message.voice.file_id,
                                           caption=caption, parse_mode=cap_mode)
        elif message.video_note:
            return await client.send_video_note(target, message.video_note.file_id)
        elif message.sticker:
            return await client.send_sticker(target, message.sticker.file_id)
        elif message.text or caption:
            return await client.send_message(target, caption or message.text,
                                             parse_mode=cap_mode)
        else:
            logger.warning(f"_send_restricted_media: unknown media type for msg {message.id}")
            return None
    except Exception as e:
        logger.error(f"_send_restricted_media file_id failed ({e}), trying download re-upload")
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                dl_path = await client.download_media(message, file_name=tmpdir + "/")
                if not dl_path:
                    return None
                if message.photo:
                    return await client.send_photo(target, dl_path,
                                                   caption=caption, parse_mode=cap_mode)
                elif message.video:
                    return await client.send_video(target, dl_path,
                                                   caption=caption, parse_mode=cap_mode,
                                                   supports_streaming=True)
                elif message.audio:
                    return await client.send_audio(target, dl_path,
                                                   caption=caption, parse_mode=cap_mode)
                else:
                    return await client.send_document(target, dl_path,
                                                      caption=caption, parse_mode=cap_mode)
        except Exception as e2:
            logger.error(f"_send_restricted_media download fallback also failed: {e2}")
            return None


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
    """
    FIX #5: Har handler ka apna correct group use karo.
    Default 10 nahi — proper _handler_groups lookup.
    """
    for handler_key, h in _handlers.items():
        grp = _handler_groups.get(handler_key)
        if grp is None:
            continue
        try:
            client.add_handler(h, grp)
        except Exception:
            pass
    for handler_key, he in _edit_handlers.items():
        grp = _handler_groups.get(handler_key)
        if grp is None:
            continue
        try:
            client.add_handler(he, grp)
        except Exception:
            pass
