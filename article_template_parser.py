"""Разбор статьи, оформленной по шаблону издательства (Word/.doc → текст → структура).

Шаблон (наблюдался на реальных статьях журнала «Нейрокомпьютеры»):

  Научная статья
  УДК ...
  DOI: https://doi.org/...
  <Заглавие RU>
  <И.О. Фамилия1[, И.О. Фамилия2] ... 1[, 2] Организация (Город, Страна)>
  <1 email1[, 2 email2]>
  Аннотация
  Постановка проблемы. ...
  Цель. ...
  Результаты. ...
  Практическая значимость. ...
  Ключевые слова
  <слово1, слово2, ...>
  Для цитирования
  <строка цитирования>
  A brief version in English is given at the end of the article
  Введение
  ...полный текст статьи...
  Список источников
  <ссылка 1>
  <ссылка 2>
  ...
  Информация об авторах
  <ФИО – должность SPIN-код: XXXX-XXXX>
  ...
  Статья поступила в редакцию DD.MM.YYYY
  Одобрена после рецензирования DD.MM.YYYY
  Принята к публикации DD.MM.YYYY
  Original article
  <Title EN>
  <Initials Surname1[, ...] ... Org (City, Country)>
  <emails>
  Abstract
  <текст>
  Keywords
  <keywords>
  For citation
  <citation>
  References
  <refs>
  Information about the authors
  <ФИО – position>
  ...

Парсер терпим к небольшим отклонениям, но рассчитан именно на эту структуру.
Не гарантирует 100% точность — результат нужно проверить в форме перед отправкой.
"""
import re


def _lines(text):
    return [l.strip() for l in text.split("\n")]


