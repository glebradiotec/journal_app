"""Клиент API ИС «Метафора» (КИАС РЦНИ), версия API v2.

Спецификация: https://metafora.rcsi.science/api/v2/openapi.yaml (копия — metafora_schema/openapi_v2.yaml).
Авторизация — персональный API-ключ в заголовке `Api-Key`. Ключ берётся ТОЛЬКО из переменной
окружения METAFORA_API_KEY (файл .env), в коде, логах и сообщениях об ошибках его нет.
"""
import os

import requests
from lxml import etree

BASE_URL = os.environ.get("METAFORA_BASE_URL", "https://metafora.rcsi.science").rstrip("/")
TIMEOUT = 60

_XSD_PATH = os.path.join(os.path.dirname(__file__), "metafora_schema", "journal3.xsd")

FILE_STATUS_TEXT = {
    1: "загружен, ожидает обработки",
    2: "обрабатывается",
    3: "обработан успешно",
    4: "ошибка при обработке",
}


class MetaforaError(Exception):
    """Ошибка работы с API. message — уже готовый русский текст для показа пользователю."""

    def __init__(self, message, code=None, status=None, data=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.data = data or {}


# ---------- проверка XML до отправки ----------

def validate_journal_xml(xml_bytes):
    """Проверяет Journal XML по схеме journal3.xsd и обязательным полям Метафоры.
    Возвращает список проблем (пустой — можно отправлять)."""
    problems = []
    try:
        doc = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as e:
        return [f"XML не читается: {e}"]

    schema = etree.XMLSchema(etree.parse(_XSD_PATH))
    if not schema.validate(doc):
        for err in list(schema.error_log)[:10]:
            problems.append(f"Схема journal3.xsd, строка {err.line}: {err.message}")

    if not (doc.findtext("issn") or doc.findtext("eissn")):
        problems.append("Не указан ISSN журнала.")
    for i, art in enumerate(doc.iter("article"), start=1):
        title = (art.findtext("artTitles/artTitle") or "").strip()[:60]
        label = f"Статья №{i} «{title}»"
        if not (art.findtext("pages") or "").strip():
            problems.append(f"{label}: нет диапазона страниц (<pages>) — Метафора такое не принимает.")
        if art.find("authors/author") is None:
            problems.append(f"{label}: нет авторов.")
        if not title:
            problems.append(f"{label}: нет названия.")
        refs = art.findall("references/reference")
        en_only = [n for n, r in enumerate(refs, start=1)
                   if r.find("refInfo[@lang='RUS']") is None and r.find("refInfo[@lang='ENG']") is not None]
        if en_only and len(en_only) < len(refs):
            problems.append(
                f"{label}: ссылки №{', '.join(map(str, en_only))} есть только на английском — в Метафоре "
                "русская версия окажется пустой. Сверьте русский и английский списки литературы."
            )
    return problems


# ---------- запросы ----------

def _api_key():
    key = os.environ.get("METAFORA_API_KEY", "").strip()
    if not key:
        raise MetaforaError(
            "Не задан API-ключ Метафоры. Добавьте строку METAFORA_API_KEY=… в файл .env "
            "на сервере приложения и перезагрузите приложение."
        )
    return key


def _explain(resp):
    """Превращает ответ с ошибкой в MetaforaError с понятным текстом."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("error")
    data = body.get("data") or {}
    status = resp.status_code
    detail = ""
    if status == 422:
        if isinstance(data.get("errors"), list):
            detail = " ".join(str(x) for x in data["errors"])
        elif data.get("description"):
            detail = str(data["description"])
        elif body.get("errors"):
            detail = " ".join(str(v) for vs in body["errors"].values() for v in vs)
        text = f"Метафора не приняла файл (ошибка проверки): {detail or body.get('message', '')}"
    elif status == 409 and code == "XML_ALREADY_EXISTS":
        text = "Такой файл уже загружен в Метафору."
    elif status == 409:
        text = f"Конфликт: {body.get('message', '')}"
    elif status == 401:
        text = "Метафора: API-ключ деактивирован. Создайте новый в разделе «API-ключи»."
    elif status == 403:
        text = "Метафора: нет доступа — ключ неверный или у издательства нет прав на этот журнал (проверьте ISSN)."
    elif status == 404:
        text = "Метафора: не найдено (журнал с таким ISSN у издательства, файл или публикация)."
    else:
        text = f"Метафора вернула ошибку {status}: {body.get('message', '')}"
    return MetaforaError(text.strip(), code=code, status=status, data=data)


def _request(method, path, **kwargs):
    headers = {"Api-Key": _api_key(), "Accept": "application/json"}
    try:
        resp = requests.request(method, BASE_URL + path, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        # В тексте исключения может оказаться URL, но не заголовки — ключ не утечёт.
        raise MetaforaError(f"Не удалось связаться с Метафорой: {type(e).__name__}") from e
    if resp.status_code >= 400:
        raise _explain(resp)
    return resp


def upload_journal_xml(xml_bytes, filename="issue.xml"):
    """Загружает Journal XML выпуска. Возвращает file_uid.
    Если такой файл уже есть, возвращает uid существующего (повторная отправка безопасна)."""
    try:
        resp = _request("POST", "/api/v2/files/journal/",
                        files={"xml": (filename, xml_bytes, "application/xml")})
    except MetaforaError as e:
        if e.code == "XML_ALREADY_EXISTS" and e.data.get("exists_file_uid"):
            return e.data["exists_file_uid"]
        raise
    return resp.json()["data"]["file_uid"]


def file_status(file_uid):
    """Статус обработки файла: {'code': 1..4, 'text': ..., 'articles': [article_uid, ...]}."""
    data = _request("GET", "/api/v2/files/status/", params={"file_uid": file_uid}).json()["data"]
    status = (data.get("xml") or {}).get("status") or {}
    code = status.get("code")
    return {
        "code": code,
        "text": FILE_STATUS_TEXT.get(code, status.get("status_text") or "неизвестно"),
        "articles": data.get("articles") or [],
        "raw": data,
    }


def publications_status(article_uids):
    """{article_uid: signed_at|None} для найденных публикаций."""
    if not article_uids:
        return {}
    items = _request("POST", "/api/v2/publications/status/batch",
                     json={"article_uids": list(article_uids)}).json()
    if isinstance(items, dict):
        items = items.get("data") or []
    return {it["article_uid"]: it.get("signed_at") for it in items}


def get_publication(article_uid):
    """Метаданные публикации: {'RUS': {...title, doi, ...}, 'ENG': {...}}."""
    return _request("GET", f"/api/v2/publications/{article_uid}").json()["article"]


def upload_publication_pdf(article_uid, pdf_bytes, filename="article.pdf"):
    """Загружает PDF статьи. Для подписанной публикации Метафора ответит 409 — сначала снимите подпись."""
    return _request("POST", f"/api/v2/publications/{article_uid}/pdf/",
                    files={"pdf": (filename, pdf_bytes, "application/pdf")}).json()


def sign_publication(article_uid):
    return _request("PUT", f"/api/v2/publications/{article_uid}/sign/").json()


def unsign_publication(article_uid):
    return _request("PUT", f"/api/v2/publications/{article_uid}/unsign/").json()


def delete_file(file_uid):
    _request("DELETE", f"/api/v2/files/{file_uid}")
