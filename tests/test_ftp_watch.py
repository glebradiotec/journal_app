"""Слежение за FTP: разбор листинга, обход, сравнение, сводка, запоминание снимка. FTP подменён."""
import ftplib
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ftp_watch as fw  # noqa: E402


class FakeFTP:
    """Папки и файлы в словаре: {'/путь': [(имя, 'dir'|'file', размер, 'YYYYMMDDHHMMSS')]}; mlsd можно «отключить»."""

    def __init__(self, tree, mlsd=True):
        self.tree, self.mlsd_ok, self.cwd_path, self.quit_called = tree, mlsd, "/", False

    def mlsd(self, path, facts=None):
        if not self.mlsd_ok:
            raise ftplib.error_perm("500 MLSD not understood")
        return [(n, {"type": k, "size": str(s), "modify": m}) for n, k, s, m in self.tree.get(path, [])]

    def cwd(self, path):
        self.cwd_path = path

    def retrlines(self, cmd, cb):
        for n, k, s, m in self.tree.get(self.cwd_path, []):
            mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(m[4:6]) - 1]
            when = f"{mon} {int(m[6:8]):2d} {m[8:10]}:{m[10:12]}"
            cb(f"{'d' if k == 'dir' else '-'}rwxr-xr-x 1 ftp ftp {s} {when} {n}")

    def quit(self):
        self.quit_called = True


def tree():
    return {
        "/журналы текущие": [("Радиотехника", "dir", 0, "20261010100000"), ("Успехи", "dir", 0, "20261010100000"), ("readme.txt", "file", 5, "20261001000000")],
        "/журналы текущие/Радиотехника": [("9", "dir", 0, "20261012100000"), ("10", "dir", 0, "20261018100000")],
        "/журналы текущие/Радиотехника/9": [("DOC", "dir", 0, "20261012100000"), ("PDF", "dir", 0, "20261012100000")],
        "/журналы текущие/Радиотехника/9/DOC": [("01_Иванов.doc", "file", 100, "20261012100000"), ("02 Петров и др.docx", "file", 200, "20261012100100")],
        "/журналы текущие/Радиотехника/9/PDF": [("01_Иванов.pdf", "file", 900, "20261012100200")],
        "/журналы текущие/Радиотехника/10": [("DOC", "dir", 0, "20261018100000")],
        "/журналы текущие/Радиотехника/10/DOC": [("01_Сидоров.doc", "file", 150, "20261018100000")],
        "/журналы текущие/Успехи": [("9", "dir", 0, "20261010100000")],
        "/журналы текущие/Успехи/9": [("a.doc", "file", 1, "20261010100000")],
    }


CFG = {"host": "h", "port": 21, "user": "u", "password": "p", "tls": "no", "root": "/журналы текущие", "encoding": "utf-8",
       "years_back": None}      # деревья в старых тестах — без папок-годов, поэтому без ограничения по годам


def test_parse_unix_and_dos_listing_lines():
    now = datetime(2026, 10, 19)
    e = fw.parse_list_line("-rw-r--r--   1 ftp  ftp     12345 Oct 12 10:30 02 Петров и др.docx", now)
    assert e == {"name": "02 Петров и др.docx", "is_dir": False, "size": 12345, "mtime": "20261012103000"}
    d = fw.parse_list_line("drwxr-xr-x   2 ftp  ftp      4096 Mar  3  2025 Радиотехника", now)
    assert d["is_dir"] and d["name"] == "Радиотехника" and d["mtime"].startswith("20250303")
    assert fw.parse_list_line("10-12-26  02:30PM       <DIR>          PDF")["is_dir"]
    f = fw.parse_list_line("10-12-26  02:30PM              2048 a b.pdf")
    assert (f["name"], f["size"], f["mtime"]) == ("a b.pdf", 2048, "20261012143000")
    assert fw.parse_list_line("total 12") is None


