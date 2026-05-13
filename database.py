import motor.motor_asyncio
from config import Config


class Database:
    def __init__(self, uri, name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        db = self._client[name]

        # collections
        self.users      = db.users
        self.userbots   = db.userbots       # ab multiple per owner
        self.auto_rules = db.auto_rules
        self.tasks      = db.tasks

    # ────────────────────────────── USERS ──────────────────────────────

    async def ensure_user(self, user_id: int, name: str = ""):
        if not await self.users.find_one({"id": user_id}):
            await self.users.insert_one({"id": user_id, "name": name})

    async def get_all_users(self):
        return self.users.find({})

    # ────────────────────────── USERBOT (MULTI) ─────────────────────────
    #
    # Schema: { owner_id, phone, session, slot }
    # slot = 0, 1, 2, ... (account number, 0-indexed)
    # Ek owner ke multiple accounts ho sakte hain
    # ─────────────────────────────────────────────────────────────────────

    async def save_userbot(self, owner_id: int, phone: str, session_string: str):
        """
        Naya account add karo. Agar same phone pehle se hai toh update karo.
        Slot automatically assign hoga (0, 1, 2...)
        """
        existing = await self.userbots.find_one({"owner_id": owner_id, "phone": phone})
        if existing:
            await self.userbots.update_one(
                {"owner_id": owner_id, "phone": phone},
                {"$set": {"session": session_string}}
            )
            return existing.get("slot", 0)

        # Naya slot assign karo
        all_slots = [r["slot"] async for r in self.userbots.find({"owner_id": owner_id})]
        slot = max(all_slots) + 1 if all_slots else 0
        await self.userbots.insert_one({
            "owner_id": owner_id,
            "phone":    phone,
            "session":  session_string,
            "slot":     slot
        })
        return slot

    async def get_userbot(self, owner_id: int):
        """Pehla (slot=0) userbot return karo — backward compatibility."""
        return await self.userbots.find_one({"owner_id": owner_id, "slot": 0})

    async def get_all_userbots(self, owner_id: int) -> list:
        """Owner ke saare accounts return karo, slot order mein."""
        docs = [r async for r in self.userbots.find({"owner_id": owner_id})]
        return sorted(docs, key=lambda x: x.get("slot", 0))

    async def get_userbot_by_slot(self, owner_id: int, slot: int):
        return await self.userbots.find_one({"owner_id": owner_id, "slot": slot})

    async def delete_userbot_by_slot(self, owner_id: int, slot: int):
        """Ek specific account remove karo."""
        await self.userbots.delete_one({"owner_id": owner_id, "slot": slot})
        # Slots re-number karo taaki gap na rahe
        remaining = sorted(
            [r async for r in self.userbots.find({"owner_id": owner_id})],
            key=lambda x: x.get("slot", 0)
        )
        for i, doc in enumerate(remaining):
            await self.userbots.update_one({"_id": doc["_id"]}, {"$set": {"slot": i}})

    async def delete_userbot(self, owner_id: int):
        """Saare accounts ek saath remove karo."""
        await self.userbots.delete_many({"owner_id": owner_id})

    async def get_all_userbot_records(self):
        """Startup pe saare records load karne ke liye."""
        return [r async for r in self.userbots.find({})]

    # ────────────────────────── AUTO RULES ─────────────────────────────

    async def add_auto_rule(self, owner_id: int, rule: dict):
        rule["owner_id"] = owner_id
        await self.auto_rules.insert_one(rule)

    async def get_auto_rules(self, owner_id: int):
        return [r async for r in self.auto_rules.find({"owner_id": owner_id})]

    async def get_rule_by_id(self, rule_id: str):
        return await self.auto_rules.find_one({"rule_id": rule_id})

    async def delete_auto_rule(self, owner_id: int, rule_id: str):
        await self.auto_rules.delete_one({"owner_id": owner_id, "rule_id": rule_id})

    async def get_all_rules(self):
        return [r async for r in self.auto_rules.find({})]

    # ────────────────────────── MANUAL TASKS ───────────────────────────

    async def save_task(self, task_id: str, data: dict):
        await self.tasks.update_one({"task_id": task_id}, {"$set": data}, upsert=True)

    async def get_task(self, task_id: str):
        return await self.tasks.find_one({"task_id": task_id})

    async def delete_task(self, task_id: str):
        await self.tasks.delete_many({"task_id": task_id})

    async def update_rule_field(self, owner_id: int, rule_id: str, field: str, value):
        await self.auto_rules.update_one(
            {"owner_id": owner_id, "rule_id": rule_id},
            {"$set": {field: value}}
        )


db = Database(Config.DATABASE_URI, Config.DATABASE_NAME)
