import logging
import json
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters
import uvicorn
import asyncio

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-app.onrender.com')}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()
ptb_app = None
db_pool = None

MAIN_KEYBOARD = ReplyKeyboardMarkup([["New File", "My Files"], ["Memory"]], resize_keyboard=True)
BACK_KEYBOARD = ReplyKeyboardMarkup([["Back"]], resize_keyboard=True)
BATCH_KEYBOARD = ReplyKeyboardMarkup([["Done"], ["Back"]], resize_keyboard=True)

FILE_TYPE_EMOJI = {
    "photo": "🖼️", "video": "📽️", "audio": "🎵", "voice": "🎙️", "document": "📄"
}

PAGE_SIZE = 5

def get_msg(update: Update):
    if update.message:
        return update.message
    elif update.callback_query:
        return update.callback_query.message
    return None

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

# ====================== ERROR HANDLER (بروزرسانی شده - بدون تغییر) ======================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# ====================== تشخیص هوشمند موسیقی (بروزرسانی) ======================
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

# ====================== INLINE QUERY (بروزرسانی شده - بدون تغییر) ======================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    results = []
    
    logger.info(f"Inline query from user {user_id} | query: '{query_text}'")
    
    try:
        files = await get_user_files(user_id)
        
        for row in files:
            try:
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
                
            except Exception as e:
                logger.warning(f"Error processing file {row.get('id')}: {e}")
                continue
                
        await update.inline_query.answer(results[:50], cache_time=5, is_personal=True)
        
    except Exception as e:
        logger.error(f"Critical inline query error: {e}", exc_info=True)
        await update.inline_query.answer([])

# ====================== HANDLER فایل (بروزرسانی) ======================
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
        await message.reply_text("این نوع فایل پشتیبانی نمی‌شود.")
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

    await message.reply_text(f"✅ {FILE_TYPE_EMOJI.get(file_type, '📄')} **{file_name}** ذخیره شد.", parse_mode="Markdown")

# ====================== توابع منو و حالت‌ها (اضافه شده از کد قدیمی) ======================
async def enter_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str):
    msg = get_msg(update)
    if not msg:
        return
    context.user_data['state'] = state
    if state == "main":
        await msg.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)
    elif state == "awaiting_file":
        await msg.reply_text("Send a file or press Done.", reply_markup=BATCH_KEYBOARD)
    elif state == "awaiting_name":
        await msg.reply_text("File received. Send the name for this file (or /cancel):", reply_markup=BACK_KEYBOARD)
    elif state == "myfiles_list":
        await show_myfiles_page(update, context, page=0)
        await msg.reply_text("Select a file, use navigation, search, or press Back.", reply_markup=BACK_KEYBOARD)
    elif state == "file_options":
        file_id = context.user_data.get('current_file_id')
        if not file_id:
            await enter_state(update, context, "myfiles_list")
            return
        row = await get_file_by_id(file_id)
        if not row:
            await msg.reply_text("File not found.", reply_markup=BACK_KEYBOARD)
            return
        cnames = json.loads(row['custom_names'])
        title = cnames[0]
        size_str = human_readable_size(row['file_size'])
        type_emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Show", callback_data=f"showf_{file_id}")],
            [InlineKeyboardButton("Rename", callback_data=f"renamef_{file_id}"), InlineKeyboardButton("Add Name", callback_data=f"addnamef_{file_id}")],
            [InlineKeyboardButton("Delete", callback_data=f"delf_{file_id}")]
        ])
        text = f"📁 {title}\n📏 Size: {size_str}\n📌 Type: {type_emoji} {row['file_type']}"
        sent = await msg.reply_text(text, reply_markup=markup)
        context.user_data['file_options_msg'] = sent.message_id
    elif state == "awaiting_rename_text":
        await msg.reply_text("Send the new name:", reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_addname_text":
        await msg.reply_text("Send additional name:", reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_search":
        await msg.reply_text("Send the search term:", reply_markup=BACK_KEYBOARD)
    elif state == "search_results":
        await show_search_results(update, context)
    elif state == "awaiting_broadcast_message":
        await msg.reply_text("Send the message to broadcast to all users:", reply_markup=BACK_KEYBOARD)

async def show_myfiles_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    user = update.effective_user
    msg = get_msg(update)
    if not msg: return
    offset = page * PAGE_SIZE
    files = await get_user_files_paginated(user.id, offset, PAGE_SIZE)
    total = await get_user_files_count(user.id)
    total_pages = max(1, -(-total // PAGE_SIZE))
    keyboard = [[InlineKeyboardButton("🔍 Search", callback_data="search_start")]]
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_{page-1}"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_{page+1}"))
    if nav: keyboard.append(nav)
    markup = InlineKeyboardMarkup(keyboard)
    new_text = f"📂 Your files (Page {page+1}/{total_pages})"
    sent = await msg.reply_text(new_text, reply_markup=markup)
    context.user_data['myfiles_list_msg'] = sent.message_id
    context.user_data['page'] = page

async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = get_msg(update)
    if not msg: return
    query = context.user_data.get('search_query', '').strip()
    if not query:
        await enter_state(update, context, "myfiles_list")
        return
    results = await search_files(user.id, query)
    if not results:
        await msg.reply_text("No files found.", reply_markup=BACK_KEYBOARD)
        return
    keyboard = []
    for row in results:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    markup = InlineKeyboardMarkup(keyboard)
    await msg.reply_text(f"Search results for '{query}':", reply_markup=markup)
    await msg.reply_text("Select a file or press Back.", reply_markup=BACK_KEYBOARD)

async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get('nav_history', [])
    prev_state = history.pop() if history else "main"
    context.user_data['nav_history'] = history
    for key in ['pending_file', 'rename_id', 'addname_id', 'current_file_id', 'myfiles_list_msg', 'file_options_msg', 'page', 'search_query', 'delete_file_id']:
        context.user_data.pop(key, None)
    context.user_data['state'] = prev_state
    await enter_state(update, context, prev_state)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    await update.message.reply_text("Use the menu buttons.\nInline search: @botusername query", parse_mode="Markdown")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    context.user_data['state'] = "awaiting_broadcast_message"
    await update.message.reply_text("Send the message to broadcast:", reply_markup=BACK_KEYBOARD)

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    user_ids = await get_all_user_ids()
    await update.message.reply_text(f"Total users: {len(user_ids)}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in ['pending_file', 'rename_id', 'addname_id', 'current_file_id', 'myfiles_list_msg', 'file_options_msg', 'page', 'search_query', 'delete_file_id', 'pending_duplicate']:
        context.user_data.pop(key, None)
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    await update.message.reply_text("Operation cancelled.", reply_markup=MAIN_KEYBOARD)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user

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
    elif data.startswith("delf_"):
        file_id = int(data[5:])
        await query.edit_message_text("Are you sure?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes", callback_data=f"confirmdel_{file_id}"), InlineKeyboardButton("No", callback_data="cancel_del")]
        ]))
    elif data.startswith("confirmdel_"):
        await delete_file(int(data[11:]))
        await query.edit_message_text("File deleted.")
        await go_back(update, context)
    elif data == "cancel_del":
        await query.edit_message_text("Cancelled.")
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
        await show_myfiles_page(update, context, int(data[5:]))

