"""Раздел публикации: доступ, загрузка выпуска целиком, шаги отправки в Метафору.
Метафора подменена (monkeypatch) — сеть и ключ не используются."""
import io
import re
from datetime import datetime, timezone
import os
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


def test_summary_tells_whether_existing_issue_can_be_replaced(client, issue):
    token = publish_import.new_token()
    _session_with_article(token)
    ex = client.get(f"/admin/publish/import/{token}/summary").get_json()["existing"]
    assert ex == {"articles": 1, "blocked": ""}
    hub.mf_save_state(issue.id, {"file_uid": "f1"})
    ex = client.get(f"/admin/publish/import/{token}/summary").get_json()["existing"]
    assert "Метафору" in ex["blocked"]


def test_replace_swaps_the_draft_for_the_new_upload(client, issue, dirs):
    old_id = issue.id
    pdf_dir = publish_import.issue_pdf_dir(old_id)
    os.makedirs(pdf_dir)
    open(os.path.join(pdf_dir, "old.pdf"), "wb").write(b"%PDF-old")
    token = publish_import.new_token()
    _session_with_article(token)
    os.makedirs(publish_import.session_pdf_dir(token), exist_ok=True)
    open(os.path.join(publish_import.session_pdf_dir(token), "new.pdf"), "wb").write(b"%PDF-new")

    r = client.post(f"/admin/publish/import/{token}/create",
                    json={"issn": "2070-0970", "year": 2025, "number": "3", "replace": True})
    assert r.status_code == 200
    iss = PubIssue.query.one()
    assert PubArticle.query.count() == 1
    names = os.listdir(publish_import.issue_pdf_dir(iss.id))
    assert names == ["new.pdf"]                      # PDF старого черновика не достались новому
    assert iss.id == old_id or not os.path.exists(pdf_dir)


@pytest.mark.parametrize("blocker", ["site", "metafora"])
def test_replace_is_refused_for_issue_already_sent(client, issue, blocker):
    if blocker == "site":
        issue.pushed_at = datetime.now(timezone.utc)
        db.session.commit()
    else:
        hub.mf_save_state(issue.id, {"file_uid": "f1"})
    token = publish_import.new_token()
    _session_with_article(token)
    r = client.post(f"/admin/publish/import/{token}/create",
                    json={"issn": "2070-0970", "year": 2025, "number": "3", "replace": True})
    assert r.status_code == 400 and "заменить его нельзя" in r.get_json()["error"]
    assert PubIssue.query.one().id == issue.id and PubArticle.query.count() == 1


def test_failed_replace_keeps_the_old_draft(client, issue, monkeypatch):
    token = publish_import.new_token()
    _session_with_article(token)
    monkeypatch.setattr(publish_import, "make_article", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("сбой")))
    with pytest.raises(RuntimeError):
        client.post(f"/admin/publish/import/{token}/create",
                    json={"issn": "2070-0970", "year": 2025, "number": "3", "replace": True})
    assert PubIssue.query.count() == 1 and PubArticle.query.count() == 1


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


def test_csrf_token_does_not_expire_after_an_hour(app):
    # Страница раздела стоит открытой часами; токен с часовым сроком ронял кнопки «CSRF token has expired».
    assert app.config["WTF_CSRF_TIME_LIMIT"] is None


def test_bad_csrf_token_returns_to_the_page_with_a_message(app, client, issue):
    app.config["WTF_CSRF_ENABLED"] = True
    url = f"/admin/publish/issue/{issue.id}"
    r = client.post(url + "/delete", headers={"Referer": url}, data={"csrf_token": "stale"})
    assert r.status_code == 302 and r.headers["Location"].endswith(url)
    assert PubIssue.query.count() == 1
    with client.session_transaction() as s:
        assert "устарела" in dict(s["_flashes"] and [tuple(f) for f in s["_flashes"]])["error"]


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
        self.existing, self.signed, self.deleted, self.uploaded, self.signed_calls = {}, {}, [], [], []
        monkeypatch.setattr(metafora_api, "file_status", lambda uid: {
            "code": 3, "text": "обработан", "raw": {},
            "articles": [h["article_uid"] for h in self.existing.values() if h["file_uid"] == uid]})
        monkeypatch.setattr(metafora_api, "_request", self._request)
        monkeypatch.setattr(metafora_api, "upload_journal_xml", self._upload)
        monkeypatch.setattr(metafora_api, "publications_status", lambda uids: {u: self.signed.get(u) for u in uids})
        monkeypatch.setattr(metafora_api, "delete_file", lambda uid: self.deleted.append(uid))
        monkeypatch.setattr(metafora_api, "sign_publication", self._sign)

    def _request(self, method, path, **kw):
        doi = path.rsplit("/doi/", 1)[-1]
        if doi in self.existing:
            hit = self.existing[doi]
            return type("R", (), {"json": lambda _self: {"message": "Success", "data": hit}})()
        raise metafora_api.MetaforaError("не найдено", status=404)

    def add_existing(self, doi, file_uid="ext-1", signed_at=None, has_pdf=True):
        self.existing[doi] = {"article_uid": f"uid-{doi[-3:]}", "has_pdf": has_pdf, "signed_at": signed_at,
                              "file_uid": file_uid, "created_at": "2026-10-09T12:25:18.000000Z"}

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


