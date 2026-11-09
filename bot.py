import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "5839586443"))
def is_owner(user_id):
    return int(user_id) == OWNER_ID
OWNER_USERNAME = "@zindadil07"
SUPPORT_USERNAME = "@HeavenCoreX"
DB_PATH = os.getenv("DATABASE_PATH", "bot_data/bot.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("muzaffar-task-reward")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
user_states = {}  # in-memory conversation steps; core records are stored in SQLite


# ============================================================
# DATABASE
# ============================================================
def db():
    con = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            first_name TEXT DEFAULT '',
            last_name TEXT DEFAULT '',
            username TEXT DEFAULT '',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
            is_banned INTEGER DEFAULT 0,
            dm_started INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS groups(
            chat_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '',
            username TEXT DEFAULT '',
            added_by INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            bot_admin INTEGER DEFAULT 0,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tasks(
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            instructions TEXT DEFAULT '',
            reward_type TEXT DEFAULT 'text',
            reward_text TEXT DEFAULT '',
            reward_file_id TEXT DEFAULT '',
            reward_file_kind TEXT DEFAULT '',
            max_users INTEGER DEFAULT 0,
            claimed_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS submissions(
            submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            proof TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT DEFAULT '',
            reviewed_by INTEGER DEFAULT 0,
            UNIQUE(task_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS deliveries(
            delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reward_type TEXT DEFAULT '',
            delivered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(task_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS activity(
            activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            chat_id INTEGER DEFAULT 0,
            action TEXT NOT NULL,
            details TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS broadcast_log(
            broadcast_id INTEGER PRIMARY KEY AUTOINCREMENT,
            audience TEXT,
            sent INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

        WELCOME_TEXT = (
            "<b>Welcome to MUZAFFAR TASK REWARD BOT!</b>\n\n"
            "🎯 Complete tasks and earn rewards.\n"
            "🎁 Receive redeem codes, files and exclusive gifts.\n\n"
            "👥 You can add this bot to your own Telegram group. "
            "Please make the bot an administrator for group features.\n\n"
            "🤖 Want a similar bot? DM @zindadil07\n"
            "🛠 Support: @HeavenCoreX\n\n"
            "❤️ Powered by MUZAFFAR"
        )

        con.execute(
            "INSERT OR IGNORE INTO settings(key,value) VALUES('welcome_text', ?)",
            (WELCOME_TEXT,)
        )
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('welcome_media_id','')")
        con.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('welcome_media_kind','')")


def setting(key):
    with db() as con:
        row = con.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else ""


def set_setting(key, value):
    with db() as con:
        con.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


def log_activity(user_id=0, chat_id=0, action="", details=""):
    with db() as con:
        con.execute(
            "INSERT INTO activity(user_id,chat_id,action,details) VALUES(?,?,?,?)",
            (user_id, chat_id, action, details[:1000])
        )


def upsert_user(user):
    if not user or user.is_bot:
        return
    with db() as con:
        dm_flag = 1 if getattr(user, "_dm_started", False) else 0
        con.execute("""
        INSERT INTO users(user_id,first_name,last_name,username,last_seen,dm_started)
        VALUES(?,?,?,?,CURRENT_TIMESTAMP,?)
        ON CONFLICT(user_id) DO UPDATE SET
          first_name=excluded.first_name,
          last_name=excluded.last_name,
          username=excluded.username,
          last_seen=CURRENT_TIMESTAMP
        """, (user.id, user.first_name or "", user.last_name or "", user.username or "", dm_flag))


def is_banned(user_id):
    with db() as con:
        row = con.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
def main_menu(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=2)

    kb.add(
        types.InlineKeyboardButton("🎯 Available Tasks", callback_data="tasks"),
        types.InlineKeyboardButton("👤 My Profile", callback_data="profile")
    )

    kb.add(
        types.InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
        types.InlineKeyboardButton("📖 Help", callback_data="help")
    )

    kb.add(
        types.InlineKeyboardButton(
            "➕ Add to Your Group",
            url=f"https://t.me/{bot.get_me().username}?startgroup=true"
        )
    )

    return kb


def owner_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton("➕ Create Task", callback_data="own:create"),
        types.InlineKeyboardButton("📋 Manage Tasks", callback_data="own:tasks"),
        types.InlineKeyboardButton("👥 Users", callback_data="own:users"),
        types.InlineKeyboardButton("🏘 Groups", callback_data="own:groups"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="own:broadcast"),
        types.InlineKeyboardButton("🎬 Welcome Settings", callback_data="own:welcome"),
        types.InlineKeyboardButton("🧾 Pending Proofs", callback_data="own:pending"),
        types.InlineKeyboardButton("📊 Statistics", callback_data="own:stats"),
    ]

    for b in buttons:
        kb.add(b)

    return kb


def safe_send_message(chat_id, text, **kwargs):
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        log.warning("send_message failed to %s: %s", chat_id, e)
        return None
        log.warning("send_message failed to %s: %s", chat_id, e)
        return None


# USER COMMANDS / GROUP TRACKING
@bot.message_handler(commands=["start"])
def start(message):
    user = message.from_user
    user._dm_started = (message.chat.type == "private")
    upsert_user(user)

    if is_banned(user.id):
        return

    if message.chat.type in ("group", "supergroup"):
        register_group(message.chat, user.id)
        with db() as con:
            con.execute(
                "UPDATE users SET dm_started=0 WHERE user_id=?",
                (user.id,)
            )

        safe_send_message(
            message.chat.id,
            "👋 <b>MUZAFFAR TASK REWARD BOT is active!</b>\n"
            "Use /tasks to view available tasks. For private rewards, each member must start me in DM."
        )
        log_activity(
            user.id, message.chat.id, "group_start",
            message.chat.title or ""
        )
        return

    log_activity(user.id, message.chat.id, "start", "Private /start")
    text = setting("welcome_text")
    media_id = setting("welcome_media_id")
    media_kind = setting("welcome_media_kind")
    markup = main_menu(message.chat.id)

    try:
        if media_id and media_kind == "video":
            bot.send_video(
                message.chat.id, media_id,
                caption=text, reply_markup=markup
            )
        elif media_id and media_kind == "photo":
            bot.send_photo(
                message.chat.id, media_id,
                caption=text, reply_markup=markup
            )
        else:
            bot.send_message(
                message.chat.id, text, reply_markup=markup
            )
    except Exception:
        safe_send_message(
            message.chat.id, text, reply_markup=markup
        )


def register_group(chat, added_by=0):
    if chat.type not in ("group", "supergroup"):
        return

    try:
        member = bot.get_chat_member(chat.id, bot.get_me().id)
        admin = member.status in ("administrator", "creator")
    except Exception:
        admin = False
    with db() as con:
        con.execute("""
        INSERT INTO groups(chat_id,title,username,added_by,status,bot_admin,last_seen)
        VALUES(?,?,?,?, 'active', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chat_id) DO UPDATE SET
          title=excluded.title, username=excluded.username,
          status='active', bot_admin=excluded.bot_admin,
          last_seen=CURRENT_TIMESTAMP
        """, (chat.id, chat.title or "", chat.username or "", added_by, int(admin)))
    log_activity(added_by, chat.id, "group_added_or_seen", chat.title or "")


@bot.message_handler(commands=["tasks"])
def tasks_cmd(message):
    if message.from_user:
        upsert_user(message.from_user)
    if message.chat.type in ("group", "supergroup"):
        register_group(message.chat, message.from_user.id if message.from_user else 0)
    show_tasks(message.chat.id)


def show_tasks(chat_id):
    with db() as con:
        rows = con.execute("""
        SELECT * FROM tasks WHERE status='active'
        AND (max_users=0 OR claimed_count < max_users)
        ORDER BY task_id DESC LIMIT 20
        """).fetchall()
    if not rows:
        safe_send_message(chat_id, "🎯 Abhi koi active task available nahi hai.")
        return
    for row in rows:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(
            "✅ Participate / Submit Proof",
            callback_data=f"task:{row['task_id']}"
        ))
        msg = (
            f"🎯 <b>Task #{row['task_id']}: {esc(row['title'])}</b>\n\n"
            f"{esc(row['description'])}\n\n"
            f"📝 <b>Instructions:</b> {esc(row['instructions'])}\n"
            f"🎁 <b>Reward:</b> {esc(row['reward_type']).title()}\n"
            f"👥 <b>Slots:</b> {row['claimed_count']}/{row['max_users'] if row['max_users'] else 'Unlimited'}"
        )
        safe_send_message(chat_id, msg, reply_markup=kb)


@bot.message_handler(commands=["profile"])
def show_profile(chat_id, user_id):
    with db() as con:
        u = con.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        done = con.execute(
            "SELECT COUNT(*) n FROM deliveries WHERE user_id=?",
            (user_id,)
        ).fetchone()["n"]

        pending = con.execute(
            "SELECT COUNT(*) n FROM submissions WHERE user_id=? AND status='pending'",
            (user_id,)
        ).fetchone()["n"]

    if not u:
        safe_send_message(chat_id, "Please /start me in private first.")
        return

    name = (u["first_name"] + " " + u["last_name"]).strip()

    safe_send_message(
        chat_id,
        f"👤 <b>Your Profile</b>\n"
        f"Name: {esc(name)}\n"
        f"Username: @{esc(u['username']) if u['username'] else 'Not set'}\n"
        f"ID: <code>{user_id}</code>\n"
        f"🎁 Rewards received: {done}\n"
        f"⏳ Pending submissions: {pending}"
    )
    lines = ["🏆 <b>TOP REWARD EARNERS</b>"]
    for i, r in enumerate(rows, 1):
        label = r["first_name"] or r["username"] or str(r["user_id"])
        lines.append(f"{i}. {esc(label)} — {r['total']} rewards")
    safe_send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["help"])
def help_cmd(message):
    safe_send_message(
        message.chat.id,
        "📖 <b>Commands</b>\n/start - Start bot\n/tasks - View tasks\n/profile - My profile\n"
        "/leaderboard - Top users\n/help - Help\n\n"
        "Group admins can use /tasks after adding the bot. For private rewards, users must start the bot in DM."
    )


# Track group messages, new members, and bot removal
@bot.message_handler(content_types=["new_chat_members"])
def new_members(message):
    register_group(message.chat, message.from_user.id if message.from_user else 0)
    me = bot.get_me().id
    for member in message.new_chat_members:
        if member.id == me:
            owner_notice = (
                f"➕ <b>Bot added to a group</b>\n"
                f"Group: {esc(message.chat.title)}\n"
                f"ID: <code>{message.chat.id}</code>\n"
                f"Added by: <code>{message.from_user.id if message.from_user else 0}</code>"
            )
            safe_send_message(OWNER_ID, owner_notice)


@bot.my_chat_member_handler()
def bot_chat_member_update(update):
    chat = update.chat
    if chat.type not in ("group", "supergroup"):
        return
    new_status = update.new_chat_member.status
    if new_status in ("left", "kicked"):
        with db() as con:
            con.execute("UPDATE groups SET status='removed',bot_admin=0 WHERE chat_id=?", (chat.id,))
        safe_send_message(OWNER_ID, f"➖ Bot removed from <b>{esc(chat.title)}</b>\nID: <code>{chat.id}</code>")
    else:
        register_group(chat, update.from_user.id if update.from_user else 0)
        safe_send_message(OWNER_ID, f"🏘 Group updated: <b>{esc(chat.title)}</b>\nID: <code>{chat.id}</code>\nStatus: {esc(new_status)}")


# ============================================================
# CALLBACKS
# ============================================================
@bot.callback_query_handler(func=lambda c: not (c.data or "").startswith("wizard:"))
def callbacks(call):
    uid = call.from_user.id
    upsert_user(call.from_user)
    data = call.data or ""
    try:
        if data == "tasks":
            bot.answer_callback_query(call.id)
            show_tasks(call.message.chat.id)
        elif data == "profile":
            bot.answer_callback_query(call.id)
            show_profile(call.message.chat.id, uid)
        elif data == "leaderboard":
            bot.answer_callback_query(call.id)
            leaderboard_cmd(call.message)
        elif data == "help":
            bot.answer_callback_query(call.id)
            help_cmd(call.message)
        elif data.startswith("task:"):
            bot.answer_callback_query(call.id)
            task_id = int(data.split(":")[1])
            begin_submission(call.message.chat.id, uid, task_id)
        elif data.startswith("own:"):
            if not is_owner(uid):
                bot.answer_callback_query(call.id, "Not authorized.", show_alert=True)
                return
            bot.answer_callback_query(call.id)
            owner_action(call.message.chat.id, data.split(":", 1)[1])
        elif data.startswith("review:"):
            if not is_owner(uid):
                bot.answer_callback_query(call.id, "Not authorized.", show_alert=True)
                return
            _, decision, sub_id = data.split(":")
            review_submission(call.message.chat.id, int(sub_id), decision)
            bot.answer_callback_query(call.id, "Updated.")
        elif data.startswith("taskstatus:"):
            if not is_owner(uid):
                bot.answer_callback_query(call.id, "Not authorized.", show_alert=True)
                return
            _, task_id, status = data.split(":")
            with db() as con:
                con.execute("UPDATE tasks SET status=? WHERE task_id=?", (status, int(task_id)))
            bot.answer_callback_query(call.id, "Task updated.")
            owner_action(call.message.chat.id, "tasks")
        else:
            bot.answer_callback_query(call.id)
    except Exception as e:
        log.exception("callback error: %s", e)
        try:
            bot.answer_callback_query(call.id, "Something went wrong.", show_alert=True)
        except Exception:
            pass


def begin_submission(chat_id, user_id, task_id):
    with db() as con:
        task = con.execute("SELECT * FROM tasks WHERE task_id=? AND status='active'", (task_id,)).fetchone()
        old = con.execute("SELECT * FROM submissions WHERE task_id=? AND user_id=?", (task_id, user_id)).fetchone()
    if not task:
        safe_send_message(chat_id, "❌ Task is unavailable.")
        return
    if old and old["status"] in ("pending", "approved"):
        safe_send_message(chat_id, f"Your submission status: <b>{esc(old['status'])}</b>.")
        return
    user_states[user_id] = {"mode": "submit_proof", "task_id": task_id, "return_chat": chat_id}
    try:
        target_chat = bot.get_chat(chat_id)
        is_group = target_chat.type in ("group", "supergroup")
    except Exception:
        is_group = chat_id < 0
    prompt = (
        f"📝 <b>Submit proof for Task #{task_id}</b>\n"
        "Ab task ka proof text, screenshot, photo ya document bhejo. Cancel ke liye /cancel bhejo."
)
    if is_group:
        sent = safe_send_message(user_id, prompt)
        if not sent:
            safe_send_message(chat_id, "📩 Proof submit karne ke liye pehle bot ko private chat mein /start karein, phir task button dobara press karein.")
            user_states.pop(user_id, None)
    else:
        safe_send_message(chat_id, prompt)


def owner_action(chat_id, action):
    if action == "create":
        user_states[OWNER_ID] = {"mode": "create_title"}
        safe_send_message(chat_id, "➕ <b>Create Task</b>\nTask ka title bhejo:")
    elif action == "tasks":
        with db() as con:
            rows = con.execute("SELECT * FROM tasks ORDER BY task_id DESC LIMIT 20").fetchall()
        if not rows:
            safe_send_message(chat_id, "No tasks created yet.")
            return
        for r in rows:
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("🟢 Activate", callback_data=f"taskstatus:{r['task_id']}:active"),
                types.InlineKeyboardButton("⏸ Pause", callback_data=f"taskstatus:{r['task_id']}:paused")
            )
            safe_send_message(chat_id, f"#{r['task_id']} <b>{esc(r['title'])}</b> — {esc(r['status'])}\nClaims: {r['claimed_count']}", reply_markup=kb)
    elif action == "users":
        with db() as con:
            rows = con.execute("SELECT * FROM users ORDER BY joined_at DESC LIMIT 30").fetchall()
            total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        safe_send_message(chat_id, f"👥 <b>Users: {total}</b>")
        for u in rows:
            name = (u["first_name"] + " " + u["last_name"]).strip()
            safe_send_message(chat_id, f"👤 {esc(name or 'Unknown')} (@{esc(u['username']) if u['username'] else 'no username'})\nID: <code>{u['user_id']}</code>\nLast seen: {esc(u['last_seen'])}")
    elif action == "groups":
        with db() as con:
            rows = con.execute("SELECT * FROM groups ORDER BY added_at DESC").fetchall()
        safe_send_message(chat_id, f"🏘 <b>Connected groups: {len(rows)}</b>")
        for g in rows:
            status = "🟢 Active" if g["status"] == "active" else "🔴 Removed"
            admin = "Admin" if g["bot_admin"] else "Admin required"
            safe_send_message(chat_id, f"<b>{esc(g['title'])}</b>\nID: <code>{g['chat_id']}</code>\n{status} | {admin}")
    elif action == "pending":
        with db() as con:
            rows = con.execute("""
            SELECT s.*,t.title,u.first_name,u.username
            FROM submissions s JOIN tasks t ON t.task_id=s.task_id
            LEFT JOIN users u ON u.user_id=s.user_id
            WHERE s.status='pending' ORDER BY s.submitted_at LIMIT 20
            """).fetchall()
        if not rows:
            safe_send_message(chat_id, "✅ No pending submissions.")
            return
        for s in rows:
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("✅ Approve + Send Reward", callback_data=f"review:approve:{s['submission_id']}"),
                types.InlineKeyboardButton("❌ Reject", callback_data=f"review:reject:{s['submission_id']}")
            )
            safe_send_message(chat_id, f"🧾 <b>Submission #{s['submission_id']}</b>\nTask: #{s['task_id']} {esc(s['title'])}\nUser: {esc(s['first_name'])} (@{esc(s['username'])})\nID: <code>{s['user_id']}</code>\nProof: {esc(s['proof'])}", reply_markup=kb)
    elif action == "stats":
        with db() as con:
            users = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            groups = con.execute("SELECT COUNT(*) n FROM groups WHERE status='active'").fetchone()["n"]
            tasks = con.execute("SELECT COUNT(*) n FROM tasks").fetchone()["n"]
            rewards = con.execute("SELECT COUNT(*) n FROM deliveries").fetchone()["n"]
            pending = con.execute("SELECT COUNT(*) n FROM submissions WHERE status='pending'").fetchone()["n"]
        safe_send_message(chat_id, f"📊 <b>Bot Statistics</b>\nUsers: {users}\nActive groups: {groups}\nTasks: {tasks}\nRewards sent: {rewards}\nPending proofs: {pending}")
    elif action == "broadcast":
        user_states[OWNER_ID] = {"mode": "broadcast"}
        safe_send_message(chat_id, "📢 Broadcast ke liye message bhejo. Text, photo, video, audio ya document supported.\nCancel: /cancel")
    elif action == "welcome":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✏️ Edit Welcome Text", callback_data="own:welcome_text"),
            types.InlineKeyboardButton("🎬 Set Welcome Video", callback_data="own:welcome_video"),
            types.InlineKeyboardButton("🖼 Set Welcome Photo", callback_data="own:welcome_photo"),
            types.InlineKeyboardButton("🗑 Remove Welcome Media", callback_data="own:welcome_remove"),
            types.InlineKeyboardButton("👁 Preview", callback_data="own:welcome_preview")
        )
        safe_send_message(chat_id, "🎬 <b>Welcome Settings</b>", reply_markup=kb)
    elif action == "welcome_text":
        user_states[OWNER_ID] = {"mode": "welcome_text"}
        safe_send_message(chat_id, "Naya welcome text bhejo. HTML formatting supported. Cancel: /cancel")
    elif action == "welcome_video":
        user_states[OWNER_ID] = {"mode": "welcome_video"}
        safe_send_message(chat_id, "Welcome video as a Telegram video bhejo. Cancel: /cancel")
    elif action == "welcome_photo":
        user_states[OWNER_ID] = {"mode": "welcome_photo"}
        safe_send_message(chat_id, "Welcome photo bhejo. Cancel: /cancel")
    elif action == "welcome_remove":
        set_setting("welcome_media_id", "")
        set_setting("welcome_media_kind", "")
        safe_send_message(chat_id, "✅ Welcome media removed.")
    elif action == "welcome_preview":
        media_id = setting("welcome_media_id")
        kind = setting("welcome_media_kind")
        text = setting("welcome_text")
        try:
            if media_id and kind == "video":
                bot.send_video(chat_id, media_id, caption=text)
            elif media_id and kind == "photo":
                bot.send_photo(chat_id, media_id, caption=text)
            else:
                safe_send_message(chat_id, text)
        except Exception:
            safe_send_message(chat_id, text)


def review_submission(chat_id, submission_id, decision):
    with db() as con:
        sub = con.execute("SELECT * FROM submissions WHERE submission_id=?", (submission_id,)).fetchone()
        if not sub or sub["status"] != "pending":
            safe_send_message(chat_id, "Submission already reviewed or not found.")
            return
        if decision == "reject":
            con.execute(
                "UPDATE submissions SET status='rejected',reviewed_at=CURRENT_TIMESTAMP,reviewed_by=? WHERE submission_id=?",
                (OWNER_ID, submission_id)
            )
            user_id = sub["user_id"]
            task_id = sub["task_id"]
        else:
            task = con.execute("SELECT * FROM tasks WHERE task_id=?", (sub["task_id"],)).fetchone()
            if not task:
                safe_send_message(chat_id, "Task not found.")
                return
            con.execute(
                "UPDATE submissions SET status='approved',reviewed_at=CURRENT_TIMESTAMP,reviewed_by=? WHERE submission_id=?",
                (OWNER_ID, submission_id)
            )
            con.execute(
                "INSERT OR IGNORE INTO deliveries(task_id,user_id,reward_type) VALUES(?,?,?)",
                        (task["task_id"], sub["user_id"], task["reward_type"])
            )
            con.execute("UPDATE tasks SET claimed_count=claimed_count+1 WHERE task_id=?", (task["task_id"],))
            user_id = sub["user_id"]
            task_id = sub["task_id"]
            reward_type = task["reward_type"]
            reward_text = task["reward_text"]
            reward_file_id = task["reward_file_id"]
            reward_file_kind = task["reward_file_kind"]

    if decision == "reject":
        safe_send_message(user_id, f"❌ Your proof for Task #{task_id} was rejected. Please contact support or try again if the task allows resubmission.")
        log_activity(user_id, 0, "submission_rejected", str(submission_id))
        safe_send_message(chat_id, f"❌ Submission #{submission_id} rejected.")
        return

    # Send reward privately; users must have started the bot in DM.
    try:
        if reward_file_id and reward_file_kind:
            if reward_file_kind == "document":
                bot.send_document(user_id, reward_file_id, caption=f"🎁 Reward for Task #{task_id}")
            elif reward_file_kind == "photo":
                bot.send_photo(user_id, reward_file_id, caption=f"🎁 Reward for Task #{task_id}")
            elif reward_file_kind == "video":
                bot.send_video(user_id, reward_file_id, caption=f"🎁 Reward for Task #{task_id}")
            elif reward_file_kind == "audio":
                bot.send_audio(user_id, reward_file_id, caption=f"🎁 Reward for Task #{task_id}")
            elif reward_file_kind == "voice":
                bot.send_voice(user_id, reward_file_id, caption=f"🎁 Reward for Task #{task_id}")
            else:
                bot.send_message(user_id, f"🎁 <b>Your reward for Task #{task_id}</b>\n{esc(reward_text)}")
        else:
            bot.send_message(user_id, f"🎉 <b>Congratulations! Task #{task_id} approved.</b>\n\n🎁 <b>Your Reward:</b>\n{esc(reward_text)}")
        safe_send_message(chat_id, f"✅ Submission #{submission_id} approved and reward sent to <code>{user_id}</code>.")
        log_activity(user_id, 0, "reward_delivered", f"Task #{task_id}")
    except Exception as e:
        safe_send_message(chat_id, f"⚠️ Approved, but reward delivery failed for <code>{user_id}</code>: {esc(e)}. Ask user to /start the bot.")
        log.exception("reward delivery failed: %s", e)


# ============================================================
# OWNER CONVERSATION WIZARDS
# ============================================================
@bot.message_handler(commands=["admin"])
def admin_cmd(message):
    if not is_owner(message.from_user.id):
        return
    bot.send_message(message.chat.id, "👑 <b>OWNER CONTROL PANEL</b>", reply_markup=owner_menu())


@bot.message_handler(commands=["cancel"])
def cancel_cmd(message):
    user_states.pop(message.from_user.id, None)
    safe_send_message(message.chat.id, "❎ Current action cancelled.")


def finish_task_creation(chat_id, state):
    with db() as con:
        cur = con.execute("""
        INSERT INTO tasks(title,description,instructions,reward_type,reward_text,reward_file_id,
                          reward_file_kind,max_users,created_by)
        VALUES(?,?,?,?,?,?,?,?,?)
        """, (
            state["title"], state["description"], state["instructions"],
            state["reward_type"], state.get("reward_text", ""),
            state.get("reward_file_id", ""), state.get("reward_file_kind", ""),
            state["max_users"], OWNER_ID
        ))
        task_id = cur.lastrowid
    user_states.pop(OWNER_ID, None)
    safe_send_message(chat_id, f"✅ Task #{task_id} created successfully.")
    log_activity(OWNER_ID, chat_id, "task_created", f"Task #{task_id}: {state['title']}")


def copy_message_to_target(message, target_id):
    try:
        bot.copy_message(target_id, message.chat.id, message.message_id)
        return True
    except Exception as e:
        log.info("broadcast copy failed %s: %s", target_id, e)
        return False


def perform_broadcast(message):
    # Broadcast only to users who started the bot and groups where bot is still recorded active.
    with db() as con:
        users = [r["user_id"] for r in con.execute("SELECT user_id FROM users WHERE is_banned=0 AND dm_started=1")]
        groups = [r["chat_id"] for r in con.execute("SELECT chat_id FROM groups WHERE status='active'")]
    sent = failed = 0
    for target in users + groups:
        if copy_message_to_target(message, target):
            sent += 1
        else:
            failed += 1
        time.sleep(0.05)  # modest pacing; Telegram limits still apply
    with db() as con:
        con.execute("INSERT INTO broadcast_log(audience,sent,failed) VALUES(?,?,?)",
                    ("users+groups", sent, failed))
    safe_send_message(OWNER_ID, f"📢 <b>Broadcast finished</b>\n✅ Sent: {sent}\n❌ Failed: {failed}")
    log_activity(OWNER_ID, 0, "broadcast", f"sent={sent}, failed={failed}")


@bot.message_handler(content_types=["text", "photo", "video", "audio", "document", "voice"])
def all_messages(message):
    if not message.from_user:
        return

    if message.chat.type == "private":
        message.from_user._dm_started = True

    upsert_user(message.from_user)
    uid = message.from_user.id

    state = user_states.get(uid, {})
    mode = state.get("mode")
    content_type = message.content_type

    if mode == "submit_proof":
        task_id = state["task_id"]
        proof = message.text or f"[{content_type} message_id={message.message_id}]"

        with db() as con:
            try:
                con.execute(
                    "INSERT INTO submissions(task_id,user_id,proof,status) VALUES(?,?,?,'pending')",
                    (task_id, uid, proof[:3000])
                )
            except sqlite3.IntegrityError:
                con.execute(
                    "UPDATE submissions SET proof=?,status='pending',submitted_at=CURRENT_TIMESTAMP WHERE task_id=? AND user_id=?",
                    (proof[:3000], task_id, uid)
                )

        user_states.pop(uid, None)

        safe_send_message(
            message.chat.id,
            "✅ Proof submitted! Owner review ke baad reward DM mein milega."
        )

        safe_send_message(
            OWNER_ID,
            f"🧾 <b>New proof submitted</b>\n"
            f"Task: #{task_id}\n"
            f"User: {esc(message.from_user.first_name)} "
            f"(@{esc(message.from_user.username or 'no username')})\n"
            f"ID: <code>{uid}</code>\n"
            f"Proof: {esc(proof[:1500])}\n"
            f"Use /admin → Pending Proofs to review."
        )

        log_activity(uid, message.chat.id, "proof_submitted", f"Task #{task_id}")
        return

    if message.text:
        command = message.text.strip().lower()

        if command == "/profile" or command.startswith("/profile@") or command == "👤 my profile":
            show_profile(message.chat.id, uid)
            return

    if uid != OWNER_ID:
        return
    if mode == "create_title":
        if content_type != "text":
            safe_send_message(message.chat.id, "Please send task title as text.")
            return

        state["title"] = message.text[:200]
        state["mode"] = "create_description"
        safe_send_message(message.chat.id, "Task description bhejo:")

    elif mode == "create_description":
        state["description"] = (message.text or "")[:1500]
        state["mode"] = "create_instructions"
        safe_send_message(message.chat.id, "Task instructions bhejo:")

    elif mode == "create_instructions":
        state["instructions"] = (message.text or "")[:1500]
        state["mode"] = "create_reward_type"

        kb = types.InlineKeyboardMarkup(row_width=2)

        for label, value in [
            ("🎟 Redeem Code / Text", "text"),
            ("📁 File", "file")
        ]:
            kb.add(types.InlineKeyboardButton(
                label,
                callback_data=f"wizard:reward:{value}"
            ))

        safe_send_message(
            message.chat.id,
            "Reward type select karo:",
            reply_markup=kb
        )

    elif mode == "create_reward":
        if state["reward_type"] == "file":
            file_id = None
            kind = None

            if content_type == "document":
                file_id, kind = message.document.file_id, "document"
            elif content_type == "photo":
                file_id, kind = message.photo[-1].file_id, "photo"
            elif content_type == "video":
                file_id, kind = message.video.file_id, "video"
            elif content_type == "audio":
                file_id, kind = message.audio.file_id, "audio"
            elif content_type == "voice":
                file_id, kind = message.voice.file_id, "voice"

            if not file_id:
                safe_send_message(
                    message.chat.id,
                    "Reward file/photo/video/audio bhejo."
                )
                return

            state["reward_file_id"] = file_id
            state["reward_file_kind"] = kind
            state["reward_text"] = ""

        else:
            if content_type != "text":
                safe_send_message(
                    message.chat.id,
                    "Redeem code ya reward text bhejo."
                )
                return

            state["reward_text"] = message.text[:3000]

        state["mode"] = "create_slots"
        safe_send_message(
            message.chat.id,
            "Maximum users/slots number bhejo. Unlimited ke liye 0 bhejo:"
        )

    elif mode == "create_slots":
        try:
            slots = int(message.text.strip())
            if slots < 0:
                raise ValueError
        except Exception:
            safe_send_message(
                message.chat.id,
                "Valid number bhejo, e.g. 100 ya 0."
            )
            return

        state["max_users"] = slots
        finish_task_creation(message.chat.id, state)

    elif mode == "broadcast":
        user_states.pop(uid, None)
        safe_send_message(
            message.chat.id,
            "⏳ Broadcast started. Result aapko yahin milega."
        )
        perform_broadcast(message)

    elif mode == "welcome_text":
        set_setting("welcome_text", message.text or "")
        user_states.pop(uid, None)
        safe_send_message(
            message.chat.id,
            "✅ Welcome text updated."
        )

    elif mode in ("welcome_video", "welcome_photo"):
        if mode == "welcome_video" and content_type == "video":
            set_setting("welcome_media_id", message.video.file_id)
            set_setting("welcome_media_kind", "video")

        elif mode == "welcome_photo" and content_type == "photo":
            set_setting("welcome_media_id", message.photo[-1].file_id)
            set_setting("welcome_media_kind", "photo")

        else:
            safe_send_message(
                message.chat.id,
                "Selected media type bhejo."
            )
            return

        user_states.pop(uid, None)
        safe_send_message(
            message.chat.id,
            "✅ Welcome media saved."
        )


# Handle reward type selection wizard
@bot.callback_query_handler(func=lambda c: c.data.startswith("wizard:"))
def wizard_callbacks(call):
    if not is_owner(call.from_user.id):
        bot.answer_callback_query(
            call.id,
            "Not authorized.",
            show_alert=True
        )
        return

    parts = call.data.split(":")

    if parts[1] == "reward":
        state = user_states.get(OWNER_ID)

        if not state:
            bot.answer_callback_query(
                call.id,
                "Wizard expired.",
                show_alert=True
            )
            return

        state["reward_type"] = parts[2]
        state["mode"] = "create_reward"

        bot.answer_callback_query(call.id)
        bot.send_message(
            call.message.chat.id,
            "Ab reward code/text ya reward file bhejo:"
        )


# ============================================================
# OWNER GROUP / BROADCAST / USER COMMANDS
# ============================================================

@bot.message_handler(commands=["groups"])
def groups_cmd(message):
    if not is_owner(message.from_user.id):
        return
    owner_action(message.chat.id, "groups")


@bot.message_handler(commands=["users"])
def users_cmd(message):
    if not is_owner(message.from_user.id):
        return
    owner_action(message.chat.id, "users")


@bot.message_handler(commands=["broadcast"])
def broadcast_cmd(message):
    if not is_owner(message.from_user.id):
        return

    user_states[OWNER_ID] = {"mode": "broadcast"}
    safe_send_message(
        message.chat.id,
        "Broadcast message/media bhejo. Cancel: /cancel"
    )


# ============================================================
# STARTUP
# ============================================================

def main():
    init_db()
    me = bot.get_me()

    log.info(
        "Bot started: @%s | owner=%s | db=%s",
        me.username,
        OWNER_ID,
        DB_PATH
    )

    try:
        bot.send_message(
            OWNER_ID,
            f"✅ <b>MUZAFFAR TASK REWARD BOT is online</b>\nBot: @{me.username}"
        )
    except Exception:
        pass

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )


if __name__ == "__main__":
    main()
