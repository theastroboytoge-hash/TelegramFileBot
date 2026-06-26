import logging
import sqlite3
import json
import os
from telegram import Update, InlineQueryResultCachedDocument, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, InlineQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
TOKEN = "YOUR_TOKEN_HERE"
ADMIN_ID = YOUR_ADMIN_TELEGRAM_ID
CHANNEL_USERNAME = "@dilemmapl"
DB_FILE = "bot_files.db"
FILES_DIR = "uploaded_files"
os.makedirs(FILES_DIR, exist_ok=True)
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
    user = update.effective_user
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text("برای استفاده از بات ابتدا در کانال @dilemmapl عضو شوید.")
        return
    await update.message.reply_text("سلام! فایل بفرستید و نام دلخواه وارد کنید. از /myfiles برای مدیریت استفاده کنید.")
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text("ابتدا در @dilemmapl عضو شوید.")
        return
    file = update.message.document
    file_id = file.file_id
    file_name = file.file_name or "file"
    context.user_data['pending_file'] = {'file_id': file_id, 'file_name': file_name, 'file_type': 'document'}
    await update.message.reply_text(f"فایل دریافت شد. نام دلخواه را ارسال کنید (یا /cancel):")
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if 'pending_file' in context.user_data:
        name = update.message.text.strip()
        if name == '/cancel':
            context.user_data.pop('pending_file')
            await update.message.reply_text("لغو شد.")
            return
        data = context.user_data.pop('pending_file')
        custom_names = [name]
        add_file(user.id, data['file_id'], data['file_name'], custom_names, data['file_type'])
        await update.message.reply_text(f"فایل با نام '{name}' ذخیره شد. حالا در اینلاین سرچ کنید.")
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    user_id = update.inline_query.from_user.id
    results = []
    files = get_user_files(user_id)
    for row in files:
        db_id, uid, fid, fname, cnames_json, ftype = row
        cnames = json.loads(cnames_json)
        if not query or any(query.lower() in n.lower() for n in cnames):
            results.append(InlineQueryResultCachedDocument(id=str(db_id), document_file_id=fid, title=cnames[0], description=fname))
    await update.inline_query.answer(results, cache_time=0)
async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await check_membership(context.bot, user.id):
        await update.message.reply_text("عضو کانال شوید.")
        return
    files = get_user_files(user.id)
    if not files:
        await update.message.reply_text("فایلی ندارید.")
        return
    keyboard = []
    for row in files:
        db_id = row[0]
        cnames = json.loads(row[4])
        btn_text = cnames[0]
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"file_{db_id}")])
    await update.message.reply_text("فایل‌های شما:", reply_markup=InlineKeyboardMarkup(keyboard))
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("file_"):
        db_id = int(data[5:])
        keyboard = [[InlineKeyboardButton("ارسال", switch_inline_query_current_chat="")],
                    [InlineKeyboardButton("تغییر نام", callback_data=f"rename_{db_id}")],
                    [InlineKeyboardButton("نام جدید اضافه", callback_data=f"addname_{db_id}")],
                    [InlineKeyboardButton("حذف", callback_data=f"del_{db_id}")]]
        await query.edit_message_text("عملیات:", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.startswith("rename_"):
        db_id = int(data[7:])
        context.user_data['rename_id'] = db_id
        await query.edit_message_text("نام جدید را ارسال کنید:")
    elif data.startswith("addname_"):
        db_id = int(data[8:])
        context.user_data['addname_id'] = db_id
        await query.edit_message_text("نام اضافه را ارسال کنید:")
    elif data.startswith("del_"):
        db_id = int(data[4:])
        delete_file(db_id)
        await query.edit_message_text("فایل حذف شد.")
async def handle_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'rename_id' in context.user_data:
        db_id = context.user_data.pop('rename_id')
        new_name = update.message.text.strip()
        files = get_user_files(update.effective_user.id)
        for row in files:
            if row[0] == db_id:
                cnames = json.loads(row[4])
                cnames[0] = new_name
                update_names(db_id, cnames)
                await update.message.reply_text("نام تغییر کرد.")
                return
async def handle_addname_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'addname_id' in context.user_data:
        db_id = context.user_data.pop('addname_id')
        new_name = update.message.text.strip()
        files = get_user_files(update.effective_user.id)
        for row in files:
            if row[0] == db_id:
                cnames = json.loads(row[4])
                if new_name not in cnames:
                    cnames.append(new_name)
                update_names(db_id, cnames)
                await update.message.reply_text("نام اضافه شد.")
                return
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myfiles", myfiles))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_text))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_rename_text)) # reuse text handler
    app.add_handler(MessageHandler(filters.TEXT & \~filters.COMMAND, handle_addname_text))
    app.run_polling()
if __name__ == '__main__':
    main()