def test_start_adopts_issue_already_in_metafora(client, issue, mf):
    """Выпуск, загруженный в Метафору другим путём, не отправляется повторно (были бы дубликаты): подхватывается."""
    mf.add_existing(ARTICLE["doi"], signed_at="2026-10-09T12:32:23Z")
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 200 and r.get_json() == {"file_uid": "ext-1", "linked": True}
    state = hub.mf_load_state(issue.id)
    assert mf.uploaded == [] and state["file_uid"] == "ext-1" and state["status_code"] == 3
    assert state["articles"][0]["has_pdf"] is True and state["articles"][0]["signed_at"]


def _second_article(issue, staff):
    publish_import.make_article(issue.id, dict(ARTICLE, doi=ARTICLE["doi"][:-2] + "99"), staff.id)
    db.session.commit()
    return ARTICLE["doi"][:-2] + "99"


def test_start_refuses_when_only_part_of_the_issue_is_in_metafora(client, issue, staff, mf):
    _second_article(issue, staff)
    mf.add_existing(ARTICLE["doi"])
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 409 and "1 из 2" in r.get_json()["error"]
    assert mf.uploaded == [] and not hub.mf_load_state(issue.id)


def test_start_refuses_when_issue_is_split_between_files(client, issue, staff, mf):
    second = _second_article(issue, staff)
    mf.add_existing(ARTICLE["doi"], file_uid="ext-1")
    mf.add_existing(second, file_uid="ext-2")
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    assert r.status_code == 409 and "разных файлах" in r.get_json()["error"] and mf.uploaded == []


def test_link_picks_up_issue_uploaded_elsewhere(client, issue, mf):
    mf.add_existing(ARTICLE["doi"], signed_at="2026-10-09T12:32:23Z")
    assert client.post(f"/admin/publish/issue/{issue.id}/metafora/link").get_json() == {"linked": True, "articles": 1}
    st = hub.mf_load_state(issue.id)
    assert st["file_uid"] == "ext-1" and st["linked"] and st["articles"][0]["doi"] == ARTICLE["doi"]
    assert hub.issue_stage(issue)["mf_sent"] and hub.issue_stage(issue)["mf_signed"] == 1


def test_index_offers_metafora_sync_only_for_unsent_issues(client, issue, monkeypatch):
    monkeypatch.setenv("METAFORA_API_KEY", "k")
    html = client.get("/admin/publish").get_data(as_text=True)
    assert 'id="mfSyncBtn"' in html and f"[{issue.id}]" in html
    hub.mf_save_state(issue.id, {"file_uid": "f1"})
    assert 'id="mfSyncBtn"' not in client.get("/admin/publish").get_data(as_text=True)


def test_hidden_site_journals_are_not_listed_unless_they_have_drafts(client, issue, monkeypatch):
    import routes_publish
    site = [{"journ_id": 2, "name": "Радиосистемы", "issn": "0869-7220", "titleid": ""},
            {"journ_id": 10, "name": "Экология - XXI век", "issn": "", "titleid": ""},
            {"journ_id": 3, "name": "Антенны", "issn": "0320-9601", "titleid": ""},
            {"journ_id": 20, "name": "Нелинейный мир", "issn": issue.issn, "titleid": ""}]  # скрытый номер, но выпуск есть
    monkeypatch.setattr(routes_publish, "_safe_list_journals", lambda: (site, None))
    html = client.get("/admin/publish").get_data(as_text=True)
    assert "Антенны" in html and "Нелинейный мир" in html
    assert "Радиосистемы" not in html and "Экология" not in html


