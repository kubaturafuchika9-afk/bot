import os
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from threading import Thread
import time

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
import google.generativeai as genai

# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PORT = int(os.getenv("PORT", 10000))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

# Data directories
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ============================================================================
# FLASK APP (Keep-Alive)
# ============================================================================

flask_app = Flask(__name__)

@flask_app.route("/")
def index():
    return "Бот живой!", 200

@flask_app.route("/ping")
def ping():
    return "pong", 200

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_today_date():
    """Get today's date in YYYY-MM-DD format"""
    return datetime.now().strftime("%Y-%m-%d")

def get_current_hour():
    """Get current hour in HH format"""
    return datetime.now().strftime("%H")

def get_dialogs_file():
    """Get path to today's dialogs file"""
    return os.path.join(DATA_DIR, f"dialogs_{get_today_date()}.json")

def get_user_context_file():
    """Get path to user context file"""
    return os.path.join(DATA_DIR, "user_context.json")

def get_hourly_report_file(hour=None):
    """Get path to hourly report file"""
    if hour is None:
        hour = get_current_hour()
    return os.path.join(DATA_DIR, f"hourly_report_{hour}.txt")

def get_daily_report_file():
    """Get path to daily report file"""
    return os.path.join(DATA_DIR, "daily_report.txt")

def load_json(filepath):
    """Load JSON from file, return empty list/dict if not exists"""
    if not os.path.exists(filepath):
        return [] if filepath.endswith("dialogs") else {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return [] if filepath.endswith("dialogs") else {}

def save_json(filepath, data):
    """Save JSON to file"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Error saving JSON to {filepath}: {e}")

def log_dialog(user_id, user_name, message_text):
    """Log user message to dialogs file"""
    dialogs_file = get_dialogs_file()
    dialogs = load_json(dialogs_file)
    
    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": user_id,
        "user_name": user_name,
        "message": message_text
    }
    
    dialogs.append(new_entry)
    save_json(dialogs_file, dialogs)

def get_user_context(user_id):
    """Get user's message context (last 5 messages)"""
    context_file = get_user_context_file()
    contexts = load_json(context_file)
    return contexts.get(str(user_id), [])

def save_user_context(user_id, messages):
    """Save user's message context"""
    context_file = get_user_context_file()
    contexts = load_json(context_file)
    
    # Keep only last 5 messages
    contexts[str(user_id)] = messages[-5:]
    save_json(context_file, contexts)

def clear_user_context(user_id):
    """Clear user's context"""
    context_file = get_user_context_file()
    contexts = load_json(context_file)
    
    if str(user_id) in contexts:
        del contexts[str(user_id)]
    save_json(context_file, contexts)

def generate_hourly_report():
    """Generate hourly report from dialogs (LOCAL - no API calls)"""
    dialogs_file = get_dialogs_file()
    dialogs = load_json(dialogs_file)
    
    if not dialogs:
        return
    
    # Get current hour
    current_hour = get_current_hour()
    hour_start = f"{current_hour}:"
    
    # Filter messages from this hour
    hour_messages = [
        d for d in dialogs
        if d["timestamp"].startswith(f"{get_today_date()} {current_hour}:")
    ]
    
    if not hour_messages:
        return
    
    # Calculate stats
    unique_users = len(set(d["user_id"] for d in hour_messages))
    total_messages = len(hour_messages)
    
    # Extract topics (simple keyword counting)
    all_text = " ".join([d["message"].lower() for d in hour_messages])
    
    # Get interesting questions (messages with "?")
    questions = [d["message"] for d in hour_messages if "?" in d["message"]][:5]
    
    # Generate report
    report = f"""ОТЧЁТ ЗА ЧАС {current_hour}:00-{current_hour}:59
════════════════════════
Всего сообщений: {total_messages}
Пользователей: {unique_users}
Интересные вопросы:
"""
    
    if questions:
        for q in questions:
            report += f"- {q[:100]}\n"
    else:
        report += "- Нет вопросов\n"
    
    # Save report
    report_file = get_hourly_report_file(current_hour)
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"Hourly report saved: {report_file}")
    except Exception as e:
        logger.error(f"Error saving hourly report: {e}")

def generate_daily_report():
    """Generate daily report from dialogs (LOCAL - no API calls)"""
    dialogs_file = get_dialogs_file()
    dialogs = load_json(dialogs_file)
    
    if not dialogs:
        return
    
    # Calculate daily stats
    unique_users = len(set(d["user_id"] for d in dialogs))
    total_messages = len(dialogs)
    
    # Count messages per user
    user_counts = defaultdict(int)
    for d in dialogs:
        user_counts[d["user_name"]] += 1
    
    top_users = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    
    # Get interesting questions
    questions = [d["message"] for d in dialogs if "?" in d["message"]][:10]
    
    # Generate report
    report = f"""ЕЖЕДНЕВНЫЙ ОТЧЁТ {get_today_date()}
════════════════════════════════
Всего сообщений: {total_messages}
Всего пользователей: {unique_users}

Топ пользователей:
"""
    
    for name, count in top_users:
        report += f"- {name}: {count} сообщений\n"
    
    report += f"""
Интересные вопросы:
"""
    
    if questions:
        for q in questions:
            report += f"- {q[:100]}\n"
    else:
        report += "- Нет вопросов\n"
    
    # Save report
    report_file = get_daily_report_file()
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"Daily report saved: {report_file}")
    except Exception as e:
        logger.error(f"Error saving daily report: {e}")