@pytest.mark.parametrize("mlsd", [True, False])
def test_scan_walks_the_whole_folder_with_mlsd_or_list(mlsd):
    snap = fw.scan(FakeFTP(tree(), mlsd), "/журналы текущие")
    assert set(snap["files"]) == {
        "readme.txt", "Радиотехника/9/DOC/01_Иванов.doc", "Радиотехника/9/DOC/02 Петров и др.docx",
        "Радиотехника/9/PDF/01_Иванов.pdf", "Радиотехника/10/DOC/01_Сидоров.doc", "Успехи/9/a.doc"}
    assert "Радиотехника/9/DOC" in snap["dirs"]


def test_scan_depth_limit():
    snap = fw.scan(FakeFTP(tree()), "/журналы текущие", max_depth=2)
    assert "Радиотехника/9/DOC/01_Иванов.doc" not in snap["files"] and "Радиотехника/9" in snap["dirs"]


def test_diff_new_changed_removed():
    old = {"files": {"a/1/x.doc": [1, "1"], "a/1/y.doc": [2, "1"], "a/1/z.doc": [3, "1"]}}
    new = {"files": {"a/1/x.doc": [1, "1"], "a/1/y.doc": [9, "1"], "a/2/n.pdf": [4, "1"]}}
    assert fw.diff(old, new) == {"added": ["a/2/n.pdf"], "changed": ["a/1/y.doc"], "removed": ["a/1/z.doc"]}


def test_latest_issue_per_journal_is_numeric_not_alphabetic():
    snap = fw.scan(FakeFTP(tree()), "/журналы текущие")
    latest = fw.latest_per_journal(snap)
    assert latest["Радиотехника"][0] == "№10"          # а не «№9»: по номеру, не по алфавиту
    assert latest["Успехи"][0] == "№9"


def test_summary_first_run_then_no_changes_then_new_issue():
    first = fw.scan(FakeFTP(tree()), "/журналы текущие")
    now = datetime(2026, 10, 19, 10, 0)
    text = fw.build_summary("журналы текущие", first, None, when=now)
    assert "Первая проверка" in text and "6 файлов" in text and "Радиотехника — №10" in text
    assert "Новых и изменённых файлов нет" in fw.build_summary("журналы текущие", first, first, when=now)

    t2 = tree()
    t2["/журналы текущие/Радиотехника"].append(("11", "dir", 0, "20261019090000"))
    t2["/журналы текущие/Радиотехника/11"] = [("01_Новый.doc", "file", 10, "20261019090000"), ("01_Новый.pdf", "file", 20, "20261019090100")]
    second = fw.scan(FakeFTP(t2), "/журналы текущие")
    text = fw.build_summary("журналы текущие", second, first, when=now)
    assert "<b>Радиотехника / 11</b>: +2 новых (doc 1, pdf 1)" in text
    assert "Радиотехника — №11" in text and "Что нового" in text


def test_summary_escapes_html_and_stays_within_telegram_limit():
    files = {f"Журнал <&> {i}/1/файл{i}.doc": [1, "20261019090000"] for i in range(600)}
    new = {"files": files, "dirs": []}
    text = fw.build_summary("папка", new, {"files": {}, "dirs": []}, when=datetime(2026, 10, 19, 14, 0))
    assert "<&>" not in text and len(text) <= fw.TELEGRAM_LIMIT + 40


def _factory(tr, mlsd=True):
    return lambda cfg: (FakeFTP(tr, mlsd), "FTP")


def test_dry_run_neither_sends_nor_remembers(tmp_path):
    snap = str(tmp_path / "snap.json")
    sent = []
    code, text = fw.run_check(dry=True, cfg=CFG, ftp_factory=_factory(tree()), snapshot_path=snap, send=lambda t: sent.append(t) or (True, ""))
    assert code == 0 and "Первая проверка" in text and not sent and not os.path.exists(snap)


class _PlainFTP:
    """Обычный FTP, который принимает вход; запоминает, чем и как подключались."""
    instances = []

    def __init__(self, *a, **k):
        self.logged = None
        _PlainFTP.instances.append(self)

    def connect(self, host, port):
        self.addr = (host, port)

    def login(self, user, password):
        self.logged = (user, password)


