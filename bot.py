### slash_vpn_bot/bot.py
import os, asyncio, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                           ContextTypes, filters, ConversationHandler)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
import ai_gen, storage, threads_api

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN = os.environ['BOT_TOKEN']
ADMIN_IDS_RAW = os.environ.get('ADMIN_IDS', '')
ADMIN_IDS = set(int(x.strip()) for x in ADMIN_IDS_RAW.split(',') if x.strip())

scheduler = AsyncIOScheduler()

# Состояния ConversationHandler для загрузки картинки
WAIT_LOGIN_FOR_IMAGE, WAIT_PHOTO = range(2)
# Состояния для ручных cookies
WAIT_MANUAL_LOGIN, WAIT_MANUAL_SESSION, WAIT_MANUAL_CSRF = range(10, 13)


def is_admin(update: Update) -> bool:
    if not ADMIN_IDS:
        return True  # если список пуст — пускаем всех
    return update.effective_user.id in ADMIN_IDS


# --- /start ---
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 *SLASH VPN Bot*\n\n"
        "Команды:\n"
        "/add\\_account `login password` — авторизовать аккаунт Threads через Selenium\n"
        "/manual\\_cookies — добавить аккаунт вручную через cookies из браузера\n"
        "/accounts — список аккаунтов\n"
        "/topic `[login]` — сгенерировать тему\n"
        "/seriya `login тема серии` — сгенерировать серию\n"
        "/autoseriya `login` — авто-тема + серия\n"
        "/queue — очередь постов\n"
        "/post\\_now — опубликовать первую из очереди прямо сейчас\n"
        "/kartinka `login` — загрузить картинку для аккаунта\n"
        "/interval `часы` — интервал автопостинга\n"
        "/status — статус бота\n"
    )
    await upd.message.reply_text(text, parse_mode='Markdown')


# --- /add_account ---
async def cmd_add_account(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd):
        return
    if len(ctx.args) < 2:
        await upd.message.reply_text("Используй: /add_account login password")
        return
    login, password = ctx.args[0], ctx.args[1]
    msg = await upd.message.reply_text(f"⏳ Авторизую {login} через Selenium...")
    try:
        await asyncio.to_thread(threads_api.add_account, login, password)
        await msg.edit_text(f"✅ Аккаунт *{login}* добавлен и авторизован", parse_mode='Markdown')
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


# --- /manual_cookies (ConversationHandler) ---
async def cmd_manual_cookies(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd):
        return
    await upd.message.reply_text(
        "Введи логин аккаунта Threads:\n"
        "(Как достать cookies: DevTools → Application → Cookies → threads.net → sessionid и csrftoken)"
    )
    return WAIT_MANUAL_LOGIN

