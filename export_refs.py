"""Связывание русского и английского списков литературы в пары для экспорта (Метафора, eLibrary).

В документах авторов английский список (References) — транслитерация русского, но числа ссылок
в них нередко расходятся (в EN лишние или пропущенные позиции). Поэтому по порядку их склеивать
нельзя: ссылки съедут. Здесь списки выравниваются по сходству транслитерации и общему DOI,
порядок сохраняется. Что не нашло пару, остаётся отдельной ссылкой на своём языке.
"""
import re
from difflib import SequenceMatcher

_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;]+", re.I)
MIN_SIMILARITY = 0.5
_HEAD = 70


def _skeleton(s):
    """Грубый «скелет» строки: латиница, без гласных-вариантов и знаков, чтобы разные схемы
    транслитерации (j/y/i, kh/h, ts/c) давали близкие строки."""
    s = "".join(_TR.get(ch, ch) for ch in s.lower())
    s = re.sub(r"[^a-z0-9]", "", s)
    for a, b in (("shch", "sc"), ("sch", "sc"), ("sh", "s"), ("ch", "c"), ("zh", "z"), ("kh", "h"), ("ts", "c"),
                 ("yu", "u"), ("ya", "a"), ("ju", "u"), ("ja", "a"), ("y", "i"), ("j", "i")):
        s = s.replace(a, b)
    return s


def _similarity(ru, en):
    d1 = {m.group(0).lower().rstrip(".") for m in _DOI.finditer(ru)}
    d2 = {m.group(0).lower().rstrip(".") for m in _DOI.finditer(en)}
    if d1 and d1 & d2:
        return 1.0
    return SequenceMatcher(None, _skeleton(ru)[:_HEAD], _skeleton(en)[:_HEAD]).ratio()


def pair_references(ru_list, en_list):
    """-> [(ru|None, en|None), ...] в порядке следования; пары найдены выравниванием по сходству."""
    ru_list, en_list = list(ru_list or []), list(en_list or [])
    if not en_list:
        return [(r, None) for r in ru_list]
    if not ru_list:
        return [(None, e) for e in en_list]
    n, m = len(ru_list), len(en_list)
    sim = [[_similarity(r, e) for e in en_list] for r in ru_list]
    # best[i][j] — максимум суммарного сходства для ru[:i], en[:j] при сохранении порядка
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = sim[i - 1][j - 1]
            take = best[i - 1][j - 1] + s if s >= MIN_SIMILARITY else -1.0
            best[i][j] = max(best[i - 1][j], best[i][j - 1], take)
    pairs, i, j = [], n, m
    while i > 0 and j > 0:
        s = sim[i - 1][j - 1]
        if s >= MIN_SIMILARITY and abs(best[i][j] - (best[i - 1][j - 1] + s)) < 1e-9:
            pairs.append((ru_list[i - 1], en_list[j - 1])); i -= 1; j -= 1
        elif best[i][j] == best[i - 1][j]:
            pairs.append((ru_list[i - 1], None)); i -= 1
        else:
            pairs.append((None, en_list[j - 1])); j -= 1
    while i > 0:
        pairs.append((ru_list[i - 1], None)); i -= 1
    while j > 0:
        pairs.append((None, en_list[j - 1])); j -= 1
    pairs.reverse()
    return pairs
