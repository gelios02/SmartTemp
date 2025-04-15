import os
import sqlite3
import random
import datetime
import textwrap
import matplotlib.pyplot as plt
import requests
import paho.mqtt.client as mqtt
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from threading import Thread
import time

SENSOR_COLORS = {
    'tempC': 'red',
    'Humidity': 'blue',
    'q': 'green'
}

MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TOPICS = {
    "tempC": "sensors/temperature",
    "Humidity": "sensors/humidity",
    "q": "sensors/thermal"
}

DB_FILE = 'sensor_data.db'
ADMIN_CHAT_ID = None

SPIKE_THRESHOLD = 2.0

class MQTTClientHandler:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        print("Connected to MQTT Broker")
        for topic in TOPICS.values():
            client.subscribe(topic)
            print(f"Subscribed to: {topic}")

    def on_message(self, client, userdata, msg):
        sensor_type = None
        for key, topic in TOPICS.items():
            if msg.topic == topic:
                sensor_type = key
                break
        if sensor_type:
            try:
                value = float(msg.payload.decode())
                self.save_to_db(sensor_type, value)
                print(f"Received {sensor_type}: {value}")
            except ValueError:
                print(f"Invalid data for {sensor_type}")

    def save_to_db(self, sensor, value):
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO sensor_data (sensor, value, timestamp) VALUES (?, ?, ?)",
            (sensor, value, timestamp)
        )
        conn.commit()
        conn.close()

    def start(self):
        while True:
            try:
                self.client.connect(MQTT_BROKER, MQTT_PORT)
                break
            except Exception as e:
                print("Ошибка подключения к MQTT брокеру:", e)
                time.sleep(5)
        Thread(target=self.client.loop_forever, daemon=True).start()

def check_and_create_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor TEXT,
            value REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY
        )
    ''')
    conn.commit()
    conn.close()

def get_current_data(sensor):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value, timestamp FROM sensor_data WHERE sensor=? ORDER BY timestamp DESC LIMIT 1", (sensor,))
    result = c.fetchone()
    conn.close()
    return result

def get_data_period(sensor, start_time, end_time):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        SELECT value, timestamp FROM sensor_data 
        WHERE sensor=? AND timestamp BETWEEN ? AND ? 
        ORDER BY timestamp
    ''', (sensor, start_time, end_time))
    result = c.fetchall()
    conn.close()
    return result

def generate_graph(data, sensor):
    if not data:
        return None
    values = [row[0] for row in data]
    timestamps = [datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") for row in data]
    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, values, marker='o', color=SENSOR_COLORS.get(sensor, 'black'))
    plt.title(f'Изменение показаний {sensor}')
    plt.xlabel('Время')
    plt.ylabel('Значение')
    plt.grid(True)
    graph_file = f'{sensor}_graph.png'
    plt.savefig(graph_file)
    plt.close()
    print(f"Сохранён график {graph_file} для датчика {sensor}")
    return graph_file

def generate_alert_graph(sensor, period_minutes, alert_message):
    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(minutes=period_minutes)
    data = get_data_period(sensor, start_time.strftime("%Y-%m-%d %H:%M:%S"),
                           end_time.strftime("%Y-%m-%d %H:%M:%S"))
    if not data:
        print(f"Нет данных для графика аномалии по {sensor} за последние {period_minutes} минут.")
        return None
    values = [row[0] for row in data]
    timestamps = [datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") for row in data]
    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, values, marker='o', linestyle='-', label=sensor,
             color=SENSOR_COLORS.get(sensor, 'black'))
    plt.title(f'Изменение показаний {sensor} с обнаруженным перепадом')
    plt.xlabel('Время')
    plt.ylabel('Значение')
    plt.grid(True)
    wrapped_message = textwrap.fill(alert_message, width=30)
    plt.annotate(
        wrapped_message,
        xy=(timestamps[-1], values[-1]),
        xycoords='data',
        xytext=(-150, 20),
        textcoords='offset points',
        arrowprops=dict(facecolor='red', shrink=0.05),
        fontsize=12,
        color='red',
        ha='right'
    )
    plt.legend()
    alert_graph_file = f'{sensor}_alert_graph.png'
    plt.savefig(alert_graph_file)
    plt.close()
    print(f"Сохранён график с аномалией: {alert_graph_file}")
    return alert_graph_file