async def manual_get_login(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['manual_login'] = upd.message.text.strip()
    await upd.message.reply_text("Введи sessionid:")
    return WAIT_MANUAL_SESSION

async def manual_get_session(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['manual_session'] = upd.message.text.strip()
    await upd.message.reply_text("Введи csrftoken:")
    return WAIT_MANUAL_CSRF

async def manual_get_csrf(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    login = ctx.user_data['manual_login']
    session = ctx.user_data['manual_session']
    csrf = upd.message.text.strip()
    try:
        await asyncio.to_thread(threads_api.add_account_manual, login, session, csrf)
        await upd.message.reply_text(f"✅ Аккаунт *{login}* добавлен", parse_mode='Markdown')
    except Exception as e:
        await upd.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

async def conv_cancel(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("Отменено.")
    return ConversationHandler.END


# --- /accounts ---
async def cmd_accounts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    accs = threads_api.list_accounts()
    if not accs:
        await upd.message.reply_text("Нет авторизованных аккаунтов.")
        return
    lines = [f"✅ {a}" for a in accs]
    await upd.message.reply_text("Аккаунты:\n" + "\n".join(lines))


# --- /topic ---
async def cmd_topic(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    login = ctx.args[0] if ctx.args else None
    msg = await upd.message.reply_text("⏳ Генерирую тему...")
    try:
        topic = await asyncio.to_thread(ai_gen.generate_topic, login)
        await msg.edit_text(f"💡 Тема: *{topic}*", parse_mode='Markdown')
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


# --- /seriya login <тема> ---
async def cmd_seriya(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await upd.message.reply_text("Используй: /seriya login тема серии")
        return
    account_login = ctx.args[0]
    topic = ' '.join(ctx.args[1:])

    # Проверяем что аккаунт есть
    if account_login not in threads_api.list_accounts():
        await upd.message.reply_text(f"❌ Аккаунт {account_login} не найден. Добавь через /add_account")
        return

    msg = await upd.message.reply_text(f"⏳ Генерирую серию: _{topic}_...", parse_mode='Markdown')
    try:
        series = await asyncio.to_thread(ai_gen.generate_series, topic, account_login)
        storage.add_series(series, account_login)
        preview = series['post1'][:150] + ('...' if len(series['post1']) > 150 else '')
        await msg.edit_text(
            f"✅ Серия добавлена в очередь ({storage.count()} в очереди)\n\n"
            f"*Хук:* {preview}",
            parse_mode='Markdown'
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


# --- /autoseriya login ---
async def cmd_autoseriya(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await upd.message.reply_text("Используй: /autoseriya login")
        return
    account_login = ctx.args[0]
    if account_login not in threads_api.list_accounts():
        await upd.message.reply_text(f"❌ Аккаунт {account_login} не найден.")
        return
    msg = await upd.message.reply_text("⏳ Генерирую тему и серию...")
    try:
        topic = await asyncio.to_thread(ai_gen.generate_topic, account_login)
        series = await asyncio.to_thread(ai_gen.generate_series, topic, account_login)
        storage.add_series(series, account_login)
        await msg.edit_text(
            f"✅ Тема: *{topic}*\nДобавлено в очередь ({storage.count()})",
            parse_mode='Markdown'
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")


# --- /queue ---
async def cmd_queue(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items = storage.get_queue()
    if not items:
        await upd.message.reply_text("Очередь пуста.")
        return
    lines = [f"{i+1}. [{item['account_login']}] {item['topic']} ({item['added_at'][:10]})"
             for i, item in enumerate(items)]
    await upd.message.reply_text(f"📋 Очередь ({len(items)}):\n" + "\n".join(lines))


# --- /post_now ---
async def cmd_post_now(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd):
        return
    msg = await upd.message.reply_text("⏳ Публикую из очереди...")
    try:
        await post_from_queue()
        await msg.edit_text("✅ Серия опубликована")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка публикации: {e}")


# --- /kartinka (ConversationHandler) ---
async def cmd_kartinka(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd):
        return
    accs = threads_api.list_accounts()
    if not accs:
        await upd.message.reply_text("Нет аккаунтов.")
        return ConversationHandler.END
    await upd.message.reply_text(
        f"Для какого аккаунта загрузить картинку?\nАккаунты: {', '.join(accs)}\n\nВведи логин:"
    )
    return WAIT_LOGIN_FOR_IMAGE

async def kartinka_get_login(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    login = upd.message.text.strip()
    if login not in threads_api.list_accounts():
        await upd.message.reply_text(f"Аккаунт {login} не найден. Введи логин ещё раз:")
        return WAIT_LOGIN_FOR_IMAGE
    ctx.user_data['image_login'] = login
    await upd.message.reply_text(f"Отправь картинку для *{login}* как фото (не файлом):", parse_mode='Markdown')
    return WAIT_PHOTO

async def handle_photo(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    login = ctx.user_data.get('image_login')
    if not login:
        return ConversationHandler.END
    file = await ctx.bot.get_file(upd.message.photo[-1].file_id)
    os.makedirs('images', exist_ok=True)
    path = f"images/{login}.jpg"
    await file.download_to_drive(path)
    storage.set_image(login, path)
    await upd.message.reply_text(f"✅ Картинка для *{login}* сохранена", parse_mode='Markdown')
    return ConversationHandler.END


# --- /interval ---
async def cmd_interval(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(upd):
        return
    if not ctx.args:
        current = storage.get_setting('interval_hours', '4')
        await upd.message.reply_text(f"Текущий интервал: {current} ч.\nИспользуй: /interval <часы>")
        return
    try:
        hours = int(ctx.args[0])
        if hours < 1:
            raise ValueError
        storage.set_setting('interval_hours', hours)
        _reschedule(hours)
        await upd.message.reply_text(f"✅ Интервал автопостинга: {hours} ч.")
    except ValueError:
        await upd.message.reply_text("Укажи целое число часов, например /interval 4")


# --- /status ---
async def cmd_status(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    accs = threads_api.list_accounts()
    q = storage.count()
    interval = storage.get_setting('interval_hours', '4')
    lines = [
        f"🤖 *Статус SLASH VPN Bot*",
        f"Аккаунты: {len(accs)} ({', '.join(accs) if accs else 'нет'})",
        f"В очереди: {q} серий",
        f"Интервал: {interval} ч.",
    ]
    for login in accs:
        img = storage.get_image(login)
        lines.append(f"  [{login}] картинка: {'✅' if img else '❌ нет'}")
    await upd.message.reply_text("\n".join(lines), parse_mode='Markdown')


# --- Автопостинг ---
async def post_from_queue():
    item = storage.pop()
    if not item:
        logger.info("Очередь пуста, нечего публиковать.")
        return
    account_login = item['account_login']
    image = storage.get_image(account_login)
    logger.info(f"Публикую серию для {account_login}: {item['posts'].get('topic', '—')}")
    await asyncio.to_thread(threads_api.post_series, item['posts'], image, account_login)
    storage.archive_item(item['posts'], account_login)


def _reschedule(hours: int):
    if scheduler.get_job('post_job'):
        scheduler.remove_job('post_job')
    scheduler.add_job(post_from_queue, 'interval', hours=hours, id='post_job')
    logger.info(f"Планировщик перезапущен: каждые {hours} ч.")


def start_scheduler():
    interval = int(storage.get_setting('interval_hours', '4'))
    scheduler.add_job(post_from_queue, 'interval', hours=interval, id='post_job')
    scheduler.start()
    logger.info(f"Планировщик запущен: каждые {interval} ч.")


# --- Сборка приложения ---
def build_app():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('accounts', cmd_accounts))
    app.add_handler(CommandHandler('add_account', cmd_add_account))
    app.add_handler(CommandHandler('topic', cmd_topic))
    app.add_handler(CommandHandler('seriya', cmd_seriya))
    app.add_handler(CommandHandler('autoseriya', cmd_autoseriya))
    app.add_handler(CommandHandler('queue', cmd_queue))
    app.add_handler(CommandHandler('post_now', cmd_post_now))
    app.add_handler(CommandHandler('interval', cmd_interval))
    app.add_handler(CommandHandler('status', cmd_status))

    # ConversationHandler для картинки
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('kartinka', cmd_kartinka)],
        states={
            WAIT_LOGIN_FOR_IMAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, kartinka_get_login)],
            WAIT_PHOTO: [MessageHandler(filters.PHOTO, handle_photo)],
        },
        fallbacks=[CommandHandler('cancel', conv_cancel)]
    ))

    # ConversationHandler для ручных cookies
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('manual_cookies', cmd_manual_cookies)],
        states={
            WAIT_MANUAL_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_get_login)],
            WAIT_MANUAL_SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_get_session)],
            WAIT_MANUAL_CSRF: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_get_csrf)],
        },
        fallbacks=[CommandHandler('cancel', conv_cancel)]
    ))

    return app


if __name__ == '__main__':
    threads_api.load_accounts_from_db()
    start_scheduler()
    app = build_app()
    app.run_polling()
