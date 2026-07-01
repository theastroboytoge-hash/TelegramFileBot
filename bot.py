import logging
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import uvicorn
import asyncio

# ======================== Logging ========================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ======================== Config ========================
TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
RENDER_EXTERNAL_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_EXTERNAL_HOSTNAME}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN or not WEBHOOK_SECRET or not DATABASE_URL:
    raise ValueError("TOKEN, WEBHOOK_SECRET and DATABASE_URL environment variables must be set!")

app = FastAPI()
ptb_app = None
db_pool = None

# ======================== Texts ========================
TEXTS = {
    "main_menu": "🏠 Main Menu",
    "my_files": "📁 My Files",
    "new_file": "➕ New File",
    "search": "🔍 Search",
    "memory": "📊 Memory",
    "admin_panel": "🛠 Admin",
    "back": "🔙 Back",
    "no_files": "You have no files yet.",
    "file_deleted": "✅ File deleted successfully.",
    "enter_new_name": "Please send the new name:",
    "enter_search": "Send the search term:",
    "no_results": "No files found.",
    "total_memory": "Total storage used: {size}",
    "select_action": "Choose an action from the menu:",
    "unknown": "Unknown command.",
    "welcome": "👋 Welcome to your Personal File Bot!\nUse the buttons below."
}

# ======================== Database ========================
async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=6)
        async with db_pool.acquire() as conn:
            # Files table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    custom_name TEXT,
                    file_type TEXT NOT NULL,
                    file_size BIGINT DEFAULT 0,
                    view_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
    return db_pool

async def record_user(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO UPDATE SET last_seen = NOW()",
            user_id
        )

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ======================== Helpers ========================
def human_readable_size(size_bytes: int) -> str:
    units = ['B', 'KB', 'MB', 'GB']
    size = float(size_bytes)
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}"

def main_menu_keyboard(user_id: int):
    keyboard = [
        [InlineKeyboardButton(TEXTS["my_files"], callback_data="myfiles")],
        [InlineKeyboardButton(TEXTS["new_file"], callback_data="newfile")],
        [InlineKeyboardButton(TEXTS["search"], callback_data="search")],
        [InlineKeyboardButton(TEXTS["memory"], callback_data="memory")],
    ]
    if is_admin(user_id):
        keyboard.append([InlineKeyboardButton(TEXTS["admin_panel"], callback_data="admin")])
    return InlineKeyboardMarkup(keyboard)

# ======================== My Files ========================
async def show_myfiles(update: Update, context, page: int = 0):
    user_id = update.effective_user.id
    page_size = 8
    offset = page * page_size

    pool = await get_pool()
    async with pool.acquire() as conn:
        files = await conn.fetch(
            "SELECT * FROM files WHERE user_id=$1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            user_id, page_size, offset
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM files WHERE user_id=$1", user_id)

    total_pages = max(1, (total + page_size - 1) // page_size)
    text = f"📁 My Files (Page {page+1}/{total_pages})\n\n"

    if not files:
        text += TEXTS["no_files"]

    keyboard = []
    for f in files:
        name = f['custom_name'] or f['file_name']
        emoji = {"photo": "🖼️", "video": "🎥", "audio": "🎵", "voice": "🎙️", "document": "📄"}.get(f['file_type'], "📄")
        keyboard.append([InlineKeyboardButton(f"{emoji} {name[:35]}", callback_data=f"file_{f['id']}")])

    # Pagination
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
        if nav:
            keyboard.append(nav)

    keyboard.append([InlineKeyboardButton(TEXTS["back"], callback_data="home")])
    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)

# ======================== Callback ========================
async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data == "home":
        await query.edit_message_text(TEXTS["select_action"], reply_markup=main_menu_keyboard(user_id))

    elif data == "myfiles":
        await show_myfiles(update, context)

    elif data == "newfile":
        context.user_data['state'] = "awaiting_file"
        await query.edit_message_text("📤 Please send your file (photo, video, document, audio or voice):")

    elif data == "search":
        context.user_data['state'] = "awaiting_search"
        await query.edit_message_text(TEXTS["enter_search"])

    elif data == "memory":
        pool = await get_pool()
        async with pool.acquire() as conn:
            total_size = await conn.fetchval("SELECT COALESCE(SUM(file_size), 0) FROM files WHERE user_id=$1", user_id)
        text = TEXTS["total_memory"].format(size=human_readable_size(total_size))
        await query.edit_message_text(text, reply_markup=main_menu_keyboard(user_id))

    elif data.startswith("page_"):
        page = int(data[5:])
        await show_myfiles(update, context, page)

    elif data.startswith("file_"):
        file_id = int(data[5:])
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_id)
        if not row:
            await query.answer("File not found")
            return

        name = row['custom_name'] or row['file_name']
        text = f"📄 {name}\nType: {row['file_type']}\nSize: {human_readable_size(row['file_size'])}"

        keyboard = [
            [InlineKeyboardButton("👁 Show", callback_data=f"show_{file_id}")],
            [InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{file_id}")],
            [InlineKeyboardButton("🗑 Delete", callback_data=f"del_{file_id}")],
            [InlineKeyboardButton(TEXTS["back"], callback_data="myfiles")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("show_"):
        file_id = int(data[5:])
        pool = await get_pool()
        async with pool.acquire() as conn:
            f = await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_id)
            if f:
                await conn.execute("UPDATE files SET view_count = view_count + 1 WHERE id=$1", file_id)
                if f['file_type'] == "photo":
                    await context.bot.send_photo(user_id, f['file_id'])
                elif f['file_type'] == "video":
                    await context.bot.send_video(user_id, f['file_id'])
                else:
                    await context.bot.send_document(user_id, f['file_id'])

    elif data.startswith("rename_"):
        context.user_data['rename_id'] = int(data[7:])
        context.user_data['state'] = "awaiting_rename"
        await query.edit_message_text(TEXTS["enter_new_name"])

    elif data.startswith("del_"):
        file_id = int(data[4:])
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yes", callback_data=f"confirmdel_{file_id}"),
            InlineKeyboardButton("❌ No", callback_data="myfiles")
        ]])
        await query.edit_message_text("Delete this file?", reply_markup=keyboard)

    elif data.startswith("confirmdel_"):
        file_id = int(data[11:])
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM files WHERE id=$1 AND user_id=$2", file_id, user_id)
        await query.edit_message_text(TEXTS["file_deleted"])
        await asyncio.sleep(1)
        await show_myfiles(update, context)

    else:
        await query.answer(TEXTS["unknown"])

