import shutil
import os
from datetime import datetime, timedelta

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
