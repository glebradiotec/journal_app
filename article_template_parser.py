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


# Word иногда форматирует номер сноски автора/организации верхним индексом — в некоторых
# .doc такой символ так и попадает в текст как отдельный юникод-глиф надстрочной цифры,
# а не как обычная цифра ("Большаков¹" вместо "Большаков1"). Приводим к обычным цифрам сразу,
# иначе вся привязка автор↔организация по номеру сноски ломается.
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _lines(text):
    return [l.strip().translate(_SUPERSCRIPT_DIGITS) for l in text.split("\n")]


def _find(lines, pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return None


def _find_eq(lines, s, start=0):
    return _find(lines, lambda l: l == s, start)


def _find_startswith(lines, prefix, start=0):
    return _find(lines, lambda l: l.startswith(prefix), start)


def _find_ci_eq(lines, s, start=0):
    """Как _find_eq, но без учёта регистра — часть журналов оформляет заголовки
    капслоком (напр. «REFERENCES» вместо «References»)."""
    return _find(lines, lambda l: l.strip().lower() == s.lower(), start)


_SENTENCE_END_RE = re.compile(r'[.!?…»"\')]\s*$')
# Ссылка (гиперссылка Word на URL/DOI), вынесенная catdoc на отдельную «строку» — почти
# всегда продолжение той же ссылки на источник, а не отдельный источник сама по себе,
# даже если строка перед ней случайно заканчивается точкой (см. _slice_text).
_STARTS_WITH_URL_RE = re.compile(r'^(https?://|www\.)', re.I)


def _slice_text(lines, start, end):
    """Склеивает строки блока в абзацы. Word (через catdoc) иногда переносит одно
    предложение на несколько «строк» текста (мягкий перенос/разрыв страницы посреди
    абзаца) — если предыдущая строка не заканчивается концом предложения, считаем
    следующую её продолжением (склеиваем пробелом), а не новым абзацем."""
    if start is None or end is None:
        return ""
    paragraphs = []
    for l in lines[start:end]:
        l = l.strip()
        if not l:
            continue
        is_url_continuation = paragraphs and _STARTS_WITH_URL_RE.match(l)
        if paragraphs and (is_url_continuation or not _SENTENCE_END_RE.search(paragraphs[-1])):
            paragraphs[-1] = paragraphs[-1] + " " + l
        else:
            paragraphs.append(l)
    return "\n\n".join(paragraphs)


# Одно вхождение автора: "И.О. Фамилия<N>" (N — номер сноски на организацию, приклеен без пробела).
# Инициал — заглавная буква + необязательные строчные (англ. транслитерация вида "Yu.", "Ya.").
_INITIAL = r"[А-ЯЁA-Z][а-яёa-z]*\."
AUTHOR_TOKEN_RE = re.compile(
    rf"({_INITIAL}\s?{_INITIAL})\s*([А-Яа-яЁёA-Za-z\-]+)(\d*)"
)


def _parse_index_list(s):
    """'1, 2, 4' -> {1,2,4}; '1–3' (диапазон через тире/дефис) -> {1,2,3}."""
    indices = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–—−]\s*(\d+)$", part)
        if m:
            indices.update(range(int(m.group(1)), int(m.group(2)) + 1))
        else:
            indices.update(int(d) for d in re.findall(r"\d+", part))
    return indices


def _parse_authors_line(line):
    """'М.А. Басараб1, В.А. Бобков2 1, 2 Организация (Город, Страна)' ->
    ([{'initials':.., 'surname':.., 'index': 1|None}], rest) — где rest — весь остаток
    строки после последнего автора (номера сносок + одна или несколько организаций),
    ещё не разобранный: сам разбор организаций общий для одной и нескольких строк,
    см. _split_org_chunks."""
    all_matches = list(AUTHOR_TOKEN_RE.finditer(line))
    # Реальные авторы разделены запятой; отсекаем ложные срабатывания вида "Н.Э." внутри
    # названия организации ("МГТУ им. Н.Э. Баумана"), которые запятой не предваряются.
    matches = [
        mm for mm in all_matches
        if mm.start() == 0 or line[max(0, mm.start() - 2):mm.start()] == ", "
    ]
    authors = [
        {
            "initials": mm.group(1).replace(" ", ""),
            "surname": mm.group(2).strip(),
            "index": int(mm.group(3)) if mm.group(3) else None,
        }
        for mm in matches
    ]
    rest = line[matches[-1].end():].strip() if matches else line.strip()
    return authors, rest


