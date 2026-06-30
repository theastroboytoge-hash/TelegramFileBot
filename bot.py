import logging
import json
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters
import uvicorn
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict
import pytz
import random
import atexit
import signal

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-app.onrender.com')}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")
TIMEZONE = pytz.timezone("Asia/Tehran")  # منطقه زمانی

app = FastAPI()
ptb_app = None
db_pool = None
shutdown_event = asyncio.Event()

# ====================== KEYBOARDS ======================
MAIN_KEYBOARD = ReplyKeyboardMarkup([["➕ New File", "📂 My Files"], ["📊 Memory", "⚙️ Settings"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["🔙 Back"]], resize_keyboard=True)
BATCH_KEYBOARD = ReplyKeyboardMarkup([["✅ Done"], ["🔙 Back"]], resize_keyboard=True)

FILE_TYPE_EMOJI = {
    "photo": "🖼️", "video": "📽️", "audio": "🎵", "voice": "🎙️", "document": "📄"
}

PAGE_SIZE = 5
LANGUAGES = {"en": "English", "fa": "Persian"}

# ====================== HELPERS ======================
def get_msg(update: Update):
    if update.message:
        return update.message
    elif update.callback_query:
        return update.callback_query.message
    return None

def get_user_lang(context):
    return context.user_data.get('lang', 'en')

def get_text(key, lang='en'):
    texts = {
        'en': {
            'welcome': "Welcome! Choose an option:",
            'new_file': "Send a file or press Done.",
            'file_received': "File received. Send a name for this file (or /cancel):",
            'my_files': "Select a file, use navigation, search, or press Back.",
            'file_options': "📁 {title}\n📏 Size: {size}\n📌 Type: {emoji} {type}",
            'rename_prompt': "Send the new name:",
            'addname_prompt': "Send additional name:",
            'search_prompt': "Send the search term:",
            'broadcast_prompt': "Send the message to broadcast to all users:",
            'no_files': "No files found.",
            'file_saved': "✅ {emoji} **{name}** saved.",
            'name_updated': "Name updated.",
            'name_added': "Name added.",
            'name_exists': "Name already exists.",
            'file_deleted': "File deleted.",
            'canceled': "Operation cancelled.",
            'total_storage': "Total storage: {size}",
            'join_channel': "Please join @dilemmapl first.",
            'error': "An error occurred. Please try again later.",
            'unknown': "Unknown command. Use /start.",
        },
        'fa': {
            'welcome': "خوش آمدید! یک گزینه انتخاب کنید:",
            'new_file': "یک فایل ارسال کنید یا Done را بزنید.",
            'file_received': "فایل دریافت شد. نامی برای این فایل ارسال کنید (یا /cancel):",
            'my_files': "یک فایل انتخاب کنید، از ناوبری استفاده کنید، جستجو کنید یا Back را بزنید.",
            'file_options': "📁 {title}\n📏 اندازه: {size}\n📌 نوع: {emoji} {type}",
            'rename_prompt': "نام جدید را ارسال کنید:",
            'addname_prompt': "نام اضافی را ارسال کنید:",
            'search_prompt': "عبارت جستجو را ارسال کنید:",
            'broadcast_prompt': "پیام برای پخش همگانی به همه کاربران را ارسال کنید:",
            'no_files': "هیچ فایلی یافت نشد.",
            'file_saved': "✅ {emoji} **{name}** ذخیره شد.",
            'name_updated': "نام به‌روز شد.",
            'name_added': "نام اضافه شد.",
            'name_exists': "نام از قبل وجود دارد.",
            'file_deleted': "فایل حذف شد.",
            'canceled': "عملیات لغو شد.",
            'total_storage': "حجم کل: {size}",
            'join_channel': "لطفاً ابتدا به @dilemmapl بپیوندید.",
            'error': "خطایی رخ داد. لطفاً دوباره تلاش کنید.",
            'unknown': "دستور ناشناخته. از /start استفاده کنید.",
        }
    }
    return texts.get(lang, texts['en']).get(key, key)

# ====================== DATABASE ======================
async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=5, max_inactive_connection_lifetime=300.0
        )
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    custom_names JSONB NOT NULL DEFAULT '[]',
                    file_type TEXT NOT NULL,
                    file_size BIGINT NOT NULL DEFAULT 0
                )
            ''')
            await conn.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS file_size BIGINT NOT NULL DEFAULT 0")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_seen TIMESTAMP DEFAULT NOW(),
                    lang TEXT DEFAULT 'en',
                    is_blocked BOOLEAN DEFAULT FALSE
                )
            ''')
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS lang TEXT DEFAULT 'en'")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN DEFAULT FALSE")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    action TEXT NOT NULL,
                    file_id INTEGER,
                    details TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    message TEXT NOT NULL,
                    scheduled_time TIMESTAMP,
                    sent BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
    return db_pool

async def close_pool():
    global db_pool
    if db_pool:
        await db_pool.close()
        db_pool = None
        logger.info("Database pool closed.")

# بستن pool هنگام خاموشی
def shutdown_handler():
    asyncio.create_task(close_pool())
atexit.register(shutdown_handler)
signal.signal(signal.SIGTERM, lambda s, f: asyncio.create_task(close_pool()))

async def record_user(user_id, lang='en'):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, first_seen, lang) VALUES ($1, NOW(), $2) ON CONFLICT (user_id) DO UPDATE SET lang = EXCLUDED.lang",
            user_id, lang
        )

