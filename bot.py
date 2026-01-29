import os
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

# === GEMINI ===
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

bot_model = genai.GenerativeModel(
    'gemini-flash-latest',
    system_instruction="""Ты дерзкий помощник! 🎯
- Отвечаешь коротко и по делу (2-3 абзаца макс)
- Можешь матом на мат
- Помнишь контекст разговора
- Русский, азербайджанский, английский
- Таблицы в Markdown:
| Колонка1 | Колонка2 |
|----------|----------|
| Знач | Знач |
- МАКСИМУМ 500 СИМВОЛОВ!"""
)

# === КОНТЕКСТ ПОЛЬЗОВАТЕЛЕЙ ===
user_context = {}

def get_user_key(update: Update) -> str:
    user = update.effective_user
    return f"{user.id}"

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user = update.effective_user
    user_key = get_user_key(update)
    
    # Инициализировать контекст пользователя
    if user_key not in user_context:
        user_context[user_key] = []
    
    # Добавить сообщение в историю
    user_context[user_key].append({
        "role": "user",
        "parts": [user_message]
    })
    
    try:
        # Отправить запрос
        response = bot_model.generate_content(user_message)
        bot_reply = response.text
        
        # Добавить ответ в историю
        user_context[user_key].append({
            "role": "model",
            "parts": [bot_reply]
        })
        
        # Обрезать если слишком длинный
        if len(bot_reply) > 4000:
            bot_reply = bot_reply[:3990] + "..."
        
        await update.message.reply_text(bot_reply, parse_mode="Markdown")
        logger.info(f"✅ {user.first_name}: OK")
        
    except Exception as e:
        error = str(e)
        logger.error(f"❌ {user.first_name}: {error[:100]}")
        
        if "429" in error or "quota" in error.lower():
            await update.message.reply_text("💰 Лимит на сегодня. Завтра попробуй!")
        else:
            await update.message.reply_text(f"❌ Ошибка: {error[:80]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Yo! 🎯\n\nЯ дерзкий бот, отвечаю коротко и по делу!\n"
        "/start - меню\n"
        "/clear - забыть историю\n\n"
        "Просто пиши! 💪"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_key(update)
    if user_key in user_context:
        user_context[user_key] = []
    await update.message.reply_text("🧹 История очищена!")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

# === FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)

# === MAIN ===
def main():
    logger.info("=" * 60)
    logger.info("BOT STARTING - gemini-flash-latest")
    logger.info("=" * 60)
    
    # Flask в потоке
    Thread(target=run_flask, daemon=True).start()
    logger.info("✅ Flask started")
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()
    
    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    
    # Сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Ошибки
    application.add_error_handler(error_handler)
    
    logger.info("✅ Handlers registered")
    logger.info("🚀 Starting polling...")
    
    try:
        asyncio.run(application.run_polling(drop_pending_updates=True))
    except Exception as e:
        logger.error(f"Fatal: {e}")

if __name__ == "__main__":
    main()