def test_link_does_nothing_when_issue_is_not_in_metafora_or_already_known(client, issue, mf):
    assert client.post(f"/admin/publish/issue/{issue.id}/metafora/link").get_json()["linked"] is False
    assert not hub.mf_load_state(issue.id)
    mf.add_existing(ARTICLE["doi"])
    hub.mf_save_state(issue.id, {"file_uid": "mine"})
    client.post(f"/admin/publish/issue/{issue.id}/metafora/link")
    assert hub.mf_load_state(issue.id) == {"file_uid": "mine"}


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


@pytest.fixture()
def pdf_mismatch(monkeypatch):
    """Сверка находит PDF статьи и одно расхождение; сами PDF не нужны."""
    state = {"errors": ["англ. ссылок 7, по нумерации PDF 6"]}
    monkeypatch.setattr(metafora_check, "load_pdfs", lambda folder: {"a.pdf": {"p1": "", "all": "", "n": 1}})
    monkeypatch.setattr(metafora_check, "find_pdf", lambda pdfs, doi: ["a.pdf"])
    monkeypatch.setattr(metafora_check, "check_article", lambda a, P: (list(state["errors"]), []))
    return state


def _row(client, issue):
    return client.get(f"/admin/publish/issue/{issue.id}/check").get_json()


def test_accepting_a_pdf_mismatch_unblocks_check_and_can_be_revoked(client, issue, pdf_mismatch):
    art = PubArticle.query.one()
    assert _row(client, issue)["articles"][0]["errors"] == pdf_mismatch["errors"]
    r = client.post(f"/admin/publish/issue/{issue.id}/check/accept", json={"article_id": art.id})
    assert r.status_code == 200 and r.get_json()["accepted"] is True
    row = _row(client, issue)["articles"][0]
    assert row["errors"] == [] and row["accepted"] is True and any("принято вручную" in w for w in row["warnings"])
    client.post(f"/admin/publish/issue/{issue.id}/check/accept", json={"article_id": art.id, "revoke": True})
    assert _row(client, issue)["articles"][0]["errors"] == pdf_mismatch["errors"]


def test_accepted_mismatch_does_not_cover_a_new_error(client, issue, pdf_mismatch):
    art = PubArticle.query.one()
    client.post(f"/admin/publish/issue/{issue.id}/check/accept", json={"article_id": art.id})
    pdf_mismatch["errors"] = ["англ. ссылок 7, по нумерации PDF 6", "нет УДК"]
    assert _row(client, issue)["articles"][0]["errors"] == ["нет УДК"]
    pdf_mismatch["errors"] = ["англ. ссылок 8, по нумерации PDF 6"]   # текст изменился — принятие не действует
    assert _row(client, issue)["articles"][0]["errors"] == pdf_mismatch["errors"]


def test_nothing_to_accept_without_mismatch(client, issue, pdf_mismatch):
    pdf_mismatch["errors"] = []
    r = client.post(f"/admin/publish/issue/{issue.id}/check/accept", json={"article_id": PubArticle.query.one().id})
    assert r.status_code == 409


def test_remove_deletes_file_and_forgets_state(client, issue, mf):
    client.post(f"/admin/publish/issue/{issue.id}/metafora/start", json={})
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/remove")
    assert r.status_code == 200 and mf.deleted == ["file-1"] and hub.mf_load_state(issue.id) == {}
    assert publish_import.replace_blocker(issue) == ""   # теперь черновик можно заменить новой загрузкой


def test_remove_button_shown_only_while_nothing_is_signed(client, issue, mf):
    hub.mf_save_state(issue.id, {"file_uid": "f1", "status_code": 3, "articles": [{"uid": "a1", "doi": ARTICLE["doi"]}]})
    assert 'id="mfRemove"' in client.get(f"/admin/publish/issue/{issue.id}").get_data(as_text=True)
    hub.mf_save_state(issue.id, {"file_uid": "f1", "status_code": 3,
                                 "articles": [{"uid": "a1", "doi": ARTICLE["doi"], "signed_at": "2026-10-09T12:00:00+03:00"}]})
    assert 'id="mfRemove"' not in client.get(f"/admin/publish/issue/{issue.id}").get_data(as_text=True)


def test_remove_is_refused_when_something_is_signed(client, issue, mf):
    hub.mf_save_state(issue.id, {"file_uid": "f1", "articles": [{"uid": "a1", "doi": ARTICLE["doi"]}]})
    mf.signed["a1"] = "2026-10-09T12:00:00+03:00"
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/remove")
    assert r.status_code == 409 and mf.deleted == [] and hub.mf_load_state(issue.id)["file_uid"] == "f1"


