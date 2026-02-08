"""
Парсер PDF-файлов научных статей.
Извлекает название, авторов, email и организации из первых страниц PDF.
Использует PyMuPDF (fitz) для извлечения текста с информацией о шрифтах.
"""

import re
import fitz  # PyMuPDF


# Паттерны для распознавания
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Русские ФИО — оба порядка:
#   "Иванов А.Б.", "Иванов А. Б."   — фамилия + инициалы
#   "А.Б. Иванов", "А.Б.Иванов"     — инициалы + фамилия
#   "Иванов Алексей Борисович"       — полное ФИО
_RU_NAME_PATTERNS = [
    # И.О. Фамилия (инициалы перед фамилией): М.В. Букин, А.А. Керхайли
    re.compile(r'[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]{2,}'),
    # Фамилия И.О. (фамилия перед инициалами): Букин М.В.
    re.compile(r'[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.'),
    # Полное ФИО: Иванов Алексей Борисович
    re.compile(r'[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ][а-яё]{2,}'),
]

# Английские имена
_EN_NAME_PATTERNS = [
    # A.B. Ivanov
    re.compile(r'[A-Z]\.\s*[A-Z]\.\s*[A-Z][a-z]{2,}'),
    # Ivanov A.B.
    re.compile(r'[A-Z][a-z]{2,}\s+[A-Z]\.\s*[A-Z]\.'),
    # John Smith
    re.compile(r'[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}'),
]

# Ключевые слова организаций
_ORG_KEYWORDS = (
    'университет', 'институт', 'академия', 'нии',
    'мгту', 'мгу', 'мирэа', 'мфти', 'мифи', 'вунц',
    'факультет', 'кафедра', 'лаборатория', 'центр',
    'university', 'institute', 'academy', 'college', 'department',
    'laboratory', 'research center', 'school of',
)

# Маркеры окончания зоны авторов (начало основного текста)
_STOP_MARKERS = (
    'аннотация', 'abstract', 'введение', 'introduction',
    'ключевые слова', 'keywords', 'key words',
    'постановка', 'рецензи', 'поступила', 'received',
)

# Маркеры начала блока авторов (разделяют заголовок и авторов)
_AUTHOR_MARKERS = ('авторы', 'authors', 'автор:', 'author:')

# Маркеры, которые нужно пропускать (до заголовка)
_SKIP_MARKERS = (
    'удк', 'udc', 'doi:', 'doi ', 'научная статья', 'original article',
    'http', 'https',
)


def _clean_superscripts(text):
    """Убирает суперскрипт-номера аффилиаций из текста авторов: '1 , ' '2,' и т.д."""
    # Убираем одиночные цифры, окружённые пробелами/запятыми (аффилиации)
    text = re.sub(r'\s*\d+\s*,\s*\d+\s*', ' ', text)  # "1,2" -> " "
    text = re.sub(r'\s+\d{1,2}\s*(?=[,А-ЯЁA-Z])', ' ', text)  # " 1 ," -> " ,"
    text = re.sub(r'(?<=[а-яёa-z])\s*\d{1,2}\s*(?=[,\s])', ' ', text)  # "Букин 1 ," -> "Букин ,"
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_text_blocks(page):
    """
    Извлекает текстовые блоки с информацией о шрифте.
    Возвращает список: [{'text': str, 'size': float, 'y': float, 'flags': int}]
    """
    blocks = []
    raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text_parts = []
            max_size = 0
            flags = 0
            for span in line.get("spans", []):
                text_parts.append(span["text"])
                if span["size"] > max_size:
                    max_size = span["size"]
                    flags = span["flags"]

            text = " ".join(text_parts).strip()
            if text:
                blocks.append({
                    "text": text,
                    "size": round(max_size, 1),
                    "y": round(line["bbox"][1], 1),
                    "flags": flags,
                })

    return blocks


