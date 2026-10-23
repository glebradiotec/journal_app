"""Раздел «Файлы»: чтение файлового сервера (SFTP подменён), отбор файлов выпуска, защита путей, доступ, выдача файлов."""
import io
import stat as _stat
import time
import zipfile
from types import SimpleNamespace as NS

import pytest

import file_store as fs
from models import User, db

T = 1760000000
ROOT = "/Журналы текущие"
PDF_BYTES = b"%PDF-1.4 test"


class FakeClient:
    def __init__(self, tree, blobs):
        self.tree, self.blobs, self.listed, self.opened = tree, blobs, [], []

    def listdir_attr(self, path):
        if path not in self.tree:
            raise FileNotFoundError(path)
        self.listed.append(path)
        modes = {"dir": _stat.S_IFDIR | 0o755, "file": _stat.S_IFREG | 0o644}
        return [NS(filename=n, st_mode=modes[k], st_size=sz, st_mtime=t) for n, k, sz, t in self.tree[path]]

    def stat(self, path):
        if path in self.tree:
            return NS(st_mode=_stat.S_IFDIR | 0o755, st_size=0)
        if path in self.blobs:
            return NS(st_mode=_stat.S_IFREG | 0o644, st_size=len(self.blobs[path]))
        raise FileNotFoundError(path)

    def open(self, path, mode="rb"):
        self.opened.append(path)
        if path not in self.blobs:
            raise FileNotFoundError(path)
        f = io.BytesIO(self.blobs[path])
        f.prefetch = lambda: None
        return f

    def getfo(self, path, dst):
        dst.write(self.blobs[path])

    def close(self):
        pass


def make_tree():
    j = ROOT + "/Радиотехника"
    i = j + "/2026/8"
    tree = {
        ROOT: [("Радиотехника", "dir", 0, T), ("Rt_10", "dir", 0, T), ("Rt_9", "dir", 0, T), ("note.txt", "file", 3, T)],
        j: [("2026", "dir", 0, T)],
        j + "/2026": [("8", "dir", 0, T), ("~$tmp.doc", "file", 1, T)],
        i: [("doc", "dir", 0, T), ("pdf", "dir", 0, T), ("Рецензии", "dir", 0, T), ("#recycle", "dir", 0, T), ("cover.pdf", "file", 5, T)],
        i + "/doc": [("01_Иванов.doc", "file", 100, T), ("~$01_Иванов.doc", "file", 1, T), ("10_Петров.docx", "file", 100, T), ("2_Сидоров.docx", "file", 100, T)],
        i + "/pdf": [("01_Иванов.pdf", "file", 13, T)],
        i + "/Рецензии": [("рецензия.doc", "file", 50, T)],
        i + "/#recycle": [("x.doc", "file", 1, T)],
    }
    blobs = {i + "/pdf/01_Иванов.pdf": PDF_BYTES, i + "/doc/01_Иванов.doc": b"DOC", ROOT + "/note.txt": b"abc",
             i + "/cover.pdf": b"%PDF-cover"}
    return tree, blobs


class FakeSession(fs.fw.SftpSession):
    """Настоящая SftpSession (список папок, как на сервере) поверх фейкового клиента."""

    def __init__(self, client):
        super().__init__(NS(is_active=lambda: True, close=lambda: None), client)


@pytest.fixture()
def sftp(monkeypatch):
    tree, blobs = make_tree()
    client = FakeClient(tree, blobs)
    client.connects = []

    def connect(cfg):
        client.connects.append(1)
        return FakeSession(client), "SFTP"

    monkeypatch.setattr(fs.fw, "config", lambda env=None: {"proto": "sftp", "root": ROOT})
    monkeypatch.setattr(fs.fw, "connect", connect)
    fs.reset()
    yield client
    fs.reset()


# ---------------------------------------------------------------- пути

@pytest.mark.parametrize("bad", ["../etc", "a/../../b", "a\\..\\b", "x\x00y"])
def test_clean_rel_rejects_traversal(bad):
    with pytest.raises(fs.BadPath):
        fs.clean_rel(bad)


def test_clean_rel_normalizes():
    assert fs.clean_rel("/a//b/./c/") == "a/b/c" and fs.clean_rel("") == "" and fs.clean_rel("a\\b") == "a/b"


def test_is_issue_folder():
    assert fs.is_issue_folder("Радиотехника/2026/8")
    assert not fs.is_issue_folder("Радиотехника/2026") and not fs.is_issue_folder("Радиотехника/2026/8/doc")
    assert not fs.is_issue_folder("Радиотехника/архив/8")