_KNOWN_HEADINGS = {
    "Аннотация", "Ключевые слова", "Для цитирования", "Введение",
    "Abstract", "Keywords", "For citation",
}


def _looks_like_org_line(line):
    """Отличает «организация отдельной строкой» (напр. «1, 2 МГТУ им. Баумана (Москва, Россия)»,
    возможно несколько таких строк подряд — у статьи может быть несколько мест работы) от
    заголовка следующего блока (Аннотация/Abstract/...) — без этого организация-без-примет
    ошибочно проглатывала «Аннотация» как своё название. Ожидает, что email (если он был
    приклеен к той же строке) уже отрезан вызывающим кодом."""
    line = line.strip()
    if not line or line in _KNOWN_HEADINGS:
        return False
    if re.search(r"\([^()]+,\s*[^()]+\)\s*$", line):
        return True
    if re.match(r"^\d+(\s*[,\-–—−]\s*\d+)*\s+\S", line):
        return True
    return False


# Начало нового блока организации: список номеров сносок ("1, 2, 4 ", "3 ", "1–4 ").
_CHUNK_START_RE = re.compile(r"(?:^|(?<=\s))(\d+(?:\s*[,\-–—−]\s*\d+)*)\s+")


def _split_org_tail(segment):
    """'Организация (Город, Страна)' -> (org, town, country); поддерживает и «(Город,
    Область, Страна)» — последняя запятая перед скобкой всегда отделяет страну, всё
    остальное — город/область. Скобка без запятой внутри (уточнение вида «(национальный
    исследовательский университет)») концом организации не считается и остаётся в её
    названии — только конце строки с реальным «(Город, Страна)» так и разбираем."""
    m = re.search(r"\(([^()]+)\)\s*$", segment)
    if not m or "," not in m.group(1):
        return segment.strip(), "", ""
    org = segment[:m.start()].strip()
    parts = [p.strip() for p in m.group(1).split(",")]
    return org, ", ".join(parts[:-1]), parts[-1]


def _split_org_chunks(text):
    """Разбирает 'text' (всё после списка авторов — одна строка или несколько склеенных
    через пробел) на отдельные организации, у каждой из которых свой набор номеров-сносок
    авторов: '1, 2, 4 Орг1 (Город1, Страна1) 3 Орг2 (Город2, Страна2) 1, 4 Орг3 (Город3,
    Страна3)' -> [{'indices': {1,2,4}, 'org': 'Орг1', 'town': 'Город1', 'country': 'Страна1'}, ...].

    Границей чанка служит сам список номеров (а не «(Город, Страна)» в конце) — так корректно
    разбираются и организации без города/страны вовсе (в паре реальных статей автор мирного
    перевода забывал их указать для английской версии, не оставлять же организацию совсем
    без имени из-за этого)."""
    text = text.strip()
    if not text:
        return []
    starts = list(_CHUNK_START_RE.finditer(text))
    if not starts:
        # Без номеров сносок вовсе — единственное место работы, общее для всех авторов.
        org, town, country = _split_org_tail(text)
        return [{"indices": set(), "org": org, "town": town, "country": country}] if org else []
    chunks = []
    for i, sm in enumerate(starts):
        seg_end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        segment = text[sm.end():seg_end].strip()
        org, town, country = _split_org_tail(segment)
        if org:
            chunks.append({"indices": _parse_index_list(sm.group(1)), "org": org, "town": town, "country": country})
    return chunks


