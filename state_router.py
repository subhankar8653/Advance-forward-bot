"""
state_router.py — Central message handler
Teeno plugins ke wizard states yahan route hote hain.
Conflict bilkul nahi — ek hi handler hai.
"""

# Global state stores
# { owner_id: { step, ... } }
LOGIN_STATE:   dict[int, dict] = {}
FORWARD_STATE: dict[int, dict] = {}
AUTO_STATE:    dict[int, dict] = {}
EDIT_STATE:    dict[int, dict] = {}   # Rule edit wizard


def clear_all(owner_id: int):
    """Kisi bhi active state ko clear karo."""
    LOGIN_STATE.pop(owner_id, None)
    FORWARD_STATE.pop(owner_id, None)
    AUTO_STATE.pop(owner_id, None)
    EDIT_STATE.pop(owner_id, None)


def active_state(owner_id: int) -> str | None:
    """Kaunsa wizard active hai?"""
    if owner_id in LOGIN_STATE:   return "login"
    if owner_id in FORWARD_STATE: return "forward"
    if owner_id in AUTO_STATE:    return "auto"
    if owner_id in EDIT_STATE:    return "edit"
    return None
