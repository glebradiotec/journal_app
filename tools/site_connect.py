#!/usr/bin/env python3
"""Подключение приложения к боевой базе сайта radiotec.ru (MySQL).

  setup   спрашивает хост, логин, пароль ЗДЕСЬ, в терминале; проверяет подключение, и только если оно работает,
          записывает SITE_DATABASE_URL в .env (старый .env сохраняется) и перезагружает приложение.
          Пароль не печатается, не попадает в историю команд и не передаётся никуда, кроме базы и файла .env.
  ping    ТОЛЬКО ЧТЕНИЕ: к какой базе подключено приложение, какие права у пользователя, какие журналы и
          выпуски там есть. Ничего не пишет.

Запуск на сервере:  cd /var/www/journal_app && venv/bin/python3 tools/site_connect.py setup
"""
import getpass
import os
import re
import shutil
import sys
from urllib.parse import quote_plus

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENV_PATH = os.path.join(ROOT, ".env")
TABLES = ("journals", "nomera", "razdel_numbers", "articles")


def build_url(host, port, user, password, database):
    """Пароль и логин кодируются: символы вроде @ : / # в пароле иначе ломают адрес."""
    return f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{int(port)}/{database}?charset=utf8mb4"


def mask(url):
    """Адрес без пароля — его можно показывать на экране и в отчётах."""
    return re.sub(r"(://[^:/@]*):[^@]*@", r"\1:***@", url or "")


def _engine(url):
    from sqlalchemy import create_engine
    return create_engine(url, connect_args={"connect_timeout": 10} if url.startswith("mysql") else {})


def ping(url=None):
    from sqlalchemy import text
    import publish_config
    url = url or publish_config.SITE_DATABASE_URL
    if not url:
        print("SITE_DATABASE_URL не задан: приложение работает с локальной тестовой базой, на сайт ничего не пишется.")
        return 1
    print("Подключение:", mask(url))
    eng = _engine(url)
    with eng.connect() as c:
        dialect = eng.dialect.name
        print("СУБД:", dialect, c.execute(text("SELECT VERSION()" if dialect == "mysql" else "SELECT sqlite_version()")).scalar())
        if dialect == "mysql":
            print("База:", c.execute(text("SELECT DATABASE()")).scalar())
            grants = [re.sub(r"IDENTIFIED BY.*", "", r[0]) for r in c.execute(text("SHOW GRANTS FOR CURRENT_USER()"))]
            print("Права пользователя:")
            for g in grants:
                print("   ", g.strip())
        for t in TABLES:
            try:
                print(f"таблица {t}: строк", c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar())
            except Exception as e:  # noqa: BLE001
                print(f"таблица {t}: НЕДОСТУПНА ({str(e)[:90]})")
        rows = c.execute(text("SELECT journ_id, menu_name, issn FROM journals ORDER BY journ_id")).fetchall()
        print("\nЖурналы на сайте и их последние выпуски:")
        for jid, name, issn in rows:
            last = c.execute(text("SELECT num_year, num_num FROM nomera WHERE jr_num = :j ORDER BY num_year DESC, num_id DESC LIMIT 1"),
                             {"j": jid}).fetchone()
            print(f"   {issn or '—':10} {name[:44]:44} последний: {f'{last[0]} №{last[1]}' if last else 'нет выпусков'}")
    return 0


def setup():
    import publish_config  # noqa: F401  (проверка, что модуль настроек читается)
    current = open(ENV_PATH, encoding="utf-8").read() if os.path.exists(ENV_PATH) else ""
    if re.search(r"^SITE_DATABASE_URL=", current, re.M):
        if input("SITE_DATABASE_URL уже задан в .env. Заменить? [y/N] ").strip().lower() != "y":
            print("Ничего не изменено.")
            return 1
    print("Подключение к базе сайта radiotec.ru. Пароль при вводе не показывается.")
    host = input("Хост базы: ").strip()
    port = input("Порт [3306]: ").strip() or "3306"
    user = input("Пользователь: ").strip()
    database = input("Имя базы [radiotec_main4]: ").strip() or "radiotec_main4"
    password = getpass.getpass("Пароль: ")
    if not (host and user and password):
        print("Нужны хост, пользователь и пароль. Ничего не изменено.")
        return 1
    url = build_url(host, port, user, password, database)
    print("\nПроверяю подключение…")
    try:
        code = ping(url)
    except Exception as e:  # noqa: BLE001
        print("Подключиться не удалось:", re.sub(re.escape(password), "***", str(e))[:300])
        print("Ничего не изменено: .env не тронут.")
        return 1
    if code:
        return code
    if input("\nПодключение работает. Записать в .env и применить? [y/N] ").strip().lower() != "y":
        print("Ничего не изменено.")
        return 1
    shutil.copy2(ENV_PATH, ENV_PATH + ".bak_before_site") if os.path.exists(ENV_PATH) else None
    lines = [l for l in current.splitlines() if not l.startswith("SITE_DATABASE_URL=")]
    open(ENV_PATH, "w", encoding="utf-8").write("\n".join(lines).rstrip("\n") + f"\nSITE_DATABASE_URL={url}\n")
    os.chmod(ENV_PATH, 0o600)
    print("Записано в .env (права 600), копия прежнего файла — .env.bak_before_site.")
    try:
        import subprocess
        pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "journal.service"],
                             capture_output=True, text=True).stdout.strip()
        if pid and pid != "0":
            os.kill(int(pid), 1)  # SIGHUP — мягкая перезагрузка воркеров, как при деплое
            print("Приложение перезагружено.")
    except Exception as e:  # noqa: BLE001
        print("Перезагрузите приложение вручную (kill -HUP главного процесса gunicorn):", e)
    print("\nГотово. Теперь «Записать на сайт» пишет в БОЕВУЮ базу.")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"setup": setup, "ping": ping}.get(cmd, lambda: print(__doc__) or 2)())
