"""Добавляет колонку submitted_by_user_id в article (подачи из кабинета автора). Запустить один раз: python migrate_author_submitted.py"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'journal.db')
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
