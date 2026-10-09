"""Клиент API Метафоры: проверки XML и разбор ответов. Сеть не используется — requests замокан."""
from types import SimpleNamespace as NS

import pytest

import export_metafora
import metafora_api


def _issue(pages="5-12"):
    author = NS(orcid="", spin="", researcherid="", scopusid="", surname_ru="Иванов", initials_ru="И.И.",
                org_ru="МГУ", town_ru="Москва", country_ru="Россия", position_ru="", email="",
                surname_en="Ivanov", initials_en="I.I.", org_en="MSU", town_en="Moscow",
                country_en="Russia", position_en="")
    art = NS(section_ru="", section_en="", pages=pages, art_type="RAR", authors=[author],
             title_ru="Тестовая статья", title_en="Test article", abstract_ru="Аннотация", abstract_en="",
             fulltext_ru="", doi="", udk="", keywords_ru=["слово"], keywords_en=[], references=[],
             date_received="", date_accepted="", date_published="", funding_ru="", funding_en="")
    return NS(issn="2070-0970", year=2026, number="3", alt_number="", part="", pages="", elibrary_titleid="",
              volume="", articles=[art])


def _xml(**kw):
    return export_metafora.build_metafora_xml(_issue(**kw), "Нелинейный мир")


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setenv("METAFORA_API_KEY", "SECRET-KEY-123")


def test_valid_xml_has_no_problems():
    assert metafora_api.validate_journal_xml(_xml()) == []


def test_missing_pages_is_reported():
    problems = metafora_api.validate_journal_xml(_xml(pages=""))
    assert any("страниц" in p for p in problems)


def test_broken_xml_is_reported():
    assert "не читается" in metafora_api.validate_journal_xml(b"<journal>")[0]


def test_no_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("METAFORA_API_KEY", raising=False)
    with pytest.raises(metafora_api.MetaforaError, match="METAFORA_API_KEY"):
        metafora_api.upload_journal_xml(b"<x/>")


def test_upload_returns_file_uid_and_sends_key(monkeypatch, key):
    seen = {}

    def fake(method, url, headers, timeout, **kw):
        seen.update(method=method, url=url, headers=headers, files=kw["files"])
        return FakeResp(200, {"message": "Success", "data": {"file_uid": "abc"}})

    monkeypatch.setattr(metafora_api.requests, "request", fake)
    assert metafora_api.upload_journal_xml(b"<x/>", "i.xml") == "abc"
    assert seen["url"].endswith("/api/v2/files/journal/")
    assert seen["headers"]["Api-Key"] == "SECRET-KEY-123"


def test_duplicate_upload_returns_existing_uid(monkeypatch, key):
    body = {"message": "XML file already exists", "error": "XML_ALREADY_EXISTS",
            "data": {"exists_file_uid": "old-uid"}}
    monkeypatch.setattr(metafora_api.requests, "request", lambda *a, **k: FakeResp(409, body))
    assert metafora_api.upload_journal_xml(b"<x/>") == "old-uid"


def test_validation_error_text_is_readable(monkeypatch, key):
    body = {"message": "Missing required data", "error": "MISSING_REQUIRED_DATA",
            "data": {"errors": ["Публикация «Test»: необходимо указать диапазон страниц."]}}
    monkeypatch.setattr(metafora_api.requests, "request", lambda *a, **k: FakeResp(422, body))
    with pytest.raises(metafora_api.MetaforaError) as e:
        metafora_api.upload_journal_xml(b"<x/>")
    assert "диапазон страниц" in str(e.value)
    assert "SECRET-KEY-123" not in str(e.value)


@pytest.mark.parametrize("status,fragment", [(401, "деактивирован"), (403, "ISSN"), (404, "не найдено")])
def test_auth_errors_have_hints(monkeypatch, key, status, fragment):
    monkeypatch.setattr(metafora_api.requests, "request", lambda *a, **k: FakeResp(status, {}))
    with pytest.raises(metafora_api.MetaforaError, match=fragment):
        metafora_api.file_status("u")


