import os
import logging
import sqlite3
import threading
import time
import requests
from flask import Flask
from telegram import Update, InlineQueryResultCachedDocument
from telegram.ext import Application, CommandHandler, MessageHandler, filters, InlineQueryHandler, ContextTypes, ConversationHandler
TOKEN = os.environ.get("BOT_TOKEN")
PORT = int(os.environ.get('PORT', 5000))
APP_URL = os.environ.get("RENDER_URL")
CHANNEL_ID = "@dilemmapl"
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
if not APP_URL:
    APP_URL = f"https://localhost:{PORT}"
logging.basicConfig(level=logging.INFO)
DB_PATH = "bot_database.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        file_id TEXT NOT NULL,
        file_name TEXT NOT NULL,
        aliases TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    conn.commit()
    conn.close()
init_db()
def add_user(user_id, username=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()
def save_file_to_db(user_id, file_id, file_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO files (user_id, file_id, file_name, aliases) VALUES (?, ?, ?, ?)",
              (user_id, file_id, file_name, ""))
    conn.commit()
    conn.close()
def get_user_files(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT file_id, file_name, aliases FROM files WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows
def get_all_files():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, file_id, file_name FROM files")
    rows = c.fetchall()
    conn.close()
    return rows
def delete_file_by_name(user_id, file_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM files WHERE user_id=? AND file_name=?", (user_id, file_name))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted
def update_file_name(user_id, old_name, new_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE files SET file_name=? WHERE user_id=? AND file_name=?", (new_name, user_id, old_name))
    updated = c.rowcount > 0
    conn.commit()
    conn.close()
    return updated
def add_alias_to_file(user_id, file_name, new_alias):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT aliases FROM files WHERE user_id=? AND file_name=?", (user_id, file_name))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    aliases = row[0]
    if aliases:
        aliases += f",{new_alias}"
    else:
        aliases = new_alias
    c.execute("UPDATE files SET aliases=? WHERE user_id=? AND file_name=?", (aliases, user_id, file_name))
    conn.commit()
    conn.close()
    return True
def search_files_by_name(user_id, query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT file_id, file_name FROM files WHERE user_id=? AND file_name LIKE ?", (user_id, f"%{query}%"))
    rows = c.fetchall()
    c.execute("SELECT file_id, file_name FROM files WHERE user_id=? AND aliases LIKE ?", (user_id, f"%{query}%"))
    rows += c.fetchall()
    conn.close()
    seen = set()
    unique_rows = []
    for file_id, fname in rows:
        if file_id not in seen:
            seen.add(file_id)
            unique_rows.append((file_id, fname))
    return unique_rows
async def is_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False
app = Flask(__name__)
@app.route('/')
def home():
    return "ربات فعال است!"
def keep_alive():
    while True:
        try:
            requests.get(APP_URL)
            print("Keep-alive sent")
        except:
            print("Keep-alive failed")
        time.sleep(600)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)
    if not await is_member(update, context):
        await update.message.reply_text(
            f"❌ ابتدا باید در کانال {CHANNEL_ID} عضو شوید.\n"
            "لطفاً عضو شده و دوباره /start را بزنید."
        )
        return
    await update.message.reply_text(
        "سلام! 👋\n"
        "یک فایل بفرستید و سپس نام دلخواه را به‌صورت متن ارسال کنید.\n\n"
        "🔍 در هر چت، @نام_ربات_شما را تایپ کرده و نام فایل را جستجو کنید.\n"
        "📌 دستورات:\n"
        "/myfiles - لیست فایل‌های من\n"
        "/delete <نام> - حذف فایل\n"
        "/rename <نام قدیم> <نام جدید> - تغییر نام\n"
        "/addname <نام فایل> <نام اضافی> - افزودن نام مستعار\n"
        "/admin_all - (فقط ادمین) دیدن همه فایل‌ها"
    )
WAITING_FOR_NAME = 1
async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_member(update, context):
        await update.message.reply_text(f"❌ ابتدا در کانال {CHANNEL_ID} عضو شوید.")
        return
    if not update.message.document and not update.message.photo and not update.message.video and not update.message.audio:
        await update.message.reply_text("❌ لطفاً یک فایل معتبر بفرستید.")
        return
    if update.message.document:
        file_id = update.message.document.file_id
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.video:
        file_id = update.message.video.file_id
    elif update.message.audio:
        file_id = update.message.audio.file_id
    else:
        await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود.")
        return
    context.user_data['temp_file_id'] = file_id
    await update.message.reply_text("✅ فایل دریافت شد. لطفاً یک **نام** برای آن وارد کنید.")
    return WAITING_FOR_NAME
async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ نام نمی‌تواند خالی باشد.")
        return WAITING_FOR_NAME
    file_id = context.user_data.get('temp_file_id')
    if not file_id:
        await update.message.reply_text("❌ خطا: دوباره فایل را بفرستید.")
        return ConversationHandler.END
    save_file_to_db(user.id, file_id, name)
    add_user(user.id, user.username)
    await update.message.reply_text(f"✅ فایل با نام '{name}' ذخیره شد.\nاکنون در اینلاین قابل جستجو است.")
    context.user_data.clear()
    return ConversationHandler.END
async def inline_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user_id = update.inline_query.from_user.id
    if not await is_member(update, context):
        await update.inline_query.answer([], cache_time=5)
        return
    files = search_files_by_name(user_id, query)
    results = []
    for file_id, file_name in files:
        results.append(
            InlineQueryResultCachedDocument(
                id=file_id,
                title=file_name,
                document_file_id=file_id,
                description=f"📁 {file_name}"
            )
        )
    await update.inline_query.answer(results, cache_time=5)
async def myfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_member(update, context):
        await update.message.reply_text(f"❌ ابتدا در کانال {CHANNEL_ID} عضو شوید.")
        return
    files = get_user_files(user.id)
    if not files:
        await update.message.reply_text("📭 شما هیچ فایلی ذخیره نکرده‌اید.")
        return
    text = "📂 **فایل‌های شما:**\n"
    for file_id, fname, aliases in files:
        text += f"- {fname}"
        if aliases:
            text += f" (نام‌های دیگر: {aliases})"
        text += "\n"
    await update.message.reply_text(text)
async def delete_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_member(update, context):
        await update.message.reply_text(f"❌ ابتدا در کانال {CHANNEL_ID} عضو شوید.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ استفاده: /delete <نام>")
        return
    file_name = " ".join(args)
    if delete_file_by_name(user.id, file_name):
        await update.message.reply_text(f"✅ فایل '{file_name}' حذف شد.")
    else:
        await update.message.reply_text(f"❌ فایلی با نام '{file_name}' پیدا نشد.")
async def rename_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_member(update, context):
        await update.message.reply_text(f"❌ ابتدا در کانال {CHANNEL_ID} عضو شوید.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ استفاده: /rename <نام قدیم> <نام جدید>")
        return
    old_name = args[0]
    new_name = " ".join(args[1:])
    if update_file_name(user.id, old_name, new_name):
        await update.message.reply_text(f"✅ نام فایل از '{old_name}' به '{new_name}' تغییر یافت.")
    else:
        await update.message.reply_text(f"❌ فایلی با نام '{old_name}' پیدا نشد.")
async def add_alias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_member(update, context):
        await update.message.reply_text(f"❌ ابتدا در کانال {CHANNEL_ID} عضو شوید.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ استفاده: /addname <نام فایل> <نام جدید>")
        return
    file_name = args[0]
    new_alias = " ".join(args[1:])
    if add_alias_to_file(user.id, file_name, new_alias):
        await update.message.reply_text(f"✅ نام مستعار '{new_alias}' به فایل '{file_name}' اضافه شد.")
    else:
        await update.message.reply_text(f"❌ فایلی با نام '{file_name}' پیدا نشد.")
async def admin_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id != ADMIN_ID:
        await update.message.reply_text("⛔ شما دسترسی به این دستور ندارید.")
        return
    all_files = get_all_files()
    if not all_files:
        await update.message.reply_text("📭 هیچ فایلی در دیتابیس وجود ندارد.")
        return
    text = "📂 **همه فایل‌های ذخیره‌شده:**\n"
    for uid, file_id, fname in all_files:
        text += f"- کاربر {uid}: {fname}\n"
    if len(text) > 4000:
        await update.message.reply_text("تعداد فایل‌ها زیاد است، لطفاً از طریق دیتابیس بررسی کنید.")
    else:
        await update.message.reply_text(text)
application = Application.builder().token(TOKEN).build()
conv_handler = ConversationHandler(
    entry_points=[MessageHandler(filters.ALL & ~filters.COMMAND, receive_file)],
    states={
        WAITING_FOR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)]
    },
    fallbacks=[]
)
application.add_handler(conv_handler)
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("myfiles", myfiles))
application.add_handler(CommandHandler("delete", delete_file))
application.add_handler(CommandHandler("rename", rename_file))
application.add_handler(CommandHandler("addname", add_alias))
application.add_handler(CommandHandler("admin_all", admin_all))
application.add_handler(InlineQueryHandler(inline_handler))
def run_bot():
    application.run_polling(allowed_updates=Update.ALL_TYPES)
if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    from threading import Thread
    Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=PORT)
