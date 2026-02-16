"""Добавляет колонки deleted_at для корзины (Article, Issue). Запустить один раз: python migrate_deleted_at.py"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'journal.db')
if not os.path.isfile(db_path):
    print('DB not found:', db_path)
    exit(1)

conn = sqlite3.connect(db_path)
c = conn.cursor()

for table, col in [('article', 'deleted_at'), ('issue', 'deleted_at')]:
    try:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {col} DATETIME')
        print(f'Added {table}.{col}')
    except sqlite3.OperationalError as e:
        if 'duplicate column' in str(e).lower():
            print(f'{table}.{col} already exists')
        else:
            raise

conn.commit()
conn.close()
print('Migration done')
