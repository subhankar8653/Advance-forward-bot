"""
Userbot login & multi-client management.
Ek owner ke paas multiple accounts ho sakte hain (slot 0, 1, 2...).
Forwarding mein saare accounts parallel kaam karte hain.
"""
import os
import asyncio
import logging
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid,
    PhoneCodeExpired, SessionPasswordNeeded,
    FloodWait
)
from config import Config
from database import db

logger = logging.getLogger(__name__)

# { owner_id: { slot: Client } }
_clients: dict[int, dict[int, Client]] = {}


# ─────────────────────── Public API ──────────────────────────────────────

async def get_client(owner_id: int) -> Client | None:
    """Pehla available client return karo (slot 0). Backward compat."""
    slots = _clients.get(owner_id, {})
    if not slots:
        return None
    return slots.get(0) or next(iter(slots.values()))


async def get_all_clients(owner_id: int) -> list[tuple[int, Client]]:
    """
    [(slot, Client), ...] sorted by slot.
    Forwarding mein saare use hote hain.
    """
    slots = _clients.get(owner_id, {})
    return sorted(slots.items())


async def get_client_count(owner_id: int) -> int:
    return len(_clients.get(owner_id, {}))


# ─────────────────────── Startup ─────────────────────────────────────────

async def start_all_clients():
    """Bot start hone pe DB se saare sessions load karo."""
    records = await db.get_all_userbot_records()
    for rec in records:
        owner_id = rec["owner_id"]
        slot     = rec.get("slot", 0)
        session  = rec["session"]
        try:
            client = Client(
                name=f"user_{owner_id}_{slot}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                session_string=session,
                in_memory=True
            )
            await client.start()
            if owner_id not in _clients:
                _clients[owner_id] = {}
            _clients[owner_id][slot] = client
            me = await client.get_me()
            logger.info(f"Userbot started: owner={owner_id} slot={slot} name={me.first_name}")
        except Exception as e:
            logger.error(f"Could not start userbot owner={owner_id} slot={slot}: {e}")


# ─────────────────────── Logout ──────────────────────────────────────────

async def logout(owner_id: int, slot: int | None = None) -> str:
    """
    slot=None → saare accounts logout
    slot=N    → sirf woh account logout
    """
    if slot is None:
        # Saare
        slots_dict = _clients.pop(owner_id, {})
        for s, client in slots_dict.items():
            try:
                await client.log_out()
            except Exception:
                pass
        await db.delete_userbot(owner_id)
        return f"✅ Saare {len(slots_dict)} account(s) logout ho gaye."
    else:
        # Specific slot
        slots_dict = _clients.get(owner_id, {})
        client = slots_dict.pop(slot, None)
        if not client:
            return f"❌ Slot {slot} nahi mila."
        try:
            await client.log_out()
        except Exception:
            pass
        await db.delete_userbot_by_slot(owner_id, slot)
        # Slots re-number karo memory mein bhi
        new_dict = {i: c for i, (_, c) in enumerate(sorted(slots_dict.items()))}
        _clients[owner_id] = new_dict
        return f"✅ Account slot {slot} remove ho gaya."


# ─────────────────────── Login Flow ──────────────────────────────────────

