import logging
import json
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, filters
import uvicorn
import asyncio

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-app.onrender.com')}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()
ptb_app = None
db_pool = None

FILE_TYPE_EMOJI = {
    "photo": "🖼️", "video": "📽️", "audio": "🎵", "voice": "🎙️", "document": "📄"
}

PAGE_SIZE = 5

def get_main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 My Files", callback_data="main_myfiles")],
        [InlineKeyboardButton("➕ New File", callback_data="main_newfile")],
        [InlineKeyboardButton("🔍 Search", callback_data="main_search")],
        [InlineKeyboardButton("📊 Storage", callback_data="main_storage")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="main_settings")]
    ])

async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL, custom_names JSONB NOT NULL DEFAULT '[]',
                    file_type TEXT NOT NULL, file_size BIGINT NOT NULL DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT NOW(), view_count INTEGER DEFAULT 0
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, first_seen TIMESTAMP DEFAULT NOW(),
                    is_banned BOOLEAN DEFAULT FALSE, last_active TIMESTAMP DEFAULT NOW()
                )
            ''')
    return db_pool

async def record_user(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, last_active) 
            VALUES ($1, NOW()) 
            ON CONFLICT (user_id) DO UPDATE SET last_active = NOW()
        """, user_id)

async def add_file(user_id, file_id, file_name, custom_names, file_type, file_size):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO files (user_id, file_id, file_name, custom_names, file_type, file_size) VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, file_id, file_name, json.dumps(custom_names), file_type, file_size
        )

async def get_user_files_paginated(user_id, offset, limit):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            return await conn.fetch("SELECT * FROM files ORDER BY id LIMIT $1 OFFSET $2", limit, offset)
        return await conn.fetch("SELECT * FROM files WHERE user_id=$1 ORDER BY id LIMIT $2 OFFSET $3", user_id, limit, offset)

async def get_user_files_count(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            row = await conn.fetchrow("SELECT COUNT(*) FROM files")
        else:
            row = await conn.fetchrow("SELECT COUNT(*) FROM files WHERE user_id=$1", user_id)
        return row[0] if row else 0

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
        return row[0] if row and row[0] else 0

async def delete_file(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM files WHERE id=$1", file_db_id)

async def update_names(file_db_id, custom_names):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET custom_names=$1 WHERE id=$2", json.dumps(custom_names), file_db_id)

async def increment_view_count(file_db_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE files SET view_count = view_count + 1 WHERE id = $1", file_db_id)

async def check_membership(bot, user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

def human_readable_size(size_bytes):
    if not size_bytes:
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

def is_audio_file(message):
    if message.audio:
        return True, "audio", message.audio.file_name or "audio.mp3"
    if message.document:
        mime = message.document.mime_type or ""
        file_name = message.document.file_name or ""
        ext = file_name.lower()
        if (mime.startswith("audio/") or ext.endswith(('.mp3', '.m4a', '.flac', '.wav', '.ogg', '.aac', '.wma', '.opus', '.m4b'))):
            return True, "audio", file_name
    return False, None, None

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.inline_query.query.lower().strip()
    user_id = update.inline_query.from_user.id
    results = []
    try:
        files = await get_user_files_paginated(user_id, 0, 50)
        for row in files:
            try:
                db_id = str(row['id'])
                file_id = row['file_id']
                ftype = row['file_type']
                cnames = json.loads(row.get('custom_names') or '[]')
                title = cnames[0] if cnames else row['file_name']
                search_text = " ".join([n.lower() for n in cnames] + [row['file_name'].lower()])
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
            except:
                continue
        await update.inline_query.answer(results[:50], cache_time=5, is_personal=True)
    except Exception as e:
        logger.error(f"Inline query error: {e}")
        await update.inline_query.answer([])

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    if context.user_data.get('state') != "awaiting_file":
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
        await message.reply_text("❌ This file type is not supported.")
        return
    file_id = file.file_id
    file_size = getattr(file, 'file_size', 0) or 0
    await add_file(user.id, file_id, file_name, [file_name], file_type, file_size)
    await message.reply_text(f"✅ {FILE_TYPE_EMOJI.get(file_type, '📄')} **{file_name}** saved successfully!", parse_mode="Markdown")
    context.user_data.pop('state', None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await record_user(update.effective_user.id)
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return
    context.user_data['state'] = "main"
    await update.message.reply_text("👋 Welcome to File Bot!\nUse the menu below:", reply_markup=get_main_menu())

async def show_file_options(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: int):
    row = await get_file_by_id(file_id)
    if not row:
        await update.callback_query.edit_message_text("❌ File not found.")
        return
    cnames = json.loads(row.get('custom_names') or '[]')
    title = cnames[0] if cnames else row['file_name']
    size_str = human_readable_size(row['file_size'])
    type_emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁️ Show File", callback_data=f"showf_{file_id}")],
        [InlineKeyboardButton("✏️ Rename", callback_data=f"renamef_{file_id}"), InlineKeyboardButton("➕ Add Name", callback_data=f"addnamef_{file_id}")],
        [InlineKeyboardButton("🗑️ Delete", callback_data=f"delf_{file_id}")],
        [InlineKeyboardButton("🏠 Home", callback_data="main_home")]
    ])
    text = f"📁 **{title}**\n📏 Size: {size_str}\n📌 Type: {type_emoji} {row['file_type']}"
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)

async def show_myfiles_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 0):
    user = update.effective_user
    query = getattr(update, 'callback_query', None)
    offset = page * PAGE_SIZE
    files = await get_user_files_paginated(user.id, offset, PAGE_SIZE)
    total = await get_user_files_count(user.id)
    total_pages = max(1, -(-total // PAGE_SIZE))
    keyboard = [[InlineKeyboardButton("🔍 Search", callback_data="main_search")]]
    for row in files:
        emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
        cnames = json.loads(row.get('custom_names') or '[]')
        name = cnames[0] if cnames else row['file_name']
        keyboard.append([InlineKeyboardButton(f"{emoji} {name[:30]}", callback_data=f"listfile_{row['id']}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("◀️", callback_data=f"page_{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1: nav.append(InlineKeyboardButton("▶️", callback_data=f"page_{page+1}"))
    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🏠 Home", callback_data="main_home")])
    markup = InlineKeyboardMarkup(keyboard)
    text = f"📂 **My Files** (Page {page+1}/{total_pages}) - {total} total"
    if query:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    context.user_data['page'] = page

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    if data == "main_myfiles":
        await show_myfiles_page(update, context, 0)
    elif data == "main_newfile":
        context.user_data['state'] = "awaiting_file"
        await query.edit_message_text("📤 Send a file now.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="main_home")]]))
    elif data == "main_search":
        context.user_data['state'] = "awaiting_search"
        await query.edit_message_text("🔍 Send search term:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="main_home")]]))
    elif data == "main_storage":
        size = await get_user_total_size(user.id)
        await query.edit_message_text(f"📊 Storage: {human_readable_size(size)}", reply_markup=get_main_menu())
    elif data == "main_settings":
        await query.edit_message_text("⚙️ Settings - Coming soon", reply_markup=get_main_menu())
    elif data == "main_home":
        await query.edit_message_text("🏠 Main Menu", reply_markup=get_main_menu())
    elif data.startswith("listfile_"):
        await show_file_options(update, context, int(data[9:]))
    elif data.startswith("showf_"):
        file_id = int(data[6:])
        await increment_view_count(file_id)
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
        await query.edit_message_text("🗑️ Are you sure?", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes", callback_data=f"confirmdel_{file_id}"), InlineKeyboardButton("No", callback_data="cancel_del")]
        ]))
    elif data.startswith("confirmdel_"):
        await delete_file(int(data[11:]))
        await query.edit_message_text("✅ File deleted.")
        await show_myfiles_page(update, context, context.user_data.get('page', 0))
    elif data.startswith("renamef_"):
        context.user_data['rename_id'] = int(data[8:])
        context.user_data['state'] = "awaiting_rename_text"
        await query.edit_message_text("Send the new name:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main_home")]]))
    elif data.startswith("addnamef_"):
        context.user_data['addname_id'] = int(data[9:])
        context.user_data['state'] = "awaiting_addname_text"
        await query.edit_message_text("Send additional name:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="main_home")]]))
    elif data.startswith("page_"):
        await show_myfiles_page(update, context, int(data[5:]))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    text = message.text or ""
    user = update.effective_user
    state = context.user_data.get('state')

    if message.photo or message.video or message.audio or message.voice or message.document:
        await handle_file(update, context)
        return

    if state == "awaiting_rename_text":
        rename_id = context.user_data.get('rename_id')
        if rename_id:
            row = await get_file_by_id(rename_id)
            if row:
                cnames = json.loads(row.get('custom_names') or '[]')
                if cnames:
                    cnames[0] = text.strip()
                else:
                    cnames = [text.strip()]
                await update_names(rename_id, cnames)
                await message.reply_text("✅ Name updated.")
                context.user_data.pop('rename_id', None)
                context.user_data['state'] = "main"
    elif state == "awaiting_addname_text":
        addname_id = context.user_data.get('addname_id')
        if addname_id:
            row = await get_file_by_id(addname_id)
            if row:
                cnames = json.loads(row.get('custom_names') or '[]')
                new_name = text.strip()
                if new_name not in cnames:
                    cnames.append(new_name)
                    await update_names(addname_id, cnames)
                    await message.reply_text("✅ Name added.")
                context.user_data.pop('addname_id', None)
                context.user_data['state'] = "main"
    elif state == "awaiting_search":
        # جستجوی ساده
        await message.reply_text(f"🔍 Search results for '{text}' (basic version)")
        context.user_data['state'] = "main"
    else:
        await message.reply_text("Use the menu.", reply_markup=get_main_menu())

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"ok": False}

async def main():
    global ptb_app
    if not TOKEN or not DATABASE_URL:
        logger.error("TOKEN and DATABASE_URL must be set!")
        return
    await get_pool()
    ptb_app = Application.builder().token(TOKEN).build()
    ptb_app.add_error_handler(error_handler)
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    ptb_app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_message))
    ptb_app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_file))
    await ptb_app.initialize()
    await ptb_app.start()
    await ptb_app.bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
