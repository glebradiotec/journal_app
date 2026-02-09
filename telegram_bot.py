"""
Telegram-бот для Radiotec Journal App.
Слушает команды и отправляет бэкапы / Excel-выгрузки.
Запускается как systemd-сервис (journal-bot.service).
"""

import os
from dotenv import load_dotenv
load_dotenv()  # Загружаем переменные из .env файла

import time
import sqlite3
import requests
import shutil
from datetime import datetime

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
BOT_PASSWORD = os.environ.get('BOT_PASSWORD', '1845')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_APP_URL = 'http://127.0.0.1:8001'

API = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}'
POLL_INTERVAL = 2  # секунды

# Авторизованные пользователи (chat_id -> True). Владелец всегда авторизован.
_authorized_users = {TELEGRAM_CHAT_ID: True}


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


def generate_excel(filepath):
    """Генерирует Excel-выгрузку статей напрямую из SQLite."""
    from excel_export import generate_articles_excel

    db_path = os.path.join(BASE_DIR, 'instance', 'journal.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    db_rows = c.execute('''
        SELECT a.id, a.title, a.authors, a.submission_date,
               a.payment_received, a.has_review, a.edited, a.has_expertise_act,
               j.name as journal_name, i.number as issue_num, i.year as issue_year
        FROM article a
        JOIN issue i ON a.issue_id = i.id
        JOIN journal j ON i.journal_id = j.id
        ORDER BY a.id DESC
    ''').fetchall()

    authors_map = {}
    for row in c.execute('SELECT article_id, full_name FROM article_author ORDER BY "order"'):
        authors_map.setdefault(row[0], []).append(row[1])
    conn.close()

    # Преобразуем в формат для общего модуля
    rows = []
    for article in db_rows:
        authors = ', '.join(authors_map.get(article['id'], [])) or article['authors'] or '-'
        issue_info = f"№{article['issue_num']}/{article['issue_year']}" if article['issue_num'] else '-'
        rows.append({
            'title': article['title'],
            'authors': authors,
            'journal_name': article['journal_name'],
            'issue_info': issue_info,
            'submission_date': article['submission_date'],
            'payment': bool(article['payment_received']),
            'review': bool(article['has_review']),
            'edited': bool(article['edited']),
            'expertise': bool(article['has_expertise_act']),
        })

    output, total = generate_articles_excel(rows)

    with open(filepath, 'wb') as f:
        f.write(output.read())

    return total


def handle_export(chat_id):
    """Генерирует и отправляет Excel-выгрузку."""
    send_message(chat_id, '⏳ Генерирую Excel-выгрузку...')
    try:
        filename = f'articles_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        filepath = os.path.join(BASE_DIR, 'backups', filename)
        os.makedirs(os.path.join(BASE_DIR, 'backups'), exist_ok=True)
        total = generate_excel(filepath)
        send_document(chat_id, filepath,
            f'📊 Excel-выгрузка статей ({total} шт.)\n📅 {datetime.now().strftime("%d.%m.%Y %H:%M")}')
        os.remove(filepath)
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
        with_expertise = c.execute('SELECT COUNT(*) FROM article WHERE has_expertise_act = 1').fetchone()[0]
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
            f'✏️ Отредактировано: <b>{edited}</b> / {total}\n'
            f'📋 С актом эксп.: <b>{with_expertise}</b> / {total}'
        )
    except Exception as e:
        send_message(chat_id, f'❌ Ошибка: {e}')


def is_authorized(chat_id):
    """Проверяет, авторизован ли пользователь."""
    return str(chat_id) in _authorized_users


def process_update(update):
    """Обработать одно обновление от Telegram."""
    msg = update.get('message')
    if not msg:
        return

    chat_id = msg['chat']['id']
    text = msg.get('text', '').strip()

    # /start доступен всем
    if text == '/start':
        if is_authorized(chat_id):
            handle_start(chat_id)
        else:
            send_message(chat_id,
                '🔒 <b>Radiotec Journal App</b>\n\n'
                'Для доступа введите пароль:'
            )
        return

    # Если не авторизован — проверяем пароль
    if not is_authorized(chat_id):
        if text == BOT_PASSWORD:
            _authorized_users[str(chat_id)] = True
            user_name = msg.get('from', {}).get('first_name', 'Пользователь')
            # Уведомляем владельца
            send_message(TELEGRAM_CHAT_ID,
                f'🔓 Новый пользователь получил доступ к боту:\n'
                f'👤 {user_name} (ID: {chat_id})')
            send_message(chat_id,
                '✅ Доступ разрешён! Добро пожаловать.',
                reply_markup=get_main_keyboard()
            )
        else:
            send_message(chat_id, '❌ Неверный пароль. Попробуйте ещё раз.')
        return

    # Авторизованные команды
    if text in ('/export', '📊 Excel-выгрузка'):
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
