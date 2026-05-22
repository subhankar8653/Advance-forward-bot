"""
worker_bots.py — Multiple Worker Bots Manager

System:
  - Owner apne bots ke tokens add kar sakta hai DB mein
  - Har bot ek independent Pyrogram Client hai
  - Manual forward mein saare bots parallel kaam karte hain
  - Har bot ko ek chunk of messages assign hota hai

Flow Example (3 bots, 300 messages):
  Bot 0 → Messages 1-100   (parallel)
  Bot 1 → Messages 101-200 (parallel)
  Bot 2 → Messages 201-300 (parallel)
  = 3x speed!
"""
import asyncio
import logging
from pyrogram import Client
from pyrogram.errors import FloodWait
from config import Config
from database import db

logger = logging.getLogger(__name__)

# { owner_id: { slot: Client } }
_worker_clients: dict[int, dict[int, Client]] = {}


# ─────────────────────── Public API ──────────────────────────────────────

async def get_all_workers(owner_id: int) -> list[tuple[int, Client]]:
    """[(slot, Client), ...] sorted by slot."""
    slots = _worker_clients.get(owner_id, {})
    return sorted(slots.items())


async def get_worker_count(owner_id: int) -> int:
    return len(_worker_clients.get(owner_id, {}))


# ─────────────────────── Startup ─────────────────────────────────────────

async def start_all_workers():
    """Bot start hone pe DB se saare worker bot tokens load karo."""
    records = await db.get_all_worker_records()
    for rec in records:
        owner_id = rec["owner_id"]
        slot     = rec.get("slot", 0)
        token    = rec["token"]
        name     = rec.get("name", f"worker_{slot}")
        try:
            client = Client(
                name=f"worker_{owner_id}_{slot}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                bot_token=token,
                in_memory=True
            )
            await client.start()
            if owner_id not in _worker_clients:
                _worker_clients[owner_id] = {}
            _worker_clients[owner_id][slot] = client
            me = await client.get_me()
            logger.info(f"Worker bot started: owner={owner_id} slot={slot} @{me.username}")
        except Exception as e:
            logger.error(f"Could not start worker bot owner={owner_id} slot={slot}: {e}")


# ─────────────────────── Add Worker Bot ──────────────────────────────────

async def add_worker(owner_id: int, token: str) -> str:
    """
    Naya worker bot add karo token se.
    Token validate karo, phir DB mein save karo.
    """
    # Check: duplicate token?
    existing = await db.get_all_workers(owner_id)
    for w in existing:
        if w["token"] == token:
            return "❌ Yeh token pehle se add hai!"

    # Token validate karo — ek temp client bana ke check karo
    try:
        test_client = Client(
            name=f"test_{owner_id}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=token,
            in_memory=True
        )
        await test_client.start()
        me = await test_client.get_me()
        bot_name     = me.first_name
        bot_username = me.username
        await test_client.stop()
    except Exception as e:
        return f"❌ Invalid token ya bot start error: {e}"

    # DB mein save karo
    slot = await db.save_worker(owner_id, token, bot_name, bot_username)

    # Live client start karo
    live_client = Client(
        name=f"worker_{owner_id}_{slot}",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=token,
        in_memory=True
    )
    try:
        await live_client.start()
    except Exception as e:
        # ✅ FIX: Orphan record cleanup — agar live start fail ho toh DB se bhi hatao
        await db.delete_worker_by_slot(owner_id, slot)
        return f"❌ Live client start failed: {e}"

    if owner_id not in _worker_clients:
        _worker_clients[owner_id] = {}
    _worker_clients[owner_id][slot] = live_client

    total = len(_worker_clients[owner_id])
    return (
        f"✅ **Worker Bot Add Ho Gaya!**\n\n"
        f"🤖 Name: **{bot_name}**\n"
        f"📛 Username: @{bot_username}\n"
        f"🔢 Slot: `{slot}`\n"
        f"📊 Total worker bots: `{total}`"
    )


# ─────────────────────── Remove Worker Bot ───────────────────────────────

async def remove_worker(owner_id: int, slot: int) -> str:
    """Ek worker bot remove karo."""
    slots_dict = _worker_clients.get(owner_id, {})
    client = slots_dict.pop(slot, None)

    if client:
        try:
            await client.stop()
        except Exception:
            pass

    result = await db.delete_worker_by_slot(owner_id, slot)
    if result:
        # Re-number remaining slots in memory
        new_dict = {i: c for i, (_, c) in enumerate(sorted(slots_dict.items()))}
        _worker_clients[owner_id] = new_dict
        return f"✅ Worker bot slot {slot} remove ho gaya."
    return f"❌ Slot {slot} nahi mila."


async def remove_all_workers(owner_id: int) -> str:
    """Saare worker bots remove karo."""
    slots_dict = _worker_clients.pop(owner_id, {})
    for client in slots_dict.values():
        try:
            await client.stop()
        except Exception:
            pass
    await db.delete_all_workers(owner_id)
    return f"✅ Saare {len(slots_dict)} worker bot(s) remove ho gaye."


# ─────────────────────── Chunk Splitter ──────────────────────────────────

def split_into_chunks(msg_ids: list[int], n_workers: int) -> list[list[int]]:
    """
    Message IDs ko n_workers mein barabar baant do.

    Example: 300 messages, 3 workers
      Worker 0 → [msg 1 .. 100]
      Worker 1 → [msg 101 .. 200]
      Worker 2 → [msg 201 .. 300]
    """
    if n_workers <= 0:
        return [msg_ids]

    total = len(msg_ids)
    chunk_size = max(1, (total + n_workers - 1) // n_workers)
    chunks = []
    for i in range(0, total, chunk_size):
        chunks.append(msg_ids[i:i + chunk_size])
    return chunks