class _TLSRefuses(_PlainFTP):
    def auth(self):
        raise ftplib.error_perm("500 AUTH not understood")      # сервер не умеет шифрование


def test_connect_falls_back_to_plain_ftp_when_server_has_no_tls(monkeypatch):
    """Ровно то, что случилось в setup: отказ в шифровании не должен ронять подключение."""
    _PlainFTP.instances.clear()
    monkeypatch.setattr(fw.ftplib, "FTP_TLS", _TLSRefuses)
    monkeypatch.setattr(fw.ftplib, "FTP", _PlainFTP)
    ftp, mode = fw.connect(dict(CFG, tls="auto"))
    assert mode == "FTP" and ftp.logged == ("u", "p") and ftp.addr == ("h", 21)


def test_connect_with_tls_required_refuses_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(fw.ftplib, "FTP_TLS", _TLSRefuses)
    monkeypatch.setattr(fw.ftplib, "FTP", _PlainFTP)
    with pytest.raises(ftplib.error_perm):
        fw.connect(dict(CFG, tls="yes"))


def test_connect_uses_tls_when_the_server_supports_it(monkeypatch):
    class _TLSOk(_PlainFTP):
        def auth(self):
            pass

        def prot_p(self):
            pass
    monkeypatch.setattr(fw.ftplib, "FTP_TLS", _TLSOk)
    _, mode = fw.connect(dict(CFG, tls="auto"))
    assert mode == "FTPS"


def test_snapshot_is_saved_only_after_telegram_accepted_the_summary(tmp_path):
    snap = str(tmp_path / "snap.json")
    code, text = fw.run_check(cfg=CFG, ftp_factory=_factory(tree()), snapshot_path=snap, send=lambda t: (False, "нет сети"))
    assert code == 1 and "нет сети" in text and not os.path.exists(snap)      # новое не «теряется»: попадёт в следующую сводку
    code, _ = fw.run_check(cfg=CFG, ftp_factory=_factory(tree()), snapshot_path=snap, send=lambda t: (True, ""))
    assert code == 0 and fw.load_snapshot(snap)["files"]


def test_not_configured_is_reported(monkeypatch):
    cfg = fw.config({"FTP_HOST": "1.2.3.4", "FTP_USER": "u", "FTP_PASSWORD": "p", "FTP_ROOT": "/журналы текущие"})
    assert cfg["port"] == 21 and cfg["tls"] == "auto" and cfg["root"] == "/журналы текущие"
    assert fw.config({}) is None
    monkeypatch.setattr(fw, "config", lambda env=None: None)
    assert fw.run_check(cfg=None)[0] == 2                                   # не настроено — понятный отказ, не падение


def test_password_with_special_characters_survives_the_env_file(tmp_path):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    path.write_text("SECRET_KEY=abc\nFTP_USER=старый\n", encoding="utf-8")
    pw = 'p@ss "w#rd" \\ $x'
    fw.write_env(str(path), {"FTP_HOST": "1.2.3.4", "FTP_USER": "u", "FTP_PASSWORD": pw})
    vals = dotenv_values(str(path))
    assert vals["FTP_PASSWORD"] == pw and vals["FTP_USER"] == "u" and vals["SECRET_KEY"] == "abc"
    assert open(path, encoding="utf-8").read().count("FTP_USER=") == 1


# ---------------------------------------------------------------- SFTP

import stat as _stat  # noqa: E402
import sys as _sys  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402


class _FakeSftpClient:
    """listdir_attr по словарю {путь: [(имя, 'dir'|'file'|'link', размер, unix-время)]}."""

    def __init__(self, tree):
        self.tree, self.closed = tree, False

    def listdir_attr(self, path):
        modes = {"dir": _stat.S_IFDIR | 0o755, "file": _stat.S_IFREG | 0o644, "link": _stat.S_IFLNK | 0o777}
        return [NS(filename=n, st_mode=modes[k], st_size=sz, st_mtime=t) for n, k, sz, t in self.tree.get(path, [])]

    def stat(self, path):
        return NS(st_mode=_stat.S_IFDIR | 0o755)           # ссылка в тесте указывает на папку

    def close(self):
        self.closed = True


