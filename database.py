import motor.motor_asyncio
from config import Config


class Database:
    def __init__(self, uri, name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        db = self._client[name]

        # collections
        self.users      = db.users          # bot users
        self.userbots   = db.userbots       # logged-in user sessions
        self.auto_rules = db.auto_rules     # auto-forward rules
        self.tasks      = db.tasks          # manual forward task progress

    # ────────────────────────────── USERS ──────────────────────────────

    async def ensure_user(self, user_id: int, name: str = ""):
        if not await self.users.find_one({"id": user_id}):
            await self.users.insert_one({"id": user_id, "name": name})

    async def get_all_users(self):
        return self.users.find({})

    # ────────────────────────── USERBOT ────────────────────────────────

    async def save_userbot(self, owner_id: int, phone: str, session_string: str):
        """Ek owner ka ek hi userbot hoga (upsert)."""
        await self.userbots.update_one(
            {"owner_id": owner_id},
            {"$set": {"phone": phone, "session": session_string}},
            upsert=True
        )

    async def get_userbot(self, owner_id: int):
        return await self.userbots.find_one({"owner_id": owner_id})

    async def delete_userbot(self, owner_id: int):
        await self.userbots.delete_many({"owner_id": owner_id})

    # ────────────────────────── AUTO RULES ─────────────────────────────

    async def add_auto_rule(self, owner_id: int, rule: dict):
        """
        rule = {
          "rule_id": str,
          "source_id": int,          # channel to monitor
          "targets": [int, ...],     # channels to forward to
          "affiliate_bot": str|None, # @bot username for affiliate rewrite
          "hide_link": bool,
          "hide_username": bool,
          "replace_map": {old: new}, # text replace dict
        }
        """
        rule["owner_id"] = owner_id
        await self.auto_rules.insert_one(rule)

    async def get_auto_rules(self, owner_id: int):
        return [r async for r in self.auto_rules.find({"owner_id": owner_id})]

    async def get_rule_by_id(self, rule_id: str):
        return await self.auto_rules.find_one({"rule_id": rule_id})

    async def delete_auto_rule(self, owner_id: int, rule_id: str):
        await self.auto_rules.delete_one({"owner_id": owner_id, "rule_id": rule_id})

    async def get_all_rules(self):
        """Startup pe saare rules load karne ke liye."""
        return [r async for r in self.auto_rules.find({})]

    # ────────────────────────── MANUAL TASKS ───────────────────────────

    async def save_task(self, task_id: str, data: dict):
        await self.tasks.update_one({"task_id": task_id}, {"$set": data}, upsert=True)

    async def get_task(self, task_id: str):
        return await self.tasks.find_one({"task_id": task_id})

    async def delete_task(self, task_id: str):
        await self.tasks.delete_many({"task_id": task_id})

    async def update_rule_field(self, owner_id: int, rule_id: str, field: str, value):
        """Rule ka ek field update karo."""
        await self.auto_rules.update_one(
            {"owner_id": owner_id, "rule_id": rule_id},
            {"$set": {field: value}}
        )


db = Database(Config.DATABASE_URI, Config.DATABASE_NAME)
