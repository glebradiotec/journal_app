"""Генерация DOI по паттерну, уже используемому сайтом radiotec.ru.

Примеры существующих DOI:
  10.18127/j00338486-201901-01           (год 2019, выпуск "1", статья 01)
  10.18127/j00338486-202001(01)-09       (год 2020, выпуск "1(1)", статья 09)
  10.18127/j20700784-201910-12           (журнал с issn 2070-0784, выпуск "10")

Правило: 10.18127/j{issn без дефиса}-{год}{номер выпуска, дополненный до 2 цифр}
         [-> если в номере выпуска есть "(N)", то тоже дополняется до 2 цифр внутри скобок]
         -{порядковый номер статьи в выпуске, 2 цифры}

Порт xml_import/doi.py.
"""
import re

DOI_PREFIX = "10.18127"


def _pad2(s):
    s = s.strip()
    if s.isdigit():
        return s.zfill(2)
    return s


def format_issue_segment(num_num):
    """'1' -> '01', '1(1)' -> '01(01)', '10' -> '10'."""
    m = re.match(r"^(\d+)(\((\d+)\))?$", num_num.strip())
    if not m:
        return num_num.strip()
    base = _pad2(m.group(1))
    if m.group(3):
        return f"{base}({_pad2(m.group(3))})"
    return base


def build_doi(issn, year, num_num, article_seq):
    issn_digits = issn.replace("-", "")
    issue_segment = format_issue_segment(str(num_num))
    seq = str(article_seq).zfill(2)
    return f"{DOI_PREFIX}/j{issn_digits}-{year}{issue_segment}-{seq}"
