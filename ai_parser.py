"""
AI-парсер научных статей через GigaChat API (Сбер).
Извлекает название, авторов, email и организации из текста PDF.
Используется как основной парсер, эвристический — как фоллбэк.
"""

import json
import os
import uuid
import logging
import urllib3

# Подавляем предупреждения о self-signed сертификатах Сбера
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# GigaChat API endpoints
_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

# Кэш access token (живёт 30 минут)
_token_cache = {"token": None, "expires_at": 0}

# Промпт для извлечения метаданных
_SYSTEM_PROMPT = """Ты — помощник для обработки научных статей. Из текста первой страницы PDF-статьи извлеки метаданные.

Верни ТОЛЬКО валидный JSON без пояснений, в формате:
{
  "title": "Название статьи",
  "authors": [
    {"name": "ФИО автора", "email": "email@example.com", "organization": "Название организации"}
  ]
}

Правила:
- Название статьи — полное, без сокращений
- Имена авторов — в том виде, как указаны в статье (ФИО или инициалы)
- Если email или организация не указаны — оставь пустую строку ""
- Если у нескольких авторов одна организация — укажи её каждому
- НЕ включай УДК, DOI, аннотацию в название
- НЕ придумывай данные — только то, что есть в тексте"""


def _get_auth_key():
    """Получает Authorization Key из переменной окружения."""
    key = os.environ.get("GIGACHAT_AUTH_KEY", "")
    return key


def _get_access_token():
    """Получает access token для GigaChat API (кэширует на 30 мин)."""
    import time
    import requests

    # Проверяем кэш (с запасом 60 сек)
    if _token_cache["token"] and _token_cache["expires_at"] > time.time() + 60:
        return _token_cache["token"]

    auth_key = _get_auth_key()
    if not auth_key:
        raise ValueError("GIGACHAT_AUTH_KEY не задан")

    response = requests.post(
        _OAUTH_URL,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": str(uuid.uuid4()),
            "Authorization": f"Basic {auth_key}",
        },
        data={"scope": "GIGACHAT_API_PERS"},
        verify=False,  # Сертификаты Сбера — self-signed
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = data["expires_at"] / 1000  # мс → сек

    return _token_cache["token"]


def _call_gigachat(text):
    """Отправляет текст в GigaChat и возвращает JSON-ответ."""
    import requests

    token = _get_access_token()

    response = requests.post(
        _CHAT_URL,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json={
            "model": "GigaChat",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Вот текст первой страницы статьи:\n\n{text}"},
            ],
            "temperature": 0.1,
            "max_tokens": 1500,
        },
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    content = data["choices"][0]["message"]["content"]
    return content


def parse_with_ai(raw_text):
    """
    Парсит текст статьи через GigaChat AI.

    Args:
        raw_text: текст первой страницы PDF

    Returns:
        dict: {title, authors: [{name, email, organization}]} или None при ошибке
    """
    if not _get_auth_key():
        logger.info("GigaChat: ключ не задан, пропускаем AI-парсинг")
        return None

    try:
        # Ограничиваем текст ~3000 символов (экономия токенов)
        text = raw_text[:3000]

        content = _call_gigachat(text)

        # Извлекаем JSON из ответа (AI может обернуть в ```json ... ```)
        content = content.strip()
        if content.startswith("```"):
            # Убираем markdown code block
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        result = json.loads(content)

        # Валидация структуры
        if "title" not in result:
            result["title"] = ""
        if "authors" not in result:
            result["authors"] = []

        # Нормализуем авторов
        authors = []
        for a in result["authors"]:
            authors.append({
                "name": a.get("name", "").strip(),
                "email": a.get("email", "").strip(),
                "organization": a.get("organization", "").strip(),
            })
        result["authors"] = authors

        logger.info(f"GigaChat: успешно извлечено — заголовок: {result['title'][:50]}..., авторов: {len(authors)}")
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"GigaChat: не удалось разобрать JSON: {e}")
        return None
    except Exception as e:
        logger.warning(f"GigaChat: ошибка AI-парсинга: {e}")
        return None
