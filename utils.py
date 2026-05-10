"""
Text processing utilities:
- Links hide/replace
- @username hide/replace
- Custom find-replace
"""
import re


# ──────────────────────────── REGEX PATTERNS ────────────────────────────

_URL_RE      = re.compile(r'https?://[^\s]+', re.IGNORECASE)
_TG_LINK_RE  = re.compile(r'(https?://)?(t\.me|telegram\.me|telegram\.dog)/\S+', re.IGNORECASE)
_USERNAME_RE = re.compile(r'@[A-Za-z0-9_]{3,}')


def process_text(
    text: str,
    hide_links: bool       = False,
    hide_usernames: bool   = False,
    replace_map: dict      = None,   # {old_str: new_str}
) -> str:
    """
    Text ko clean karo rules ke hisab se.
    replace_map pehle apply hoga, phir hide rules.
    """
    if not text:
        return text

    # 1. Custom replacements (e.g. source channel link → apna link)
    if replace_map:
        for old, new in replace_map.items():
            text = text.replace(old, new)

    # 2. Telegram links hide
    if hide_links:
        text = _TG_LINK_RE.sub('', text)
        text = _URL_RE.sub('', text)

    # 3. @username hide
    if hide_usernames:
        text = _USERNAME_RE.sub('', text)

    # Clean extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def clean_entities(message):
    """
    Pyrogram Message se text + entities nikalo.
    Return karo processed text (entities ignore karne ke liye
    copy_message use karo — yahan plain text return hoga).
    """
    return message.text or message.caption or ""
