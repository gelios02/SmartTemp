import os
import sqlite3
import random
import datetime
import textwrap
import matplotlib.pyplot as plt
import requests  # для работы с API погоды
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
)

# Файл БД SQLite
DB_FILE = 'sensor_data.db'

# Глобальные переменные для симуляции и уведомлений
simulation_counter = 0
ADMIN_CHAT_ID = None

SPIKE_THRESHOLD = 2.0  # Минимальное изменение для внутреннего датчика, чтобы считать резким скачком


# Функции работы с БД
def check_and_create_db():
    """Проверяет наличие БД и создаёт таблицу, если её нет."""
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
    conn.commit()
    conn.close()


def simulate_temp_data(timestamp=None):
    """Генерация нормальной температуры в комнате (18-25 °C).
       Заменить датчиком!!!"""
    temp = random.uniform(18, 25)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if timestamp:
        c.execute("INSERT INTO sensor_data (sensor, value, timestamp) VALUES (?, ?, ?)",
                  ("tempC", temp, timestamp))
    else:
        c.execute("INSERT INTO sensor_data (sensor, value) VALUES (?, ?)", ("tempC", temp))
    conn.commit()
    conn.close()
    return temp


def simulate_humidity_data(timestamp=None):
    """Генерация нормальной влажности в комнате (30-60 %).
       Заменить датчиком!!!"""
    humidity = random.uniform(30, 60)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if timestamp:
        c.execute("INSERT INTO sensor_data (sensor, value, timestamp) VALUES (?, ?, ?)",
                  ("Humidity", humidity, timestamp))
    else:
        c.execute("INSERT INTO sensor_data (sensor, value) VALUES (?, ?)", ("Humidity", humidity))
    conn.commit()
    conn.close()
    return humidity


def simulate_q_data(timestamp=None):
    """Генерация нормального теплового потока на улице (0-50 условных единиц).
       Заменить датчиком!!!"""
    q_value = random.uniform(0, 50)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if timestamp:
        c.execute("INSERT INTO sensor_data (sensor, value, timestamp) VALUES (?, ?, ?)",
                  ("q", q_value, timestamp))
    else:
        c.execute("INSERT INTO sensor_data (sensor, value) VALUES (?, ?)", ("q", q_value))
    conn.commit()
    conn.close()
    return q_value


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
    """Генерирует график изменения показаний датчика"""
    if not data:
        return None
    values = [row[0] for row in data]
    timestamps = [datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") for row in data]
    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, values, marker='o')
    plt.title(f'Изменение показаний {sensor}')
    plt.xlabel('Время')
    plt.ylabel('Значение')
    plt.grid(True)
    graph_file = f'{sensor}_graph.png'
    plt.savefig(graph_file)
    plt.close()
    return graph_file


