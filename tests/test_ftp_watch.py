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


CFG = {"host": "h", "port": 21, "user": "u", "password": "p", "tls": "no", "root": "/журналы текущие", "encoding": "utf-8"}


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
    assert latest["Радиотехника"][0] == "10"           # а не «9»: по номеру, не по алфавиту
    assert latest["Успехи"][0] == "9"


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
