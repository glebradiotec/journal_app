#!/usr/bin/env python3
"""Слежение за папкой на FTP/SFTP: не появились ли новые номера журналов. Сводка — в Telegram.

  setup           спрашивает хост, логин, пароль ЗДЕСЬ, в терминале; проверяет подключение и показывает папки;
                  записывает FTP_* в .env (старый .env сохраняется). Пароль не печатается и никуда не уходит, кроме FTP и .env.
  ls [глубина]    ТОЛЬКО ЧТЕНИЕ: дерево папок на FTP (по умолчанию 2 уровня).
  check [--dry]   проверка папки; сравнение с прошлым разом; сводка в Telegram. С --dry — только показать на экране,
                  ничего не отправлять и не запоминать.
  install         ставит расписание (systemd-таймер): 10:00 и 14:00 по Москве. Нужны права root.
  relay           подключает «мостик» к Telegram (если с сервера api.telegram.org недоступен).

Папка на FTP — только чтение: утилита ничего не загружает, не удаляет и не переименовывает.
Запуск на сервере:  cd /var/www/journal_app && venv/bin/python3 tools/ftp_watch.py setup
"""
import base64
import ftplib
import getpass
import hashlib
import html
import json
import os
import re
import shutil
import socket
import ssl
import stat
import sys
from collections import defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
ENV_PATH = os.path.join(ROOT, ".env")
SNAPSHOT_PATH = os.path.join(ROOT, "instance", "ftp_snapshot.json")
MAX_DEPTH = 5
TELEGRAM_LIMIT = 3800

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ---------------------------------------------------------------- настройки

def config(env=None):
    """Настройки из окружения (.env подгружает вызывающий). -> dict или None, если FTP не настроен.
    FTP_PROTO: «ftp» (по умолчанию; пробует FTPS) или «sftp» (SSH, порт 22; FTP_HOSTKEY — отпечаток ключа сервера)."""
    env = os.environ if env is None else env
    host = (env.get("FTP_HOST") or "").strip()
    if not host:
        return None
    proto = (env.get("FTP_PROTO") or "ftp").strip().lower()
    return {
        "proto": proto, "hostkey": (env.get("FTP_HOSTKEY") or "").strip(),
        "host": host, "port": int(env.get("FTP_PORT") or (22 if proto == "sftp" else 21)), "user": env.get("FTP_USER") or "anonymous",
        "password": env.get("FTP_PASSWORD") or "", "tls": (env.get("FTP_TLS") or "auto").lower(),
        "root": (env.get("FTP_ROOT") or ("." if proto == "sftp" else "/")).strip() or "/", "encoding": env.get("FTP_ENCODING") or "utf-8",
        "years_back": None if (env.get("FTP_YEARS_BACK") or "").lower() == "all" else int(env.get("FTP_YEARS_BACK") or 1),   # лет назад, кроме текущего; all — без ограничения
    }