def _find(lines, pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return None


def _find_eq(lines, s, start=0):
    return _find(lines, lambda l: l == s, start)


def _find_startswith(lines, prefix, start=0):
    return _find(lines, lambda l: l.startswith(prefix), start)


def _slice_text(lines, start, end):
    if start is None or end is None:
        return ""
    return "\n\n".join(l for l in lines[start:end] if l.strip())


# Одно вхождение автора: "И.О. Фамилия<N>" (N — номер сноски на организацию, приклеен без пробела).
# Инициал — заглавная буква + необязательные строчные (англ. транслитерация вида "Yu.", "Ya.").
_INITIAL = r"[А-ЯЁA-Z][а-яёa-z]*\."
AUTHOR_TOKEN_RE = re.compile(
    rf"({_INITIAL}\s?{_INITIAL})\s*([А-Яа-яЁёA-Za-z\-]+)\d*"
)


def _parse_authors_line(line):
    """'М.А. Басараб1, В.А. Бобков2 1, 2 Организация (Город, Страна)' ->
    ([{'initials':..,'surname':..}], org, town, country).

    Список авторов ищем по образцу «И.О. Фамилия», а всё, что остаётся после
    последнего найденного автора (за вычетом ведущего списка номеров сносок),
    считаем организацией — так неоднозначность с приклеенными номерами сносок
    у фамилий не мешает разбору."""
    m = re.search(r"\(([^,()]+),\s*([^()]+)\)\s*$", line)
    town, country = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
    rest = line[: m.start()].strip() if m else line

    # Реальные авторы разделены запятой; отсекаем ложные срабатывания вида "Н.Э." внутри
    # названия организации ("МГТУ им. Н.Э. Баумана"), которые запятой не предваряются.
    all_matches = list(AUTHOR_TOKEN_RE.finditer(rest))
    matches = [
        mm for mm in all_matches
        if mm.start() == 0 or rest[max(0, mm.start() - 2):mm.start()] == ", "
    ]
    authors = [{"initials": mm.group(1).replace(" ", ""), "surname": mm.group(2).strip()} for mm in matches]

    org = ""
    if matches:
        org_tail = rest[matches[-1].end():].strip()
        org = re.sub(r"^[\d,\s]+", "", org_tail).strip()

    return authors, org, town, country


_KNOWN_HEADINGS = {
    "Аннотация", "Ключевые слова", "Для цитирования", "Введение",
    "Abstract", "Keywords", "For citation",
}


def _looks_like_org_line(line):
    """Отличает «организация отдельной строкой» (напр. «1, 2 МГТУ им. Баумана (Москва, Россия)»)
    от заголовка следующего блока (Аннотация/Abstract/...) — без этого организация-без-примет
    ошибочно проглатывала «Аннотация» как своё название."""
    line = line.strip()
    if not line or line in _KNOWN_HEADINGS:
        return False
    if re.search(r"\([^,()]+,\s*[^()]+\)\s*$", line):
        return True
    if re.match(r"^\d+(\s*,\s*\d+)*\s+\S", line):
        return True
    return False


def _parse_org_line(line):
    """'1, 2 Организация (Город, Страна)' -> (org, town, country) — та же строка, что
    в конце _parse_authors_line, но когда организация стоит отдельной строкой без имён.
    Вызывающий код уже проверил _looks_like_org_line()."""
    m = re.search(r"\(([^,()]+),\s*([^()]+)\)\s*$", line)
    town, country = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
    rest = line[: m.start()].strip() if m else line
    org = re.sub(r"^[\d,\s]+", "", rest).strip()
    return org, town, country


def _parse_emails_line(line):
    """'1 basarab@bmstu.ru, 2 bobkovva@bmstu.ru' -> ['basarab@bmstu.ru', 'bobkovva@bmstu.ru']."""
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", line)
    return emails


def _consume_title_lines(lines, start_idx, max_lines=6):
    """Заглавие часто занимает несколько строк (перенос в Word) — читаем строки, пока
    не встретим строку, похожую на «И.О. Фамилия...» (начало блока авторов), пустую
    строку или предел max_lines. Возвращает (список строк заглавия, индекс первой строки
    после заглавия)."""
    title_lines = []
    i = start_idx
    while i < len(lines) and len(title_lines) < max_lines:
        line = lines[i]
        if not line.strip():
            break
        if AUTHOR_TOKEN_RE.search(line):
            break
        title_lines.append(line)
        i += 1
    if not title_lines:
        # Не нашли ничего похожего на заглавие до строки авторов — берём как есть,
        # чтобы не потерять индекс (страхуемся от пустого результата).
        title_lines = [lines[start_idx]]
        i = start_idx + 1
    return title_lines, i


def _parse_authors_block(lines, start_idx):
    """Разбирает блок «авторы + место работы» начиная с lines[start_idx]. Обычно email-а —
    следующая строка/абзац, но иногда (мягкий перенос строки в Word вместо разрыва абзаца)
    всё склеено в одну строку — тогда email-а вычленяются из неё же.
    Возвращает (authors, org, town, country, emails, next_idx)."""
    if start_idx >= len(lines):
        return [], "", "", "", [], start_idx
    line = lines[start_idx]
    if "@" in line:
        m = re.search(r"\s\d+\s+[\w.+-]+@", line)
        authors_part = line[: m.start()] if m else line
        emails_part = line[m.start():] if m else line
        emails = _parse_emails_line(emails_part)
        authors, org, town, country = _parse_authors_line(authors_part)
        return authors, org, town, country, emails, start_idx + 1
    authors, org, town, country = _parse_authors_line(line)
    next_idx = start_idx + 1
    if not org and next_idx < len(lines) and _looks_like_org_line(lines[next_idx]):
        # Организация иногда стоит отдельной строкой (не приклеена к строке с именами) —
        # тот же формат «1, 2 Организация (Город, Страна)», просто без имён впереди.
        org, town, country = _parse_org_line(lines[next_idx])
        next_idx += 1
    # Строку с email-ами пропускаем, только если в ней реально есть "@" — иногда после
    # авторов (и организации, если она была) сразу идёт «Аннотация»/«Abstract» без каких-либо
    # контактов, и блёндно съедать следующую строку в этом случае нельзя (см. _looks_like_org_line).
    emails = []
    if next_idx < len(lines) and "@" in lines[next_idx]:
        emails = _parse_emails_line(lines[next_idx])
        next_idx += 1
    return authors, org, town, country, emails, next_idx


def _match_author_info(fio_line, authors_ru):
    """'Михаил Алексеевич Басараб – ... SPIN-код: 9224-5685' -> (index в authors_ru, position, spin)."""
    parts = re.split(r"[–—−-]", fio_line, maxsplit=1)
    if len(parts) != 2:
        return None
    fio, rest = parts[0].strip(), parts[1].strip()
    surname = fio.split()[-1] if fio.split() else ""
    spin_m = re.search(r"SPIN[^:]*:\s*([\d\-]+|не представлен)", rest, re.I)
    spin = spin_m.group(1).strip() if spin_m else ""
    if spin.lower().startswith("не"):
        spin = ""
    position = rest[: spin_m.start()].strip(" ,") if spin_m else rest.strip()
    for i, a in enumerate(authors_ru):
        if a["surname"].lower() == surname.lower():
            return i, position, spin
    return None


def _match_author_info_en(fio_line):
    parts = re.split(r"[–—−-]", fio_line, maxsplit=1)
    if len(parts) != 2:
        return None, None
    fio, position = parts[0].strip(), parts[1].strip()
    surname = fio.split()[-1] if fio.split() else ""
    return surname, position


def parse_article_text(text):
    lines = _lines(text)
    warnings = []
    result = {
        "udk": "", "doi": "", "title_ru": "", "title_en": "",
        "authors": [],
        "abstract_ru": "", "abstract_en": "",
        "fulltext_ru": "",
        "keywords_ru": [], "keywords_en": [],
        "references": [],
        "references_en": [],
        "citation_ru": "", "citation_en": "",
        "dates": {"received": "", "approved": "", "accepted": ""},
    }

    i_udk = _find_startswith(lines, "УДК")
    if i_udk is not None:
        result["udk"] = lines[i_udk][3:].strip()
    else:
        warnings.append("Не нашёл строку «УДК» — проверьте вручную.")

    i_doi = _find_startswith(lines, "DOI")
    if i_doi is not None:
        result["doi"] = re.sub(r"^DOI:?\s*(https?://doi\.org/)?", "", lines[i_doi], flags=re.I).strip()

    i_title_ru = i_doi + 1 if i_doi is not None else None
    i_after_title_ru = None
    if i_title_ru is not None:
        title_lines, i_after_title_ru = _consume_title_lines(lines, i_title_ru)
        result["title_ru"] = " ".join(title_lines)

    authors_ru, org_ru, town_ru, country_ru = ([], "", "", "")
    emails = []
    i_after_authors_ru = None
    if i_after_title_ru is not None:
        authors_ru, org_ru, town_ru, country_ru, emails, i_after_authors_ru = _parse_authors_block(
            lines, i_after_title_ru
        )

    i_annot = _find_eq(lines, "Аннотация", i_after_authors_ru or 0)
    i_keywords_h = _find_eq(lines, "Ключевые слова", i_annot or 0) if i_annot is not None else None
    if i_annot is not None and i_keywords_h is not None:
        result["abstract_ru"] = _slice_text(lines, i_annot + 1, i_keywords_h)
    else:
        warnings.append("Не нашёл блок «Аннотация» / «Ключевые слова» на русском.")

    i_citation_h = _find_eq(lines, "Для цитирования", i_keywords_h or 0) if i_keywords_h is not None else None
    if i_keywords_h is not None and i_citation_h is not None:
        kw_text = _slice_text(lines, i_keywords_h + 1, i_citation_h)
        result["keywords_ru"] = [k.strip() for k in kw_text.split(",") if k.strip()]

    i_intro = _find_eq(lines, "Введение", i_citation_h or 0) if i_citation_h is not None else None
    i_refs_h = _find_eq(lines, "Список источников", i_intro or 0) if i_intro is not None else None
    if i_intro is None:
        i_intro = _find_startswith(lines, "Введение", i_citation_h or 0) if i_citation_h is not None else None

    if i_citation_h is not None and i_intro is not None:
        i_brief = _find(lines, lambda l: l.lower().startswith("a brief version"), i_citation_h)
        citation_end = i_brief if (i_brief is not None and i_brief < i_intro) else i_intro
        result["citation_ru"] = _slice_text(lines, i_citation_h + 1, citation_end)

    if i_intro is not None and i_refs_h is not None:
        result["fulltext_ru"] = _slice_text(lines, i_intro, i_refs_h)
    else:
        warnings.append("Не нашёл границы «Введение» ... «Список источников» — полный текст не собран автоматически.")

    i_authinfo_h = _find_startswith(lines, "Информация об автор", i_refs_h or 0) if i_refs_h is not None else None
    if i_refs_h is not None and i_authinfo_h is not None:
        refs_text = _slice_text(lines, i_refs_h + 1, i_authinfo_h)
        result["references"] = [r.strip() for r in refs_text.split("\n\n") if r.strip()]

    i_submitted = _find(lines, lambda l: l.startswith("Статья поступила"), i_authinfo_h or 0) if i_authinfo_h is not None else None
    if i_authinfo_h is not None and i_submitted is not None:
        info_text = lines[i_authinfo_h + 1:i_submitted]
        k = 0
        while k < len(info_text):
            fio_line = info_text[k]
            if not fio_line.strip():
                k += 1
                continue
            m = _match_author_info(fio_line, authors_ru)
            if m:
                idx, position, spin = m
                # SPIN иногда стоит отдельной строкой сразу после «ФИО – должность»,
                # а не в ней самой.
                if not spin and k + 1 < len(info_text):
                    spin_m = re.search(r"SPIN[^:]*:\s*([\d\-]+|не представлен)", info_text[k + 1], re.I)
                    if spin_m:
                        spin = spin_m.group(1).strip()
                        if spin.lower().startswith("не"):
                            spin = ""
                        k += 1
                authors_ru[idx]["position"] = position
                authors_ru[idx]["spin"] = spin
            k += 1

    if i_submitted is not None:
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})", lines[i_submitted])
        if m:
            result["dates"]["received"] = m.group(1)
        i_approved = _find(lines, lambda l: l.startswith("Одобрена"), i_submitted)
        if i_approved is not None:
            m1 = re.search(r"(\d{2}\.\d{2}\.\d{4})", lines[i_approved])
            if m1:
                result["dates"]["approved"] = m1.group(1)
        i_accepted = _find(lines, lambda l: l.startswith("Принята"), i_submitted)
        if i_accepted is not None:
            m2 = re.search(r"(\d{2}\.\d{2}\.\d{4})", lines[i_accepted])
            if m2:
                result["dates"]["accepted"] = m2.group(1)

    # --- English mirror ---
    i_orig = _find_eq(lines, "Original article", i_submitted or 0) if i_submitted is not None else _find_eq(lines, "Original article")
    authors_en, org_en, town_en, country_en = ([], "", "", "")
    if i_orig is not None:
        i_title_en = i_orig + 1
        title_lines_en, i_after_title_en = _consume_title_lines(lines, i_title_en)
        result["title_en"] = " ".join(title_lines_en)
        authors_en, org_en, town_en, country_en, _emails_en, i_after_authors_en = _parse_authors_block(
            lines, i_after_title_en
        )

        i_abstract_h = _find_eq(lines, "Abstract", i_after_authors_en)
        i_keywords_en_h = _find_eq(lines, "Keywords", i_abstract_h or 0) if i_abstract_h is not None else None
        if i_abstract_h is not None and i_keywords_en_h is not None:
            result["abstract_en"] = _slice_text(lines, i_abstract_h + 1, i_keywords_en_h)

        i_forcit_h = _find_eq(lines, "For citation", i_keywords_en_h or 0) if i_keywords_en_h is not None else None
        if i_keywords_en_h is not None and i_forcit_h is not None:
            kw_text = _slice_text(lines, i_keywords_en_h + 1, i_forcit_h)
            result["keywords_en"] = [k.strip() for k in kw_text.split(",") if k.strip()]

        if i_forcit_h is not None:
            i_refs_en_h_peek = _find_eq(lines, "References", i_forcit_h)
            citation_end_en = i_refs_en_h_peek if i_refs_en_h_peek is not None else len(lines)
            result["citation_en"] = _slice_text(lines, i_forcit_h + 1, citation_end_en)

        i_refs_en_h = _find_eq(lines, "References", i_forcit_h or 0) if i_forcit_h is not None else None
        i_authinfo_en_h = _find_startswith(lines, "Information about the author", i_refs_en_h or 0) if i_refs_en_h is not None else None
        if i_refs_en_h is not None and i_authinfo_en_h is not None:
            refs_text_en = _slice_text(lines, i_refs_en_h + 1, i_authinfo_en_h)
            result["references_en"] = [r.strip() for r in refs_text_en.split("\n\n") if r.strip()]

        if i_authinfo_en_h is not None:
            i_stop = _find(lines, lambda l: l.startswith("The article was submitted"), i_authinfo_en_h)
            info_en = lines[i_authinfo_en_h + 1:i_stop] if i_stop else lines[i_authinfo_en_h + 1:i_authinfo_en_h + 1 + len(authors_en)]
            for fio_line in info_en:
                if not fio_line.strip():
                    continue
                surname_en, position_en = _match_author_info_en(fio_line)
                target = surname_en.split()[-1].lower() if surname_en and surname_en.split() else None
                if target is None:
                    continue
                for a in authors_en:
                    a_surname = a["surname"].split()[-1].lower() if a["surname"].split() else None
                    if a_surname == target:
                        a["position"] = position_en
    else:
        warnings.append("Не нашёл английскую часть («Original article») — EN-поля не заполнены.")

    # --- склейка авторов RU + EN + email + org ---
    n = max(len(authors_ru), len(authors_en))
    authors = []
    for i in range(n):
        ru = authors_ru[i] if i < len(authors_ru) else {}
        en = authors_en[i] if i < len(authors_en) else {}
        authors.append({
            "surname_ru": ru.get("surname", ""), "initials_ru": ru.get("initials", ""),
            "surname_en": en.get("surname", ""), "initials_en": en.get("initials", ""),
            "org_ru": org_ru, "org_en": org_en,
            "town_ru": town_ru, "town_en": town_en,
            "position_ru": ru.get("position", ""), "position_en": en.get("position", ""),
            "email": emails[i] if i < len(emails) else "",
            "spin": ru.get("spin", ""),
        })
    result["authors"] = authors

    return result, warnings