def get_weather_novosibirsk():
    url = "https://api.open-meteo.com/v1/forecast?latitude=55.0084&longitude=82.9357&current_weather=true"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["current_weather"]["temperature"]
    except Exception as e:
        print("Ошибка получения данных погоды:", e)
        return 5.0

def check_external_temperature_alert(threshold=SPIKE_THRESHOLD, margin=5.0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM sensor_data WHERE sensor='q' ORDER BY timestamp DESC LIMIT 5")
    rows = c.fetchall()
    conn.close()
    if len(rows) < 2:
        return False, None, None, None, None
    external_current = rows[0][0]
    previous_values = [row[0] for row in rows[1:]]
    avg_previous = sum(previous_values) / len(previous_values)
    diff_external = external_current - avg_previous
    abs_diff = abs(diff_external)
    external_temp_api = get_weather_novosibirsk()
    if diff_external > threshold and (external_current - external_temp_api) > margin:
        return True, abs_diff, "increase", external_current, external_temp_api
    if diff_external < -threshold and (external_temp_api - external_current) > margin:
        return True, abs_diff, "decrease", external_current, external_temp_api
    return False, abs_diff, None, external_current, external_temp_api

def check_internal_temperature_spike(threshold=10.0):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM sensor_data WHERE sensor='tempC' ORDER BY timestamp DESC LIMIT 5")
    rows = c.fetchall()
    conn.close()
    if len(rows) < 2:
        return False, None
    current = rows[0][0]
    previous_values = [row[0] for row in rows[1:]]
    avg_previous = sum(previous_values) / len(previous_values)
    diff = current - avg_previous
    if abs(diff) > threshold:
        return True, diff
    return False, diff

def check_spike_alert():
    spike_external, diff_external, direction, external_current, external_temp_api = check_external_temperature_alert()
    spike_internal, diff_internal = check_internal_temperature_spike()
    messages = []
    if spike_external:
        if direction == "increase":
            messages.append(f"внешней температуры (рост: изменение {diff_external:.2f}°C, тек. {external_current:.2f}°C, API: {external_temp_api:.2f}°C)")
        elif direction == "decrease":
            messages.append(f"внешней температуры (падение: изменение {diff_external:.2f}°C, тек. {external_current:.2f}°C, API: {external_temp_api:.2f}°C)")
    if spike_internal:
        if diff_internal > 0:
            messages.append(f"внутренней температуры (рост: изменение {diff_internal:.2f}°C)")
        else:
            messages.append(f"внутренней температуры (падение: изменение {diff_internal:.2f}°C)")
    if messages:
        return True, "; ".join(messages)
    return False, "Резкий перепад не обнаружен или данных недостаточно."

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def build_main_menu():
    keyboard = [
        [InlineKeyboardButton("Текущие показания", callback_data="get_current")],
        [InlineKeyboardButton("Данные за период", callback_data="get_period_menu")],
        [InlineKeyboardButton("Проверить перепад", callback_data="check_spike")]
    ]
    return InlineKeyboardMarkup(keyboard)

def build_period_menu():
    periods = [
        ("15 минут", 15),
        ("1 час", 60),
        ("4 часа", 240),
        ("6 часов", 360),
        ("12 часов", 720),
        ("1 день", 1440),
        ("2 дня", 2880),
        ("10 дней", 14400),
        ("1 месяц", 43200),
        ("2 месяца", 86400),
        ("1 год", 525600)
    ]
    keyboard = []
    row = []
    for name, minutes in periods:
        row.append(InlineKeyboardButton(name, callback_data=f"period:{minutes}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

async def send_main_menu(chat_id, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=chat_id, text="Выберите действие:", reply_markup=build_main_menu())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    if ADMIN_CHAT_ID is None:
        ADMIN_CHAT_ID = update.effective_chat.id
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (update.effective_chat.id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("Ошибка сохранения пользователя:", e)
    await update.message.reply_text("Добро пожаловать!", reply_markup=build_main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "get_current":
            temp_data = get_current_data('tempC')
            humidity_data = get_current_data('Humidity')
            q_data = get_current_data('q')
            text = "Текущие показания:\n"
            if temp_data:
                text += f"Внутренняя температура (tempC): {temp_data[0]:.2f} °C\n"
            if humidity_data:
                text += f"Влажность (Humidity): {humidity_data[0]:.2f} %\n"
            if q_data:
                text += f"Внешняя температура (q): {q_data[0]:.2f} °C\n"
            await query.edit_message_text(text=text)
            await send_main_menu(update.effective_chat.id, context)
        elif data == "get_period_menu":
            await query.edit_message_text(text="Выберите период:", reply_markup=build_period_menu())
        elif data.startswith("period:"):
            minutes = int(data.split(":")[1])
            end_time = datetime.datetime.now()
            start_time = end_time - datetime.timedelta(minutes=minutes)
            sensors = ['tempC', 'Humidity', 'q']
            messages = []
            for sensor in sensors:
                sensor_name = sensor
                data_records = get_data_period(sensor, start_time.strftime("%Y-%m-%d %H:%M:%S"),
                                               end_time.strftime("%Y-%m-%d %H:%M:%S"))
                if data_records:
                    graph_file = generate_graph(data_records, sensor)
                    messages.append(f"Данные за выбранный период для датчика: {sensor_name}")
                    if graph_file and os.path.exists(graph_file):
                        await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=open(graph_file, 'rb')
                        )
                    else:
                        print(f"Не удалось найти файл графика для {sensor_name}")
                else:
                    messages.append(f"Нет данных для датчика {sensor_name} за выбранный период.")
            await query.edit_message_text(text="\n".join(messages))
            await send_main_menu(update.effective_chat.id, context)
        elif data == "check_spike":
            spike, message = check_spike_alert()
            print("Результат проверки перепада:", spike, message)
            if spike:
                text = f"Обнаружен резкий перепад: {message}"
                await query.edit_message_text(text=text)
                internal_parts = [m.strip() for m in message.split(";") if "внутренней температуры" in m]
                if internal_parts:
                    internal_msg = "; ".join(internal_parts)
                else:
                    internal_msg = "Резкий перепад внутренней температуры"
                external_parts = [m.strip() for m in message.split(";") if "внешней температуры" in m]
                if external_parts:
                    external_msg = "; ".join(external_parts)
                else:
                    external_msg = "Резкий перепад внешней температуры"
                alert_graph_temp = generate_alert_graph("tempC", 360, internal_msg)
                if alert_graph_temp and os.path.exists(alert_graph_temp):
                    print("Отправляю график с аномалией для tempC")
                    await context.bot.send_photo(chat_id=update.effective_chat.id,
                                                 photo=open(alert_graph_temp, 'rb'))
                else:
                    print("График для tempC не создан или нет данных")
                alert_graph_q = generate_alert_graph("q", 360, external_msg)
                if alert_graph_q and os.path.exists(alert_graph_q):
                    print("Отправляю график с аномалией для q")
                    await context.bot.send_photo(chat_id=update.effective_chat.id,
                                                 photo=open(alert_graph_q, 'rb'))
                else:
                    print("График для q не создан или нет данных")
            else:
                text = "Резкий перепад не обнаружен или данных недостаточно."
                await query.edit_message_text(text=text)
            await send_main_menu(update.effective_chat.id, context)
        else:
            await query.edit_message_text(text="Неизвестная команда.")
            await send_main_menu(update.effective_chat.id, context)
    except Exception as e:
        print("Ошибка в button_handler:", e)
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                       text="Произошла ошибка при обработке вашего запроса.")

async def sensor_alert_job(context: ContextTypes.DEFAULT_TYPE):
    spike, message = check_spike_alert()
    if spike:
        users = get_all_users()
        for user in users:
            await context.bot.send_message(chat_id=user, text=f"Автоматический алерт! {message}")
            alert_graph_temp = generate_alert_graph("tempC", 360, message)
            if alert_graph_temp and os.path.exists(alert_graph_temp):
                await context.bot.send_photo(chat_id=user, photo=open(alert_graph_temp, 'rb'))
            alert_graph_q = generate_alert_graph("q", 360, message)
            if alert_graph_q and os.path.exists(alert_graph_q):
                await context.bot.send_photo(chat_id=user, photo=open(alert_graph_q, 'rb'))

def main():
    check_and_create_db()
    mqtt_handler = MQTTClientHandler()
    Thread(target=mqtt_handler.start, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.job_queue.run_repeating(sensor_alert_job, interval=300, first=10)
    print("Бот запущен, начинаем опрос обновлений...")
    app.run_polling()

if __name__ == '__main__':
    main()