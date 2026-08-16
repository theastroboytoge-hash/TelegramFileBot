import logging
import json
import os
import html
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters
import uvicorn
import asyncio
from datetime import datetime

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
if RENDER_EXTERNAL_URL:
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
else:
    WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-app.onrender.com')}{WEBHOOK_PATH}"

DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()
ptb_app = None
db_pool = None

FILE_TYPE_EMOJI = {
    "photo": "🖼️", "video": "📽️", "audio": "🎵", "voice": "🎙️", "document": "📄"
}

PAGE_SIZE = 10
PANEL_PAGE_SIZE = 20

# ---------- Database Functions ----------
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
                    file_size BIGINT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS file_size BIGINT NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()")
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_seen TIMESTAMP DEFAULT NOW()
                )
            ''')
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
    return db_pool

async def record_user(user):
    """Store/update the user's id along with their current username and first name."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, username, first_name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE
            SET username = EXCLUDED.username, first_name = EXCLUDED.first_name
            """,
            user.id, user.username, user.first_name
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

async def get_user_files_filtered(user_id, offset, limit, file_type=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params = []
        if user_id != ADMIN_ID:
            conditions.append("user_id = $1")
            params.append(user_id)
        if file_type:
            conditions.append(f"file_type = ${len(params)+1}")
            params.append(file_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM files WHERE {where} ORDER BY id LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
        params.extend([limit, offset])
        return await conn.fetch(query, *params)

async def get_user_files_count_filtered(user_id, file_type=None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        conditions = []
        params = []
        if user_id != ADMIN_ID:
            conditions.append("user_id = $1")
            params.append(user_id)
        if file_type:
            conditions.append(f"file_type = ${len(params)+1}")
            params.append(file_type)
        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT COUNT(*) FROM files WHERE {where}"
        row = await conn.fetchrow(query, *params)
        return row[0] if row else 0

async def search_files(user_id, query):
    pool = await get_pool()
    async with pool.acquire() as conn:
        q = f"%{query}%"
        if user_id == ADMIN_ID:
            return await conn.fetch("SELECT * FROM files WHERE custom_names::text ILIKE $1 OR file_name ILIKE $1", q)
        return await conn.fetch("SELECT * FROM files WHERE user_id=$1 AND (custom_names::text ILIKE $2 OR file_name ILIKE $2)", user_id, q)

async def get_file_by_id(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_db_id)

async def get_user_file_stats(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            rows = await conn.fetch("""
                SELECT file_type, COUNT(*) as count, SUM(file_size) as size 
                FROM files GROUP BY file_type
            """)
            total_files = await conn.fetchval("SELECT COUNT(*) FROM files")
            total_size = await conn.fetchval("SELECT SUM(file_size) FROM files")
        else:
            rows = await conn.fetch("""
                SELECT file_type, COUNT(*) as count, SUM(file_size) as size 
                FROM files WHERE user_id=$1 GROUP BY file_type
            """, user_id)
            total_files = await conn.fetchval("SELECT COUNT(*) FROM files WHERE user_id=$1", user_id)
            total_size = await conn.fetchval("SELECT SUM(file_size) FROM files WHERE user_id=$1", user_id)
        stats = {row['file_type']: {'count': row['count'], 'size': row['size']} for row in rows}
        return total_files, total_size, stats

async def delete_file(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id=$1", file_db_id)

async def delete_files_batch(file_ids):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id = ANY($1)", file_ids)

async def update_names(file_db_id, custom_names):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET custom_names=$1 WHERE id=$2", json.dumps(custom_names), file_db_id)

async def update_file_name(file_db_id, new_name):
    """Update the file_name field in database"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET file_name=$1 WHERE id=$2", new_name, file_db_id)

async def get_all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [row['user_id'] for row in rows]

async def get_total_users_count():
    """Return the total number of registered users."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM users")
        return count or 0

async def get_users_paginated(offset, limit):
    """Return a page of users (id, username, first_name) ordered by first_seen (most recent first)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, first_name FROM users ORDER BY first_seen DESC LIMIT $1 OFFSET $2",
            limit, offset
        )
        return rows

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

# ---------- UI Helpers ----------
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📁 My Files", callback_data="myfiles")],
        [InlineKeyboardButton("➕ New File", callback_data="newfile")],
        [InlineKeyboardButton("🔍 Search", callback_data="search")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_home_keyboard(back_callback="back_to_main", home_callback="home"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=back_callback),
         InlineKeyboardButton("🏠 Home", callback_data=home_callback)]
    ])

def get_cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="home")]])

def format_breadcrumb(breadcrumb):
    return " > ".join([f"{item['label']}" for item in breadcrumb])

# ---------- Error Handler ----------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ---------- Inline Query ----------
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    results = []
    try:
        files = await get_user_files(user_id)
        for row in files:
            db_id = str(row['id'])
            file_id = row['file_id']
            ftype = row['file_type']
            file_name = row.get('file_name', 'file')
            cnames = json.loads(row.get('custom_names') or '[]')
            if not cnames:
                cnames = [file_name]
            title = cnames[0]
            search_text = " ".join([n.lower() for n in cnames] + [file_name.lower()])
            if query_text and query_text not in search_text:
                continue
            if ftype == "photo":
                results.append(InlineQueryResultCachedPhoto(id=db_id, photo_file_id=file_id, title=title))
            elif ftype == "video":
                results.append(InlineQueryResultCachedVideo(id=db_id, video_file_id=file_id, title=title))
            elif ftype == "voice":
                results.append(InlineQueryResultCachedVoice(id=db_id, voice_file_id=file_id, title=title))
            elif ftype == "audio":
                results.append(InlineQueryResultCachedAudio(id=db_id, audio_file_id=file_id))
            else:
                results.append(InlineQueryResultCachedDocument(id=db_id, document_file_id=file_id, title=title))
        await update.inline_query.answer(results[:50], cache_time=5, is_personal=True)
    except Exception as e:
        logger.error(f"Critical inline query error: {e}", exc_info=True)
        await update.inline_query.answer([])

# ---------- File Handler ----------
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

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file upload with temporary name"""
    message = update.message
    user = update.effective_user
    state = context.user_data.get('state')

    if state != "awaiting_file":
        return

    # Check membership
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return

    is_audio, file_type, original_name = is_audio_file(message)

    # Detect file type
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
        file_name = original_name or "audio.mp3"
    elif message.document:
        file_type = "document"
        file = message.document
        file_name = message.document.file_name or "document.pdf"
    else:
        await message.reply_text("This file type is not supported.")
        return

    file_id = file.file_id
    file_size = getattr(file, 'file_size', 0) or 0

    # Use temporary name (will be updated after user provides custom name)
    temp_name = f"temp_{datetime.now().timestamp()}_{file_name}"
    
    try:
        # Save file with temporary name and empty custom_names
        await add_file(
            user_id=user.id,
            file_id=file_id,
            file_name=temp_name,
            custom_names=[],  # Start with empty custom names
            file_type=file_type,
            file_size=file_size
        )

        # Get the saved file ID
        pool = await get_pool()
        async with pool.acquire() as conn:
            last_file = await conn.fetchrow(
                "SELECT id FROM files WHERE user_id=$1 ORDER BY id DESC LIMIT 1", 
                user.id
            )
            if last_file:
                file_db_id = last_file['id']
                context.user_data['pending_name_file_id'] = file_db_id
                context.user_data['pending_file_emoji'] = FILE_TYPE_EMOJI.get(file_type, '📄')
                context.user_data['pending_original_name'] = file_name
                logger.info(f"New file uploaded with temp name. DB ID: {file_db_id}, Type: {file_type}, Original: {file_name}")
            else:
                await message.reply_text("❌ Error saving file. Please try again.")
                await enter_state(update, context, "main")
                return

        # Ask for custom name
        await enter_state(update, context, "awaiting_custom_name")
        
    except Exception as e:
        logger.error(f"Error in handle_file: {e}", exc_info=True)
        await message.reply_text("❌ Error saving file. Please try again.")
        await enter_state(update, context, "main")

# ---------- Core Navigation ----------
async def enter_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str, **kwargs):
    user_data = context.user_data
    user_data['state'] = state

    if state == "main":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}]
        text = "Welcome! Choose an option:"
        reply_markup = get_main_menu_keyboard()
    elif state == "awaiting_file":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "➕ New File", "callback": "newfile"}]
        text = "Send a file or press Cancel."
        reply_markup = get_cancel_keyboard()
    elif state == "awaiting_custom_name":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "➕ New File", "callback": "newfile"}]
        emoji = user_data.get('pending_file_emoji', '📄')
        original_name = user_data.get('pending_original_name', 'file')
        text = f"{emoji} File received!\n\nOriginal name: {original_name}\n\nPlease send a name for this file:"
        reply_markup = get_back_home_keyboard(back_callback="back_to_main")
    elif state == "myfiles":
        await show_myfiles_page(update, context, page=kwargs.get('page', 0))
        return
    elif state == "file_options":
        file_id = user_data.get('current_file_id')
        if not file_id:
            await enter_state(update, context, "myfiles")
            return
        row = await get_file_by_id(file_id)
        if not row:
            await answer_callback(update, "File not found.", True)
            await enter_state(update, context, "myfiles")
            return
        cnames = json.loads(row['custom_names'])
        title = cnames[0] if cnames else row['file_name']
        size_str = human_readable_size(row['file_size'])
        type_emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        breadcrumb = [
            {"label": "🏠 Main", "callback": "home"},
            {"label": "📁 My Files", "callback": "myfiles"},
            {"label": f"📄 {title[:15]}", "callback": "file_options"}
        ]
        text = f"📁 {title}\n📏 Size: {size_str}\n📌 Type: {type_emoji} {row['file_type']}"
        keyboard = [
            [InlineKeyboardButton("👁 Show", callback_data=f"showf_{file_id}")],
            [InlineKeyboardButton("✏️ Rename", callback_data=f"renamef_{file_id}"),
             InlineKeyboardButton("➕ Add Name", callback_data=f"addnamef_{file_id}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"delf_{file_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_myfiles"),
             InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
    elif state == "awaiting_rename_text":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "📁 My Files", "callback": "myfiles"}, {"label": "✏️ Rename", "callback": "rename"}]
        text = "Send the new name:"
        reply_markup = get_back_home_keyboard(back_callback="back_to_file_options")
    elif state == "awaiting_addname_text":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "📁 My Files", "callback": "myfiles"}, {"label": "➕ Add Name", "callback": "addname"}]
        text = "Send additional name:"
        reply_markup = get_back_home_keyboard(back_callback="back_to_file_options")
    elif state == "awaiting_search":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "🔍 Search", "callback": "search"}]
        text = "Send the search term:"
        reply_markup = get_back_home_keyboard(back_callback="back_to_main")
    elif state == "search_results":
        await show_search_results(update, context)
        return
    elif state == "awaiting_broadcast_message":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}]
        text = "Send the message to broadcast to all users:"
        reply_markup = get_back_home_keyboard(back_callback="back_to_main")
    else:
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}]
        text = "Unknown state. Go to main."
        reply_markup = get_main_menu_keyboard()

    await update_main_message(update, context, text, reply_markup, breadcrumb)

async def update_main_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text, reply_markup, breadcrumb=None):
    user_data = context.user_data
    chat_id = user_data.get('main_chat_id')
    message_id = user_data.get('main_message_id')
    if breadcrumb:
        header = format_breadcrumb(breadcrumb) + "\n\n"
    else:
        header = ""
    full_text = header + text

    if chat_id and message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=full_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logger.warning(f"Could not edit message: {e}")
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=full_text,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    user_data['main_chat_id'] = msg.chat_id
    user_data['main_message_id'] = msg.message_id

async def answer_callback(update: Update, text, show_alert=False):
    if update.callback_query:
        await update.callback_query.answer(text, show_alert=show_alert)

# ---------- My Files Page ----------
async def show_myfiles_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    user = update.effective_user
    user_data = context.user_data
    file_type_filter = user_data.get('filter_type', None)
    page_size = PAGE_SIZE
    selected = user_data.get('selected_files', set())
    selection_mode = user_data.get('selection_mode', False)

    offset = page * page_size
    files = await get_user_files_filtered(user.id, offset, page_size, file_type_filter)
    total = await get_user_files_count_filtered(user.id, file_type_filter)
    total_pages = max(1, (total + page_size - 1) // page_size)

    keyboard = []

    filter_buttons = []
    if file_type_filter:
        filter_buttons.append(InlineKeyboardButton(f"❌ Filter: {file_type_filter}", callback_data="clear_filter"))
    else:
        filter_buttons.append(InlineKeyboardButton("🔍 Filter", callback_data="filter_menu"))
    keyboard.append(filter_buttons)

    mode_text = "✅ Select Mode" if selection_mode else "☑️ Select Mode"
    keyboard.append([InlineKeyboardButton(mode_text, callback_data="toggle_selection_mode")])

    if not files:
        keyboard.append([InlineKeyboardButton("📭 No files", callback_data="noop")])
    else:
        for row in files:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            cnames = json.loads(row['custom_names'])
            name = cnames[0] if cnames else row['file_name']
            file_id = row['id']
            if selection_mode:
                checked = "✅" if file_id in selected else "⬜"
                label = f"{checked} {emoji} {name}"
                callback = f"toggle_select_{file_id}"
            else:
                label = f"{emoji} {name}"
                callback = f"listfile_{file_id}"
            keyboard.append([InlineKeyboardButton(label, callback_data=callback)])

    if selection_mode and selected:
        row = []
        if len(selected) > 0:
            row.append(InlineKeyboardButton(f"🗑 Delete ({len(selected)})", callback_data="batch_delete"))
            row.append(InlineKeyboardButton(f"🏷 Add Tag", callback_data="batch_addtag"))
        row.append(InlineKeyboardButton("🔄 Clear", callback_data="clear_selection"))
        keyboard.append(row)

    if total_pages > 1:
        nav_buttons = []
        start_page = max(0, page - 3)
        end_page = min(total_pages, page + 4)
        if start_page > 0:
            nav_buttons.append(InlineKeyboardButton("1", callback_data=f"myfiles_page_0"))
            if start_page > 1:
                nav_buttons.append(InlineKeyboardButton("...", callback_data="noop"))
        for p in range(start_page, end_page):
            label = f"•{p+1}•" if p == page else str(p+1)
            nav_buttons.append(InlineKeyboardButton(label, callback_data=f"myfiles_page_{p}"))
        if end_page < total_pages:
            if end_page < total_pages - 1:
                nav_buttons.append(InlineKeyboardButton("...", callback_data="noop"))
            nav_buttons.append(InlineKeyboardButton(str(total_pages), callback_data=f"myfiles_page_{total_pages-1}"))
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("🔄 View", callback_data="toggle_view")
    ])

    keyboard.append([
        InlineKeyboardButton("🔙 Back", callback_data="back_to_main"),
        InlineKeyboardButton("🏠 Home", callback_data="home")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    breadcrumb = [
        {"label": "🏠 Main", "callback": "home"},
        {"label": "📁 My Files", "callback": "myfiles"}
    ]
    if file_type_filter:
        breadcrumb.append({"label": f"🔍 {file_type_filter}", "callback": "myfiles"})

    text = f"📂 Your files (Page {page+1}/{total_pages})"
    if selection_mode:
        text += f"\n🔘 Selection mode: {len(selected)} selected"

    await update_main_message(update, context, text, reply_markup, breadcrumb)

    user_data['myfiles_page'] = page
    user_data['state'] = "myfiles"

# ---------- Search Results ----------
async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = context.user_data
    query = user_data.get('search_query', '').strip()
    if not query:
        await enter_state(update, context, "myfiles")
        return
    results = await search_files(user.id, query)
    if not results:
        text = f"No files found for '{query}'."
        reply_markup = get_back_home_keyboard(back_callback="back_to_main")
        await update_main_message(update, context, text, reply_markup, 
                                 [{"label": "🏠 Main", "callback": "home"}, {"label": "🔍 Search", "callback": "search"}])
        return
    keyboard = []
    for row in results:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        cnames = json.loads(row['custom_names'])
        name = cnames[0] if cnames else row['file_name']
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"Search results for '{query}':"
    await update_main_message(update, context, text, reply_markup,
                             [{"label": "🏠 Main", "callback": "home"}, {"label": "🔍 Search", "callback": "search"}])

# ---------- Admin Panel (Users) ----------
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    """Render a paginated panel showing total user count and their IDs. Admin only."""
    total_users = await get_total_users_count()
    total_pages = max(1, (total_users + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    offset = page * PANEL_PAGE_SIZE

    users_rows = await get_users_paginated(offset, PANEL_PAGE_SIZE)

    lines = [f"👥 <b>Total users:</b> {total_users}", ""]
    if users_rows:
        start_num = offset + 1
        for i, row in enumerate(users_rows):
            uid = row['user_id']
            username = row['username']
            first_name = row['first_name']
            if username:
                label = html.escape(f"@{username}")
            elif first_name:
                label = html.escape(first_name)
            else:
                label = "No username"
            lines.append(f"{start_num + i}. {label} — <code>{uid}</code>")
    else:
        lines.append("No users found.")

    text = "\n".join(lines)

    keyboard = []
    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"panel_page_{page-1}"))
        nav_buttons.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"panel_page_{page+1}"))
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data=f"panel_page_{page}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    chat_id = update.effective_chat.id
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode="HTML"
            )
            return
        except Exception as e:
            logger.warning(f"Could not edit panel message: {e}")
    try:
        await context.bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to send admin panel: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "❌ Error displaying the panel. Please check the logs.")

# ---------- Callback Handlers ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    user_data = context.user_data

    await query.answer()

    if data == "home":
        user_data.clear()
        await enter_state(update, context, "main")
    elif data == "back":
        await enter_state(update, context, "main")
    elif data == "back_to_main":
        await enter_state(update, context, "main")
    elif data == "back_to_myfiles":
        await enter_state(update, context, "myfiles", page=user_data.get('myfiles_page', 0))
    elif data == "back_to_file_options":
        await enter_state(update, context, "file_options")
    elif data == "back_to_search":
        await enter_state(update, context, "search_results")

    elif data == "newfile":
        await enter_state(update, context, "awaiting_file")
    elif data == "myfiles":
        await enter_state(update, context, "myfiles", page=0)
    elif data == "search":
        await enter_state(update, context, "awaiting_search")

    elif data.startswith("myfiles_page_"):
        page = int(data.split("_")[-1])
        await show_myfiles_page(update, context, page)

    elif data.startswith("panel_page_"):
        if user.id != ADMIN_ID:
            await answer_callback(update, "⛔ Not authorized.", True)
            return
        page = int(data.split("_")[-1])
        await show_admin_panel(update, context, page)

    elif data.startswith("listfile_"):
        file_id = int(data[9:])
        user_data['current_file_id'] = file_id
        await enter_state(update, context, "file_options")

    elif data.startswith("showf_"):
        file_id = int(data[6:])
        row = await get_file_by_id(file_id)
        if row:
            ftype = row['file_type']
            fid = row['file_id']
            if ftype == "photo":
                await context.bot.send_photo(user.id, fid)
            elif ftype == "video":
                await context.bot.send_video(user.id, fid)
            elif ftype == "audio":
                await context.bot.send_audio(user.id, fid)
            elif ftype == "voice":
                await context.bot.send_voice(user.id, fid)
            else:
                await context.bot.send_document(user.id, fid)
            await answer_callback(update, "File sent.")

    elif data.startswith("delf_"):
        file_id = int(data[5:])
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes", callback_data=f"confirmdel_{file_id}"),
             InlineKeyboardButton("❌ No", callback_data="cancel_del")]
        ])
        await query.edit_message_text("Are you sure you want to delete this file?", reply_markup=keyboard)
    elif data.startswith("confirmdel_"):
        file_id = int(data[11:])
        await delete_file(file_id)
        await answer_callback(update, "File deleted.", True)
        await enter_state(update, context, "myfiles", page=user_data.get('myfiles_page', 0))
    elif data == "cancel_del":
        await enter_state(update, context, "file_options")

    elif data.startswith("renamef_"):
        user_data['rename_id'] = int(data[8:])
        await enter_state(update, context, "awaiting_rename_text")
    elif data.startswith("addnamef_"):
        user_data['addname_id'] = int(data[9:])
        await enter_state(update, context, "awaiting_addname_text")

    elif data == "toggle_selection_mode":
        user_data['selection_mode'] = not user_data.get('selection_mode', False)
        if not user_data['selection_mode']:
            user_data['selected_files'] = set()
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))
    elif data.startswith("toggle_select_"):
        file_id = int(data[14:])
        selected = user_data.get('selected_files', set())
        if file_id in selected:
            selected.remove(file_id)
        else:
            selected.add(file_id)
        user_data['selected_files'] = selected
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))
    elif data == "clear_selection":
        user_data['selected_files'] = set()
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))
    elif data == "batch_delete":
        selected = user_data.get('selected_files', set())
        if selected:
            await delete_files_batch(list(selected))
            user_data['selected_files'] = set()
            await answer_callback(update, f"Deleted {len(selected)} files.", True)
            await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))
        else:
            await answer_callback(update, "No files selected.")
    elif data == "batch_addtag":
        selected = user_data.get('selected_files', set())
        if selected:
            user_data['batch_tag_files'] = list(selected)
            user_data['state'] = "awaiting_batch_tag"
            await update_main_message(update, context, "Send the tag name to add to all selected files:",
                                      get_back_home_keyboard(back_callback="back_to_myfiles"),
                                      [{"label": "🏠 Main", "callback": "home"}, {"label": "📁 My Files", "callback": "myfiles"}])
        else:
            await answer_callback(update, "No files selected.")

    elif data == "filter_menu":
        keyboard = [
            [InlineKeyboardButton("🖼 Photo", callback_data="filter_type_photo"),
             InlineKeyboardButton("📽 Video", callback_data="filter_type_video")],
            [InlineKeyboardButton("🎵 Audio", callback_data="filter_type_audio"),
             InlineKeyboardButton("🎙 Voice", callback_data="filter_type_voice")],
            [InlineKeyboardButton("📄 Document", callback_data="filter_type_document")],
            [InlineKeyboardButton("❌ Clear Filter", callback_data="clear_filters")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_myfiles")]
        ]
        await query.edit_message_text("Select filter:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("filter_type_"):
        ftype = data[12:]
        user_data['filter_type'] = ftype if ftype != "none" else None
        await show_myfiles_page(update, context, page=0)
    elif data == "clear_filters":
        user_data['filter_type'] = None
        await show_myfiles_page(update, context, page=0)

    elif data == "toggle_view":
        current = user_data.get('view_mode', 'list')
        new_mode = 'gallery' if current == 'list' else 'list'
        user_data['view_mode'] = new_mode
        await answer_callback(update, f"View mode: {new_mode}")
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))

    elif data == "noop":
        pass

    else:
        logger.warning(f"Unknown callback: {data}")

# ---------- Message Handler ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""

    # Record user
    await record_user(user)

    # Check membership for non-command messages
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return

    # Handle file uploads
    if message.photo or message.video or message.audio or message.voice or message.document:
        await handle_file(update, context)
        return

    state = context.user_data.get('state', 'main')
    user_data = context.user_data

    # Handle custom name after file upload
    if state == "awaiting_custom_name":
        custom_name = text.strip()
        
        if not custom_name:
            await message.reply_text("❌ Please send a valid name.")
            return
        
        file_db_id = user_data.get('pending_name_file_id')
        file_emoji = user_data.get('pending_file_emoji', '📄')
        
        if not file_db_id:
            await message.reply_text("❌ No pending file found. Please upload a file first.")
            await enter_state(update, context, "main")
            return
        
        try:
            # Verify file exists
            row = await get_file_by_id(file_db_id)
            if not row:
                await message.reply_text("❌ File not found in database. Please try again.")
                user_data.pop('pending_name_file_id', None)
                user_data.pop('pending_file_emoji', None)
                await enter_state(update, context, "main")
                return
            
            # Update custom_names with the new name
            await update_names(file_db_id, [custom_name])
            
            # Also update the file_name field with the custom name
            await update_file_name(file_db_id, custom_name)
            
            logger.info(f"✅ Successfully saved custom name '{custom_name}' for file ID {file_db_id}")
            
            # Show success message
            await message.reply_text(
                f"✅ {file_emoji} File saved successfully with name: **{custom_name}**",
                parse_mode="Markdown"
            )
            
            # Clear pending data
            user_data.pop('pending_name_file_id', None)
            user_data.pop('pending_file_emoji', None)
            user_data.pop('pending_original_name', None)
            
            # Go back to main menu
            await enter_state(update, context, "main")
            
        except Exception as e:
            logger.error(f"Error saving custom name: {e}", exc_info=True)
            await message.reply_text(f"❌ Error saving the name: {str(e)}. Please try again.")
        
        return

    # Handle rename text
    if state == "awaiting_rename_text":
        new_name = text.strip()
        rename_id = user_data.get('rename_id')
        
        if not rename_id:
            await message.reply_text("❌ No file selected for rename.")
            await enter_state(update, context, "main")
            return
        
        try:
            row = await get_file_by_id(rename_id)
            if not row:
                await message.reply_text("❌ File not found.")
                await enter_state(update, context, "myfiles")
                return
            
            cnames = json.loads(row['custom_names'])
            if cnames:
                cnames[0] = new_name
            else:
                cnames = [new_name]
            
            await update_names(rename_id, cnames)
            await update_file_name(rename_id, new_name)  # Also update file_name
            
            await answer_callback(update, "✅ Name updated successfully.")
            user_data.pop('rename_id', None)
            await enter_state(update, context, "file_options")
            
        except Exception as e:
            logger.error(f"Error in rename: {e}")
            await message.reply_text(f"❌ Error renaming: {str(e)}")
        
        return

    # Handle add name text
    if state == "awaiting_addname_text":
        new_name = text.strip()
        addname_id = user_data.get('addname_id')
        
        if not addname_id:
            await message.reply_text("❌ No file selected.")
            await enter_state(update, context, "main")
            return
        
        try:
            row = await get_file_by_id(addname_id)
            if not row:
                await message.reply_text("❌ File not found.")
                await enter_state(update, context, "myfiles")
                return
            
            cnames = json.loads(row['custom_names'])
            if new_name not in cnames:
                cnames.append(new_name)
                await update_names(addname_id, cnames)
                await answer_callback(update, f"✅ Name '{new_name}' added.")
            else:
                await message.reply_text("❌ Name already exists.")
            
            user_data.pop('addname_id', None)
            await enter_state(update, context, "file_options")
            
        except Exception as e:
            logger.error(f"Error adding name: {e}")
            await message.reply_text(f"❌ Error adding name: {str(e)}")
        
        return

    # Handle search
    if state == "awaiting_search":
        query = text.strip()
        if query:
            user_data['search_query'] = query
            await enter_state(update, context, "search_results")
        else:
            await message.reply_text("❌ Please send a non-empty search term.")
        return

    # Handle broadcast (admin only)
    if state == "awaiting_broadcast_message":
        if user.id != ADMIN_ID:
            await message.reply_text("⛔ You are not authorized to broadcast.")
            await enter_state(update, context, "main")
            return
        
        broadcast_text = text
        try:
            user_ids = await get_all_user_ids()
            success_count = 0
            fail_count = 0
            
            for uid in user_ids:
                try:
                    await context.bot.send_message(uid, broadcast_text)
                    success_count += 1
                    await asyncio.sleep(0.05)  # Rate limiting
                except Exception as e:
                    logger.warning(f"Failed to send broadcast to {uid}: {e}")
                    fail_count += 1
            
            await message.reply_text(
                f"📢 Broadcast sent!\n\n"
                f"✅ Sent to: {success_count} users\n"
                f"❌ Failed: {fail_count} users"
            )
            await enter_state(update, context, "main")
            
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
            await message.reply_text(f"❌ Error sending broadcast: {str(e)}")
        
        return

    # Handle batch tag
    if state == "awaiting_batch_tag":
        tag = text.strip()
        file_ids = user_data.get('batch_tag_files', [])
        
        if not tag:
            await message.reply_text("❌ Please send a non-empty tag.")
            return
        
        if not file_ids:
            await message.reply_text("❌ No files selected for tagging.")
            await enter_state(update, context, "myfiles")
            return
        
        try:
            success_count = 0
            for fid in file_ids:
                row = await get_file_by_id(fid)
                if row:
                    cnames = json.loads(row['custom_names'])
                    if tag not in cnames:
                        cnames.append(tag)
                        await update_names(fid, cnames)
                        success_count += 1
            
            user_data.pop('batch_tag_files', None)
            await answer_callback(update, f"✅ Tag '{tag}' added to {success_count} files.", True)
            await enter_state(update, context, "myfiles", page=user_data.get('myfiles_page', 0))
            
        except Exception as e:
            logger.error(f"Error adding batch tag: {e}")
            await message.reply_text(f"❌ Error adding tag: {str(e)}")
        
        return

    # Default: Go to main menu
    await enter_state(update, context, "main")

# ---------- Start & Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await record_user(user)
    
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return

    if 'first_start' not in context.user_data:
        context.user_data['first_start'] = True
        welcome_text = (
            "👋 Hey there! Welcome aboard.\n\n"
            "Think of me as your personal file vault, right here inside Telegram — "
            "a place to upload, name, and instantly find whatever you need, whenever "
            "you need it. No more scrolling through old chats hunting for that one file.\n\n"
            "Here's what I can do for you:\n\n"
            "📤 *Save anything* — photos, videos, voice notes, audio, and documents. "
            "Give each one a name that actually makes sense to you.\n\n"
            "📁 *Stay organized* — browse everything in one place, filter by type, "
            "rename things, or tag multiple files at once.\n\n"
            "🔍 *Find it fast* — search by name and get exactly what you're looking "
            "for, no digging required.\n\n"
            "⚡ *Send from anywhere* — type @botusername in any chat and your files "
            "pop up instantly, ready to send. No need to come back here first.\n\n"
            "📊 *Keep track* — check your storage stats anytime to see what you've "
            "saved and how much space it's using.\n\n"
            "Ready to get started? Tap '➕ New File' below and upload your first one!"
        )
        await update_main_message(update, context, welcome_text, get_main_menu_keyboard())
        context.user_data['state'] = "main"
    else:
        await enter_state(update, context, "main")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user)
    help_text = (
        "📚 *Help & Commands*\n\n"
        "Here's everything you can do:\n\n"
        "*Main Menu*\n"
        "• 📁 My Files — browse, filter, rename, tag, or delete your saved files\n"
        "• ➕ New File — upload a photo, video, voice note, audio, or document\n"
        "• 🔍 Search — quickly find a file by name\n\n"
        "*Inline Search* ⚡\n"
        "Type @botusername followed by a keyword in any chat (even ones I'm not "
        "part of) to instantly search and send your saved files — no need to "
        "switch back to this chat.\n\n"
        "*Managing Files*\n"
        "Tap any file to see its options: show it, rename it, add an extra "
        "name/tag, or delete it. In My Files, switch on Select Mode to manage "
        "several files at once.\n\n"
        "*Commands*\n"
        "/start — Show the main menu\n"
        "/help — Show this help message\n"
        "/export — Get your storage summary plus a plain-text list of all your files (name, type, size) — tap a name to copy it\n"
        "/cancel — Cancel whatever you're currently doing and go back to the "
        "main menu\n\n"
        "Got stuck somewhere? Just tap 🏠 Home to reset and start fresh."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await enter_state(update, context, "main")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    await enter_state(update, context, "awaiting_broadcast_message")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    user_ids = await get_all_user_ids()
    await update.message.reply_text(f"👥 Total users: {len(user_ids)}")

async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only panel showing total user count and a paginated list of user IDs."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ You are not authorized to use this command.")
        return
    try:
        await show_admin_panel(update, context, page=0)
    except Exception as e:
        logger.error(f"Panel command error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error opening the panel: {str(e)}")

# Telegram's hard limit per message is 4096 characters; keep a safety margin
EXPORT_CHUNK_LIMIT = 3500

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the user a plain list of all their files (name, type, size).
    Each file name is wrapped in <code> so tapping it copies the name.
    Splits into multiple messages if the list exceeds Telegram's message limit."""
    user = update.effective_user
    await record_user(user)

    try:
        rows = await get_user_files(user.id)
    except Exception as e:
        logger.error(f"Export command error: {e}", exc_info=True)
        await update.message.reply_text("❌ Error retrieving your files. Please try again.")
        return

    if not rows:
        await update.message.reply_text("📭 You don't have any files saved yet.")
        return

    try:
        total_files, total_size, stats = await get_user_file_stats(user.id)
    except Exception as e:
        logger.error(f"Export stats error: {e}", exc_info=True)
        total_files, total_size, stats = len(rows), sum(r['file_size'] or 0 for r in rows), {}

    stats_lines = [
        "📊 <b>Storage Summary</b>",
        f"Total Files: {total_files}",
        f"Total Size: {html.escape(human_readable_size(total_size))}"
    ]
    for ftype, data in stats.items():
        emoji = FILE_TYPE_EMOJI.get(ftype, "📄")
        stats_lines.append(
            f"{emoji} {html.escape(ftype.capitalize())}: {data['count']} files "
            f"({html.escape(human_readable_size(data['size'] or 0))})"
        )

    lines = []
    for i, row in enumerate(rows, 1):
        cnames = json.loads(row['custom_names'])
        name = cnames[0] if cnames else row['file_name']
        safe_name = html.escape(name)
        ftype = row['file_type']
        emoji = FILE_TYPE_EMOJI.get(ftype, "📄")
        size_str = human_readable_size(row['file_size'])
        lines.append(f"{i}. <code>{safe_name}</code> — {emoji} {ftype} — {size_str}")

    header = "\n".join(stats_lines) + "\n\n📄 <b>Your files</b>\n\n"

    # Split lines into chunks that stay under Telegram's per-message limit
    chunks = []
    current = header
    for line in lines:
        # +1 accounts for the newline that will join this line to current
        if len(current) + len(line) + 1 > EXPORT_CHUNK_LIMIT:
            chunks.append(current)
            current = line
        else:
            current = current + line if current == header else current + "\n" + line
    if current:
        chunks.append(current)

    total_parts = len(chunks)
    try:
        for idx, chunk in enumerate(chunks, 1):
            text = chunk
            if total_parts > 1:
                text += f"\n\n(Part {idx}/{total_parts})"
            await update.message.reply_text(text, parse_mode="HTML")
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.error(f"Export send error: {e}", exc_info=True)
        await update.message.reply_text("❌ Error sending the file list. Please try again.")

# ---------- Webhook & FastAPI ----------
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "Bot is running", "webhook": WEBHOOK_URL}

# ---------- Main ----------
async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).updater(None).build()
    ptb_app.add_error_handler(error_handler)

    await ptb_app.initialize()
    await ptb_app.start()

    # Add command handlers
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
    ptb_app.add_handler(CommandHandler("users", users_command))
    ptb_app.add_handler(CommandHandler("panel", panel_command))
    ptb_app.add_handler(CommandHandler("export", export_command))
    ptb_app.add_handler(CommandHandler("export", export_command))

    # Add other handlers
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, 
        handle_file
    ))
    ptb_app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_message
    ))

    # Set webhook
    webhook_set = await ptb_app.bot.set_webhook(WEBHOOK_URL)
    if webhook_set:
        logger.info(f"✅ Webhook successfully set to {WEBHOOK_URL}")
    else:
        logger.error(f"❌ Failed to set webhook to {WEBHOOK_URL}")

    # Run server
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
