"""Файловый сервер издательства (SFTP) для раздела «Файлы». ТОЛЬКО ЧТЕНИЕ: здесь нет ни загрузки, ни удаления, ни переименования.

Подключение — настройки FTP_* из .env (те же, что у слежения `tools/ftp_watch.py`): FTP_PROTO=sftp, FTP_HOST, FTP_USER, FTP_PASSWORD,
FTP_HOSTKEY (ключ сервера проверяется до отправки пароля), FTP_ROOT — корневая папка («Журналы текущие»): выше неё подняться нельзя.
"""
import os
import re
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ftp_watch as fw  # noqa: E402  (общий код подключения и чтения папок)

LIST_TTL = 60                                    # секунд: список папки не перечитывается чаще
IDLE_TTL = 45                                    # секунд: соединение для просмотра держится открытым
ZIP_MAX_FILES = 400
ZIP_MAX_BYTES = 400 * 1024 * 1024
MAX_ISSUE_DEPTH = 4                              # глубина обхода папки выпуска при отборе файлов для загрузки
DOC_RE = re.compile(r"\.(docx?|rtf)$", re.I)
PDF_RE = re.compile(r"\.pdf$", re.I)
INLINE_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")

_lock = threading.Lock()
_pool = {"session": None, "used": 0.0}
_cache = {}


class FileStoreError(Exception):
    """Ошибка, текст которой можно показать пользователю."""


class NotConfigured(FileStoreError):
    pass


class BadPath(FileStoreError):
    pass


class TooBig(FileStoreError):
    pass


# ---------------------------------------------------------------- настройки и пути

def _cfg():
    c = fw.config()
    if not c or c.get("proto") != "sftp":
        raise NotConfigured("Файловый сервер не подключён (нужен SFTP: tools/ftp_watch.py setup).")
    return c


def configured():
    try:
        _cfg()
        return True
    except NotConfigured:
        return False


def clean_rel(rel):
    """Путь от корня раздела -> «а/б/в» без «..», пустых звеньев и обратных косых. Всё лишнее — BadPath."""
    rel = (rel or "").replace("\\", "/")
    if "\x00" in rel:
        raise BadPath("Недопустимый путь.")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise BadPath("Недопустимый путь.")
    return "/".join(parts)


def _abs(rel, cfg=None):
    root = (cfg or _cfg())["root"].rstrip("/")
    return (root + ("/" + rel if rel else "")) or "/"


