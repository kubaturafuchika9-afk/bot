import os
from datetime import datetime, time
from flask import Flask
from threading import Thread
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот живой!", 200

@app.route('/ping')
def ping():
    logger.info("Ping received - keeping service alive")
    return "pong", 200

# Gemini
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Основная модель - универсальная
main_model = genai.GenerativeModel(
    'gemini-flash-latest',
    system_instruction="""Ты дерзкий и остроумный помощник! 🎯
Особенности твоего поведения:
- Говоришь прямо и без лишних церемоний
- Можешь отвечать матом на мат (но не оскорбляй человека)
- Помогаешь с информацией, кодом, анализом, творчеством
- Отвечаешь кратко и по делу, НО с юмором и энергией
- Помнишь контекст диалога и можешь ссылаться на предыдущие сообщения
- Поддерживаешь азербайджанский, русский и английский языки
- Если нужна таблица - форматируй в Markdown для Telegram:

| МОДЕЛЬ | SHTORM | URBAN |
|--------|--------|-------|
| Полотно | 90 мм | 105 мм |
| Коробка | 100 мм | 115 мм |
| Цвет | Графит | Черная |

- Не пиши гигантские монологи! Максимум 2-3 абзаца или 500 символов
- Если ответ длинный - раздели на части с заголовками
- Будь полезен, но коротко!"""
)

# Для сложных задач
nano_model = genai.GenerativeModel('nano-banana-pro-preview')

# Хранилище контекста по пользователям
user_conversations = {}
daily_conversations = []
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))

def get_user_key(update: Update) -> str:
    """Получить уникальный ключ пользователя (имя + ID)"""
    user = update.effective_user
    return f"{user.id}_{user.first_name}"

def init_user_context(user_key: str):
    """Инициализировать контекст пользователя"""
    if user_key not in user_conversations:
        user_conversations[user_key] = {
            "history": [],
            "name": None,
            "chat_type": None
        }

def should_use_nano(message: str) -> bool:
    """Определить, нужна ли nano-banana для сложной задачи"""
    keywords = [
        "код", "программ", "алгоритм", "базу", "анализ", 
        "вычисли", "обработай", "расчет", "данных", "вычисл"
    ]
    return any(keyword in message.lower() for keyword in keywords)

def format_for_telegram(text: str) -> str:
    """Форматировать ответ для Telegram (макс 4096 символов)"""
    if len(text) > 4000:
        # Обрезать и добавить многоточие
        return text[:3990] + "\n\n...(продолжение в следующем сообщении)"
    return text

# === HANDLERS ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user = update.effective_user
    user_key = get_user_key(update)
    
    # Инициализация контекста
    init_user_context(user_key)
    user_conversations[user_key]["name"] = user.first_name
    user_conversations[user_key]["chat_type"] = update.effective_chat.type
    
    # Добавить в историю диалога
    user_conversations[user_key]["history"].append({
        "role": "user",
        "parts": [user_message]
    })
    
    # Добавить в ежедневный лог
    daily_conversations.append({
        "time": datetime.now().strftime("%H:%M"),
        "user": user.first_name,
        "message": user_message
    })
    
    try:
        # Решить, какую модель использовать
        if should_use_nano(user_message):
            logger.info(f"Using nano-banana for complex task from {user.first_name}")
            model = nano_model
        else:
            model = main_model
        
        # Отправить запрос с историей
        response = model.generate_content(user_message)
        bot_reply = response.text
        
        # Добавить ответ в историю
        user_conversations[user_key]["history"].append({
            "role": "model",
            "parts": [bot_reply]
        })
        
        # Форматировать и отправить
        formatted_reply = format_for_telegram(bot_reply)
        await update.message.reply_text(formatted_reply)
        
        logger.info(f"Response sent to {user.first_name}")
        
    except Exception as e:
        error_text = str(e)
        logger.error(f"Error from {user.first_name}: {error_text}")
        
        # Более информативное сообщение об ошибке
        if "429" in error_text:
            error_msg = "🔴 Слишком много запросов. Попробуй позже!"
        elif "quota" in error_text.lower():
            error_msg = "💰 Квота исчерпана. Жди завтра!"
        else:
            error_msg = f"❌ Ошибка: {error_text[:100]}"
        
        await update.message.reply_text(error_msg)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка голосовых сообщений"""
    user = update.effective_user
    user_key = get_user_key(update)
    
    try:
        await update.message.reply_text("🎙️ Голосовые сообщения скоро поддержу, пока пишите текстом!")
    except Exception as e:
        logger.error(f"Voice error: {e}")

async def generate_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневный отчёт"""
    global daily_conversations
    
    if not daily_conversations or not ADMIN_CHAT_ID:
        logger.info("No conversations to report")
        return
    
    conversations_text = "\n".join([
        f"[{c['time']}] {c['user']}: {c['message']}" 
        for c in daily_conversations
    ])
    
    try:
        report_prompt = f"""Проанализируй эти диалоги и дай КРАТКИЙ отчёт (не больше 500 символов):
{conversations_text}

Отчёт должен содержать:
- О чём общались пользователи
- Какие были основные темы
- Интересные вопросы"""
        
        report = main_model.generate_content(report_prompt)
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"📊 Отчёт за день\n\n{report.text[:4096]}"
        )
        logger.info("Daily report sent")
    except Exception as e:
        logger.error(f"Report error: {e}")
    
    daily_conversations = []

