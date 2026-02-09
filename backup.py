import shutil
import os
import requests
from datetime import datetime, timedelta

TELEGRAM_BOT_TOKEN = '8568162243:AAFIJGHdgjb4swYCUuBU2pzMHggp9pRGMhA'
TELEGRAM_CHAT_ID = '134711555'


def send_to_telegram(backup_file):
    """Отправляет файл бэкапа в Telegram."""
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument'
    size_mb = os.path.getsize(backup_file) / 1024 / 1024
    caption = (
        f"📦 Бэкап БД Radiotec Journal App\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        f"💾 {size_mb:.1f} МБ"
    )
    try:
        with open(backup_file, 'rb') as f:
            resp = requests.post(url, data={
                'chat_id': TELEGRAM_CHAT_ID,
                'caption': caption,
            }, files={'document': (os.path.basename(backup_file), f)}, timeout=30)
        if resp.status_code == 200:
            print(f'Backup sent to Telegram')
        else:
            print(f'Telegram error: {resp.status_code} {resp.text}')
    except Exception as e:
        print(f'Telegram send failed: {e}')


def send_excel_to_telegram():
    """Скачивает Excel-выгрузку с сервера и отправляет в Telegram."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    backups_dir = os.path.join(base_dir, 'backups')
    os.makedirs(backups_dir, exist_ok=True)

    try:
        resp = requests.get('http://127.0.0.1:8001/admin/articles/bulk-export', timeout=30)
        if resp.status_code == 200:
            filename = f'articles_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            filepath = os.path.join(backups_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(resp.content)

            url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument'
            with open(filepath, 'rb') as f:
                resp = requests.post(url, data={
                    'chat_id': TELEGRAM_CHAT_ID,
                    'caption': f'📊 Excel-выгрузка статей\n📅 {datetime.now().strftime("%d.%m.%Y %H:%M")}',
                }, files={'document': (filename, f)}, timeout=30)

            os.remove(filepath)
            if resp.status_code == 200:
                print('Excel export sent to Telegram')
            else:
                print(f'Telegram error (excel): {resp.status_code}')
        else:
            print(f'Excel export failed: HTTP {resp.status_code}')
    except Exception as e:
        print(f'Excel export send failed: {e}')


def create_backup():
    # Файлы в папке instance
    base_dir = os.path.dirname(os.path.abspath(__file__))
    instance_dir = os.path.join(base_dir, 'instance')
    db_file = os.path.join(instance_dir, 'journal.db')
    backups_dir = os.path.join(base_dir, 'backups')
    
    if not os.path.exists(db_file):
        print(f'DB file not found: {db_file}')
        return
    
    os.makedirs(backups_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_file = os.path.join(backups_dir, f'journal_backup_{timestamp}.db')
    
    shutil.copy(db_file, backup_file)
    print(f'Backup created: {backup_file}')
    
    # Отправляем .db в Telegram
    send_to_telegram(backup_file)

    # Отправляем Excel-выгрузку
    send_excel_to_telegram()
    
    # Удаляем старые (старше 7 дней)
    for f in os.listdir(backups_dir):
        path = os.path.join(backups_dir, f)
        if os.path.isfile(path):
            age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(path))
            if age > timedelta(days=7):
                os.remove(path)
                print(f'Removed old backup: {f}')

if __name__ == '__main__':
    create_backup()
