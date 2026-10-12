"""Раздел публикации: доступ, загрузка выпуска целиком, шаги отправки в Метафору.
Метафора подменена (monkeypatch) — сеть и ключ не используются."""
import io
import json

import pytest

import metafora_api
import metafora_check
import publish_import
import routes_publish_hub as hub
from models import User, db
from models_publish import PubArticle, PubAuthor, PubIssue

ARTICLE = {
    "doi": "10.18127/j20700970-202503-02", "udk": "004.89", "title_ru": "Тестовая статья", "title_en": "Test article",
    "pages": "5-13", "art_type": "RAR", "abstract_ru": "Аннотация", "abstract_en": "Abstract",
    "fulltext_ru": "Текст", "keywords_ru": ["слово"], "keywords_en": ["word"],
    "references": ["Иванов И.И. Основы теории. М.: Наука. 2010."], "references_en": ["Ivanov I.I. Osnovy teorii. M.: Nauka. 2010."],
    "citation_ru": "Иванов И.И. Тестовая статья // Нелинейный мир. 2025. Т. 23. № 3. С. 5–13.", "citation_en": "",
    "funding_ru": "", "funding_en": "", "dates": {"received": "12.05.2025", "approved": "", "accepted": "30.07.2025"},
    "authors": [{"surname_ru": "Иванов", "initials_ru": "И.И.", "surname_en": "Ivanov", "initials_en": "I.I.",
                 "org_ru": "МГУ", "org_en": "MSU", "town_ru": "Москва", "town_en": "Moscow", "position_ru": "доцент",
                 "position_en": "Associate Professor", "email": "ivanov@msu.ru", "orcid": "0000-0002-1825-0097", "spin": "1234-5678"}],
}


def _user(role):
    # Хэш задаём напрямую: set_password использует scrypt, которого нет в части сборок Python.
    u = User(username=f"u_{role}", display_name=role, role=role, password_hash="x")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, user):
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)
        s["_fresh"] = True


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_import, "IMPORT_ROOT", str(tmp_path / "import"))
    monkeypatch.setattr(publish_import, "ISSUE_PDF_ROOT", str(tmp_path / "pdfs"))
    monkeypatch.setattr(publish_import, "MANUSCRIPT_ROOT", str(tmp_path / "manuscripts"))
    monkeypatch.setattr(hub, "PUB_EXPORTS_FOLDER", str(tmp_path / "exports"))
    import routes_publish
    monkeypatch.setattr(routes_publish, "PUB_EXPORTS_FOLDER", str(tmp_path / "exports"))
    return tmp_path


@pytest.fixture()
def staff(app, client):
    u = _user("user")
    _login(client, u)
    return u


@pytest.fixture()
def issue(app, staff, dirs):
    iss = PubIssue(issn="2070-0970", year=2025, number="3", journal_name="Нелинейный мир", volume="23")
    db.session.add(iss)
    db.session.flush()
    publish_import.make_article(iss.id, ARTICLE, staff.id)
    db.session.commit()
    return iss


# ---------------------------------------------------------------- доступ

def test_staff_user_can_open_section(client, staff, dirs):
    assert client.get("/admin/publish").status_code == 200
    assert client.get("/admin/publish/import").status_code == 200


def test_home_button_link_has_no_host_or_port(client, staff, dirs):
    """Кнопка на главной не должна содержать адрес сервера: за nginx он получается без порта (был 502)."""
    import re
    html = client.get("/").get_data(as_text=True)
    m = re.search(r'<a href="([^"]+)" class="home-btn home-btn-secondary">', html)
    assert m and m.group(1) == "admin/publish"


def test_author_is_kept_out(app, client, dirs):
    _login(client, _user("author"))
    assert client.get("/admin/publish").status_code == 302
    # приложение уводит автора в его кабинет ещё до маршрута раздела (302) либо отвечает 403
    r = client.post("/admin/publish/issue/1/metafora/start", json={})
    assert r.status_code in (302, 403)


def test_anonymous_goes_to_login(client, dirs):
    r = client.get("/admin/publish")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_site_push_stays_admin_only(client, issue):
    r = client.post(f"/admin/publish/issue/{issue.id}/confirm")
    assert r.status_code == 302 and f"/issue/{issue.id}" not in r.headers["Location"]
    assert PubIssue.query.get(issue.id).pushed_at is None