def env_quote(value):
    """Значение для .env в двойных кавычках: пароли с #, пробелами и кавычками иначе ломают файл."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(path, updates):
    """Дописывает/заменяет переменные в .env, остальное не трогает."""
    current = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    keys = tuple(updates)
    lines = [l for l in current.splitlines() if not any(l.startswith(k + "=") for k in keys)]
    lines += [f"{k}={env_quote(v)}" for k, v in updates.items()]
    open(path, "w", encoding="utf-8").write("\n".join(lines).rstrip("\n") + "\n")


# ---------------------------------------------------------------- FTP

class HostKeyChanged(Exception):
    """Ключ SFTP-сервера не тот, что запомнили при настройке: либо сервер переустановили, либо кто-то подменил адрес."""


class SftpSession:
    """Тонкая обёртка над paramiko: список папки и закрытие. Пароль уходит на сервер только после проверки его ключа."""

    def __init__(self, transport, client, fingerprint=""):
        self.transport, self.client, self.fingerprint = transport, client, fingerprint

    def list(self, path):
        out = []
        for a in self.client.listdir_attr(path):
            if a.filename in (".", ".."):
                continue
            mode = a.st_mode or 0
            if stat.S_ISLNK(mode):                       # ссылка: смотрим, на что она указывает
                try:
                    mode = self.client.stat(path.rstrip("/") + "/" + a.filename).st_mode or 0
                except OSError:
                    pass
            mtime = datetime.fromtimestamp(a.st_mtime, timezone.utc).strftime("%Y%m%d%H%M%S") if a.st_mtime else ""
            out.append({"name": a.filename, "is_dir": stat.S_ISDIR(mode), "size": int(a.st_size or 0), "mtime": mtime})
        return out

    def quit(self):
        try:
            self.client.close()
        finally:
            self.transport.close()


def key_fingerprint(key):
    """Отпечаток ключа сервера в том виде, как его показывает ssh: SHA256:…"""
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def sftp_connect(cfg):
    """SFTP-подключение. Порядок: ключ сервера -> сверка с запомненным -> только потом пароль. -> (SftpSession, 'SFTP')."""
    import paramiko                                    # ставится отдельно (pip install paramiko); без SFTP не нужен
    sock = socket.create_connection((cfg["host"], cfg["port"]), timeout=20)
    sock.settimeout(60)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=30)
        fingerprint = key_fingerprint(transport.get_remote_server_key())
        if cfg.get("hostkey") and cfg["hostkey"] != fingerprint:
            raise HostKeyChanged(f"отпечаток ключа сервера {fingerprint} не совпадает с запомненным {cfg['hostkey']}")
        transport.auth_password(cfg["user"], cfg["password"])
        client = paramiko.SFTPClient.from_transport(transport)
    except Exception:
        transport.close()
        raise
    return SftpSession(transport, client, fingerprint), "SFTP"


def connect(cfg):
    """Подключение. tls=auto: сначала шифрованное (FTPS), если сервер не умеет — обычное с предупреждением.
    -> (ftp, 'FTPS'|'FTP')."""
    if cfg.get("proto") == "sftp":
        return sftp_connect(cfg)
    last = None
    if cfg["tls"] in ("auto", "yes", "on", "true", "1"):
        try:
            ftp = ftplib.FTP_TLS(timeout=30, context=ssl._create_unverified_context())
            ftp.encoding = cfg["encoding"]
            ftp.connect(cfg["host"], cfg["port"])
            ftp.auth()
            ftp.login(cfg["user"], cfg["password"])
            ftp.prot_p()
            return ftp, "FTPS"
        except ftplib.all_errors as e:        # плоский кортеж: вложенный в except не допускается (ssl.SSLError и так OSError)
            last = e
            if cfg["tls"] != "auto":
                raise
    ftp = ftplib.FTP(timeout=30)
    ftp.encoding = cfg["encoding"]
    ftp.connect(cfg["host"], cfg["port"])
    ftp.login(cfg["user"], cfg["password"])
    return ftp, "FTP"


_UNIX = re.compile(r"^(?P<perm>[-dlbcps][rwxsStT-]{9})\s+\d+\s+\S+\s+\S+\s+(?P<size>\d+)\s+"
                   r"(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<year_time>\d{4}|\d{1,2}:\d{2})\s+(?P<name>.+)$")
_DOS = re.compile(r"^(?P<date>\d{2}-\d{2}-\d{2,4})\s+(?P<time>\d{1,2}:\d{2}(?:AM|PM)?)\s+(?P<size><DIR>|\d+)\s+(?P<name>.+)$", re.I)


def parse_list_line(line, now=None):
    """Строка ответа LIST (unix- или DOS-формат) -> dict(name, is_dir, size, mtime 'YYYYMMDDHHMMSS') или None."""
    now = now or datetime.now()
    m = _UNIX.match(line.strip())
    if m:
        name = m["name"].split(" -> ")[0]
        mon, day = _MONTHS.get(m["mon"].lower(), 1), int(m["day"])
        yt = m["year_time"]
        if ":" in yt:
            hh, mm = map(int, yt.split(":"))
            year = now.year - (1 if (mon, day) > (now.month, now.day) else 0)
        else:
            year, hh, mm = int(yt), 0, 0
        return {"name": name, "is_dir": m["perm"][0] == "d", "size": int(m["size"]),
                "mtime": f"{year:04d}{mon:02d}{day:02d}{hh:02d}{mm:02d}00"}
    m = _DOS.match(line.strip())
    if m:
        mo, d, y = m["date"].split("-")
        y = int(y) + (2000 if int(y) < 70 else 1900) if len(y) == 2 else int(y)
        t = m["time"].upper()
        hh, mm = map(int, re.sub(r"[AP]M", "", t).split(":"))
        if t.endswith("PM") and hh < 12:
            hh += 12
        if t.endswith("AM") and hh == 12:
            hh = 0
        return {"name": m["name"], "is_dir": m["size"] == "<DIR>", "size": 0 if m["size"] == "<DIR>" else int(m["size"]),
                "mtime": f"{y:04d}{int(mo):02d}{int(d):02d}{hh:02d}{mm:02d}00"}
    return None


def _list_dir_raw(ftp, path):
    """Содержимое папки: MLSD, а если сервер её не знает — LIST. -> [dict(name, is_dir, size, mtime)]."""
    if isinstance(ftp, SftpSession):
        return ftp.list(path)
    out = []
    try:
        for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
            kind = (facts.get("type") or "").lower()
            if kind in ("cdir", "pdir") or name in (".", ".."):
                continue
            out.append({"name": name, "is_dir": kind == "dir", "size": int(facts.get("size") or 0),
                        "mtime": (facts.get("modify") or "")[:14]})
        return out
    except ftplib.error_perm:
        pass                                           # 500/502: MLSD не поддерживается
    lines = []
    ftp.cwd(path)
    ftp.retrlines("LIST", lines.append)
    for line in lines:
        e = parse_list_line(line)
        if e and e["name"] not in (".", ".."):
            out.append(e)
    return out


_JUNK_RE = re.compile(r"^(\._|\.DS_Store$|Thumbs\.db$|@eaDir|#recycle|\$RECYCLE|~\$|\.AppleDouble|\.TemporaryItems)", re.I)


def list_dir(ftp, path):
    """Содержимое папки без служебного мусора (корзина NAS, «._DAV», .DS_Store, временные файлы Word)."""
    return [e for e in _list_dir_raw(ftp, path) if not _JUNK_RE.match(e["name"])]


_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")


def default_prune(min_year):
    """Что не обходить при слежении: папки журналов с одними цифрами (служебные), в журнале — всё, кроме папок-годов не старше
    min_year (архив с 2008 года не нужен, «font», «TEMP» тоже), файлы прямо в корне. -> prune(родитель, имя, это_папка)."""
    def prune(rel, name, is_dir):
        depth = rel.count("/") + 1 if rel else 0
        if depth == 0:
            return name.isdigit() if is_dir else True
        if depth == 1:
            return not (is_dir and _YEAR_RE.match(name) and int(name) >= min_year)
        return False
    return prune


def scan(ftp, root, max_depth=MAX_DEPTH, prune=None):
    """Обход папки в ширину. -> {'files': {путь_от_корня: [размер, mtime]}, 'dirs': [путь, …], 'skipped': [путь, …]}.
    Папка, которую пользователь читать не может (нет прав), пропускается и попадает в 'skipped' — обход из-за неё не падает.
    prune(родитель, имя, это_папка) -> True: не заходить/не учитывать (см. default_prune)."""
    files, dirs, skipped = {}, [], []
    queue = [("", 0)]
    while queue:
        rel, depth = queue.pop(0)
        full = root.rstrip("/") + ("/" + rel if rel else "")
        try:
            entries = list_dir(ftp, full or "/")
        except (OSError, ftplib.error_perm):
            if not rel:
                raise                                    # не открылась сама отслеживаемая папка — это настоящая ошибка
            skipped.append(rel)
            continue
        for e in sorted(entries, key=lambda x: x["name"]):
            if prune and prune(rel, e["name"], e["is_dir"]):
                continue
            p = f"{rel}/{e['name']}" if rel else e["name"]
            if e["is_dir"]:
                dirs.append(p)
                if depth + 1 < max_depth:
                    queue.append((p, depth + 1))
            else:
                files[p] = [e["size"], e["mtime"]]
    return {"files": files, "dirs": sorted(dirs), "skipped": sorted(skipped)}


# ---------------------------------------------------------------- сравнение и сводка

def diff(old, new):
    """Что изменилось с прошлой проверки: новые, изменённые, пропавшие файлы (пути)."""
    of, nf = (old or {}).get("files", {}), new["files"]
    added = sorted(p for p in nf if p not in of)
    changed = sorted(p for p in nf if p in of and nf[p] != of[p])
    removed = sorted(p for p in of if p not in nf)
    return {"added": added, "changed": changed, "removed": removed}


def issue_title(year, folder):
    """Папка номера «Emw_04-2026» в папке года 2026 -> («2026 №4», (4,)); «02-03_2008» -> («2008 №2-3», (2, 3)).
    Номер — первое число из 1–2 цифр после вычёркивания года. Не нашли — название папки как есть."""
    rest = re.sub(r"(?:19|20)\d\d", " ", folder)
    m = re.search(r"(?<!\d)(\d{1,2})(?:\s*[-_–]\s*(\d{1,2}))?(?!\d)", rest)
    if not m:
        return f"{year} {folder}", (999,)
    nums = tuple(int(x) for x in m.groups() if x)
    return f"{year} №" + "-".join(str(n) for n in nums), nums


def _group(path):
    """Журнал / выпуск по пути. Структура на сервере: журнал / год / папка номера / файлы —
    «Радиотехника/2026/Rt_09-2026/макет/doc/a.doc» -> («Радиотехника», «2026 №9»). Без папок-годов:
    «Радиотехника/8/DOC/a.doc» -> («Радиотехника», «8»)."""
    parts = path.split("/")
    if len(parts) > 2 and _YEAR_RE.match(parts[1]):
        return (parts[0], issue_title(parts[1], parts[2])[0] if len(parts) > 3 else parts[1])
    return (parts[0], parts[1] if len(parts) > 2 else "")


def _kinds(paths):
    c = defaultdict(int)
    for p in paths:
        ext = p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else "без расширения"
        c["doc" if ext in ("doc", "docx", "rtf") else ext] += 1
    return ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _num_key(s):
    m = re.match(r"\d+", s or "")
    return (0, int(m.group(0)), s) if m else (1, 0, s or "")


def _issue_info(path):
    """(ключ сортировки, подпись) выпуска, к которому относится файл, или None (файл не в папке номера)."""
    parts = path.split("/")
    if len(parts) > 3 and _YEAR_RE.match(parts[1]):
        title, nums = issue_title(parts[1], parts[2])
        return (int(parts[1]), nums), title
    if len(parts) > 2 and not _YEAR_RE.match(parts[1]):
        return (0, _num_key(parts[1])), f"№{parts[1]}"
    return None


def latest_per_journal(snapshot):
    """Для каждого журнала: самый поздний выпуск (год, потом номер — по именам папок) и время последнего файла в нём."""
    best = {}
    for path, (size, mtime) in snapshot["files"].items():
        info = _issue_info(path)
        if not info:
            continue
        key, title = info
        journal = path.split("/")[0]
        cur = best.get(journal)
        if cur is None or key > cur[0]:
            best[journal] = [key, title, mtime]
        elif key == cur[0] and mtime > cur[2]:
            cur[2] = mtime
    return {j: (v[1], v[2]) for j, v in best.items()}


def _fmt_mtime(m):
    try:
        return datetime.strptime(m[:12], "%Y%m%d%H%M").strftime("%d.%m %H:%M")
    except ValueError:
        return "—"


def build_summary(root_label, new_snap, old_snap, when=None):
    """Текст сводки для Telegram (HTML). -> str."""
    when = when or datetime.now()
    esc = html.escape
    head = f"📂 <b>FTP «{esc(root_label)}»</b> — сводка {when.strftime('%d.%m %H:%M')}"
    nfiles, ndirs = len(new_snap["files"]), len(new_snap["dirs"])
    lines = [head]
    if old_snap is None:
        lines.append(f"Первая проверка: слежение включено. В папке {nfiles} файлов, {ndirs} подпапок.")
    else:
        d = diff(old_snap, new_snap)
        if not (d["added"] or d["changed"] or d["removed"]):
            lines.append("Новых и изменённых файлов нет.")
        else:
            groups = defaultdict(lambda: {"added": [], "changed": [], "removed": []})
            for kind in ("added", "changed", "removed"):
                for p in d[kind]:
                    groups[_group(p)][kind].append(p)
            lines.append("Что нового с прошлой проверки:")
            for (journal, issue), g in sorted(groups.items(), key=lambda kv: (kv[0][0], _num_key(kv[0][1]))):
                title = f"{journal} / {issue}" if issue else journal
                bits = []
                if g["added"]:
                    bits.append(f"+{len(g['added'])} новых ({_kinds(g['added'])})")
                if g["changed"]:
                    bits.append(f"изменено {len(g['changed'])}")
                if g["removed"]:
                    bits.append(f"удалено {len(g['removed'])}")
                lines.append(f"• <b>{esc(title)}</b>: " + "; ".join(bits))
    if new_snap.get("skipped"):
        lines.append(f"⚠️ Не прочитал папок (нет прав): {len(new_snap['skipped'])} — {esc(', '.join(new_snap['skipped'][:3]))}"
                     + ("…" if len(new_snap["skipped"]) > 3 else ""))
    latest = latest_per_journal(new_snap)
    if latest:
        lines.append("")
        lines.append("Последний выпуск по журналам (по папкам):")
        for journal, (issue, mtime) in sorted(latest.items()):
            lines.append(f"  {esc(journal)} — {esc(issue)} ({_fmt_mtime(mtime)})")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_LIMIT:
        text = text[:TELEGRAM_LIMIT].rsplit("\n", 1)[0] + "\n… (сводка обрезана)"
    return text


# ---------------------------------------------------------------- Telegram, снимок

def telegram_base(env=None):
    """Адрес Telegram Bot API. С сервера приложения api.telegram.org не открывается (сеть блокирует) — тогда в .env задан
    TELEGRAM_API_BASE: адрес «мостика» (Cloudflare Worker, tools/telegram_relay_worker.js), который передаёт запросы дальше."""
    env = os.environ if env is None else env
    return (env.get("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")


def send_telegram(text, env=None):
    """Отправка в Telegram токеном и чатом приложения (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). -> (ok, пояснение)."""
    import requests
    import urllib3.util.connection as _conn
    _conn.allowed_gai_family = lambda: socket.AF_INET        # у сервера нет маршрута IPv6: имя Telegram сначала даёт IPv6 и зависает
    env = os.environ if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы в .env"
    try:
        r = requests.post(f"{telegram_base(env)}/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"},
                          timeout=20)
    except requests.RequestException as e:
        return False, f"не удалось связаться с Telegram ({telegram_base(env)}): {type(e).__name__}"   # текст ошибки может содержать токен
    if r.status_code != 200:
        return False, f"Telegram ответил {r.status_code}: {r.text[:150].replace(token, '***')}"
    return True, ""


def load_snapshot(path=SNAPSHOT_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_snapshot(snap, path=SNAPSHOT_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, path)


# ---------------------------------------------------------------- команды

def _load_env():
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)


def run_check(dry=False, cfg=None, ftp_factory=None, snapshot_path=SNAPSHOT_PATH, send=send_telegram, now=None):
    """Одна проверка. -> (код выхода, текст сводки)."""
    cfg = cfg or config()
    if not cfg:
        return 2, "FTP не настроен: выполните «tools/ftp_watch.py setup»."
    ftp, mode = (ftp_factory or connect)(cfg)
    try:
        years_back = cfg.get("years_back", 1)
        new = scan(ftp, cfg["root"], prune=None if years_back is None else default_prune((now or datetime.now()).year - years_back))
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass
    new["checked_at"] = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    new["mode"] = mode
    old = load_snapshot(snapshot_path)
    label = os.path.basename(cfg["root"].rstrip("/"))
    text = build_summary(label if label not in ("", ".") else "корень", new, old, when=now)
    if dry:
        return 0, text
    ok, why = send(text)
    if not ok:
        # не запоминаем снимок: иначе новое «потеряется» — в следующий раз оно снова попадёт в сводку
        return 1, text + f"\n\n[Telegram: {why}]"
    save_snapshot(new, snapshot_path)
    return 0, text


def cmd_check(args):
    _load_env()
    code, text = run_check(dry="--dry" in args)
    print(re.sub(r"</?b>", "", html.unescape(text)))
    return code


def cmd_ls(args):
    _load_env()
    cfg = config()
    if not cfg:
        print("FTP не настроен.")
        return 2
    depth = int(args[0]) if args else 2
    ftp, mode = connect(cfg)
    print(f"Подключено ({mode}), папка: {cfg['root']}")
    snap = scan(ftp, cfg["root"], max_depth=depth)
    ftp.quit()
    for d in snap["dirs"]:
        n = sum(1 for p in snap["files"] if p.startswith(d + "/"))
        print(f"  {d}/" + (f"   [{n} файлов]" if n else ""))
    for p in sorted(p for p in snap["files"] if "/" not in p):
        print(f"  {p}")
    print(f"\nВсего: {len(snap['files'])} файлов на глубине до {depth}, {len(snap['dirs'])} папок.")
    if snap.get("skipped"):
        print(f"Не удалось прочитать (нет прав), пропущено папок: {len(snap['skipped'])}:")
        for d in snap["skipped"]:
            print(f"   {d}/")
    return 0


def cmd_relay(args):
    """Подключает «мостик» к Telegram: проверяет, что через него бот отвечает, и записывает TELEGRAM_API_BASE в .env."""
    import requests
    import urllib3.util.connection as _conn
    _conn.allowed_gai_family = lambda: socket.AF_INET
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from site_connect import fix_env_permissions
    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("В .env нет TELEGRAM_BOT_TOKEN.")
        return 1
    base = input("Адрес мостика (вида https://имя.workers.dev/СЕКРЕТ): ").strip().rstrip("/")
    if not base.startswith("https://"):
        print("Адрес должен начинаться с https://. Ничего не изменено.")
        return 1
    print("Проверяю, отвечает ли бот через мостик…")
    try:
        r = requests.get(f"{base}/bot{token}/getMe", timeout=20)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print("Мостик не отвечает:", type(e).__name__, "— ничего не изменено.")
        return 1
    if not data.get("ok"):
        print(f"Telegram ответил отказом (http {r.status_code}): {str(data.get('description') or data)[:150]} — ничего не изменено.")
        return 1
    print(f"Бот отвечает: @{(data.get('result') or {}).get('username')}")
    if input("Записать в .env? [y/N] ").strip().lower() != "y":
        print("Ничего не изменено.")
        return 1
    if os.path.exists(ENV_PATH):
        shutil.copy2(ENV_PATH, ENV_PATH + ".bak_before_relay")
    write_env(ENV_PATH, {"TELEGRAM_API_BASE": base})
    ok, why = fix_env_permissions(ENV_PATH)
    if not ok:
        print("ВНИМАНИЕ:", why)
        shutil.copy2(ENV_PATH + ".bak_before_relay", ENV_PATH)
        print("Прежний .env возвращён, ничего не применено.")
        return 1
    print("Записано. Теперь сводки пойдут через мостик (`check`). Бот приложения подхватит его после перезапуска службы journal-bot.")
    return 0


def pick_folder(typed, entries):
    """Что ввёл пользователь -> имя папки на сервере. Номер из списка, либо название без учёта регистра («журналы текущие» найдёт
    «Журналы текущие»: на Linux регистр важен). Не нашли — возвращаем как введено."""
    dirs = [e["name"] for e in entries if e["is_dir"]]
    t = typed.strip()
    if t.isdigit() and 1 <= int(t) <= len(dirs):
        return dirs[int(t) - 1]
    if "/" in t.strip("/"):
        return t
    low = [d for d in dirs if d.casefold() == t.strip("/").casefold()]
    return low[0] if len(low) == 1 else t.strip("/")


def cmd_setup(args):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from site_connect import fix_env_permissions
    current = open(ENV_PATH, encoding="utf-8").read() if os.path.exists(ENV_PATH) else ""
    if re.search(r"^FTP_HOST=", current, re.M) and input("Подключение уже настроено. Заменить? [y/N] ").strip().lower() != "y":
        print("Ничего не изменено.")
        return 1
    print("Подключение к серверу с файлами. Пароль при вводе не показывается.")
    kind = input("Протокол: 1 — SFTP (через SSH, порт 22, надёжнее), 2 — FTP (порт 21) [1]: ").strip() or "1"
    proto = "ftp" if kind == "2" else "sftp"
    host = input("Адрес сервера (например 85.30.219.155): ").strip().replace("sftp://", "").replace("ftp://", "").strip("/")
    default_port = "21" if proto == "ftp" else "22"
    port = input(f"Порт [{default_port}]: ").strip() or default_port
    user = input("Логин: ").strip()
    password = getpass.getpass("Пароль: ")
    if not (host and user and password):
        print("Нужны адрес, логин и пароль. Ничего не изменено.")
        return 1
    cfg = {"proto": proto, "hostkey": "", "host": host, "port": int(port), "user": user, "password": password,
           "tls": "auto", "root": "." if proto == "sftp" else "/", "encoding": "utf-8"}
    print("\nПроверяю подключение…")
    try:
        session, mode = connect(cfg)
        top = list_dir(session, cfg["root"])
        fingerprint = getattr(session, "fingerprint", "")
        session.quit()
    except Exception as e:  # noqa: BLE001
        print("Подключиться не удалось:", str(e).replace(password, "***")[:300])
        print("Ничего не изменено: .env не тронут.")
        return 1
    if mode == "SFTP":
        print("Подключено по SFTP (шифрованное соединение).")
        print(f"Отпечаток ключа сервера: {fingerprint}")
        print("Он запоминается: если ключ когда-нибудь изменится, утилита откажется подключаться и не отдаст пароль.")
    else:
        print(f"Подключено. Режим: {mode}" + (" (шифрованное соединение)" if mode == "FTPS" else
              " — ВНИМАНИЕ: сервер не поддерживает шифрование, пароль и файлы идут открытым текстом"))
    print("В начальной папке:")
    n = 0
    for e in top:
        if e["is_dir"]:
            n += 1
        print(f"   {str(n) + '.' if e['is_dir'] else '   '} {'папка' if e['is_dir'] else 'файл '}  {e['name']}")
    root = input("\nКакую папку отслеживать (номер из списка или название; Enter — начальную): ").strip()
    root = pick_folder(root, top) if root else ""
    if proto == "sftp":
        root = root if root.startswith("/") else (root.strip("/") or ".")
    else:
        root = "/" + root.strip("/") if root else "/"
    if root not in (".", "/"):
        try:
            session, _ = connect(dict(cfg, hostkey=fingerprint))
            n = len(list_dir(session, root))
            session.quit()
        except Exception as e:  # noqa: BLE001
            print(f"Папка «{root}» не открывается: {str(e).replace(password, '***')[:200]}")
            return 1
        print(f"Папка найдена, в ней {n} элементов.")
    if input("\nЗаписать в .env? [y/N] ").strip().lower() != "y":
        print("Ничего не изменено.")
        return 1
    if os.path.exists(ENV_PATH):
        shutil.copy2(ENV_PATH, ENV_PATH + ".bak_before_ftp")
    write_env(ENV_PATH, {"FTP_PROTO": proto, "FTP_HOST": host, "FTP_PORT": port, "FTP_USER": user, "FTP_PASSWORD": password,
                         "FTP_HOSTKEY": fingerprint, "FTP_TLS": "auto", "FTP_ROOT": root, "FTP_ENCODING": "utf-8"})
    ok, why = fix_env_permissions(ENV_PATH)
    if not ok:
        print("ВНИМАНИЕ:", why)
        shutil.copy2(ENV_PATH + ".bak_before_ftp", ENV_PATH)
        print("Прежний .env возвращён, ничего не применено.")
        return 1
    print("Записано в .env (копия прежнего — .env.bak_before_ftp). Приложение перезагружать не нужно.")
    print("Дальше: «ftp_watch.py ls» — посмотреть структуру, «ftp_watch.py check --dry» — пробная сводка.")
    return 0


UNIT_SERVICE = """[Unit]
Description=Слежение за папкой на FTP (сводка в Telegram)
After=network-online.target