def test_remove_checks_signature_by_metafora_not_only_by_saved_state(client, issue, mf):
    """В сохранённом состоянии статей может не быть (файл ещё не обработан при записи) — подпись смотрим в Метафоре."""
    mf.add_existing(ARTICLE["doi"], file_uid="f1", signed_at="2026-10-09T12:00:00+03:00")
    mf.signed["uid-" + ARTICLE["doi"][-3:]] = "2026-10-09T12:00:00+03:00"
    hub.mf_save_state(issue.id, {"file_uid": "f1", "articles": []})
    r = client.post(f"/admin/publish/issue/{issue.id}/metafora/remove")
    assert r.status_code == 409 and mf.deleted == []


def test_remove_without_sending_is_404(client, issue, mf):
    assert client.post(f"/admin/publish/issue/{issue.id}/metafora/remove").status_code == 404


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


def test_keywords_with_commas_in_brackets_survive_storage():
    art = PubArticle(keywords_ru="протоколы маршрутизации (EGP, IGP), гибкий алгоритм", keywords_en="a (b, c); d")
    assert art.keywords_ru_list == ["протоколы маршрутизации (EGP, IGP)", "гибкий алгоритм"]
    assert art.keywords_en_list == ["a (b, c)", "d"]


def test_uneven_keyword_counts_are_flagged_and_block_only_elibrary(client, issue):
    art = PubArticle.query.one()
    art.keywords_ru, art.keywords_en = "один, два", "one, two, three"
    db.session.commit()
    row = client.get(f"/admin/publish/issue/{issue.id}/check").get_json()["articles"][0]
    assert any("русских 2, английских 3" in w for w in row["warnings"])
    r = client.get(f"/admin/publish/issue/{issue.id}/export/elibrary")
    assert r.status_code == 302 and "zip" not in (r.headers.get("Content-Type") or "")
    with client.session_transaction() as s:
        assert "ключевых слов не поровну" in dict((c, m) for c, m in s["_flashes"])["error"]
    page = client.get(f"/admin/publish/issue/{issue.id}").get_data(as_text=True)
    assert "Ключевых слов не поровну" in page and "русских 2, английских 3" in page           # список остаётся на странице
    assert f"/article/{art.id}" in page.split("Ключевых слов не поровну", 1)[1].split("</ul>", 1)[0]
    assert client.get(f"/admin/publish/issue/{issue.id}/export/metafora").status_code == 200   # Метафоре поровну не нужно
    art.keywords_en = "one, two"
    db.session.commit()
    assert client.get(f"/admin/publish/issue/{issue.id}/export/elibrary").status_code == 200


def test_exports_equalize_keywords_of_drafts_saved_before_the_fix(client, issue):
    """Черновик с уже сохранёнными «machine learning, ML» выгружается в eLibrary без блокировки и с вклеенной аббревиатурой."""
    import io
    import zipfile
    art = PubArticle.query.one()
    art.keywords_ru, art.keywords_en = "машинное обучение, глубокое обучение", "machine learning, ML, deep learning, DL"
    db.session.commit()
    assert art.keywords_pair == (["машинное обучение", "глубокое обучение"], ["machine learning (ML)", "deep learning (DL)"])
    assert art.keywords_en_list == ["machine learning", "ML", "deep learning", "DL"]        # на сайт уходит как есть
    r = client.get(f"/admin/publish/issue/{issue.id}/export/elibrary")
    assert r.status_code == 200
    xml = zipfile.ZipFile(io.BytesIO(r.data)).read(zipfile.ZipFile(io.BytesIO(r.data)).namelist()[0]).decode("utf-8")
    assert "machine learning (ML)" in xml and ">ML<" not in xml


def test_article_form_has_keyword_pair_table_and_still_saves_the_two_fields(client, issue):
    art = PubArticle.query.one()
    art.keywords_ru, art.keywords_en = "один, два", "one, two, three"
    db.session.commit()
    html = client.get(f"/admin/publish/article/{art.id}/edit").get_data(as_text=True)
    assert 'id="kwBox"' in html and 'id="kwTable"' in html
    assert html.count('name="keywords_ru"') == 1 and html.count('name="keywords_en"') == 1   # то, что уходит на сервер
    assert "one, two, three" in html                                                           # таблица строится из них
    r = client.post(f"/admin/publish/article/{art.id}/edit", data={
        "title_ru": "Тест", "keywords_ru": "один, два (а, б)", "keywords_en": "one, two (a, b)", "pub_author_count": "0"})
    assert r.status_code == 302
    art = PubArticle.query.get(art.id)
    assert art.keywords_ru_list == ["один", "два (а, б)"] and art.keywords_en_list == ["one", "two (a, b)"]


