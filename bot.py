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
    logger.info("Ping received")
    return "pong", 200

# === GEMINI КОНФИГ ===
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

main_model = genai.GenerativeModel(
    'gemini-flash-latest',
    system_instruction="""Ты дерзкий помощник! 🎯
- Отвечаешь коротко и по делу (не больше 2-3 абзацев)
- Можешь матом на мат, но не оскорбляй
- Помнишь контекст диалога
- Поддерживаешь русский, азербайджанский, английский
- Таблицы в Markdown для Telegram:

| Колонка1 | Колонка2 |
|----------|----------|
| Значение | Значение |

- НЕ пиши гигантские монологи!
- Максимум 500 символов в ответе"""
)

# === ХРАНИЛИЩЕ ===
user_conversations = {}
daily_conversations = []
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))

def get_user_key(update: Update) -> str:
    user = update.effective_user
    return f"{user.id}_{user.first_name}"

def init_user_context(user_key: str):
    if user_key not in user_conversations:
        user_conversations[user_key] = {
            "history": [],
            "name": None,
            "chat_type": None
        }

def format_for_telegram(text: str) -> str:
    """Обрезать ответ если слишком длинный"""
    if len(text) > 4000:
        return text[:3990] + "\n\n...(продолжение в следующем сообщении)"
    return text

# === HANDLERS ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user = update.effective_user
    user_key = get_user_key(update)
    
    init_user_context(user_key)
    user_conversations[user_key]["name"] = user.first_name
    user_conversations[user_key]["chat_type"] = update.effective_chat.type
    
    # Добавить в историю
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
        response = main_model.generate_content(user_message)
        bot_reply = response.text
        
        # Добавить ответ в историю
        user_conversations[user_key]["history"].append({
            "role": "model",
            "parts": [bot_reply]
        })
        
        # Форматировать и отправить
        formatted_reply = format_for_telegram(bot_reply)
        await update.message.reply_text(formatted_reply, parse_mode="Markdown")
        
        logger.info(f"✅ Response to {user.first_name}: {len(bot_reply)} chars")
        
    except Exception as e:
        error_text = str(e)
        logger.error(f"❌ Error from {user.first_name}: {error_text[:150]}")
        
        if "429" in error_text or "quota" in error_text.lower():
            await update.message.reply_text("💰 Лимит исчерпан. Попробуй позже!")
        else:
            await update.message.reply_text(f"❌ Ошибка: {error_text[:100]}")

async def generate_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """Ежедневный отчёт в 23:00"""
    global daily_conversations
    
    if not daily_conversations or not ADMIN_CHAT_ID:
        logger.info("No conversations to report")
        return
    
    conversations_text = "\n".join([
        f"[{c['time']}] {c['user']}: {c['message']}" 
        for c in daily_conversations
    ])
    
    try:
        report_prompt = f"""Проанализируй эти диалоги и дай КРАТКИЙ отчёт (максимум 500 символов):
{conversations_text}

Отчёт должен содержать:
- О чём общались пользователи
- Основные темы
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
        report_prompt = f"""Проанализируй эти диалоги и дай КРАТКИЙ отчёт (максимум 500 символов):
{conversations_text}

Отчёт должен содержать:
- О чём общались пользователи
- Основные темы
- Интересные вопросы"""
        
        report = main_model.generate_content(report_prompt)
        await update.message.reply_text(f"📊 Отчёт\n\n{report.text[:4096]}")
        logger.info("Manual report sent via /ok")
    except Exception as e:
        error_text = str(e)
        logger.error(f"Report error: {error_text}")
        await update.message.reply_text(f"❌ Ошибка отчёта: {error_text[:100]}")

async def get_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /stats - статистика"""
    user_key = get_user_key(update)
    init_user_context(user_key)
    
    history = user_conversations[user_key]["history"]
    message_count = len([h for h in history if h["role"] == "user"])
    
    stats = f"""📊 Твоя статистика:
👤 Имя: {user_conversations[user_key]['name']}
💬 Сообщений в чате: {message_count}
🔄 Тип чата: {user_conversations[user_key]['chat_type']}"""
    
    await update.message.reply_text(stats)

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /history - история диалога"""
    user_key = get_user_key(update)
    init_user_context(user_key)
    
    history = user_conversations[user_key]["history"]
    
    if not history:
        await update.message.reply_text("📭 История диалога пуста")
        return
    
    # Последние 4 сообщения (2 пары)
    text = "📝 Последний диалог:\n\n"
    relevant = [h for h in history[-4:]]
    
    for msg in relevant:
        if msg["role"] == "user":
            text += f"👤 Ты: {msg['parts'][0][:60]}{'...' if len(msg['parts'][0]) > 60 else ''}\n"
        else:
            text += f"🤖 Я: {msg['parts'][0][:60]}{'...' if len(msg['parts'][0]) > 60 else ''}\n"
    
    await update.message.reply_text(text[:4000])

async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /clear - очистить историю"""
    user_key = get_user_key(update)
    if user_key in user_conversations:
        user_conversations[user_key]["history"] = []
    
    await update.message.reply_text("🧹 История очищена!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_name = update.effective_user.first_name
    await update.message.reply_text(
        f"Yo, {user_name}! 🎯\n\n"
        "Я дерзкий бот с Gemini, отвечаю коротко и по делу!\n\n"
        "Команды:\n"
        "/start - начало\n"
        "/stats - твоя статистика\n"
        "/history - последний диалог\n"
        "/clear - очистить историю\n"
        "/ok - отчёт о диалогах\n\n"
        "Пиши что угодно! 💪"
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Exception: {context.error}")

# === FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Starting Flask on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)

# === MAIN ===
def main():
    logger.info("=" * 60)
    logger.info("BOT STARTING - gemini-flash-latest")
    logger.info("=" * 60)
    
    # Flask в потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("✅ Flask thread started")
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN не установлен!")
        return
    
    application = Application.builder().token(token).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", get_user_stats))
    application.add_handler(CommandHandler("history", get_history))
    application.add_handler(CommandHandler("clear", clear_context))
    application.add_handler(CommandHandler("ok", manual_report))
    
    # Сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Ошибки
    application.add_error_handler(error_handler)
    
    # Job для ежедневного отчёта в 23:00
    application.job_queue.run_daily(generate_daily_report, time=time(hour=23, minute=0))
    
    logger.info("✅ Handlers registered")
    logger.info("🚀 Starting polling...")
    
    try:
        asyncio.run(application.run_polling(drop_pending_updates=True))
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
