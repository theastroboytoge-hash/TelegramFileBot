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
    "photo": "🖼️",
    "video": "📽️",
    "audio": "🎵",
    "voice": "🎙️",
    "document": "📄"
}
PAGE_SIZE = 5
def get_msg(update: Update):
    if update.message:
        return update.message
    elif update.callback_query:
        return update.callback_query.message
    else:
        return None
async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=1,
            max_size=5,
            max_inactive_connection_lifetime=300.0
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
        await conn.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            user_id
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
            rows = await conn.fetch("SELECT * FROM files")
        else:
            rows = await conn.fetch("SELECT * FROM files WHERE user_id=$1", user_id)
        return rows
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
        if user_id == ADMIN_ID:
            rows = await conn.fetch("SELECT * FROM files WHERE custom_names::text ILIKE $1", f'%{query}%')
        else:
            rows = await conn.fetch("SELECT * FROM files WHERE user_id=$1 AND custom_names::text ILIKE $2", user_id, f'%{query}%')
        return rows
async def get_file_by_id(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM files WHERE id=$1", file_db_id)
        return row
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
        await conn.execute(
            "UPDATE files SET custom_names=$1 WHERE id=$2",
            json.dumps(custom_names), file_db_id
        )
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
    user = update.effective_user
    msg = get_msg(update)
    if not msg:
        return
    chat_id = msg.chat_id
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
            [InlineKeyboardButton("Rename", callback_data=f"renamef_{file_id}"),
             InlineKeyboardButton("Add Name", callback_data=f"addnamef_{file_id}")],
            [InlineKeyboardButton("Delete", callback_data=f"delf_{file_id}")]
        ])
        text = f"📁 {title}\n📏 Size: {size_str}\n📌 Type: {type_emoji} {row['file_type']}"
        if 'file_options_msg' in context.user_data:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=context.user_data['file_options_msg'],
                    text=text,
                    reply_markup=markup
                )
                return
            except:
                pass
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
    elif state == "awaiting_delete_confirmation":
        pass
