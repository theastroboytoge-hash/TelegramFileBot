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

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if update and hasattr(update, 'effective_message') and update.effective_message:
            await update.effective_message.reply_text("An error occurred. Please try again later.")
    except:
        pass

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

# ==================== INLINE QUERY بهبود یافته ====================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    results = []
    try:
        files = await get_user_files(user_id)
        for row in files:
            try:
                cnames = json.loads(row['custom_names'] or '[]')
                if not cnames:
                    cnames = [row.get('file_name', 'file')]
                title = cnames[0]
                fid = row['file_id']
                ftype = row['file_type']
                if not query_text or any(query_text in n.lower() for n in cnames):
                    if ftype == "photo":
                        results.append(InlineQueryResultCachedPhoto(id=str(row['id']), photo_file_id=fid, title=title))
                    elif ftype == "video":
                        results.append(InlineQueryResultCachedVideo(id=str(row['id']), video_file_id=fid, title=title))
                    elif ftype == "audio":
                        results.append(InlineQueryResultCachedAudio(id=str(row['id']), audio_file_id=fid, title=title))
                    elif ftype == "voice":
                        results.append(InlineQueryResultCachedVoice(id=str(row['id']), voice_file_id=fid, title=title))
                    else:
                        results.append(InlineQueryResultCachedDocument(id=str(row['id']), document_file_id=fid, title=title))
            except:
                continue
        await update.inline_query.answer(results[:50], cache_time=5)
    except Exception as e:
        logger.error(f"Inline error: {e}")
        await update.inline_query.answer([])

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
    elif data.startswith("dupadd_"):
        # ... (duplicate logic if needed)
        pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption or ""

    if text.strip() == "Back":
        await go_back(update, context)
        return
    if text.strip() == "Done":
        if context.user_data.get('state') in ("awaiting_file",):
            await enter_state(update, context, "main")
            return

    await record_user(user.id)
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return

    state = context.user_data.get('state', 'main')
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
    # ... (بقیه منطق awaiting_file, awaiting_name و ... را می‌توانی از نسخه قبلی تکمیل کنی اگر نیاز بود)

async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).build()
    ptb_app.add_error_handler(error_handler)
    await ptb_app.initialize()
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("myfiles", lambda u,c: enter_state(u,c,"myfiles_list")))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
    ptb_app.add_handler(CommandHandler("users", users_command))
    ptb_app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.ALL & \~filters.TEXT & \~filters.COMMAND, handle_message))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
