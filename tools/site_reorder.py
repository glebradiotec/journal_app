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
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as flask_app  # noqa: E402  (сначала app: он читает .env, потом остальное)

import site_db  # noqa: E402
from models import db  # noqa: E402
from models_publish import PubIssue  # noqa: E402
import routes_publish  # noqa: E402


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
        mine = [x for x in issue.articles if x.site_art_id]
        moves = {x.id: (old, new) for x, old, new in site_db.reorder_plan(issue.articles)}
        print(f"{issue.journal_name} {issue.year} №{issue.number}: статей на сайте {len(mine)}")
        for x in mine:
            old, new = moves.get(x.id, (x.site_art_id, x.site_art_id))
            print(f"  {x.pages:>8}  {x.doi[-5:]}  art_id {old}" + (f" -> {new}" if old != new else ""))
        if not moves:
            print("\nПорядок уже верный, менять нечего.")
            return 0
        print(f"\nменяется номеров: {len(moves)} из {len(mine)}")
        if not args.apply:
            print("Чтобы записать: добавьте --apply")
            return 0
        with site_db.get_engine().begin() as conn:
            done, why = site_db.reorder_apply(conn, issue.articles)
        if why:
            print(f"\nОСТАНОВЛЕНО: {why}. Ничего не записано.")
            return 1
        db.session.commit()
        routes_publish.site_push_record_sync_ids(issue)
        print("Готово: номера переставлены на сайте, в черновике и в записи для отката.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