def test_network_error_does_not_leak_key(monkeypatch, key):
    def boom(*a, **k):
        raise metafora_api.requests.ConnectionError("down")

    monkeypatch.setattr(metafora_api.requests, "request", boom)
    with pytest.raises(metafora_api.MetaforaError) as e:
        metafora_api.file_status("u")
    assert "SECRET-KEY-123" not in str(e.value)


def test_file_status_parsed(monkeypatch, key):
    body = {"data": {"file_uid": "f", "xml": {"status": {"code": 3, "status_text": "Processed"}},
                     "articles": ["a1", "a2"]}}
    monkeypatch.setattr(metafora_api.requests, "request", lambda *a, **k: FakeResp(200, body))
    st = metafora_api.file_status("f")
    assert st["code"] == 3 and st["articles"] == ["a1", "a2"] and "успешно" in st["text"]


def test_publications_status_maps_signed(monkeypatch, key):
    body = [{"article_uid": "a1", "signed_at": "2026-10-09T10:00:00+03:00", "unsigned_at": None},
            {"article_uid": "a2", "signed_at": None, "unsigned_at": None}]
    monkeypatch.setattr(metafora_api.requests, "request", lambda *a, **k: FakeResp(200, body))
    assert metafora_api.publications_status(["a1", "a2"]) == {"a1": "2026-10-09T10:00:00+03:00", "a2": None}


# ---------- связывание русской и английской литературы ----------
import export_refs


def test_equal_lists_are_paired_in_order():
    ru = ["Иванов И.И. Основы теории. М.: Наука, 2010.", "Петров П.П. Методы расчёта. СПб.: Питер, 2015."]
    en = ["Ivanov I.I. Osnovy teorii. M.: Nauka, 2010.", "Petrov P.P. Metody raschyota. SPb.: Piter, 2015."]
    assert export_refs.pair_references(ru, en) == list(zip(ru, en))


def test_extra_english_item_stays_separate_and_order_kept():
    ru = ["Иванов И.И. Основы теории. М.: Наука, 2010.", "Петров П.П. Методы расчёта. СПб.: Питер, 2015."]
    en = ["Ivanov I.I. Osnovy teorii. M.: Nauka, 2010.", "Sidorov S.S. Lishnyaya ssylka. M., 2001.",
          "Petrov P.P. Metody raschyota. SPb.: Piter, 2015."]
    pairs = export_refs.pair_references(ru, en)
    assert (ru[0], en[0]) in pairs and (ru[1], en[2]) in pairs and (None, en[1]) in pairs
    assert len(pairs) == 3


def test_same_doi_pairs_even_if_text_differs():
    ru = ["Статья без латиницы. DOI: 10.1000/abc.123"]
    en = ["Totally different words. DOI: 10.1000/abc.123"]
    assert export_refs.pair_references(ru, en) == [(ru[0], en[0])]


def test_no_english_list_keeps_russian_only():
    assert export_refs.pair_references(["Иванов. Книга."], []) == [("Иванов. Книга.", None)]


def test_metafora_xml_contains_english_references():
    from lxml import etree
    issue = _issue()
    issue.articles[0].references = ["Иванов И.И. Основы теории. М.: Наука, 2010."]
    issue.articles[0].references_en = ["Ivanov I.I. Osnovy teorii. M.: Nauka, 2010."]
    doc = etree.fromstring(export_metafora.build_metafora_xml(issue, "Нелинейный мир"))
    ref = doc.find(".//reference")
    assert ref.find("refInfo[@lang='RUS']/text").text.startswith("Иванов")
    assert ref.find("refInfo[@lang='ENG']/text").text.startswith("Ivanov")
    assert metafora_api.validate_journal_xml(etree.tostring(doc)) == []


def test_english_only_reference_is_reported():
    issue = _issue()
    issue.articles[0].references = ["Иванов И.И. Основы теории. М.: Наука, 2010."]
    issue.articles[0].references_en = ["Ivanov I.I. Osnovy teorii. M.: Nauka, 2010.", "Sidorov S.S. Lishnyaya ssylka. M., 2001."]
    problems = metafora_api.validate_journal_xml(export_metafora.build_metafora_xml(issue, "Нелинейный мир"))
    assert any("только на английском" in p for p in problems)