def natural_key(name):
    """«Rt_9» раньше «Rt_10»; регистр не важен."""
    return [(0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------- соединение

def _close(session):
    try:
        session.quit()
    except Exception:  # noqa: BLE001
        pass


def reset():
    """Сброс общего соединения и кэша (после сбоя и в тестах)."""
    with _lock:
        if _pool["session"] is not None:
            _close(_pool["session"])
        _pool.update(session=None, used=0.0)
        _cache.clear()


def _shared_session():
    now = time.time()
    s = _pool["session"]
    if s is not None and (now - _pool["used"] > IDLE_TTL or not _active(s)):
        _close(s)
        s = None
    if s is None:
        s, _mode = fw.connect(_cfg())
        _pool["session"] = s
    _pool["used"] = now
    return s


def _active(session):
    try:
        return bool(session.transport.is_active())
    except Exception:  # noqa: BLE001
        return False


def _friendly(e):
    if isinstance(e, FileNotFoundError) or getattr(e, "errno", None) == 2:
        return FileStoreError("Папка или файл не найдены.")
    if isinstance(e, PermissionError):
        return FileStoreError("Нет прав на чтение этой папки.")
    return FileStoreError("Файловый сервер сейчас не отвечает. Попробуйте ещё раз через минуту.")


def _with_shared(fn):
    """Выполнить fn(session) на общем соединении; при сбое соединения — один повтор на новом."""
    with _lock:
        for attempt in (1, 2):
            try:
                return fn(_shared_session())
            except FileStoreError:
                raise
            except (FileNotFoundError, PermissionError) as e:
                raise _friendly(e)
            except Exception as e:  # noqa: BLE001 — обрыв связи, истёкшая сессия и т. п.
                if _pool["session"] is not None:
                    _close(_pool["session"])
                    _pool.update(session=None, used=0.0)
                if attempt == 2:
                    raise _friendly(e)


# ---------------------------------------------------------------- просмотр папок

def list_folder(rel, refresh=False):
    """Содержимое папки: папки вперёд, дальше по имени «по-человечески». -> [dict(name, is_dir, size, mtime)] (mtime — UTC, ГГГГММДДччммсс)."""
    rel = clean_rel(rel)
    hit = _cache.get(rel)
    if hit and not refresh and time.time() - hit[0] < LIST_TTL:
        return hit[1]
    entries = _with_shared(lambda s: fw.list_dir(s, _abs(rel)))
    entries.sort(key=lambda e: (not e["is_dir"], natural_key(e["name"])))
    _cache[rel] = (time.time(), entries)
    return entries


def current_year():
    return datetime.now().year


def latest_year(entries):
    """Папка года для показа: самая поздняя из свежих (текущий или прошлый год). Журнал без неё — не журнал (служебная папка
    «1111111», брошенный «Наноматериалы…» с 2019 годом) и в списке не показывается."""
    for y in sorted((e["name"] for e in entries if e["is_dir"] and _YEAR_RE.match(e["name"])), reverse=True):
        if int(y) >= current_year() - 1:
            return y
    return None


def journals():
    """Журналы файлового сервера: [dict(name, year, issues=[имена папок номеров], mtime)] — только с папкой свежего года."""
    out = []
    for e in list_folder(""):
        if not e["is_dir"]:
            continue
        try:
            sub = list_folder(e["name"])
            year = latest_year(sub)
            if not year:
                continue
            issues = [i for i in list_folder(f"{e['name']}/{year}") if i["is_dir"]]
        except FileStoreError:
            continue                                   # папку журнала не прочитали — остальные показываем
        ymtime = next((x["mtime"] for x in sub if x["name"] == year), "")
        out.append({"name": e["name"], "year": year, "issues": [i["name"] for i in issues], "mtime": max([ymtime] + [i["mtime"] for i in issues])})
    return out


def is_year(name):
    return bool(_YEAR_RE.match(name))


def is_issue_folder(rel):
    """Папка выпуска — «журнал / год / номер»: только для неё предлагается «Загрузить выпуск в раздел»."""
    parts = clean_rel(rel).split("/")
    return len(parts) == 3 and bool(_YEAR_RE.match(parts[1]))


def recent(mtime, now=None, days=3):
    """Файл или папка изменены за последние days суток — в списке им ставится «новое»."""
    try:
        t = datetime.strptime(mtime[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return ((now or datetime.now(timezone.utc)) - t).total_seconds() < days * 86400


# ---------------------------------------------------------------- чтение файла

def mimetype_for(name):
    return INLINE_TYPES.get(os.path.splitext(name)[1].lower())


def file_size(rel):
    rel = clean_rel(rel)
    if not rel:
        raise BadPath("Это папка.")
    st = _with_shared(lambda s: s.client.stat(_abs(rel)))
    import stat as _stat
    if _stat.S_ISDIR(st.st_mode or 0):
        raise BadPath("Это папка, а не файл.")
    return int(st.st_size or 0)


def stream_file(rel, chunk=64 * 1024):
    """Файл по кускам. Своё отдельное соединение — чтобы долгая выдача не мешала просмотру папок."""
    rel = clean_rel(rel)
    session, _mode = fw.connect(_cfg())
    try:
        f = session.client.open(_abs(rel), "rb")
        try:
            try:
                f.prefetch()
            except Exception:  # noqa: BLE001
                pass
            while True:
                data = f.read(chunk)
                if not data:
                    break
                yield data
        finally:
            f.close()
    finally:
        _close(session)


# ---------------------------------------------------------------- архив папки

def _walk_files(session, rel, max_files, max_bytes):
    """Все файлы под папкой: [(путь_от_корня, размер)]. Лимиты — TooBig."""
    out, total, queue = [], 0, [rel]
    while queue:
        cur = queue.pop(0)
        for e in fw.list_dir(session, _abs(cur)):
            p = f"{cur}/{e['name']}" if cur else e["name"]
            if e["is_dir"]:
                queue.append(p)
                continue
            out.append((p, e["size"]))
            total += e["size"]
            if len(out) > max_files or total > max_bytes:
                raise TooBig(f"Папка слишком большая для архива (больше {max_files} файлов или {max_bytes // 1048576} МБ). "
                             "Откройте её и скачайте нужные файлы по отдельности.")
    return out


def make_zip(rel, max_files=ZIP_MAX_FILES, max_bytes=ZIP_MAX_BYTES):
    """Архив папки во временном файле. -> (путь_к_zip, имя_для_скачивания). Файл удаляет вызывающий."""
    rel = clean_rel(rel)
    if not rel:
        raise TooBig("Корневую папку целиком скачать нельзя — выберите журнал, год или выпуск.")
    session, _mode = fw.connect(_cfg())
    tmp = tempfile.NamedTemporaryFile(prefix="files_", suffix=".zip", delete=False)
    tmp.close()
    try:
        files = _walk_files(session, rel, max_files, max_bytes)
        if not files:
            raise FileStoreError("В папке нет файлов.")
        base = rel.rsplit("/", 1)[-1]
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for p, _size in files:
                inner = p[len(rel) + 1:]
                with zf.open(f"{base}/{inner}", "w", force_zip64=True) as dst:
                    session.client.getfo(_abs(p), dst)
        return tmp.name, base + ".zip"
    except Exception:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        raise
    finally:
        _close(session)


# ---------------------------------------------------------------- файлы выпуска для загрузки в раздел

def pick_issue_files(files):
    """Те же правила, что при перетаскивании папки в загрузку (см. pick() в _import_widget_script.html): .doc/.docx/.rtf и .pdf без
    скрытых и временных файлов Word; если есть подпапки doc / pdf с такими файлами — берутся только они (рецензии, акты, обложки
    не нужны). files — [(путь_от_корня, размер)]. -> [dict(path, name, kind, size)], сначала документы, потом PDF."""
    cand = []
    for path, size in files:
        name = path.rsplit("/", 1)[-1]
        if name.startswith("~$") or name.startswith("."):
            continue
        if DOC_RE.search(name) or PDF_RE.search(name):
            cand.append((path, name, size))

    def in_dir(path, d):
        return d in [p.lower() for p in path.split("/")[:-1]]

    has_doc_dir = any(DOC_RE.search(n) and in_dir(p, "doc") for p, n, _ in cand)
    has_pdf_dir = any(PDF_RE.search(n) and in_dir(p, "pdf") for p, n, _ in cand)
    out = []
    for path, name, size in cand:
        is_doc = bool(DOC_RE.search(name))
        if is_doc and (not has_doc_dir or in_dir(path, "doc")):
            out.append({"path": path, "name": name, "kind": "doc", "size": size})
        elif not is_doc and (not has_pdf_dir or in_dir(path, "pdf")):
            out.append({"path": path, "name": name, "kind": "pdf", "size": size})
    out.sort(key=lambda f: (f["kind"] != "doc", natural_key(f["name"])))
    return out


def issue_files(rel):
    """Файлы выпуска для загрузки в раздел «Публикация» (обход до MAX_ISSUE_DEPTH уровней)."""
    rel = clean_rel(rel)
    found, queue = [], [(rel, 0)]

    def walk(session):
        while queue:
            cur, depth = queue.pop(0)
            for e in fw.list_dir(session, _abs(cur)):
                p = f"{cur}/{e['name']}" if cur else e["name"]
                if e["is_dir"]:
                    if depth + 1 < MAX_ISSUE_DEPTH:
                        queue.append((p, depth + 1))
                else:
                    found.append((p, e["size"]))
        return found

    return pick_issue_files(_with_shared(walk))


def fetch_to_storage(rel):
    """Файл с сервера целиком, в виде объекта, который принимают publish_import.add_doc / add_pdf (имя + save)."""
    rel = clean_rel(rel)
    name = rel.rsplit("/", 1)[-1]
    if not (DOC_RE.search(name) or PDF_RE.search(name)):
        raise BadPath("Для загрузки в раздел подходят только .doc, .docx, .rtf и .pdf.")
    if file_size(rel) > 100 * 1024 * 1024:
        raise TooBig("Файл больше 100 МБ.")
    chunks = b"".join(stream_file(rel))

    class _Storage:
        filename = name

        @staticmethod
        def save(path):
            with open(path, "wb") as f:
                f.write(chunks)

    return _Storage()
