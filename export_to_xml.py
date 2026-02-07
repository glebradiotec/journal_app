import sqlite3
import xml.etree.ElementTree as ET
from xml.dom import minidom
import os

# Подключение к БД
db_path = 'instance/journal.db'
if not os.path.exists(db_path):
    print("❌ База данных не найдена!")
    exit()

conn = sqlite3.connect(db_path)
c = conn.cursor()

# Создаем корневой элемент
root = ET.Element('database')
root.set('name', 'journal_app')

# Получаем все таблицы (кроме alembic_version)
c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version';")
tables = c.fetchall()

for table in tables:
    table_name = table[0]
    
    # Создаем элемент таблицы
    table_elem = ET.SubElement(root, 'table')
    table_elem.set('name', table_name)
    
    # Получаем структуру таблицы (PRAGMA безопасен — table_name из sqlite_master)
    c.execute("PRAGMA table_info([{}])".format(table_name.replace(']', ']]')))
    columns = c.fetchall()
    
    # Добавляем информацию о колонках
    schema_elem = ET.SubElement(table_elem, 'schema')
    for col in columns:
        col_elem = ET.SubElement(schema_elem, 'column')
        col_elem.set('name', col[1])
        col_elem.set('type', col[2])
        col_elem.set('nullable', 'NO' if col[3] else 'YES')
    
    # Получаем все записи
    c.execute("SELECT * FROM [{}]".format(table_name.replace(']', ']]')))
    rows = c.fetchall()
    
    # Добавляем данные
    data_elem = ET.SubElement(table_elem, 'data')
    data_elem.set('count', str(len(rows)))
    
    for row in rows:
        row_elem = ET.SubElement(data_elem, 'row')
        for i, value in enumerate(row):
            col_name = columns[i][1]
            col_elem = ET.SubElement(row_elem, col_name)
            col_elem.text = str(value) if value is not None else ''

conn.close()

# Красивое форматирование XML
def prettify(elem):
    rough_string = ET.tostring(elem, encoding='utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ")

# Сохраняем в файл
with open('journal_database.xml', 'w', encoding='utf-8') as f:
    f.write(prettify(root))

print("✅ XML файл создан: journal_database.xml")
print(f"📊 Таблиц: {len(tables)}")