def _assign_orgs(authors, chunks):
    """Проставляет каждому автору org/town/country по совпадению номера сноски с одним из
    чанков _split_org_chunks. Автор может входить в несколько организаций сразу (индекс
    встречается в нескольких чанках) — тогда они склеиваются через «; », город/страна берутся
    от первой подходящей. Если сносок нет вовсе (журнал не нумерует авторов/организации,
    обычно при единственном месте работы на всех) — всем достаётся единственная организация."""
    no_indexed_chunks = chunks and all(not c["indices"] for c in chunks)
    for a in authors:
        matched = [c for c in chunks if a["index"] is not None and a["index"] in c["indices"]]
        if not matched and (a["index"] is None or no_indexed_chunks):
            matched = chunks
        a["org"] = "; ".join(c["org"] for c in matched)
        a["town"] = matched[0]["town"] if matched else ""
        a["country"] = matched[0]["country"] if matched else ""


def _parse_emails_line(line):
    """'1 basarab@bmstu.ru, 2 bobkovva@bmstu.ru' -> {1: 'basarab@bmstu.ru', 2: 'bobkovva@bmstu.ru'}.
    Без индексов ('basarab@bmstu.ru, bobkovva@bmstu.ru') -> {0: ..., 1: ...} (позиционно,
    ключ — порядковый номер email в строке, начиная с 0)."""
    result = {}
    pos = 0
    for m in re.finditer(r"(?:(\d+)\s+)?([\w.+-]+@[\w-]+\.[\w.-]+)", line):
        idx = int(m.group(1)) if m.group(1) else pos
        result[idx] = m.group(2)
        pos += 1
    return result


def _strip_email(line):
    """Отрезает от строки хвост с email-ами (иногда мягкий перенос строки в Word вместо
    разрыва абзаца склеивает организацию/авторов с email-ами в одну строку).
    Возвращает (текст без email-ов, отрезанный хвост или '')."""
    if "@" not in line:
        return line, ""
    m = re.search(r"(?:\s\d+\s+)?[\w.+-]+@[\w-]+\.[\w.-]+", line)
    if not m:
        return line, ""
    return line[:m.start()].strip(), line[m.start():]


def _consume_title_lines(lines, start_idx, max_lines=6):
    """Заглавие часто занимает несколько строк (перенос в Word) — читаем строки, пока
    не встретим строку, похожую на «И.О. Фамилия...» (начало блока авторов), пустую
    строку или предел max_lines. Возвращает (список строк заглавия, индекс первой строки
    после заглавия)."""
    title_lines = []
    i = start_idx
    # Пропускаем пустые строки перед самим заглавием — часть журналов оставляет пустой
    # абзац между маркером («Original article», копирайтом) и текстом названия.
    while i < len(lines) and not lines[i].strip():
        i += 1
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
    """Разбирает блок «авторы + место(-а) работы [+ email-а]» начиная с lines[start_idx].
    Мест работы может быть несколько, и не все авторы работают во всех сразу — у каждого
    автора и каждой организации есть номер сноски, по которому и сопоставляем (см.
    _split_org_chunks/_assign_orgs). Обычно email-а — следующая строка/абзац, но иногда
    (мягкий перенос строки в Word вместо разрыва абзаца) всё склеено в одну строку — тогда
    email-а вычленяются из неё же.
    Возвращает (authors, next_idx), где authors — список словарей с surname/initials/index/
    org/town/country/email."""
    if start_idx >= len(lines):
        return [], start_idx

    line, email_part = _strip_email(lines[start_idx])

    authors, rest = _parse_authors_line(line)
    next_idx = start_idx + 1

    # Организация(-и) может идти сразу за именами на той же строке (rest) и/или занимать
    # одну или несколько следующих строк — конкатенируем всё и разбираем как единый текст.
    # Email иногда приклеен (мягкий перенос строки Word) прямо к последней такой строке —
    # отрезаем его так же, как от строки с именами, до проверки _looks_like_org_line.
    org_text_parts = [rest] if rest else []
    while not email_part and next_idx < len(lines):
        candidate, candidate_email = _strip_email(lines[next_idx])
        if not candidate or not _looks_like_org_line(candidate):
            break
        org_text_parts.append(candidate)
        next_idx += 1
        email_part = candidate_email
    _assign_orgs(authors, _split_org_chunks(" ".join(org_text_parts)))

    # Строку с email-ами пропускаем, только если в ней реально есть "@" — иногда после
    # авторов (и организации, если она была) сразу идёт «Аннотация»/«Abstract» без каких-либо
    # контактов, и слепо съедать следующую строку в этом случае нельзя (см. _looks_like_org_line).
    if not email_part and next_idx < len(lines) and "@" in lines[next_idx]:
        email_part = lines[next_idx]
        next_idx += 1

    emails_by_idx = _parse_emails_line(email_part) if email_part else {}
    for pos, a in enumerate(authors):
        if a["index"] is not None and a["index"] in emails_by_idx:
            a["email"] = emails_by_idx[a["index"]]
        elif a["index"] is None and pos in emails_by_idx:
            a["email"] = emails_by_idx[pos]
        else:
            a["email"] = ""

    return authors, next_idx


