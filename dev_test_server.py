"""Запуск app.py с отдельной тестовой БД (instance/test_ui.db), чтобы не трогать
реальную синхронизированную с сервера БД при ручной проверке UI. Использовать только
локально для разработки/тестирования модуля «Публикация на сайте»."""
import os
import runpy
from pathlib import Path

BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)
(BASE_DIR / 'instance').mkdir(exist_ok=True)
os.environ['DATABASE_URL'] = f"sqlite:///{(BASE_DIR / 'instance' / 'test_ui.db').as_posix()}"
runpy.run_path('app.py', run_name='__main__')