def test_every_section_page_describes_its_path_for_the_shared_header(client, issue):
    """Путь рисуется в общей плашке (base.html копирует [data-gh-crumbs]), а не отдельной строкой на странице."""
    art = PubArticle.query.one()
    pages = {
        "/admin/publish": ["Публикация"],
        f"/admin/publish/journal/{issue.issn}": ["Публикация", "Нелинейный мир"],
        f"/admin/publish/issue/{issue.id}": ["Публикация", "Нелинейный мир", "2025 №3"],
        f"/admin/publish/article/{art.id}/edit": ["Публикация", "Нелинейный мир", "2025 №3", "Редактирование статьи"],
        "/admin/publish/import": ["Публикация", "Загрузка выпуска"],
    }
    for url, crumbs in pages.items():
        html = client.get(url).get_data(as_text=True)
        block = html.split("data-gh-crumbs", 1)[1].split("</div>", 1)[0]
        text = " ".join(re.sub(r"<[^>]+>", " ", block).split()).replace(" › ", " ")
        for c in crumbs:
            assert c in text, (url, c)
        assert block.count("gh-crumb-current") == 1, url          # ровно одна «текущая» капсула
        assert "pub-crumbs" not in html, url                       # отдельной строки крошек на странице больше нет


def test_staff_pages_with_their_own_breadcrumb_row_are_picked_up_by_the_shared_header(client, staff, dirs):
    """Страницы учёта статей рисуют <ul class="breadcrumbs">; base.html переносит её звенья в плашку (#gh-crumbs) и прячет строку."""
    html = client.get("/journals").get_data(as_text=True)
    assert 'id="gh-crumbs"' in html and 'class="breadcrumbs"' in html
    assert "ul.breadcrumbs" in html and "gh-crumbs-src" in html      # сам перенос — в base.html / header.html


def test_journal_page_has_one_primary_action_in_the_page_header(client, issue):
    html = client.get(f"/admin/publish/journal/{issue.issn}").get_data(as_text=True)
    assert html.count("pub-btn-primary") == 1 and "pub-pagehead-actions" in html
    assert "Загрузить выпуск целиком" not in html                  # дубль «Загрузить выпуск» убран


def test_article_form_has_reference_pair_table_and_still_saves_the_two_fields(client, issue):
    art = PubArticle.query.one()
    art.references_text, art.references_en_text = "Первая. 2021.\nВторая. 2022.", "First. 2021.\nSecond. 2022.\n_tail"
    db.session.commit()
    html = client.get(f"/admin/publish/article/{art.id}/edit").get_data(as_text=True)
    assert 'id="refBox"' in html and 'id="refTable"' in html and 'id="refSuggest"' in html
    assert html.count('name="references_text"') == 1 and html.count('name="references_en_text"') == 1
    r = client.post(f"/admin/publish/article/{art.id}/edit", data={
        "title_ru": "Тест", "references_text": "Первая. 2021.\nВторая. 2022.", "references_en_text": "First. 2021.\nSecond. 2022.", "pub_author_count": "0"})
    assert r.status_code == 302
    art = PubArticle.query.get(art.id)
    assert art.references_list == ["Первая. 2021.", "Вторая. 2022."] and art.references_en_list == ["First. 2021.", "Second. 2022."]


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


# ---------------------------------------------------------------- безопасная запись на сайт (структура базы сайта как на радиотехнике)

SITE_DDL = [
    "CREATE TABLE journals (journ_id INTEGER PRIMARY KEY, menu_name TEXT, journ_name TEXT, link TEXT, issn TEXT)",
    "CREATE TABLE nomera (num_id INTEGER PRIMARY KEY, jr_num INTEGER, num_year INTEGER, num_num TEXT, num_descript TEXT,"
    " num_act INTEGER, file TEXT, price INTEGER, num_descript_eng TEXT)",
    "CREATE TABLE razdel_numbers (razd_id INTEGER PRIMARY KEY, number INTEGER, razd_name TEXT, post INTEGER, jr_in_jr TEXT,"
    " r_sort INTEGER, razd_name_eng TEXT)",
    "CREATE TABLE articles (art_id INTEGER PRIMARY KEY, razd_id INTEGER, art_page TEXT, authors TEXT, art_name TEXT,"
    " descript TEXT, literature TEXT, authors_eng TEXT, art_name_eng TEXT, descript_eng TEXT, literature_eng TEXT,"
    " keyword TEXT, keyword_eng TEXT, article_type INTEGER, udk TEXT, doi TEXT, citata TEXT, data_recieved TEXT,"
    " data_approved TEXT, data_accepted TEXT, citata_eng TEXT, rubr_vak TEXT, article_text TEXT, file TEXT, price INTEGER)",
]