def generate_alert_graph(sensor, period_minutes, alert_message):
    """
    Генерирует график за указанный период с аннотацией,
    выделяющей последний измеренный показатель и выводящей сообщение об аномалии.
    """
    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(minutes=period_minutes)
    data = get_data_period(sensor, start_time.strftime("%Y-%m-%d %H:%M:%S"),
                           end_time.strftime("%Y-%m-%d %H:%M:%S"))
    if not data:
        return None

    values = [row[0] for row in data]
    timestamps = [datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S") for row in data]

    plt.figure(figsize=(10, 5))
    plt.plot(timestamps, values, marker='o', linestyle='-', label=sensor)
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
    return alert_graph_file


def get_weather_novosibirsk():
    """
    Получает текущую температуру в Новосибирске с использованием API Open-Meteo.
    Координаты для Новосибирска: latitude=55.0084, longitude=82.9357.
    """
    url = "https://api.open-meteo.com/v1/forecast?latitude=55.0084&longitude=82.9357&current_weather=true"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["current_weather"]["temperature"]
    except Exception as e:
        print("Ошибка получения данных погоды:", e)
        return 5.0  # Значение по умолчанию


# проверка внутренней температуры с учётом внешней (API)
def check_internal_temperature_alert(threshold=SPIKE_THRESHOLD, margin=5.0):
    """
    Проверяет резкий перепад внутренней температуры (q) с учётом внешней температуры.
    Рассматриваются оба случая – резкий рост (increase) и резкий спад (decrease).

    Алгоритм:
      1. Извлекаются последние 5 значений внутренней температуры.
      2. Вычисляется разница: diff_internal = current - average(previous_values).
      3. Получается внешняя температура через API (get_weather_novosibirsk()).
      4. Если:
           - Для роста: diff_internal > threshold и (current - external_temp) > margin,
           - Для падения: diff_internal < -threshold и (external_temp - current) > margin,
         то считается, что произошёл резкий перепад.

    Возвращает:
      (alert_flag, abs_diff, direction, internal_current, external_temp)
      где direction = "increase" или "decrease", если аномалия обнаружена, иначе None.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM sensor_data WHERE sensor='q' ORDER BY timestamp DESC LIMIT 5")
    rows = c.fetchall()
    conn.close()
    if len(rows) < 2:
        return False, None, None, None, None
    internal_current = rows[0][0]
    previous_values = [row[0] for row in rows[1:]]
    avg_previous = sum(previous_values) / len(previous_values)
    diff_internal = internal_current - avg_previous
    abs_diff = abs(diff_internal)
    external_temp = get_weather_novosibirsk()
    # Проверка резкого роста
    if diff_internal > threshold and (internal_current - external_temp) > margin:
        return True, abs_diff, "increase", internal_current, external_temp
    # Проверка резкого падения
    if diff_internal < -threshold and (external_temp - internal_current) > margin:
        return True, abs_diff, "decrease", internal_current, external_temp
    return False, abs_diff, None, internal_current, external_temp


def check_thermal_flow_spike(threshold=10.0):
    """
    Проверяет резкий перепад для сенсора теплового потока (tempC) на основе последних 5 значений.
    Аргумент threshold задаёт фиксированный порог (например, 10 условных единиц).

    Возвращает (True, diff) если разница (с учётом направления) превышает порог, иначе (False, diff).
    """
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
    """
    Объединяющая функция, которая проверяет, произошёл ли резкий перепад либо внутренней температуры
    (с учетом внешней температуры через API, рассматривая рост и спад), либо показаний сенсора теплового потока (q).

    Если хотя бы одна проверка возвращает True, функция возвращает (True, сообщение),
    где сообщение содержит детали обнаруженного перепада.
    """
    spike_internal, diff_internal, direction, internal_current, external_temp = check_internal_temperature_alert()
    spike_q, diff_q = check_thermal_flow_spike()
    messages = []
    if spike_internal:
        if direction == "increase":
            messages.append(
                f"внутренней температуры (рост: изменение {diff_internal:.2f}°C, тек. {internal_current:.2f}°C, внеш. {external_temp:.2f}°C)"
            )
        elif direction == "decrease":
            messages.append(
                f"внутренней температуры (падение: изменение {diff_internal:.2f}°C, тек. {internal_current:.2f}°C, внеш. {external_temp:.2f}°C)"
            )
    if spike_q:
        if diff_q > 0:
            messages.append(f"теплового потока (рост: изменение {diff_q:.2f})")
        else:
            messages.append(f"теплового потока (падение: изменение {diff_q:.2f})")
    if messages:
        return True, "; ".join(messages)
    return False, "Резкий перепад не обнаружен или данных недостаточно."


# --- Фоновая задача симуляции данных ---
async def sensor_simulation_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Фоновая задача, которая каждые 5 минут генерирует данные.
    Каждые 10 минут (т.е. каждые две итерации) для температуры генерируется скачок.
    После генерации данных вызывается объединённая проверка (внутренняя температура + тепловой поток)
    для определения, нужно ли отправлять уведомление.

    Заменить датчиком!!! (При переходе на реальные данные – заменить вызовы симуляционных функций)
    """
    global simulation_counter, ADMIN_CHAT_ID
    simulation_counter += 1
    timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Генерируем данные для влажности и теплового потока
    simulate_humidity_data(timestamp_str)
    simulate_q_data(timestamp_str)
    # Для температуры: каждые две итерации генерируем скачок
    if simulation_counter % 2 == 0:
        temp_spike = random.uniform(30, 35)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO sensor_data (sensor, value, timestamp) VALUES (?, ?, ?)",
                  ("tempC", temp_spike, timestamp_str))
        conn.commit()
        conn.close()
    else:
        simulate_temp_data(timestamp_str)

    # Объединённая проверка: если обнаружен резкий перепад внутренней температуры или теплового потока,
    # отправляем уведомление.
    spike, message = check_spike_alert()
    if spike and ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"Alert: обнаружен резкий перепад: {message}!"
        )
        # Генерируем график с аннотацией для внутренних показаний, если перепад внутренней температуры обнаружен
        if "внутренней температуры" in message:
            alert_graph = generate_alert_graph("tempC", 60, message)
            if alert_graph and os.path.exists(alert_graph):
                await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=open(alert_graph, 'rb'))
        # Если перепад обнаружен по тепловому потоку, генерируем график для него
        if "теплового потока" in message:
            alert_graph = generate_alert_graph("q", 60, message)
            if alert_graph and os.path.exists(alert_graph):
                await context.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=open(alert_graph, 'rb'))


