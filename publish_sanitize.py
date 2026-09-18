"""Очистка текста, вставленного вручную/скопированного из PDF или Word, от служебного мусора
(возврат каретки, управляющие символы, символ мягкого переноса Word), который иначе
попадает в XML и может не приниматься сторонними валидаторами (например, eLibrary).

Порт xml_import/sanitize.py.
"""
import re

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_SEP_RE = re.compile("[  ]")


# Коды полей Word: год, том, страницы и DOI в шаблоне вставлены полями, и при конвертации .doc
# в текст от них остаётся сам код («REF Год \\* MERGEFORMAT 2026», «PAGEREF Начало \\h 6»), а не
# одно подставленное значение. Вырезаем маркер поля вместе с ключами, оставляя значение;
# EMBED (формулы, картинки) значения не несёт и удаляется целиком.
_WORD_FIELD_RE = re.compile(
    r"\b(?:PAGEREF|REF|SEQ|STYLEREF|NUMPAGES|TOC)\s+[^\s\\]*\s*(?:\\\*?\s*[A-Za-z]+\s*)*"
)
_WORD_EMBED_RE = re.compile(r"\bEMBED\s+\S+\s*")


def clean_text(s):
    if not s:
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _LINE_SEP_RE.sub(" ", s)
    s = _CONTROL_CHARS_RE.sub("", s)
    s = _WORD_EMBED_RE.sub("", s)
    s = _WORD_FIELD_RE.sub("", s)
    s = re.sub(r"\s+([.,;:])", r"\1", s)  # пробел перед знаком, оставшийся от вырезанного поля
    s = re.sub(r" {2,}", " ", s).strip()
    return s


def deep_clean_text(value):
    """Рекурсивно применяет clean_text ко всем строкам внутри dict/list (для данных из веб-формы)."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [deep_clean_text(v) for v in value]
    if isinstance(value, dict):
        return {k: deep_clean_text(v) for k, v in value.items()}
    return value
