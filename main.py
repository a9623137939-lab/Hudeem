import os
import json
import sqlite3
from datetime import datetime, time, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
import asyncio
import threading

# ---------- Конфигурация ----------
TOKEN = os.getenv("BOT_TOKEN")  # установите переменную окружения
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # например, https://ваш-бот.onrender.com
SYNC_SECRET = os.getenv("SYNC_SECRET", "default_secret_change_me")  # опционально

# Flask приложение для приёма синхронизации
flask_app = Flask(__name__)

# SQLite
DB_PATH = "users.db"

# Глобальный бот и планировщик
bot_app = None
scheduler = None

# ---------- Работа с БД ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Пользователи и их настройки
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        daily_enabled INTEGER DEFAULT 0,
        daily_time TEXT DEFAULT '19:00',
        weekly_enabled INTEGER DEFAULT 0,
        weekly_day INTEGER DEFAULT 6,
        weekly_time TEXT DEFAULT '19:00',
        last_sync TIMESTAMP
    )''')
    # Веса пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS weights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        date TEXT,
        weight REAL,
        UNIQUE(chat_id, date)
    )''')
    conn.commit()
    conn.close()

def get_user_settings(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT daily_enabled, daily_time, weekly_enabled, weekly_day, weekly_time FROM users WHERE chat_id = ?", (chat_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "daily_enabled": bool(row[0]),
            "daily_time": row[1],
            "weekly_enabled": bool(row[2]),
            "weekly_day": row[3],
            "weekly_time": row[4]
        }
    return None

def set_user_settings(chat_id, settings):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO users 
        (chat_id, daily_enabled, daily_time, weekly_enabled, weekly_day, weekly_time, last_sync)
        VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (chat_id, int(settings['daily_enabled']), settings['daily_time'],
         int(settings['weekly_enabled']), settings['weekly_day'], settings['weekly_time'], datetime.now()))
    conn.commit()
    conn.close()

def add_weight(chat_id, date, weight):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO weights (chat_id, date, weight) VALUES (?, ?, ?)", (chat_id, date, weight))
    conn.commit()
    conn.close()

