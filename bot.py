import os
from datetime import datetime, time
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai

app = Flask(__name__)

@app.route('/')
def home():
    return "Бот живой!"

@app.route('/ping')
def ping():
    return "pong", 200

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
main_model = genai.GenerativeModel('gemini-1.5-flash')
report_model = genai.GenerativeModel('gemini-1.5-pro')

daily_conversations = []
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_name = update.effective_user.first_name
    
    daily_conversations.append({
        "time": datetime.now().strftime("%H:%M"),
        "user": user_name,
        "message": user_message
    })
    
    response = main_model.generate_content(user_message)
    await update.message.reply_text(response.text)

async def generate_daily_report(context: ContextTypes.DEFAULT_TYPE):
    global daily_conversations
    
    if not daily_conversations or not ADMIN_CHAT_ID:
        return
    
    conversations_text = "\n".join([
        f"[{c['time']}] {c['user']}: {c['message']}" 
        for c in daily_conversations
    ])
    
    report = report_model.generate_content(
        f"Проанализируй диалоги и дай краткий отчёт:\n{conversations_text}"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📊 Отчёт\n\n{report.text}")
    daily_conversations = []

async def manual_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await generate_daily_report(context)
    await update.message.reply_text("✅ Отчёт отправлен!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот с Gemini 🤖")

def main():
    Thread(target=run_web, daemon=True).start()
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    application = Application.builder().token(token).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("report", manual_report))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    application.job_queue.run_daily(generate_daily_report, time=time(hour=23, minute=0))
    
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
