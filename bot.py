import logging
import sqlite3
import json
import os
from fastapi import FastAPI, Request
from telegram import Update, InlineQueryResultCachedDocument, InlineKeyboardButton, InlineKeyboardMarkup
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
DB_FILE = "bot_files.db"
FILES_DIR = "uploaded_files"
os.makedirs(FILES_DIR, exist_ok=True)
app = FastAPI()
ptb_app = None
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER, file_id TEXT, file_name TEXT, custom_names TEXT, file_type TEXT)''')
    conn.commit()
    conn.close()
def add_file(user_id, file_id, file_name, custom_names, file_type):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO files (user_id, file_id, file_name, custom_names, file_type) VALUES (?, ?, ?, ?, ?)", (user_id, file_id, file_name, json.dumps(custom_names), file_type))
    conn.commit()
    conn.close()
def get_user_files(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if user_id == ADMIN_ID:
        c.execute("SELECT * FROM files")
    else:
        c.execute("SELECT * FROM files WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows
def delete_file(file_db_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM files WHERE id=?", (file_db_id,))
    conn.commit()
    conn.close()
def update_names(file_db_id, custom_names):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE files SET custom_names=? WHERE id=?", (json.dumps(custom_names), file_db_id))
    conn.commit()
    conn.close()
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
            add_file(user.id, data['file_id'], data['file_name'], [name], data['file_type'])
            await message.reply_text(f"✅ فایل با نام '{name}' ذخیره شد.\nحالا در اینلاین سرچ کنید.")
        elif 'rename_id' in context.user_data:
            db_id = context.user_data.pop('rename_id')
            files = get_user_files(user.id)
            for row in files:
                if row[0] == db_id:
                    cnames = json.loads(row[4])
                    cnames[0] = text.strip()
                    update_names(db_id, cnames)
                    await message.reply_text("نام تغییر کرد.")
                    return
            await message.reply_text("فایل یافت نشد.")
        elif 'addname_id' in context.user_data:
            db_id = context.user_data.pop('addname_id')
            files = get_user_files(user.id)
            for row in files:
                if row[0] == db_id:
                    cnames = json.loads(row[4])
                    if text.strip() not in cnames:
                        cnames.append(text.strip())
                    update_names(db_id, cnames)
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
    files = get_user_files(user_id)
    for row in files:
        db_id, _, fid, _, cnames_json, _ = row
        cnames = json.loads(cnames_json)
        if not query or any(query in n.lower() for n in cnames):
            results.append(InlineQueryResultCachedDocument(id=str(db_id), document_file_id=fid, title=cnames[0]))
    await update.inline_query.answer(results, cache_time=0)
async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("عضو کانال شوید.")
        return
    files = get_user_files(update.effective_user.id)
    if not files:
        await update.message.reply_text("فایلی ندارید.")
        return
    keyboard = [[InlineKeyboardButton(json.loads(row[4])[0], callback_data=f"file_{row[0]}")] for row in files]
    await update.message.reply_text("فایل‌های شما:", reply_markup=InlineKeyboardMarkup(keyboard))
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("file_"):
        db_id = int(data[5:])
        keyboard = [[InlineKeyboardButton("ارسال", switch_inline_query_current_chat="")],[InlineKeyboardButton("تغییر نام", callback_data=f"rename_{db_id}")],[InlineKeyboardButton("اضافه کردن نام", callback_data=f"addname_{db_id}")],[InlineKeyboardButton("حذف", callback_data=f"del_{db_id}")]]
        await query.edit_message_text("انتخاب عملیات:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("del_"):
        db_id = int(data[4:])
        delete_file(db_id)
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
    init_db()
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
if __name__ == "__main__":
    asyncio.run(main())
    uvicorn.run(app, host="0.0.0.0", port=PORT)
