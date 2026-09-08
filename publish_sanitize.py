"""Очистка текста, вставленного вручную/скопированного из PDF или Word, от служебного мусора
(возврат каретки, управляющие символы, символ мягкого переноса Word), который иначе
попадает в XML и может не приниматься сторонними валидаторами (например, eLibrary).

Порт xml_import/sanitize.py.
"""
import re

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_SEP_RE = re.compile("[  ]")


def clean_text(s):
    if not s:
        return s
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _LINE_SEP_RE.sub(" ", s)
    s = _CONTROL_CHARS_RE.sub("", s)
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
