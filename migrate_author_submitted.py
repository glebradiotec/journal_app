"""Добавляет колонку submitted_by_user_id в article (подачи из кабинета автора). Запустить один раз: python migrate_author_submitted.py
На сервере: cd /var/www/journal_app && venv/bin/python migrate_author_submitted.py"""
import sqlite3
import os

# Как в app: DATABASE_URL или sqlite по умолчанию
db_url = os.environ.get('DATABASE_URL', 'sqlite:///journal.db')
if db_url.startswith('sqlite:///'):
    path = db_url.replace('sqlite:///', '')
    if path == ':memory:' or not path:
        print('Cannot migrate in-memory DB')
        exit(1)
    # Относительный путь — от корня проекта
    if not os.path.isabs(path):
        base = os.path.dirname(os.path.abspath(__file__))
        db_path = os.path.normpath(os.path.join(base, path))
        if not os.path.isfile(db_path) and path == 'journal.db':
            db_path = os.path.join(base, 'instance', 'journal.db')
    else:
        db_path = path
else:
    print('Only SQLite supported by this script. DATABASE_URL:', db_url[:50])
    exit(1)

if not os.path.isfile(db_path):
    print('DB not found:', db_path)
    exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()
try:
    c.execute('ALTER TABLE article ADD COLUMN submitted_by_user_id INTEGER REFERENCES user(id)')
    print('Added article.submitted_by_user_id')
except sqlite3.OperationalError as e:
    if 'duplicate column' in str(e).lower():
        print('article.submitted_by_user_id already exists')
    else:
        raise
conn.commit()
conn.close()
print('Migration done')