# ---------------------------------------------------------------- реквизиты выпуска из DOI

def test_issue_identity_is_read_from_doi():
    assert publish_import.issue_from_doi("10.18127/j20700970-202503-10") == {"issn": "2070-0970", "year": 2025, "number": "3"}
    assert publish_import.issue_from_doi("https://doi.org/10.18127/j0033848X-202612-01")["issn"] == "0033-848X"
    assert publish_import.issue_from_doi("10.1000/other") is None


def test_detect_issue_takes_majority_and_reports_strangers():
    a = dict(ARTICLE, _file="a.doc")
    b = dict(ARTICLE, _file="b.doc", doi="10.18127/j20700970-202503-03")
    c = dict(ARTICLE, _file="c.doc", doi="10.18127/j20700970-202504-01")
    found, odd = publish_import.detect_issue([a, b, c])
    assert found == {"issn": "2070-0970", "year": 2025, "number": "3", "journal_name": "Нелинейный мир", "volume": "23"}
    assert odd == ["c.doc"]


# ---------------------------------------------------------------- загрузка выпуска

def _session_with_article(token):
    folder = publish_import.session_dir(token)
    import os
    os.makedirs(os.path.join(folder, "doc"), exist_ok=True)
    with open(os.path.join(folder, "doc", "02_Nm.doc.json"), "w", encoding="utf-8") as f:
        json.dump(dict(ARTICLE, _file="02_Nm.doc", _warnings=[]), f, ensure_ascii=False)


def test_import_creates_issue_with_articles_and_authors(client, staff, dirs):
    token = publish_import.new_token()
    _session_with_article(token)
    summary = client.get(f"/admin/publish/import/{token}/summary").get_json()
    assert summary["issue"]["number"] == "3" and summary["articles"] == 1 and summary["existing_url"] is None

    r = client.post(f"/admin/publish/import/{token}/create", json=summary["issue"])
    assert r.status_code == 200
    iss = PubIssue.query.one()
    art = PubArticle.query.one()
    author = PubAuthor.query.one()
    assert (iss.issn, iss.year, iss.number, iss.volume, iss.journal_name) == ("2070-0970", 2025, "3", "23", "Нелинейный мир")
    assert (art.doi, art.pages, art.date_accepted) == (ARTICLE["doi"], "5-13", "30.07.2025")
    assert art.references_en_list == ARTICLE["references_en"]
    assert (author.surname_en, author.orcid, author.spin, author.email) == ("Ivanov", "0000-0002-1825-0097", "1234-5678", "ivanov@msu.ru")
    assert r.get_json()["url"].endswith(f"/issue/{iss.id}")


def test_import_refuses_duplicate_issue(client, issue):
    token = publish_import.new_token()
    _session_with_article(token)
    r = client.post(f"/admin/publish/import/{token}/create",
                    json={"issn": "2070-0970", "year": 2025, "number": "3"})
    assert r.status_code == 409 and r.get_json()["existing_url"].endswith(f"/issue/{issue.id}")
    assert PubIssue.query.count() == 1


