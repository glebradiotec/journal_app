"""Работа с БД сайта radiotec.ru (реальная схема: journals -> nomera -> razdel_numbers ->
articles): поиск/создание выпусков и разделов, вставка статей.

Работает и с локальной тестовой SQLite (для безопасной разработки/предпросмотра — используется
по умолчанию, пока не задан SITE_DATABASE_URL), и с боевой MySQL сайта — выбор через переменную
окружения SITE_DATABASE_URL (см. publish_config.py).

Порт xml_import/db.py.
"""
import os

from sqlalchemy import create_engine, text

import publish_config

DEFAULT_LOCAL_DB = os.path.join(os.path.dirname(__file__), "instance", "site_test.db")


def is_production_configured():
    """True, если явно указан боевой SITE_DATABASE_URL (а не локальная тестовая копия)."""
    return bool(publish_config.SITE_DATABASE_URL)


def get_engine():
    url = publish_config.SITE_DATABASE_URL or f"sqlite:///{DEFAULT_LOCAL_DB}"
    return create_engine(url)


def get_journal_by_issn(conn, issn):
    row = conn.execute(
        text("SELECT journ_id, menu_name, journ_name, link FROM journals WHERE issn = :issn"),
        {"issn": issn},
    ).fetchone()
    return row


def list_journals(conn):
    """Все журналы сайта — для выпадающего списка в форме, чтобы не набирать ISSN руками.
    Порядок — по journ_id (порядок добавления), не по алфавиту: так журналы без ISSN
    (например, «Спутниковые системы связи и вещания») можно осмысленно поставить в конец."""
    rows = conn.execute(
        text("SELECT journ_id, menu_name, issn FROM journals ORDER BY journ_id")
    ).fetchall()
    return rows


def get_latest_issue(conn, jr_num):
    """Последний известный на сайте выпуск этого журнала — подсказка при создании нового."""
    row = conn.execute(
        text(
            "SELECT num_year, num_num FROM nomera WHERE jr_num = :jr_num "
            "ORDER BY num_year DESC, num_id DESC LIMIT 1"
        ),
        {"jr_num": jr_num},
    ).fetchone()
    return row


def find_nomera(conn, jr_num, year, num_num):
    row = conn.execute(
        text(
            "SELECT num_id FROM nomera WHERE jr_num = :jr_num AND num_year = :year AND num_num = :num_num"
        ),
        {"jr_num": jr_num, "year": year, "num_num": num_num},
    ).fetchone()
    return row[0] if row else None


def create_nomera(conn, jr_num, year, num_num):
    result = conn.execute(
        text(
            "INSERT INTO nomera (jr_num, num_year, num_num, num_descript, num_act, file, price, num_descript_eng) "
            "VALUES (:jr_num, :year, :num_num, '', 1, '', 0, '')"
        ),
        {"jr_num": jr_num, "year": year, "num_num": num_num},
    )
    return result.lastrowid if result.lastrowid else conn.execute(
        text("SELECT last_insert_rowid()" if conn.engine.dialect.name == "sqlite" else "SELECT LAST_INSERT_ID()")
    ).scalar()


def find_or_create_nomera(conn, jr_num, year, num_num):
    num_id = find_nomera(conn, jr_num, year, num_num)
    if num_id:
        return num_id, False
    num_id = create_nomera(conn, jr_num, year, num_num)
    return num_id, True


def find_section(conn, num_id, razd_name):
    row = conn.execute(
        text("SELECT razd_id FROM razdel_numbers WHERE number = :num_id AND razd_name = :name"),
        {"num_id": num_id, "name": razd_name},
    ).fetchone()
    return row[0] if row else None


def next_section_sort(conn, num_id):
    row = conn.execute(
        text("SELECT COALESCE(MAX(r_sort), 0) FROM razdel_numbers WHERE number = :num_id"),
        {"num_id": num_id},
    ).fetchone()
    return (row[0] or 0) + 1


def create_section(conn, num_id, razd_name, razd_name_eng):
    sort = next_section_sort(conn, num_id)
    result = conn.execute(
        text(
            "INSERT INTO razdel_numbers (number, razd_name, post, jr_in_jr, r_sort, razd_name_eng) "
            "VALUES (:num_id, :name, 1, 'off', :sort, :name_eng)"
        ),
        {"num_id": num_id, "name": razd_name, "sort": sort, "name_eng": razd_name_eng},
    )
    return result.lastrowid if result.lastrowid else conn.execute(
        text("SELECT last_insert_rowid()" if conn.engine.dialect.name == "sqlite" else "SELECT LAST_INSERT_ID()")
    ).scalar()


def find_or_create_section(conn, num_id, razd_name, razd_name_eng):
    razd_id = find_section(conn, num_id, razd_name)
    if razd_id:
        return razd_id, False
    razd_id = create_section(conn, num_id, razd_name, razd_name_eng)
    return razd_id, True


def count_articles_in_issue(conn, num_id):
    """Сколько статей уже вставлено в этот выпуск — для порядкового номера в DOI."""
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM articles a "
            "JOIN razdel_numbers r ON a.razd_id = r.razd_id "
            "WHERE r.number = :num_id"
        ),
        {"num_id": num_id},
    ).fetchone()
    return row[0] or 0


def insert_article(conn, fields):
    """fields — dict с ключами, соответствующими колонкам таблицы articles."""
    cols = list(fields.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    result = conn.execute(
        text(f"INSERT INTO articles ({col_list}) VALUES ({placeholders})"),
        fields,
    )
    return result.lastrowid if result.lastrowid else conn.execute(
        text("SELECT last_insert_rowid()" if conn.engine.dialect.name == "sqlite" else "SELECT LAST_INSERT_ID()")
    ).scalar()


def existing_doi(conn, doi):
    """Ищет статью по DOI. Проверяем обе формы записи: на сайте DOI хранится и ссылкой
    (https://doi.org/10.18127/...), и «голым» — в разные годы заносили по-разному."""
    row = conn.execute(
        text("SELECT art_id FROM articles WHERE doi = :doi OR doi = :url"),
        {"doi": doi, "url": f"https://doi.org/{doi}"},
    ).fetchone()
    return row[0] if row else None


def column_limits(conn, table="articles"):
    """{колонка: максимальная длина} для строковых колонок таблицы сайта.

    Нужно, чтобы проверить длины ДО первой вставки: таблицы сайта — MyISAM, транзакций там нет,
    и упавшая на середине отправка оставляет часть статей на сайте (см. insert_articles)."""
    if conn.engine.dialect.name == "sqlite":
        return {}
    rows = conn.execute(
        text(
            "SELECT column_name, character_maximum_length FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND character_maximum_length IS NOT NULL"
        ),
        {"t": table},
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def check_field_lengths(limits, fields):
    """Список ('колонка', длина, предел) для значений, которые не влезут в колонку."""
    too_long = []
    for col, value in fields.items():
        limit = limits.get(col)
        if limit and isinstance(value, str) and len(value) > limit:
            too_long.append((col, len(value), limit))
    return too_long


def delete_articles(conn, art_ids):
    """Удаляет статьи по списку art_id — ручная компенсация вместо отката транзакции
    (таблицы сайта MyISAM, ROLLBACK на них не работает)."""
    for art_id in art_ids:
        conn.execute(text("DELETE FROM articles WHERE art_id = :id"), {"id": art_id})
