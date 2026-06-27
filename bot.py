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
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_USERNAME = "@dilemmapl"
PORT = int(os.getenv("PORT", 10000))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME', 'your-app.onrender.com')}{WEBHOOK_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL")
app = FastAPI()
ptb_app = None
db_pool = None
async def get_pool():
    global db_pool
    if db_pool is None:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS files (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    file_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    custom_names JSONB NOT NULL DEFAULT '[]',
                    file_type TEXT NOT NULL
                )
            ''')
    return db_pool
async def add_file(user_id, file_id, file_name, custom_names, file_type):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO files (user_id, file_id, file_name, custom_names, file_type) VALUES ($1, $2, $3, $4, $5)",
            user_id, file_id, file_name, json.dumps(custom_names), file_type
        )
async def get_user_files(user_id):
    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id == ADMIN_ID:
            rows = await conn.fetch("SELECT * FROM files")
        else:
            rows = await conn.fetch("SELECT * FROM files WHERE user_id=$1", user_id)
        return rows
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("برای استفاده ابتدا در کانال @dilemmapl عضو شوید.")
        return
    await update.message.reply_text("سلام!\nعکس، فیلم، آهنگ، ویس یا هر فایل دیگری بفرستید.")
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.message
    if 'pending_file' in context.user_data or 'rename_id' in context.user_data or 'addname_id' in context.user_data:
        text = message.text or message.caption
        if not text:
            await message.reply_text("لطفاً نام را به صورت متن ارسال کنید.")
            return
        if 'pending_file' in context.user_data:
            name = text.strip()
            if name == '/cancel':
                context.user_data.pop('pending_file')
                await message.reply_text("لغو شد.")
                return
            data = context.user_data.pop('pending_file')
            await add_file(user.id, data['file_id'], data['file_name'], [name], data['file_type'])
            await message.reply_text(f"✅ فایل با نام '{name}' ذخیره شد.\nحالا در اینلاین سرچ کنید.")
        elif 'rename_id' in context.user_data:
            db_id = context.user_data.pop('rename_id')
            files = await get_user_files(user.id)
            for row in files:
                if row['id'] == db_id:
                    cnames = json.loads(row['custom_names'])
                    cnames[0] = text.strip()
                    await update_names(db_id, cnames)
                    await message.reply_text("نام تغییر کرد.")
                    return
            await message.reply_text("فایل یافت نشد.")
        elif 'addname_id' in context.user_data:
            db_id = context.user_data.pop('addname_id')
            files = await get_user_files(user.id)
            for row in files:
                if row['id'] == db_id:
                    cnames = json.loads(row['custom_names'])
                    if text.strip() not in cnames:
                        cnames.append(text.strip())
                    await update_names(db_id, cnames)
                    await message.reply_text("نام اضافه شد.")
                    return
            await message.reply_text("فایل یافت نشد.")
        return
    if not await check_membership(context.bot, user.id):
        await message.reply_text("ابتدا در کانال عضو شوید.")
        return
    file = None
    file_name = "file"
    file_type = "document"
    if message.document:
        file = message.document
        file_name = file.file_name or "document"
        file_type = "document"
    elif message.photo:
        file = message.photo[-1]
        file_name = "photo.jpg"
        file_type = "photo"
    elif message.video:
        file = message.video
        file_name = "video.mp4"
        file_type = "video"
    elif message.audio:
        file = message.audio
        file_name = file.file_name or "audio"
        file_type = "audio"
    elif message.voice:
        file = message.voice
        file_name = "voice.ogg"
        file_type = "voice"
    else:
        await message.reply_text("فقط فایل، عکس، ویدیو، آهنگ یا ویس ارسال کنید.")
        return
    context.user_data['pending_file'] = {'file_id': file.file_id, 'file_name': file_name, 'file_type': file_type}
    await message.reply_text("✅ فایل دریافت شد.\nنام دلخواه خود را ارسال کنید (یا /cancel):")
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop('pending_file', None)
    context.user_data.pop('rename_id', None)
    context.user_data.pop('addname_id', None)
    await update.message.reply_text("عملیات لغو شد.")
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
async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("عضو کانال شوید.")
        return
    files = await get_user_files(update.effective_user.id)
    if not files:
        await update.message.reply_text("فایلی ندارید.")
        return
    keyboard = [[InlineKeyboardButton(json.loads(row['custom_names'])[0], callback_data=f"file_{row['id']}")] for row in files]
    await update.message.reply_text("فایل‌های شما:", reply_markup=InlineKeyboardMarkup(keyboard))
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("file_"):
        db_id = int(data[5:])
        keyboard = [[InlineKeyboardButton("ارسال", switch_inline_query_current_chat="")],
                    [InlineKeyboardButton("تغییر نام", callback_data=f"rename_{db_id}")],
                    [InlineKeyboardButton("اضافه کردن نام", callback_data=f"addname_{db_id}")],
                    [InlineKeyboardButton("حذف", callback_data=f"del_{db_id}")]]
        await query.edit_message_text("انتخاب عملیات:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("del_"):
        db_id = int(data[4:])
        await delete_file(db_id)
        await query.edit_message_text("فایل حذف شد.")
    elif data.startswith("rename_"):
        context.user_data['rename_id'] = int(data[7:])
        await query.edit_message_text("نام جدید را ارسال کنید:")
    elif data.startswith("addname_"):
        context.user_data['addname_id'] = int(data[8:])
        await query.edit_message_text("نام اضافه را ارسال کنید:")
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
    ptb_app.add_handler(CommandHandler("myfiles", myfiles))
    ptb_app.add_handler(CommandHandler("cancel", cancel))
    ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
    config = uvicorn.Config(app, host="0.0.0.0", port=PORT)
    server = uvicorn.Server(config)
    await server.serve()
if __name__ == "__main__":
    asyncio.run(main())
