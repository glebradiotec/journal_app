import sqlite3
import os

# Проверь обе локации
db_locations = [
    'journal.db',
    'journals.db',
    'instance/journal.db',
    'instance/journals.db'
]

found = False
for db_file in db_locations:
    if os.path.exists(db_file):
        found = True
        print(f"\n{'='*70}")
        print(f"📊 АНАЛИЗ {db_file}")
        print("="*70)
        
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        
        # Таблицы
        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = c.fetchall()
        
        if tables:
            print(f"✅ Таблицы: {', '.join([t[0] for t in tables])}")
            
            # Статистика
            print("\n📈 КОЛИЧЕСТВО ЗАПИСЕЙ:")
            for table in tables:
                table_name = table[0]
                c.execute("SELECT COUNT(*) FROM [{}]".format(table_name.replace(']', ']]')))
                count = c.fetchone()[0]
                print(f"  ➜ {table_name}: {count} записей")
            
            # Первые 3 записи
            print("\n📋 ПЕРВЫЕ 3 ЗАПИСИ:")
            for table in tables:
                table_name = table[0]
                print(f"\n{table_name.upper()}:")
                c.execute("SELECT * FROM [{}] LIMIT 3".format(table_name.replace(']', ']]')))
                rows = c.fetchall()
                if rows:
                    for row in rows:
                        print(f"  {row}")
                else:
                    print("  (пусто)")
        else:
            print("❌ Таблиц не найдено!")
        
        conn.close()

if not found:
    print("❌ База данных не найдена!")
    print("Проверь наличие в: journal.db, journals.db, instance/journal.db")

print("\n✅ Готово!")