# Функции формирования меню
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
    await context.bot.send_message(
        chat_id=chat_id,
        text="Выберите действие:",
        reply_markup=build_main_menu()
    )


# --- Обработчики команд и кнопок Telegram ---
TOKEN = '7452678432:AAFXVD4xr1e8ygYmA1S14GIkUCFP_1IxaHc'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_CHAT_ID
    if ADMIN_CHAT_ID is None:
        ADMIN_CHAT_ID = update.effective_chat.id
    await update.message.reply_text("Добро пожаловать!", reply_markup=build_main_menu())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "get_current":
        temp_data = get_current_data('tempC')
        humidity_data = get_current_data('Humidity')
        q_data = get_current_data('q')
        text = "Текущие показания:\n"
        if temp_data:
            text += f"Температура (в комнате): {temp_data[0]:.2f} °C\n"
        if humidity_data:
            text += f"Влажность (в комнате): {humidity_data[0]:.2f} %\n"
        if q_data:
            text += f"Тепловой поток (на улице): {q_data[0]:.2f}\n"
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
            sensor_name = "Неизвестно"
            if sensor == 'tempC':
                sensor_name = "Температура (в комнате)"
            elif sensor == 'Humidity':
                sensor_name = "Влажность (в комнате)"
            elif sensor == 'q':
                sensor_name = "Тепловой поток (на улице)"
            data_records = get_data_period(sensor, start_time.strftime("%Y-%m-%d %H:%M:%S"),
                                           end_time.strftime("%Y-%m-%d %H:%M:%S"))
            if data_records:
                graph_file = generate_graph(data_records, sensor)
                messages.append(f"Данные за выбранный период для {sensor_name}:")
                if graph_file and os.path.exists(graph_file):
                    await context.bot.send_photo(chat_id=update.effective_chat.id,
                                                 photo=open(graph_file, 'rb'))
            else:
                messages.append(f"Нет данных для {sensor_name} за выбранный период.")
        await query.edit_message_text(text="\n".join(messages))
        await send_main_menu(update.effective_chat.id, context)

    elif data == "check_spike":
        spike, message = check_spike_alert()
        if spike:
            text = f"Обнаружен резкий перепад: {message}"
            await query.edit_message_text(text=text)
            # Если аномалия обнаружена, выводим график для соответствующего датчика
            if "внутренней температуры" in message:
                alert_graph = generate_alert_graph("tempC", 60, message)
                if alert_graph and os.path.exists(alert_graph):
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(alert_graph, 'rb'))
            if "теплового потока" in message:
                alert_graph = generate_alert_graph("q", 60, message)
                if alert_graph and os.path.exists(alert_graph):
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=open(alert_graph, 'rb'))
        else:
            text = "Резкий перепад не обнаружен или данных недостаточно."
            await query.edit_message_text(text=text)
        await send_main_menu(update.effective_chat.id, context)

    else:
        await query.edit_message_text(text="Неизвестная команда.")
        await send_main_menu(update.effective_chat.id, context)

def main():
    check_and_create_db()  # Создаём базу
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    # Планировщик фоновой задачи: каждые 5 минут
    job_queue = app.job_queue  # Для работы JobQueue
    job_queue.run_repeating(sensor_simulation_job, interval=300, first=10)

    print("Бот запущен, начинаем опрос обновлений...")
    app.run_polling()

if __name__ == '__main__':
    main()
