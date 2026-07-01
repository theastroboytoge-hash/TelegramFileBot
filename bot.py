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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"

# اصلاح آدرس Webhook با استفاده از متغیر استاندارد Render
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

PAGE_SIZE_OPTIONS = [5, 10, 20]
DEFAULT_PAGE_SIZE = 5

# ---------- Database ----------
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
    return db_pool

async def record_user(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)

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

async def get_user_files_filtered(user_id, offset, limit, file_type=None, date_from=None, date_to=None):
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
        if date_from:
            conditions.append(f"created_at >= ${len(params)+1}")
            params.append(date_from)
        if date_to:
            conditions.append(f"created_at <= ${len(params)+1}")
            params.append(date_to)
        where = " AND ".join(conditions) if conditions else "1=1"
        query = f"SELECT * FROM files WHERE {where} ORDER BY id LIMIT ${len(params)+1} OFFSET ${len(params)+2}"
        params.extend([limit, offset])
        return await conn.fetch(query, *params)

async def get_user_files_count_filtered(user_id, file_type=None, date_from=None, date_to=None):
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
        if date_from:
            conditions.append(f"created_at >= ${len(params)+1}")
            params.append(date_from)
        if date_to:
            conditions.append(f"created_at <= ${len(params)+1}")
            params.append(date_to)
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

async def delete_files_batch(file_ids):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id = ANY($1)", file_ids)

async def update_names(file_db_id, custom_names):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET custom_names=$1 WHERE id=$2", json.dumps(custom_names), file_db_id)

async def get_all_user_ids():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [row['user_id'] for row in rows]

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

def get_msg(update: Update):
    if update.message:
        return update.message
    elif update.callback_query:
        return update.callback_query.message
    return None

# ---------- UI Helpers ----------
def get_main_menu_keyboard():
    # گزینه Settings حذف شد
    keyboard = [
        [InlineKeyboardButton("📁 My Files", callback_data="myfiles")],
        [InlineKeyboardButton("➕ New File", callback_data="newfile")],
        [InlineKeyboardButton("🔍 Search", callback_data="search")],
        [InlineKeyboardButton("📊 Memory", callback_data="memory")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_home_keyboard(back_callback="back", home_callback="home"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=back_callback),
         InlineKeyboardButton("🏠 Home", callback_data=home_callback)]
    ])

