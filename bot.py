import logging
import json
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters
import uvicorn
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import io
import csv

# ======================== Logging ========================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ======================== Environment ========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "CHANGE_ME")  # Must be set!
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME", "your-app.onrender.com")
WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")

# ======================== FastAPI & PTB ========================
app = FastAPI()
ptb_app = None
db_pool = None
scheduler = AsyncIOScheduler()

# ======================== Constants ========================
PAGE_SIZES = [5, 10, 20]
DEFAULT_PAGE_SIZE = 5
FILE_TYPE_EMOJI = {
    "photo": "🖼️", "video": "📽️", "audio": "🎵", "voice": "🎙️", "document": "📄"
}

# ======================== Translations (English only) ========================
TEXTS = {
    "main_menu": "Main Menu",
    "my_files": "📁 My Files",
    "new_file": "➕ New File",
    "search": "🔍 Search",
    "memory": "📊 Memory",
    "settings": "⚙️ Settings",
    "admin_panel": "🛠 Admin Panel",
    "back": "🔙 Back",
    "home": "🏠 Home",
    "select_action": "Select an action:",
    "no_files": "You have no files.",
    "file_options": "📁 File Options",
    "show": "Show",
    "rename": "Rename",
    "add_name": "Add Name",
    "delete": "Delete",
    "confirm_delete": "Are you sure?",
    "yes": "Yes",
    "no": "No",
    "cancel": "Cancel",
    "file_deleted": "File deleted.",
    "name_updated": "Name updated.",
    "name_added": "Name added.",
    "name_exists": "Name already exists.",
    "enter_new_name": "Send the new name:",
    "enter_additional_name": "Send additional name:",
    "enter_search_term": "Send the search term:",
    "search_results": "Search results for '{query}':",
    "no_results": "No files found.",
    "total_memory": "Total storage: {size}",
    "page": "Page {current}/{total}",
    "select_file": "Select a file:",
    "select_files_batch": "Select files (tap to toggle), then choose action:",
    "batch_actions": "Batch Actions:",
    "delete_selected": "🗑 Delete Selected",
    "add_tag_selected": "🏷 Add Tag",
    "move_folder_selected": "📁 Move to Folder",
    "done_selecting": "✅ Done",
    "enter_tag": "Enter tag to add to selected files:",
    "select_folder": "Select destination folder:",
    "move_success": "Moved {count} files.",
    "tag_added": "Tag added to {count} files.",
    "delete_selected_confirm": "Delete {count} files?",
    "folder_created": "Folder created.",
    "folder_deleted": "Folder deleted.",
    "folder_renamed": "Folder renamed.",
    "enter_folder_name": "Enter folder name:",
    "enter_new_folder_name": "Enter new folder name:",
    "file_details": "📄 File Details\nName: {name}\nType: {type}\nSize: {size}\nUploaded: {date}\nViews: {views}\nDownloads: {downloads}",
    "share_link": "Share Link: {link}",
    "settings_title": "⚙️ Settings",
    "page_size": "Page size: {size}",
    "sort_by": "Sort by: {sort}",
    "sort_order": "Order: {order}",
    "view_mode": "View mode: {mode}",
    "set_page_size": "Select page size:",
    "set_sort_by": "Select sort by:",
    "set_sort_order": "Select sort order:",
    "set_view_mode": "Select view mode:",
    "gallery": "Gallery",
    "list": "List",
    "tour_welcome": "👋 Welcome! Let's get started.\nUse the menu below to manage your files.",
    "tour_step1": "Step 1: Upload a file using 'New File'.",
    "tour_step2": "Step 2: View your files in 'My Files'.",
    "tour_step3": "Step 3: Search files with 'Search'.",
    "tour_end": "You're all set! Enjoy.",
    "admin_dashboard": "📊 Admin Dashboard\nUsers: {users}\nFiles: {files}\nTotal Size: {size}\nPhotos: {photos}\nVideos: {videos}\nAudios: {audios}\nDocuments: {docs}\nVoices: {voices}",
    "admin_users": "👥 User Management",
    "admin_content": "📂 Content Management",
    "admin_logs": "📜 Logs",
    "admin_announce": "📢 Announcements",
    "admin_settings": "⚙️ System Settings",
    "admin_backup": "💾 Backup",
    "user_details": "User ID: {id}\nFiles: {files}\nSize: {size}\nJoined: {joined}\nLast active: {last}\nBanned: {banned}",
    "block_user": "Block",
    "unblock_user": "Unblock",
    "send_message": "Send Message",
    "delete_user_files": "Delete All Files",
    "enter_message_for_user": "Enter message to send to user:",
    "message_sent": "Message sent.",
    "user_files_deleted": "All files of user deleted.",
    "confirm_delete_user_files": "Delete all files of this user?",
    "logs_title": "Recent Logs",
    "log_entry": "{time} - {user} - {action} {details}",
    "announcements_title": "Announcements",
    "send_now": "Send Now",
    "schedule": "Schedule",
    "history": "History",
    "enter_announcement": "Enter announcement message:",
    "enter_schedule_time": "Enter schedule time in UTC (YYYY-MM-DD HH:MM):",
    "announce_sent": "Announcement sent.",
    "announce_scheduled": "Announcement scheduled.",
    "system_settings": "System Settings",
    "max_user_size": "Max user size (MB):",
    "public_sharing": "Public sharing: {status}",
    "welcome_message": "Welcome message: {msg}",
    "set_max_user_size": "Set max user size (MB):",
    "toggle_public_sharing": "Toggle public sharing",
    "set_welcome_message": "Set welcome message:",
    "backup_created": "Backup created.",
    "restore": "Restore from backup (send JSON file):",
    "restore_success": "Restore successful.",
    "enter_file_or_done": "Send a file or press Done.",
    "file_not_found": "File not found.",
    "user_not_found": "User not found.",
    "new_folder": "📁 New Folder",
    "unknown_action": "Unknown action.",
    "confirm_delete_folder": "Delete folder '{name}' and move files to parent?",
    "folder_options": "📁 Folder Options",
    "rename_folder": "Rename Folder",
    "delete_folder": "Delete Folder",
    "filter_by_type": "Filter by type:",
    "filter_by_user": "Filter by user ID:",
    "filter_by_date": "Filter by date (YYYY-MM-DD):",
    "apply_filter": "Apply Filter",
    "clear_filter": "Clear Filter",
    "csv_export": "📊 Export CSV",
    "choose_report": "Choose report type:",
    "report_users": "Users Report",
    "report_files": "Files Report",
    "report_logs": "Logs Report"
}

# ======================== Database helpers ========================
async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=5, max_inactive_connection_lifetime=300.0
        )
        async with db_pool.acquire() as conn:
            # Files table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    custom_names JSONB NOT NULL DEFAULT '[]',
                    file_type TEXT NOT NULL,
                    file_size BIGINT NOT NULL DEFAULT 0,
                    folder_id INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    view_count INTEGER DEFAULT 0,
                    download_count INTEGER DEFAULT 0
                )
            ''')
            for col in ['folder_id', 'created_at', 'view_count', 'download_count']:
                await conn.execute(f"ALTER TABLE files ADD COLUMN IF NOT EXISTS {col} " + 
                                   ("INTEGER" if col == 'folder_id' else 
                                    "TIMESTAMP WITH TIME ZONE DEFAULT NOW()" if col == 'created_at' else 
                                    "INTEGER DEFAULT 0"))
            # GIN index for custom_names
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_files_custom_names ON files USING gin (custom_names jsonb_path_ops)")
            # Folders
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS folders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    parent_id INTEGER,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            # User settings
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    page_size INTEGER DEFAULT 5,
                    sort_by TEXT DEFAULT 'id',
                    sort_order TEXT DEFAULT 'ASC',
                    view_mode TEXT DEFAULT 'list',
                    tour_shown BOOLEAN DEFAULT FALSE
                )
            ''')
            # Logs
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS logs (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    action TEXT NOT NULL,
                    file_id INTEGER,
                    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    details JSONB
                )
            ''')
            # Announcements
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS announcements (
                    id SERIAL PRIMARY KEY,
                    message TEXT NOT NULL,
                    scheduled_time TIMESTAMP WITH TIME ZONE,
                    sent BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            # User status (banned)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_status (
                    user_id BIGINT PRIMARY KEY,
                    is_banned BOOLEAN DEFAULT FALSE,
                    ban_reason TEXT,
                    banned_at TIMESTAMP WITH TIME ZONE
                )
            ''')
            # System settings
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()")
    return db_pool