[Service]
Type=oneshot
User=www-data
WorkingDirectory={root}
ExecStart={root}/venv/bin/python3 {root}/tools/ftp_watch.py check
"""
UNIT_TIMER = """[Unit]
Description=Слежение за FTP: 10:00 и 14:00 по Москве

[Timer]
OnCalendar=*-*-* 10,14:00:00 Europe/Moscow
Persistent=true

[Install]
WantedBy=timers.target
"""


def cmd_install(args):
    import subprocess
    if os.geteuid() != 0:
        print("Нужны права root.")
        return 1
    for name, body in (("ftp-watch.service", UNIT_SERVICE.format(root=ROOT)), ("ftp-watch.timer", UNIT_TIMER)):
        with open(f"/etc/systemd/system/{name}", "w", encoding="utf-8") as f:
            f.write(body)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "ftp-watch.timer"], check=True)
    print(subprocess.run(["systemctl", "list-timers", "ftp-watch.timer", "--no-pager"], capture_output=True, text=True).stdout)
    return 0


if __name__ == "__main__":
    cmd, rest = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    handler = {"setup": cmd_setup, "ls": cmd_ls, "check": cmd_check, "install": cmd_install, "relay": cmd_relay}.get(cmd)
    sys.exit(handler(rest) if handler else (print(__doc__) or 2))
