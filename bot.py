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
    return "pong", 200

# === GEMINI (БЕЗ большого system_instruction) ===
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

# Создаём модель БЕЗ system_instruction в инициализации
bot_model = genai.GenerativeModel('gemini-flash-latest')

# === КОНТЕКСТ (максимум 5 последних сообщений) ===
user_context = {}
MAX_HISTORY = 5  # Ограничиваем историю!

def get_user_key(update: Update) -> str:
    return str(update.effective_user.id)

def trim_history(history):
    """Оставляем только последние 5 сообщений"""
    if len(history) > MAX_HISTORY * 2:  # 5 пар = 10 элементов
        return history[-(MAX_HISTORY * 2):]
    return history

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user = update.effective_user
    user_key = get_user_key(update)
    
    # Инициализировать контекст
    if user_key not in user_context:
        user_context[user_key] = []
    
    # Добавить сообщение
    user_context[user_key].append({
        "role": "user",
        "parts": [user_message]
    })
    
    # Обрезать историю
    user_context[user_key] = trim_history(user_context[user_key])
    
    try:
        # Формируем промпт с инструкциями (экономим токены!)
        system_msg = "Ты дерзкий помощник. Отвечай коротко (макс 300 символов). Помни контекст."
        
        # Отправляем с минимальным промптом
        response = bot_model.generate_content(
            [system_msg] + user_context[user_key]
        )
        bot_reply = response.text[:4000]
        
        # Добавить ответ
        user_context[user_key].append({
            "role": "model",
            "parts": [bot_reply]
        })
        
        await update.message.reply_text(bot_reply, parse_mode="Markdown")
        logger.info(f"✅ {user.first_name}")
        
    except Exception as e:
        error = str(e)
        logger.error(f"❌ {user.first_name}: {error[:80]}")
        
        if "429" in error:
            await update.message.reply_text("💰 Лимит на сегодня. Завтра попробуй!")
        else:
            await update.message.reply_text(f"❌ Ошибка")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yo! 🎯 Я дерзкий бот. Пиши! 💪\n/clear - забыть")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_key = get_user_key(update)
    user_context[user_key] = []
    await update.message.reply_text("🧹 История очищена!")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=False)

def main():
    logger.info("BOT STARTING - gemini-flash-latest (OPTIMIZED)")
    
    Thread(target=run_flask, daemon=True).start()
    logger.info("✅ Flask started")
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("🚀 Starting polling...")
    
    try:
        asyncio.run(application.run_polling(drop_pending_updates=True))
    except Exception as e:
        logger.error(f"Fatal: {e}")

if __name__ == "__main__":
    main()
