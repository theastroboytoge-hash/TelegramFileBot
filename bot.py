import logging
import json
import os
import asyncpg
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultCachedAudio, InlineQueryResultCachedVoice, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
FILE_TYPE_EMOJI = {
    "photo": "🖼️",
    "video": "📽️",
    "audio": "🎵",
    "voice": "🎙️",
    "document": "📄"
}
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
    return db_pool
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
async def enter_state(update: Update, context: ContextTypes.DEFAULT_TYPE, state: str):
    user = update.effective_user
    chat_id = update.effective_chat.id
    context.user_data['state'] = state
    if state == "main":
        await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)
    elif state == "awaiting_file":
        await update.message.reply_text("Please send the file you want to save.", reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_name":
        await update.message.reply_text("File received. Send the name for this file (or /cancel):", reply_markup=BACK_KEYBOARD)
    elif state == "myfiles_list":
        files = await get_user_files(user.id)
        if not files:
            await update.message.reply_text("You have no files.", reply_markup=BACK_KEYBOARD)
            return
        keyboard = []
        for row in files:
            emoji = FILE_TYPE_EMOJI.get(row['file_type'], "📄")
            name = json.loads(row['custom_names'])[0]
            keyboard.append([InlineKeyboardButton(f"{emoji} {name}", callback_data=f"listfile_{row['id']}")])
        markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text("Your files:", reply_markup=markup)
        context.user_data['myfiles_list_msg'] = msg.message_id
        await update.message.reply_text("Select a file or press Back.", reply_markup=BACK_KEYBOARD)
    elif state == "file_options":
        file_id = context.user_data.get('current_file_id')
        if not file_id:
            await enter_state(update, context, "myfiles_list")
            return
        row = await get_file_by_id(file_id)
        if not row:
            await update.message.reply_text("File not found.", reply_markup=BACK_KEYBOARD)
            return
        cnames = json.loads(row['custom_names'])
        title = cnames[0]
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Show", callback_data=f"showf_{file_id}")],
            [InlineKeyboardButton("Rename", callback_data=f"renamef_{file_id}"),
             InlineKeyboardButton("Add Name", callback_data=f"addnamef_{file_id}")],
            [InlineKeyboardButton("Delete", callback_data=f"delf_{file_id}")]
        ])
        if 'file_options_msg' in context.user_data:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=context.user_data['file_options_msg'],
                    text=f"File: {title}",
                    reply_markup=markup
                )
                return
            except:
                pass
        msg = await update.message.reply_text(f"File: {title}", reply_markup=markup)
        context.user_data['file_options_msg'] = msg.message_id
    elif state == "awaiting_rename_text":
        await update.message.reply_text("Send the new name:", reply_markup=BACK_KEYBOARD)
    elif state == "awaiting_addname_text":
        await update.message.reply_text("Send additional name:", reply_markup=BACK_KEYBOARD)
async def go_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = context.user_data.get('nav_history', [])
    if history:
        prev_state = history.pop()
        context.user_data['nav_history'] = history
        context.user_data.pop('pending_file', None)
        context.user_data.pop('rename_id', None)
        context.user_data.pop('addname_id', None)
        context.user_data.pop('current_file_id', None)
        context.user_data.pop('myfiles_list_msg', None)
        context.user_data.pop('file_options_msg', None)
        context.user_data['state'] = prev_state
        await enter_state(update, context, prev_state)
    else:
        context.user_data.pop('pending_file', None)
        context.user_data.pop('rename_id', None)
        context.user_data.pop('addname_id', None)
        context.user_data.pop('current_file_id', None)
        context.user_data.pop('myfiles_list_msg', None)
        context.user_data.pop('file_options_msg', None)
        context.user_data['state'] = "main"
        await enter_state(update, context, "main")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("Please join @dilemmapl first.")
        return
    context.user_data['state'] = "main"
    context.user_data['nav_history'] = []
    await update.message.reply_text("Welcome! Choose an option:", reply_markup=MAIN_KEYBOARD)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    text = message.text or message.caption
    state = context.user_data.get('state', 'main')
    if text and text.strip() == "Back":
        await go_back(update, context)
        return
    if not await check_membership(context.bot, user.id):
        await message.reply_text("Please join @dilemmapl first.")
        return
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
            await message.reply_text("Please use the menu buttons.", reply_markup=MAIN_KEYBOARD)
        return
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
            await message.reply_text("Please send a file (photo, video, audio, voice, or document).", reply_markup=BACK_KEYBOARD)
            return
        context.user_data['pending_file'] = {
            'file_id': file.file_id,
            'file_name': file_name,
            'file_type': file_type,
            'file_size': file_size
        }
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["awaiting_file"]
        await enter_state(update, context, "awaiting_name")
        return
    elif state == "awaiting_name":
        if not text:
            await message.reply_text("Please send a text name.", reply_markup=BACK_KEYBOARD)
            return
        name = text.strip()
        if name.lower() == '/cancel':
            context.user_data.pop('pending_file', None)
            await go_back(update, context)
            return
        data = context.user_data.pop('pending_file', None)
        if data:
            await add_file(user.id, data['file_id'], data['file_name'], [name], data['file_type'], data['file_size'])
            await message.reply_text(f"File saved as '{name}'. You can now search it inline.", reply_markup=MAIN_KEYBOARD)
            context.user_data['state'] = "main"
            context.user_data['nav_history'] = []
        else:
            await message.reply_text("No pending file.", reply_markup=MAIN_KEYBOARD)
            context.user_data['state'] = "main"
        return
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
        return
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
        return
    else:
        await message.reply_text("Unknown state. Restarting.", reply_markup=MAIN_KEYBOARD)
        context.user_data['state'] = "main"
        context.user_data['nav_history'] = []
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_file', None)
    context.user_data.pop('rename_id', None)
    context.user_data.pop('addname_id', None)
    context.user_data.pop('current_file_id', None)
    context.user_data.pop('myfiles_list_msg', None)
    context.user_data.pop('file_options_msg', None)
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
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["myfiles_list"]
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
        await delete_file(file_id)
        await query.answer("File deleted.")
        await go_back(update, context)
    elif data.startswith("renamef_"):
        file_id = int(data[8:])
        context.user_data['rename_id'] = file_id
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["file_options"]
        await enter_state(update, context, "awaiting_rename_text")
    elif data.startswith("addnamef_"):
        file_id = int(data[9:])
        context.user_data['addname_id'] = file_id
        context.user_data['nav_history'] = context.user_data.get('nav_history', []) + ["file_options"]
        await enter_state(update, context, "awaiting_addname_text")
async def myfiles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await ptb_app.initialize()
    ptb_app.add_handler(CommandHandler("start", start))
    ptb_app.add_handler(CommandHandler("myfiles", myfiles_command))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
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
