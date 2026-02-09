import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'journal.db')
conn = sqlite3.connect(db_path)
c = conn.cursor()

for col, typ in [('has_expertise_act', 'BOOLEAN DEFAULT 0'), ('expertise_act_file', 'VARCHAR(500)')]:
    try:
        c.execute(f'ALTER TABLE article ADD COLUMN {col} {typ}')
        print(f'Added {col}')
    except Exception as e:
        print(f'{col} already exists or error: {e}')

conn.commit()
conn.close()
print('Migration done')
