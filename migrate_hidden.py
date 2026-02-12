import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'journal.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()

try:
    c.execute('ALTER TABLE journal ADD COLUMN is_hidden BOOLEAN DEFAULT 0')
    print('Added is_hidden column')
except Exception as e:
    print(f'is_hidden already exists or error: {e}')

conn.commit()
conn.close()
print('Migration done')
