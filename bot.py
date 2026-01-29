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
main_model = genai.GenerativeModel('gemini-flash-latest')  # ✅ gemini-1.5-flash
report_model = genai.GenerativeModel('gemini-pro-latest')  # ✅ для отчётов

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
        logger.error(f"Error: {e}")
        await update.message.reply_text("❌ Ошибка при обработке")

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
            f"Проанализируй диалоги и дай краткий отчёт:\n{conversations_text}"
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, 
            text=f"📊 Отчёт\n\n{report.text[:4096]}"
        )
        logger.info("Daily report sent")
    except Exception as e:
        logger.error(f"Report error: {e}")
    
    daily_conversations = []

async def manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await generate_daily_report(context)
    await update.message.reply_text("✅ Отчёт отправлен!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с Gemini 🤖")

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
    application.add_handler(CommandHandler("report", manual_report))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.job_queue.run_daily(generate_daily_report, time=time(hour=23, minute=0))
    
    logger.info("Starting polling...")
    asyncio.run(application.run_polling(drop_pending_updates=True))

if __name__ == "__main__":
    main()
