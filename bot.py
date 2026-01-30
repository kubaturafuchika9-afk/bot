import os
import json
import logging
import datetime
import threading
import time
from collections import deque, Counter
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import google.generativeai as genai
from telegram.error import BadRequest

# === CONFIGURATION ===
PORT = int(os.environ.get("PORT", 10000))
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
DATA_DIR = "data"

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# === FLASK KEEP-ALIVE SERVER ===
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!", 200

@app.route('/ping')
def ping():
    return "pong", 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# === GEMINI SETUP ===
genai.configure(api_key=GEMINI_KEY)
# Using gemini-flash-latest as requested
model = genai.GenerativeModel('gemini-1.5-flash-latest')

# === STATE MANAGEMENT ===
# Context: {user_id: deque([{'role': 'user'/'model', 'text': '...'}], maxlen=5)}
user_contexts = {}

def get_dialog_filename():
    """Returns filename for today's JSON log."""
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    return os.path.join(DATA_DIR, f"dialogs_{date_str}.json")

def log_message_to_json(user_id, user_name, text):
    """Logs user message to JSON file without API calls."""
    filename = get_dialog_filename()
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_id": str(user_id),
        "user_name": user_name,
        "message": text
    }
    
    data = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    
    data.append(entry)
    
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def generate_local_report(context: ContextTypes.DEFAULT_TYPE):
    """
    Generates reports locally based on JSON logs.
    Running this WITHOUT API calls to save tokens.
    """
    now = datetime.datetime.now()
    filename = get_dialog_filename()
    
    if not os.path.exists(filename):
        logger.info("No data for report yet.")
        return

    try:
        with open(filename, "r", encoding="utf-8") as f:
            logs = json.load(f)
    except Exception as e:
        logger.error(f"Error reading logs for report: {e}")
        return

    # Filter logs for the last hour
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)
    last_hour_start = current_hour_start - datetime.timedelta(hours=1)
    
    hourly_logs = []
    for log in logs:
        try:
            log_time = datetime.datetime.strptime(log['timestamp'], "%Y-%m-%d %H:%M:%S")
            if last_hour_start <= log_time < current_hour_start:
                hourly_logs.append(log)
        except ValueError:
            continue

    if not hourly_logs:
        return # Nothing to report for this hour

    # Basic Analytics (No AI)
    msg_count = len(hourly_logs)
    users = set(l['user_id'] for l in hourly_logs)
    
    # Simple keyword extraction (proxy for "themes")
    all_words = " ".join([l['message'] for l in hourly_logs]).lower().split()
    # Filter short words to remove prepositions roughly
    words = [w for w in all_words if len(w) > 4] 
    common_themes = Counter(words).most_common(3)
    themes_str = ", ".join([t[0] for t in common_themes])
    
    # "Interesting" questions = simply longest messages (heuristic)
    sorted_by_len = sorted(hourly_logs, key=lambda x: len(x['message']), reverse=True)
    top_questions = sorted_by_len[:2]
    
    report_content = (
        f"ОТЧЁТ ЗА ЧАС {last_hour_start.strftime('%H:%M')}-{current_hour_start.strftime('%H:%M')}\n"
        f"════════════════════════\n"
        f"Всего сообщений: {msg_count}\n"
        f"Пользователей: {len(users)}\n"
        f"Основные слова (темы): {themes_str}\n"
        f"Интересные вопросы (длинные):\n"
    )
    
    for q in top_questions:
        report_content += f"- {q['message'][:50]}...\n"

    # Save Hourly Report
    report_filename = os.path.join(DATA_DIR, f"hourly_report_{last_hour_start.strftime('%H')}.txt")
    with open(report_filename, "w", encoding="utf-8") as f:
        f.write(report_content)
        
    # Daily Report (Triggered at 23:00)
    if now.hour == 23 and now.minute < 59: # Check logic handled by JobQueue timing ideally
        daily_filename = os.path.join(DATA_DIR, "daily_report.txt")
        # Simple daily summary overwriting the hourly structure logic for brevity
        daily_content = f"ИТОГОВЫЙ ОТЧЁТ ЗА {now.strftime('%Y-%m-%d')}\nTotal logs: {len(logs)}"
        with open(daily_filename, "w", encoding="utf-8") as f:
            f.write(daily_content)

# === BOT COMMANDS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Initialize context
    user_contexts[user_id] = deque(maxlen=5)
    await update.message.reply_text("Привет! Я бот на базе Gemini Flash. Говорим коротко и по делу. /clear чтобы забыть всё.")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_contexts:
        user_contexts[user_id].clear()
    await update.message.reply_text("🧹 Память очищена.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user stats based on local file parsing."""
    user_id = str(update.effective_user.id)
    filename = get_dialog_filename()
    count = 0
    last_time = "Никогда"
    
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
                user_msgs = [d for d in data if d['user_id'] == user_id]
                count = len(user_msgs)
                if user_msgs:
                    last_time = user_msgs[-1]['timestamp']
        except:
            pass
            
    await update.message.reply_text(f"📊 Твоя статистика за сегодня:\nСообщений: {count}\nПоследний раз: {last_time}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    text = update.message.text

    if not text:
        return

    # 1. Log to JSON (Local Requirement)
    log_message_to_json(user_id, user_name, text)

    # 2. Manage Context
    if user_id not in user_contexts:
        user_contexts[user_id] = deque(maxlen=5)
    
    # 3. Construct Prompt (System Instruction + Context + Current)
    # Instruction is injected here to save tokens on init and keep control
    system_instruction = (
        "ROLE: You are a helpful Telegram bot.\n"
        "MODEL: gemini-flash-latest\n"
        "LANGUAGES: Russian, Azerbaijani, English.\n"
        "STYLE: Short (max 300 chars), concise, helpful. Can use profanity ONLY if user swears first, but NO insults.\n"
        "FORMAT: Use Markdown for tables/bold.\n"
        "GOAL: Answer the user's last message based on history."
    )

    history_str = ""
    for msg in user_contexts[user_id]:
        role_label = "User" if msg['role'] == 'user' else "Bot"
        history_str += f"{role_label}: {msg['text']}\n"

    full_prompt = f"{system_instruction}\n\nHISTORY:\n{history_str}\n\nUser (Current): {text}\nBot:"

    # 4. Call Gemini API
    try:
        response = model.generate_content(full_prompt)
        reply_text = response.text.strip()
        
        # Save interaction to RAM context
        user_contexts[user_id].append({'role': 'user', 'text': text})
        user_contexts[user_id].append({'role': 'model', 'text': reply_text})

        # Send to Telegram
        await update.message.reply_markdown(reply_text)

    except Exception as e:
        error_str = str(e)
        logger.error(f"Gemini API Error: {error_str}")
        
        if "429" in error_str:
            await update.message.reply_text("💰 Лимит на сегодня. Завтра попробуй!")
        else:
            await update.message.reply_text("❌ Ошибка. Попробуй позже.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# === MAIN ENTRY POINT ===

def main():
    # 1. Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # 2. Setup Telegram Bot
    if not TOKEN:
        logger.error("No TELEGRAM_BOT_TOKEN found!")
        return

    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Job Queue for Reports (Every hour)
    job_queue = application.job_queue
    # Run report generation every hour at minute 0
    job_queue.run_repeating(generate_local_report, interval=3600, first=10) 

    logger.info("Bot started via Polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
