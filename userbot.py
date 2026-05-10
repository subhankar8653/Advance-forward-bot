"""
Userbot login & client management.
Phone → OTP → session string save in MongoDB.
Railway restart pe bhi session reuse hota hai.
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

# Active pyrogram Client objects  { owner_id: Client }
_clients: dict[int, Client] = {}


async def get_client(owner_id: int) -> Client | None:
    """Return running Client for owner, or None."""
    return _clients.get(owner_id)


async def start_all_clients():
    """
    Bot start hone pe DB se saare sessions load karo
    aur clients start karo.
    """
    cursor = db.userbots.find({})
    async for rec in cursor:
        owner_id = rec["owner_id"]
        session  = rec["session"]
        try:
            client = Client(
                name=f"user_{owner_id}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                session_string=session,
                in_memory=True
            )
            await client.start()
            _clients[owner_id] = client
            logger.info(f"Userbot started for owner {owner_id}")
        except Exception as e:
            logger.error(f"Could not start userbot for {owner_id}: {e}")


# ─────────────────────────────── LOGIN FLOW ───────────────────────────────

class LoginSession:
    """
    Ek owner ke liye temporary login state rakhta hai.
    State machine:  idle → sent_code → done
    """
    _pending: dict[int, dict] = {}   # { owner_id: {client, phone_code_hash, step} }

    @classmethod
    async def start_login(cls, owner_id: int, phone: str) -> str:
        """
        Phone number se OTP bhejo.
        Returns: status message string
        """
        os.makedirs(Config.SESSION_DIR, exist_ok=True)

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
            return f"⏳ FloodWait: try after {e.value}s."

        cls._pending[owner_id] = {
            "client": client,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "step": "otp"
        }
        return "✅ OTP sent! Now send the code like: `12345`"

    @classmethod
    async def submit_otp(cls, owner_id: int, code: str) -> str:
        """OTP verify karo. 2FA wale ke liye password bhi mange."""
        pending = cls._pending.get(owner_id)
        if not pending or pending["step"] != "otp":
            return "❌ No login in progress. Send /login first."

        client: Client = pending["client"]

        try:
            await client.sign_in(
                phone_number   = pending["phone"],
                phone_code_hash= pending["phone_code_hash"],
                phone_code     = code.strip()
            )
        except PhoneCodeInvalid:
            return "❌ Wrong OTP. Try again."
        except PhoneCodeExpired:
            cls._pending.pop(owner_id, None)
            await client.disconnect()
            return "❌ OTP expired. Send /login again."
        except SessionPasswordNeeded:
            pending["step"] = "2fa"
            return "🔐 2FA enabled. Send your password:"
        except Exception as e:
            return f"❌ Error: {e}"

        return await cls._finish_login(owner_id, client, pending["phone"])

    @classmethod
    async def submit_2fa(cls, owner_id: int, password: str) -> str:
        pending = cls._pending.get(owner_id)
        if not pending or pending["step"] != "2fa":
            return "❌ No 2FA pending."
        client: Client = pending["client"]
        try:
            await client.check_password(password)
        except Exception as e:
            return f"❌ Wrong password: {e}"
        return await cls._finish_login(owner_id, client, pending["phone"])

    @classmethod
    async def _finish_login(cls, owner_id: int, client: Client, phone: str) -> str:
        session_string = await client.export_session_string()
        # Reconnect as persistent in-memory client
        await client.disconnect()

        live_client = Client(
            name=f"user_{owner_id}",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=session_string,
            in_memory=True
        )
        await live_client.start()
        _clients[owner_id] = live_client

        # DB mein save
        await db.save_userbot(owner_id, phone, session_string)
        cls._pending.pop(owner_id, None)

        me = await live_client.get_me()
        return f"✅ Logged in as **{me.first_name}** (`{me.phone_number}`)"

    @classmethod
    def get_step(cls, owner_id: int) -> str | None:
        p = cls._pending.get(owner_id)
        return p["step"] if p else None


async def logout(owner_id: int) -> str:
    client = _clients.pop(owner_id, None)
    if client:
        try:
            await client.log_out()
        except Exception:
            pass
    await db.delete_userbot(owner_id)
    return "✅ Logged out successfully."
