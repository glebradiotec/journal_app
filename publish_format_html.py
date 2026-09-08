"""Сборка HTML-блоков в том виде, в каком их хранит сайт (поля authors/descript/literature).

Порт xml_import/format_html.py.
"""
from xml.sax.saxutils import escape


def _esc(s):
    return escape(s or "").replace("«", "&laquo;").replace("»", "&raquo;").replace("–", "&ndash;")


def format_authors_html(authors, lang):
    """authors — список dict с ключами surname, initials, org, town, position, email (уже на нужном языке)."""
    blocks = []
    for a in authors:
        name = f"{_esc(a.get('initials'))} {_esc(a.get('surname'))}".strip()
        position = _esc(a.get("position"))
        org = _esc(a.get("org"))
        town = _esc(a.get("town"))
        org_line = org
        if town:
            org_line = f"{org} (г.&nbsp;{town})" if org else f"г.&nbsp;{town}"

        first_line = f"<p><strong>{name}</strong>"
        if position:
            first_line += f" &ndash; {position},<br />\n{org_line}</p>"
        elif org_line:
            first_line += f" &ndash; {org_line}</p>"
        else:
            first_line += "</p>"
        block = first_line
        if a.get("email"):
            block += f"\n\n<p>E-mail: {_esc(a['email'])}</p>"
        blocks.append(block)
    return "\n\n".join(blocks) + ("\n\n" if blocks else "")


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
    return "\n\n".join(f"<p>{_esc(p)}</p>" for p in paras)


def format_references_html(references):
    """references — список строк -> <ol><li>...</li></ol>, как хранит сайт."""
    if not references:
        return ""
    items = "\n".join(f"\t<li>{_esc(r)}</li>" for r in references)
    return f"<ol>\n{items}\n</ol>"


def format_keywords(keywords):
    return ", ".join(k.strip() for k in keywords if k.strip())