def _sftp_tree():
    t = 1760000000
    return {
        ".": [("журналы текущие", "dir", 0, t)],
        "./журналы текущие": [("Радиотехника", "dir", 0, t), ("ссылка", "link", 0, t), ("readme.txt", "file", 5, t)],
        "./журналы текущие/Радиотехника": [("9", "dir", 0, t)],
        "./журналы текущие/Радиотехника/9": [("01_Иванов.doc", "file", 100, t), ("01_Иванов.pdf", "file", 900, t + 60)],
        "./журналы текущие/ссылка": [("внутри.doc", "file", 1, t)],
    }


def test_sftp_session_lists_dirs_files_and_follows_links_and_scan_works_on_it():
    sess = fw.SftpSession(NS(close=lambda: None), _FakeSftpClient(_sftp_tree()))
    entries = {e["name"]: e for e in sess.list("./журналы текущие")}
    assert entries["Радиотехника"]["is_dir"] and not entries["readme.txt"]["is_dir"]
    assert entries["ссылка"]["is_dir"]                                   # ссылка на папку — как папка
    assert entries["readme.txt"]["size"] == 5 and len(entries["readme.txt"]["mtime"]) == 14
    snap = fw.scan(sess, "./журналы текущие")
    assert set(snap["files"]) == {"readme.txt", "Радиотехника/9/01_Иванов.doc", "Радиотехника/9/01_Иванов.pdf", "ссылка/внутри.doc"}


def test_sftp_config_defaults_port_and_root():
    cfg = fw.config({"FTP_HOST": "85.30.219.155", "FTP_PROTO": "SFTP", "FTP_USER": "u", "FTP_PASSWORD": "p"})
    assert (cfg["proto"], cfg["port"], cfg["root"]) == ("sftp", 22, ".")
    assert fw.config({"FTP_HOST": "h"})["port"] == 21 and fw.config({"FTP_HOST": "h"})["root"] == "/"


def _fake_paramiko(monkeypatch, key=b"server-key", log=None):
    log = log if log is not None else []

    class Transport:
        def __init__(self, sock):
            self.sock = sock

        def start_client(self, timeout=None):
            log.append("start_client")

        def get_remote_server_key(self):
            return NS(asbytes=lambda: key)

        def auth_password(self, user, password):
            log.append(("auth", user, password))

        def close(self):
            log.append("close")

    class SFTPClient:
        @staticmethod
        def from_transport(t):
            return _FakeSftpClient({})

    monkeypatch.setitem(_sys.modules, "paramiko", NS(Transport=Transport, SFTPClient=SFTPClient))
    monkeypatch.setattr(fw.socket, "create_connection", lambda addr, timeout=None: NS(settimeout=lambda t: None))
    return log


def test_sftp_connect_checks_the_server_key_before_sending_the_password(monkeypatch):
    log = _fake_paramiko(monkeypatch)
    cfg = dict(CFG, proto="sftp", port=22, hostkey="")
    sess, mode = fw.connect(cfg)
    assert mode == "SFTP" and sess.fingerprint.startswith("SHA256:") and ("auth", "u", "p") in log
    # ключ запомнен, и он тот же — пускаем
    sess2, _ = fw.connect(dict(cfg, hostkey=sess.fingerprint))
    assert sess2.fingerprint == sess.fingerprint
    # ключ изменился — пароль НЕ отправляется
    log.clear()
    with pytest.raises(fw.HostKeyChanged):
        fw.connect(dict(cfg, hostkey="SHA256:другой"))
    assert not any(isinstance(x, tuple) and x[0] == "auth" for x in log) and "close" in log