# ====================== HANDLE MESSAGE (ترکیبی از قدیم و جدید) ======================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""

    # دکمه‌های Back و Done
    if text.strip() == "Back":
        await go_back(update, context)
        return
    if text.strip() == "Done":
        if context.user_data.get('state') in ("awaiting_file",):
            await enter_state(update, context, "main")
            return

    # اگر فایل ارسال شده باشد، به handle_file بسپار
    if message.photo or message.video or message.audio or message.voice or message.document:
        await handle_file(update, context)
        return

    # ثبت کاربر و بررسی عضویت
    await record_user(user.id)
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return

    state = context.user_data.get('state', 'main')

    # مدیریت حالت‌های مختلف (از کد قدیمی)
    if state == "main":
        if text == "New File":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
            await enter_state(update, context, "awaiting_file")
        elif text == "My Files":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
            await enter_state(update, context, "myfiles_list")
        elif text == "Memory":
            size = await get_user_total_size(user.id)
            await message.reply_text(f"Total storage: {human_readable_size(size)}", reply_markup=MAIN_KEYBOARD)
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
                await message.reply_text("Name updated.", reply_markup=MAIN_KEYBOARD)
                context.user_data.pop('rename_id', None)
                await enter_state(update, context, "main")
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
                    await message.reply_text("Name added.", reply_markup=MAIN_KEYBOARD)
                else:
                    await message.reply_text("Name already exists.", reply_markup=BACK_KEYBOARD)
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
        if update.effective_user.id == ADMIN_ID:
            user_ids = await get_all_user_ids()
            success = 0
            for uid in user_ids:
                try:
                    await context.bot.send_message(uid, text)
                    success += 1
                    await asyncio.sleep(0.05)
                except:
                    pass
            await message.reply_text(f"Broadcast sent to {success} users.", reply_markup=MAIN_KEYBOARD)
            await enter_state(update, context, "main")
    else:
        await message.reply_text("Unknown command. Use /start.", reply_markup=MAIN_KEYBOARD)

# ====================== روت وب‌هوک در FastAPI ======================
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    if ptb_app:
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
    return {"status": "ok"}

# ====================== MAIN (ترکیبی از قدیم و جدید) ======================
async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).build()
    ptb_app.add_error_handler(error_handler)

    # فرمان‌ها (از کد قدیمی)
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("myfiles", lambda u,c: enter_state(u,c,"myfiles_list")))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
    ptb_app.add_handler(CommandHandler("users", users_command))

    # Inline query (بروزرسانی شده)
    ptb_app.add_handler(InlineQueryHandler(inline_query))

    # Callback query (از کد قدیمی)
    ptb_app.add_handler(CallbackQueryHandler(button_callback))

    # هندلرهای پیام (بروزرسانی شده)
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_file))

    # تنظیم وب‌هوک (بدون نیاز به initialize)
    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

    # راه‌اندازی سرور FastAPI با uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