@pytest.fixture()
def site(tmp_path, monkeypatch, issue):
    """Подставная «база сайта» в SQLite с той же структурой; админом вместо обычного сотрудника."""
    import routes_publish
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    # AUTOCOMMIT: как таблицы сайта (MyISAM), откатить сбой транзакцией нельзя — только удалением вручную
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
                           isolation_level="AUTOCOMMIT")
    with engine.begin() as c:
        for ddl in SITE_DDL:
            c.execute(text(ddl))
        c.execute(text("INSERT INTO journals VALUES (1, 'Нелинейный мир', 'Нелинейный мир', 'nm', '2070-0970')"))
    monkeypatch.setattr(routes_publish.site_db, "get_engine", lambda: engine)
    monkeypatch.setattr(routes_publish.site_db, "is_production_configured", lambda: True)
    admin = User(username="adm", display_name="adm", role="admin", password_hash="x")
    db.session.add(admin)
    db.session.commit()
    return engine, admin


def _count(engine, table):
    from sqlalchemy import text
    with engine.connect() as c:
        return c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def _eight_authors(article):
    base = article["authors"][0]
    names = ["Рязанов", "Сучилин", "Чичерин", "Недосекин", "Дручевский", "Онуфриевич", "Сидоров", "Ветрова"]
    article["authors"] = [dict(base, surname_ru=n, surname_en=f"Surname{i}", orcid="", spin="") for i, n in enumerate(names)]
    return article


def test_footnote_numbers_are_separated_from_organisation_and_email():
    """На странице сайта «1,2МГТУ» читалось слитно — между номерами и текстом должен быть пробел."""
    import publish_format_html as F
    authors = [{"surname": "Иванов", "initials": "И.И.", "org": "МГТУ им. Н.Э. Баумана", "town": "Москва",
                "country": "Россия", "email": "ivanov@bmstu.ru"},
               {"surname": "Петров", "initials": "П.П.", "org": "МГТУ им. Н.Э. Баумана; ФГБУ «ГВКГ»", "town": "Москва",
                "country": "Россия", "email": "petrov@mail.ru"}]
    html = F.format_authors_html(authors, "ru")
    assert "<p>1,2 МГТУ им. Н.Э. Баумана (Москва, Россия)</p>" in html
    assert "<p>2 ФГБУ &laquo;ГВКГ&raquo; (Москва, Россия)</p>" in html
    assert "<p>1 ivanov@bmstu.ru, 2 petrov@mail.ru</p>" in html


def test_fix_authors_tool_repairs_only_untouched_rows_and_only_with_apply(client, issue, site, monkeypatch, capsys):
    import re
    import sys
    from sqlalchemy import text
    sys.path.insert(0, "tools")
    import site_fix_authors
    engine, admin = site
    _login(client, admin)
    client.post(f"/admin/publish/issue/{issue.id}/confirm")

    def stored():
        with engine.connect() as c:
            return tuple(c.execute(text("SELECT authors, authors_eng FROM articles")).one())

    fixed = stored()
    assert re.search(r"<p>1 \S", fixed[0]) and "1 ivanov@msu.ru" in fixed[0]
    unspace = lambda h: re.sub(r"(<p>\d+(?:,\d+)*|, \d+) (?=\S)", r"\1", h)    # как записывали раньше: номер вплотную
    legacy = tuple(unspace(h) for h in fixed)
    assert legacy != fixed
    with engine.begin() as c:
        c.execute(text("UPDATE articles SET authors = :a, authors_eng = :e"), {"a": legacy[0], "e": legacy[1]})
    monkeypatch.setattr(sys, "argv", ["site_fix_authors"])
    site_fix_authors.main()
    assert stored() == legacy and "исправил бы" in capsys.readouterr().out      # без --apply в базу не пишем

    monkeypatch.setattr(sys, "argv", ["site_fix_authors", "--apply"])
    site_fix_authors.main()
    assert stored() == fixed

    with engine.begin() as c:                                                    # блок правили руками — не трогаем
        c.execute(text("UPDATE articles SET authors = :a, authors_eng = :e"),
                  {"a": legacy[0] + "<p>Добавлено вручную</p>", "e": legacy[1]})
    site_fix_authors.main()
    assert stored()[0].endswith("Добавлено вручную</p>") and "правили вручную" in capsys.readouterr().out