def test_bad_token_and_wrong_file_type_are_rejected(client, staff, dirs):
    r = client.post("/admin/publish/import/../../etc/doc", data={"file": (io.BytesIO(b"x"), "a.doc")})
    assert r.status_code in (400, 404)
    token = publish_import.new_token()
    r = client.post(f"/admin/publish/import/{token}/doc", data={"file": (io.BytesIO(b"x"), "evil.exe")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    r = client.post(f"/admin/publish/import/{'z' * 32}/doc", data={"file": (io.BytesIO(b"x"), "a.doc")},
                    content_type="multipart/form-data")
    assert r.status_code == 400


def test_uploaded_name_cannot_escape_the_folder():
    assert "/" not in publish_import._safe_name("../../etc/passwd.doc")
    assert publish_import._safe_name("C:\\\\dir\\\\07_Nm_Есиков.doc") == "07_Nm_Есиков.doc"


def test_delete_draft_issue(client, issue):
    r = client.post(f"/admin/publish/issue/{issue.id}/delete")
    assert r.status_code == 302 and PubIssue.query.count() == 0


def test_issue_sent_to_metafora_cannot_be_deleted(client, issue):
    hub.mf_save_state(issue.id, {"file_uid": "f1"})
    client.post(f"/admin/publish/issue/{issue.id}/delete")
    assert PubIssue.query.count() == 1


# ---------------------------------------------------------------- сверка без PDF

def test_check_reports_missing_pdf(client, issue):
    d = client.get(f"/admin/publish/issue/{issue.id}/check").get_json()
    assert d["ok"] is False and "нет PDF" in d["articles"][0]["errors"][0]


# ---------------------------------------------------------------- Метафора (подменена)

class FakeMetafora:
    def __init__(self, monkeypatch):
        self.existing_dois, self.signed, self.deleted, self.uploaded, self.signed_calls = set(), {}, [], [], []
        monkeypatch.setattr(metafora_api, "_request", self._request)
        monkeypatch.setattr(metafora_api, "upload_journal_xml", self._upload)
        monkeypatch.setattr(metafora_api, "publications_status", lambda uids: {u: self.signed.get(u) for u in uids})
        monkeypatch.setattr(metafora_api, "delete_file", lambda uid: self.deleted.append(uid))
        monkeypatch.setattr(metafora_api, "sign_publication", self._sign)

    def _request(self, method, path, **kw):
        doi = path.rsplit("/doi/", 1)[-1]
        if doi in self.existing_dois:
            return object()
        raise metafora_api.MetaforaError("не найдено", status=404)

    def _upload(self, xml, filename="x.xml"):
        self.uploaded.append(filename)
        return f"file-{len(self.uploaded)}"

    def _sign(self, uid):
        self.signed_calls.append(uid)
        self.signed[uid] = "2026-10-09T12:00:00+03:00"


@pytest.fixture()
def mf(monkeypatch):
    return FakeMetafora(monkeypatch)


def test_start_uploads_xml_and_remembers_file(client, issue, mf):
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 200 and r.get_json()["file_uid"] == "file-1"
    assert hub.mf_load_state(issue.id)["file_uid"] == "file-1"


def test_start_refuses_when_articles_already_in_metafora(client, issue, mf):
    """Выпуск, загруженный в Метафору другим путём, повторно не отправляется — были бы дубликаты."""
    mf.existing_dois.add(ARTICLE["doi"])
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 409 and r.get_json()["dois"] == [ARTICLE["doi"]]
    assert mf.uploaded == [] and not hub.mf_load_state(issue.id)


def test_start_twice_needs_explicit_replace(client, issue, mf):
    client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 409 and r.get_json()["already"] is True and mf.uploaded == [f"{issue.id}_metafora.xml"]
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={"replace": True})
    assert r.status_code == 200 and mf.deleted == ["file-1"] and hub.mf_load_state(issue.id)["file_uid"] == "file-2"


def test_replace_is_refused_when_something_is_signed(client, issue, mf):
    hub.mf_save_state(issue.id, {"file_uid": "f1", "articles": [{"uid": "a1", "doi": ARTICLE["doi"]}]})
    mf.signed["a1"] = "2026-10-09T12:00:00+03:00"
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={"replace": True})
    assert r.status_code == 409 and mf.deleted == [] and mf.uploaded == []


def test_start_refuses_invalid_xml(client, issue, mf):
    PubArticle.query.one().pages = ""
    db.session.commit()
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 422 and any("страниц" in p for p in r.get_json()["problems"]) and mf.uploaded == []


def test_sign_requires_clean_verification(client, issue, mf):
    """Подписать можно только публикацию, сверенную с PDF без ошибок."""
    state = {"file_uid": "f1", "status_code": 3, "articles": [{"uid": "a1", "doi": ARTICLE["doi"]}]}
    hub.mf_save_state(issue.id, state)
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/sign-one", json={"uid": "a1"})
    assert r.status_code == 409 and mf.signed_calls == []          # ещё не сверена

    state["articles"][0]["verify_errors"] = ["авторов 6, а в PDF их 7"]
    hub.mf_save_state(issue.id, state)
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/sign-one", json={"uid": "a1"})
    assert r.status_code == 409 and mf.signed_calls == []          # сверка нашла ошибки

    state["articles"][0]["verify_errors"] = []
    hub.mf_save_state(issue.id, state)
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/sign-one", json={"uid": "a1"})
    assert r.status_code == 200 and mf.signed_calls == ["a1"]
    assert hub.mf_load_state(issue.id)["articles"][0]["signed_at"]


