"""
Парсер PDF-файлов научных статей.
Извлекает название, авторов, email и организации из первых страниц PDF.
Использует PyMuPDF (fitz) для извлечения текста с информацией о шрифтах.
"""

import re
import fitz  # PyMuPDF


# Паттерны для распознавания
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

# Русские ФИО: "Иванов А.Б.", "Иванов А. Б.", "Иванов Алексей Борисович"
_RU_NAME_RE = re.compile(
    r'[А-ЯЁ][а-яё]+\s+'                       # Фамилия
    r'(?:'
    r'[А-ЯЁ]\.\s*[А-ЯЁ]\.'                     # И.О.
    r'|[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+'         # Имя Отчество
    r')'
)

# Английские имена: "A.B. Ivanov", "Ivanov A.B.", "John Smith"
_EN_NAME_RE = re.compile(
    r'(?:'
    r'[A-Z]\.\s*[A-Z]\.\s*[A-Z][a-z]+'          # A.B. Ivanov
    r'|[A-Z][a-z]+\s+[A-Z]\.\s*[A-Z]\.'         # Ivanov A.B.
    r'|[A-Z][a-z]+\s+[A-Z][a-z]+'               # John Smith
    r')'
)

# Ключевые слова организаций
_ORG_KEYWORDS = (
    'университет', 'институт', 'академия', 'нии', 'мгту', 'мгу',
    'факультет', 'кафедра', 'лаборатория', 'центр',
    'university', 'institute', 'academy', 'college', 'department',
    'laboratory', 'research', 'school of',
)

# Маркеры окончания зоны авторов (начало основного текста)
_STOP_MARKERS = (
    'аннотация', 'abstract', 'введение', 'introduction',
    'ключевые слова', 'keywords', 'key words',
    'удк', 'udc', 'doi:', 'doi ',
    'рецензи', 'поступила', 'received',
)


def _extract_text_blocks(page):
    """
    Извлекает текстовые блоки с первой страницы с информацией о шрифте.
    Возвращает список: [{'text': str, 'size': float, 'y': float, 'flags': int}]
    """
    blocks = []
    raw = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # только текст
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
    Название статьи — текст с наибольшим размером шрифта.
    Объединяет соседние строки с тем же размером (многострочные заголовки).
    """
    if not blocks:
        return "", 0

    # Находим максимальный размер шрифта (исключая слишком короткие строки — номера страниц и т.д.)
    meaningful_blocks = [b for b in blocks if len(b["text"]) > 5]
    if not meaningful_blocks:
        return blocks[0]["text"] if blocks else "", 0

    max_size = max(b["size"] for b in meaningful_blocks)

    # Собираем все строки с максимальным шрифтом подряд
    title_parts = []
    last_idx = -1

    for i, b in enumerate(blocks):
        if abs(b["size"] - max_size) < 0.5 and len(b["text"]) > 3:
            # Проверяем, что это не стоп-маркер
            if any(m in b["text"].lower() for m in _STOP_MARKERS):
                break
            # Собираем подряд идущие строки
            if last_idx == -1 or i == last_idx + 1:
                title_parts.append(b["text"])
                last_idx = i
            else:
                break  # Разрыв между строками — конец заголовка

    title = " ".join(title_parts).strip()
    # Убираем номера вроде "1." в начале
    title = re.sub(r'^\d+\.\s*', '', title)

    return title, last_idx


def _find_authors_and_orgs(blocks, start_idx):
    """
    Ищет авторов и организации в блоках после заголовка.
    Останавливается при встрече стоп-маркера (аннотация, введение и т.д.).
    """
    authors = []
    emails = []
    organizations = []

    # Берём блоки после заголовка до стоп-маркера
    candidate_texts = []
    for b in blocks[start_idx + 1:]:
        text_lower = b["text"].lower().strip()

        # Стоп — дошли до аннотации/введения
        if any(text_lower.startswith(m) or text_lower == m for m in _STOP_MARKERS):
            break

        candidate_texts.append(b["text"])

    full_text = "\n".join(candidate_texts)

    # Извлекаем email-адреса
    emails = _EMAIL_RE.findall(full_text)

    # Извлекаем русские ФИО
    ru_names = _RU_NAME_RE.findall(full_text)
    # Извлекаем английские имена
    en_names = _EN_NAME_RE.findall(full_text)

    # Объединяем, убираем дубликаты
    all_names = []
    seen = set()
    for name in ru_names + en_names:
        name = name.strip().rstrip(',;.')
        if name not in seen and len(name) > 3:
            seen.add(name)
            all_names.append(name)

    # Извлекаем организации
    for text in candidate_texts:
        text_lower = text.lower()
        if any(kw in text_lower for kw in _ORG_KEYWORDS):
            org = text.strip().rstrip(',;.')
            if org and len(org) > 5 and org not in organizations:
                organizations.append(org)

    # Собираем авторов с привязкой email/org
    for i, name in enumerate(all_names):
        author = {"name": name, "email": "", "organization": ""}

        # Привязываем email по порядку
        if i < len(emails):
            author["email"] = emails[i]

        # Привязываем организацию (если одна — всем, если несколько — по порядку)
        if len(organizations) == 1:
            author["organization"] = organizations[0]
        elif i < len(organizations):
            author["organization"] = organizations[i]

        authors.append(author)

    # Если нашли email но не нашли авторов — вернём хотя бы email
    if not authors and emails:
        for email in emails:
            authors.append({"name": "", "email": email, "organization": ""})

    return authors


def parse_article_pdf(file_path):
    """
    Парсит PDF-файл научной статьи и извлекает метаданные.
    
    Args:
        file_path: путь к PDF-файлу
        
    Returns:
        dict с ключами:
            - title: str — название статьи
            - authors: list[dict] — авторы [{name, email, organization}]
            - raw_text: str — первые 500 символов текста (для отладки)
    """
    try:
        doc = fitz.open(file_path)
    except Exception as e:
        return {"error": f"Не удалось открыть PDF: {str(e)}", "title": "", "authors": []}

    if doc.page_count == 0:
        doc.close()
        return {"error": "PDF пустой", "title": "", "authors": []}

    # Извлекаем блоки с первой страницы
    page = doc[0]
    blocks = _extract_text_blocks(page)

    # Если мало текста на первой — пробуем вторую
    if len(blocks) < 3 and doc.page_count > 1:
        blocks += _extract_text_blocks(doc[1])

    # Находим название
    title, title_end_idx = _find_title(blocks)

    # Находим авторов
    authors = _find_authors_and_orgs(blocks, title_end_idx)

    # Сырой текст для отладки
    raw_text = page.get_text("text")[:500]

    doc.close()

    return {
        "title": title,
        "authors": authors,
        "raw_text": raw_text,
    }