def test_citation_is_cut_to_first_authors_only_when_it_does_not_fit():
    import publish_format_html as F
    authors = [{"surname": n, "initials": "А.Б."} for n in ["Рязанов", "Сучилин", "Чичерин", "Недосекин", "Дручевский"]]
    args = (authors, "Длинное название статьи " * 4, "Журнал", 2026, "18", "3", "5-21", "10.18127/x-01")
    full, shown, cut = F.format_citation_fit(*args, max_len=None)
    assert (shown, cut) == (5, False) and "и др." not in full
    html, shown, cut = F.format_citation_fit(*args, max_len=len(full) - 1)
    assert (shown, cut) == (3, True) and len(html) < len(full)
    assert "Чичерин А.Б. и др.</em>" in html and "Недосекин" not in html
    html, shown, _ = F.format_citation_fit(*args, max_len=10)            # не влезает вовсе — самый короткий вариант
    assert shown == 1 and "Рязанов А.Б. и др.</em>" in html
    en, _, cut = F.format_citation_fit([dict(a, surname="Ab") for a in authors], "T", "J", 2026, "1", "1", "1-2", "d",
                                       lang="en", max_len=60)
    assert cut and "et al.</em>" in en


def test_site_check_looks_at_all_fields_and_reports_cut_citation(client, issue, site, monkeypatch):
    import routes_publish
    engine, admin = site
    _login(client, admin)
    art = PubArticle.query.one()
    publish_import.make_article(issue.id, _eight_authors(dict(ARTICLE, doi="10.18127/j20700970-202503-05", title_en="E" * 40)), None)
    db.session.commit()
    monkeypatch.setattr(routes_publish.site_db, "column_limits",
                        lambda conn, table="articles": {"citata": 260, "citata_eng": 260, "art_name_eng": 30})
    w = client.get(f"/admin/publish/issue/{issue.id}/site-check").get_json()["warnings"]
    text = "\n".join(w)
    assert "Статья №2" in text and "первые авторы (3 из 8)" in text           # русская строка сокращена, а не отклонена
    assert "поле art_name_eng длиннее" in text                                  # английское название проверяется тоже
    assert not any(x.startswith("Статья №1") and "citata" in x for x in w)    # короткая статья — без замечаний


def test_push_writes_the_cut_citation_that_the_check_announced(client, issue, site, monkeypatch):
    import routes_publish
    from sqlalchemy import text
    engine, admin = site
    _login(client, admin)
    PubArticle.query.one().section_ru = "Раздел"
    for a in PubArticle.query.all():
        db.session.delete(a)
    db.session.commit()
    publish_import.make_article(issue.id, _eight_authors(dict(ARTICLE, title_en="T")), None)
    db.session.commit()
    monkeypatch.setattr(routes_publish.site_db, "column_limits",
                        lambda conn, table="articles": {"citata": 220, "citata_eng": 220})
    client.post(f"/admin/publish/issue/{issue.id}/confirm")
    with engine.connect() as c:
        citata, citata_eng, authors = c.execute(text("SELECT citata, citata_eng, authors FROM articles")).one()
    assert len(citata) <= 220 and citata.count("и др.") == 1 and "Сидоров" not in citata
    assert "et al." in citata_eng
    assert authors.count("Сидоров") == 1                                        # авторы статьи на сайте остаются полными


def test_push_records_what_it_created_and_rollback_removes_exactly_that(client, issue, site):
    engine, admin = site
    _login(client, admin)
    PubArticle.query.one().section_ru = "Новый раздел"
    db.session.commit()
    assert client.post(f"/admin/publish/issue/{issue.id}/confirm").status_code == 302
    assert (_count(engine, "nomera"), _count(engine, "razdel_numbers"), _count(engine, "articles")) == (1, 1, 1)
    rec = json.load(open(hub.PUB_EXPORTS_FOLDER + f"/{issue.id}_site_push.json", encoding="utf-8"))
    assert rec["nomera_created"] is True and rec["sections"][0]["created"] is True and len(rec["articles"]) == 1
    assert PubIssue.query.get(issue.id).pushed_at is not None

    client.post(f"/admin/publish/issue/{issue.id}/site-rollback")
    assert (_count(engine, "nomera"), _count(engine, "razdel_numbers"), _count(engine, "articles")) == (0, 0, 0)
    iss = PubIssue.query.get(issue.id)
    assert iss.pushed_at is None and PubArticle.query.one().site_art_id is None