# ============================================================================
# TELEGRAM BOT HANDLERS
# ============================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я ИИ-помощник на базе Gemini. Могу:\n"
        "• Отвечать на вопросы\n"
        "• Помогать с кодом\n"
        "• Просто общаться\n\n"
        "Команды:\n"
        "/clear - очистить историю\n"
        "/stats - твоя статистика"
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command"""
    user_id = update.effective_user.id
    clear_user_context(user_id)
    await update.message.reply_text("✅ История очищена!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    dialogs_file = get_dialogs_file()
    dialogs = load_json(dialogs_file)
    
    user_messages = [d for d in dialogs if d["user_id"] == user_id]
    message_count = len(user_messages)
    
    last_message = "нет сообщений"
    if user_messages:
        last_message = user_messages[-1]["timestamp"]
    
    await update.message.reply_text(
        f"📊 Статистика {user_name}:\n"
        f"Всего сообщений: {message_count}\n"
        f"Последнее: {last_message}"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user messages and reply with Gemini"""
    user = update.effective_user
    user_id = user.id
    user_name = user.first_name or "Unknown"
    message_text = update.message.text
    
    # Log dialog
    log_dialog(user_id, user_name, message_text)
    
    # Get user context
    user_messages = get_user_context(user_id)
    
    # Add current message to context
    user_messages.append({
        "role": "user",
        "content": message_text
    })
    
    # Build system prompt
    system_prompt = (
        "You are a helpful Russian-speaking AI assistant named Artemiy. "
        "Respond concisely (max 300-500 characters). "
        "You can respond in Russian, Azerbaijani, or English based on user's language. "
        "Be friendly and helpful. "
        "You can use mild swearing in response to swearing, but never insult. "
        "Support Markdown formatting for tables."
    )
    
    try:
        # Call Gemini API
        model = genai.GenerativeModel("gemini-flash-latest")
        
        # Build messages for API
        api_messages = [
            {"role": "user", "parts": [system_prompt]},
        ]
        
        for msg in user_messages:
            api_messages.append({
                "role": msg["role"],
                "parts": [msg["content"]]
            })
        
        # Generate response
        response = model.generate_content(
            contents=api_messages,
            generation_config={"max_output_tokens": 300}
        )
        
        bot_reply = response.text.strip()
        
        # Add bot response to context
        user_messages.append({
            "role": "model",
            "content": bot_reply
        })
        
        # Save updated context
        save_user_context(user_id, user_messages)
        
        # Send reply
        await update.message.reply_text(bot_reply)
        
        # Check if hourly report should be generated
        # (generate every hour at minute 0)
        if datetime.now().minute == 0:
            generate_hourly_report()
        
        # Check if daily report should be generated (at 23:00)
        if datetime.now().hour == 23 and datetime.now().minute == 0:
            generate_daily_report()
        
    except Exception as e:
        error_msg = str(e)
        
        if "429" in error_msg or "quota" in error_msg.lower():
            await update.message.reply_text("💰 Лимит на сегодня. Завтра попробуй!")
        else:
            await update.message.reply_text("❌ Ошибка. Попробуй позже")
        
        logger.error(f"Error processing message from {user_id}: {e}")

# ============================================================================
# BACKGROUND TASKS
# ============================================================================

def run_flask():
    """Run Flask app in background thread"""
    flask_app.run(host="0.0.0.0", port=PORT, debug=False)

def periodic_reports():
    """Check for reports every minute"""
    while True:
        try:
            now = datetime.now()
            
            # Generate hourly report at minute 0 of every hour
            if now.minute == 0:
                generate_hourly_report()
                time.sleep(60)  # Sleep 60 seconds to avoid duplicate
            
            # Generate daily report at 23:00
            if now.hour == 23 and now.minute == 0:
                generate_daily_report()
                time.sleep(60)
            
            time.sleep(30)  # Check every 30 seconds
        except Exception as e:
            logger.error(f"Error in periodic_reports: {e}")
            time.sleep(60)

# ============================================================================
# MAIN
# ============================================================================

def main():
    """Start the bot"""
    
    # Start Flask in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Flask app started on port {PORT}")
    
    # Start periodic reports thread
    reports_thread = Thread(target=periodic_reports, daemon=True)
    reports_thread.start()
    logger.info("Periodic reports thread started")
    
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start polling
    logger.info("Bot started. Polling for messages...")
    app.run_polling(allowed_updates=["message"], drop_pending_updates=True)

if __name__ == "__main__":
    main()