async def add_file(user_id, file_id, file_name, custom_names, file_type, file_size):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO files (user_id, file_id, file_name, custom_names, file_type, file_size) VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, file_id, file_name, json.dumps(custom_names), file_type, file_size
        )

async def get_user_files(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            return await conn.fetch("SELECT * FROM files ORDER BY id")
        return await conn.fetch("SELECT * FROM files WHERE user_id=$1 ORDER BY id", user_id)

async def get_user_files_paginated(user_id, offset, limit):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            rows = await conn.fetch("SELECT * FROM files ORDER BY id LIMIT $1 OFFSET $2", limit, offset)
        else:
            rows = await conn.fetch("SELECT * FROM files WHERE user_id=$1 ORDER BY id LIMIT $2 OFFSET $3", user_id, limit, offset)
        return rows

async def get_user_files_count(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            row = await conn.fetchrow("SELECT COUNT(*) FROM files")
        else:
            row = await conn.fetchrow("SELECT COUNT(*) FROM files WHERE user_id=$1", user_id)
        return row[0]

async def search_files(user_id, query):
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = f"%{query}%"
        if user_id == ADMIN_ID:
            return await conn.fetch("SELECT * FROM files WHERE custom_names::text ILIKE $1 OR file_name ILIKE $1", q)
        return await conn.fetch("SELECT * FROM files WHERE user_id=$1 AND (custom_names::text ILIKE $2 OR file_name ILIKE $2)", user_id, q)

async def search_files_inline(user_id, query):
    """جستجوی بهینه برای اینلاین (فقط فیلدهای مورد نیاز)"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = f"%{query}%"
        if user_id == ADMIN_ID:
            return await conn.fetch("SELECT id, file_id, file_type, file_name, custom_names FROM files WHERE custom_names::text ILIKE $1 OR file_name ILIKE $1 ORDER BY id LIMIT 50", q)
        return await conn.fetch("SELECT id, file_id, file_type, file_name, custom_names FROM files WHERE user_id=$1 AND (custom_names::text ILIKE $2 OR file_name ILIKE $2) ORDER BY id LIMIT 50", user_id, q)

async def get_file_by_id(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_db_id)

async def get_user_total_size(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            row = await conn.fetchrow("SELECT SUM(file_size) FROM files")
        else:
            row = await conn.fetchrow("SELECT SUM(file_size) FROM files WHERE user_id=$1", user_id)
        return row[0] if row[0] else 0

async def delete_file(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id=$1", file_db_id)

async def update_names(file_db_id, custom_names):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET custom_names=$1 WHERE id=$2", json.dumps(custom_names), file_db_id)

async def get_all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE is_blocked = FALSE")
        return [row['user_id'] for row in rows]

async def get_user_lang_from_db(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT lang FROM users WHERE user_id=$1", user_id)
        return row['lang'] if row else 'en'

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def human_readable_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(units)-1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"

def safe_json_loads(data):
    try:
        return json.loads(data) if data else []
    except:
        return []

# ====================== LOGGING ======================
async def log_action(user_id, action, file_id=None, details=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO logs (user_id, action, file_id, details) VALUES ($1, $2, $3, $4)",
            user_id, action, file_id, details
        )

async def get_recent_logs(limit=50):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM logs ORDER BY timestamp DESC LIMIT $1", limit)

async def get_user_logs(user_id, limit=50):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM logs WHERE user_id=$1 ORDER BY timestamp DESC LIMIT $2", user_id, limit)

# ====================== ANNOUNCEMENTS ======================
async def create_announcement(message, scheduled_time=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO announcements (message, scheduled_time) VALUES ($1, $2)",
            message, scheduled_time
        )

async def get_pending_announcements():
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM announcements WHERE sent = FALSE AND (scheduled_time IS NULL OR scheduled_time <= NOW())")

async def mark_announcement_sent(aid):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE announcements SET sent = TRUE WHERE id = $1", aid)

async def get_announcement_history(limit=20):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM announcements ORDER BY created_at DESC LIMIT $1", limit)

# ====================== USER STATS ======================
async def get_user_stats(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
            total_files = await conn.fetchval("SELECT COUNT(*) FROM files")
            total_size = await conn.fetchval("SELECT COALESCE(SUM(file_size), 0) FROM files")
            file_type_counts = await conn.fetch("SELECT file_type, COUNT(*) FROM files GROUP BY file_type")
            daily_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE first_seen > NOW() - INTERVAL '1 day'")
            weekly_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE first_seen > NOW() - INTERVAL '7 days'")
            return {
                'total_users': total_users,
                'total_files': total_files,
                'total_size': total_size,
                'file_type_counts': dict(file_type_counts),
                'daily_users': daily_users,
                'weekly_users': weekly_users,
            }
        else:
            user_files = await conn.fetchval("SELECT COUNT(*) FROM files WHERE user_id=$1", user_id)
            user_size = await conn.fetchval("SELECT COALESCE(SUM(file_size), 0) FROM files WHERE user_id=$1", user_id)
            return {
                'user_files': user_files,
                'user_size': user_size,
            }

async def get_all_users_with_stats(offset=0, limit=10):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT u.user_id, u.first_seen, u.lang, u.is_blocked,
                   COUNT(f.id) as file_count,
                   COALESCE(SUM(f.file_size), 0) as total_size
            FROM users u
            LEFT JOIN files f ON u.user_id = f.user_id
            GROUP BY u.user_id
            ORDER BY u.first_seen DESC
            LIMIT $1 OFFSET $2
        """, limit, offset)
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        return rows, total

async def toggle_block_user(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        current = await conn.fetchval("SELECT is_blocked FROM users WHERE user_id=$1", user_id)
        new_status = not current
        await conn.execute("UPDATE users SET is_blocked=$1 WHERE user_id=$2", new_status, user_id)
        return new_status

async def delete_user_files(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE user_id=$1", user_id)

# ====================== ERROR HANDLER ======================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            user_id = update.effective_user.id
            lang = await get_user_lang_from_db(user_id)
            await update.effective_message.reply_text(get_text('error', lang))
    except:
        pass

# ====================== AUDIO DETECTION ======================
def is_audio_file(message):
    if message.audio:
        return True, "audio", message.audio.file_name or "audio.mp3"
    if message.document:
        mime = message.document.mime_type or ""
        file_name = message.document.file_name or ""
        ext = file_name.lower()
        if (mime.startswith("audio/") or 
            ext.endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg', '.aac', '.wma', '.opus', '.m4b'))):
            return True, "audio", file_name
    return False, None, None

# ====================== INLINE QUERY ======================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    results = []
    
    logger.info(f"Inline query from user {user_id} | query: '{query_text}'")
    
    try:
        if query_text:
            rows = await search_files_inline(user_id, query_text)
        else:
            # اگر کوئری خالی بود، چند فایل اخیر را نشان بده
            rows = await get_user_files_paginated(user_id, 0, 20)
        for row in rows:
            try:
                db_id = str(row['id'])
                file_id = row['file_id']
                ftype = row['file_type']
                file_name = row.get('file_name', 'file')
                cnames = safe_json_loads(row.get('custom_names'))
                if not cnames: cnames = [file_name]
                title = cnames[0]
                if ftype == "photo":
                    results.append(InlineQueryResultCachedPhoto(id=db_id, photo_file_id=file_id, title=title))
                elif ftype == "video":
                    results.append(InlineQueryResultCachedVideo(id=db_id, video_file_id=file_id, title=title))
                elif ftype == "voice":
                    results.append(InlineQueryResultCachedVoice(id=db_id, voice_file_id=file_id, title=title))
                elif ftype == "audio":
                    results.append(InlineQueryResultCachedAudio(id=db_id, audio_file_id=file_id, title=title))
                else:
                    results.append(InlineQueryResultCachedDocument(id=db_id, document_file_id=file_id, title=title))
            except Exception as e:
                logger.warning(f"Error processing file {row.get('id')}: {e}")
                continue
        await update.inline_query.answer(results[:50], cache_time=5, is_personal=True)
    except Exception as e:
        logger.error(f"Critical inline query error: {e}", exc_info=True)
        await update.inline_query.answer([])

# ====================== HANDLE FILE ======================
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    lang = get_user_lang(context)

    # اگر در حالت آپلود نبودیم، به‌طور خودکار واردش می‌شویم
    if context.user_data.get('state') != "awaiting_file":
        context.user_data['state'] = "awaiting_file"
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
        await message.reply_text(get_text('new_file', lang), reply_markup=BATCH_KEYBOARD)

    is_audio, file_type, original_name = is_audio_file(message)

    if message.photo:
        file_type = "photo"
        file = message.photo[-1]
        file_name = "photo.jpg"
    elif message.video:
        file_type = "video"
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.voice:
        file_type = "voice"
        file = message.voice
        file_name = "voice.ogg"
    elif is_audio:
        file_type = "audio"
        file = message.audio or message.document
        file_name = original_name
    elif message.document:
        file_type = "document"
        file = message.document
        file_name = message.document.file_name or "document"
    else:
        await message.reply_text("This file type is not supported.")
        return

    file_id = file.file_id
    file_size = getattr(file, 'file_size', 0) or 0

    await add_file(user.id, file_id, file_name, [file_name], file_type, file_size)
    await log_action(user.id, 'upload', details=f"{file_type}: {file_name}")
    await message.reply_text(
        get_text('file_saved', lang).format(emoji=FILE_TYPE_EMOJI.get(file_type, '📄'), name=file_name),
        parse_mode="Markdown"
    )

# ====================== ADMIN PANEL ======================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized.")
        return
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("📜 Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("📢 Announcements", callback_data="admin_announce")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ **Admin Panel**\nSelect an option:", reply_markup=markup, parse_mode="Markdown")

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stats = await get_user_stats(ADMIN_ID)
    msg = (
        f"📊 **Dashboard**\n"
        f"👥 Total Users: {stats['total_users']}\n"
        f"📁 Total Files: {stats['total_files']}\n"
        f"💾 Total Size: {human_readable_size(stats['total_size'])}\n"
        f"📈 New Users (24h): {stats['daily_users']}\n"
        f"📈 New Users (7d): {stats['weekly_users']}\n"
        f"📂 File Types:\n"
    )
    for ftype, count in stats['file_type_counts'].items():
        msg += f"  {FILE_TYPE_EMOJI.get(ftype, '📄')} {ftype}: {count}\n"
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    query = update.callback_query
    await query.answer()
    limit = 10
    offset = page * limit
    users, total = await get_all_users_with_stats(offset, limit)
    if not users:
        await query.edit_message_text("No users found.")
        return
    msg = f"👥 **Users List** (Page {page+1} of {-(-total//limit)}):\n"
    for i, row in enumerate(users):
        blocked = "🚫" if row['is_blocked'] else "✅"
        msg += f"{i+1+offset}. ID: `{row['user_id']}` {blocked} | Files: {row['file_count']} | Size: {human_readable_size(row['total_size'])} | Lang: {row['lang']}\n"
    keyboard = []
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{-(-total//limit)}", callback_data="noop"))
    if page < -(-total//limit) - 1: nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔍 Search User", callback_data="admin_search_user")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)

async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please send the user ID to search:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))
    context.user_data['admin_state'] = 'waiting_user_search'

async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    logs = await get_recent_logs(30)
    if not logs:
        await query.edit_message_text("No logs found.")
        return
    msg = "📜 **Recent Logs** (last 30):\n"
    for log in logs:
        timestamp = log['timestamp'].strftime("%Y-%m-%d %H:%M")
        msg += f"`{timestamp}` | User: {log['user_id']} | {log['action']}"
        if log['details']:
            msg += f" | {log['details']}"
        msg += "\n"
    keyboard = [
        [InlineKeyboardButton("🔍 Filter by User", callback_data="admin_filter_logs")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)

async def admin_filter_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send user ID to filter logs:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))
    context.user_data['admin_state'] = 'waiting_log_filter'

async def admin_announce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📢 Send Now", callback_data="admin_announce_now")],
        [InlineKeyboardButton("⏰ Schedule", callback_data="admin_announce_schedule")],
        [InlineKeyboardButton("📋 History", callback_data="admin_announce_history")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("📢 **Announcement Manager**", parse_mode="Markdown", reply_markup=markup)

async def admin_announce_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send the announcement message now:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))
    context.user_data['admin_state'] = 'waiting_announce_now'

async def admin_announce_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send the announcement message and schedule time (YYYY-MM-DD HH:MM) separated by '|'.\nExample: `Hello everyone | 2026-07-01 10:00`\n(Time is in Tehran timezone)", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))
    context.user_data['admin_state'] = 'waiting_announce_schedule'

async def admin_announce_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    history = await get_announcement_history()
    if not history:
        await query.edit_message_text("No announcements yet.")
        return
    msg = "📋 **Announcement History** (last 20):\n"
    for ann in history:
        sent = "✅" if ann['sent'] else "⏳"
        scheduled = ann['scheduled_time'].strftime("%Y-%m-%d %H:%M") if ann['scheduled_time'] else "Now"
        msg += f"{sent} Scheduled: {scheduled}\n{ann['message'][:100]}...\n\n"
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=markup)

async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🔢 Set Page Size", callback_data="admin_set_pagesize")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚙️ **Settings**", parse_mode="Markdown", reply_markup=markup)

async def admin_set_pagesize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Send new page size (e.g., 10):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_back")]]))
    context.user_data['admin_state'] = 'waiting_pagesize'

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['admin_state'] = None
    # نمایش مجدد پنل ادمین با ویرایش پیام فعلی
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("📜 Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("📢 Announcements", callback_data="admin_announce")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("🛠️ **Admin Panel**\nSelect an option:", reply_markup=markup, parse_mode="Markdown")

# ====================== UI IMPROVEMENTS ======================
# توابع انتخاب گروهی و غیره به‌روزرسانی شده برای ویرایش پیام

async def show_myfiles_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    user = update.effective_user
    msg = get_msg(update)
    if not msg: return
    lang = get_user_lang(context)
    view_mode = context.user_data.get('view_mode', 'list')
    page_size = context.user_data.get('page_size', PAGE_SIZE)
    offset = page * page_size
    files = await get_user_files_paginated(user.id, offset, page_size)
    total = await get_user_files_count(user.id)
    total_pages = max(1, -(-total // page_size))
    
    keyboard = []
    keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search_start")])
    keyboard.append([InlineKeyboardButton(f"🔄 View: {view_mode.capitalize()}", callback_data="toggle_view")])
    
    if files:
        keyboard.append([InlineKeyboardButton("☑️ Select All", callback_data="select_all"), InlineKeyboardButton("❌ Deselect All", callback_data="deselect_all")])
    
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        cnames = safe_json_loads(row['custom_names'])
        name = cnames[0] if cnames else row['file_name']
        selected = context.user_data.get('selected_files', [])
        check = "☑️" if row['id'] in selected else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {emoji} {name}", callback_data=f"select_{row['id']}")])
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
    if nav: keyboard.append(nav)
    
    if context.user_data.get('selected_files'):
        keyboard.append([InlineKeyboardButton("🗑️ Delete Selected", callback_data="batch_delete"), InlineKeyboardButton("🏷️ Add Tag", callback_data="batch_addtag")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])
    markup = InlineKeyboardMarkup(keyboard)
    new_text = f"📂 Your files (Page {page+1}/{total_pages}) - {total} files"
    
    # اگر پیام قبلی وجود دارد، ویرایش شود
    if context.user_data.get('myfiles_list_msg'):
        try:
            await msg.edit_text(new_text, reply_markup=markup)
            return
        except:
            pass
    sent = await msg.reply_text(new_text, reply_markup=markup)
    context.user_data['myfiles_list_msg'] = sent.message_id
    context.user_data['page'] = page

async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = get_msg(update)
    if not msg: return
    lang = get_user_lang(context)
    query = context.user_data.get('search_query', '').strip()
    if not query:
        await enter_state(update, context, "myfiles_list")
        return
    results = await search_files(user.id, query)
    if not results:
        await msg.reply_text(get_text('no_files', lang), reply_markup=BACK_KEYBOARD)
        return
    keyboard = []
    for row in results:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        cnames = safe_json_loads(row['custom_names'])
        name = cnames[0] if cnames else row['file_name']
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_files")])
    markup = InlineKeyboardMarkup(keyboard)
    await msg.reply_text(f"Search results for '{query}':", reply_markup=markup)

async def enter_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str):
    msg = get_msg(update)
    if not msg:
        # اگر پیام وجود نداشت، از روش جایگزین استفاده کنیم (فقط برای کالبک‌ها)
        if update.callback_query:
            msg = update.callback_query.message
        else:
            return
    context.user_data['state'] = state
    lang = get_user_lang(context)
    if state == "main":
        await msg.reply_text(get_text('welcome', lang), reply_markup=MAIN_KEYBOARD)
    elif state == "awaiting_file":
        await msg.reply_text(get_text('new_file', lang), reply_markup=BATCH_KEYBOARD)
    elif state == "awaiting_name":
        await msg.reply_text(get_text('file_received', lang), reply_markup=BACK_KEYBOARD)
    elif state == "myfiles_list":
        context.user_data['view_mode'] = context.user_data.get('view_mode', 'list')
        await show_myfiles_page(update, context, page=0)
        await msg.reply_text(get_text('my_files', lang), reply_markup=BACK_KEYBOARD)
    elif state == "file_options":
        file_id = context.user_data.get('current_file_id')
        if not file_id:
            await enter_state(update, context, "myfiles_list")
            return
        row = await get_file_by_id(file_id)
        if not row:
            await msg.reply_text("File not found.", reply_markup=BACK_KEYBOARD)
            return
        cnames = safe_json_loads(row['custom_names'])
        title = cnames[0] if cnames else row['file_name']
        size_str = human_readable_size(row['file_size'])
        type_emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("👁️ Show", callback_data=f"showf_{file_id}")],
            [InlineKeyboardButton("✏️ Rename", callback_data=f"renamef_{file_id}"), InlineKeyboardButton("➕ Add Name", callback_data=f"addnamef_{file_id}")],
            [InlineKeyboardButton("🗑️ Delete", callback_data=f"delf_{file_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_files")]
        ])
        text = get_text('file_options', lang).format(title=title, size=size_str, emoji=type_emoji, type=row['file_type'])
        sent = await msg.reply_text(text, reply_markup=markup)
        context.user_data['file_options_msg'] = sent.message_id
    elif state == "awaiting_rename_text":
        await msg.reply_text(get_text('rename_prompt', lang), reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_addname_text":
        await msg.reply_text(get_text('addname_prompt', lang), reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_search":
        await msg.reply_text(get_text('search_prompt', lang), reply_markup=BACK_KEYBOARD)
    elif state == "search_results":
        await show_search_results(update, context)
    elif state == "awaiting_broadcast_message":
        await msg.reply_text(get_text('broadcast_prompt', lang), reply_markup=BACK_KEYBOARD)
    elif state == "settings":
        await show_settings(update, context)

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_user_lang(context)
    current_lang = LANGUAGES.get(lang, 'English')
    view_mode = context.user_data.get('view_mode', 'list')
    keyboard = [
        [InlineKeyboardButton(f"🌐 Language: {current_lang}", callback_data="settings_lang")],
        [InlineKeyboardButton(f"🖼️ View Mode: {view_mode.capitalize()}", callback_data="settings_view")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_main")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        await msg.reply_text("⚙️ **Settings**", parse_mode="Markdown", reply_markup=markup)

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get('nav_history', [])
    prev_state = history.pop() if history else "main"
    context.user_data['nav_history'] = history
    for key in ['pending_file', 'rename_id', 'addname_id', 'current_file_id', 'myfiles_list_msg', 'file_options_msg', 'page', 'search_query', 'delete_file_id', 'selected_files']:
        context.user_data.pop(key, None)
    context.user_data['state'] = prev_state
    await enter_state(update, context, prev_state)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = context.user_data.get('lang', 'en')
    await record_user(user.id, lang)
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text(get_text('join_channel', lang))
        return
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    await log_action(user.id, 'start')
    await update.message.reply_text(get_text('welcome', lang), reply_markup=MAIN_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    lang = get_user_lang(context)
    await update.message.reply_text("Use the menu buttons.\nInline search: @botusername query", parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ['pending_file', 'rename_id', 'addname_id', 'current_file_id', 'myfiles_list_msg', 'file_options_msg', 'page', 'search_query', 'delete_file_id', 'pending_duplicate', 'selected_files', 'batch_tag_action', 'batch_selected']:
        context.user_data.pop(key, None)
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    lang = get_user_lang(context)
    await update.message.reply_text(get_text('canceled', lang), reply_markup=MAIN_KEYBOARD)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    lang = get_user_lang(context)

    # Admin callbacks
    if data.startswith("admin_"):
        if user.id != ADMIN_ID:
            await query.edit_message_text("Unauthorized.")
            return
        if data == "admin_dashboard":
            await admin_dashboard(update, context)
        elif data == "admin_users":
            await admin_users(update, context, 0)
        elif data.startswith("admin_users_page_"):
            page = int(data.split("_")[-1])
            await admin_users(update, context, page)
        elif data == "admin_logs":
            await admin_logs(update, context)
        elif data == "admin_announce":
            await admin_announce(update, context)
        elif data == "admin_settings":
            await admin_settings(update, context)
        elif data == "admin_back":
            await admin_back(update, context)
        elif data == "admin_search_user":
            await admin_search_user(update, context)
        elif data == "admin_filter_logs":
            await admin_filter_logs(update, context)
        elif data == "admin_announce_now":
            await admin_announce_now(update, context)
        elif data == "admin_announce_schedule":
            await admin_announce_schedule(update, context)
        elif data == "admin_announce_history":
            await admin_announce_history(update, context)
        elif data == "admin_set_pagesize":
            await admin_set_pagesize(update, context)
        elif data.startswith("admin_toggleblock_"):
            target_id = int(data.split("_")[-1])
            new_status = await toggle_block_user(target_id)
            await query.edit_message_text(f"User {target_id} block status: {'Blocked' if new_status else 'Unblocked'}")
            await admin_back(update, context)
        elif data.startswith("admin_deleteuserfiles_"):
            target_id = int(data.split("_")[-1])
            await delete_user_files(target_id)
            await query.edit_message_text(f"All files of user {target_id} deleted.")
            await admin_back(update, context)
        return

    # File management
    if data.startswith("listfile_"):
        file_id = int(data[9:])
        context.user_data['current_file_id'] = file_id
        context.user_data['state'] = "file_options"
        await enter_state(update, context, "file_options")
    elif data.startswith("showf_"):
        file_id = int(data[6:])
        row = await get_file_by_id(file_id)
        if row:
            ftype = row['file_type']
            fid = row['file_id']
            if ftype == "photo": await context.bot.send_photo(user.id, fid)
            elif ftype == "video": await context.bot.send_video(user.id, fid)
            elif ftype == "audio": await context.bot.send_audio(user.id, fid)
            elif ftype == "voice": await context.bot.send_voice(user.id, fid)
            else: await context.bot.send_document(user.id, fid)
            await log_action(user.id, 'view', file_id=file_id)
    elif data.startswith("delf_"):
        file_id = int(data[5:])
        # حذف پیام قبلی و نمایش پیام تأیید جدید
        await query.edit_message_text("Are you sure?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes", callback_data=f"confirmdel_{file_id}"), InlineKeyboardButton("No", callback_data="cancel_del")]
        ]))
    elif data.startswith("confirmdel_"):
        file_id = int(data[11:])
        await delete_file(file_id)
        await log_action(user.id, 'delete', file_id=file_id)
        await query.edit_message_text("File deleted.")
        # حذف پیام تأیید
        await query.message.delete()
        await go_back(update, context)
    elif data == "cancel_del":
        await query.edit_message_text("Cancelled.")
        await query.message.delete()
        await go_back(update, context)
    elif data.startswith("renamef_"):
        context.user_data['rename_id'] = int(data[8:])
        await enter_state(update, context, "awaiting_rename_text")
    elif data.startswith("addnamef_"):
        context.user_data['addname_id'] = int(data[9:])
        await enter_state(update, context, "awaiting_addname_text")
    elif data == "search_start":
        await enter_state(update, context, "awaiting_search")
    elif data.startswith("page_"):
        page = int(data[5:])
        await show_myfiles_page(update, context, page)
    elif data == "back_to_files":
        await enter_state(update, context, "myfiles_list")
    elif data == "back_to_main":
        await enter_state(update, context, "main")
    elif data == "noop":
        pass
    elif data == "toggle_view":
        current = context.user_data.get('view_mode', 'list')
        context.user_data['view_mode'] = 'gallery' if current == 'list' else 'list'
        await show_myfiles_page(update, context, context.user_data.get('page', 0))
    elif data.startswith("select_"):
        file_id = int(data[7:])
        selected = context.user_data.get('selected_files', [])
        if file_id in selected:
            selected.remove(file_id)
        else:
            selected.append(file_id)
        context.user_data['selected_files'] = selected
        await show_myfiles_page(update, context, context.user_data.get('page', 0))
    elif data == "select_all":
        page = context.user_data.get('page', 0)
        page_size = context.user_data.get('page_size', PAGE_SIZE)
        offset = page * page_size
        files = await get_user_files_paginated(user.id, offset, page_size)
        selected = context.user_data.get('selected_files', [])
        for row in files:
            if row['id'] not in selected:
                selected.append(row['id'])
        context.user_data['selected_files'] = selected
        await show_myfiles_page(update, context, page)
    elif data == "deselect_all":
        context.user_data['selected_files'] = []
        await show_myfiles_page(update, context, context.user_data.get('page', 0))
    elif data == "batch_delete":
        selected = context.user_data.get('selected_files', [])
        if not selected:
            await query.answer("No files selected.")
            return
        for fid in selected:
            await delete_file(fid)
            await log_action(user.id, 'batch_delete', file_id=fid)
        context.user_data['selected_files'] = []
        await query.edit_message_text(f"Deleted {len(selected)} files.")
        await show_myfiles_page(update, context, context.user_data.get('page', 0))
    elif data == "batch_addtag":
        selected = context.user_data.get('selected_files', [])
        if not selected:
            await query.answer("No files selected.")
            return
        context.user_data['batch_selected'] = selected
        await query.edit_message_text("Send the tag to add to selected files:")
        context.user_data['state'] = 'awaiting_batch_tag'
    elif data == "settings_lang":
        lang_choice = [
            [InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en")],
            [InlineKeyboardButton("🇮🇷 Persian", callback_data="setlang_fa")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_settings")]
        ]
        markup = InlineKeyboardMarkup(lang_choice)
        await query.edit_message_text("Select your language:", reply_markup=markup)
    elif data.startswith("setlang_"):
        lang_code = data[8:]
        context.user_data['lang'] = lang_code
        await record_user(user.id, lang_code)
        await query.answer(f"Language set to {LANGUAGES.get(lang_code, 'English')}")
        await show_settings(update, context)
    elif data == "back_to_settings":
        await show_settings(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""
    lang = get_user_lang(context)

    if text.strip() == "🔙 Back":
        await go_back(update, context)
        return
    if text.strip() == "✅ Done":
        if context.user_data.get('state') in ("awaiting_file",):
            await enter_state(update, context, "main")
            return
    if text.strip() == "❌ Cancel":
        await cancel(update, context)
        return

    # File upload
    if message.photo or message.video or message.audio or message.voice or message.document:
        await handle_file(update, context)
        return

    await record_user(user.id)
    if not await check_membership(context.bot, user.id):
        await message.reply_text(get_text('join_channel', lang))
        return

    state = context.user_data.get('state', 'main')
    admin_state = context.user_data.get('admin_state')

    # Handle admin states
    if admin_state == 'waiting_user_search':
        try:
            target_id = int(text.strip())
            users, _ = await get_all_users_with_stats(0, 1000)  # جستجو در کل کاربران
            target = next((u for u in users if u['user_id'] == target_id), None)
            if target:
                blocked = "🚫" if target['is_blocked'] else "✅"
                msg = f"👤 User `{target['user_id']}`\n"
                msg += f"📅 First seen: {target['first_seen']}\n"
                msg += f"📁 Files: {target['file_count']}\n"
                msg += f"💾 Size: {human_readable_size(target['total_size'])}\n"
                msg += f"🌐 Language: {target['lang']}\n"
                msg += f"Status: {blocked}\n"
                keyboard = [
                    [InlineKeyboardButton("🚫 Toggle Block", callback_data=f"admin_toggleblock_{target_id}")],
                    [InlineKeyboardButton("🗑️ Delete All Files", callback_data=f"admin_deleteuserfiles_{target_id}")],
                    [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
                ]
                markup = InlineKeyboardMarkup(keyboard)
                await message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
            else:
                await message.reply_text("User not found.")
        except:
            await message.reply_text("Invalid user ID. Please send a number.")
        context.user_data['admin_state'] = None
        return

    if admin_state == 'waiting_log_filter':
        try:
            target_id = int(text.strip())
            logs = await get_user_logs(target_id, 30)
            if not logs:
                await message.reply_text("No logs for this user.")
                return
            msg = f"📜 Logs for user `{target_id}` (last 30):\n"
            for log in logs:
                timestamp = log['timestamp'].strftime("%Y-%m-%d %H:%M")
                msg += f"`{timestamp}` | {log['action']}"
                if log['details']:
                    msg += f" | {log['details']}"
                msg += "\n"
            await message.reply_text(msg, parse_mode="Markdown")
        except:
            await message.reply_text("Invalid user ID.")
        context.user_data['admin_state'] = None
        return

    if admin_state == 'waiting_announce_now':
        user_ids = await get_all_user_ids()
        success = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(uid, text)
                success += 1
                await asyncio.sleep(random.uniform(0.05, 0.15))  # تأخیر تصادفی
            except:
                pass
        await message.reply_text(f"Announcement sent to {success} users.")
        await log_action(ADMIN_ID, 'announcement', details=f"Sent to {success} users")
        context.user_data['admin_state'] = None
        await enter_state(update, context, "main")
        return

    if admin_state == 'waiting_announce_schedule':
        try:
            parts = text.split('|')
            if len(parts) != 2:
                await message.reply_text("Invalid format. Use `message | YYYY-MM-DD HH:MM`")
                return
            msg_text = parts[0].strip()
            schedule_str = parts[1].strip()
            # استفاده از منطقه زمانی تهران
            scheduled_time = datetime.strptime(schedule_str, "%Y-%m-%d %H:%M")
            scheduled_time = TIMEZONE.localize(scheduled_time).astimezone(pytz.UTC).replace(tzinfo=None)
            await create_announcement(msg_text, scheduled_time)
            await message.reply_text(f"Announcement scheduled for {schedule_str} Tehran time.")
            await log_action(ADMIN_ID, 'announcement_schedule', details=f"Scheduled: {schedule_str}")
        except Exception as e:
            await message.reply_text(f"Error: {e}")
        context.user_data['admin_state'] = None
        await enter_state(update, context, "main")
        return

    if admin_state == 'waiting_pagesize':
        try:
            new_size = int(text.strip())
            if new_size < 1 or new_size > 100:
                await message.reply_text("Page size must be between 1 and 100.")
                return
            context.user_data['page_size'] = new_size
            await message.reply_text(f"Page size set to {new_size}.")
        except:
            await message.reply_text("Invalid number.")
        context.user_data['admin_state'] = None
        await enter_state(update, context, "main")
        return

    # Batch add tag
    if state == 'awaiting_batch_tag':
        selected = context.user_data.get('batch_selected', [])
        if not selected:
            await message.reply_text("No files selected.")
            context.user_data['state'] = "main"
            return
        tag = text.strip()
        for fid in selected:
            row = await get_file_by_id(fid)
            if row:
                cnames = safe_json_loads(row['custom_names'])
                if tag not in cnames:
                    cnames.append(tag)
                    await update_names(fid, cnames)
        context.user_data['batch_selected'] = []
        context.user_data['state'] = "main"
        await message.reply_text(f"Tag '{tag}' added to {len(selected)} files.")
        await log_action(user.id, 'batch_addtag', details=f"Tag: {tag}")
        await enter_state(update, context, "main")
        return

    # Main menu
    if state == "main":
        if text == "➕ New File":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
            await enter_state(update, context, "awaiting_file")
        elif text == "📂 My Files":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
            await enter_state(update, context, "myfiles_list")
        elif text == "📊 Memory":
            size = await get_user_total_size(user.id)
            await message.reply_text(get_text('total_storage', lang).format(size=human_readable_size(size)), reply_markup=MAIN_KEYBOARD)
        elif text == "⚙️ Settings":
            await show_settings(update, context)
        else:
            await message.reply_text(get_text('unknown', lang), reply_markup=MAIN_KEYBOARD)
    elif state == "awaiting_rename_text":
        new_name = text.strip()
        rename_id = context.user_data.get('rename_id')
        if rename_id:
            row = await get_file_by_id(rename_id)
            if row:
                cnames = safe_json_loads(row['custom_names'])
                if cnames:
                    cnames[0] = new_name
                else:
                    cnames = [new_name]
                await update_names(rename_id, cnames)
                await log_action(user.id, 'rename', file_id=rename_id, details=new_name)
                await message.reply_text(get_text('name_updated', lang), reply_markup=MAIN_KEYBOARD)
                context.user_data.pop('rename_id', None)
                await enter_state(update, context, "main")
    elif state == "awaiting_addname_text":
        new_name = text.strip()
        addname_id = context.user_data.get('addname_id')
        if addname_id:
            row = await get_file_by_id(addname_id)
            if row:
                cnames = safe_json_loads(row['custom_names'])
                if new_name not in cnames:
                    cnames.append(new_name)
                    await update_names(addname_id, cnames)
                    await log_action(user.id, 'addname', file_id=addname_id, details=new_name)
                    await message.reply_text(get_text('name_added', lang), reply_markup=MAIN_KEYBOARD)
                else:
                    await message.reply_text(get_text('name_exists', lang), reply_markup=BACK_KEYBOARD)
                context.user_data.pop('addname_id', None)
                await enter_state(update, context, "main")
    elif state == "awaiting_search":
        query = text.strip()
        if query:
            context.user_data['search_query'] = query
            await enter_state(update, context, "search_results")
        else:
            await message.reply_text("Please send a non-empty search term.")
    elif state == "awaiting_broadcast_message":
        if user.id == ADMIN_ID:
            user_ids = await get_all_user_ids()
            success = 0
            for uid in user_ids:
                try:
                    await context.bot.send_message(uid, text)
                    success += 1
                    await asyncio.sleep(random.uniform(0.05, 0.15))
                except:
                    pass
            await message.reply_text(f"Broadcast sent to {success} users.", reply_markup=MAIN_KEYBOARD)
            await log_action(ADMIN_ID, 'broadcast', details=f"Sent to {success} users")
            await enter_state(update, context, "main")
    else:
        await message.reply_text(get_text('unknown', lang), reply_markup=MAIN_KEYBOARD)

# ====================== ANNOUNCEMENT SCHEDULER ======================
async def check_announcements():
    while not shutdown_event.is_set():
        try:
            pending = await get_pending_announcements()
            for ann in pending:
                user_ids = await get_all_user_ids()
                for uid in user_ids:
                    try:
                        await ptb_app.bot.send_message(uid, ann['message'])
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                    except:
                        pass
                await mark_announcement_sent(ann['id'])
                await log_action(ADMIN_ID, 'scheduled_announcement', details=f"Sent scheduled announcement ID {ann['id']}")
        except Exception as e:
            logger.error(f"Announcement scheduler error: {e}")
        await asyncio.sleep(60)

# ====================== FASTAPI WEBHOOK ======================
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

# ====================== MAIN ======================
async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).updater(None).build()
    ptb_app.add_error_handler(error_handler)
    await ptb_app.initialize()
    await ptb_app.start()

    # Commands
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("myfiles", lambda u,c: enter_state(u,c,"myfiles_list")))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("admin", admin_panel))
    ptb_app.add_handler(CommandHandler("broadcast", lambda u,c: enter_state(u,c,"awaiting_broadcast_message") if u.effective_user.id == ADMIN_ID else None))

    # Handlers
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_file))

    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

    # Start announcement scheduler
    asyncio.create_task(check_announcements())

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # بستن pool در پایان
        asyncio.run(close_pool())
