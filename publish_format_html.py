"""Сборка HTML-блоков в том виде, в каком их хранит сайт (поля authors/descript/literature).

Порт xml_import/format_html.py.
"""
import re
from xml.sax.saxutils import escape


def _esc(s):
    return escape(s or "").replace("«", "&laquo;").replace("»", "&raquo;").replace("–", "&ndash;")


def format_authors_html(authors, lang):
    """authors — список dict с ключами surname, initials, org, town, country (уже на нужном языке).

    Формат такой же, какой редакция заносит на сайт руками: сначала абзац с именами и номерами
    сносок, затем по абзацу на каждую организацию с номерами тех авторов, которые в ней работают:

        <p><strong>И.О.&nbsp;Фамилия</strong>1, <strong>И.О.&nbsp;Фамилия</strong>2</p>
        <p>1Организация (Город, Страна)</p>
        <p>1,2Другая организация (Город, Страна)</p>

    У автора может быть несколько мест работы — в org они склеены через «; » (см. разбор статьи).
    Город берётся один на автора, поэтому у автора с организациями в разных городах у обеих
    будет указан его основной город — редкий случай, поправляется руками в форме.
    """
    names, orgs = [], []  # orgs: [(название, [номера авторов])], порядок — как встретились
    for idx, a in enumerate(authors, start=1):
        surname, initials = _esc(a.get("surname")), _esc(a.get("initials"))
        name = f"{initials}&nbsp;{surname}" if initials and surname else (surname or initials)
        names.append(f"<strong>{name}</strong>{idx}")

        town, country = _esc(a.get("town")), _esc(a.get("country"))
        place = ", ".join(p for p in (town, country) if p)
        for org in (a.get("org") or "").split(";"):
            org = _esc(org.strip())
            if not org:
                continue
            full = f"{org} ({place})" if place else org
            for existing_org, idx_list in orgs:
                if existing_org == full:
                    idx_list.append(idx)
                    break
            else:
                orgs.append((full, [idx]))

    blocks = []
    if names:
        blocks.append(f"<p>{', '.join(names)}</p>")
    for org, idx_list in orgs:
        blocks.append(f"<p>{','.join(str(i) for i in idx_list)}{org}</p>")

    # E-mail-ы сайт хранит последним абзацем блока, с номером автора перед адресом.
    emails = [f"{idx}{_esc(a.get('email'))}" for idx, a in enumerate(authors, start=1) if a.get("email")]
    if emails:
        blocks.append(f"<p>{', '.join(emails)}</p>")
    return "\n\n".join(blocks)


_ABSTRACT_LABELS = (
    "Постановка проблемы", "Цель", "Результаты", "Практическая значимость",
    "Актуальность", "Методы", "Выводы", "Заключение",
)


def _bold_abstract_labels(text):
    """«Постановка проблемы. ...» -> «<strong>Постановка проблемы.</strong> ...» — структурные
    подзаголовки аннотации сайт хранит выделенными (так их заносят руками)."""
    labels = "|".join(_ABSTRACT_LABELS)
    return re.sub(rf"^({labels})\.\s*", r"<strong>\1.</strong> ", text)


def _split_by_abstract_labels(paragraph):
    """Структурированная аннотация приходит из .doc одним куском, а сайт хранит каждый
    подзаголовок отдельным абзацем — разрезаем перед каждым из них."""
    labels = "|".join(_ABSTRACT_LABELS)
    parts = re.split(rf"(?<=[.!?])\s+(?=(?:{labels})\.)", paragraph)
    return [p.strip() for p in parts if p.strip()]


def format_paragraphs_html(text):
    """Оборачивает текст в <p>, разбивая по пустым строкам. Если уже похоже на HTML — не трогает."""
    if not text:
        return ""
    text = text.strip()
    if "<p" in text.lower() or "<ol" in text.lower():
        return text
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        paras = [text]
    paras = [part for p in paras for part in _split_by_abstract_labels(p)]
    return "\n\n".join(f"<p>{_bold_abstract_labels(_esc(p))}</p>" for p in paras)


def format_references_html(references):
    """references — список строк -> <ol><li>...</li></ol>, как хранит сайт."""
    if not references:
        return ""
    items = "\n".join(f"\t<li>{_esc(r)}</li>" for r in references)
    return f"<ol>\n{items}\n</ol>"


def format_keywords(keywords):
    return ", ".join(k.strip() for k in keywords if k.strip())


def _format_pages(pages):
    m = re.match(r"^\s*(\d+)\s*[-–—−]\s*(\d+)\s*$", pages or "")
    return f"{m.group(1)}&minus;{m.group(2)}" if m else _esc(pages)


def format_citation_html(authors, title, journal_name, year, volume, number, pages, doi, lang="ru"):
    """Строка «Для цитирования» в том виде, как её хранит сайт (поле citata, varchar(400)):
    <p><em>Фамилия И.О., ...</em> Название // Журнал. 2026. Т. 24. № 4.
    С. 6&minus;13. DOI: https://doi.org/...</p>

    Собирается из полей, а не берётся из текста статьи: в Word год, том и страницы вставлены
    полями (REF/PAGEREF), и при конвертации в текст от них остаются коды, а не значения —
    такая строка и мусорная, и в колонку сайта не влезает.
    """
    sep = " "  # в строках цитирования сайт везде ставит обычный пробел, а не &nbsp;
    names = []
    for a in authors:
        surname, initials = _esc(a.get("surname")), _esc(a.get("initials"))
        if not surname:
            continue
        names.append(f"{surname}{sep}{initials}" if initials else surname)

    parts = []
    if names:
        parts.append(f"<em>{', '.join(names)}</em>")
    if title:
        parts.append(_esc(title))
    if journal_name:
        parts.append(f"// {_esc(journal_name)}.")
    if year:
        parts.append(f"{year}.")
    if volume:
        parts.append(f"{'Т.' if lang == 'ru' else 'V.'}{sep}{_esc(str(volume))}.")
    if number:
        parts.append(f"№{sep}{_esc(str(number))}.")
    if pages:
        parts.append(f"{'С.' if lang == 'ru' else 'P.'}{sep}{_format_pages(pages)}.")
    if doi:
        parts.append(f"DOI:{sep}https://doi.org/{_esc(doi)}")

    return f"<p>{' '.join(parts)}</p>"
