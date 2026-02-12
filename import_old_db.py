"""
Импорт данных из старого MySQL дампа (oldsite2/dump.sql) в journal_app SQLite.

Цепочка: articles.razd_id → razdel_numbers → nomera → journals
Парсинг авторов из текстового поля (3 формата: простые имена, подробные, HTML).

Запуск:  python import_old_db.py
"""

import re
import sys
import io
import html as html_module
from pathlib import Path
from collections import defaultdict

# Принудительно UTF-8 для вывода (Windows cp1251 не тянет кириллицу в print)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Путь к дампу
DUMP_PATH = r'C:\Users\andri\Desktop\oldsite2\dump.sql'

# ---------------------------------------------------------------------------
#  1. ПАРСЕР MySQL INSERT VALUES
# ---------------------------------------------------------------------------

def parse_mysql_values(sql_line):
    """Парсит MySQL INSERT ... VALUES (...),(...),... в список кортежей."""
    idx = sql_line.find('VALUES ')
    if idx == -1:
        idx = sql_line.find('VALUES(')
        if idx == -1:
            return []

    s = sql_line[idx + 7:] if sql_line[idx + 6] == ' ' else sql_line[idx + 6:]
    results = []
    pos = 0
    length = len(s)

    while pos < length:
        # Ищем открывающую скобку
        while pos < length and s[pos] != '(':
            pos += 1
        if pos >= length:
            break
        pos += 1  # пропускаем '('

        values = []
        while pos < length:
            # Пропускаем пробелы
            while pos < length and s[pos] in ' \t':
                pos += 1
            if pos >= length:
                break

            if s[pos] == ')':
                results.append(tuple(values))
                pos += 1
                break

            if s[pos] == "'":
                # Строковое значение
                pos += 1
                chars = []
                while pos < length:
                    ch = s[pos]
                    if ch == '\\':
                        pos += 1
                        if pos < length:
                            esc = s[pos]
                            if esc == 'r':
                                chars.append('\r')
                            elif esc == 'n':
                                chars.append('\n')
                            elif esc == "'":
                                chars.append("'")
                            elif esc == '\\':
                                chars.append('\\')
                            elif esc == '0':
                                chars.append('')
                            elif esc == 'Z':
                                chars.append('')
                            else:
                                chars.append(esc)
                    elif ch == "'":
                        pos += 1
                        break
                    else:
                        chars.append(ch)
                    pos += 1
                values.append(''.join(chars))
            elif s[pos:pos + 4] == 'NULL':
                values.append(None)
                pos += 4
            else:
                # Число или другое значение без кавычек
                chars = []
                while pos < length and s[pos] not in ',)':
                    chars.append(s[pos])
                    pos += 1
                values.append(''.join(chars).strip())

            # Пропускаем запятую между значениями
            while pos < length and s[pos] in ' \t':
                pos += 1
            if pos < length and s[pos] == ',':
                pos += 1
            elif pos < length and s[pos] == ')':
                results.append(tuple(values))
                pos += 1
                break

    return results


# ---------------------------------------------------------------------------
#  2. ИЗВЛЕЧЕНИЕ ДАННЫХ ИЗ ДАМПА
# ---------------------------------------------------------------------------

