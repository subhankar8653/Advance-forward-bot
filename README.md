# 🚀 Forward Bot

Ek powerful Telegram forward bot — Manual + Auto forward with link hiding, affiliate bot support.

---

## 📁 File Structure

```
ForwardBot/
├── main.py              # Entry point
├── config.py            # Env variables
├── database.py          # MongoDB functions
├── userbot.py           # Userbot login & management
├── auto_listener.py     # Auto-forward engine
├── utils.py             # Text processing
├── requirements.txt
├── Procfile             # Railway ke liye
├── .env.example         # Variables example
└── plugins/
    ├── start.py         # /start /help /status
    ├── login.py         # /login /logout
    ├── manual_forward.py# /forward (bulk)
    └── auto_rules.py    # /auto /rules
```

---

## ⚙️ Railway pe Deploy karna

### Step 1 — GitHub pe push karo
```bash
git init
git add .
git commit -m "first commit"
git remote add origin https://github.com/tumhara/repo.git
git push -u origin main
```

### Step 2 — Railway pe new project banao
1. railway.app pe jao → New Project → Deploy from GitHub
2. Apna repo select karo

### Step 3 — Environment Variables set karo
Railway Dashboard → Variables → inhe add karo:

| Variable | Value |
|---|---|
| `API_ID` | my.telegram.org se |
| `API_HASH` | my.telegram.org se |
| `BOT_TOKEN` | @BotFather se |
| `OWNER_ID` | Tumhara user ID |
| `DATABASE_URI` | MongoDB Atlas URI |
| `DATABASE_NAME` | `forwardbot` |

### Step 4 — Deploy!
Railway automatically deploy karega. Logs mein dekho:
```
Bot started: @yourbotname
All systems ready ✅
```

---

## 📱 Bot Use Karna

### Userbot Login
```
/login
→ Phone number bhejo: +919876543210
→ OTP bhejo: 12345
→ ✅ Logged in!
```

### Manual Bulk Forward
```
/forward
→ Target channel select karo
→ Source channel ka message forward karo
→ Start ID: 1
→ End ID: 10000
→ Confirm → Start!
```

### Auto Forward Rule
```
/auto
→ Source channel ka message forward karo
→ Target channel IDs do
→ Affiliate bot: skip ya @botname
→ Hide links: ON/OFF
→ Hide @usernames: ON/OFF
→ Text replacements: t.me/old | t.me/new
→ ✅ Rule saved!
```

### Rules Manage Karna
```
/rules         → saare rules dekho
🗑 Delete      → rule hata do
```

---

## 🔧 Features

| Feature | Description |
|---|---|
| Manual Forward | 10,000+ messages bulk forward |
| Auto Forward | Public/Private channel monitor (userbot se) |
| Link Hide | Telegram links remove kar do |
| Username Hide | @mentions remove kar do |
| Text Replace | Custom find-replace |
| Affiliate Bot | Source post → bot → affiliate post → target |
| Multi Target | Ek source → multiple channels |
| Phone Login | Number → OTP → Session save |
| Railway Ready | Procfile included |
