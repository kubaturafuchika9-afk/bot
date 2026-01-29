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
main_model = genai.GenerativeModel('gemini-flash-latest')
report_model = genai.GenerativeModel('gemini-pro-latest')

daily_conversations = []
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))

# === HANDLERS ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_name = update.effective_user.first_name
    
    daily_conversations.append({
        "time": datetime.now().strftime("%H:%M"),
        "user": user_name,
        "message": user_message
    })
    
    try:
        response = main_model.generate_content(user_message)
        await update.message.reply_text(response.text[:4096])
    except Exception as e:
        error_text = str(e)
        logger.error(f"Error: {error_text}")
        print(f"DEBUG: {error_text}")
        await update.message.reply_text(f"❌ Ошибка: {error_text[:200]}")

async def generate_daily_report(context: ContextTypes.DEFAULT_TYPE):
    global daily_conversations
    
    if not daily_conversations or not ADMIN_CHAT_ID:
        logger.info("No conversations to report")
        return
    
    conversations_text = "\n".join([
        f"[{c['time']}] {c['user']}: {c['message']}" 
        for c in daily_conversations
    ])
    
    try:
        report = report_model.generate_content(
            f"Проанализируй диалоги и дай краткий отчёт о чем общались:\n{conversations_text}"
        )
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
        report = report_model.generate_content(
            f"Проанализируй диалоги и дай краткий отчёт о чем общались:\n{conversations_text}"
        )
        await update.message.reply_text(f"📊 Отчёт\n\n{report.text[:4096]}")
        logger.info("Manual report sent via /ok")
    except Exception as e:
        error_text = str(e)
        logger.error(f"Report error: {error_text}")
        await update.message.reply_text(f"❌ Ошибка отчёта: {error_text[:200]}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот с Gemini 🤖\n\n"
        "Команды:\n"
        "/start - начать\n"
        "/ok - получить отчёт о диалогах\n"
        "/report - ежедневный отчёт (для админа)"
    )

# === FLASK ===
def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)

# === MAIN ===
def main():
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask started in background thread")
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ok", manual_report))
    application.add_handler(CommandHandler("report", manual_report))  # Два способа
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Job для ежедневного отчёта в 23:00
    application.job_queue.run_daily(generate_daily_report, time=time(hour=23, minute=0))
    
    logger.info("Starting polling...")
    asyncio.run(application.run_polling(drop_pending_updates=True))

if __name__ == "__main__":
    main()
