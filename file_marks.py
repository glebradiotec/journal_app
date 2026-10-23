"""Пометки в разделе «Файлы»: какие выпуски с файлового сервера уже есть в разделе «Публикация», а каких нет.

Папка журнала («Нейрокомпьютеры») и название в разделе («Нейрокомпьютеры: разработка, применение») пишутся по-разному, поэтому названия
сравниваются без знаков и регистра, а более короткое может быть началом длинного. Номер берётся из имени папки («Nk_01_2026» → 1).
"""
import re

import file_store as fs
from models_publish import PubIssue

PUSHED, LOADED, MISSING, NO_JOURNAL = "pushed", "loaded", "missing", "nojournal"
LABELS = {PUSHED: ("на сайте", "ok"), LOADED: ("в разделе", "ok"), MISSING: ("нет в разделе", "warn"), NO_JOURNAL: ("журнал не заведён", "off")}
_NUM_RE = re.compile(r"\s*(\d+)(?:\s*[-–]\s*(\d+))?")


def norm(name):
    return re.sub(r"[\W_]+", "", (name or "").casefold().replace("ё", "е"))


def same_journal(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 8 and (a.startswith(b) or b.startswith(a)))


def db_nums(number):
    """'5' → (5,), '2-3' → (2, 3), '1(1)' → (1,); не разобрали — None."""
    m = _NUM_RE.match(number or "")
    return tuple(int(x) for x in m.groups() if x) if m else None


def load_index():
    """Что есть в разделе: [(id, issn, название, год, номер как кортеж, записан ли на сайт)]."""
    return [(i.id, i.issn, i.journal_name or "", i.year, db_nums(i.number), bool(i.pushed_at)) for i in PubIssue.query.all()]


def journal_issns(index, journal_folder):
    return {row[1] for row in index if same_journal(journal_folder, row[2])}


def marks(index, journal_folder, year, issue_folders):
    """{имя папки номера: (состояние, id выпуска в разделе или None)}. Номер не разобрали — папки нет в результате."""
    issns = journal_issns(index, journal_folder)
    found = {row[4]: row for row in index if row[1] in issns and str(row[3]) == str(year) and row[4]}
    out = {}
    for folder in issue_folders:
        nums = fs.fw.issue_title(year, folder)[1]
        if nums == (999,):
            continue
        if not issns:
            out[folder] = (NO_JOURNAL, None)
        elif nums in found:
            row = found[nums]
            out[folder] = (PUSHED if row[5] else LOADED, row[0])
        else:
            out[folder] = (MISSING, None)
    return out


def counts(states):
    c = {PUSHED: 0, LOADED: 0, MISSING: 0, NO_JOURNAL: 0}
    for st, _id in states.values():
        c[st] += 1
    return c