def test_recent():
    now = __import__("datetime").datetime(2026, 10, 20, tzinfo=__import__("datetime").timezone.utc)
    assert fs.recent("20261019000000", now) and not fs.recent("20261001000000", now) and not fs.recent("", now)


# ---------------------------------------------------------------- список папок

def test_list_folder_sorts_naturally_dirs_first_and_hides_junk(sftp):
    names = [e["name"] for e in fs.list_folder("")]
    assert names == ["Rt_9", "Rt_10", "Радиотехника", "note.txt"]
    assert "#recycle" not in [e["name"] for e in fs.list_folder("Радиотехника/2026/8")]
    assert "~$tmp.doc" not in [e["name"] for e in fs.list_folder("Радиотехника/2026")]


def test_list_folder_is_cached_and_refresh_rereads(sftp):
    fs.list_folder("")
    fs.list_folder("")
    assert sftp.listed.count(ROOT) == 1
    fs.list_folder("", refresh=True)
    assert sftp.listed.count(ROOT) == 2


def test_list_folder_missing_is_friendly_error(sftp):
    with pytest.raises(fs.FileStoreError) as e:
        fs.list_folder("нет/такой")
    assert "не найден" in str(e.value)


def test_reconnects_once_after_dead_connection(sftp, monkeypatch):
    fs.list_folder("")
    fs._pool["session"].transport.is_active = lambda: False
    fs.list_folder("Радиотехника", refresh=True)
    assert len(sftp.connects) == 2


def test_not_configured(monkeypatch):
    monkeypatch.setattr(fs.fw, "config", lambda env=None: None)
    fs.reset()
    assert not fs.configured()
    with pytest.raises(fs.NotConfigured):
        fs.list_folder("")


# ---------------------------------------------------------------- файлы выпуска

def test_pick_issue_files_prefers_doc_and_pdf_subfolders_and_skips_temp():
    files = [("j/2026/8/doc/01.doc", 1), ("j/2026/8/doc/~$01.doc", 1), ("j/2026/8/pdf/01.pdf", 1), ("j/2026/8/cover.pdf", 1),
             ("j/2026/8/Рецензии/r.doc", 1), ("j/2026/8/doc/10.docx", 1), ("j/2026/8/doc/2.docx", 1), ("j/2026/8/.hidden.doc", 1),
             ("j/2026/8/doc/readme.txt", 1)]
    got = fs.pick_issue_files(files)
    assert [f["path"] for f in got] == ["j/2026/8/doc/01.doc", "j/2026/8/doc/2.docx", "j/2026/8/doc/10.docx", "j/2026/8/pdf/01.pdf"]
    assert [f["kind"] for f in got] == ["doc", "doc", "doc", "pdf"]


def test_pick_issue_files_without_subfolders_takes_everything_suitable():
    got = fs.pick_issue_files([("a/1.doc", 1), ("a/1.pdf", 1), ("a/x.zip", 1)])
    assert [f["name"] for f in got] == ["1.doc", "1.pdf"]


def test_issue_files_walks_the_folder(sftp):
    got = fs.issue_files("Радиотехника/2026/8")
    assert [f["name"] for f in got] == ["01_Иванов.doc", "2_Сидоров.docx", "10_Петров.docx", "01_Иванов.pdf"]


def test_fetch_to_storage_saves_file_and_checks_type(sftp, tmp_path):
    st = fs.fetch_to_storage("Радиотехника/2026/8/pdf/01_Иванов.pdf")
    assert st.filename == "01_Иванов.pdf"
    st.save(str(tmp_path / "x.pdf"))
    assert (tmp_path / "x.pdf").read_bytes() == PDF_BYTES
    with pytest.raises(fs.BadPath):
        fs.fetch_to_storage("note.txt")
    with pytest.raises(fs.BadPath):
        fs.fetch_to_storage("../secret.pdf")


# ---------------------------------------------------------------- архив

def test_make_zip_contains_folder_files(sftp):
    path, name = fs.make_zip("Радиотехника/2026/8/pdf")
    try:
        assert name == "pdf.zip"
        with zipfile.ZipFile(path) as z:
            assert z.namelist() == ["pdf/01_Иванов.pdf"] and z.read("pdf/01_Иванов.pdf") == PDF_BYTES
    finally:
        __import__("os").remove(path)


def test_make_zip_limits_and_root(sftp):
    with pytest.raises(fs.TooBig):
        fs.make_zip("Радиотехника/2026/8", max_files=2)
    with pytest.raises(fs.TooBig):
        fs.make_zip("Радиотехника/2026/8", max_bytes=10)
    with pytest.raises(fs.TooBig):
        fs.make_zip("")


