"""Загрузка выпуска целиком: папка с .doc статей и их PDF -> черновик выпуска в модуле
«Публикация на сайте».

Файлы загружаются по одному в «сессию импорта» (временная папка на сервере): .doc сразу
разбирается, результат разбора лежит рядом в JSON. Когда всё загружено и пользователь подтвердил
реквизиты выпуска, из разобранных статей создаётся PubIssue с PubArticle/PubAuthor, а PDF
переезжают в постоянную папку выпуска — по ним идёт сверка (metafora_check) и из неё же они
отправляются в «Метафору».

Реквизиты выпуска не вводятся руками: журнал, год и номер читаются из DOI статей
(10.18127/j20700970-202503-10 -> ISSN 2070-0970, 2025, №3), название журнала и том — из строки
«Для цитирования».
"""
import json
import os
import re
import shutil
import uuid
from collections import Counter

from werkzeug.utils import secure_filename

import article_template_parser
import pdf_parser
import publish_sanitize
from models import db
from models_publish import PubArticle, PubAuthor, PubIssue

BASE_DIR = os.path.dirname(__file__)
IMPORT_ROOT = os.path.join(BASE_DIR, "uploads", "publish_import")
ISSUE_PDF_ROOT = os.path.join(BASE_DIR, "uploads", "publish_issue_pdfs")
MANUSCRIPT_ROOT = os.path.join(BASE_DIR, "uploads", "publish_manuscripts")

DOC_EXT = (".doc", ".docx", ".rtf")
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_DOI_RE = re.compile(r"10\.18127/j(\d{4})(\d{3}[\dXx])-(\d{4})(\d{2})-(\d+)", re.I)
_AUTHOR_FIELDS = (
    "surname_ru", "initials_ru", "surname_en", "initials_en", "org_ru", "org_en", "town_ru", "town_en",
    "position_ru", "position_en", "email", "orcid", "spin",
)


class ImportError_(Exception):
    """Ошибка импорта с текстом для показа пользователю."""


def new_token():
    return uuid.uuid4().hex


def session_dir(token):
    if not _TOKEN_RE.match(token or ""):
        raise ImportError_("Неверный идентификатор загрузки.")
    return os.path.join(IMPORT_ROOT, token)


def issue_pdf_dir(issue_id):
    return os.path.join(ISSUE_PDF_ROOT, str(int(issue_id)))


def _safe_name(filename):
    """Имя файла для хранения: без путей, но с кириллицей (secure_filename её выбрасывает,
    а по имени «07_Nm_Есиков.doc» человек узнаёт статью)."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f/:*?\"<>|]", "_", name).lstrip(".")
    return name[:180] or secure_filename(filename) or "file"


def _unique_path(folder, name):
    path, stem, ext, n = os.path.join(folder, name), *os.path.splitext(name), 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(folder, f"{stem}_{n}{ext}")
    return path


def issue_from_doi(doi):
    """'10.18127/j20700970-202503-10' -> {'issn': '2070-0970', 'year': 2025, 'number': '3'} или None."""
    m = _DOI_RE.search(doi or "")
    if not m:
        return None
    return {"issn": f"{m.group(1)}-{m.group(2).upper()}", "year": int(m.group(3)), "number": str(int(m.group(4)))}


def _journal_and_volume(citation):
    """«… // Нелинейный мир. 2025. Т. 23. № 3. С. 81–87.» -> ('Нелинейный мир', '23')."""
    tail = (citation or "").split("//", 1)[-1] if "//" in (citation or "") else ""
    name = re.match(r"\s*(.+?)\.\s*(?:19|20)\d{2}\b", tail)
    vol = re.search(r"\bТ\.\s*(\d+)", tail)
    return (name.group(1).strip() if name else ""), (vol.group(1) if vol else "")


# ---------------------------------------------------------------- загрузка файлов в сессию