def extract_data(dump_path):
    """Читает dump.sql и извлекает журналы, выпуски, разделы, статьи."""
    journals = {}    # old_id -> dict
    nomera = {}      # num_id -> dict
    razdels = {}     # razd_id -> dict
    articles = []    # list of dicts

    print(f"Читаем дамп: {dump_path}")
    with open(dump_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            if line_num % 50 == 0 and line_num < 300:
                pass  # progress

            if 'INSERT INTO `journals`' in line:
                for row in parse_mysql_values(line):
                    if len(row) < 5:
                        continue
                    jid = int(row[0])
                    journals[jid] = {
                        'id': jid,
                        'menu_name': row[1] or '',
                        'journ_name': row[3] or '',
                        'issn': row[14] if len(row) > 14 else '',
                    }

            elif 'INSERT INTO `nomera`' in line:
                for row in parse_mysql_values(line):
                    if len(row) < 4:
                        continue
                    nid = int(row[0])
                    nomera[nid] = {
                        'id': nid,
                        'journal_id': int(row[1]) if row[1] else None,
                        'year': int(row[2]) if row[2] else None,
                        'number_str': row[3] or '',
                        'active': row[5] == '1' if len(row) > 5 else True,
                    }

            elif 'INSERT INTO `razdel_numbers`' in line:
                for row in parse_mysql_values(line):
                    if len(row) < 3:
                        continue
                    rid = int(row[0])
                    razdels[rid] = {
                        'id': rid,
                        'nomera_id': int(row[1]) if row[1] else None,
                        'name': row[2] or '',
                    }

            elif 'INSERT INTO `articles`' in line:
                for row in parse_mysql_values(line):
                    if len(row) < 6:
                        continue
                    art_id = int(row[0])
                    razd_id = int(row[1]) if row[1] else None
                    articles.append({
                        'id': art_id,
                        'razd_id': razd_id,
                        'pages': row[2] or '',
                        'authors_raw': row[3] or '',
                        'title': row[4] or '',
                        'abstract': row[5] or '',
                        'literature': row[6] if len(row) > 6 else '',
                        'authors_eng': row[7] if len(row) > 7 else '',
                        'title_eng': row[8] if len(row) > 8 else '',
                        'abstract_eng': row[9] if len(row) > 9 else '',
                        'keywords': row[11] if len(row) > 11 else '',
                        'keywords_eng': row[12] if len(row) > 12 else '',
                        'doi': row[15] if len(row) > 15 else '',
                        'date_received': row[17] if len(row) > 17 else '',
                    })

    print(f"  Журналов: {len(journals)}")
    print(f"  Выпусков (nomera): {len(nomera)}")
    print(f"  Разделов: {len(razdels)}")
    print(f"  Статей: {len(articles)}")

    return journals, nomera, razdels, articles


# ---------------------------------------------------------------------------
#  3. ПАРСЕР АВТОРОВ
# ---------------------------------------------------------------------------

def strip_html(text):
    """Убирает HTML-теги и декодирует HTML-entities."""
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html_module.unescape(text)
    # Убираем множественные пробелы/переносы
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n', '\n', text)
    return text.strip()


def extract_email(text):
    """Извлекает email из текста."""
    m = re.search(r'[\w.+-]+@[\w.-]+\.\w{2,}', text)
    return m.group(0) if m else ''


def clean_name(name):
    """Чистит имя автора от лишних символов."""
    name = name.strip()
    # Убираем числовые индексы (1, 2, ¹, ², ³ и т.д.)
    name = re.sub(r'[\u00b9\u00b2\u00b3\u2074-\u2079\u2070\u2071]+', '', name)
    name = re.sub(r'\d+$', '', name)
    name = re.sub(r'^\d+', '', name)
    name = name.strip(' ,;.')
    # Убираем пустые скобки
    name = re.sub(r'\(\s*\)', '', name)
    return name.strip()


def is_valid_name(name):
    """Проверяет, похоже ли на имя автора."""
    if not name or len(name) < 3:
        return False
    if len(name) > 200:
        return False
    # Должно содержать буквы
    if not re.search(r'[а-яА-ЯёЁa-zA-Z]', name):
        return False
    # Не должно быть чистым URL или email
    if '@' in name or 'http' in name.lower():
        return False
    return True


def parse_authors_html(text):
    """Парсит авторов из HTML-формата (новые статьи)."""
    authors = []

    # Извлекаем имена из <strong>...</strong>
    name_pattern = r'<strong>(.*?)</strong>\s*(\d+(?:\s*,\s*\d+)*)?'
    names_raw = re.findall(name_pattern, text, re.IGNORECASE)

    if not names_raw:
        return []

    # Убираем HTML из имён
    author_nums = {}  # name -> [numbers]
    for raw_name, nums_str in names_raw:
        name = strip_html(raw_name).strip(' ,;')
        name = clean_name(name)
        if not is_valid_name(name):
            continue
        numbers = []
        if nums_str:
            numbers = [int(n.strip()) for n in nums_str.split(',') if n.strip().isdigit()]
        # Также проверяем символы ¹²³ после strong
        author_nums[name] = numbers

    # Также ищем числа-суперскрипты типа &sup1; или ¹²³ рядом с именами
    # Паттерн для имён с числовыми суперскриптами
    sup_pattern = r'<strong>(.*?)</strong>\s*(?:&sup(\d);|[\u00b9\u00b2\u00b3])'
    sup_matches = re.findall(sup_pattern, text, re.IGNORECASE)
    for raw_name, num_str in sup_matches:
        name = strip_html(raw_name).strip(' ,;')
        name = clean_name(name)
        if is_valid_name(name) and name not in author_nums:
            try:
                author_nums[name] = [int(num_str)]
            except ValueError:
                author_nums[name] = []

    # Убираем все теги для дальнейшего парсинга
    plain = strip_html(text)
    lines = [l.strip() for l in plain.split('\n') if l.strip()]

    # Парсим организации: строки вида "1,2 Название университета (город, страна)"
    orgs = {}  # number -> organization
    email_map = {}  # number -> email
    for line in lines:
        # Организации: "1,2 Юго-Западный государственный университет..."
        org_match = re.match(
            r'^([\d\s,–\-]+)\s+([А-ЯЁA-Z«\"].*)',
            line
        )
        if org_match:
            nums_str = org_match.group(1)
            org_text = org_match.group(2).strip()
            # Если это не email строка
            if '@' not in org_text:
                nums = re.findall(r'\d+', nums_str)
                for n in nums:
                    orgs[int(n)] = org_text

        # Emails: "1 email@domain.ru; 2 email2@domain.ru" или "1 email1, 2 email2"
        email_matches = re.findall(r'(\d+)\s*([\w.+-]+@[\w.-]+\.\w{2,})', line)
        for num_str, email in email_matches:
            email_map[int(num_str)] = email

    # Собираем авторов
    for idx, (name, numbers) in enumerate(author_nums.items()):
        author = {
            'name': name,
            'email': '',
            'organization': '',
            'degree': '',
            'position': '',
        }

        if numbers:
            # Берём организацию первого номера автора
            for n in numbers:
                if n in orgs:
                    author['organization'] = orgs[n]
                    break
            for n in numbers:
                if n in email_map:
                    author['email'] = email_map[n]
                    break
        elif len(author_nums) == 1:
            # Единственный автор — берём первый email из текста
            author['email'] = extract_email(plain)
            # И первую организацию
            if orgs:
                author['organization'] = list(orgs.values())[0]

        authors.append(author)

    return authors


def parse_authors_detailed(text):
    """Парсит авторов из подробного формата (средний период).
    Формат: ФИО - степень, должность, организация. E-mail: email
    Разделитель: \r\n или \n
    """
    authors = []
    # Нормализуем переносы
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    # Разделяем по строкам
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    # Иногда E-mail идёт на отдельной строке, объединяем
    merged = []
    for line in lines:
        if re.match(r'^[EeЕе][\-\s]*mail\s*:', line, re.IGNORECASE) and merged:
            merged[-1] += ' ' + line
        else:
            merged.append(line)
    lines = merged

    for line in lines:
        # Пропускаем строки без букв
        if not re.search(r'[а-яА-ЯёЁa-zA-Z]', line):
            continue

        author = {
            'name': '',
            'email': '',
            'organization': '',
            'degree': '',
            'position': '',
        }

        # Извлекаем email
        email = extract_email(line)
        if email:
            author['email'] = email
            # Убираем email из строки
            line = re.sub(r'[EeЕе][\-\s]*mail\s*:\s*' + re.escape(email), '', line)
            line = re.sub(re.escape(email), '', line)

        line = line.strip(' .,;')

        # Ищем разделитель " - " или " – "
        sep_match = re.search(r'\s+[\-–—]\s+', line)
        if sep_match:
            name_part = line[:sep_match.start()].strip()
            info_part = line[sep_match.end():].strip()

            author['name'] = clean_name(name_part)

            # Из info_part пытаемся извлечь степень
            degree_match = re.search(
                r'([дкДК]\.\s*[а-яА-Я]+\.[\s\-]*[а-яА-Я]*\.?\s*н\.)',
                info_part
            )
            if degree_match:
                author['degree'] = degree_match.group(1).strip()
                info_part = info_part[:degree_match.start()] + info_part[degree_match.end():]

            # Всё остальное — организация (может включать должность)
            info_part = info_part.strip(' ,;.')
            author['organization'] = info_part if info_part else ''
        else:
            # Нет разделителя — вся строка это имя (или имя с должностью)
            author['name'] = clean_name(line)

        if is_valid_name(author['name']):
            authors.append(author)

    return authors


def parse_authors_simple(text):
    """Парсит авторов из простого формата (старые статьи).
    Формат: Фамилия И.О., Фамилия2 И.О., ...
    """
    authors = []
    if not text.strip():
        return authors

    # Проверяем, есть ли "разделитель по авторам"
    # Паттерн русского имени: И.О. Фамилия или Фамилия И.О.
    # Разделитель — запятая
    parts = [p.strip() for p in text.split(',')]

    # Объединяем части, если они похожи на одного автора
    # Например: "Иванов А." + " Б." → "Иванов А. Б."
    # Проверка: если часть начинается с заглавной и содержит только инициал — это продолжение
    merged = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Если это одиночный инициал типа "Б." или "В. И."
        if merged and re.match(r'^[А-ЯЁA-Z]\.\s*[А-ЯЁA-Z]?\.?$', part):
            merged[-1] += ', ' + part
        else:
            merged.append(part)

    for part in merged:
        name = clean_name(part)
        if is_valid_name(name):
            authors.append({
                'name': name,
                'email': '',
                'organization': '',
                'degree': '',
                'position': '',
            })

    return authors


def parse_authors(raw_text):
    """Главная функция парсинга авторов. Определяет формат и вызывает нужный парсер."""
    if not raw_text or not raw_text.strip():
        return []

    text = raw_text.strip()

    # Формат 3: HTML (содержит <strong>, <p>, <br> и т.д.)
    if '<strong>' in text or '<p>' in text:
        result = parse_authors_html(text)
        if result:
            return result
        # Если HTML-парсер не справился, пробуем как текст после strip_html
        text = strip_html(text)

    # Формат 2: Подробный (содержит " - " или " – " И переносы строк)
    has_separator = bool(re.search(r'\s[\-–—]\s', text))
    has_newlines = '\n' in text or '\r' in text
    has_email_marker = bool(re.search(r'[Ee][\-\s]*mail', text, re.IGNORECASE))

    if has_separator and (has_newlines or has_email_marker):
        result = parse_authors_detailed(text)
        if result:
            return result

    # Если есть разделитель " - " но нет переносов — один автор с подробной инфой
    if has_separator:
        result = parse_authors_detailed(text)
        if result:
            return result

    # Формат 1: Простой (только имена через запятую)
    return parse_authors_simple(text)


# ---------------------------------------------------------------------------
#  4. ИМПОРТ В FLASK SQLite
# ---------------------------------------------------------------------------

def import_data():
    """Главная функция импорта."""
    # Проверяем дамп
    dump_path = Path(DUMP_PATH)
    if not dump_path.exists():
        print(f"ОШИБКА: Файл дампа не найден: {DUMP_PATH}")
        sys.exit(1)

    # Извлекаем данные из дампа
    old_journals, nomera, razdels, articles = extract_data(dump_path)

    # Импортируем в Flask
    sys.path.insert(0, str(Path(__file__).parent))
    from app import app, db
    from models import Journal, Issue, Article, ArticleAuthor

    with app.app_context():
        # Проверяем, есть ли уже импортированные данные
        existing_articles = Article.query.count()
        if existing_articles > 100:
            print(f"\n[!] В базе уже {existing_articles} статей.")
            resp = input("Продолжить импорт? Существующие данные НЕ удаляются. (y/n): ")
            if resp.lower() != 'y':
                print("Импорт отменён.")
                return

        # === МАППИНГ ЖУРНАЛОВ ===
        print("\n=== Маппинг журналов ===")

        # Получаем существующие журналы из новой БД
        new_journals = {j.name: j for j in Journal.query.all()}

        # Маппинг old_journal_id → new_journal
        journal_map = {}  # old_id -> new Journal object
        for old_id, old_j in old_journals.items():
            old_name = old_j['menu_name'].strip()
            matched = None

            # Точное совпадение
            if old_name in new_journals:
                matched = new_journals[old_name]
            else:
                # Нечёткий поиск
                for new_name, new_j in new_journals.items():
                    if old_name.lower() in new_name.lower() or new_name.lower() in old_name.lower():
                        matched = new_j
                        break

            if matched:
                journal_map[old_id] = matched
                print(f"  [{old_id}] {old_name} -> [{matched.id}] {matched.name}")
            else:
                # Создаём новый журнал
                issn = old_j.get('issn', '') or ''
                new_j = Journal(name=old_name, issn=issn)
                db.session.add(new_j)
                db.session.flush()  # получаем id
                journal_map[old_id] = new_j
                new_journals[old_name] = new_j
                print(f"  [{old_id}] {old_name} -> СОЗДАН [{new_j.id}] {new_j.name}")

        db.session.commit()

        # === СОЗДАНИЕ ВЫПУСКОВ ===
        print("\n=== Создание выпусков ===")

        # Сначала строим маппинг nomera_id → journal через razdel
        # nomera.jr_num → journals.journ_id
        issue_map = {}   # old nomera_id -> new Issue object
        issues_created = 0
        issues_skipped = 0

        # Кэш существующих выпусков (journal_id, year, number_str) → Issue
        existing_issues = {}
        for iss in Issue.query.all():
            key = (iss.journal_id, iss.year, iss.number)
            existing_issues[key] = iss

        for nid, nom in nomera.items():
            old_journal_id = nom['journal_id']
            if old_journal_id not in journal_map:
                issues_skipped += 1
                continue

            new_journal = journal_map[old_journal_id]
            year = nom['year']
            if not year:
                issues_skipped += 1
                continue

            # Парсим номер выпуска
            num_str = nom['number_str']
            num_match = re.search(r'\d+', num_str)
            number = int(num_match.group()) if num_match else 0

            # Проверяем, есть ли уже такой выпуск
            key = (new_journal.id, year, number)
            if key in existing_issues:
                issue_map[nid] = existing_issues[key]
            else:
                issue = Issue(
                    number=number,
                    year=year,
                    journal_id=new_journal.id,
                    position=0,
                )
                db.session.add(issue)
                db.session.flush()
                issue_map[nid] = issue
                existing_issues[key] = issue
                issues_created += 1

        db.session.commit()
        print(f"  Создано выпусков: {issues_created}")
        print(f"  Пропущено: {issues_skipped}")

        # === ИМПОРТ СТАТЕЙ ===
        print("\n=== Импорт статей ===")

        # Маппинг razd_id → nomera_id
        razd_to_nomera = {}
        for rid, razd in razdels.items():
            razd_to_nomera[rid] = razd['nomera_id']

        articles_created = 0
        articles_skipped = 0
        authors_created = 0
        batch_size = 500
        total = len(articles)

        for i, art in enumerate(articles):
            if (i + 1) % 1000 == 0:
                print(f"  Обработано {i + 1}/{total} статей...")
                db.session.commit()

            # Пропускаем статьи без названия
            title = (art['title'] or '').strip()
            if not title:
                articles_skipped += 1
                continue

            # Находим выпуск через раздел → номер
            razd_id = art['razd_id']
            if razd_id and razd_id in razd_to_nomera:
                nomera_id = razd_to_nomera[razd_id]
                if nomera_id in issue_map:
                    issue = issue_map[nomera_id]
                else:
                    articles_skipped += 1
                    continue
            else:
                articles_skipped += 1
                continue

            # Парсим авторов
            parsed_authors = parse_authors(art['authors_raw'])

            # Формируем строку авторов для поля authors
            author_names = ', '.join(a['name'] for a in parsed_authors if a['name'])

            # Создаём статью
            article = Article(
                title=title,
                authors=author_names or art['authors_raw'][:500],
                issue_id=issue.id,
                submission_date=art.get('date_received', ''),
                notes='',
            )
            db.session.add(article)
            db.session.flush()

            # Создаём записи авторов
            for order, author_data in enumerate(parsed_authors):
                if not author_data['name']:
                    continue
                aa = ArticleAuthor(
                    article_id=article.id,
                    full_name=author_data['name'],
                    email=author_data.get('email', ''),
                    organization=author_data.get('organization', ''),
                    degree=author_data.get('degree', ''),
                    position=author_data.get('position', ''),
                    order=order,
                )
                db.session.add(aa)
                authors_created += 1

            articles_created += 1

        db.session.commit()

        print(f"\n=== РЕЗУЛЬТАТЫ ===")
        print(f"  Журналов в базе: {Journal.query.count()}")
        print(f"  Выпусков в базе: {Issue.query.count()}")
        print(f"  Статей создано: {articles_created}")
        print(f"  Статей пропущено: {articles_skipped}")
        print(f"  Авторов создано: {authors_created}")
        print(f"  Уникальных авторов (по имени): {db.session.query(db.func.count(db.distinct(ArticleAuthor.full_name))).scalar()}")

        # Показываем топ-10 авторов по количеству статей
        print(f"\n=== Топ-10 авторов ===")
        top_authors = (
            db.session.query(
                ArticleAuthor.full_name,
                db.func.count(ArticleAuthor.id).label('cnt')
            )
            .group_by(ArticleAuthor.full_name)
            .order_by(db.text('cnt DESC'))
            .limit(10)
            .all()
        )
        for name, cnt in top_authors:
            print(f"  {name}: {cnt} статей")


if __name__ == '__main__':
    import_data()