def _find_title(blocks):
    """
    Название статьи — текст с наибольшим размером шрифта на первой странице.
    Объединяет соседние строки с тем же размером (многострочные заголовки).
    Пропускает колонтитулы и короткие строки.
    """
    if not blocks:
        return "", 0

    # Находим максимальный размер шрифта среди значимых строк
    meaningful = [b for b in blocks if len(b["text"]) > 5]
    if not meaningful:
        return blocks[0]["text"] if blocks else "", 0

    max_size = max(b["size"] for b in meaningful)

    # Собираем подряд идущие строки с максимальным шрифтом
    title_parts = []
    last_idx = -1

    for i, b in enumerate(blocks):
        if abs(b["size"] - max_size) < 0.5 and len(b["text"]) > 3:
            text_lower = b["text"].lower().strip()
            # Стоп: дошли до аннотации/введения
            if any(m in text_lower for m in _STOP_MARKERS):
                break
            # Стоп: дошли до маркера авторов («Авторы», «Authors»)
            if any(text_lower == m or text_lower.startswith(m + ' ') for m in _AUTHOR_MARKERS):
                last_idx = i  # запоминаем позицию для поиска авторов
                break
            # Пропускаем служебные строки
            if any(text_lower.startswith(m) for m in _SKIP_MARKERS):
                continue

            if last_idx == -1 or i == last_idx + 1:
                title_parts.append(b["text"])
                last_idx = i
            elif last_idx != -1:
                break  # Разрыв — конец заголовка

    title = " ".join(title_parts).strip()
    title = re.sub(r'^\d+\.\s*', '', title)

    return title, last_idx


def _find_names_in_text(text):
    """Ищет имена во всех поддерживаемых форматах."""
    names = []
    seen = set()

    for pattern in _RU_NAME_PATTERNS + _EN_NAME_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group().strip().rstrip(',;.')
            # Нормализуем пробелы
            name = re.sub(r'\s+', ' ', name)
            if name not in seen and len(name) > 3:
                seen.add(name)
                names.append(name)

    return names


