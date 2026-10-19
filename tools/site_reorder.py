#!/usr/bin/env python3
"""Ставит статьи выпуска на сайте в порядок страниц (когда статью дописали позже и она оказалась в конце списка).

Сайт выводит статьи выпуска по номеру art_id, а у дописанной позже статьи он больше, чем у соседей. Утилита берёт номера
art_id, которые выпуск УЖЕ занимает на сайте, и раздаёт их заново по порядку страниц. Чужие номера не затрагиваются, ничего не
удаляется. Страницы статей, чей номер поменялся, получают новый адрес — старый перестаёт открываться.

  python3 tools/site_reorder.py ID_ВЫПУСКА            — только показывает, что изменится (в базу ничего не пишет)
  python3 tools/site_reorder.py ID_ВЫПУСКА --apply    — переставляет номера в БД сайта и в черновике

Перед записью проверяется, что под каждым номером на сайте лежит статья с тем же DOI, что в черновике; иначе утилита не
пишет ничего. Запускать на сервере приложения из папки проекта.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as flask_app  # noqa: E402  (сначала app: он читает .env, потом остальное)
from sqlalchemy import text  # noqa: E402

import site_db  # noqa: E402
from models import db  # noqa: E402
from models_publish import PubIssue  # noqa: E402
from routes_publish_hub import PUB_EXPORTS_FOLDER  # noqa: E402

TEMP_BASE = 60000   # art_id на сайте — smallint unsigned (до 65535); выше любого занятого номера


def plan(articles):
    """[(статья, номер сейчас, номер должен быть)] — номера занимаемые выпуском раздаются по порядку статей в выпуске."""
    mine = [a for a in articles if a.site_art_id]
    ids = sorted(a.site_art_id for a in mine)
    return [(a, a.site_art_id, new) for a, new in zip(mine, ids)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("issue_id", type=int)
    ap.add_argument("--apply", action="store_true", help="записать изменения в БД сайта")
    args = ap.parse_args()
    if not site_db.is_production_configured():
        print("SITE_DATABASE_URL не задан — это не боевая база, выхожу.")
        return 1

    with flask_app.app.app_context():
        issue = db.session.get(PubIssue, args.issue_id)
        if issue is None:
            print("Такого выпуска нет.")
            return 1
        rows = plan(issue.articles)
        moves = [(a, old, new) for a, old, new in rows if old != new]
        print(f"{issue.journal_name} {issue.year} №{issue.number}: статей на сайте {len(rows)}")
        for a, old, new in rows:
            print(f"  {a.pages:>8}  {a.doi[-5:]}  art_id {old}" + (f" -> {new}" if old != new else ""))
        if not moves:
            print("\nПорядок уже верный, менять нечего.")
            return 0
        with site_db.get_engine().begin() as conn:
            for a, old, _new in rows:
                got = conn.execute(text("SELECT doi FROM articles WHERE art_id = :i"), {"i": old}).scalar()
                if not (got or "").endswith(a.doi or "\0"):
                    print(f"\nОСТАНОВЛЕНО: на сайте под номером {old} лежит не та статья ({got}), ожидалась {a.doi}. Ничего не записано.")
                    return 1
            busy = conn.execute(text("SELECT COUNT(*) FROM articles WHERE art_id >= :b"), {"b": TEMP_BASE}).scalar()
            if busy:
                print("\nОСТАНОВЛЕНО: временные номера заняты. Ничего не записано.")
                return 1
            print(f"\nменяется номеров: {len(moves)} из {len(rows)}")
            if not args.apply:
                print("Чтобы записать: добавьте --apply")
                return 0
            # В два шага через временные номера: два соседних номера нельзя поменять местами напрямую.
            for k, (a, old, _new) in enumerate(moves):
                conn.execute(text("UPDATE articles SET art_id = :t WHERE art_id = :o"), {"t": TEMP_BASE + k, "o": old})
            for k, (a, _old, new) in enumerate(moves):
                conn.execute(text("UPDATE articles SET art_id = :n WHERE art_id = :t"), {"n": new, "t": TEMP_BASE + k})
        for a, _old, new in moves:
            a.site_art_id = new
        db.session.commit()
        rec_path = os.path.join(PUB_EXPORTS_FOLDER, f"{issue.id}_site_push.json")
        try:
            with open(rec_path, encoding="utf-8") as f:
                rec = json.load(f)
            by_doi = {a.doi: a.site_art_id for a in issue.articles if a.site_art_id}
            for x in rec.get("articles") or []:
                x["art_id"] = by_doi.get(x["doi"], x["art_id"])
            with open(rec_path, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=1)
        except (OSError, ValueError):
            print("Внимание: запись для отката не обновлена (её нет или она не читается).")
        print("Готово: номера переставлены на сайте, в черновике и в записи для отката.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