def _extract_pages(citation_text):
    """Достаёт диапазон страниц из строки цитирования вида
    'С.   PAGEREF УДК \\h  8 –15.' — Word подставляет туда код поля PAGEREF (не
    вычисленный при экспорте в текст), а не просто число, так что ищем первые два
    числа после маркера 'С.'/'P.', пропуская весь мусор между ними. Ищем только в
    части после «//» (после названия журнала) — до неё «С.»/«P.» может быть началом
    инициалов кого-то из авторов (напр. «Гаранин С.М.»)."""
    part = citation_text.split("//", 1)[-1]
    m = re.search(r"[СP]\.\s*\D*?(\d+)\D*?(\d+)", part)
    if not m:
        return ""
    return f"{m.group(1)}-{m.group(2)}"


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
        "funding_ru": "", "funding_en": "",
        "citation_ru": "", "citation_en": "",
        "dates": {"received": "", "approved": "", "accepted": ""},
        "pages": "",
    }

    i_udk = _find_startswith(lines, "УДК")
    if i_udk is not None:
        result["udk"] = lines[i_udk][3:].strip().lstrip(":").strip()
    else:
        warnings.append("Не нашёл строку «УДК» — проверьте вручную.")

    i_doi = _find_startswith(lines, "DOI")
    if i_doi is not None:
        result["doi"] = re.sub(r"^DOI:?\s*(https?://doi\.org/)?", "", lines[i_doi], flags=re.I).strip()

    i_title_ru = i_doi + 1 if i_doi is not None else None
    i_after_title_ru = None
    if i_title_ru is not None:
        # Некоторые журналы вставляют между DOI и заглавием строку копирайта
        # («© Фамилия И.О., ..., Год») — пропускаем её, иначе копирайт принимается за
        # заглавие и всё дальнейшее (авторы, организация) съезжает. Пустую строку после
        # неё (если есть) пропустит сам _consume_title_lines.
        if i_title_ru < len(lines) and lines[i_title_ru].strip().startswith("©"):
            i_title_ru += 1
        title_lines, i_after_title_ru = _consume_title_lines(lines, i_title_ru)
        result["title_ru"] = " ".join(title_lines)

    authors_ru = []
    i_after_authors_ru = None
    if i_after_title_ru is not None:
        authors_ru, i_after_authors_ru = _parse_authors_block(lines, i_after_title_ru)

    i_annot = _find_eq(lines, "Аннотация", i_after_authors_ru or 0)
    i_keywords_h = _find_eq(lines, "Ключевые слова", i_annot or 0) if i_annot is not None else None
    if i_annot is not None and i_keywords_h is not None:
        result["abstract_ru"] = _slice_text(lines, i_annot + 1, i_keywords_h)
    else:
        warnings.append("Не нашёл блок «Аннотация» / «Ключевые слова» на русском.")

    i_citation_h = _find_eq(lines, "Для цитирования", i_keywords_h or 0) if i_keywords_h is not None else None
    if i_keywords_h is not None and i_citation_h is not None:
        # Ключевые слова всегда одна строка (список через запятую) — не склеиваем через
        # _slice_text: часть журналов вставляет следом абзац с благодарностью/финансированием
        # без отдельного заголовка, а он не заканчивается на слово-ключевик точкой, из-за чего
        # _slice_text считает его продолжением последнего ключевого слова.
        kw_lines = [l for l in lines[i_keywords_h + 1:i_citation_h] if l.strip()]
        if kw_lines:
            result["keywords_ru"] = [k.strip() for k in kw_lines[0].split(",") if k.strip()]
            result["funding_ru"] = " ".join(kw_lines[1:]).strip()

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

    # «Статья поступила...» — в шаблоне; часть журналов пишет короче, «Поступила в редакцию...».
    i_submitted = _find(
        lines, lambda l: l.startswith("Статья поступила") or l.startswith("Поступила"), i_authinfo_h or 0
    ) if i_authinfo_h is not None else None
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
    authors_en = []
    if i_orig is not None:
        i_title_en = i_orig + 1
        title_lines_en, i_after_title_en = _consume_title_lines(lines, i_title_en)
        result["title_en"] = " ".join(title_lines_en)
        authors_en, i_after_authors_en = _parse_authors_block(lines, i_after_title_en)

        i_abstract_h = _find_eq(lines, "Abstract", i_after_authors_en)
        i_keywords_en_h = _find_eq(lines, "Keywords", i_abstract_h or 0) if i_abstract_h is not None else None
        if i_abstract_h is not None and i_keywords_en_h is not None:
            result["abstract_en"] = _slice_text(lines, i_abstract_h + 1, i_keywords_en_h)

        i_forcit_h = _find_eq(lines, "For citation", i_keywords_en_h or 0) if i_keywords_en_h is not None else None
        if i_keywords_en_h is not None and i_forcit_h is not None:
            # См. комментарий у русского блока ключевых слов выше — та же логика.
            kw_lines_en = [l for l in lines[i_keywords_en_h + 1:i_forcit_h] if l.strip()]
            if kw_lines_en:
                result["keywords_en"] = [k.strip() for k in kw_lines_en[0].split(",") if k.strip()]
                result["funding_en"] = " ".join(kw_lines_en[1:]).strip()

        if i_forcit_h is not None:
            i_refs_en_h_peek = _find_ci_eq(lines, "References", i_forcit_h)
            citation_end_en = i_refs_en_h_peek if i_refs_en_h_peek is not None else len(lines)
            result["citation_en"] = _slice_text(lines, i_forcit_h + 1, citation_end_en)

        # Некоторые журналы пишут заголовок капслоком («REFERENCES») — ищем без учёта регистра.
        i_refs_en_h = _find_ci_eq(lines, "References", i_forcit_h or 0) if i_forcit_h is not None else None
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
            "org_ru": ru.get("org", ""), "org_en": en.get("org", ""),
            "town_ru": ru.get("town", ""), "town_en": en.get("town", ""),
            "position_ru": ru.get("position", ""), "position_en": en.get("position", ""),
            "email": ru.get("email", "") or en.get("email", ""),
            "spin": ru.get("spin", ""),
        })
    result["authors"] = authors

    result["pages"] = _extract_pages(result["citation_ru"]) or _extract_pages(result["citation_en"])
    if not result["pages"]:
        warnings.append("Не удалось определить диапазон страниц по строке цитирования — заполните вручную.")

    return result, warnings
