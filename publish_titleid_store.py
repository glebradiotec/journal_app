"""Запоминает elibrary-titleid по ISSN, чтобы не вводить его заново для каждого нового выпуска
одного и того же журнала.

Порт xml_import/titleid_store.py.
"""
import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "instance", "publish_titleids.json")

_SEED = {
    "0033-8486": "7978",   # Радиотехника
    "0320-9601": "7662",   # Антенны
    "2070-0784": "9782",   # Успехи современной радиоэлектроники
    "1560-4128": "8291",   # Электромагнитные волны и электронные системы
    "1560-4136": "25238",  # Биомедицинская радиоэлектроника
    "1999-8554": "7915",   # Нейрокомпьютеры: разработка, применение
    "1999-8465": "7913",   # Наукоемкие технологии
    "2070-0814": "7841",   # Информационно-измерительные и управляющие системы
    "2070-0970": "7917",   # Нелинейный мир
    "2070-0997": "9620",   # Технологии живых систем
    "2072-9472": "10621",  # Системы высокой доступности
    "1999-7493": "30242",  # Динамика сложных систем - XXI век
    "2225-0980": "37415",  # Нанотехнологии: разработка, применение - XXI век
}


def load():
    if not os.path.exists(STORE_PATH):
        return dict(_SEED)
    with open(STORE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    merged = dict(_SEED)
    merged.update(data)
    return merged


def save(issn, titleid):
    if not issn or not titleid:
        return
    data = load()
    data[issn] = titleid
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
