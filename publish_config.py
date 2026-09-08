"""Настройки модуля «Публикация на сайте» (не путать с основной БД journal_app).

Порт xml_import/config.py. SITE_DATABASE_URL по умолчанию не задан — модуль работает
в режиме предпросмотра без реальной записи в БД сайта, пока переменная не будет
явно указана (после того как боевой доступ будет протестирован и подтверждён).
"""
import os

SITE_DATABASE_URL = os.environ.get("SITE_DATABASE_URL", "")

CROSSREF_DEPOSITOR_NAME = os.environ.get("CROSSREF_DEPOSITOR_NAME", "Radiotekhnika")
CROSSREF_DEPOSITOR_EMAIL = os.environ.get("CROSSREF_DEPOSITOR_EMAIL", "andrianov4444@gmail.com")
CROSSREF_REGISTRANT = os.environ.get("CROSSREF_REGISTRANT", "Radiotekhnika Publishing House")

ARTICLE_URL_TEMPLATE = os.environ.get(
    "ARTICLE_URL_TEMPLATE",
    "https://radiotec.ru/en/journal/{journal_link}/number/{year}-{number}/article/{art_id}",
)

DEFAULT_ARTICLE_PRICE = int(os.environ.get("DEFAULT_ARTICLE_PRICE", "350"))

SITE_UPLOADS_DIR = os.environ.get(
    "SITE_UPLOADS_DIR", os.path.join(os.path.dirname(__file__), "uploads", "publish_manuscripts")
)
