import os
import re
import ast
import math
import asyncio
import secrets
import sqlite3
import string
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.exceptions import TelegramAPIError


# =====================================================
# CONFIGURATION
# =====================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "5839586443"))
BOT_USERNAME = os.getenv(
    "BOT_USERNAME", "MUZA4RGROUPMANAGERbot"
).lstrip("@")

CONTACT_USERNAME = "Contactmuzaffarbot"
DB_PATH = os.getenv("DB_PATH", "muza4r.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable missing! "
        "Add BOT_TOKEN in your hosting variables."
    )

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# =====================================================
# DATABASE
# =====================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    joined_at TEXT,
    banned INTEGER DEFAULT 0,
    premium_until TEXT
);

CREATE TABLE IF NOT EXISTS files (
    code TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    file_name TEXT DEFAULT '',
    caption TEXT DEFAULT '',
    uploader_id INTEGER,
    created_at TEXT,
    downloads INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS redeem_codes (
    code TEXT PRIMARY KEY,
    days INTEGER DEFAULT 30,
    uses_left INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS redemptions (
    user_id INTEGER,
    code TEXT,
    PRIMARY KEY(user_id, code)
);

CREATE TABLE IF NOT EXISTS referrals (
    user_id INTEGER PRIMARY KEY,
    referred_by INTEGER
);
""")
db.commit()


# =====================================================
# GENERAL HELPERS
# =====================================================

def now():
    return datetime.utcnow()


def now_iso():
    return now().isoformat()


def is_owner(user_id):
    return user_id == OWNER_ID


def is_admin(user_id):
    if is_owner(user_id):
        return True

    row = db.execute(
        "SELECT 1 FROM admins WHERE user_id=?",
        (user_id,)
    ).fetchone()

    return row is not None


def is_banned(user_id):
    row = db.execute(
        "SELECT banned FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    return bool(row and row["banned"])


def register_user(user):
    db.execute("""
        INSERT INTO users(user_id, username, first_name, joined_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name
    """, (
        user.id,
        user.username or "",
        user.first_name or "",
        now_iso()
    ))
    db.commit()


def get_setting(key, default=""):
    row = db.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()

    return row["value"] if row else default


def set_setting(key, value):
    db.execute("""
        INSERT INTO settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    db.commit()


def make_code(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def file_link(code):
    return f"https://t.me/{BOT_USERNAME}?start=file_{code}"


def user_count():
    return db.execute(
        "SELECT COUNT(*) AS n FROM users"
    ).fetchone()["n"]


def file_count():
    return db.execute(
        "SELECT COUNT(*) AS n FROM files"
    ).fetchone()["n"]


def premium_active(user_id):
    row = db.execute(
        "SELECT premium_until FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    if not row or not row["premium_until"]:
        return False

    try:
        expiry = datetime.fromisoformat(row["premium_until"])
        return expiry > now()
    except ValueError:
        return False


def grant_premium(user_id, days):
    row = db.execute(
        "SELECT premium_until FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()

    start = now()

    if row and row["premium_until"]:
        try:
            existing = datetime.fromisoformat(row["premium_until"])
            if existing > start:
                start = existing
        except ValueError:
            pass

    expiry = start + timedelta(days=days)

    db.execute(
        "UPDATE users SET premium_until=? WHERE user_id=?",
        (expiry.isoformat(), user_id)
    )
    db.commit()

    return expiry


# =====================================================
# KEYBOARDS
# =====================================================

def main_keyboard(user_id):
    rows = [
        [
            KeyboardButton(text="📁 File Sharing"),
            KeyboardButton(text="🧰 Extra Tools")
        ],
        [
            KeyboardButton(text="👤 My Account"),
            KeyboardButton(text="⭐ Premium")
        ],
        [
            KeyboardButton(text="📊 Statistics"),
            KeyboardButton(text="🎟️ Redeem Code")
        ],
        [
            KeyboardButton(text="🎁 Refer & Earn"),
            KeyboardButton(text="📢 Updates")
        ],
        [
            KeyboardButton(text="👑 MUZA4R"),
            KeyboardButton(text="ℹ️ Help")
        ]
    ]

    if is_admin(user_id):
        rows.insert(4, [KeyboardButton(text="👑 Admin Panel")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True
    )


def contact_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="👑 Contact MUZA4R",
                url=f"https://t.me/{CONTACT_USERNAME}"
            )
        ]
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📊 Dashboard",
                callback_data="admin_stats"
            ),
            InlineKeyboardButton(
                text="👥 Users",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                text="📁 Files",
                callback_data="admin_files"
            ),
            InlineKeyboardButton(
                text="📢 Broadcast",
                callback_data="admin_broadcast"
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Edit Welcome",
                callback_data="admin_welcome"
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Main Menu",
                callback_data="home"
            )
        ]
    ])


# =====================================================
# SIMPLE USER WORKFLOWS
# =====================================================

# Admin workflows are stored in memory. If the process
# restarts, an unfinished workflow must be started again.
workflows = {}


async def show_home(message: Message):
    uid = message.from_user.id

    welcome = get_setting(
        "welcome",
        "Assalamualaikum {name}! ❤️\n\n"
        "Welcome to MUZA4R ULTIMATE BOT.\n"
        "Choose an option below."
    )

    try:
        welcome = welcome.replace(
            "{name}", message.from_user.first_name or "User"
        )
    except Exception:
        pass

    await message.answer(
        welcome,
        reply_markup=main_keyboard(uid)
    )


# =====================================================
# START COMMAND + REFERRALS + FILE LINKS
# =====================================================

@dp.message(Command("start"))
async def start_handler(
    message: Message,
    command: CommandObject
):
    user = message.from_user
    register_user(user)

    if is_banned(user.id):
        await message.answer("⛔ Aapka access disable hai.")
        return

    payload = command.args or ""

    # File deep link: /start file_CODE
    if payload.startswith("file_"):
        code = payload[5:]

        row = db.execute(
            "SELECT * FROM files WHERE code=?",
            (code,)
        ).fetchone()

        if not row:
            await message.answer(
                "❌ File nahi mili. Link invalid ya file deleted hai."
            )
            return

        try:
            caption = row["caption"] or row["file_name"] or "Your file"

            if row["file_type"] == "document":
                await message.answer_document(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "photo":
                await message.answer_photo(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "video":
                await message.answer_video(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "audio":
                await message.answer_audio(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "voice":
                await message.answer_voice(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "animation":
                await message.answer_animation(
                    row["file_id"], caption=caption
                )
            elif row["file_type"] == "video_note":
                await message.answer_video_note(row["file_id"])
            elif row["file_type"] == "sticker":
                await message.answer_sticker(row["file_id"])
            else:
                await message.answer(
                    "❌ Is file type ko support nahi kiya gaya."
                )
                return

            db.execute(
                "UPDATE files SET downloads=downloads+1 WHERE code=?",
                (code,)
            )
            db.commit()

        except TelegramAPIError:
            await message.answer(
                "❌ File send nahi hui. Admin se contact karein."
            )

        return

    # Referral: /start ref_USERID
    if payload.startswith("ref_"):
        try:
            referrer_id = int(payload[4:])

            if referrer_id != user.id:
                already = db.execute(
                    "SELECT 1 FROM referrals WHERE user_id=?",
                    (user.id,)
                ).fetchone()

                referrer_exists = db.execute(
                    "SELECT 1 FROM users WHERE user_id=?",
                    (referrer_id,)
                ).fetchone()

                if not already and referrer_exists:
                    db.execute(
                        "INSERT INTO referrals(user_id, referred_by) "
                        "VALUES (?, ?)",
                        (user.id, referrer_id)
                    )
                    db.commit()

        except (ValueError, sqlite3.IntegrityError):
            pass

    await show_home(message)


# =====================================================
# FILE SHARING
# =====================================================

@dp.message(Command("savefile"))
async def savefile_help(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(
            "⛔ Sirf admin files save kar sakta hai."
        )
        return

    await message.answer(
        "📁 File Sharing\n\n"
        "Ab mujhe document, photo, video, audio ya supported "
        "media bhejo. Bot automatic share link generate karega."
    )


@dp.message(
    F.document | F.photo | F.video | F.audio |
    F.voice | F.animation | F.video_note | F.sticker
)
async def file_upload_handler(message: Message):
    uid = message.from_user.id

    # Non-admin uploads are not stored.
    if not is_admin(uid):
        return

    file_id = None
    file_type = None
    file_name = ""

    if message.document:
        file_id = message.document.file_id
        file_type = "document"
        file_name = message.document.file_name or "document"

    elif message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
        file_name = "photo"

    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
        file_name = message.video.file_name or "video"

    elif message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
        file_name = message.audio.file_name or "audio"

    elif message.voice:
        file_id = message.voice.file_id
        file_type = "voice"
        file_name = "voice message"

    elif message.animation:
        file_id = message.animation.file_id
        file_type = "animation"
        file_name = message.animation.file_name or "animation"

    elif message.video_note:
        file_id = message.video_note.file_id
        file_type = "video_note"
        file_name = "video note"

    elif message.sticker:
        file_id = message.sticker.file_id
        file_type = "sticker"
        file_name = "sticker"

    if not file_id:
        return

    code = make_code()

    db.execute("""
        INSERT INTO files(
            code, file_id, file_type, file_name,
            caption, uploader_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        code,
        file_id,
        file_type,
        file_name,
        message.caption or "",
        uid,
        now_iso()
    ))
    db.commit()

    await message.answer(
        "✅ File saved successfully!\n\n"
        f"📄 Name: {file_name}\n"
        f"🆔 Code: `{code}`\n\n"
        f"🔗 Share Link:\n{file_link(code)}",
        parse_mode="Markdown"
    )


@dp.message(Command("deletefile"))
async def delete_file_command(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    code = (command.args or "").strip()

    if not code:
        await message.answer("Use: /deletefile FILE_CODE")
        return

    cur = db.execute(
        "DELETE FROM files WHERE code=?",
        (code,)
    )
    db.commit()

    if cur.rowcount:
        await message.answer("✅ File deleted.")
    else:
        await message.answer("❌ File code nahi mila.")


# =====================================================
# ACCOUNT + STATISTICS
# =====================================================

@dp.message(F.text == "👤 My Account")
async def account_handler(message: Message):
    uid = message.from_user.id

    register_user(message.from_user)

    row = db.execute(
        "SELECT * FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()

    premium = "Active" if premium_active(uid) else "Inactive"

    referrals = db.execute(
        "SELECT COUNT(*) AS n FROM referrals WHERE referred_by=?",
        (uid,)
    ).fetchone()["n"]

    await message.answer(
        "👤 MY ACCOUNT\n\n"
        f"🆔 User ID: {uid}\n"
        f"👤 Name: {message.from_user.first_name}\n"
        f"🔗 Username: @{message.from_user.username or 'Not set'}\n"
        f"⭐ Premium: {premium}\n"
        f"🎁 Referrals: {referrals}",
        reply_markup=main_keyboard(uid)
    )


@dp.message(F.text == "📊 Statistics")
async def statistics_handler(message: Message):
    await message.answer(
        "📊 BOT STATISTICS\n\n"
        f"👥 Total users: {user_count()}\n"
        f"📁 Total files: {file_count()}"
    )


# =====================================================
# CONTACT + HELP + PREMIUM
# =====================================================

@dp.message(F.text == "👑 MUZA4R")
async def contact_handler(message: Message):
    await message.answer(
        "👑 MUZA4R\n\nContact the owner using the button below.",
        reply_markup=contact_keyboard()
    )


@dp.message(F.text == "ℹ️ Help")
async def help_handler(message: Message):
    await message.answer(
        "ℹ️ MUZA4R ULTIMATE BOT HELP\n\n"
        "📁 File Sharing: Admin uploads files to generate links.\n"
        "👤 My Account: View your account details.\n"
        "⭐ Premium: Check premium status.\n"
        "🎟️ Redeem Code: Redeem a premium code.\n"
        "🎁 Refer & Earn: Get your referral link.\n"
        "🧰 Extra Tools: Password generator, calculator and QR link.\n\n"
        "Admin commands:\n"
        "/id — Your Telegram ID\n"
        "/stats — Bot statistics\n"
        "/ban USER_ID — Ban a user\n"
        "/unban USER_ID — Unban a user\n"
        "/deletefile FILE_CODE — Delete a stored file\n"
        "/addadmin USER_ID — Add admin (owner only)\n"
        "/removeadmin USER_ID — Remove admin (owner only)\n"
        "/makecode DAYS USES — Create premium redeem code\n"
        "/broadcast — Start text broadcast workflow\n"
        "/cancel — Cancel an active workflow"
    )


@dp.message(F.text == "⭐ Premium")
async def premium_handler(message: Message):
    uid = message.from_user.id

    row = db.execute(
        "SELECT premium_until FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()

    if premium_active(uid):
        await message.answer(
            f"⭐ Your Premium is active until:\n{row['premium_until']}"
        )
    else:
        await message.answer(
            "⭐ Premium is currently inactive.\n\n"
            "A redeem code can activate Premium."
        )


# =====================================================
# REFERRAL SYSTEM
# =====================================================

@dp.message(F.text == "🎁 Refer & Earn")
async def referral_handler(message: Message):
    uid = message.from_user.id

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"

    count = db.execute(
        "SELECT COUNT(*) AS n FROM referrals WHERE referred_by=?",
        (uid,)
    ).fetchone()["n"]

    await message.answer(
        "🎁 REFER & EARN\n\n"
        f"Your referral link:\n{link}\n\n"
        f"👥 Total referrals: {count}\n\n"
        "Share your link with friends. Referral rewards are not "
        "automatically granted by this version."
    )


# =====================================================
# REDEEM CODES
# =====================================================

@dp.message(F.text == "🎟️ Redeem Code")
async def redeem_prompt(message: Message):
    workflows[message.from_user.id] = "redeem"

    await message.answer(
        "🎟️ Apna redeem code bhejo.\n/cancel se cancel kar sakte ho."
    )


@dp.message(Command("makecode"))
async def make_redeem_code(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    args = (command.args or "").split()

    if len(args) != 2:
        await message.answer("Use: /makecode DAYS USES")
        return

    try:
        days = int(args[0])
        uses = int(args[1])

        if not 1 <= days <= 3650 or not 1 <= uses <= 100000:
            raise ValueError

    except ValueError:
        await message.answer("Days aur uses valid positive numbers hone chahiye.")
        return

    code = make_code(16)

    db.execute(
        "INSERT INTO redeem_codes(code, days, uses_left) VALUES (?, ?, ?)",
        (code, days, uses)
    )
    db.commit()

    await message.answer(
        "✅ Redeem code created!\n\n"
        f"🎟️ Code: `{code}`\n"
        f"⭐ Premium days: {days}\n"
        f"🔢 Uses: {uses}",
        parse_mode="Markdown"
    )


# =====================================================
# EXTRA TOOLS
# =====================================================

@dp.message(F.text == "🧰 Extra Tools")
async def extra_tools_handler(message: Message):
    await message.answer(
        "🧰 EXTRA TOOLS\n\n"
        "/password — Generate a random password\n"
        "/calc EXPRESSION — Calculate arithmetic\n"
        "/qr URL — Create a QR-code link\n"
        "/site URL — Check website response\n\n"
        "Examples:\n"
        "/calc (25 + 5) * 2\n"
        "/qr https://example.com\n"
        "/site https://example.com"
    )


@dp.message(Command("password"))
async def password_handler(message: Message):
    password = "".join(
        secrets.choice(string.ascii_letters + string.digits + "!@#$%")
        for _ in range(16)
    )

    await message.answer(
        f"🔐 Random password:\n`{password}`",
        parse_mode="Markdown"
    )


# Safe calculator: only basic arithmetic AST nodes allowed.
def safe_calculate(expression):
    if len(expression) > 100:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")

    allowed_nodes = (
        ast.Expression, ast.Constant,
        ast.BinOp, ast.UnaryOp,
        ast.Add, ast.Sub, ast.Mult, ast.Div,
        ast.FloorDiv, ast.Mod, ast.Pow,
        ast.UAdd, ast.USub
    )

    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            raise ValueError("Unsupported expression")

        if isinstance(node, ast.Constant):
            if type(node.value) not in (int, float):
                raise ValueError("Numbers only")

        if isinstance(node, ast.Pow):
            pass

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)

        if isinstance(node, ast.Constant):
            return node.value

        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, ast.USub):
                return -value

        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)

            if abs(left) > 10**12 or abs(right) > 10**12:
                raise ValueError("Number too large")

            if isinstance(node.op, ast.Add):
                result = left + right
            elif isinstance(node.op, ast.Sub):
                result = left - right
            elif isinstance(node.op, ast.Mult):
                result = left * right
            elif isinstance(node.op, ast.Div):
                result = left / right
            elif isinstance(node.op, ast.FloorDiv):
                result = left // right
            elif isinstance(node.op, ast.Mod):
                result = left % right
            elif isinstance(node.op, ast.Pow):
                if abs(right) > 10 or abs(left) > 10**6:
                    raise ValueError("Power too large")
                result = left ** right
            else:
                raise ValueError("Unsupported operator")

            if isinstance(result, complex):
                raise ValueError("Complex results are not supported")

            if abs(result) > 10**15:
                raise ValueError("Result too large")

            return result

        raise ValueError("Invalid expression")

    result = evaluate(tree)

    if not math.isfinite(float(result)):
        raise ValueError("Result is not finite")

    return result


@dp.message(Command("calc"))
async def calculator_handler(message: Message, command: CommandObject):
    expression = (command.args or "").strip()

    if not expression:
        await message.answer("Use: /calc (25 + 5) * 2")
        return

    try:
        result = safe_calculate(expression)
        await message.answer(f"🧮 Result: {result}")
    except Exception:
        await message.answer("❌ Invalid expression.")


@dp.message(Command("qr"))
async def qr_handler(message: Message, command: CommandObject):
    url = (command.args or "").strip()

    if not url.startswith(("https://", "http://")):
        await message.answer("Use: /qr https://example.com")
        return

    from urllib.parse import quote

    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=300x300&data={quote(url, safe='')}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📱 Open QR Code",
            url=qr_url
        )
    ]])

    await message.answer(
        "📱 QR code link ready:",
        reply_markup=keyboard
    )


@dp.message(Command("site"))
async def site_handler(message: Message, command: CommandObject):
    url = (command.args or "").strip()

    if not url.startswith(("https://", "http://")):
        await message.answer("Use: /site https://example.com")
        return

    # Basic website check using Python's standard library.
    import urllib.request

    def check_site():
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "MUZA4RBot/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status

    try:
        status = await asyncio.to_thread(check_site)
        await message.answer(f"🌐 Website response status: {status}")
    except Exception:
        await message.answer(
            "⚠️ Website check failed or the server did not respond."
        )


# =====================================================
# ADMIN COMMANDS
# =====================================================

@dp.message(Command("id"))
async def id_handler(message: Message):
    await message.answer(f"🆔 Your ID: {message.from_user.id}")


@dp.message(Command("stats"))
async def stats_command(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "📊 BOT STATISTICS\n\n"
        f"👥 Users: {user_count()}\n"
        f"📁 Files: {file_count()}"
    )


@dp.message(Command("ban"))
async def ban_command(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    try:
        target = int((command.args or "").strip())
    except ValueError:
        await message.answer("Use: /ban USER_ID")
        return

    if target == OWNER_ID:
        await message.answer("❌ Owner ko ban nahi kar sakte.")
        return

    db.execute(
        "UPDATE users SET banned=1 WHERE user_id=?",
        (target,)
    )
    db.commit()

    await message.answer(f"⛔ User {target} banned (if registered).")


@dp.message(Command("unban"))
async def unban_command(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    try:
        target = int((command.args or "").strip())
    except ValueError:
        await message.answer("Use: /unban USER_ID")
        return

    db.execute(
        "UPDATE users SET banned=0 WHERE user_id=?",
        (target,)
    )
    db.commit()

    await message.answer(f"✅ User {target} unbanned (if registered).")


@dp.message(Command("addadmin"))
async def add_admin_command(message: Message, command: CommandObject):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Sirf owner ye command use kar sakta hai.")
        return

    try:
        target = int((command.args or "").strip())
    except ValueError:
        await message.answer("Use: /addadmin USER_ID")
        return

    db.execute(
        "INSERT OR IGNORE INTO admins(user_id) VALUES (?)",
        (target,)
    )
    db.commit()

    await message.answer(f"✅ Admin added: {target}")


@dp.message(Command("removeadmin"))
async def remove_admin_command(message: Message, command: CommandObject):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Sirf owner ye command use kar sakta hai.")
        return

    try:
        target = int((command.args or "").strip())
    except ValueError:
        await message.answer("Use: /removeadmin USER_ID")
        return

    if target == OWNER_ID:
        await message.answer("❌ Owner ko remove nahi kar sakte.")
        return

    db.execute("DELETE FROM admins WHERE user_id=?", (target,))
    db.commit()

    await message.answer(f"✅ Admin removed: {target}")


# =====================================================
# ADMIN PANEL
# =====================================================

@dp.message(F.text == "👑 Admin Panel")
async def admin_panel_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    await message.answer(
        "👑 MUZA4R ADMIN PANEL\n\nChoose an action:",
        reply_markup=admin_keyboard()
    )


@dp.callback_query(F.data.startswith("admin_"))
async def admin_callback_handler(callback: CallbackQuery):
    uid = callback.from_user.id

    if not is_admin(uid):
        await callback.answer("Access denied", show_alert=True)
        return

    action = callback.data

    if action == "admin_stats":
        await callback.message.answer(
            "📊 DASHBOARD\n\n"
            f"👥 Users: {user_count()}\n"
            f"📁 Files: {file_count()}"
        )

    elif action == "admin_users":
        rows = db.execute(
            "SELECT user_id, username, first_name "
            "FROM users ORDER BY joined_at DESC LIMIT 20"
        ).fetchall()

        if not rows:
            await callback.message.answer("No registered users.")
        else:
            lines = ["👥 RECENT USERS"]
            for row in rows:
                lines.append(
                    f"{row['user_id']} | "
                    f"{row['first_name']} | "
                    f"@{row['username'] or 'none'}"
                )
            await callback.message.answer("\n".join(lines))

    elif action == "admin_files":
        rows = db.execute(
            "SELECT code, file_name, downloads "
            "FROM files ORDER BY created_at DESC LIMIT 20"
        ).fetchall()

        if not rows:
            await callback.message.answer("No stored files.")
        else:
            lines = ["📁 RECENT FILES"]
            for row in rows:
                lines.append(
                    f"{row['code']} | {row['file_name']} | "
                    f"Downloads: {row['downloads']}"
                )
            await callback.message.answer("\n".join(lines))

    elif action == "admin_broadcast":
        workflows[uid] = "broadcast"
        await callback.message.answer(
            "📢 Broadcast text bhejo.\n/cancel to cancel."
        )

    elif action == "admin_welcome":
        workflows[uid] = "welcome"
        await callback.message.answer(
            "✏️ New welcome message bhejo.\n"
            "Use {name} to insert the user's first name.\n"
            "/cancel to cancel."
        )

    await callback.answer()


@dp.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery):
    await callback.message.answer(
        "🏠 Main menu",
        reply_markup=main_keyboard(callback.from_user.id)
    )
    await callback.answer()


# =====================================================
# BROADCAST + WORKFLOWS
# =====================================================

@dp.message(Command("broadcast"))
async def broadcast_command(message: Message):
    if not is_admin(message.from_user.id):
        return

    workflows[message.from_user.id] = "broadcast"

    await message.answer(
        "📢 Broadcast message bhejo.\n/cancel se cancel kar sakte ho."
    )


@dp.message(Command("cancel"))
async def cancel_command(message: Message):
    workflows.pop(message.from_user.id, None)
    await message.answer("❎ Workflow cancelled.")


@dp.message(F.text)
async def workflow_and_menu_handler(message: Message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    # Handle pending admin/user workflows first.
    workflow = workflows.get(uid)

    if workflow == "redeem":
        workflows.pop(uid, None)

        code = text.strip()

        row = db.execute(
            "SELECT * FROM redeem_codes WHERE code=?",
            (code,)
        ).fetchone()

        if not row or row["uses_left"] <= 0:
            await message.answer("❌ Invalid or expired redeem code.")
            return

        used = db.execute(
            "SELECT 1 FROM redemptions WHERE user_id=? AND code=?",
            (uid, code)
        ).fetchone()

        if used:
            await message.answer("⚠️ Aap ye code pehle use kar chuke ho.")
            return

        grant_premium(uid, row["days"])

        db.execute(
            "INSERT INTO redemptions(user_id, code) VALUES (?, ?)",
            (uid, code)
        )

        db.execute(
            "UPDATE redeem_codes SET uses_left=uses_left-1 WHERE code=?",
            (code,)
        )
        db.commit()

        await message.answer(
            f"🎉 Premium activated for {row['days']} days!"
        )
        return

    if workflow == "welcome":
        if not is_admin(uid):
            workflows.pop(uid, None)
            return

        set_setting("welcome", text)
        workflows.pop(uid, None)

        await message.answer("✅ Welcome message updated.")
        return

    if workflow == "broadcast":
        if not is_admin(uid):
            workflows.pop(uid, None)
            return

        workflows.pop(uid, None)

        users = db.execute(
            "SELECT user_id FROM users WHERE banned=0"
        ).fetchall()

        sent = 0
        failed = 0

        status = await message.answer(
            f"📢 Broadcast started for {len(users)} users..."
        )

        for row in users:
            try:
                await bot.send_message(
                    row["user_id"],
                    text
                )
                sent += 1
            except TelegramAPIError:
                failed += 1

            await asyncio.sleep(0.04)

        await status.edit_text(
            "✅ Broadcast finished.\n\n"
            f"📨 Sent: {sent}\n"
            f"⚠️ Failed: {failed}"
        )
        return

    # Regular menu buttons.
    if text == "📁 File Sharing":
        await message.answer(
            "📁 FILE SHARING\n\n"
            "Admin uploaded files automatically get share links.\n"
            "If you have a file link, open it to receive the file.\n"
            "To upload files, contact the bot admin."
        )

    elif text == "📢 Updates":
        await message.answer(
            "📢 Updates\n\nFor updates, contact the owner.",
            reply_markup=contact_keyboard()
        )

    elif text == "👤 My Account":
        # Normally handled by account_handler.
        pass

    elif text == "⭐ Premium":
        pass

    elif text == "📊 Statistics":
        pass

    elif text == "🎟️ Redeem Code":
        pass

    elif text == "🎁 Refer & Earn":
        pass

    elif text == "🧰 Extra Tools":
        pass

    elif text == "👑 MUZA4R":
        pass

    elif text == "ℹ️ Help":
        pass

    elif text == "👑 Admin Panel":
        pass


# =====================================================
# STARTUP
# =====================================================

async def main():
    print("MUZA4R ULTIMATE BOT starting...")
    print(f"Owner ID: {OWNER_ID}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