async def record_user(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO UPDATE SET last_seen = NOW()", user_id)

async def get_text(user_id, key, **kwargs):
    # English only
    text = TEXTS.get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text

async def send_message(update, context, key, **kwargs):
    user_id = update.effective_user.id
    text = await get_text(user_id, key, **kwargs)
    msg = get_msg(update)
    if msg:
        await msg.reply_text(text, parse_mode='Markdown')

async def log_action(user_id, action, file_id=None, details=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO logs (user_id, action, file_id, details) VALUES ($1, $2, $3, $4)",
            user_id, action, file_id, json.dumps(details) if details else None
        )

# ======================== File & Folder functions ========================
async def add_file(user_id, file_id, file_name, custom_names, file_type, file_size, folder_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO files (user_id, file_id, file_name, custom_names, file_type, file_size, folder_id) VALUES ($1, $2, $3, $4, $5, $6, $7)",
            user_id, file_id, file_name, json.dumps(custom_names), file_type, file_size, folder_id
        )

async def get_user_files(user_id, folder_id=None, sort_by='id', sort_order='ASC', limit=None, offset=None):
    allowed_sort = ['id', 'file_name', 'file_size', 'created_at', 'view_count', 'download_count']
    if sort_by not in allowed_sort:
        sort_by = 'id'
    if sort_order.upper() not in ['ASC', 'DESC']:
        sort_order = 'ASC'
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT * FROM files WHERE user_id=$1"
        params = [user_id]
        if folder_id is not None:
            query += " AND folder_id=$2"
            params.append(folder_id)
        query += f" ORDER BY {sort_by} {sort_order}"
        if limit is not None:
            query += f" LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
            params.extend([limit, offset])
        rows = await conn.fetch(query, *params)
        return rows

async def get_user_files_count(user_id, folder_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT COUNT(*) FROM files WHERE user_id=$1"
        params = [user_id]
        if folder_id is not None:
            query += " AND folder_id=$2"
            params.append(folder_id)
        row = await conn.fetchrow(query, *params)
        return row[0]

async def get_user_total_size(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COALESCE(SUM(file_size), 0) FROM files WHERE user_id=$1", user_id)
        return row[0]

async def get_file_by_id(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_db_id)

async def delete_file(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id=$1", file_db_id)

async def update_names(file_db_id, custom_names):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET custom_names=$1 WHERE id=$2", json.dumps(custom_names), file_db_id)

async def update_file_folder(file_db_id, folder_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET folder_id=$1 WHERE id=$2", folder_id, file_db_id)

async def increment_view(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET view_count = view_count + 1 WHERE id=$1", file_db_id)

# Folders
async def create_folder(user_id, name, parent_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("INSERT INTO folders (user_id, name, parent_id) VALUES ($1, $2, $3) RETURNING id", user_id, name, parent_id)

async def get_folders(user_id, parent_id=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT * FROM folders WHERE user_id=$1"
        params = [user_id]
        if parent_id is not None:
            query += " AND parent_id=$2"
            params.append(parent_id)
        else:
            query += " AND parent_id IS NULL"
        return await conn.fetch(query, *params)

async def get_folder_by_id(folder_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM folders WHERE id=$1", folder_id)

async def delete_folder(folder_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        folder = await get_folder_by_id(folder_id)
        if folder:
            parent = folder['parent_id']
            await conn.execute("UPDATE files SET folder_id=$1 WHERE folder_id=$2", parent, folder_id)
            await conn.execute("DELETE FROM folders WHERE id=$1", folder_id)

async def rename_folder(folder_id, new_name):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE folders SET name=$1 WHERE id=$2", new_name, folder_id)

# User settings
async def get_user_settings(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM user_settings WHERE user_id=$1", user_id)
        if not row:
            await conn.execute("INSERT INTO user_settings (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
            row = await conn.fetchrow("SELECT * FROM user_settings WHERE user_id=$1", user_id)
        return dict(row)

async def update_user_setting(user_id, key, value):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE user_settings SET {key}=$1 WHERE user_id=$2", value, user_id)

# System settings
async def get_system_setting(key, default=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM system_settings WHERE key=$1", key)
        return row['value'] if row else default

async def set_system_setting(key, value):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO system_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value=$2", key, value)

# Banned
async def is_user_banned(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_banned FROM user_status WHERE user_id=$1", user_id)
        return row and row['is_banned']

async def set_user_banned(user_id, banned):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO user_status (user_id, is_banned, banned_at) VALUES ($1, $2, NOW()) ON CONFLICT (user_id) DO UPDATE SET is_banned=$2, banned_at=NOW()", user_id, banned)

async def delete_all_user_files(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE user_id=$1", user_id)

# Admin dashboard
async def get_admin_dashboard():
    pool = await get_pool()
    async with pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        files = await conn.fetchval("SELECT COUNT(*) FROM files")
        total_size = await conn.fetchval("SELECT COALESCE(SUM(file_size), 0) FROM files")
        photos = await conn.fetchval("SELECT COUNT(*) FROM files WHERE file_type='photo'")
        videos = await conn.fetchval("SELECT COUNT(*) FROM files WHERE file_type='video'")
        audios = await conn.fetchval("SELECT COUNT(*) FROM files WHERE file_type='audio'")
        docs = await conn.fetchval("SELECT COUNT(*) FROM files WHERE file_type='document'")
        voices = await conn.fetchval("SELECT COUNT(*) FROM files WHERE file_type='voice'")
        return {
            'users': users, 'files': files, 'size': total_size,
            'photos': photos, 'videos': videos, 'audios': audios,
            'docs': docs, 'voices': voices
        }

# ======================== Helper ========================
def get_msg(update: Update):
    if update.message:
        return update.message
    elif update.callback_query:
        return update.callback_query.message
    return None

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

def format_datetime(dt):
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return ""

async def is_admin(user_id):
    return user_id == ADMIN_ID

# ======================== Main Menu & Navigation ========================
async def show_main_menu(update, context):
    user_id = update.effective_user.id
    buttons = [
        [InlineKeyboardButton(TEXTS["my_files"], callback_data="main_myfiles")],
        [InlineKeyboardButton(TEXTS["new_file"], callback_data="main_newfile")],
        [InlineKeyboardButton(TEXTS["search"], callback_data="main_search")],
        [InlineKeyboardButton(TEXTS["memory"], callback_data="main_memory")],
        [InlineKeyboardButton(TEXTS["settings"], callback_data="main_settings")],
    ]
    if await is_admin(user_id):
        buttons.append([InlineKeyboardButton(TEXTS["admin_panel"], callback_data="main_admin")])
    markup = InlineKeyboardMarkup(buttons)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(TEXTS["select_action"], reply_markup=markup)
        else:
            await msg.reply_text(TEXTS["select_action"], reply_markup=markup)

# ======================== My Files with pagination, selection, folders ========================
async def show_myfiles(update, context, page=0, folder_id=None, edit_msg=False):
    user_id = update.effective_user.id
    context.user_data['current_folder_id'] = folder_id
    settings = await get_user_settings(user_id)
    page_size = settings.get('page_size', DEFAULT_PAGE_SIZE)
    sort_by = settings.get('sort_by', 'id')
    sort_order = settings.get('sort_order', 'ASC')
    view_mode = settings.get('view_mode', 'list')
    offset = page * page_size
    files = await get_user_files(user_id, folder_id=folder_id, sort_by=sort_by, sort_order=sort_order, limit=page_size, offset=offset)
    total = await get_user_files_count(user_id, folder_id=folder_id)
    total_pages = max(1, -(-total // page_size))
    text = TEXTS["page"].format(current=page+1, total=total_pages)
    breadcrumb = "🏠 " + TEXTS["main_menu"]
    if folder_id:
        folder = await get_folder_by_id(folder_id)
        if folder:
            breadcrumb += " > 📁 " + folder['name']
    text = breadcrumb + "\n" + text + "\n"
    if not files:
        text += TEXTS["no_files"]
    else:
        if view_mode == 'list':
            for row in files:
                emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
                name = json.loads(row['custom_names'])[0]
                text += f"{emoji} {name}\n"
        else:
            for row in files:
                emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
                name = json.loads(row['custom_names'])[0][:12]
                text += f"{emoji} {name}\n"
    keyboard = []
    if folder_id:
        keyboard.append([InlineKeyboardButton("📂 " + TEXTS["back"], callback_data=f"folder_parent_{folder_id}")])
    folders = await get_folders(user_id, parent_id=folder_id)
    for f in folders:
        keyboard.append([
            InlineKeyboardButton("📁 " + f['name'], callback_data=f"folder_open_{f['id']}"),
            InlineKeyboardButton("⚙️", callback_data=f"folder_options_{f['id']}")
        ])
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        cb = f"file_{row['id']}"
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=cb)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"myfiles_page_{page-1}_{folder_id if folder_id else ''}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"myfiles_page_{page+1}_{folder_id if folder_id else ''}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("✅ " + TEXTS["select_files_batch"], callback_data="batch_start")])
    keyboard.append([
        InlineKeyboardButton("📁 " + TEXTS["new_folder"], callback_data="folder_create"),
        InlineKeyboardButton(TEXTS["home"], callback_data="main_home")
    ])
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if edit_msg and update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await msg.reply_text(text, reply_markup=markup)

async def show_file_options(update, context, file_id):
    row = await get_file_by_id(file_id)
    if not row:
        await send_message(update, context, "file_not_found")
        return
    user_id = update.effective_user.id
    cnames = json.loads(row['custom_names'])
    title = cnames[0]
    size_str = human_readable_size(row['file_size'])
    type_emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
    upload_date = format_datetime(row['created_at'])
    views = row['view_count']
    downloads = row['download_count']
    text = TEXTS["file_details"].format(name=title, type=type_emoji, size=size_str, date=upload_date, views=views, downloads=downloads)
    keyboard = [
        [InlineKeyboardButton(TEXTS["show"], callback_data=f"showf_{file_id}")],
        [InlineKeyboardButton(TEXTS["rename"], callback_data=f"renamef_{file_id}"),
         InlineKeyboardButton(TEXTS["add_name"], callback_data=f"addnamef_{file_id}")],
        [InlineKeyboardButton(TEXTS["delete"], callback_data=f"delf_{file_id}")],
        [InlineKeyboardButton(TEXTS["back"], callback_data="myfiles_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await msg.reply_text(text, reply_markup=markup)

# ======================== Batch selection ========================
async def start_batch_selection(update, context):
    user_id = update.effective_user.id
    context.user_data['batch_selected'] = set()
    context.user_data['batch_mode'] = True
    await show_myfiles_batch(update, context)

async def show_myfiles_batch(update, context, page=0, folder_id=None):
    user_id = update.effective_user.id
    settings = await get_user_settings(user_id)
    page_size = settings.get('page_size', DEFAULT_PAGE_SIZE)
    offset = page * page_size
    files = await get_user_files(user_id, folder_id=folder_id, limit=page_size, offset=offset)
    total = await get_user_files_count(user_id, folder_id=folder_id)
    total_pages = max(1, -(-total // page_size))
    selected = context.user_data.get('batch_selected', set())
    keyboard = []
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        cb = f"batch_toggle_{row['id']}"
        check = "☑️" if row['id'] in selected else "⬜"
        keyboard.append([InlineKeyboardButton(f"{check} {emoji} {name}", callback_data=cb)])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"batch_page_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"batch_page_{page+1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton("🗑 " + TEXTS["delete_selected"], callback_data="batch_delete"),
        InlineKeyboardButton("🏷 " + TEXTS["add_tag_selected"], callback_data="batch_tag"),
        InlineKeyboardButton("📁 " + TEXTS["move_folder_selected"], callback_data="batch_move")
    ])
    keyboard.append([InlineKeyboardButton(TEXTS["done_selecting"], callback_data="batch_done")])
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(TEXTS["select_files_batch"], reply_markup=markup)
        else:
            await msg.reply_text(TEXTS["select_files_batch"], reply_markup=markup)

# ======================== Settings ========================
async def show_settings(update, context):
    user_id = update.effective_user.id
    settings = await get_user_settings(user_id)
    text = TEXTS["settings_title"] + "\n"
    text += TEXTS["page_size"].format(size=settings['page_size']) + "\n"
    text += TEXTS["sort_by"].format(sort=settings['sort_by']) + "\n"
    text += TEXTS["sort_order"].format(order=settings['sort_order']) + "\n"
    text += TEXTS["view_mode"].format(mode=settings['view_mode']) + "\n"
    keyboard = [
        [InlineKeyboardButton(TEXTS["set_page_size"], callback_data="set_page_size")],
        [InlineKeyboardButton(TEXTS["set_sort_by"], callback_data="set_sort_by")],
        [InlineKeyboardButton(TEXTS["set_sort_order"], callback_data="set_sort_order")],
        [InlineKeyboardButton(TEXTS["set_view_mode"], callback_data="set_view_mode")],
        [InlineKeyboardButton(TEXTS["back"], callback_data="main_home")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await msg.reply_text(text, reply_markup=markup)

# ======================== Admin Panel ========================
async def show_admin_panel(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id):
        return
    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("📂 Content", callback_data="admin_content")],
        [InlineKeyboardButton("📜 Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("📢 Announcements", callback_data="admin_announce")],
        [InlineKeyboardButton("⚙️ System Settings", callback_data="admin_syssettings")],
        [InlineKeyboardButton("💾 Backup", callback_data="admin_backup")],
        [InlineKeyboardButton("📊 Export CSV", callback_data="admin_export_csv")],
        [InlineKeyboardButton("🏠 Home", callback_data="main_home")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text("🛠 Admin Panel", reply_markup=markup)
        else:
            await msg.reply_text("🛠 Admin Panel", reply_markup=markup)

# Admin users
async def show_admin_users(update, context, page=0):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    pool = await get_pool()
    async with pool.acquire() as conn:
        offset = page * 10
        users = await conn.fetch("SELECT u.user_id, u.first_seen, u.last_seen, COUNT(f.id) as file_count, COALESCE(SUM(f.file_size), 0) as total_size, s.is_banned FROM users u LEFT JOIN files f ON u.user_id = f.user_id LEFT JOIN user_status s ON u.user_id = s.user_id GROUP BY u.user_id, s.is_banned ORDER BY u.user_id LIMIT 10 OFFSET $1", offset)
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_pages = max(1, -(-total // 10))
        keyboard = []
        for row in users:
            uid = row['user_id']
            banned = "🚫" if row['is_banned'] else ""
            keyboard.append([InlineKeyboardButton(f"{banned} {uid} ({row['file_count']} files)", callback_data=f"admin_user_{uid}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_users_page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_users_page_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("Back", callback_data="main_admin")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = get_msg(update)
        if msg:
            if update.callback_query:
                await update.callback_query.edit_message_text(f"👥 Users (Page {page+1}/{total_pages})", reply_markup=markup)
            else:
                await msg.reply_text(f"👥 Users (Page {page+1}/{total_pages})", reply_markup=markup)

async def show_user_details(update, context, uid):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    pool = await get_pool()
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        if not user:
            await send_message(update, context, "user_not_found")
            return
        status = await conn.fetchrow("SELECT is_banned FROM user_status WHERE user_id=$1", uid)
        banned = status['is_banned'] if status else False
        files = await conn.fetch("SELECT COUNT(*) FROM files WHERE user_id=$1", uid)
        size = await conn.fetchval("SELECT COALESCE(SUM(file_size), 0) FROM files WHERE user_id=$1", uid)
        text = TEXTS["user_details"].format(
            id=uid, files=files[0][0], size=human_readable_size(size),
            joined=format_datetime(user['first_seen']), last=format_datetime(user['last_seen']),
            banned="Yes" if banned else "No")
        keyboard = []
        if banned:
            keyboard.append([InlineKeyboardButton("Unblock", callback_data=f"admin_user_unblock_{uid}")])
        else:
            keyboard.append([InlineKeyboardButton("Block", callback_data=f"admin_user_block_{uid}")])
        keyboard.append([InlineKeyboardButton("Send Message", callback_data=f"admin_sendmsg_{uid}")])
        keyboard.append([InlineKeyboardButton("Delete All Files", callback_data=f"admin_user_deletefiles_{uid}")])
        keyboard.append([InlineKeyboardButton("Back", callback_data="admin_users")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = get_msg(update)
        if msg:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup)

# Admin content with filters (FIXED: store filters in context for pagination)
async def show_admin_content(update, context, page=0, filter_type=None, filter_user=None, filter_date=None):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    # Store filters in context for pagination
    context.user_data['admin_content_filters'] = {
        'filter_type': filter_type,
        'filter_user': filter_user,
        'filter_date': filter_date
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT * FROM files"
        params = []
        conditions = []
        if filter_type:
            conditions.append("file_type = $" + str(len(params)+1))
            params.append(filter_type)
        if filter_user:
            conditions.append("user_id = $" + str(len(params)+1))
            params.append(int(filter_user))
        if filter_date:
            conditions.append("DATE(created_at) = $" + str(len(params)+1))
            params.append(filter_date)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT 10 OFFSET $" + str(len(params)+1)
        params.append(page*10)
        files = await conn.fetch(query, *params)
        count_query = "SELECT COUNT(*) FROM files"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = await conn.fetchval(count_query, *params[:-1])
        total_pages = max(1, -(-total // 10))
        keyboard = []
        for row in files:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            name = json.loads(row['custom_names'])[0]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name} (user {row['user_id']})", callback_data=f"file_{row['id']}")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_content_page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_content_page_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("🔍 Filter", callback_data="admin_content_filter")])
        keyboard.append([InlineKeyboardButton("Back", callback_data="main_admin")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = get_msg(update)
        text = f"📂 All Files (Page {page+1}/{total_pages})"
        if filter_type:
            text += f" [Type: {filter_type}]"
        if filter_user:
            text += f" [User: {filter_user}]"
        if filter_date:
            text += f" [Date: {filter_date}]"
        if msg:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup)

# Admin logs with pagination (FIXED: added pagination)
async def show_admin_logs(update, context, page=0, filter_user=None, filter_action=None):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    # Store filters for pagination
    context.user_data['admin_logs_filters'] = {
        'filter_user': filter_user,
        'filter_action': filter_action
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = "SELECT * FROM logs"
        params = []
        conditions = []
        if filter_user:
            conditions.append("user_id = $" + str(len(params)+1))
            params.append(int(filter_user))
        if filter_action:
            conditions.append("action = $" + str(len(params)+1))
            params.append(filter_action)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT 20 OFFSET $" + str(len(params)+1)
        params.append(page*20)
        logs = await conn.fetch(query, *params)
        count_query = "SELECT COUNT(*) FROM logs"
        if conditions:
            count_query += " WHERE " + " AND ".join(conditions)
        total = await conn.fetchval(count_query, *params[:-1])
        total_pages = max(1, -(-total // 20))
        text = f"📜 Logs (Page {page+1}/{total_pages}):\n"
        for log in logs:
            text += f"{format_datetime(log['timestamp'])} - {log['user_id']} - {log['action']}"
            if log['details']:
                text += f" {log['details']}"
            text += "\n"
        keyboard = []
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"admin_logs_page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"admin_logs_page_{page+1}"))
        if nav:
            keyboard.append(nav)
        keyboard.append([InlineKeyboardButton("🔍 Filter", callback_data="admin_logs_filter")])
        keyboard.append([InlineKeyboardButton("Back", callback_data="main_admin")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = get_msg(update)
        if msg:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup)

# Admin announcements
async def show_admin_announce(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    keyboard = [
        [InlineKeyboardButton("Send Now", callback_data="admin_announce_now")],
        [InlineKeyboardButton("Schedule", callback_data="admin_announce_schedule")],
        [InlineKeyboardButton("History", callback_data="admin_announce_history")],
        [InlineKeyboardButton("Back", callback_data="main_admin")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text("📢 Announcements", reply_markup=markup)
        else:
            await msg.reply_text("📢 Announcements", reply_markup=markup)

# Admin system settings
async def show_admin_system_settings(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    max_size = await get_system_setting('max_user_size_mb', '100')
    public_sharing = await get_system_setting('public_sharing', 'false')
    welcome = await get_system_setting('welcome_message', 'Welcome!')
    text = f"⚙️ System Settings\nMax user size: {max_size} MB\nPublic sharing: {public_sharing}\nWelcome message: {welcome}"
    keyboard = [
        [InlineKeyboardButton("Set max user size", callback_data="admin_set_maxsize")],
        [InlineKeyboardButton("Toggle public sharing", callback_data="admin_toggle_public")],
        [InlineKeyboardButton("Set welcome message", callback_data="admin_set_welcome")],
        [InlineKeyboardButton("Back", callback_data="main_admin")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        else:
            await msg.reply_text(text, reply_markup=markup)

# Backup & Restore
async def admin_backup(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    pool = await get_pool()
    async with pool.acquire() as conn:
        tables = ['files', 'folders', 'users', 'user_settings', 'logs', 'announcements', 'user_status', 'system_settings']
        backup = {}
        for table in tables:
            rows = await conn.fetch(f"SELECT * FROM {table}")
            backup[table] = [dict(row) for row in rows]
        data = json.dumps(backup, default=str)
        await context.bot.send_document(user_id, io.BytesIO(data.encode()), filename=f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        await send_message(update, context, "backup_created")

async def admin_restore(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    message = update.message
    if message.document:
        file = await context.bot.get_file(message.document.file_id)
        data = await file.download_as_bytearray()
        try:
            backup = json.loads(data.decode('utf-8'))
        except:
            await message.reply_text("Invalid JSON file.")
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            for table in ['files', 'folders', 'user_settings', 'logs', 'announcements', 'user_status']:
                await conn.execute(f"DELETE FROM {table}")
            for table, rows in backup.items():
                if table == 'system_settings':
                    continue
                if rows:
                    for row in rows:
                        cols = list(row.keys())
                        placeholders = ','.join(['$' + str(i+1) for i in range(len(cols))])
                        query = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                        await conn.execute(query, *[row[c] for c in cols])
            await message.reply_text(TEXTS["restore_success"])
            await log_action(user_id, "restore_backup")
    else:
        await message.reply_text("Please send a JSON backup file.")

# Export CSV
async def admin_export_csv(update, context):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    keyboard = [
        [InlineKeyboardButton("Users Report", callback_data="admin_csv_users")],
        [InlineKeyboardButton("Files Report", callback_data="admin_csv_files")],
        [InlineKeyboardButton("Logs Report", callback_data="admin_csv_logs")],
        [InlineKeyboardButton("Back", callback_data="main_admin")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    msg = get_msg(update)
    if msg:
        if update.callback_query:
            await update.callback_query.edit_message_text(TEXTS["choose_report"], reply_markup=markup)
        else:
            await msg.reply_text(TEXTS["choose_report"], reply_markup=markup)

async def generate_csv_report(update, context, report_type):
    user_id = update.effective_user.id
    if not await is_admin(user_id): return
    pool = await get_pool()
    async with pool.acquire() as conn:
        if report_type == 'users':
            rows = await conn.fetch("SELECT u.user_id, u.first_seen, u.last_seen, COUNT(f.id) as file_count, COALESCE(SUM(f.file_size), 0) as total_size, s.is_banned FROM users u LEFT JOIN files f ON u.user_id = f.user_id LEFT JOIN user_status s ON u.user_id = s.user_id GROUP BY u.user_id, s.is_banned ORDER BY u.user_id")
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['user_id', 'first_seen', 'last_seen', 'file_count', 'total_size', 'is_banned'])
            for r in rows:
                writer.writerow([r['user_id'], r['first_seen'], r['last_seen'], r['file_count'], r['total_size'], r['is_banned']])
            output.seek(0)
            await context.bot.send_document(user_id, io.BytesIO(output.getvalue().encode()), filename="users_report.csv")
        elif report_type == 'files':
            rows = await conn.fetch("SELECT * FROM files ORDER BY id")
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['id', 'user_id', 'file_name', 'file_type', 'file_size', 'folder_id', 'created_at', 'view_count', 'download_count'])
            for r in rows:
                writer.writerow([r['id'], r['user_id'], r['file_name'], r['file_type'], r['file_size'], r['folder_id'], r['created_at'], r['view_count'], r['download_count']])
            output.seek(0)
            await context.bot.send_document(user_id, io.BytesIO(output.getvalue().encode()), filename="files_report.csv")
        elif report_type == 'logs':
            rows = await conn.fetch("SELECT * FROM logs ORDER BY id DESC LIMIT 1000")
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['id', 'user_id', 'action', 'timestamp', 'details'])
            for r in rows:
                writer.writerow([r['id'], r['user_id'], r['action'], r['timestamp'], r['details']])
            output.seek(0)
            await context.bot.send_document(user_id, io.BytesIO(output.getvalue().encode()), filename="logs_report.csv")
        await send_message(update, context, "csv_export")

# ======================== Handler: CallbackQuery ========================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("admin_") and not await is_admin(user_id):
        await query.answer("Unauthorized", show_alert=True)
        return

    # Main menu
    if data == "main_myfiles":
        await show_myfiles(update, context, edit_msg=True)
    elif data == "main_newfile":
        context.user_data['state'] = "awaiting_file"
        await send_message(update, context, "enter_file_or_done")
    elif data == "main_search":
        context.user_data['state'] = "awaiting_search"
        await send_message(update, context, "enter_search_term")
    elif data == "main_memory":
        size = await get_user_total_size(user_id)
        await send_message(update, context, "total_memory", size=human_readable_size(size))
        await show_main_menu(update, context)
    elif data == "main_settings":
        await show_settings(update, context)
    elif data == "main_admin":
        await show_admin_panel(update, context)
    elif data == "main_home":
        await show_main_menu(update, context)

    # File actions
    elif data.startswith("file_"):
        file_id = int(data[5:])
        await show_file_options(update, context, file_id)
    elif data.startswith("showf_"):
        file_id = int(data[6:])
        row = await get_file_by_id(file_id)
        if row:
            await increment_view(file_id)
            ftype = row['file_type']
            fid = row['file_id']
            if ftype == "photo": await context.bot.send_photo(user_id, fid)
            elif ftype == "video": await context.bot.send_video(user_id, fid)
            elif ftype == "audio": await context.bot.send_audio(user_id, fid)
            elif ftype == "voice": await context.bot.send_voice(user_id, fid)
            else: await context.bot.send_document(user_id, fid)
            await query.answer("File shown")
    elif data.startswith("delf_"):
        file_id = int(data[5:])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data=f"confirmdel_{file_id}"),
             InlineKeyboardButton("❌ No", callback_data="cancel_del")]
        ])
        await query.edit_message_text(TEXTS["confirm_delete"], reply_markup=keyboard)
    elif data.startswith("confirmdel_"):
        file_id = int(data[11:])
        await delete_file(file_id)
        await log_action(user_id, "delete_file", file_id)
        await query.edit_message_text(TEXTS["file_deleted"])
        await show_myfiles(update, context, edit_msg=True)
    elif data == "cancel_del":
        await query.edit_message_text(TEXTS["cancel"])
        await show_myfiles(update, context, edit_msg=True)
    elif data.startswith("renamef_"):
        file_id = int(data[8:])
        context.user_data['rename_id'] = file_id
        context.user_data['state'] = "awaiting_rename_text"
        await query.edit_message_text(TEXTS["enter_new_name"])
    elif data.startswith("addnamef_"):
        file_id = int(data[9:])
        context.user_data['addname_id'] = file_id
        context.user_data['state'] = "awaiting_addname_text"
        await query.edit_message_text(TEXTS["enter_additional_name"])
    elif data == "myfiles_back":
        await show_myfiles(update, context, edit_msg=True)

    # Pagination
    elif data.startswith("myfiles_page_"):
        parts = data.split('_')
        page = int(parts[2])
        folder = parts[3] if len(parts) > 3 and parts[3] else None
        folder_id = int(folder) if folder and folder.isdigit() else None
        await show_myfiles(update, context, page=page, folder_id=folder_id, edit_msg=True)

    # Batch
    elif data == "batch_start":
        await start_batch_selection(update, context)
    elif data.startswith("batch_toggle_"):
        file_id = int(data[13:])
        selected = context.user_data.get('batch_selected', set())
        if file_id in selected:
            selected.remove(file_id)
        else:
            selected.add(file_id)
        context.user_data['batch_selected'] = selected
        await show_myfiles_batch(update, context)
    elif data.startswith("batch_page_"):
        page = int(data[11:])
        await show_myfiles_batch(update, context, page=page)
    elif data == "batch_delete":
        selected = context.user_data.get('batch_selected', set())
        if not selected:
            await query.answer("No files selected")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data="batch_confirm_delete"),
             InlineKeyboardButton("❌ No", callback_data="batch_cancel")]
        ])
        await query.edit_message_text(TEXTS["delete_selected_confirm"].format(count=len(selected)), reply_markup=keyboard)
    elif data == "batch_confirm_delete":
        selected = context.user_data.get('batch_selected', set())
        for fid in selected:
            await delete_file(fid)
            await log_action(user_id, "delete_file", fid)
        context.user_data['batch_selected'] = set()
        context.user_data['batch_mode'] = False
        await query.edit_message_text(TEXTS["file_deleted"])
        await show_myfiles(update, context, edit_msg=True)
    elif data == "batch_cancel":
        await query.edit_message_text(TEXTS["cancel"])
        await show_myfiles_batch(update, context)
    elif data == "batch_tag":
        selected = context.user_data.get('batch_selected', set())
        if not selected:
            await query.answer("No files selected")
            return
        context.user_data['batch_tag_files'] = list(selected)
        context.user_data['state'] = "awaiting_batch_tag"
        await query.edit_message_text(TEXTS["enter_tag"])
    elif data == "batch_move":
        selected = context.user_data.get('batch_selected', set())
        if not selected:
            await query.answer("No files selected")
            return
        folders = await get_folders(user_id)
        keyboard = []
        for f in folders:
            keyboard.append([InlineKeyboardButton("📁 " + f['name'], callback_data=f"batch_move_to_{f['id']}")])
        keyboard.append([InlineKeyboardButton("📁 Root", callback_data="batch_move_to_0")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="batch_cancel")])
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(TEXTS["select_folder"], reply_markup=markup)
    elif data.startswith("batch_move_to_"):
        folder_id = int(data[14:]) if data[14:] != '0' else None
        selected = context.user_data.get('batch_selected', set())
        for fid in selected:
            await update_file_folder(fid, folder_id)
            await log_action(user_id, "move_file", fid, {"new_folder": folder_id})
        context.user_data['batch_selected'] = set()
        context.user_data['batch_mode'] = False
        await query.edit_message_text(TEXTS["move_success"].format(count=len(selected)))
        await show_myfiles(update, context, edit_msg=True)
    elif data == "batch_done":
        context.user_data['batch_selected'] = set()
        context.user_data['batch_mode'] = False
        await show_myfiles(update, context, edit_msg=True)

    # Folders
    elif data.startswith("folder_open_"):
        folder_id = int(data[12:])
        await show_myfiles(update, context, folder_id=folder_id, edit_msg=True)
    elif data.startswith("folder_parent_"):
        folder_id = int(data[14:])
        parent = await get_folder_by_id(folder_id)
        if parent and parent['parent_id']:
            await show_myfiles(update, context, folder_id=parent['parent_id'], edit_msg=True)
        else:
            await show_myfiles(update, context, edit_msg=True)
    elif data == "folder_create":
        context.user_data['state'] = "awaiting_folder_name"
        await query.edit_message_text(TEXTS["enter_folder_name"])
    elif data.startswith("folder_options_"):
        folder_id = int(data[15:])
        folder = await get_folder_by_id(folder_id)
        if not folder:
            await query.answer("Folder not found")
            return
        text = TEXTS["folder_options"] + "\n" + folder['name']
        keyboard = [
            [InlineKeyboardButton(TEXTS["rename_folder"], callback_data=f"folder_rename_{folder_id}")],
            [InlineKeyboardButton(TEXTS["delete_folder"], callback_data=f"folder_delete_{folder_id}")],
            [InlineKeyboardButton(TEXTS["back"], callback_data=f"folder_open_{folder_id}")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=markup)
    elif data.startswith("folder_rename_"):
        folder_id = int(data[14:])
        context.user_data['rename_folder_id'] = folder_id
        context.user_data['state'] = "awaiting_rename_folder"
        await query.edit_message_text(TEXTS["enter_new_folder_name"])
    elif data.startswith("folder_delete_"):
        folder_id = int(data[14:])
        folder = await get_folder_by_id(folder_id)
        if not folder:
            await query.answer("Folder not found")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data=f"folder_confirm_del_{folder_id}"),
             InlineKeyboardButton("❌ No", callback_data=f"folder_open_{folder_id}")]
        ])
        await query.edit_message_text(TEXTS["confirm_delete_folder"].format(name=folder['name']), reply_markup=keyboard)
    elif data.startswith("folder_confirm_del_"):
        folder_id = int(data[19:])
        await delete_folder(folder_id)
        await log_action(user_id, "delete_folder", None, {"folder_id": folder_id})
        await query.edit_message_text(TEXTS["folder_deleted"])
        await show_myfiles(update, context, edit_msg=True)

    # Settings
    elif data == "set_page_size":
        keyboard = []
        for size in PAGE_SIZES:
            keyboard.append([InlineKeyboardButton(str(size), callback_data=f"page_size_{size}")])
        keyboard.append([InlineKeyboardButton("Back", callback_data="main_settings")])
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(TEXTS["set_page_size"], reply_markup=markup)
    elif data.startswith("page_size_"):
        size = int(data[10:])
        await update_user_setting(user_id, "page_size", size)
        await query.answer("Page size updated")
        await show_settings(update, context)
    elif data == "set_sort_by":
        opts = ['id', 'file_name', 'file_size', 'created_at']
        keyboard = []
        for opt in opts:
            keyboard.append([InlineKeyboardButton(opt, callback_data=f"sort_by_{opt}")])
        keyboard.append([InlineKeyboardButton("Back", callback_data="main_settings")])
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(TEXTS["set_sort_by"], reply_markup=markup)
    elif data.startswith("sort_by_"):
        sort = data[8:]
        await update_user_setting(user_id, "sort_by", sort)
        await query.answer("Sort by updated")
        await show_settings(update, context)
    elif data == "set_sort_order":
        keyboard = [
            [InlineKeyboardButton("ASC", callback_data="sort_order_ASC")],
            [InlineKeyboardButton("DESC", callback_data="sort_order_DESC")],
            [InlineKeyboardButton("Back", callback_data="main_settings")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(TEXTS["set_sort_order"], reply_markup=markup)
    elif data.startswith("sort_order_"):
        order = data[11:]
        await update_user_setting(user_id, "sort_order", order)
        await query.answer("Sort order updated")
        await show_settings(update, context)
    elif data == "set_view_mode":
        keyboard = [
            [InlineKeyboardButton("List", callback_data="view_mode_list")],
            [InlineKeyboardButton("Gallery", callback_data="view_mode_gallery")],
            [InlineKeyboardButton("Back", callback_data="main_settings")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(TEXTS["set_view_mode"], reply_markup=markup)
    elif data.startswith("view_mode_"):
        mode = data[10:]
        await update_user_setting(user_id, "view_mode", mode)
        await query.answer("View mode updated")
        await show_settings(update, context)

    # Admin actions
    elif data == "admin_dashboard":
        stats = await get_admin_dashboard()
        text = TEXTS["admin_dashboard"].format(
            users=stats['users'], files=stats['files'],
            size=human_readable_size(stats['size']),
            photos=stats['photos'], videos=stats['videos'],
            audios=stats['audios'], docs=stats['docs'], voices=stats['voices'])
        keyboard = [[InlineKeyboardButton("Back", callback_data="main_admin")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_users":
        await show_admin_users(update, context)
    elif data.startswith("admin_users_page_"):
        page = int(data[17:])
        await show_admin_users(update, context, page=page)
    elif data.startswith("admin_user_"):
        uid = int(data[11:])
        await show_user_details(update, context, uid)
    elif data.startswith("admin_user_block_"):
        uid = int(data[17:])
        await set_user_banned(uid, True)
        await log_action(user_id, "block_user", None, {"target": uid})
        await query.answer("User blocked")
        await show_admin_users(update, context)
    elif data.startswith("admin_user_unblock_"):
        uid = int(data[19:])
        await set_user_banned(uid, False)
        await log_action(user_id, "unblock_user", None, {"target": uid})
        await query.answer("User unblocked")
        await show_admin_users(update, context)
    elif data.startswith("admin_user_deletefiles_"):
        uid = int(data[22:])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes", callback_data=f"admin_confirm_delfiles_{uid}"),
             InlineKeyboardButton("No", callback_data="admin_users")]
        ])
        await query.edit_message_text(TEXTS["confirm_delete_user_files"], reply_markup=keyboard)
    elif data.startswith("admin_confirm_delfiles_"):
        uid = int(data[23:])
        await delete_all_user_files(uid)
        await log_action(user_id, "delete_all_user_files", None, {"target": uid})
        await query.answer("Files deleted")
        await show_admin_users(update, context)
    elif data.startswith("admin_sendmsg_"):
        uid = int(data[14:])
        context.user_data['admin_msg_target'] = uid
        context.user_data['state'] = "awaiting_admin_message"
        await query.edit_message_text(TEXTS["enter_message_for_user"])
    elif data == "admin_content":
        await show_admin_content(update, context)
    elif data.startswith("admin_content_page_"):
        page = int(data[19:])
        # Retrieve stored filters
        filters = context.user_data.get('admin_content_filters', {})
        await show_admin_content(update, context, page=page,
                                 filter_type=filters.get('filter_type'),
                                 filter_user=filters.get('filter_user'),
                                 filter_date=filters.get('filter_date'))
    elif data == "admin_content_filter":
        keyboard = [
            [InlineKeyboardButton("By Type", callback_data="admin_content_filter_type")],
            [InlineKeyboardButton("By User", callback_data="admin_content_filter_user")],
            [InlineKeyboardButton("By Date", callback_data="admin_content_filter_date")],
            [InlineKeyboardButton("Clear", callback_data="admin_content_clear")],
            [InlineKeyboardButton("Back", callback_data="admin_content")]
        ]
        await query.edit_message_text("Select filter:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_content_filter_type":
        context.user_data['admin_filter_type'] = 'type'
        context.user_data['state'] = "awaiting_admin_filter"
        await query.edit_message_text("Send file type (photo, video, audio, document, voice):")
    elif data == "admin_content_filter_user":
        context.user_data['admin_filter_type'] = 'user'
        context.user_data['state'] = "awaiting_admin_filter"
        await query.edit_message_text("Send user ID:")
    elif data == "admin_content_filter_date":
        context.user_data['admin_filter_type'] = 'date'
        context.user_data['state'] = "awaiting_admin_filter"
        await query.edit_message_text("Send date (YYYY-MM-DD):")
    elif data == "admin_content_clear":
        # Clear filters and reset
        context.user_data['admin_content_filters'] = {}
        await show_admin_content(update, context)
    elif data == "admin_logs":
        await show_admin_logs(update, context)
    elif data.startswith("admin_logs_page_"):
        page = int(data[17:])
        filters = context.user_data.get('admin_logs_filters', {})
        await show_admin_logs(update, context, page=page,
                              filter_user=filters.get('filter_user'),
                              filter_action=filters.get('filter_action'))
    elif data == "admin_logs_filter":
        keyboard = [
            [InlineKeyboardButton("By User", callback_data="admin_logs_filter_user")],
            [InlineKeyboardButton("By Action", callback_data="admin_logs_filter_action")],
            [InlineKeyboardButton("Clear", callback_data="admin_logs_clear")],
            [InlineKeyboardButton("Back", callback_data="admin_logs")]
        ]
        await query.edit_message_text("Select filter:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_logs_filter_user":
        context.user_data['admin_filter_logs'] = 'user'
        context.user_data['state'] = "awaiting_admin_logs_filter"
        await query.edit_message_text("Send user ID:")
    elif data == "admin_logs_filter_action":
        context.user_data['admin_filter_logs'] = 'action'
        context.user_data['state'] = "awaiting_admin_logs_filter"
        await query.edit_message_text("Send action name:")
    elif data == "admin_logs_clear":
        context.user_data['admin_logs_filters'] = {}
        await show_admin_logs(update, context)
    elif data == "admin_announce":
        await show_admin_announce(update, context)
    elif data == "admin_announce_now":
        context.user_data['state'] = "awaiting_announcement"
        await query.edit_message_text(TEXTS["enter_announcement"])
    elif data == "admin_announce_schedule":
        # Fixed: now we first get the message, then ask for schedule
        context.user_data['state'] = "awaiting_announcement"
        await query.edit_message_text(TEXTS["enter_announcement"])
    elif data == "admin_announce_history":
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM announcements ORDER BY id DESC LIMIT 20")
            text = "📢 Announcement History:\n"
            for r in rows:
                text += f"ID {r['id']}: {r['message'][:30]}... Sent: {r['sent']} Scheduled: {format_datetime(r['scheduled_time'])}\n"
            keyboard = [[InlineKeyboardButton("Back", callback_data="admin_announce")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "admin_announce_sendnow":
        msg = context.user_data.get('announcement_msg')
        if not msg:
            await query.answer("No message")
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            users = await conn.fetch("SELECT user_id FROM users")
            count = 0
            for u in users:
                try:
                    await context.bot.send_message(u['user_id'], msg)
                    count += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
            await conn.execute("INSERT INTO announcements (message, sent, scheduled_time) VALUES ($1, TRUE, NOW())", msg)
            await log_action(user_id, "announcement_sent", None, {"count": count})
            await query.edit_message_text(f"Announcement sent to {count} users.")
            context.user_data.pop('announcement_msg', None)
            await show_admin_announce(update, context)
    elif data == "admin_announce_schedule_time":
        # Now we expect user has already provided the message, so we ask for time
        context.user_data['state'] = "awaiting_announce_schedule_time"
        await query.edit_message_text(TEXTS["enter_schedule_time"])
    elif data == "admin_syssettings":
        await show_admin_system_settings(update, context)
    elif data == "admin_set_maxsize":
        context.user_data['state'] = "awaiting_maxsize"
        await query.edit_message_text(TEXTS["set_max_user_size"])
    elif data == "admin_toggle_public":
        current = await get_system_setting('public_sharing', 'false')
        new = 'true' if current == 'false' else 'false'
        await set_system_setting('public_sharing', new)
        await query.answer(f"Public sharing set to {new}")
        await show_admin_system_settings(update, context)
    elif data == "admin_set_welcome":
        context.user_data['state'] = "awaiting_welcome"
        await query.edit_message_text(TEXTS["set_welcome_message"])
    elif data == "admin_backup":
        await admin_backup(update, context)
    elif data == "admin_export_csv":
        await admin_export_csv(update, context)
    elif data.startswith("admin_csv_"):
        report_type = data[10:]
        await generate_csv_report(update, context, report_type)

    else:
        await query.answer(TEXTS["unknown_action"], show_alert=True)

# ======================== Message Handler ========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""
    await record_user(user.id)

    if await is_user_banned(user.id):
        await message.reply_text("You are banned.")
        return

    state = context.user_data.get('state', 'main')

    if state == "awaiting_file":
        await handle_file(update, context)
        return
    elif state == "awaiting_rename_text":
        new_name = text.strip()
        rename_id = context.user_data.get('rename_id')
        if rename_id:
            row = await get_file_by_id(rename_id)
            if row:
                cnames = json.loads(row['custom_names'])
                if cnames:
                    cnames[0] = new_name
                else:
                    cnames = [new_name]
                await update_names(rename_id, cnames)
                await log_action(user.id, "rename_file", rename_id, {"new_name": new_name})
                await message.reply_text(TEXTS["name_updated"])
                context.user_data.pop('rename_id', None)
                await show_main_menu(update, context)
        return
    elif state == "awaiting_addname_text":
        new_name = text.strip()
        addname_id = context.user_data.get('addname_id')
        if addname_id:
            row = await get_file_by_id(addname_id)
            if row:
                cnames = json.loads(row['custom_names'])
                if new_name not in cnames:
                    cnames.append(new_name)
                    await update_names(addname_id, cnames)
                    await log_action(user.id, "add_name", addname_id, {"name": new_name})
                    await message.reply_text(TEXTS["name_added"])
                else:
                    await message.reply_text(TEXTS["name_exists"])
                context.user_data.pop('addname_id', None)
                await show_main_menu(update, context)
        return
    elif state == "awaiting_search":
        query = text.strip()
        if query:
            context.user_data['search_query'] = query
            await show_search_results(update, context, query)
        else:
            await message.reply_text(TEXTS["enter_search_term"])
        return
    elif state == "awaiting_folder_name":
        name = text.strip()
        if name:
            folder_id = context.user_data.get('current_folder_id')
            await create_folder(user.id, name, parent_id=folder_id)
            await log_action(user.id, "create_folder", None, {"name": name, "parent": folder_id})
            await message.reply_text(TEXTS["folder_created"])
            await show_myfiles(update, context)
        else:
            await message.reply_text("Invalid name")
        return
    elif state == "awaiting_rename_folder":
        new_name = text.strip()
        folder_id = context.user_data.get('rename_folder_id')
        if folder_id:
            await rename_folder(folder_id, new_name)
            await log_action(user.id, "rename_folder", None, {"folder_id": folder_id, "new_name": new_name})
            await message.reply_text(TEXTS["folder_renamed"])
            context.user_data.pop('rename_folder_id', None)
            await show_myfiles(update, context)
        return
    elif state == "awaiting_batch_tag":
        tag = text.strip()
        if tag:
            files = context.user_data.get('batch_tag_files', [])
            for fid in files:
                row = await get_file_by_id(fid)
                if row:
                    cnames = json.loads(row['custom_names'])
                    if tag not in cnames:
                        cnames.append(tag)
                        await update_names(fid, cnames)
                        await log_action(user.id, "add_tag", fid, {"tag": tag})
            await message.reply_text(TEXTS["tag_added"].format(count=len(files)))
            context.user_data.pop('batch_tag_files', None)
            context.user_data.pop('batch_selected', None)
            context.user_data.pop('batch_mode', None)
            await show_myfiles(update, context)
        else:
            await message.reply_text("Please enter a tag.")
        return
    elif state == "awaiting_admin_message":
        uid = context.user_data.get('admin_msg_target')
        if uid:
            try:
                await context.bot.send_message(uid, text)
                await message.reply_text(TEXTS["message_sent"])
                await log_action(user.id, "admin_send_message", None, {"target": uid})
            except Exception as e:
                await message.reply_text(f"Failed: {e}")
            context.user_data.pop('admin_msg_target', None)
            await show_admin_users(update, context)
        return
    elif state == "awaiting_announcement":
        context.user_data['announcement_msg'] = text
        keyboard = [
            [InlineKeyboardButton("Send Now", callback_data="admin_announce_sendnow")],
            [InlineKeyboardButton("Schedule", callback_data="admin_announce_schedule_time")]
        ]
        markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text("Send now or schedule?", reply_markup=markup)
        return
    elif state == "awaiting_announce_schedule_time":
        try:
            dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            msg = context.user_data.get('announcement_msg')
            if msg:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await conn.execute("INSERT INTO announcements (message, scheduled_time) VALUES ($1, $2)", msg, dt)
                await message.reply_text(TEXTS["announce_scheduled"])
                context.user_data.pop('announcement_msg', None)
                await show_admin_announce(update, context)
            else:
                await message.reply_text("No announcement message found.")
        except ValueError:
            await message.reply_text("Invalid format. Use YYYY-MM-DD HH:MM UTC")
        return
    elif state == "awaiting_maxsize":
        try:
            size = int(text)
            await set_system_setting('max_user_size_mb', str(size))
            await message.reply_text(f"Max user size set to {size} MB")
            await show_admin_system_settings(update, context)
        except ValueError:
            await message.reply_text("Please enter a valid number.")
        return
    elif state == "awaiting_welcome":
        await set_system_setting('welcome_message', text)
        await message.reply_text("Welcome message updated.")
        await show_admin_system_settings(update, context)
        return
    elif state == "awaiting_admin_filter":
        filter_type = context.user_data.get('admin_filter_type')
        if filter_type == 'type':
            await show_admin_content(update, context, filter_type=text.strip())
        elif filter_type == 'user':
            try:
                uid = int(text.strip())
                await show_admin_content(update, context, filter_user=uid)
            except:
                await message.reply_text("Invalid user ID")
        elif filter_type == 'date':
            await show_admin_content(update, context, filter_date=text.strip())
        context.user_data.pop('admin_filter_type', None)
        context.user_data['state'] = 'main'
        return
    elif state == "awaiting_admin_logs_filter":
        filter_type = context.user_data.get('admin_filter_logs')
        if filter_type == 'user':
            try:
                uid = int(text.strip())
                await show_admin_logs(update, context, filter_user=uid)
            except:
                await message.reply_text("Invalid user ID")
        elif filter_type == 'action':
            await show_admin_logs(update, context, filter_action=text.strip())
        context.user_data.pop('admin_filter_logs', None)
        context.user_data['state'] = 'main'
        return
    else:
        if message.document or message.photo or message.video or message.audio or message.voice:
            if context.user_data.get('state') == 'awaiting_file':
                await handle_file(update, context)
            else:
                await message.reply_text("Use the 'New File' button first.")
        else:
            await show_main_menu(update, context)

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    state = context.user_data.get('state')
    if state != "awaiting_file":
        await message.reply_text("Use the 'New File' button first.")
        return

    # Determine file type
    if message.photo:
        file_type = "photo"
        file = message.photo[-1]
        file_name = "photo.jpg"
    elif message.video:
        file_type = "video"
        file = message.video
        file_name = message.video.file_name or "video.mp4"
    elif message.audio:
        file_type = "audio"
        file = message.audio
        file_name = message.audio.file_name or "audio.mp3"
    elif message.voice:
        file_type = "voice"
        file = message.voice
        file_name = "voice.ogg"
    elif message.document:
        if message.document.mime_type and message.document.mime_type.startswith('audio/'):
            file_type = "audio"
            file = message.document
            file_name = message.document.file_name or "audio.mp3"
        else:
            file_type = "document"
            file = message.document
            file_name = message.document.file_name or "document"
    else:
        await message.reply_text("Unsupported file type.")
        return

    file_id = file.file_id
    file_size = getattr(file, 'file_size', 0) or 0

    max_size_mb = int(await get_system_setting('max_user_size_mb', '100'))
    total_size = await get_user_total_size(user.id)
    if total_size + file_size > max_size_mb * 1024 * 1024:
        await message.reply_text(f"Storage limit exceeded. Max {max_size_mb} MB.")
        return

    folder_id = context.user_data.get('current_folder_id')
    await add_file(
        user_id=user.id,
        file_id=file_id,
        file_name=file_name,
        custom_names=[file_name],
        file_type=file_type,
        file_size=file_size,
        folder_id=folder_id
    )
    await log_action(user.id, "upload_file", None, {"file_name": file_name, "type": file_type})
    await message.reply_text("✅ File saved.")
    context.user_data['state'] = 'main'
    await show_main_menu(update, context)

# ======================== Inline Query ========================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    if await is_user_banned(user_id):
        await update.inline_query.answer([])
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = f"%{query_text}%"
        rows = await conn.fetch(
            "SELECT * FROM files WHERE user_id=$1 AND (custom_names::text ILIKE $2 OR file_name ILIKE $2) LIMIT 50",
            user_id, q
        )
        results = []
        for row in rows:
            try:
                db_id = str(row['id'])
                file_id = row['file_id']
                ftype = row['file_type']
                cnames = json.loads(row.get('custom_names') or '[]')
                title = cnames[0] if cnames else row['file_name']
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

# ======================== Search ========================
async def show_search_results(update, context, query):
    user_id = update.effective_user.id
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = f"%{query}%"
        rows = await conn.fetch("SELECT * FROM files WHERE user_id=$1 AND (custom_names::text ILIKE $2 OR file_name ILIKE $2) ORDER BY id", user_id, q)
        if not rows:
            await send_message(update, context, "no_results")
            await show_main_menu(update, context)
            return
        text = TEXTS["search_results"].format(query=query) + "\n"
        for row in rows:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            name = json.loads(row['custom_names'])[0]
            text += f"{emoji} {name}\n"
        keyboard = []
        for row in rows:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            name = json.loads(row['custom_names'])[0]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"file_{row['id']}")])
        keyboard.append([InlineKeyboardButton(TEXTS["back"], callback_data="main_home")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = get_msg(update)
        if msg:
            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup)

# ======================== Start command ========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await record_user(user.id)
    if await is_user_banned(user.id):
        await update.message.reply_text("You are banned.")
        return
    settings = await get_user_settings(user.id)
    if not settings.get('tour_shown'):
        await update.message.reply_text(TEXTS["tour_welcome"])
        await update.message.reply_text(TEXTS["tour_step1"])
        await update.message.reply_text(TEXTS["tour_step2"])
        await update.message.reply_text(TEXTS["tour_step3"])
        await update.message.reply_text(TEXTS["tour_end"])
        await update_user_setting(user.id, 'tour_shown', True)
    context.user_data['state'] = 'main'
    await show_main_menu(update, context)

# ======================== Error handler ========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ======================== Scheduler for announcements ========================
async def send_scheduled_announcements():
    global ptb_app
    if ptb_app is None or ptb_app.bot is None:
        return
    pool = await get_pool()
    async with pool.acquire() as conn:
        now = datetime.now(ZoneInfo("UTC"))
        rows = await conn.fetch("SELECT * FROM announcements WHERE sent=FALSE AND scheduled_time <= $1", now)
        for row in rows:
            users = await conn.fetch("SELECT user_id FROM users")
            count = 0
            for u in users:
                try:
                    await ptb_app.bot.send_message(u['user_id'], row['message'])
                    count += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
            await conn.execute("UPDATE announcements SET sent=TRUE WHERE id=$1", row['id'])
            await log_action(ADMIN_ID, "auto_announcement_sent", None, {"announcement_id": row['id'], "count": count})

# ======================== Webhook ========================
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != WEBHOOK_SECRET:
        return {"status": "unauthorized"}, 401
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

# ======================== Main ========================
async def main():
    global ptb_app, scheduler
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).updater(None).build()
    ptb_app.add_error_handler(error_handler)
    await ptb_app.initialize()
    await ptb_app.start()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", start))
    ptb_app.add_handler(CommandHandler("cancel", start))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_file))

    await ptb_app.bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    logger.info(f"Webhook set to {WEBHOOK_URL} with secret token")

    scheduler.add_job(send_scheduled_announcements, 'interval', minutes=1, id='announcement_job')
    scheduler.start()

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