def get_weights(chat_id, start_date=None, end_date=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    query = "SELECT date, weight FROM weights WHERE chat_id = ?"
    params = [chat_id]
    if start_date:
        query += " AND date >= ?"
        params.append(start_date)
    if end_date:
        query += " AND date <= ?"
        params.append(end_date)
    query += " ORDER BY date"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [{"date": r[0], "weight": r[1]} for r in rows]

# ---------- Уведомления ----------
async def send_daily_reminder(chat_id: int, bot: Bot):
    await bot.send_message(chat_id, "🔔 Новый день, новые достижения! Не забудь указать свой вес сегодня.")

async def send_weekly_summary(chat_id: int, bot: Bot):
    # Определяем последнюю неделю: понедельник - воскресенье (ISO неделя)
    today = datetime.now().date()
    # Найти последний понедельник
    days_since_monday = today.weekday()  # 0=понедельник
    monday = today - timedelta(days=days_since_monday)
    sunday = monday + timedelta(days=6)
    weights = get_weights(chat_id, monday.isoformat(), sunday.isoformat())
    if not weights:
        await bot.send_message(chat_id, "📊 За прошедшую неделю нет записей о весе. Начните записывать!")
        return
    # Создаём словарь по дням недели
    weekdays = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    day_map = {}
    for w in weights:
        d = datetime.fromisoformat(w['date']).date()
        if monday <= d <= sunday:
            day_map[weekdays[d.weekday()]] = w['weight']
    # Ищем вес в понедельник и воскресенье
    mon_weight = day_map.get("ПН")
    sun_weight = day_map.get("ВС")
    diff_text = ""
    if mon_weight and sun_weight:
        diff = sun_weight - mon_weight
        sign = "+" if diff > 0 else ""
        diff_text = f"\nИзменение за неделю: {sign}{diff:.1f} кг"
    elif mon_weight:
        diff_text = f"\nВес в понедельник: {mon_weight:.1f} кг. Нет данных за воскресенье."
    elif sun_weight:
        diff_text = f"\nВес в воскресенье: {sun_weight:.1f} кг. Нет данных за понедельник."
    else:
        diff_text = "\nНет данных за понедельник или воскресенье."
    # Построим таблицу
    table = "📅 *Сводка за неделю:*\n"
    for day in weekdays:
        val = day_map.get(day)
        table += f"{day}: {val:.1f} кг\n" if val else f"{day}: —\n"
    await bot.send_message(chat_id, table + diff_text, parse_mode="Markdown")

# Планировщик: перезапуск заданий для всех пользователей
async def reschedule_all_jobs():
    scheduler.remove_all_jobs()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id, daily_enabled, daily_time, weekly_enabled, weekly_day, weekly_time FROM users")
    users = c.fetchall()
    conn.close()
    for (chat_id, daily_en, daily_t, weekly_en, weekly_day, weekly_t) in users:
        if daily_en:
            hour, minute = map(int, daily_t.split(':'))
            trigger = CronTrigger(hour=hour, minute=minute)
            scheduler.add_job(send_daily_reminder, trigger, args=(chat_id, bot_app.bot), id=f"daily_{chat_id}", replace_existing=True)
        if weekly_en:
            hour, minute = map(int, weekly_t.split(':'))
            # день недели: 0=понедельник ... 6=воскресенье
            trigger = CronTrigger(day_of_week=weekly_day, hour=hour, minute=minute)
            scheduler.add_job(send_weekly_summary, trigger, args=(chat_id, bot_app.bot), id=f"weekly_{chat_id}", replace_existing=True)

# ---------- Telegram команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    # Регистрируем пользователя (если нет)
    if get_user_settings(chat_id) is None:
        set_user_settings(chat_id, {"daily_enabled": False, "daily_time": "19:00", "weekly_enabled": False, "weekly_day": 6, "weekly_time": "19:00"})
        await update.message.reply_text("👋 Добро пожаловать! Вы будете получать уведомления о весе. Настройте их в Mini App.")
    else:
        await update.message.reply_text("✅ Вы уже зарегистрированы. Используйте Mini App для управления весом и настройками.")
    # Отправим ссылку на Mini App (замените YOUR_BOT_USERNAME)
    await update.message.reply_text("📱 Откройте Mini App: https://t.me/YOUR_BOT_USERNAME/startapp")

# ---------- Flask эндпоинт для синхронизации ----------
@flask_app.route('/sync', methods=['POST'])
def sync():
    data = request.json
    chat_id = data.get('chat_id')
    if not chat_id:
        return jsonify({"error": "no chat_id"}), 400
    # (опционально проверка секрета)
    if data.get('type') == 'weight':
        date = data.get('date')
        weight = data.get('weight')
        if date and weight:
            add_weight(chat_id, date, weight)
    elif data.get('type') == 'settings':
        settings = data.get('settings')
        if settings:
            set_user_settings(chat_id, settings)
            # Перепланируем уведомления для этого пользователя
            asyncio.run_coroutine_threadsafe(reschedule_all_jobs(), bot_app.application.update_queue._loop)
    elif data.get('type') == 'reset':
        # удаляем все веса пользователя
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM weights WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()
    return jsonify({"status": "ok"})

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

# ---------- Основная функция ----------
def main():
    global bot_app, scheduler
    init_db()
    # Создаём бота
    application = Application.builder().token(TOKEN).build()
    bot_app = application
    application.add_handler(CommandHandler("start", start))
    # Запускаем планировщик
    scheduler = AsyncIOScheduler()
    # Загружаем задания после старта
    async def on_startup():
        await reschedule_all_jobs()
    application.job_queue = None  # не используем встроенный, используем apscheduler
    # Запускаем Flask в отдельном потоке
    threading.Thread(target=run_flask, daemon=True).start()
    # Установка вебхука (если задан WEBHOOK_URL)
    if WEBHOOK_URL:
        application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        # Тогда нужно добавить обработчик вебхука во Flask
        @flask_app.route('/webhook', methods=['POST'])
        async def webhook():
            update = Update.de_json(request.get_json(), application.bot)
            await application.process_update(update)
            return "ok"
        print(f"Webhook set to {WEBHOOK_URL}/webhook")
    else:
        # Иначе используем polling
        print("No WEBHOOK_URL, using polling")
    # Запускаем планировщик
    scheduler.start()
    # Запускаем бота
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()