"""
Telegram-бот для Radiotec Journal App.
Слушает команды и отправляет бэкапы / Excel-выгрузки.
Запускается как systemd-сервис (journal-bot.service).
"""

import os
import time
import requests
import shutil
from datetime import datetime

TELEGRAM_BOT_TOKEN = '8568162243:AAFIJGHdgjb4swYCUuBU2pzMHggp9pRGMhA'
TELEGRAM_CHAT_ID = '134711555'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_APP_URL = 'http://127.0.0.1:8001'

API = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}'
POLL_INTERVAL = 2  # секунды


def send_message(chat_id, text, reply_markup=None):
    """Отправить текстовое сообщение."""
    data = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        import json
        data['reply_markup'] = json.dumps(reply_markup)
    requests.post(f'{API}/sendMessage', data=data, timeout=15)


def send_document(chat_id, filepath, caption=''):
    """Отправить файл."""
    with open(filepath, 'rb') as f:
        requests.post(f'{API}/sendDocument', data={
            'chat_id': chat_id,
            'caption': caption,
        }, files={'document': (os.path.basename(filepath), f)}, timeout=60)


def get_main_keyboard():
    """Клавиатура с основными кнопками."""
    return {
        'keyboard': [
            [{'text': '📊 Excel-выгрузка'}, {'text': '💾 Бэкап БД'}],
            [{'text': '📈 Статистика'}],
        ],
        'resize_keyboard': True,
    }


def handle_start(chat_id):
    """Обработка /start."""
    send_message(chat_id,
        '👋 <b>Radiotec Journal App</b>\n\n'
        '📦 Каждый день в 3:00 сюда приходит бэкап БД и Excel-выгрузка.\n\n'
        'Доступные действия:',
        reply_markup=get_main_keyboard()
    )


def handle_export(chat_id):
    """Скачать Excel с сервера и отправить в Telegram."""
    send_message(chat_id, '⏳ Генерирую Excel-выгрузку...')
    try:
        resp = requests.get(f'{LOCAL_APP_URL}/admin/articles/bulk-export', timeout=30)
        if resp.status_code == 200:
            filename = f'articles_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            filepath = os.path.join(BASE_DIR, 'backups', filename)
            os.makedirs(os.path.join(BASE_DIR, 'backups'), exist_ok=True)
            with open(filepath, 'wb') as f:
                f.write(resp.content)
            send_document(chat_id, filepath,
                f'📊 Excel-выгрузка статей\n📅 {datetime.now().strftime("%d.%m.%Y %H:%M")}')
            os.remove(filepath)
        else:
            send_message(chat_id, f'❌ Ошибка при генерации Excel (код {resp.status_code})')
    except Exception as e:
        send_message(chat_id, f'❌ Ошибка: {e}')


def handle_backup(chat_id):
    """Создать бэкап БД и отправить."""
    send_message(chat_id, '⏳ Создаю бэкап БД...')
    try:
        from backup import create_backup
        create_backup()

        # Найти последний бэкап
        backups_dir = os.path.join(BASE_DIR, 'backups')
        files = sorted(
            [f for f in os.listdir(backups_dir) if f.startswith('journal_backup_')],
            reverse=True
        )
        if files:
            filepath = os.path.join(backups_dir, files[0])
            size_mb = os.path.getsize(filepath) / 1024 / 1024
            send_document(chat_id, filepath,
                f'💾 Бэкап БД\n📅 {datetime.now().strftime("%d.%m.%Y %H:%M")}\n📦 {size_mb:.1f} МБ')
        else:
            send_message(chat_id, '❌ Бэкап создан, но файл не найден')
    except Exception as e:
        send_message(chat_id, f'❌ Ошибка: {e}')


def handle_stats(chat_id):
    """Получить статистику из приложения."""
    try:
        import sqlite3
        db_path = os.path.join(BASE_DIR, 'instance', 'journal.db')
        conn = sqlite3.connect(db_path)
        c = conn.cursor()

        total = c.execute('SELECT COUNT(*) FROM article').fetchone()[0]
        paid = c.execute('SELECT COUNT(*) FROM article WHERE payment_received = 1').fetchone()[0]
        reviewed = c.execute('SELECT COUNT(*) FROM article WHERE has_review = 1').fetchone()[0]
        edited = c.execute('SELECT COUNT(*) FROM article WHERE edited = 1').fetchone()[0]
        journals = c.execute('SELECT COUNT(*) FROM journal').fetchone()[0]
        issues = c.execute('SELECT COUNT(*) FROM issue').fetchone()[0]
        conn.close()

        send_message(chat_id,
            f'📈 <b>Статистика Radiotec Journal App</b>\n\n'
            f'📚 Журналов: <b>{journals}</b>\n'
            f'📖 Выпусков: <b>{issues}</b>\n'
            f'📄 Статей: <b>{total}</b>\n\n'
            f'💰 Оплачено: <b>{paid}</b> / {total}\n'
            f'📝 С рецензией: <b>{reviewed}</b> / {total}\n'
            f'✏️ Отредактировано: <b>{edited}</b> / {total}'
        )
    except Exception as e:
        send_message(chat_id, f'❌ Ошибка: {e}')


def process_update(update):
    """Обработать одно обновление от Telegram."""
    msg = update.get('message')
    if not msg:
        return

    chat_id = msg['chat']['id']
    text = msg.get('text', '').strip()

    # Проверяем, что это наш чат
    if str(chat_id) != TELEGRAM_CHAT_ID:
        send_message(chat_id, '⛔ Доступ запрещён.')
        return

    if text == '/start':
        handle_start(chat_id)
    elif text in ('/export', '📊 Excel-выгрузка'):
        handle_export(chat_id)
    elif text in ('/backup', '💾 Бэкап БД'):
        handle_backup(chat_id)
    elif text in ('/stats', '📈 Статистика'):
        handle_stats(chat_id)
    else:
        send_message(chat_id,
            'Используйте кнопки или команды:\n'
            '/export — Excel-выгрузка\n'
            '/backup — Бэкап БД\n'
            '/stats — Статистика',
            reply_markup=get_main_keyboard()
        )


def main():
    """Основной цикл polling."""
    print(f'[{datetime.now()}] Telegram bot started (polling)')
    offset = None

    while True:
        try:
            params = {'timeout': 30}
            if offset:
                params['offset'] = offset
            resp = requests.get(f'{API}/getUpdates', params=params, timeout=35)
            data = resp.json()

            if data.get('ok') and data.get('result'):
                for update in data['result']:
                    offset = update['update_id'] + 1
                    try:
                        process_update(update)
                    except Exception as e:
                        print(f'Error processing update: {e}')

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            print(f'Polling error: {e}')
            time.sleep(5)


if __name__ == '__main__':
    main()