def _find_authors_and_orgs(blocks, start_idx):
    """
    Ищет авторов и организации в блоках после заголовка.
    Останавливается при встрече стоп-маркера.
    """
    emails = []
    organizations = []
    author_line_texts = []

    # --- Шаг 1: собираем сырые строки между заголовком и аннотацией ---
    raw_lines = []
    for b in blocks[start_idx + 1:]:
        text = b["text"].strip()
        text_lower = text.lower()

        # Стоп — дошли до аннотации/введения
        if any(text_lower.startswith(m) or text_lower == m for m in _STOP_MARKERS):
            break

        # Пропускаем маркер «Авторы»
        if any(text_lower == m or text_lower.startswith(m + ' ') for m in _AUTHOR_MARKERS):
            continue

        # Пропускаем служебные строки (УДК, DOI, и т.д.)
        if any(text_lower.startswith(m) for m in _SKIP_MARKERS):
            continue

        # Пропускаем очень короткие строки
        if len(text) <= 3:
            continue

        raw_lines.append(text)

    # --- Шаг 2: склеиваем строки с переносами (напр. «Государ-» + «ственный ...») ---
    merged_lines = []
    for line in raw_lines:
        if merged_lines and merged_lines[-1].endswith('-'):
            prev = merged_lines[-1]
            # Убираем дефис переноса и склеиваем
            merged_lines[-1] = prev[:-1] + line
        elif merged_lines and line and line[0].islower():
            # Продолжение предыдущей строки (начинается со строчной)
            merged_lines[-1] = merged_lines[-1] + ' ' + line
        else:
            merged_lines.append(line)

    # --- Шаг 3: классифицируем склеенные строки ---
    name_lines = []
    org_lines = []
    for text in merged_lines:
        author_line_texts.append(text)
        text_lower = text.lower()
        has_org_kw = any(kw in text_lower for kw in _ORG_KEYWORDS)
        has_email = bool(_EMAIL_RE.search(text))

        if has_org_kw:
            org_lines.append(text)
            if has_email:
                # Строка с email + орг = смешанная, ищем имена
                name_lines.append(text)
            else:
                # Проверяем: начинается ли строка с ФИО? (напр. «Гладышев В.О., ... Университет ...»)
                # Если да — это смешанная строка автор+орг, ищем имена
                # Имя должно начинаться в первых 5 символах строки
                line_start = text.lstrip()[:60]
                starts_with_name = False
                for pattern in _RU_NAME_PATTERNS + _EN_NAME_PATTERNS:
                    m = pattern.search(line_start)
                    if m and m.start() < 5:
                        starts_with_name = True
                        break
                if starts_with_name:
                    name_lines.append(text)
                # Иначе — чистая строка организации, имена не ищем
        else:
            name_lines.append(text)

    full_text = "\n".join(author_line_texts)

    # Извлекаем email
    emails = _EMAIL_RE.findall(full_text)

    # Ищем имена в строках авторов
    name_text = _clean_superscripts("\n".join(name_lines))
    raw_names = _find_names_in_text(name_text)

    # Фильтруем ложные имена:
    # 1) Имена, содержащие ключевые слова организаций (напр. "Московский ... Университет")
    # 2) Имена, которые являются частью "им./имени" конструкций (напр. "Н.Э. Баумана")
    org_text_lower = " ".join(author_line_texts).lower()
    all_names = []
    for name in raw_names:
        name_lower = name.lower()
        # Фильтр 1: имя содержит ключевое слово организации
        if any(kw in name_lower for kw in _ORG_KEYWORDS):
            continue
        # Фильтр 2: имя стоит после "им.", "имени", "named after"
        idx = org_text_lower.find(name_lower)
        if idx != -1:
            before = org_text_lower[max(0, idx - 15):idx]
            if any(m in before for m in ('им.', 'имени', 'named', 'name')):
                continue
        all_names.append(name)

    # Фоллбэк: ищем в строке копирайта © Букин М.В., Керхайли А.А., ...
    if not all_names:
        for b in blocks:
            if '©' in b["text"]:
                copyright_text = b["text"].split('©')[-1]
                copyright_text = re.sub(r',?\s*\d{4}\s*$', '', copyright_text)
                all_names = _find_names_in_text(copyright_text)
                if all_names:
                    break

    # Извлекаем организации — вырезаем только часть с названием организации
    for text in org_lines:
        # Убираем ведущие цифры/диапазоны аффилиаций: "1,2 ", "1–3 ", "4−6 " и т.д.
        org = re.sub(r'^\s*[\d,\-–−\s]+\s+', '', text.strip()).rstrip(',;.')
        # Ищем первое ключевое слово организации и берём от него
        best_org = None
        for kw in _ORG_KEYWORDS:
            idx = org.lower().find(kw)
            if idx != -1:
                # Ищем начало названия организации (обычно перед ключевым словом есть полное название)
                # Ищем с начала, если это чистая org-строка, или от ключевого слова назад до запятой
                # Находим начало: идём назад от ключевого слова до запятой или начала строки
                start = org.rfind(',', 0, idx)
                start = start + 1 if start != -1 else 0
                org_part = org[start:]
                # Обрезаем после адреса/email/улицы
                org_part = re.split(r',\s*(?:улица|ул\.|e-mail|email|тел|phone|spin|адрес|д\.\s*\d)',
                                    org_part, flags=re.IGNORECASE)[0].strip().rstrip(',;.')
                if org_part and len(org_part) > 5:
                    best_org = org_part
                    break
        if best_org and best_org not in organizations:
            organizations.append(best_org)

    # Собираем авторов с привязкой email/org
    authors = []
    for i, name in enumerate(all_names):
        author = {"name": name, "email": "", "organization": ""}

        # Привязываем email по порядку
        if i < len(emails):
            author["email"] = emails[i]

        # Привязываем организацию
        if len(organizations) == 1:
            author["organization"] = organizations[0]
        elif organizations:
            if i < len(organizations):
                author["organization"] = organizations[i]
            else:
                author["organization"] = organizations[-1]

        authors.append(author)

    # Если нашли email но не нашли авторов — вернём хотя бы email
    if not authors and emails:
        for email in emails:
            authors.append({"name": "", "email": email, "organization": ""})

    return authors


def parse_article_pdf(file_path):
    """
    Парсит PDF-файл научной статьи и извлекает метаданные.

    Returns:
        dict: {title, authors: [{name, email, organization}], raw_text}
    """
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {"error": f"Не удалось открыть PDF: {str(e)}", "title": "", "authors": []}

    if doc.page_count == 0:
        doc.close()
        return {"error": "PDF пустой", "title": "", "authors": []}

    page = doc[0]
    blocks = _extract_text_blocks(page)

    # Если мало текста на первой — добавляем вторую
    if len(blocks) < 3 and doc.page_count > 1:
        blocks += _extract_text_blocks(doc[1])

    title, title_end_idx = _find_title(blocks)
    authors = _find_authors_and_orgs(blocks, title_end_idx)
    raw_text = page.get_text("text")[:500]

    doc.close()

    return {
        "title": title,
        "authors": authors,
        "raw_text": raw_text,
    }
