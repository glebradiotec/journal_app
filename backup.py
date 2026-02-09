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
        f"📦 Бэкап БД journal_app\n"
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
    
    # Отправляем в Telegram
    send_to_telegram(backup_file)
    
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