def test_stream_file(sftp):
    assert b"".join(fs.stream_file("Радиотехника/2026/8/pdf/01_Иванов.pdf", chunk=4)) == PDF_BYTES
    with pytest.raises(FileNotFoundError):
        b"".join(fs.stream_file("нет.pdf"))


# ---------------------------------------------------------------- страницы

def _login(client, role):
    u = User(username="u_" + role, display_name=role, role=role, password_hash="x")   # set_password тянет scrypt
    db.session.add(u)
    db.session.commit()
    with client.session_transaction() as s:
        s["_user_id"] = str(u.id)
        s["_fresh"] = True


def test_files_requires_login_and_hides_from_authors(app, client, sftp):
    r = client.get("/files/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    _login(client, "author")                              # автора приложение уводит в его кабинет
    for url in ("/files/", "/files/raw/note.txt", "/files/zip/Радиотехника", "/files/api/issue/Радиотехника/2026/8"):
        assert client.get(url).status_code in (302, 403)


def test_browse_pages(app, client, sftp):
    _login(client, "user")
    r = client.get("/files/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Радиотехника" in html and "Загрузить выпуск" not in html
    r = client.get("/files/Радиотехника/2026/8")
    html = r.get_data(as_text=True)
    assert "Загрузить выпуск в раздел" in html and "from=" in html and "новое" not in html  # файлам 2025 год — не новые
    assert "01_Иванов" not in html and "cover.pdf" in html
    assert "Скачать папку архивом" in client.get("/files/Радиотехника/2026").get_data(as_text=True)
    assert "Загрузить выпуск" not in client.get("/files/Радиотехника/2026").get_data(as_text=True)


def test_browse_errors_are_shown_not_500(app, client, sftp):
    _login(client, "user")
    r = client.get("/files/нет/такой")
    assert r.status_code == 200 and "не найден" in r.get_data(as_text=True)
    assert client.get("/files/%2e%2e/x").status_code in (200, 404)


def test_raw_pdf_inline_and_download_and_other_types_attach(app, client, sftp):
    _login(client, "user")
    r = client.get("/files/raw/Радиотехника/2026/8/pdf/01_Иванов.pdf")
    assert r.status_code == 200 and r.data == PDF_BYTES and r.mimetype == "application/pdf"
    assert r.headers["Content-Disposition"].startswith("inline") and r.headers["Content-Length"] == str(len(PDF_BYTES))
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = client.get("/files/raw/Радиотехника/2026/8/pdf/01_Иванов.pdf?download=1")
    assert r.headers["Content-Disposition"].startswith("attachment") and r.data == PDF_BYTES   # .data дочитывает поток и снимает контекст
    r = client.get("/files/raw/note.txt")
    assert r.data == b"abc" and r.headers["Content-Disposition"].startswith("attachment") and r.mimetype == "application/octet-stream"
    assert client.get("/files/raw/Радиотехника").status_code == 404           # папка
    assert client.get("/files/raw/нет.pdf").status_code == 404


def test_zip_route_and_too_big(app, client, sftp, monkeypatch):
    _login(client, "user")
    made = []
    real = fs.make_zip
    monkeypatch.setattr(fs, "make_zip", lambda rel: made.append(real(rel)) or made[-1])
    r = client.get("/files/zip/Радиотехника/2026/8/pdf")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(r.data)).namelist() == ["pdf/01_Иванов.pdf"]
    assert not __import__("os").path.exists(made[0][0])                       # временный архив удалён после выдачи


def test_api_issue_lists_files(app, client, sftp):
    _login(client, "user")
    r = client.get("/files/api/issue/Радиотехника/2026/8")
    assert r.status_code == 200 and [f["name"] for f in r.get_json()["files"]][-1] == "01_Иванов.pdf"


def test_import_endpoints_accept_sftp_path(app, client, sftp, monkeypatch):
    import publish_import
    _login(client, "user")
    seen = {}
    monkeypatch.setattr(publish_import, "add_pdf", lambda folder, f: seen.update(name=f.filename) or {"toc": {"sections": 1, "warnings": []}})
    monkeypatch.setattr(publish_import, "session_pdf_dir", lambda token: "/tmp/x")
    r = client.post("/admin/publish/import/tok/pdf", data={"sftp_path": "Радиотехника/2026/8/pdf/01_Иванов.pdf"})
    assert r.status_code == 200 and seen["name"] == "01_Иванов.pdf"
    r = client.post("/admin/publish/import/tok/pdf", data={"sftp_path": "../../etc/passwd.pdf"})
    assert r.status_code == 400 and "error" in r.get_json()
    r = client.post("/admin/publish/import/tok/pdf", data={})
    assert r.status_code == 400