def test_summary_label_for_the_home_folder_is_not_a_dot(tmp_path):
    sftp_cfg = dict(CFG, proto="sftp", root=".")
    code, text = fw.run_check(dry=True, cfg=sftp_cfg, ftp_factory=lambda c: (fw.SftpSession(NS(close=lambda: None), _FakeSftpClient(_sftp_tree())), "SFTP"),
                              snapshot_path=str(tmp_path / "s.json"))
    assert code == 0 and "«корень»" in text and "«.»" not in text


def test_folder_is_found_by_number_or_by_name_without_regard_to_case():
    top = [{"name": "chat", "is_dir": True}, {"name": "АРХИВ", "is_dir": True}, {"name": "readme.txt", "is_dir": False},
           {"name": "Журналы текущие", "is_dir": True}, {"name": "Книги текущие", "is_dir": True}]
    assert fw.pick_folder("журналы текущие", top) == "Журналы текущие"       # на Linux регистр важен — находим настоящее имя
    assert fw.pick_folder("3", top) == "Журналы текущие" and fw.pick_folder("1", top) == "chat"
    assert fw.pick_folder("Журналы текущие/Радиотехника", top) == "Журналы текущие/Радиотехника"
    assert fw.pick_folder("нет такой", top) == "нет такой" and fw.pick_folder("9", top) == "9"


class _DeniedFTP(FakeFTP):
    def mlsd(self, path, facts=None):
        if "Успехи" in path:
            raise PermissionError(13, "Permission denied")
        return super().mlsd(path, facts)


def test_unreadable_folder_is_skipped_and_reported_not_a_crash():
    snap = fw.scan(_DeniedFTP(tree()), "/журналы текущие")
    assert snap["skipped"] == ["Успехи"] and "Успехи/9/a.doc" not in snap["files"]
    assert "Радиотехника/9/DOC/01_Иванов.doc" in snap["files"]                 # остальное прочитано
    text = fw.build_summary("журналы текущие", snap, None, when=datetime(2026, 10, 23, 10, 0))
    assert "Не прочитал папок (нет прав): 1" in text and "Успехи" in text
    # а если не открылась сама отслеживаемая папка — это ошибка, её не прячем
    class _Broken(FakeFTP):
        def mlsd(self, path, facts=None):
            raise PermissionError(13, "Permission denied")
    with pytest.raises(PermissionError):
        fw.scan(_Broken(tree()), "/журналы текущие")


# ---------------------------------------------------------------- настоящая структура: журнал / год / папка номера

def year_tree():
    t = "20261019090000"
    return {
        "/Журналы текущие": [("Радиотехника", "dir", 0, t), ("Электромагнитные волны", "dir", 0, t), ("1111111", "dir", 0, t),
                             ("#recycle", "dir", 0, t), ("._DAV", "dir", 0, t), (".DS_Store", "file", 6, t), ("Painter Presets.sqlrsr", "file", 9, t)],
        "/Журналы текущие/Радиотехника": [("2008", "dir", 0, t), ("2025", "dir", 0, t), ("2026", "dir", 0, t), ("font", "dir", 0, t)],
        "/Журналы текущие/Радиотехника/2008": [("01_2008", "dir", 0, t)],
        "/Журналы текущие/Радиотехника/2025": [("Rt_12-2025", "dir", 0, t)],
        "/Журналы текущие/Радиотехника/2025/Rt_12-2025": [("old.pdf", "file", 5, t)],
        "/Журналы текущие/Радиотехника/2026": [("Rt_09-2026", "dir", 0, t), ("Rt_10-2026", "dir", 0, t)],
        "/Журналы текущие/Радиотехника/2026/Rt_09-2026": [("макет", "dir", 0, t)],
        "/Журналы текущие/Радиотехника/2026/Rt_09-2026/макет": [("01_Иванов.doc", "file", 100, t), ("~$01_Иванов.doc", "file", 1, t), ("01_Иванов.pdf", "file", 900, t)],
        "/Журналы текущие/Радиотехника/2026/Rt_10-2026": [("02_Петров.docx", "file", 50, t)],
        "/Журналы текущие/Радиотехника/font": [("a.ttf", "file", 1, t)],
        "/Журналы текущие/Электромагнитные волны": [("2026", "dir", 0, t)],
        "/Журналы текущие/Электромагнитные волны/2026": [("Emw_04-2026", "dir", 0, t), ("02-03_2026", "dir", 0, t)],
        "/Журналы текущие/Электромагнитные волны/2026/Emw_04-2026": [("a.doc", "file", 1, t)],
        "/Журналы текущие/Электромагнитные волны/2026/02-03_2026": [("b.doc", "file", 1, t)],
    }


