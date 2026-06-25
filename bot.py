import logging,os,asyncio,sqlite3
from telegram import Update,InlineQueryResultCachedDocument,InlineQueryResultCachedVideo,InlineQueryResultCachedAudio,InlineQueryResultCachedVoice,InlineQueryResultCachedPhoto
from telegram.ext import Application,CommandHandler,MessageHandler,InlineQueryHandler,ContextTypes,filters
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',level=logging.INFO)
logger=logging.getLogger(__name__)
TOKEN=os.environ['BOT_TOKEN']
ADMIN_ID=int(os.environ.get('ADMIN_ID',0))
CHANNEL_USERNAME='@dilemmapl'
DB_DIR='/data'
DB_PATH=os.path.join(DB_DIR,'bot.db') if os.path.isdir(DB_DIR) else 'bot.db'
def init_db():
    conn=sqlite3.connect(DB_PATH,check_same_thread=False)
    cur=conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        tg_file_id TEXT NOT NULL,
        file_type TEXT NOT NULL
    )''')
    cur.execute('''CREATE TABLE IF NOT EXISTS aliases(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        file_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        UNIQUE(user_id,name)
    )''')
    conn.commit()
    return conn
conn=init_db()
def get_cursor():
    return conn.cursor()
async def check_membership(update:Update,context:ContextTypes.DEFAULT_TYPE)->bool:
    user=update.effective_user
    try:
        member=await context.bot.get_chat_member(chat_id=CHANNEL_USERNAME,user_id=user.id)
        if member.status in ['member','administrator','creator']:
            return True
        else:
            await update.effective_message.reply_text('برای استفاده از ربات باید در کانال @dilemmapl عضو باشید.')
            return False
    except Exception:
        await update.effective_message.reply_text('برای استفاده از ربات باید در کانال @dilemmapl عضو باشید.')
        return False
async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('سلام! فایل بفرستید تا ذخیره کنم. پس از ذخیره‌سازی می‌توانید در حالت اینلاین جستجو کنید.')
async def cancel(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if 'pending_file' in context.user_data:
        del context.user_data['pending_file']
        await update.message.reply_text('عملیات لغو شد.')
    else:
        await update.message.reply_text('هیچ عملیات در حال انتظاری نیست.')
async def handle_file(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update,context):
        return
    if 'pending_file' in context.user_data:
        await update.message.reply_text('شما در حال ذخیره‌سازی فایل قبلی هستید. با /cancel لغو کنید.')
        return
    msg=update.message
    file_id=None
    file_type=None
    if msg.document:
        file_id=msg.document.file_id
        file_type='document'
    elif msg.video:
        file_id=msg.video.file_id
        file_type='video'
    elif msg.audio:
        file_id=msg.audio.file_id
        file_type='audio'
    elif msg.voice:
        file_id=msg.voice.file_id
        file_type='voice'
    elif msg.photo:
        file_id=msg.photo[-1].file_id
        file_type='photo'
    else:
        await update.message.reply_text('نوع فایل پشتیبانی نمی‌شود.')
        return
    context.user_data['pending_file']={'file_id':file_id,'file_type':file_type}
    await update.message.reply_text('لطفاً یک نام برای فایل بفرستید (یا /cancel):')
async def handle_text(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if 'pending_file' not in context.user_data:
        await update.message.reply_text('دستوری ندارم. فایل بفرستید.')
        return
    if not await check_membership(update,context):
        return
    name=update.message.text.strip()
    if not name:
        await update.message.reply_text('نام نمی‌تواند خالی باشد.')
        return
    user_id=update.effective_user.id
    pending=context.user_data['pending_file']
    cur=get_cursor()
    try:
        cur.execute('INSERT INTO files(user_id,tg_file_id,file_type) VALUES(?,?,?)',(user_id,pending['file_id'],pending['file_type']))
        file_id=cur.lastrowid
        cur.execute('INSERT INTO aliases(user_id,file_id,name) VALUES(?,?,?)',(user_id,file_id,name))
        conn.commit()
        await update.message.reply_text(f'فایل با نام "{name}" ذخیره شد. حالا در اینلاین قابل جستجو است.')
    except sqlite3.IntegrityError:
        await update.message.reply_text('این نام قبلاً برای شما ثبت شده. نام دیگری انتخاب کنید.')
        return
    finally:
        del context.user_data['pending_file']
async def delete_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update,context):
        return
    if not context.args:
        await update.message.reply_text('لطفاً نام فایل را وارد کنید: /delete name')
        return
    name=' '.join(context.args)
    user_id=update.effective_user.id
    cur=get_cursor()
    cur.execute('SELECT file_id FROM aliases WHERE user_id=? AND name=?',(user_id,name))
    row=cur.fetchone()
    if not row:
        await update.message.reply_text('فایلی با این نام یافت نشد.')
        return
    file_id=row[0]
    cur.execute('DELETE FROM aliases WHERE user_id=? AND file_id=?',(user_id,file_id))
    cur.execute('DELETE FROM files WHERE id=? AND user_id=?',(file_id,user_id))
    conn.commit()
    await update.message.reply_text(f'فایل با نام "{name}" حذف شد.')
async def rename_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update,context):
        return
    if len(context.args)<2:
        await update.message.reply_text('استفاده: /rename oldname newname')
        return
    old_name=context.args[0]
    new_name=context.args[1]
    user_id=update.effective_user.id
    cur=get_cursor()
    cur.execute('SELECT id FROM aliases WHERE user_id=? AND name=?',(user_id,old_name))
    if not cur.fetchone():
        await update.message.reply_text('فایلی با نام قدیمی یافت نشد.')
        return
    cur.execute('SELECT id FROM aliases WHERE user_id=? AND name=?',(user_id,new_name))
    if cur.fetchone():
        await update.message.reply_text('نام جدید قبلاً وجود دارد.')
        return
    cur.execute('UPDATE aliases SET name=? WHERE user_id=? AND name=?',(new_name,user_id,old_name))
    conn.commit()
    await update.message.reply_text(f'نام "{old_name}" به "{new_name}" تغییر یافت.')
async def addalias_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not await check_membership(update,context):
        return
    if len(context.args)<2:
        await update.message.reply_text('استفاده: /addalias existing_name new_name')
        return
    exist_name=context.args[0]
    new_name=context.args[1]
    user_id=update.effective_user.id
    cur=get_cursor()
    cur.execute('SELECT file_id FROM aliases WHERE user_id=? AND name=?',(user_id,exist_name))
    row=cur.fetchone()
    if not row:
        await update.message.reply_text('فایلی با نام موجود یافت نشد.')
        return
    file_id=row[0]
    try:
        cur.execute('INSERT INTO aliases(user_id,file_id,name) VALUES(?,?,?)',(user_id,file_id,new_name))
        conn.commit()
        await update.message.reply_text(f'نام "{new_name}" به فایل اضافه شد.')
    except sqlite3.IntegrityError:
        await update.message.reply_text('این نام جدید قبلاً وجود دارد.')
async def allfiles_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('دسترسی ندارید.')
        return
    cur=get_cursor()
    cur.execute('''SELECT f.id,f.user_id,f.file_type,GROUP_CONCAT(a.name,', ') as names 
                   FROM files f JOIN aliases a ON f.id=a.file_id 
                   GROUP BY f.id ORDER BY f.user_id,f.id''')
    rows=cur.fetchall()
    if not rows:
        await update.message.reply_text('هیچ فایلی آپلود نشده.')
        return
    text_lines=[]
    for r in rows:
        text_lines.append(f'ID:{r[0]} | User:{r[1]} | Type:{r[2]} | Names: {r[3]}')
    msg='\n'.join(text_lines)
    for chunk in [msg[i:i+4000] for i in range(0,len(msg),4000)]:
        await update.message.reply_text(chunk)
async def inline_query(update:Update,context:ContextTypes.DEFAULT_TYPE):
    query=update.inline_query
    user=query.from_user
    try:
        member=await context.bot.get_chat_member(CHANNEL_USERNAME,user.id)
        if member.status not in ['member','administrator','creator']:
            await query.answer([],switch_pm_text='برای استفاده عضو کانال شوید',switch_pm_parameter='join')
            return
    except Exception:
        await query.answer([],switch_pm_text='برای استفاده عضو کانال شوید',switch_pm_parameter='join')
        return
    search=query.query.strip()
    pattern=f'%{search}%' if search else '%'
    cur=get_cursor()
    cur.execute('''
        SELECT f.id,f.tg_file_id,f.file_type,
               (SELECT a2.name FROM aliases a2 WHERE a2.file_id=f.id LIMIT 1) as display_name
        FROM files f
        WHERE f.user_id=? AND EXISTS (
            SELECT 1 FROM aliases a WHERE a.file_id=f.id AND a.name LIKE ?
        )
        LIMIT 50
    ''',(user.id,pattern))
    rows=cur.fetchall()
    results=[]
    for r in rows:
        file_id=r[1]
        file_type=r[2]
        name=r[3]
        if file_type=='document':
            results.append(InlineQueryResultCachedDocument(id=str(r[0]),title=name,document_file_id=file_id))
        elif file_type=='video':
            results.append(InlineQueryResultCachedVideo(id=str(r[0]),title=name,video_file_id=file_id))
        elif file_type=='audio':
            results.append(InlineQueryResultCachedAudio(id=str(r[0]),title=name,audio_file_id=file_id))
        elif file_type=='voice':
            results.append(InlineQueryResultCachedVoice(id=str(r[0]),title=name,voice_file_id=file_id))
        elif file_type=='photo':
            results.append(InlineQueryResultCachedPhoto(id=str(r[0]),title=name,photo_file_id=file_id))
        else:
            continue
    await query.answer(results,cache_time=0)
def main():
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',start))
    app.add_handler(CommandHandler('cancel',cancel))
    app.add_handler(CommandHandler('delete',delete_command))
    app.add_handler(CommandHandler('rename',rename_command))
    app.add_handler(CommandHandler('addalias',addalias_command))
    app.add_handler(CommandHandler('allfiles',allfiles_command))
    app.add_handler(MessageHandler(filters.Document.ALL|filters.VIDEO|filters.AUDIO|filters.VOICE|filters.PHOTO,handle_file))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_text))
    app.add_handler(InlineQueryHandler(inline_query))
    port=int(os.environ.get('PORT',8443))
    webhook_url=os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        hostname=os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        if hostname:
            webhook_url=f'https://{hostname}/webhook'
    if webhook_url:
        app.run_webhook(listen='0.0.0.0',port=port,webhook_url=webhook_url,path='/webhook',drop_pending_updates=True)
    else:
        app.run_polling(drop_pending_updates=True)
if __name__=='__main__':
    main()