async def show_myfiles_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    user = update.effective_user
    msg = get_msg(update)
    if not msg:
        return
    offset = page * PAGE_SIZE
    files = await get_user_files_paginated(user.id, offset, PAGE_SIZE)
    total = await get_user_files_count(user.id)
    total_pages = max(1, -(-total // PAGE_SIZE))
    keyboard = []
    keyboard.append([InlineKeyboardButton("🔍 Search", callback_data="search_start")])
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        name = json.loads(row['custom_names'])[0]
        keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Prev", callback_data=f"page_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    markup = InlineKeyboardMarkup(keyboard)
    new_text = f"📂 Your files (Page {page+1}/{total_pages})"
    if 'myfiles_list_msg' in context.user_data:
        try:
            await context.bot.edit_message_text(
                chat_id=msg.chat_id,
                message_id=context.user_data['myfiles_list_msg'],
                text=new_text,
                reply_markup=markup
            )
            return
        except:
            pass
    sent = await msg.reply_text(new_text, reply_markup=markup)
    context.user_data['myfiles_list_msg'] = sent.message_id
    context.user_data['page'] = page
async def show_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = get_msg(update)
    if not msg:
        return
    query = context.user_data.get('search_query', '').strip()
    if not query:
        await msg.reply_text("No search query. Returning.", reply_markup=BACK_KEYBOARD)
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
    if history:
        prev_state = history.pop()
        context.user_data['nav_history'] = history
    else:
        prev_state = "main"
    context.user_data.pop('pending_file', None)
    context.user_data.pop('rename_id', None)
    context.user_data.pop('addname_id', None)
    context.user_data.pop('current_file_id', None)
    context.user_data.pop('myfiles_list_msg', None)
    context.user_data.pop('file_options_msg', None)
    context.user_data.pop('page', None)
    context.user_data.pop('search_query', None)
    context.user_data.pop('delete_file_id', None)
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
    text = (
        "🤖 *Bot Help*\n\n"
        "*Main Menu:*\n"
        "• New File – Add a new file to your collection.\n"
        "• My Files – Browse, search, rename, add names, show, or delete files.\n"
        "• Memory – View total storage used.\n\n"
        "*Inline Search:* Type @bot_name followed by a query in any chat to send files.\n\n"
        "Use /cancel to abort any ongoing operation.\n"
        "Use Back buttons to return to previous menu."
    )
    await update.message.reply_text(text, parse_mode="Markdown")
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized.")
        return
    context.user_data['state'] = "awaiting_broadcast_message"
    context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
    await update.message.reply_text("Send the message to broadcast to all users:", reply_markup=BACK_KEYBOARD)
async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("You are not authorized.")
        return
    user_ids = await get_all_user_ids()
    await update.message.reply_text(f"Total users: {len(user_ids)}")
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption
    if text and text.strip() == "Back":
        await go_back(update, context)
        return
    if text and text.strip() == "Done":
        if context.user_data.get('state') in ("awaiting_file", "batch_awaiting_file"):
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
        else:
            await message.reply_text("Use the menu buttons.", reply_markup=MAIN_KEYBOARD)
    elif state == "awaiting_file":
        file = None
        file_name = "file"
        file_type = "document"
        file_size = 0
        if message.document:
            file = message.document
            file_name = file.file_name or "document"
            file_type = "document"
            file_size = file.file_size or 0
        elif message.photo:
            file = message.photo[-1]
            file_name = "photo.jpg"
            file_type = "photo"
            file_size = file.file_size or 0
        elif message.video:
            file = message.video
            file_name = "video.mp4"
            file_type = "video"
            file_size = file.file_size or 0
        elif message.audio:
            file = message.audio
            file_name = file.file_name or "audio"
            file_type = "audio"
            file_size = file.file_size or 0
        elif message.voice:
            file = message.voice
            file_name = "voice.ogg"
            file_type = "voice"
            file_size = file.file_size or 0
        else:
            await message.reply_text("Please send a file or press Done.", reply_markup=BATCH_KEYBOARD)
            return
        context.user_data['pending_file'] = {
            'file_id': file.file_id,
            'file_name': file_name,
            'file_type': file_type,
            'file_size': file_size
        }
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["awaiting_file"]
        await enter_state(update, context, "awaiting_name")
    elif state == "awaiting_name":
        if not text:
            await message.reply_text("Send a name for the file.", reply_markup=BACK_KEYBOARD)
            return
        name = text.strip()
        if name.lower() == '/cancel':
            context.user_data.pop('pending_file', None)
            await go_back(update, context)
            return
        data = context.user_data.get('pending_file')
        if not data:
            await message.reply_text("No pending file.", reply_markup=MAIN_KEYBOARD)
            context.user_data['state'] = "main"
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT id, custom_names FROM files WHERE user_id=$1 AND file_id=$2", user.id, data['file_id'])
        if existing:
            existing_id = existing['id']
            existing_names = json.loads(existing['custom_names'])
            existing_title = existing_names[0]
            context.user_data['pending_duplicate'] = {
                'existing_id': existing_id,
                'new_name': name,
                'data': data
            }
            await message.reply_text(
                f"This file already exists as '{existing_title}'. Add '{name}' as an additional name?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Yes, add name", callback_data=f"dupadd_{existing_id}"),
                     InlineKeyboardButton("No, cancel", callback_data="dupcancel")]
                ])
            )
            context.user_data['state'] = "awaiting_duplicate_decision"
            return
        await add_file(user.id, data['file_id'], data['file_name'], [name], data['file_type'], data['file_size'])
        context.user_data.pop('pending_file', None)
        await message.reply_text(f"File saved as '{name}'. Send another file or press Done.", reply_markup=BATCH_KEYBOARD)
        context.user_data['state'] = "awaiting_file"
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
    elif state == "awaiting_rename_text":
        if not text:
            await message.reply_text("Send the new name.", reply_markup=BACK_KEYBOARD)
            return
        file_id = context.user_data.get('rename_id')
        if not file_id:
            await go_back(update, context)
            return
        row = await get_file_by_id(file_id)
        if not row:
            await message.reply_text("File not found.", reply_markup=BACK_KEYBOARD)
            await go_back(update, context)
            return
        cnames = json.loads(row['custom_names'])
        cnames[0] = text.strip()
        await update_names(file_id, cnames)
        await message.reply_text("Name updated.", reply_markup=BACK_KEYBOARD)
        context.user_data.pop('rename_id', None)
        await go_back(update, context)
    elif state == "awaiting_addname_text":
        if not text:
            await message.reply_text("Send additional name.", reply_markup=BACK_KEYBOARD)
            return
        file_id = context.user_data.get('addname_id')
        if not file_id:
            await go_back(update, context)
            return
        row = await get_file_by_id(file_id)
        if not row:
            await message.reply_text("File not found.", reply_markup=BACK_KEYBOARD)
            await go_back(update, context)
            return
        cnames = json.loads(row['custom_names'])
        new_name = text.strip()
        if new_name not in cnames:
            cnames.append(new_name)
        await update_names(file_id, cnames)
        await message.reply_text("Name added.", reply_markup=BACK_KEYBOARD)
        context.user_data.pop('addname_id', None)
        await go_back(update, context)
    elif state == "awaiting_search":
        if not text:
            await message.reply_text("Send a search term.", reply_markup=BACK_KEYBOARD)
            return
        context.user_data['search_query'] = text.strip()
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["myfiles_list"]
        await enter_state(update, context, "search_results")
    elif state == "awaiting_broadcast_message":
        if not text:
            await message.reply_text("Send the broadcast message.", reply_markup=BACK_KEYBOARD)
            return
        user_ids = await get_all_user_ids()
        count = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(chat_id=uid, text=text.strip())
                count += 1
            except:
                continue
        await message.reply_text(f"Broadcast sent to {count}/{len(user_ids)} users.", reply_markup=MAIN_KEYBOARD)
        context.user_data['state'] = "main"
        context.user_data['nav_history'] = []
    elif state in ("myfiles_list", "file_options", "search_results", "awaiting_duplicate_decision"):
        await message.reply_text("Use the inline buttons or press Back.", reply_markup=BACK_KEYBOARD)
    else:
        await message.reply_text("Something went wrong. Returning to main menu.", reply_markup=MAIN_KEYBOARD)
        context.user_data['state'] = "main"
        context.user_data['nav_history'] = []
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_file', None)
    context.user_data.pop('rename_id', None)
    context.user_data.pop('addname_id', None)
    context.user_data.pop('current_file_id', None)
    context.user_data.pop('myfiles_list_msg', None)
    context.user_data.pop('file_options_msg', None)
    context.user_data.pop('page', None)
    context.user_data.pop('search_query', None)
    context.user_data.pop('delete_file_id', None)
    context.user_data.pop('pending_duplicate', None)
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    await update.message.reply_text("Operation cancelled.", reply_markup=MAIN_KEYBOARD)
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.lower()
    user_id = update.inline_query.from_user.id
    results = []
    files = await get_user_files(user_id)
    for row in files:
        db_id = row['id']
        fid = row['file_id']
        cnames = json.loads(row['custom_names'])
        file_type = row['file_type']
        if not query or any(query in n.lower() for n in cnames):
            title = cnames[0]
            if file_type == "photo":
                results.append(InlineQueryResultCachedPhoto(id=str(db_id), photo_file_id=fid, title=title))
            elif file_type == "video":
                results.append(InlineQueryResultCachedVideo(id=str(db_id), video_file_id=fid, title=title))
            elif file_type == "audio":
                results.append(InlineQueryResultCachedAudio(id=str(db_id), audio_file_id=fid, title=title))
            elif file_type == "voice":
                results.append(InlineQueryResultCachedVoice(id=str(db_id), voice_file_id=fid, title=title))
            else:
                results.append(InlineQueryResultCachedDocument(id=str(db_id), document_file_id=fid, title=title))
    await update.inline_query.answer(results, cache_time=0)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    if data.startswith("listfile_"):
        file_id = int(data[9:])
        context.user_data['current_file_id'] = file_id
        if context.user_data.get('state') == "myfiles_list":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["myfiles_list"]
        elif context.user_data.get('state') == "search_results":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["search_results"]
        context.user_data['state'] = "file_options"
        await enter_state(update, context, "file_options")
    elif data.startswith("showf_"):
        file_id = int(data[6:])
        row = await get_file_by_id(file_id)
        if row:
            file_type = row['file_type']
            fid = row['file_id']
            if file_type == "photo":
                await context.bot.send_photo(chat_id=user.id, photo=fid)
            elif file_type == "video":
                await context.bot.send_video(chat_id=user.id, video=fid)
            elif file_type == "audio":
                await context.bot.send_audio(chat_id=user.id, audio=fid)
            elif file_type == "voice":
                await context.bot.send_voice(chat_id=user.id, voice=fid)
            else:
                await context.bot.send_document(chat_id=user.id, document=fid)
    elif data.startswith("delf_"):
        file_id = int(data[5:])
        context.user_data['delete_file_id'] = file_id
        await query.edit_message_text(
            "Are you sure you want to delete this file?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Yes, delete", callback_data=f"confirmdel_{file_id}"),
                 InlineKeyboardButton("No", callback_data="cancel_del")]
            ])
        )
    elif data.startswith("confirmdel_"):
        file_id = int(data[11:])
        await delete_file(file_id)
        await query.edit_message_text("File deleted.")
        await go_back(update, context)
    elif data == "cancel_del":
        await query.edit_message_text("Deletion cancelled.")
        await go_back(update, context)
    elif data.startswith("renamef_"):
        file_id = int(data[8:])
        context.user_data['rename_id'] = file_id
        if context.user_data.get('state') == "file_options":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["file_options"]
        await enter_state(update, context, "awaiting_rename_text")
    elif data.startswith("addnamef_"):
        file_id = int(data[9:])
        context.user_data['addname_id'] = file_id
        if context.user_data.get('state') == "file_options":
            context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["file_options"]
        await enter_state(update, context, "awaiting_addname_text")
    elif data == "search_start":
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["myfiles_list"]
        await enter_state(update, context, "awaiting_search")
    elif data.startswith("page_"):
        page = int(data[5:])
        context.user_data['page'] = page
        await show_myfiles_page(update, context, page)
    elif data.startswith("dupadd_"):
        existing_id = int(data[7:])
        dup_data = context.user_data.get('pending_duplicate')
        if not dup_data or dup_data['existing_id'] != existing_id:
            await query.edit_message_text("Something went wrong. Try again.")
            await go_back(update, context)
            return
        row = await get_file_by_id(existing_id)
        if not row:
            await query.edit_message_text("File no longer exists.")
            await go_back(update, context)
            return
        cnames = json.loads(row['custom_names'])
        new_name = dup_data['new_name']
        if new_name not in cnames:
            cnames.append(new_name)
        await update_names(existing_id, cnames)
        await query.edit_message_text(f"Name '{new_name}' added to existing file.")
        context.user_data.pop('pending_duplicate', None)
        context.user_data.pop('pending_file', None)
        context.user_data['state'] = "awaiting_file"
        await context.bot.send_message(chat_id=user.id, text="You can send another file or press Done.", reply_markup=BATCH_KEYBOARD)
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
    elif data == "dupcancel":
        context.user_data.pop('pending_duplicate', None)
        context.user_data.pop('pending_file', None)
        await query.edit_message_text("Cancelled. You can send another file or press Done.")
        context.user_data['state'] = "awaiting_file"
        await context.bot.send_message(chat_id=user.id, text="Send a file or press Done.", reply_markup=BATCH_KEYBOARD)
async def myfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return
    context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["main"]
    await enter_state(update, context, "myfiles_list")
@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, ptb_app.bot)
    await ptb_app.process_update(update)
    return {"status": "ok"}
@app.get("/")
async def root():
    return {"status": "bot is running"}
async def main():
    global ptb_app
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).build()
    ptb_app.add_error_handler(error_handler)
    await ptb_app.initialize()
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("help", help_command))
    ptb_app.add_handler(CommandHandler("myfiles", myfiles_command))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(CommandHandler("broadcast", broadcast_command))
    ptb_app.add_handler(CommandHandler("users", users_command))
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()
if __name__ == "__main__":
    asyncio.run(main())
