"""Короткие буквенные коды журналов для читаемых имён экспортных XML-файлов
(например «iiius_4_2026_elbry.xml»). ISSN — те же, что в publish_titleid_store.py.
"""
import re

ABBR_BY_ISSN = {
    "0033-8486": "RT",     # Радиотехника
    "0320-9601": "ANT",    # Антенны
    "2070-0784": "USR",    # Успехи современной радиоэлектроники
    "1560-4128": "EMV",    # Электромагнитные волны и электронные системы
    "1560-4136": "BR",     # Биомедицинская радиоэлектроника
    "1999-8554": "NK",     # Нейрокомпьютеры: разработка, применение
    "1999-8465": "NAUK",   # Наукоемкие технологии
    "2070-0814": "IIiUS",  # Информационно-измерительные и управляющие системы
    "2070-0970": "NM",     # Нелинейный мир
    "2070-0997": "TGS",    # Технологии живых систем
    "2072-9472": "SVD",    # Системы высокой доступности
    "1999-7493": "DSS",    # Динамика сложных систем - XXI век
    "2225-0980": "NANO",   # Нанотехнологии: разработка, применение - XXI век
}

# Спутниковые системы связи и вещания не имеют ISSN — опознаём по названию.
ABBR_BY_NAME_SUBSTR = {
    "Спутников": "SSSV",
}

EXPORT_SUFFIX = {
    "elibrary": "elbry",
    "crossref": "crssrf",
    "metafora": "metafr",
}


def abbr_for(issn, journal_name=""):
    if issn and issn in ABBR_BY_ISSN:
        return ABBR_BY_ISSN[issn]
    for substr, abbr in ABBR_BY_NAME_SUBSTR.items():
        if substr in (journal_name or ""):
            return abbr
    if journal_name:
        return re.sub(r"[^A-Za-zА-Яа-яЁё0-9]", "", journal_name)[:6].upper() or "JRN"
    return "JRN"


def export_filename(issue, kind):
    abbr = abbr_for(issue.issn, issue.journal_name)
    number = re.sub(r"[^A-Za-zА-Яа-яЁё0-9()]", "-", (issue.number or "").strip()) or "0"
    suffix = EXPORT_SUFFIX.get(kind, kind)
    return f"{abbr.lower()}_{number}_{issue.year}_{suffix}.xml"
