import motor.motor_asyncio
from config import Config


class Database:
    def __init__(self, uri, name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        db = self._client[name]

        # collections
        self.users        = db.users
        self.userbots     = db.userbots       # multiple userbot accounts per owner
        self.worker_bots  = db.worker_bots    # NEW: multiple bot tokens per owner
        self.auto_rules   = db.auto_rules
        self.tasks        = db.tasks

    # ────────────────────────────── USERS ──────────────────────────────

    async def ensure_user(self, user_id: int, name: str = ""):
        if not await self.users.find_one({"id": user_id}):
            await self.users.insert_one({"id": user_id, "name": name})

    async def get_all_users(self):
        return self.users.find({})

    # ────────────────────────── USERBOT (MULTI) ─────────────────────────

    async def save_userbot(self, owner_id: int, phone: str, session_string: str):
        existing = await self.userbots.find_one({"owner_id": owner_id, "phone": phone})
        if existing:
            await self.userbots.update_one(
                {"owner_id": owner_id, "phone": phone},
                {"$set": {"session": session_string}}
            )
            return existing.get("slot", 0)
        all_slots = [r.get("slot", 0) async for r in self.userbots.find({"owner_id": owner_id})]
        slot = max(all_slots) + 1 if all_slots else 0
        await self.userbots.insert_one({
            "owner_id": owner_id,
            "phone":    phone,
            "session":  session_string,
            "slot":     slot
        })
        return slot

    async def get_userbot(self, owner_id: int):
        return await self.userbots.find_one({"owner_id": owner_id, "slot": 0})

    async def get_all_userbots(self, owner_id: int) -> list:
        docs = [r async for r in self.userbots.find({"owner_id": owner_id})]
        return sorted(docs, key=lambda x: x.get("slot", 0))

    async def get_userbot_by_slot(self, owner_id: int, slot: int):
        return await self.userbots.find_one({"owner_id": owner_id, "slot": slot})

    async def delete_userbot_by_slot(self, owner_id: int, slot: int):
        await self.userbots.delete_one({"owner_id": owner_id, "slot": slot})
        remaining = sorted(
            [r async for r in self.userbots.find({"owner_id": owner_id})],
            key=lambda x: x.get("slot", 0)
        )
        for i, doc in enumerate(remaining):
            await self.userbots.update_one({"_id": doc["_id"]}, {"$set": {"slot": i}})

    async def delete_userbot(self, owner_id: int):
        await self.userbots.delete_many({"owner_id": owner_id})

    async def get_all_userbot_records(self):
        return [r async for r in self.userbots.find({})]

    # ─────────────────────── WORKER BOTS ──────────────────────────
    # Schema: { owner_id, token, name, username, slot }

    async def save_worker(self, owner_id: int, token: str, name: str, username: str) -> int:
        all_slots = [r.get("slot", 0) async for r in self.worker_bots.find({"owner_id": owner_id})]
        slot = max(all_slots) + 1 if all_slots else 0
        await self.worker_bots.insert_one({
            "owner_id": owner_id,
            "token":    token,
            "name":     name,
            "username": username,
            "slot":     slot
        })
        return slot

    async def get_all_workers(self, owner_id: int) -> list:
        docs = [r async for r in self.worker_bots.find({"owner_id": owner_id})]
        return sorted(docs, key=lambda x: x.get("slot", 0))

    async def delete_worker_by_slot(self, owner_id: int, slot: int) -> bool:
        result = await self.worker_bots.delete_one({"owner_id": owner_id, "slot": slot})
        if result.deleted_count == 0:
            return False
        remaining = sorted(
            [r async for r in self.worker_bots.find({"owner_id": owner_id})],
            key=lambda x: x.get("slot", 0)
        )
        for i, doc in enumerate(remaining):
            await self.worker_bots.update_one({"_id": doc["_id"]}, {"$set": {"slot": i}})
        return True

    async def delete_all_workers(self, owner_id: int):
        await self.worker_bots.delete_many({"owner_id": owner_id})

    async def get_all_worker_records(self) -> list:
        return [r async for r in self.worker_bots.find({})]

    # ────────────────────────── AUTO RULES ─────────────────────────────
    # Rule schema (full):
    # {
    #   rule_id, owner_id, source_id, targets,
    #   affiliate_bot,
    #   hide_links, hide_usernames,
    #   remove_thumbnail,        ← NEW: thumbnail hatana (bool)
    #   bold_text,               ← NEW: text bold karna (bool)
    #   replace_map,
    #   specific_forward_mode,   ← NEW: "forward" | "not_forward" | None
    #   trigger_words,           ← NEW: list of words
    # }

    async def add_auto_rule(self, owner_id: int, rule: dict):
        rule["owner_id"] = owner_id
        # Naye fields ka default set karo agar missing ho
        # (purane rules ke saath backward compatible)
        rule.setdefault("remove_thumbnail",      False)
        rule.setdefault("bold_text",             False)
        rule.setdefault("specific_forward_mode", None)
        rule.setdefault("trigger_words",         [])
        await self.auto_rules.insert_one(rule)

    async def get_auto_rules(self, owner_id: int):
        rules = [r async for r in self.auto_rules.find({"owner_id": owner_id})]
        # Purane rules ke liye missing fields ka default dena
        for r in rules:
            r.setdefault("remove_thumbnail",      False)
            r.setdefault("bold_text",             False)
            r.setdefault("specific_forward_mode", None)
            r.setdefault("trigger_words",         [])
        return rules

    async def get_rule_by_id(self, rule_id: str):
        r = await self.auto_rules.find_one({"rule_id": rule_id})
        if r:
            r.setdefault("remove_thumbnail",      False)
            r.setdefault("bold_text",             False)
            r.setdefault("specific_forward_mode", None)
            r.setdefault("trigger_words",         [])
        return r

    async def delete_auto_rule(self, owner_id: int, rule_id: str):
        await self.auto_rules.delete_one({"rule_id": rule_id})

    async def get_all_rules(self):
        rules = [r async for r in self.auto_rules.find({})]
        for r in rules:
            r.setdefault("remove_thumbnail",      False)
            r.setdefault("bold_text",             False)
            r.setdefault("specific_forward_mode", None)
            r.setdefault("trigger_words",         [])
        return rules

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