class LoginSession:
    """
    Multi-account login support.
    State: { client, phone, phone_code_hash, step }
    """
    _pending: dict[int, dict] = {}  # { owner_id: {...} }

    @classmethod
    async def start_login(cls, owner_id: int, phone: str) -> str:
        os.makedirs(Config.SESSION_DIR, exist_ok=True)

        # Check: kya yeh phone pehle se logged in hai?
        existing_accounts = await db.get_all_userbots(owner_id)
        for acc in existing_accounts:
            if acc["phone"] == phone:
                return f"❌ Yeh number (`{phone}`) pehle se add hai!"

        client = Client(
            name=f"tmp_{owner_id}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            in_memory=True
        )
        await client.connect()

        try:
            sent = await client.send_code(phone)
        except PhoneNumberInvalid:
            await client.disconnect()
            return "❌ Invalid phone number."
        except FloodWait as e:
            await client.disconnect()
            return f"⏳ FloodWait: {e.value}s baad try karo."

        cls._pending[owner_id] = {
            "client":          client,
            "phone":           phone,
            "phone_code_hash": sent.phone_code_hash,
            "step":            "otp"
        }
        return "✅ OTP bheja! Code bhejo — spaces ke saath ya bina:\n`1 2 3 4 5` ya `12345` — dono chalega 👍"

    @classmethod
    async def submit_otp(cls, owner_id: int, code: str) -> str:
        pending = cls._pending.get(owner_id)
        if not pending or pending["step"] != "otp":
            return "❌ Login chal nahi raha. Pehle /addaccount bhejo."

        client: Client = pending["client"]
        try:
            await client.sign_in(
                phone_number    = pending["phone"],
                phone_code_hash = pending["phone_code_hash"],
                phone_code      = "".join(code.split())  # spaces remove karo (5 0 8 9 7 → 50897)
            )
        except PhoneCodeInvalid:
            return "❌ Galat OTP. Dobara bhejo."
        except PhoneCodeExpired:
            cls._pending.pop(owner_id, None)
            try:
                await client.disconnect()
            except Exception:
                pass
            return "❌ OTP expire ho gaya. /addaccount dobara karo."
        except SessionPasswordNeeded:
            pending["step"] = "2fa"
            return "🔐 2FA on hai. Password bhejo:"
        except Exception as e:
            return f"❌ Error: {e}"

        return await cls._finish_login(owner_id, client, pending["phone"])

    @classmethod
    async def submit_2fa(cls, owner_id: int, password: str) -> str:
        pending = cls._pending.get(owner_id)
        if not pending or pending["step"] != "2fa":
            return "❌ 2FA pending nahi hai."
        client: Client = pending["client"]
        try:
            await client.check_password(password)
        except Exception as e:
            return f"❌ Galat password: {e}"
        return await cls._finish_login(owner_id, client, pending["phone"])

    @classmethod
    async def _finish_login(cls, owner_id: int, client: Client, phone: str) -> str:
        # Step 1: Session export
        try:
            session_string = await client.export_session_string()
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            cls._pending.pop(owner_id, None)
            return f"❌ Session export failed: {e}"

        # Step 2: Temp client disconnect
        try:
            await client.disconnect()
        except Exception:
            pass

        # Step 3: DB save (yahan KeyError fix hai database.py mein)
        try:
            slot = await db.save_userbot(owner_id, phone, session_string)
        except Exception as e:
            cls._pending.pop(owner_id, None)
            return f"❌ DB save failed: {e}"

        # Step 4: Live client start
        live_client = Client(
            name=f"user_{owner_id}_{slot}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_string,
            in_memory=True
        )
        try:
            await live_client.start()
        except Exception as e:
            cls._pending.pop(owner_id, None)
            return f"❌ Client start failed: {e}"

        if owner_id not in _clients:
            _clients[owner_id] = {}
        _clients[owner_id][slot] = live_client

        # ✅ FIX: Login ke baad is owner ke saare rule targets join karo
        # Bina join ke "Peer id invalid" error aata hai
        try:
            from database import db as _db
            rules = await _db.get_auto_rules(owner_id)
            for rule in rules:
                for target_id in rule.get("targets", []):
                    try:
                        await live_client.join_chat(target_id)
                        await asyncio.sleep(1)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Auto-join targets failed: {e}")

        # ✅ FIX: Naye client ke saath auto_listener handlers attach karo
        try:
            from auto_listener import attach_client_to_all_handlers
            attach_client_to_all_handlers(live_client)
        except Exception as e:
            logger.warning(f"attach handlers failed: {e}")

        cls._pending.pop(owner_id, None)
        me = await live_client.get_me()
        total = len(_clients[owner_id])
        return (
            f"✅ **Account add ho gaya!**\n\n"
            f"👤 Name: **{me.first_name}**\n"
            f"📱 Phone: `{me.phone_number}`\n"
            f"🔢 Slot: `{slot}`\n"
            f"📊 Total accounts: `{total}`"
        )

    @classmethod
    def get_step(cls, owner_id: int) -> str | None:
        p = cls._pending.get(owner_id)
        return p["step"] if p else None