# ======================== Message Handlers ========================
async def handle_message(update: Update, context):
    user = update.effective_user
    message = update.message
    await record_user(user.id)

    state = context.user_data.get('state')

    if state == "awaiting_file":
        await handle_file_upload(update, context)
    elif state == "awaiting_search":
        await handle_search(update, context)
    elif state == "awaiting_rename":
        await handle_rename(update, context)
    else:
        await message.reply_text(TEXTS["select_action"], reply_markup=main_menu_keyboard(user.id))

async def handle_file_upload(update: Update, context):
    message = update.message
    user = update.effective_user

    # Determine file
    if message.photo:
        file_obj = message.photo[-1]
        file_type = "photo"
        file_name = "photo.jpg"
    elif message.video:
        file_obj = message.video
        file_type = "video"
        file_name = file_obj.file_name or "video.mp4"
    elif message.document:
        file_obj = message.document
        file_type = "document"
        file_name = file_obj.file_name or "document"
    elif message.audio:
        file_obj = message.audio
        file_type = "audio"
        file_name = file_obj.file_name or "audio.mp3"
    elif message.voice:
        file_obj = message.voice
        file_type = "voice"
        file_name = "voice.ogg"
    else:
        await message.reply_text("Unsupported file.")
        return

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO files (user_id, file_id, file_name, custom_name, file_type, file_size)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            user.id, file_obj.file_id, file_name, file_name, file_type, file_obj.file_size or 0
        )

    await message.reply_text("✅ File saved successfully!")
    context.user_data.clear()
    await show_myfiles(update, context)

async def handle_search(update: Update, context):
    query = update.message.text.strip()
    user_id = update.effective_user.id

    pool = await get_pool()
    async with pool.acquire() as conn:
        files = await conn.fetch(
            """SELECT * FROM files WHERE user_id=$1 
               AND (file_name ILIKE $2 OR custom_name ILIKE $2)""",
            user_id, f"%{query}%"
        )

    if not files:
        await update.message.reply_text(TEXTS["no_results"])
        return

    keyboard = [[InlineKeyboardButton((f['custom_name'] or f['file_name'])[:35], 
                                    callback_data=f"file_{f['id']}")] for f in files]
    await update.message.reply_text(f"🔍 Results for '{query}':", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_rename(update: Update, context):
    new_name = update.message.text.strip()
    rename_id = context.user_data.get('rename_id')

    if rename_id and new_name:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE files SET custom_name = $1 WHERE id = $2", new_name, rename_id)
        await update.message.reply_text("✅ Name updated.")

    context.user_data.clear()
    await show_myfiles(update, context)

# ======================== Start ========================
async def start(update: Update, context):
    await record_user(update.effective_user.id)
    await update.message.reply_text(TEXTS["welcome"], reply_markup=main_menu_keyboard(update.effective_user.id))

# ======================== Main ========================
async def main():
    global ptb_app
    await get_pool()

    ptb_app = Application.builder().token(TOKEN).updater(None).build()
    await ptb_app.initialize()
    await ptb_app.start()

    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(filters.TEXT & \
                                       filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.DOCUMENT | filters.AUDIO | filters.VOICE, 
        handle_file_upload
    ))

    await ptb_app.bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
    logger.info(f"Webhook set successfully → {WEBHOOK_URL}")

    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return {"status": "unauthorized"}, 401
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

if __name__ == "__main__":
    asyncio.run(main())
