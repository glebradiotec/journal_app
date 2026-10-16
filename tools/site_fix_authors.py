#!/usr/bin/env python3
"""Переписывает блок авторов (поля authors и authors_eng) у статей, уже записанных на сайт из раздела «Публикация».

Зачем: раньше номер сноски стоял вплотную к организации и к e-mail («1,2МГТУ», «1ivanov@…»). Теперь между ними
пробел. Утилита исправляет только ту разницу.

  python3 tools/site_fix_authors.py            — только показывает, что изменится (в базу ничего не пишет)
  python3 tools/site_fix_authors.py --apply    — пишет в БД сайта (только поля authors и authors_eng)

Строка на сайте обновляется, только если она совпадает с новой с точностью до этих пробелов — то есть её не правили
вручную после записи. Остальное пропускается и выводится в отчёте.
Запускать на сервере приложения из папки проекта (нужны SITE_DATABASE_URL и база черновиков).
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as flask_app  # noqa: E402  (сначала app: он читает .env, потом остальное)
from sqlalchemy import text  # noqa: E402

import routes_publish  # noqa: E402
import site_db  # noqa: E402
from models_publish import PubArticle  # noqa: E402


def _norm(html):
    """Без пробела после цифры: так видно, что строки различаются только им."""
    return re.sub(r"(\d) ", r"\1", html or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="записать изменения в БД сайта")
    args = ap.parse_args()
    if not site_db.is_production_configured():
        print("SITE_DATABASE_URL не задан — это не боевая база, выхожу.")
        return 1

    changed = skipped = same = 0
    with flask_app.app.app_context():
        engine = site_db.get_engine()
        with engine.begin() as conn:
            for art in PubArticle.query.filter(PubArticle.site_art_id.isnot(None)).order_by(PubArticle.id).all():
                issue = art.issue
                row = conn.execute(text("SELECT doi, authors, authors_eng FROM articles WHERE art_id = :i"),
                                   {"i": art.site_art_id}).fetchone()
                label = f"{issue.journal_name} {issue.year} №{issue.number}, art_id {art.site_art_id}"
                if row is None or not (row[0] or "").endswith(art.doi or "\0"):
                    print(f"пропуск (на сайте под этим номером другая статья): {label}")
                    skipped += 1
                    continue
                journal_row = site_db.get_journal_by_issn(conn, issue.issn)
                fields, _ = routes_publish._site_fields(art, issue, art.doi, journal_row)
                new = {"authors": fields["authors"], "authors_eng": fields["authors_eng"]}
                old = {"authors": row[1], "authors_eng": row[2]}
                if new == old:
                    same += 1
                    continue
                if any(_norm(new[k]) != _norm(old[k]) for k in new):
                    print(f"пропуск (на сайте блок авторов правили вручную): {label}")
                    skipped += 1
                    continue
                print(f"{'исправляю' if args.apply else 'исправил бы'}: {label}")
                if args.apply:
                    conn.execute(text("UPDATE articles SET authors = :a, authors_eng = :e WHERE art_id = :i"),
                                 {"a": new["authors"], "e": new["authors_eng"], "i": art.site_art_id})
                changed += 1
    print(f"\nитого: {'исправлено' if args.apply else 'будет исправлено'} {changed}, без изменений {same}, пропущено {skipped}")
    if changed and not args.apply:
        print("Чтобы записать: добавьте --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