YEAR_CFG = dict(CFG, root="/Журналы текущие", years_back=1)


def test_issue_title_reads_number_and_year_from_the_folder_name():
    assert fw.issue_title("2026", "Emw_04-2026") == ("2026 №4", (4,))
    assert fw.issue_title("2008", "02-03_2008") == ("2008 №2-3", (2, 3))
    assert fw.issue_title("2025", "Emv_05-2025")[0] == "2025 №5"
    assert fw.issue_title("2026", "ЦНИРТИ_01_2026")[0] == "2026 №1"
    assert fw.issue_title("2026", "Особый выпуск")[0] == "2026 Особый выпуск"


def test_scan_of_the_real_layout_skips_junk_old_years_and_service_folders():
    prune = fw.default_prune(2025)
    snap = fw.scan(FakeFTP(year_tree()), "/Журналы текущие", prune=prune)
    assert set(snap["files"]) == {"Радиотехника/2025/Rt_12-2025/old.pdf", "Радиотехника/2026/Rt_09-2026/макет/01_Иванов.doc",
                                  "Радиотехника/2026/Rt_09-2026/макет/01_Иванов.pdf", "Радиотехника/2026/Rt_10-2026/02_Петров.docx",
                                  "Электромагнитные волны/2026/Emw_04-2026/a.doc", "Электромагнитные волны/2026/02-03_2026/b.doc"}
    assert not any("2008" in d or "font" in d or "recycle" in d or "._DAV" in d or "1111111" in d for d in snap["dirs"])
    assert not any("~$" in f or ".DS_Store" in f or "Painter" in f for f in snap["files"])


def test_summary_groups_by_journal_and_issue_and_latest_issue_is_by_year_then_number():
    snap = fw.scan(FakeFTP(year_tree()), "/Журналы текущие", prune=fw.default_prune(2025))
    assert fw._group("Радиотехника/2026/Rt_10-2026/02_Петров.docx") == ("Радиотехника", "2026 №10")
    latest = fw.latest_per_journal(snap)
    assert latest["Радиотехника"][0] == "2026 №10" and latest["Электромагнитные волны"][0] == "2026 №4"     # 2026 №10 > 2026 №9 > 2025 №12
    old = {"files": {k: v for k, v in snap["files"].items() if "Rt_10-2026" not in k}, "dirs": [], "skipped": []}
    text = fw.build_summary("Журналы текущие", snap, old, when=datetime(2026, 10, 23, 14, 0))
    assert "<b>Радиотехника / 2026 №10</b>: +1 новых (doc 1)" in text and "Радиотехника — 2026 №10" in text


def test_run_check_walks_only_the_current_and_previous_year_by_default(tmp_path):
    code, text = fw.run_check(dry=True, cfg=YEAR_CFG, ftp_factory=_factory(year_tree()), snapshot_path=str(tmp_path / "s.json"),
                              now=datetime(2026, 10, 23, 10, 0))
    assert code == 0 and "Первая проверка" in text and "6 файлов" in text            # 2008 и «font» не считаются
    code, text = fw.run_check(dry=True, cfg=dict(YEAR_CFG, years_back=0), ftp_factory=_factory(year_tree()),
                              snapshot_path=str(tmp_path / "s2.json"), now=datetime(2026, 10, 23, 10, 0))
    assert "5 файлов" in text                                                      # только 2026 (без old.pdf за 2025)
    assert fw.config({"FTP_HOST": "h", "FTP_YEARS_BACK": "all"})["years_back"] is None and fw.config({"FTP_HOST": "h"})["years_back"] == 1
