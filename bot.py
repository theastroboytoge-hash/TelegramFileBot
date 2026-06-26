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
WEBHOOK_PATH = f"/webhook/{TOKEN}"
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
    await update.message.reply_text("سلام! فایل بفرستید.")
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_membership(context.bot, update.effective_user.id):
        await update.message.reply_text("ابتدا در کانال عضو شوید.")
        return
    file = update.message.document
    context.user_data['pending_file'] = {'file_id': file.file_id, 'file_name': file.file_name or "file", 'file_type': 'document'}
    await update.message.reply_text("نام دلخواه فایل را ارسال کنید (یا /cancel):")
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if 'pending_file' in context.user_data:
        name = update.message.text.strip()
        if name == '/cancel':
            context.user_data.pop('pending_file')
            await update.message.reply_text("لغو شد.")
            return
        data = context.user_data.pop('pending_file')
        add_file(user.id, data['file_id'], data['file_name'], [name], data['file_type'])
        await update.message.reply_text(f"✅ فایل با نام '{name}' ذخیره شد.\nحالا در اینلاین سرچ کنید.")
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
    ptb_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    ptb_app.add_handler(MessageHandler(filters.TEXT & \
                                       filters.COMMAND, handle_text))
    ptb_app.add_handler(InlineQueryHandler(inline_query))
    ptb_app.add_handler(CallbackQueryHandler(button_callback))
    await ptb_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")
if __name__ == "__main__":
    asyncio.run(main())
    uvicorn.run(app, host="0.0.0.0", port=PORT)