async def manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /ok - отправить отчёт прямо сейчас"""
    global daily_conversations
    
    if not daily_conversations:
        await update.message.reply_text("📭 Нет диалогов для отчёта")
        return
    
    conversations_text = "\n".join([
        f"[{c['time']}] {c['user']}: {c['message']}" 
        for c in daily_conversations
    ])
    
    try:
        report_prompt = f"""Проанализируй эти диалоги и дай КРАТКИЙ отчёт (не больше 500 символов):
{conversations_text}

Отчёт должен содержать:
- О чём общались пользователи
- Какие были основные темы
- Интересные вопросы"""
        
        report = main_model.generate_content(report_prompt)
        await update.message.reply_text(f"📊 Отчёт\n\n{report.text[:4096]}")
        logger.info("Manual report sent via /ok")
    except Exception as e:
        error_text = str(e)
        logger.error(f"Report error: {error_text}")
        await update.message.reply_text(f"❌ Ошибка отчёта: {error_text[:100]}")

async def get_context_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /info - информация о контексте"""
    user_key = get_user_key(update)
    init_user_context(user_key)
    
    conv = user_conversations[user_key]
    info = f"""ℹ️ Информация о тебе:
👤 Имя: {conv['name']}
💬 Тип чата: {conv['chat_type']}
📝 Сообщений в контексте: {len(conv['history'])}
🔄 Контекст помнится и будет использован"""
    
    await update.message.reply_text(info)

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /clear - очистить контекст"""
    user_key = get_user_key(update)
    if user_key in user_conversations:
        user_conversations[user_key]["history"] = []
    
    await update.message.reply_text("🧹 Контекст очищен. Начинаем с чистого листа!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Yo, {user_name}! 🎯 Я дерзкий бот с Gemini!\n\n"
        "Команды:\n"
        "/start - старт\n"
        "/ok - отчёт о диалогах\n"
        "/info - инфо о контексте\n"
        "/clear - забыть историю\n\n"
        "Пиши что угодно, я отвечу 💪"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Exception: {context.error}")

# === FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)

# === MAIN ===
def main():
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask started in background thread")
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ok", manual_report))
    application.add_handler(CommandHandler("report", manual_report))
    application.add_handler(CommandHandler("info", get_context_info))
    application.add_handler(CommandHandler("clear", clear_context))
    
    # Сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    # Ошибки
    application.add_error_handler(error_handler)
    
    # Job для ежедневного отчёта в 23:00
    application.job_queue.run_daily(generate_daily_report, time=time(hour=23, minute=0))
    
    logger.info("Starting polling...")
    try:
        asyncio.run(application.run_polling(drop_pending_updates=True))
    except KeyboardInterrupt:
        logger.info("Bot stopped")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