def test_verify_marks_article_without_pdf_as_error(client, issue, mf, monkeypatch):
    hub.mf_save_state(issue.id, {"file_uid": "f1", "status_code": 3, "articles": [{"uid": "a1", "doi": ARTICLE["doi"]}]})
    monkeypatch.setattr(metafora_check, "article_from_api", lambda uid: {"doi": ARTICLE["doi"], "has_pdf": False, "signed_at": None})
    d = client.get(f"/admin/publish/issue/{issue.id}/metafora/verify?uid=a1").get_json()
    assert d["errors"] and hub.mf_load_state(issue.id)["articles"][0]["verify_errors"]


# ---------------------------------------------------------------- разделы из титульного PDF

def test_articles_get_sections_by_first_page(client, issue):
    arts = issue.articles
    arts[0].pages = "5-19"
    second = publish_import.make_article(issue.id, dict(ARTICLE, doi="10.18127/j20700970-202503-03", pages="20-30"), None)
    third = publish_import.make_article(issue.id, dict(ARTICLE, doi="10.18127/j20700970-202503-04", pages="41-53"), None)
    db.session.flush()
    toc = {"sections": [{"title_ru": "Первый раздел", "title_en": "First"}, {"title_ru": "Второй раздел", "title_en": ""}],
           "pages": [[5, 0], [20, 0], [41, 1]]}
    assert publish_import.assign_sections(issue, toc) == 3
    db.session.commit()
    assert [a.section_ru for a in issue.articles] == ["Первый раздел", "Первый раздел", "Второй раздел"]
    assert issue.articles[0].section_en == "First"
    assert [s.title_ru for s in issue.sections] == ["Первый раздел", "Второй раздел"]


def test_no_toc_means_no_sections(client, issue):
    assert publish_import.assign_sections(issue, None) == 0
    assert publish_import.assign_sections(issue, {"sections": [], "pages": []}) == 0
    assert issue.sections == []


def test_toc_maps_pages_to_the_heading_above_them():
    import pdf_parser
    text = ("Содержание\nПЕРВЫЙ РАЗДЕЛ\nАвтор А.А. Статья один\n5\nАвтор Б.Б. Статья два\n20\n"
            "ВТОРОЙ РАЗДЕЛ\nАвтор В.В. Статья три\n41\n")
    heads = ["ПЕРВЫЙ РАЗДЕЛ", "ВТОРОЙ РАЗДЕЛ"]
    assert pdf_parser._toc_page_sections(text, heads) == [[5, 0], [20, 0], [41, 1]]


# ---------------------------------------------------------------- .docx без внешних библиотек

def _make_docx(tmp_path, body_xml):
    import zipfile
    path = tmp_path / "a.docx"
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8"?><w:document {ns}><w:body>{body_xml}</w:body></w:document>')
    return str(path)