def get_home_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])

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
    message = update.message
    user = update.effective_user
    state = context.user_data.get('state')

    if state != "awaiting_file":
        return

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

    await add_file(
        user_id=user.id,
        file_id=file_id,
        file_name=file_name,
        custom_names=[file_name],
        file_type=file_type,
        file_size=file_size
    )

    await message.reply_text(f"✅ {FILE_TYPE_EMOJI.get(file_type, '📄')} **{file_name}** saved.", parse_mode="Markdown")
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
    elif state == "myfiles":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "📁 My Files", "callback": "myfiles"}]
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
        title = cnames[0]
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
        reply_markup = get_back_home_keyboard()
    elif state == "search_results":
        await show_search_results(update, context)
        return
    elif state == "settings":  # این بخش دیگر قابل دسترس نیست ولی برای جلوگیری از خطا نگه داشته شده
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}, {"label": "⚙️ Settings", "callback": "settings"}]
        page_size = user_data.get('page_size', DEFAULT_PAGE_SIZE)
        view_mode = user_data.get('view_mode', 'list')
        keyboard = [
            [InlineKeyboardButton(f"📏 Page Size: {page_size}", callback_data="change_pagesize")],
            [InlineKeyboardButton(f"👁 View Mode: {'Gallery' if view_mode=='gallery' else 'List'}", callback_data="toggle_view")],
            [InlineKeyboardButton("🔙 Back", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"Settings\nPage Size: {page_size}\nView Mode: {view_mode}"
    elif state == "awaiting_broadcast_message":
        breadcrumb = [{"label": "🏠 Main", "callback": "home"}]
        text = "Send the message to broadcast to all users:"
        reply_markup = get_back_home_keyboard()
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
    date_filter = user_data.get('filter_date', None)
    page_size = user_data.get('page_size', DEFAULT_PAGE_SIZE)
    view_mode = user_data.get('view_mode', 'list')
    selected = user_data.get('selected_files', set())
    selection_mode = user_data.get('selection_mode', False)

    date_from = None
    date_to = None
    if date_filter == 'today':
        date_from = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif date_filter == 'week':
        date_from = datetime.now() - timedelta(days=7)
    elif date_filter == 'month':
        date_from = datetime.now() - timedelta(days=30)

    offset = page * page_size
    files = await get_user_files_filtered(user.id, offset, page_size, file_type_filter, date_from, date_to)
    total = await get_user_files_count_filtered(user.id, file_type_filter, date_from, date_to)
    total_pages = max(1, (total + page_size - 1) // page_size)

    keyboard = []

    filter_buttons = []
    if file_type_filter:
        filter_buttons.append(InlineKeyboardButton(f"❌ Filter: {file_type_filter}", callback_data="clear_filter"))
    else:
        filter_buttons.append(InlineKeyboardButton("🔍 Filter", callback_data="filter_menu"))
    if date_filter:
        filter_buttons.append(InlineKeyboardButton(f"📅 {date_filter}", callback_data="clear_date"))
    keyboard.append(filter_buttons)

    mode_text = "✅ Select Mode" if selection_mode else "☑️ Select Mode"
    keyboard.append([InlineKeyboardButton(mode_text, callback_data="toggle_selection_mode")])

    if not files:
        keyboard.append([InlineKeyboardButton("📭 No files", callback_data="noop")])
    else:
        for row in files:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            name = json.loads(row['custom_names'])[0]
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
        InlineKeyboardButton(f"📏 {page_size}", callback_data="change_pagesize"),
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
    if file_type_filter or date_filter:
        filters_str = []
        if file_type_filter: filters_str.append(file_type_filter)
        if date_filter: filters_str.append(date_filter)
        breadcrumb.append({"label": f"🔍 {'+'.join(filters_str)}", "callback": "myfiles"})

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
        reply_markup = get_back_home_keyboard()
        await update_main_message(update, context, text, reply_markup, 
                                 [{"label": "🏠 Main", "callback": "home"}, {"label": "🔍 Search", "callback": "search"}])
        return
    keyboard = []
    for row in results:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_search")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = f"Search results for '{query}':"
    await update_main_message(update, context, text, reply_markup,
                             [{"label": "🏠 Main", "callback": "home"}, {"label": "🔍 Search", "callback": "search"}])

# ---------- Callback Handlers ----------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    user_data = context.user_data

    if data == "home":
        user_data.clear()
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
        user_data['nav_history'] = user_data.get('nav_history', []) + ["main"]
        await enter_state(update, context, "awaiting_file")
    elif data == "myfiles":
        await enter_state(update, context, "myfiles", page=0)
    elif data == "search":
        await enter_state(update, context, "awaiting_search")
    elif data == "memory":
        size = await get_user_total_size(user.id)
        await answer_callback(update, f"Total storage: {human_readable_size(size)}")
    elif data == "settings":  # اگر کسی از طریق لینک مستقیم وارد شد، به خانه برگردان
        await enter_state(update, context, "main")

    elif data.startswith("myfiles_page_"):
        page = int(data.split("_")[-1])
        await show_myfiles_page(update, context, page)

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
            [InlineKeyboardButton("📅 Today", callback_data="filter_date_today"),
             InlineKeyboardButton("📅 Week", callback_data="filter_date_week")],
            [InlineKeyboardButton("📅 Month", callback_data="filter_date_month")],
            [InlineKeyboardButton("❌ Clear Filters", callback_data="clear_filters")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_myfiles")]
        ]
        await query.edit_message_text("Select filter:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("filter_type_"):
        ftype = data[12:]
        user_data['filter_type'] = ftype if ftype != "none" else None
        await show_myfiles_page(update, context, page=0)
    elif data.startswith("filter_date_"):
        date_opt = data[12:]
        user_data['filter_date'] = date_opt if date_opt != "none" else None
        await show_myfiles_page(update, context, page=0)
    elif data == "clear_filters":
        user_data['filter_type'] = None
        user_data['filter_date'] = None
        await show_myfiles_page(update, context, page=0)

    elif data == "change_pagesize":
        current = user_data.get('page_size', DEFAULT_PAGE_SIZE)
        idx = PAGE_SIZE_OPTIONS.index(current) if current in PAGE_SIZE_OPTIONS else 0
        new_size = PAGE_SIZE_OPTIONS[(idx + 1) % len(PAGE_SIZE_OPTIONS)]
        user_data['page_size'] = new_size
        await answer_callback(update, f"Page size set to {new_size}")
        # بعد از تغییر، صفحه فعلی را به‌روز کن
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))
    elif data == "toggle_view":
        current = user_data.get('view_mode', 'list')
        new_mode = 'gallery' if current == 'list' else 'list'
        user_data['view_mode'] = new_mode
        await answer_callback(update, f"View mode: {new_mode}")
        await show_myfiles_page(update, context, page=user_data.get('myfiles_page', 0))

    elif data == "broadcast":
        if user.id != ADMIN_ID:
            await answer_callback(update, "Admin only.")
            return
        await enter_state(update, context, "awaiting_broadcast_message")

    elif data == "noop":
        pass

    else:
        logger.warning(f"Unknown callback: {data}")

# ---------- Message Handler ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""

    if message.photo or message.video or message.audio or message.voice or message.document:
        await handle_file(update, context)
        return

    await record_user(user.id)
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return

    state = context.user_data.get('state', 'main')
    user_data = context.user_data

    if state == "awaiting_rename_text":
        new_name = text.strip()
        rename_id = user_data.get('rename_id')
        if rename_id:
            row = await get_file_by_id(rename_id)
            if row:
                cnames = json.loads(row['custom_names'])
                if cnames:
                    cnames[0] = new_name
                else:
                    cnames = [new_name]
                await update_names(rename_id, cnames)
                await answer_callback(update, "Name updated.")
                user_data.pop('rename_id', None)
                await enter_state(update, context, "file_options")
        return

    elif state == "awaiting_addname_text":
        new_name = text.strip()
        addname_id = user_data.get('addname_id')
        if addname_id:
            row = await get_file_by_id(addname_id)
            if row:
                cnames = json.loads(row['custom_names'])
                if new_name not in cnames:
                    cnames.append(new_name)
                    await update_names(addname_id, cnames)
                    await answer_callback(update, "Name added.")
                else:
                    await message.reply_text("Name already exists.")
                user_data.pop('addname_id', None)
                await enter_state(update, context, "file_options")
        return

    elif state == "awaiting_search":
        query = text.strip()
        if query:
            user_data['search_query'] = query
            await enter_state(update, context, "search_results")
        else:
            await message.reply_text("Please send a non-empty search term.")
        return

    elif state == "awaiting_broadcast_message":
        if user.id == ADMIN_ID:
            user_ids = await get_all_user_ids()
            success = 0
            for uid in user_ids:
                try:
                    await context.bot.send_message(uid, text)
                    success += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
            await message.reply_text(f"Broadcast sent to {success} users.")
            await enter_state(update, context, "main")
        return

    elif state == "awaiting_batch_tag":
        tag = text.strip()
        if tag:
            file_ids = user_data.get('batch_tag_files', [])
            for fid in file_ids:
                row = await get_file_by_id(fid)
                if row:
                    cnames = json.loads(row['custom_names'])
                    if tag not in cnames:
                        cnames.append(tag)
                        await update_names(fid, cnames)
            user_data.pop('batch_tag_files', None)
            await answer_callback(update, f"Tag '{tag}' added to {len(file_ids)} files.", True)
            await enter_state(update, context, "myfiles", page=user_data.get('myfiles_page', 0))
        else:
            await message.reply_text("Please send a non-empty tag.")
        return

    await enter_state(update, context, "main")

# ---------- Start & Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await record_user(user.id)
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return

    if 'first_start' not in context.user_data:
        context.user_data['first_start'] = True
        welcome_text = (
            "👋 Welcome to FileManager Bot!\n\n"
            "I can store your files and let you search them inline.\n"
            "Here's how to start:\n"
            "1️⃣ Tap '➕ New File' to upload a file.\n"
            "2️⃣ Tap '📁 My Files' to manage your files.\n"
            "3️⃣ Use inline search by typing @botusername in any chat.\n\n"
            "Let's get started!"
        )
        await update.message.reply_text(welcome_text, reply_markup=get_main_menu_keyboard())
        context.user_data['state'] = "main"
    else:
        await enter_state(update, context, "main")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    await update.message.reply_text("Use the menu buttons.\nInline search: @botusername query", parse_mode="Markdown")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await enter_state(update, context, "main")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await enter_state(update, context, "awaiting_broadcast_message")

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    user_ids = await get_all_user_ids()
    await update.message.reply_text(f"Total users: {len(user_ids)}")

# ---------- Webhook & FastAPI ----------
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

# ---------- Main ----------
async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).updater(None).build()
    ptb_app.add_error_handler(error_handler)

    await ptb_app.initialize()
    await ptb_app.start()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
    ptb_app.add_handler(CommandHandler("users", users_command))

    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_file))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    webhook_set = await ptb_app.bot.set_webhook(WEBHOOK_URL)
    if webhook_set:
        logger.info(f"✅ Webhook successfully set to {WEBHOOK_URL}")
    else:
        logger.error(f"❌ Failed to set webhook to {WEBHOOK_URL}")

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