def test_rollback_does_not_touch_issue_or_section_that_existed_before(client, issue, site):
    from sqlalchemy import text
    engine, admin = site
    _login(client, admin)
    with engine.begin() as c:
        c.execute(text("INSERT INTO nomera (num_id, jr_num, num_year, num_num, num_descript, num_act, file, price, num_descript_eng)"
                       " VALUES (7, 1, 2025, '3', '', 1, '', 0, '')"))
        c.execute(text("INSERT INTO razdel_numbers VALUES (9, 7, 'Старый раздел', 1, 'off', 1, '')"))
    PubArticle.query.one().section_ru = "Старый раздел"
    db.session.commit()
    client.post(f"/admin/publish/issue/{issue.id}/confirm")
    assert _count(engine, "articles") == 1
    client.post(f"/admin/publish/issue/{issue.id}/site-rollback")
    assert (_count(engine, "nomera"), _count(engine, "razdel_numbers"), _count(engine, "articles")) == (1, 1, 0)


def test_failed_push_leaves_nothing_behind(client, issue, site, monkeypatch):
    """Слишком длинное поле у статьи: выпуск и раздел уже созданы, но после отказа их быть не должно."""
    import routes_publish
    engine, admin = site
    _login(client, admin)
    PubArticle.query.one().section_ru = "Раздел"
    db.session.commit()
    monkeypatch.setattr(routes_publish.site_db, "column_limits", lambda conn, table="articles": {"art_name": 3})
    client.post(f"/admin/publish/issue/{issue.id}/confirm")
    assert (_count(engine, "nomera"), _count(engine, "razdel_numbers"), _count(engine, "articles")) == (0, 0, 0)
    assert PubIssue.query.get(issue.id).pushed_at is None


def test_failure_during_insert_removes_inserted_articles_and_created_rows(client, issue, site, monkeypatch):
    import routes_publish
    engine, admin = site
    _login(client, admin)
    publish_import.make_article(issue.id, dict(ARTICLE, doi="10.18127/j20700970-202503-03", pages="14-20"), None)
    db.session.commit()
    real = routes_publish.site_db.insert_article
    calls = []

    def flaky(conn, fields):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("обрыв связи")
        return real(conn, fields)

    monkeypatch.setattr(routes_publish.site_db, "insert_article", flaky)
    client.post(f"/admin/publish/issue/{issue.id}/confirm")
    assert (_count(engine, "nomera"), _count(engine, "razdel_numbers"), _count(engine, "articles")) == (0, 0, 0)
    assert PubIssue.query.get(issue.id).pushed_at is None


def test_rollback_is_admin_only_and_needs_a_pushed_issue(client, issue, site):
    client.post(f"/admin/publish/issue/{issue.id}/site-rollback")        # сотрудник (не админ) — отказ
    assert PubIssue.query.get(issue.id).pushed_at is None
    _, admin = site
    _login(client, admin)
    r = client.post(f"/admin/publish/issue/{issue.id}/site-rollback")   # не записан — откатывать нечего
    assert r.status_code == 302


def test_rollback_skips_article_replaced_on_the_site(client, issue, site):
    """Если под сохранённым номером на сайте уже другая статья (её правили), чужое не удаляется."""
    from sqlalchemy import text
    engine, admin = site
    _login(client, admin)
    client.post(f"/admin/publish/issue/{issue.id}/confirm")
    with engine.begin() as c:
        c.execute(text("UPDATE articles SET doi = 'https://doi.org/10.1000/другая'"))
    client.post(f"/admin/publish/issue/{issue.id}/site-rollback")
    assert _count(engine, "articles") == 1


# ---------------------------------------------------------------- подключение к боевой базе: инструмент

def test_site_connect_url_encodes_special_characters_and_masks_the_password():
    import importlib.util, os as _os
    spec = importlib.util.spec_from_file_location("site_connect", _os.path.join(_os.path.dirname(__file__), "..", "tools", "site_connect.py"))
    sc = importlib.util.module_from_spec(spec); spec.loader.exec_module(sc)
    url = sc.build_url("db.example.ru", "3306", "rt user", "p@ss:w/o#rd", "radiotec_main4")
    assert url == "mysql+pymysql://rt+user:p%40ss%3Aw%2Fo%23rd@db.example.ru:3306/radiotec_main4?charset=utf8mb4"
    assert "p%40ss" not in sc.mask(url) and sc.mask(url).startswith("mysql+pymysql://rt+user:***@db.example.ru")
    assert sc.mask("") == ""