def test_docx_keeps_non_breaking_hyphen_in_footnote_ranges(tmp_path):
    """«1–4» с неразрывным дефисом Word читалось как «14», и организации не привязывались к авторам."""
    import doc_text
    body = ('<w:p><w:r><w:t>1</w:t></w:r><w:r><w:noBreakHyphen/></w:r><w:r><w:t>4 ВУНЦ ВВС (г. Воронеж, Россия)</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Первая</w:t></w:r><w:r><w:br/></w:r><w:r><w:t>вторая</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Почта </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> HYPERLINK "mailto:a@b.ru" </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>a@b.ru</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
    out = doc_text.extract_docx_text(_make_docx(tmp_path, body)).split("\n")
    assert out[0] == "1-4 ВУНЦ ВВС (г. Воронеж, Россия)"
    assert out[1] == "Первая" and out[2] == " вторая"
    assert out[3] == "Почта a@b.ru" and out[-1] == " "


def test_docx_that_is_not_word_is_rejected(tmp_path):
    import doc_text
    bad = tmp_path / "b.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(doc_text.DocFormatError):
        doc_text.extract_docx_text(str(bad))


def test_reference_check_tolerates_glyph_artifacts_in_pdf_text():
    """В слое текста PDF «Orbital» бывает прочитан как «Brbital» — это не ошибка данных."""
    import metafora_check as mc
    article = {"title": "T", "title_en": "T", "doi": "d", "udk": ["1"], "fpage": 1, "lpage": 1, "abstract": "", "abstract_en": "",
               "keywords": [], "keywords_en": [], "received": None, "accepted": None, "authors": [],
               "refs": ["Padgett M. Light's Orbital Angular Momentum [Электронный ресурс]. 2004. URL: https://x.ru/a.pdf (дата доступа 03.05.2026)."],
               "refs_en": [], "raw": ""}
    pdf = {"n": 1, "p1": "", "all": "Список источников\n1. Padgett M. Light's Brbital Angular Momentum [Электронный ресурс]. 2004. "
                                   "URL: https://x.ru/a.pdf (дата доступа 03.05.2026).\nИнформация об авторах"}
    errors, _ = mc.check_article(article, pdf)
    assert not [e for e in errors if "не совпали со списком" in e]


def test_creating_an_issue_does_not_inherit_pdfs_of_a_deleted_one(client, staff, dirs):
    import os
    stale = publish_import.issue_pdf_dir(1)
    os.makedirs(stale)
    open(os.path.join(stale, "old.pdf"), "wb").write(b"x")
    token = publish_import.new_token()
    _session_with_article(token)
    summary = client.get(f"/admin/publish/import/{token}/summary").get_json()
    client.post(f"/admin/publish/import/{token}/create", json=summary["issue"])
    assert PubIssue.query.one().id == 1 and publish_import.list_issue_pdfs(1) == []


# ---------------------------------------------------------------- действия на странице выпуска (без предпросмотра)

def test_old_preview_url_leads_to_the_issue_page(client, issue):
    r = client.get(f"/admin/publish/issue/{issue.id}/preview")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/admin/publish/issue/{issue.id}")


def test_metafora_xml_is_built_on_download_and_is_current(client, issue):
    r = client.get(f"/admin/publish/issue/{issue.id}/export/metafora")
    assert r.status_code == 200 and b"<journal>" in r.data and ARTICLE["title_ru"].encode() in r.data
    PubArticle.query.one().title_ru = "Новое название"
    db.session.commit()
    assert "Новое название".encode() in client.get(f"/admin/publish/issue/{issue.id}/export/metafora").data


def test_elibrary_export_is_a_zip_with_the_xml(client, issue):
    import io as _io
    import zipfile as _zip
    r = client.get(f"/admin/publish/issue/{issue.id}/export/elibrary")
    if r.status_code == 302:                      # у журнала нет titleid eLibrary — это сообщение, а не падение
        assert r.headers["Location"].endswith(f"/admin/publish/issue/{issue.id}")
        return
    assert r.mimetype == "application/zip"
    names = _zip.ZipFile(_io.BytesIO(r.data)).namelist()
    assert len(names) == 1 and names[0].endswith(".xml")


def test_crossref_export_before_site_push_explains_instead_of_failing(client, issue):
    r = client.get(f"/admin/publish/issue/{issue.id}/export/crossref")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/admin/publish/issue/{issue.id}")


def test_unknown_export_kind_is_404(client, issue):
    assert client.get(f"/admin/publish/issue/{issue.id}/export/other").status_code == 404


def test_site_check_is_read_only_json(client, issue, monkeypatch):
    import routes_publish
    monkeypatch.setattr(routes_publish.site_db, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("нет базы")))
    d = client.get(f"/admin/publish/issue/{issue.id}/site-check").get_json()
    assert d["production"] is False and d["pushed"] is False
    assert d["articles"][0]["doi"] == ARTICLE["doi"]
    assert any("Не удалось подключиться" in w for w in d["warnings"])
    assert PubIssue.query.one().pushed_at is None


def test_issue_page_has_all_destinations_and_no_preview_link(client, issue):
    html = client.get(f"/admin/publish/issue/{issue.id}").get_data(as_text=True)
    for label in ("Сайт radiotec.ru", "eLibrary", "CrossRef", "Метафора"):
        assert label in html
    assert "/preview" not in html and "generate-" not in html
