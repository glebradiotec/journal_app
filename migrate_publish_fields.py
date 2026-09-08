"""Добавляет новые колонки в таблицы модуля «Публикация на сайте» (pub_issue/pub_article),
если их там ещё нет. Новые таблицы (pub_section) миграции не требуют — их создаёт
db.create_all() при старте app.py."""
import os
import re
import sqlite3

from dotenv import load_dotenv

load_dotenv()

url = os.environ.get('DATABASE_URL', 'sqlite:///journal.db')
m = re.match(r'sqlite:///(.+)', url)
if not m:
    raise SystemExit(f'Эта миграция — только для SQLite. DATABASE_URL: {url}')
db_path = m.group(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

columns = [
    ('pub_issue', 'volume', 'VARCHAR(20)'),
    ('pub_article', 'date_approved', 'VARCHAR(20)'),
    ('pub_article', 'citation_ru', 'TEXT'),
    ('pub_article', 'citation_en', 'TEXT'),
    ('pub_article', 'references_en_text', 'TEXT'),
]

for table, col, typ in columns:
    try:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {typ}')
        print(f'Added {table}.{col}')
    except Exception as e:
        print(f'{table}.{col} already exists or error: {e}')

conn.commit()
conn.close()
print(f'Migration done ({db_path})')
