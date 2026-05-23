"""
Text processing utilities:
- Links hide/replace
- @username hide/replace
- Custom find-replace
- Bold text apply (Telegram HTML bold)
- Remove words (specific words delete karo)
"""
import re
import html


# ──────────────────────────── REGEX PATTERNS ────────────────────────────

_URL_RE      = re.compile(r'https?://[^\s]+', re.IGNORECASE)
_TG_LINK_RE  = re.compile(r'(https?://)?(t\.me|telegram\.me|telegram\.dog)/\S+', re.IGNORECASE)
_USERNAME_RE = re.compile(r'@[A-Za-z0-9_]{3,}')


def make_bold(text: str) -> str:
    """
    Text ko Telegram HTML bold mein wrap karo.
    HTML special chars (<, >, &) pehle escape karo — warna parse error aata hai.
    Line by line bold karta hai.
    """
    if not text:
        return text
    lines = text.split("\n")
    bold_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped:
            # HTML escape karo pehle (< > & → &lt; &gt; &amp;)
            safe = html.escape(line)
            bold_lines.append(f"<b>{safe}</b>")
        else:
            bold_lines.append(line)  # Empty lines as-is
    return "\n".join(bold_lines)


def process_text(
    text: str,
    hide_links: bool     = False,
    hide_usernames: bool = False,
    replace_map: dict    = None,   # {old_str: new_str}
    bold_text: bool      = False,
    remove_words: list   = None,   # [word1, word2, ...] — ye words delete ho jayenge
) -> str:
    """
    Text ko process karo rules ke hisab se.
    Order: remove_words → replace → hide links → hide usernames → bold
    """
    if not text:
        return text

    # 0. Remove words (pehle — taaki baaki processing pe effect na pade)
    # Case-insensitive: "blue" likhoge toh "Blue", "BLUE" sab hat jayega
    if remove_words:
        for word in remove_words:
            word = word.strip()
            if word:
                text = re.sub(re.escape(word), '', text, flags=re.IGNORECASE)

    # 1. Custom replacements (pehle — taaki replaced text pe bhi hide kaam kare)
    # Case-insensitive replace — WatcherGuru, @WatcherGuru, watcherguru sab match hoga
    if replace_map:
        for old, new in replace_map.items():
            if old:  # Empty key skip karo
                text = re.sub(re.escape(old), new, text, flags=re.IGNORECASE)

    # 2. Telegram links hide
    if hide_links:
        text = _TG_LINK_RE.sub('', text)
        text = _URL_RE.sub('', text)

    # 3. @username hide
    if hide_usernames:
        text = _USERNAME_RE.sub('', text)

    # Clean extra blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()

    # 4. Bold wrap (sabse aakhir mein — HTML escape bhi yahan hoti hai)
    if bold_text:
        text = make_bold(text)

    return text


def clean_entities(message):
    return message.text or message.caption or ""
