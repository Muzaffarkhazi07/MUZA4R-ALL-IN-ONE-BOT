import asyncio
import ipaddress
import json
import os
import secrets
import socket
import sqlite3
import string
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aiohttp
import qrcode
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "5839586443"))
DB_PATH = os.getenv("DB_PATH", "muza4r_bot.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing. Add it in Railway Variables.")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


def now():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            accepted INTEGER DEFAULT 0,
            content_consent INTEGER DEFAULT 0,
            banned INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_id TEXT,
            file_type TEXT,
            file_name TEXT,
            created_at TEXT
        );
        """)
        defaults = {
            "welcome_text": "👑 <b>WELCOME TO MUZA4R BOT</b>\n\nWelcome, {first_name}! 🚀\n\nChoose a service below.",
            "welcome_file_id": "",
            "welcome_file_type": "",
            "ai_provider": "",
            "ai_api_key": "",
            "ai_model": "",
            "ai_endpoint": "",
        }
        for k, v in defaults.items():
            con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))


def get_setting(key, default=""):
    with db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )


def user_row(user_id):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def upsert_user(user):
    with db() as con:
        con.execute("""
        INSERT INTO users(user_id,username,first_name,first_seen,last_seen)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
          username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen
        """, (user.id, user.username or "", user.first_name or "", now(), now()))


def log_action(user_id, action, details=""):
    # Details are deliberately minimized unless the user explicitly opted into content logging.
    row = user_row(user_id)
    if row and row["content_consent"] != 1:
        details = details if action in {"File uploaded", "QR generated", "Password generated", "Text formatted", "Website checked", "URL info", "AI request"} else ""
        # Action metadata is logged; sensitive content is only added by handlers after consent.
    with db() as con:
        con.execute("INSERT INTO logs(user_id,action,details,created_at) VALUES(?,?,?,?)",
                    (user_id, action, details[:3500], now()))


def is_owner(user_id):
    return user_id == OWNER_ID


def is_banned(user_id):
    row = user_row(user_id)
    return bool(row and row["banned"])


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 AI Chat", callback_data="tool:ai"),
         InlineKeyboardButton(text="🔳 QR Code", callback_data="tool:qr")],
        [InlineKeyboardButton(text="🔐 Password", callback_data="tool:password"),
         InlineKeyboardButton(text="✍️ Text Tools", callback_data="tool:format")],
        [InlineKeyboardButton(text="🌐 Website Check", callback_data="tool:webcheck"),
         InlineKeyboardButton(text="🔎 URL Info", callback_data="tool:urlinfo")],
        [InlineKeyboardButton(text="📁 File Sharing", callback_data="tool:files"),
         InlineKeyboardButton(text="👤 My Profile", callback_data="tool:profile")],
        [InlineKeyboardButton(text="📊 Statistics", callback_data="tool:stats"),
         InlineKeyboardButton(text="❓ Help", callback_data="tool:help")],
    ])


def owner_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistics", callback_data="own:stats"),
         InlineKeyboardButton(text="👥 Users", callback_data="own:users:0")],
        [InlineKeyboardButton(text="📋 Activity Logs", callback_data="own:logs"),
         InlineKeyboardButton(text="📁 Uploaded Files", callback_data="own:files")],
        [InlineKeyboardButton(text="✏️ Welcome Settings", callback_data="own:welcome"),
         InlineKeyboardButton(text="🤖 AI API Settings", callback_data="own:api")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="own:broadcast"),
         InlineKeyboardButton(text="🔙 Main Menu", callback_data="tool:home")],
    ])


def welcome_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Change Text", callback_data="wel:text"),
         InlineKeyboardButton(text="🖼️ Set Media", callback_data="wel:media")],
        [InlineKeyboardButton(text="👀 Preview", callback_data="wel:preview"),
         InlineKeyboardButton(text="🗑️ Remove Media", callback_data="wel:remove_media")],
        [InlineKeyboardButton(text="🗑️ Remove Custom Welcome", callback_data="wel:remove_text")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="own:home")],
    ])


def api_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 OpenAI", callback_data="api:openai"),
         InlineKeyboardButton(text="✨ Gemini", callback_data="api:gemini")],
        [InlineKeyboardButton(text="🔑 Add/Replace API Key", callback_data="api:key")],
        [InlineKeyboardButton(text="🧠 Set Model", callback_data="api:model")],
        [InlineKeyboardButton(text="🧪 Test Configuration", callback_data="api:test"),
         InlineKeyboardButton(text="🗑️ Remove API", callback_data="api:remove")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="own:home")],
    ])


class Form(StatesGroup):
    qr = State()
    password = State()
    formatter = State()
    website = State()
    urlinfo = State()
    ai = State()
    welcome_text = State()
    welcome_media = State()
    api_key = State()
    api_model = State()
    ban_id = State()
    unban_id = State()
    broadcast = State()


async def notify_owner(text):
    try:
        await bot.send_message(OWNER_ID, text)
    except Exception:
        pass


async def owner_log(user_id, action, details="", content=None):
    log_action(user_id, action, details)
    row = user_row(user_id)
    uname = ("@" + row["username"]) if row and row["username"] else "(no username)"
    name = row["first_name"] if row else "Unknown"
    message = (
        f"👑 <b>MUZA4R ACTIVITY LOG</b>\n\n"
        f"👤 User: {name} {uname}\n🆔 ID: <code>{user_id}</code>\n"
        f"⚡ Action: <b>{action}</b>\n📌 Details: {details or 'Not recorded'}\n🕒 {now()}"
    )
    await notify_owner(message)
    if content:
        try:
            await bot.send_message(OWNER_ID, content)
        except Exception:
            pass


async def require_user(message: Message):
    upsert_user(message.from_user)
    row = user_row(message.from_user.id)
    if is_banned(message.from_user.id):
        await message.answer("🚫 Your access to this bot has been restricted.")
        return False
    if not row or not row["accepted"]:
        await message.answer(
            "Before using MUZA4R Bot, please review this notice:\n\n"
            "• Files you send to this bot are copied to the bot owner.\n"
            "• Basic usage events are logged for bot administration.\n"
            "• Detailed text/AI content is logged only if you opt in.\n"
            "• Do not send passwords, private keys, or sensitive documents.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Accept & Continue", callback_data="consent:accept")],
                [InlineKeyboardButton(text="❌ Decline", callback_data="consent:decline")],
            ])
        )
        return False
    return True


async def safe_public_url(url):
    try:
        p = urlparse(url.strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            return False, None
        host = p.hostname
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except Exception:
            return False, None
        ips = {info[4][0] for info in infos}
        for ip in ips:
            addr = ipaddress.ip_address(ip)
            if not addr.is_global:
                return False, None
        return True, p
    except Exception:
        return False, None


def user_summary():
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        banned = con.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        accepted = con.execute("SELECT COUNT(*) n FROM users WHERE accepted=1 AND banned=0").fetchone()["n"]
        files = con.execute("SELECT COUNT(*) n FROM files").fetchone()["n"]
        logs = con.execute("SELECT COUNT(*) n FROM logs").fetchone()["n"]
    return total, accepted, banned, files, logs


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    upsert_user(message.from_user)
    if is_banned(message.from_user.id):
        await message.answer("🚫 Your access to this bot has been restricted.")
        return
    row = user_row(message.from_user.id)
    if not row["accepted"]:
        await message.answer(
            "👑 <b>MUZA4R ALL-IN-ONE BOT</b>\n\n"
            "Before continuing, please note:\n"
            "• Files sent to this bot are copied to the owner.\n"
            "• Basic activity (tool use, time and status) is logged.\n"
            "• Optional content logging for AI/text features can be enabled separately.\n\n"
            "Choose whether to continue:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Accept & Continue", callback_data="consent:accept")],
                [InlineKeyboardButton(text="❌ Decline", callback_data="consent:decline")],
            ])
        )
        return
    await send_welcome(message.chat.id, message.from_user.first_name)


async def send_welcome(chat_id, first_name="there"):
    text = get_setting("welcome_text", "").replace("{first_name}", first_name or "there")
    file_id = get_setting("welcome_file_id")
    file_type = get_setting("welcome_file_type")
    try:
        if file_id and file_type == "photo":
            await bot.send_photo(chat_id, file_id, caption=text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())
        elif file_id and file_type == "video":
            await bot.send_video(chat_id, file_id, caption=text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())
        elif file_id and file_type == "animation":
            await bot.send_animation(chat_id, file_id, caption=text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())
        elif file_id and file_type == "document":
            await bot.send_document(chat_id, file_id, caption=text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())
        else:
            await bot.send_message(chat_id, text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())
    except Exception:
        await bot.send_message(chat_id, text or "Welcome to MUZA4R Bot!", reply_markup=main_keyboard())


@dp.callback_query(F.data == "consent:accept")
async def accept_terms(callback: CallbackQuery):
    with db() as con:
        con.execute("UPDATE users SET accepted=1 WHERE user_id=?", (callback.from_user.id,))
    await callback.answer("Accepted")
    await callback.message.answer(
        "Do you also consent to the owner seeing the actual text you format and your AI questions/responses? "
        "Choose No to keep those contents private; basic usage logs still apply.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Allow content logging", callback_data="consent:content_yes")],
            [InlineKeyboardButton(text="🔒 No content logging", callback_data="consent:content_no")],
        ])
    )


@dp.callback_query(F.data.in_({"consent:content_yes", "consent:content_no"}))
async def content_consent(callback: CallbackQuery):
    value = 1 if callback.data.endswith("yes") else 0
    with db() as con:
        con.execute("UPDATE users SET content_consent=? WHERE user_id=?", (value, callback.from_user.id))
    await callback.answer("Preference saved")
    await callback.message.answer("Welcome to MUZA4R Bot! 👑", reply_markup=main_keyboard())


@dp.callback_query(F.data == "consent:decline")
async def decline_terms(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("You have not accepted the notice, so bot features remain unavailable. You can use /start if you change your mind.")


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if not await require_user(message):
        return
    await message.answer(
        "<b>👑 MUZA4R BOT — HELP</b>\n\n"
        "/start — Start bot\n/help — Help and commands\n/profile — Your profile\n/stats — Basic statistics\n"
        "/qr — Generate QR code\n/password — Generate random password\n/format — Format text\n"
        "/webcheck — Check a public website\n/urlinfo — Get URL details\n/ai — Ask AI\n/files — File sharing help\n"
        "/admin — Owner panel (owner only)\n/users — List users (owner only)\n/logs — Recent logs (owner only)\n"
        "/ban USER_ID — Ban user (owner only)\n/unban USER_ID — Unban user (owner only)\n"
        "/apistatus — AI API status (owner only)\n\n"
        "Created by <b>Muzaffar [MUZA4R]</b>",
        reply_markup=main_keyboard()
    )


@dp.message(Command("profile"))
async def profile_cmd(message: Message):
    if not await require_user(message):
        return
    row = user_row(message.from_user.id)
    with db() as con:
        counts = con.execute("SELECT action,COUNT(*) n FROM logs WHERE user_id=? GROUP BY action", (message.from_user.id,)).fetchall()
        files = con.execute("SELECT COUNT(*) n FROM files WHERE user_id=?", (message.from_user.id,)).fetchone()["n"]
    text = "\n".join(f"• {r['action']}: {r['n']}" for r in counts) or "No activity yet."
    await message.answer(
        f"👤 <b>YOUR PROFILE</b>\nName: {message.from_user.first_name}\n"
        f"Username: @{message.from_user.username or 'not set'}\nID: <code>{message.from_user.id}</code>\n"
        f"Status: {'Banned' if row['banned'] else 'Active'}\nFiles uploaded: {files}\n\n<b>Activity</b>\n{text}"
    )


@dp.message(Command("stats"))
async def stats_cmd(message: Message):
    if not await require_user(message):
        return
    total, active, banned, files, logs = user_summary()
    await message.answer(f"📊 <b>BOT STATISTICS</b>\nRegistered users: {total}\nActive accounts: {active}\nFiles received: {files}")


@dp.message(Command("qr"))
async def qr_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.qr)
    await message.answer("Send the text or URL to encode as a QR code.")


@dp.message(Form.qr)
async def qr_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    value = (message.text or "").strip()
    if not value or len(value) > 2000:
        await message.answer("Please send text/URL up to 2000 characters.")
        return
    img = qrcode.make(value)
    from io import BytesIO
    b = BytesIO()
    img.save(b, format="PNG")
    await message.answer_photo(BufferedInputFile(b.getvalue(), filename="muza4r_qr.png"), caption="🔳 QR generated by MUZA4R.")
    row = user_row(message.from_user.id)
    details = f"content length={len(value)}"
    content = f"🔳 QR content submitted by user <code>{message.from_user.id}</code>:\n<code>{value[:2500]}</code>" if row["content_consent"] else None
    await owner_log(message.from_user.id, "QR generated", details, content)
    await state.clear()


@dp.message(Command("password"))
async def password_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.password)
    await message.answer("Send password length (8–64). The generated password is not logged or sent to the owner.")


@dp.message(Form.password)
async def password_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    try:
        length = int((message.text or "").strip())
        if not 8 <= length <= 64: raise ValueError
    except ValueError:
        await message.answer("Enter a number from 8 to 64.")
        return
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    password = "".join(secrets.choice(alphabet) for _ in range(length))
    await message.answer(f"🔐 <b>Your generated password</b>\n<code>{password}</code>\n\nSave it securely. MUZA4R does not store it.")
    await owner_log(message.from_user.id, "Password generated", f"length={length}; value not stored")
    await state.clear()


@dp.message(Command("format"))
async def format_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.formatter)
    await message.answer("Send text to format. I will return bold, italic and monospace versions.")


@dp.message(Form.formatter)
async def format_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    text_in = (message.text or "").strip()
    if not text_in or len(text_in) > 3000:
        await message.answer("Please send text up to 3000 characters.")
        return
    # Escape HTML to avoid user text being interpreted as tags.
    from html import escape
    escaped = escape(text_in)
    await message.answer(f"<b>Bold:</b>\n<b>{escaped}</b>\n\n<i>Italic:</i>\n<i>{escaped}</i>\n\n<code>Monospace:</code>\n<code>{escaped}</code>")
    row = user_row(message.from_user.id)
    content = f"📝 Text formatted by user <code>{message.from_user.id}</code>:\n{escaped[:3000]}" if row["content_consent"] else None
    await owner_log(message.from_user.id, "Text formatted", f"characters={len(text_in)}; formats=bold,italic,monospace", content)
    await state.clear()


@dp.message(Command("webcheck"))
async def webcheck_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.website)
    await message.answer("Send a public website URL (http:// or https://). Private/local network addresses are blocked.")


@dp.message(Form.website)
async def webcheck_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    url = (message.text or "").strip()
    valid, parsed = await safe_public_url(url)
    if not valid:
        await message.answer("Invalid URL or private/local host. Please send a public http/https website.")
        return
    started = time.monotonic()
    status = "Unknown"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "MUZA4R-Website-Checker/1.0"}) as session:
            async with session.get(url, allow_redirects=False) as resp:
                status = f"HTTP {resp.status}"
    except Exception as e:
        status = f"Could not connect ({type(e).__name__})"
    ms = int((time.monotonic() - started) * 1000)
    await message.answer(f"🌐 <b>Website Check</b>\nURL: {url}\nResult: {status}\nTime: {ms} ms")
    await owner_log(message.from_user.id, "Website checked", f"host={parsed.hostname}; result={status}; {ms}ms")
    await state.clear()


@dp.message(Command("urlinfo"))
async def urlinfo_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.urlinfo)
    await message.answer("Send a URL to inspect its basic structure. Do not include passwords or private tokens in URLs.")


@dp.message(Form.urlinfo)
async def urlinfo_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    raw = (message.text or "").strip()
    p = urlparse(raw)
    if p.scheme not in ("http", "https") or not p.hostname:
        await message.answer("Please send a valid http:// or https:// URL.")
        return
    safe_url = f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "") + (p.path or "/")
    await message.answer(f"🔎 <b>URL Information</b>\nHost: {p.hostname}\nScheme: {p.scheme}\nPath: {p.path or '/'}\nQuery parameters: {len(p.query.split('&')) if p.query else 0}\nFragment present: {'Yes' if p.fragment else 'No'}")
    await owner_log(message.from_user.id, "URL info", f"host={p.hostname}; scheme={p.scheme}; query_params={len(p.query.split('&')) if p.query else 0}")
    await state.clear()


@dp.message(Command("ai"))
async def ai_cmd(message: Message, state: FSMContext):
    if not await require_user(message): return
    await state.set_state(Form.ai)
    await message.answer("Send your AI question. Do not send secrets or sensitive personal information.")


async def call_ai(prompt):
    provider = get_setting("ai_provider")
    key = get_setting("ai_api_key")
    model = get_setting("ai_model")
    endpoint = get_setting("ai_endpoint")
    if not provider or not key:
        return None, "AI API is not configured yet. The owner can add it in /admin → AI API Settings."
    timeout = aiohttp.ClientTimeout(total=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if provider == "openai":
            url = endpoint or "https://api.openai.com/v1/chat/completions"
            payload = {"model": model or "gpt-4o-mini", "messages": [
                {"role": "system", "content": "You are MUZA4R AI. Be helpful and accurate. Do not claim you were made by another developer. Keep answers appropriate."},
                {"role": "user", "content": prompt}], "temperature": 0.7}
            async with session.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    return None, f"API error (HTTP {resp.status}). Check the provider, key, and model."
                return data["choices"][0]["message"]["content"], None
        if provider == "gemini":
            mdl = model or "gemini-2.5-flash"
            url = endpoint or f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent"
            if "key=" not in url:
                url += ("&" if "?" in url else "?") + "key=" + key
            payload = {"system_instruction": {"parts": [{"text": "You are MUZA4R AI. Be helpful and accurate. Do not claim you were made by another developer."}]},
                       "contents": [{"parts": [{"text": prompt}]}]}
            async with session.post(url, json=payload) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    return None, f"API error (HTTP {resp.status}). Check the provider, key, and model."
                return data["candidates"][0]["content"]["parts"][0]["text"], None
    return None, "Unsupported AI provider."


@dp.message(Form.ai)
async def ai_input(message: Message, state: FSMContext):
    if not await require_user(message): return
    prompt = (message.text or "").strip()
    if not prompt or len(prompt) > 8000:
        await message.answer("Please send a text question up to 8000 characters.")
        return
    await message.answer("🤖 MUZA4R AI is thinking...")
    answer, error = await call_ai(prompt)
    if error:
        await message.answer(error)
        await owner_log(message.from_user.id, "AI request", "failed: API not configured or request error")
    else:
        answer = answer[:3800]
        await message.answer(f"{answer}\n\n━━━━━━━━━━━━━━\n👑 <b>Created by Muzaffar [MUZA4R]</b>")
        row = user_row(message.from_user.id)
        details = "response generated; content not included in log"
        content = None
        if row["content_consent"]:
            content = f"🤖 <b>AI conversation (user consented)</b>\nUser ID: <code>{message.from_user.id}</code>\n\n<b>Question:</b>\n{prompt[:2500]}\n\n<b>Response:</b>\n{answer[:2500]}"
            details = "response generated; user consented to content logging"
        await owner_log(message.from_user.id, "AI request", details, content)
    await state.clear()


@dp.message(Command("files"))
async def files_cmd(message: Message):
    if not await require_user(message): return
    await message.answer("📁 Send a photo, video, document, audio, voice note, animation/GIF or other supported Telegram attachment here. The bot will copy uploaded files to its owner, as stated in the notice.")


@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    upsert_user(message.from_user)
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    await message.answer("👑 <b>MUZA4R OWNER PANEL</b>", reply_markup=owner_keyboard())


@dp.callback_query(F.data == "tool:home")
async def home_cb(callback: CallbackQuery):
    if not await require_callback_user(callback): return
    await callback.message.answer("👑 MUZA4R BOT — Main Menu", reply_markup=main_keyboard())
    await callback.answer()


async def require_callback_user(callback):
    upsert_user(callback.from_user)
    if is_banned(callback.from_user.id):
        await callback.answer("Access restricted.", show_alert=True)
        return False
    row = user_row(callback.from_user.id)
    if not row or not row["accepted"]:
        await callback.answer("Please use /start and accept the notice first.", show_alert=True)
        return False
    return True


@dp.callback_query(F.data.startswith("tool:"))
async def tool_cb(callback: CallbackQuery, state: FSMContext):
    if not await require_callback_user(callback):
        return
    tool = callback.data.split(":", 1)[1]
    await callback.answer()
    if tool == "help":
        await callback.message.answer("Use /help for all commands.", reply_markup=main_keyboard())
    elif tool == "profile":
        row = user_row(callback.from_user.id)
        await callback.message.answer(f"👤 <b>Your Profile</b>\nName: {callback.from_user.first_name}\nID: <code>{callback.from_user.id}</code>\nStatus: Active")
    elif tool == "stats":
        total, active, banned, files, logs = user_summary()
        await callback.message.answer(f"📊 Registered users: {total}\nFiles received: {files}")
    elif tool == "files":
        await callback.message.answer("📁 Send a photo, video, document, audio, voice note, GIF or other supported attachment. Uploaded files are copied to the bot owner.")
    else:
        state_map = {"ai": (Form.ai, "Send your AI question."),
                     "qr": (Form.qr, "Send text or URL for QR generation."),
                     "password": (Form.password, "Send password length (8–64)."),
                     "format": (Form.formatter, "Send text to format."),
                     "webcheck": (Form.website, "Send a public http/https URL."),
                     "urlinfo": (Form.urlinfo, "Send a URL to inspect.")}
        if tool in state_map:
            form_state, prompt = state_map[tool]
            await state.set_state(form_state)
            await callback.message.answer(prompt)


@dp.callback_query(F.data.startswith("own:"))
async def owner_cb(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only.", show_alert=True)
        return
    await callback.answer()
    data = callback.data.split(":")
    action = data[1]
    if action == "home":
        await callback.message.answer("👑 Owner Panel", reply_markup=owner_keyboard())
    elif action == "stats":
        total, active, banned, files, logs = user_summary()
        await callback.message.answer(f"📊 <b>Statistics</b>\nUsers: {total}\nAccepted/active: {active}\nBanned: {banned}\nFiles: {files}\nLog events: {logs}")
    elif action == "users":
        offset = int(data[2]) if len(data) > 2 else 0
        with db() as con:
            rows = con.execute("SELECT * FROM users ORDER BY first_seen DESC LIMIT 10 OFFSET ?", (offset,)).fetchall()
            total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        lines = [f"👥 <b>Users ({offset+1}-{min(offset+10,total)} of {total})</b>"]
        for r in rows:
            lines.append(f"• {r['first_name']} @{r['username'] or 'none'} — <code>{r['user_id']}</code> — {'🚫 Banned' if r['banned'] else '🟢 Active'}")
        buttons = []
        nav = []
        if offset > 0: nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"own:users:{max(0,offset-10)}"))
        if offset + 10 < total: nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"own:users:{offset+10}"))
        if nav: buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="🚫 Ban User", callback_data="own:ban"), InlineKeyboardButton(text="✅ Unban User", callback_data="own:unban")])
        buttons.append([InlineKeyboardButton(text="🔙 Admin Panel", callback_data="own:home")])
        await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "logs":
        with db() as con:
            rows = con.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 15").fetchall()
        lines = ["📋 <b>Recent Activity Logs</b>"]
        for r in rows:
            lines.append(f"#{r['id']} | <code>{r['user_id']}</code> | {r['action']} | {r['created_at']}\n{r['details'][:150]}")
        await callback.message.answer("\n\n".join(lines)[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin Panel", callback_data="own:home")]]))
    elif action == "files":
        with db() as con:
            rows = con.execute("SELECT * FROM files ORDER BY id DESC LIMIT 15").fetchall()
        lines = ["📁 <b>Recent Uploaded Files</b>"]
        for r in rows:
            lines.append(f"#{r['id']} | user <code>{r['user_id']}</code> | {r['file_type']} | {r['file_name'] or '(unnamed)'} | {r['created_at']}")
        await callback.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Admin Panel", callback_data="own:home")]]))
    elif action == "welcome":
        await callback.message.answer("⚙️ <b>Welcome Settings</b>", reply_markup=welcome_keyboard())
    elif action == "api":
        await callback.message.answer(
            f"🤖 <b>AI API Settings</b>\nProvider: {get_setting('ai_provider') or 'Not set'}\n"
            f"Key: {'Configured (hidden)' if get_setting('ai_api_key') else 'Not configured'}\n"
            f"Model: {get_setting('ai_model') or 'Default'}",
            reply_markup=api_keyboard()
        )
    elif action == "broadcast":
        await state.set_state(Form.broadcast)
        await callback.message.answer("Send the announcement text to broadcast to users. This sends a message to all users who accepted the notice." )
    elif action == "ban":
        await state.set_state(Form.ban_id)
        await callback.message.answer("Send the Telegram user ID to ban.")
    elif action == "unban":
        await state.set_state(Form.unban_id)
        await callback.message.answer("Send the Telegram user ID to unban.")


@dp.callback_query(F.data.startswith("wel:"))
async def welcome_cb(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only.", show_alert=True)
        return
    await callback.answer()
    action = callback.data.split(":")[1]
    if action == "text":
        await state.set_state(Form.welcome_text)
        await callback.message.answer("Send the new welcome text. Use {first_name} to insert the user's first name. HTML formatting is supported.")
    elif action == "media":
        await state.set_state(Form.welcome_media)
        await callback.message.answer("Send one photo, video, GIF/animation or document to use with the welcome message.")
    elif action == "preview":
        await send_welcome(callback.from_user.id, callback.from_user.first_name)
    elif action == "remove_media":
        set_setting("welcome_file_id", "")
        set_setting("welcome_file_type", "")
        await callback.message.answer("✅ Welcome media removed.")
    elif action == "remove_text":
        set_setting("welcome_text", "")
        await callback.message.answer("✅ Custom welcome text removed. The bot will show a simple fallback message.")


@dp.callback_query(F.data.startswith("api:"))
async def api_cb(callback: CallbackQuery, state: FSMContext):
    if not is_owner(callback.from_user.id):
        await callback.answer("Owner only.", show_alert=True)
        return
    await callback.answer()
    action = callback.data.split(":")[1]
    if action in ("openai", "gemini"):
        set_setting("ai_provider", action)
        if action == "openai":
            set_setting("ai_endpoint", "https://api.openai.com/v1/chat/completions")
            if not get_setting("ai_model"): set_setting("ai_model", "gpt-4o-mini")
        else:
            set_setting("ai_endpoint", "")
            if not get_setting("ai_model"): set_setting("ai_model", "gemini-2.5-flash")
        await callback.message.answer(f"✅ Provider selected: {action.title()}. Now add your API key.")
    elif action == "key":
        await state.set_state(Form.api_key)
        await callback.message.answer("Send the API key in this private owner chat. It will be stored in the bot's SQLite database and hidden from bot replies/logs. For stronger security, use hosting environment secrets instead.")
    elif action == "model":
        await state.set_state(Form.api_model)
        await callback.message.answer("Send the model name (for example, gpt-4o-mini or gemini-2.5-flash).")
    elif action == "test":
        if not get_setting("ai_api_key") or not get_setting("ai_provider"):
            await callback.message.answer("❌ Select a provider and add an API key first.")
        else:
            result, error = await call_ai("Reply with exactly: MUZA4R API test successful")
            await callback.message.answer("❌ Test failed: " + error if error else "✅ API test successful. Response received.")
    elif action == "remove":
        set_setting("ai_api_key", "")
        set_setting("ai_provider", "")
        set_setting("ai_model", "")
        set_setting("ai_endpoint", "")
        await callback.message.answer("✅ AI API configuration removed.")


@dp.message(Form.welcome_text)
async def save_welcome_text(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    text = message.text or ""
    if len(text) > 1000:
        await message.answer("Welcome text must be under 1000 characters.")
        return
    set_setting("welcome_text", text)
    await state.clear()
    await message.answer("✅ Welcome text saved.", reply_markup=welcome_keyboard())


@dp.message(Form.welcome_media)
async def save_welcome_media(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    file_id, file_type = "", ""
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
    elif message.video:
        file_id, file_type = message.video.file_id, "video"
    elif message.animation:
        file_id, file_type = message.animation.file_id, "animation"
    elif message.document:
        file_id, file_type = message.document.file_id, "document"
    else:
        await message.answer("Please send a photo, video, GIF/animation or document.")
        return
    set_setting("welcome_file_id", file_id)
    set_setting("welcome_file_type", file_type)
    await state.clear()
    await message.answer("✅ Welcome media saved.", reply_markup=welcome_keyboard())


@dp.message(Form.api_key)
async def save_api_key(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    key = (message.text or "").strip()
    if len(key) < 10 or len(key) > 500:
        await message.answer("That key does not look valid. Please send the API key again.")
        return
    set_setting("ai_api_key", key)
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("✅ API key saved. The message was deleted if Telegram allowed it. Use Test Configuration to check the key.")


@dp.message(Form.api_model)
async def save_api_model(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    model = (message.text or "").strip()
    if not model or len(model) > 120:
        await message.answer("Please send a valid model name.")
        return
    set_setting("ai_model", model)
    await state.clear()
    await message.answer("✅ Model saved.")


@dp.message(Form.ban_id)
async def ban_user(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    try: target = int((message.text or "").strip())
    except ValueError:
        await message.answer("Send a numeric Telegram user ID.")
        return
    if target == OWNER_ID:
        await message.answer("You cannot ban the owner.")
        return
    with db() as con:
        cur = con.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
    await state.clear()
    await message.answer("✅ User banned." if cur.rowcount else "User not found in the bot database.")


@dp.message(Form.unban_id)
async def unban_user(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    try: target = int((message.text or "").strip())
    except ValueError:
        await message.answer("Send a numeric Telegram user ID.")
        return
    with db() as con:
        cur = con.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
    await state.clear()
    await message.answer("✅ User unbanned." if cur.rowcount else "User not found in the bot database.")


@dp.message(Form.broadcast)
async def broadcast(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id): return
    text = message.text or ""
    if not text:
        await message.answer("Please send a text announcement.")
        return
    with db() as con:
        users = con.execute("SELECT user_id FROM users WHERE accepted=1 AND banned=0").fetchall()
    success = 0
    for row in users:
        try:
            await bot.send_message(row["user_id"], text)
            success += 1
            await asyncio.sleep(0.04)
        except Exception:
            pass
    await state.clear()
    await message.answer(f"📢 Broadcast finished. Sent to {success}/{len(users)} users.")


@dp.message(F.from_user)
async def catch_uploads(message: Message):
    if not message.from_user:
        return
    upsert_user(message.from_user)
    if is_banned(message.from_user.id):
        return
    row = user_row(message.from_user.id)
    if not row or not row["accepted"]:
        return
    # Ignore ordinary text; file/media types are copied to the owner.
    file_id, file_type, file_name = "", "", ""
    if message.photo:
        file_id, file_type = message.photo[-1].file_id, "photo"
        file_name = "photo.jpg"
    elif message.video:
        file_id, file_type = message.video.file_id, "video"
        file_name = message.video.file_name or "video"
    elif message.document:
        file_id, file_type = message.document.file_id, "document"
        file_name = message.document.file_name or "document"
    elif message.audio:
        file_id, file_type = message.audio.file_id, "audio"
        file_name = message.audio.file_name or message.audio.title or "audio"
    elif message.voice:
        file_id, file_type = message.voice.file_id, "voice"
        file_name = "voice.ogg"
    elif message.video_note:
        file_id, file_type = message.video_note.file_id, "video_note"
        file_name = "video_note.mp4"
    elif message.animation:
        file_id, file_type = message.animation.file_id, "animation"
        file_name = message.animation.file_name or "animation.gif"
    elif message.sticker:
        file_id, file_type = message.sticker.file_id, "sticker"
        file_name = "sticker"
    else:
        return
    with db() as con:
        con.execute("INSERT INTO files(user_id,file_id,file_type,file_name,created_at) VALUES(?,?,?,?,?)",
                    (message.from_user.id, file_id, file_type, file_name, now()))
    await owner_log(message.from_user.id, "File uploaded", f"type={file_type}; name={file_name}")
    try:
        await bot.send_message(OWNER_ID,
            f"📥 <b>NEW FILE UPLOAD</b>\n👤 {message.from_user.first_name}\n"
            f"Username: @{message.from_user.username or 'none'}\n🆔 <code>{message.from_user.id}</code>\n"
            f"📎 {file_name}\n📦 Type: {file_type}\n🕒 {now()}")
        await bot.copy_message(OWNER_ID, message.chat.id, message.message_id)
    except Exception:
        pass


@dp.message(Command("users"))
async def users_cmd(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    with db() as con:
        rows = con.execute("SELECT user_id,first_name,username,banned FROM users ORDER BY first_seen DESC LIMIT 30").fetchall()
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    lines = [f"👥 <b>Registered users: {total}</b>"]
    for r in rows:
        lines.append(f"{r['first_name']} @{r['username'] or 'none'} — <code>{r['user_id']}</code> — {'🚫' if r['banned'] else '🟢'}")
    await message.answer("\n".join(lines)[:4000], reply_markup=owner_keyboard())


@dp.message(Command("logs"))
async def logs_cmd(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    with db() as con:
        rows = con.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 15").fetchall()
    lines = ["📋 <b>Recent Logs</b>"]
    for r in rows:
        lines.append(f"{r['created_at']} | <code>{r['user_id']}</code> | {r['action']} | {r['details'][:120]}")
    await message.answer("\n".join(lines)[:4000])


@dp.message(Command("ban"))
async def ban_cmd(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /ban USER_ID")
        return
    try: target = int(parts[1])
    except ValueError:
        await message.answer("User ID must be numeric.")
        return
    if target == OWNER_ID:
        await message.answer("You cannot ban the owner.")
        return
    with db() as con:
        cur = con.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
    await message.answer("✅ Banned." if cur.rowcount else "User not found.")


@dp.message(Command("unban"))
async def unban_cmd(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /unban USER_ID")
        return
    try: target = int(parts[1])
    except ValueError:
        await message.answer("User ID must be numeric.")
        return
    with db() as con:
        cur = con.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
    await message.answer("✅ Unbanned." if cur.rowcount else "User not found.")


@dp.message(Command("apistatus"))
async def apistatus_cmd(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Owner only.")
        return
    await message.answer(
        f"Provider: {get_setting('ai_provider') or 'Not configured'}\n"
        f"API key: {'Configured (hidden)' if get_setting('ai_api_key') else 'Not configured'}\n"
        f"Model: {get_setting('ai_model') or 'Default'}"
    )


async def main():
    init_db()
    print("MUZA4R ALL-IN-ONE BOT is starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