def add_doc(token, storage):
    """Сохраняет и разбирает один .doc. -> сводка для таблицы на странице."""
    folder = os.path.join(session_dir(token), "doc")
    os.makedirs(folder, exist_ok=True)
    name = _safe_name(storage.filename)
    if not name.lower().endswith(DOC_EXT):
        raise ImportError_(f"«{name}»: нужен файл .doc, .docx или .rtf.")
    path = _unique_path(folder, name)
    storage.save(path)
    try:
        raw = pdf_parser.extract_text_any(path)
        parsed, warnings = article_template_parser.parse_article_text(raw)
        parsed = publish_sanitize.deep_clean_text(parsed)
    except Exception as e:  # noqa: BLE001 — любой сбой разбора показываем строкой в таблице
        return {"file": os.path.basename(path), "kind": "error", "reason": f"не удалось прочитать: {e}"}

    # Статья — то, у чего есть DOI и авторы; титул, реклама, «К читателям» сюда не попадают.
    if not parsed.get("doi") or not parsed.get("authors"):
        return {"file": os.path.basename(path), "kind": "skipped", "reason": "не статья (нет DOI или авторов)"}

    parsed["_file"] = os.path.basename(path)
    parsed["_warnings"] = warnings
    with open(path + ".json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False)
    journal, volume = _journal_and_volume(parsed.get("citation_ru"))
    return {
        "file": os.path.basename(path), "kind": "article", "doi": parsed["doi"],
        "title": parsed.get("title_ru") or "", "pages": parsed.get("pages") or "",
        "authors": [a.get("surname_ru") or a.get("surname_en") or "" for a in parsed["authors"]],
        "refs": len(parsed.get("references") or []), "refs_en": len(parsed.get("references_en") or []),
        "warnings": warnings, "issue": issue_from_doi(parsed["doi"]), "journal": journal, "volume": volume,
    }


def add_pdf(folder, storage):
    """Сохраняет PDF в папку (сессии или выпуска). -> {'file', 'doi', 'pages'}; DOI — с первой
    страницы: он есть у PDF статьи и отсутствует у титула и PDF целого номера."""
    import fitz

    os.makedirs(folder, exist_ok=True)
    name = _safe_name(storage.filename)
    if not name.lower().endswith(".pdf"):
        raise ImportError_(f"«{name}»: нужен файл PDF.")
    path = _unique_path(folder, name)
    storage.save(path)
    try:
        doc = fitz.open(path)
        first = doc[0].get_text() if len(doc) else ""
        pages = len(doc)
        doc.close()
    except Exception as e:  # noqa: BLE001
        os.remove(path)
        raise ImportError_(f"«{name}»: не читается как PDF ({e}).")
    m = _DOI_RE.search(first)
    if not m:
        # Титул, PDF целого номера, список книг — не статьи; хранить их незачем.
        os.remove(path)
        return {"file": name, "doi": "", "pages": pages, "skipped": True}
    return {"file": os.path.basename(path), "doi": m.group(0), "pages": pages}


def session_pdf_dir(token):
    return os.path.join(session_dir(token), "pdf")


def load_parsed(token):
    folder = os.path.join(session_dir(token), "doc")
    out = []
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                out.append(json.load(f))
    return out


def detect_issue(parsed_list):
    """Реквизиты выпуска по большинству статей + список статей, которые из другого выпуска."""
    keys = [issue_from_doi(p.get("doi")) for p in parsed_list]
    known = [(k["issn"], k["year"], k["number"]) for k in keys if k]
    if not known:
        return None, []
    (issn, year, number), _n = Counter(known).most_common(1)[0]
    odd = [p.get("_file") for p, k in zip(parsed_list, keys) if not k or (k["issn"], k["year"], k["number"]) != (issn, year, number)]
    names = Counter(n for n, _v in (_journal_and_volume(p.get("citation_ru")) for p in parsed_list) if n)
    volumes = Counter(v for _n, v in (_journal_and_volume(p.get("citation_ru")) for p in parsed_list) if v)
    return {
        "issn": issn, "year": year, "number": number,
        "journal_name": names.most_common(1)[0][0] if names else "",
        "volume": volumes.most_common(1)[0][0] if volumes else "",
    }, odd


# ---------------------------------------------------------------- создание выпуска

def _doi_order(parsed):
    m = _DOI_RE.search(parsed.get("doi") or "")
    return (int(m.group(5)) if m else 10 ** 6, parsed.get("_file") or "")


def make_article(issue_id, parsed, user_id):
    dates = parsed.get("dates") or {}
    art = PubArticle(
        issue_id=issue_id, created_by_user_id=user_id,
        title_ru=parsed.get("title_ru") or "(без названия)", title_en=parsed.get("title_en") or "",
        pages=parsed.get("pages") or "", art_type=parsed.get("art_type") or "RAR",
        udk=(parsed.get("udk") or "")[:50], doi=parsed.get("doi") or "",
        abstract_ru=parsed.get("abstract_ru") or "", abstract_en=parsed.get("abstract_en") or "",
        fulltext_ru=parsed.get("fulltext_ru") or "",
        keywords_ru=", ".join(parsed.get("keywords_ru") or []), keywords_en=", ".join(parsed.get("keywords_en") or []),
        references_text="\n".join(parsed.get("references") or []),
        references_en_text="\n".join(parsed.get("references_en") or []),
        funding_ru=parsed.get("funding_ru") or "", funding_en=parsed.get("funding_en") or "",
        citation_ru=parsed.get("citation_ru") or "", citation_en=parsed.get("citation_en") or "",
        date_received=dates.get("received") or "", date_approved=dates.get("approved") or "",
        date_accepted=dates.get("accepted") or "",
    )
    db.session.add(art)
    db.session.flush()
    for order, a in enumerate(parsed.get("authors") or []):
        if not (a.get("surname_ru") or a.get("surname_en")):
            continue
        author = PubAuthor(article_id=art.id, order=order, country_ru="Россия", country_en="Russia")
        for f in _AUTHOR_FIELDS:
            setattr(author, f, (a.get(f) or "")[:getattr(PubAuthor, f).type.length])
        db.session.add(author)
    return art


def create_issue(token, fields, user_id):
    """Создаёт черновик выпуска из разобранных статей сессии. -> PubIssue."""
    parsed_list = sorted(load_parsed(token), key=_doi_order)
    if not parsed_list:
        raise ImportError_("В загруженных файлах не нашлось ни одной статьи.")
    issn, number = (fields.get("issn") or "").strip(), str(fields.get("number") or "").strip()
    try:
        year = int(fields.get("year"))
    except (TypeError, ValueError):
        raise ImportError_("Укажите год выпуска.")
    if not issn or not number:
        raise ImportError_("Укажите ISSN и номер выпуска.")
    existing = PubIssue.query.filter_by(issn=issn, year=year, number=number).first()
    if existing:
        err = ImportError_(f"Выпуск {year} №{number} этого журнала уже есть в разделе.")
        err.issue_id = existing.id
        raise err

    issue = PubIssue(issn=issn, year=year, number=number, journal_name=(fields.get("journal_name") or "").strip(),
                     volume=str(fields.get("volume") or "").strip(), created_by_user_id=user_id)
    db.session.add(issue)
    db.session.flush()

    os.makedirs(MANUSCRIPT_ROOT, exist_ok=True)
    doc_dir = os.path.join(session_dir(token), "doc")
    for parsed in parsed_list:
        art = make_article(issue.id, parsed, user_id)
        src = os.path.join(doc_dir, parsed.get("_file") or "")
        if os.path.isfile(src):
            dst = _unique_path(MANUSCRIPT_ROOT, f"issue{issue.id}_{parsed['_file']}")
            shutil.copy2(src, dst)
            art.manuscript_file = os.path.basename(dst)
    db.session.commit()

    src_pdf = session_pdf_dir(token)
    if os.path.isdir(src_pdf):
        dst_pdf = issue_pdf_dir(issue.id)
        os.makedirs(dst_pdf, exist_ok=True)
        for name in os.listdir(src_pdf):
            shutil.move(os.path.join(src_pdf, name), _unique_path(dst_pdf, name))
    shutil.rmtree(session_dir(token), ignore_errors=True)
    return issue


def list_issue_pdfs(issue_id):
    folder = issue_pdf_dir(issue_id)
    return sorted(n for n in os.listdir(folder) if n.lower().endswith(".pdf")) if os.path.isdir(folder) else []
