"""Запоминает elibrary-titleid по ISSN, чтобы не вводить его заново для каждого нового выпуска
одного и того же журнала.

Порт xml_import/titleid_store.py.
"""
import json
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "instance", "publish_titleids.json")

_SEED = {
    "0033-8486": "7978",  # Радиотехника
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
