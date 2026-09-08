"""Генерация XML для загрузки в eLibrary по схеме xml_import/reference/elibrary_journal.xsd.

Порт xml_import/export_elibrary.py.
"""
import datetime

from lxml import etree

import publish_titleid_store as titleid_store


class ElibraryExportError(Exception):
    pass


def _el(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, tag)
    if text is not None:
        e.text = text
    for k, v in attrs.items():
        if v:
            e.set(k, v)
    return e


def _individ_info(author_el_parent, surname, initials, org, town, country, position, email, lang):
    ii = _el(author_el_parent, "individInfo", lang=lang)
    _el(ii, "surname", surname)
    if initials:
        _el(ii, "initials", initials)
    if town:
        _el(ii, "town", town)
    if country:
        _el(ii, "country", country)
    if position:
        _el(ii, "otherInfo", position)
    if org:
        _el(ii, "orgName", org)
    if email:
        _el(ii, "email", email)


def build_elibrary_xml(issue, journal_title_ru, journal_title_en=""):
    titleid = issue.elibrary_titleid or titleid_store.load().get(issue.issn)
    if not titleid:
        raise ElibraryExportError(
            f"Не известен elibrary titleid для журнала с ISSN {issue.issn}. "
            f"Укажите его в карточке выпуска."
        )

    root = etree.Element("journal")
    _el(root, "titleid", titleid)
    _el(root, "issn", issue.issn)
    ji = _el(root, "journalInfo", lang="RUS")
    _el(ji, "title", journal_title_ru)

    issue_el = _el(root, "issue")
    if issue.number:
        _el(issue_el, "number", issue.number)
    if issue.alt_number:
        _el(issue_el, "altNumber", issue.alt_number)
    if issue.part:
        _el(issue_el, "part", issue.part)
    if issue.pages:
        _el(issue_el, "pages", issue.pages)
    _el(issue_el, "dateUni", str(issue.year))

    articles_el = _el(issue_el, "articles")
    emitted_sections = set()

    for art in issue.articles:
        if art.section_ru and art.section_ru not in emitted_sections:
            sec_el = _el(articles_el, "section")
            _el(sec_el, "secTitle", art.section_ru, lang="RUS")
            if art.section_en:
                _el(sec_el, "secTitle", art.section_en, lang="ENG")
            emitted_sections.add(art.section_ru)

        a_el = _el(articles_el, "article")
        _el(a_el, "pages", art.pages)
        _el(a_el, "artType", art.art_type)
        _el(a_el, "langPubl", "RUS")

        authors_el = _el(a_el, "authors")
        for i, au in enumerate(art.authors, start=1):
            author_el = _el(authors_el, "author", num=str(i).zfill(3))
            codes = au.orcid or au.spin or au.researcherid or au.scopusid
            if codes:
                codes_el = _el(author_el, "authorCodes")
                if au.orcid:
                    _el(codes_el, "orcid", au.orcid)
                if au.spin:
                    _el(codes_el, "spin", au.spin)
                if au.researcherid:
                    _el(codes_el, "researcherid", au.researcherid)
                if au.scopusid:
                    _el(codes_el, "scopusid", au.scopusid)
            _individ_info(
                author_el, au.surname_ru, au.initials_ru, au.org_ru, au.town_ru,
                au.country_ru, au.position_ru, au.email, "RUS",
            )
            if au.surname_en:
                _individ_info(
                    author_el, au.surname_en, au.initials_en, au.org_en, au.town_en,
                    au.country_en, au.position_en, au.email, "ENG",
                )

        titles_el = _el(a_el, "artTitles")
        _el(titles_el, "artTitle", art.title_ru, lang="RUS")
        if art.title_en:
            _el(titles_el, "artTitle", art.title_en, lang="ENG")

        if art.abstract_ru or art.abstract_en:
            abs_el = _el(a_el, "abstracts")
            if art.abstract_ru:
                _el(abs_el, "abstract", art.abstract_ru, lang="RUS")
            if art.abstract_en:
                _el(abs_el, "abstract", art.abstract_en, lang="ENG")

        # eLibrary требует "text" — неформатированный полный текст статьи.
        # Если его нет в разметке, используем аннотацию как временную замену (стоит заполнить позже).
        text_val = art.fulltext_ru or art.abstract_ru or art.title_ru
        _el(a_el, "text", text_val, lang="RUS")

        codes_el = _el(a_el, "codes")
        if art.doi:
            _el(codes_el, "doi", art.doi)
        if art.udk:
            _el(codes_el, "udk", art.udk)

        if art.keywords_ru or art.keywords_en:
            kw_el = _el(a_el, "keywords")
            if art.keywords_ru:
                grp = _el(kw_el, "kwdGroup", lang="RUS")
                for k in art.keywords_ru:
                    _el(grp, "keyword", k)
            if art.keywords_en:
                grp = _el(kw_el, "kwdGroup", lang="ENG")
                for k in art.keywords_en:
                    _el(grp, "keyword", k)

        if art.references:
            refs_el = _el(a_el, "references")
            for ref in art.references:
                ref_el = _el(refs_el, "reference")
                info_el = _el(ref_el, "refInfo", lang="RUS")
                _el(info_el, "text", ref)

        if art.date_received or art.date_accepted or art.date_published:
            dates_el = _el(a_el, "dates")
            if art.date_received:
                _el(dates_el, "dateReceived", art.date_received)
            if art.date_accepted:
                _el(dates_el, "dateAccepted", art.date_accepted)
            if art.date_published:
                _el(dates_el, "datePublication", art.date_published)

        if art.funding_ru or art.funding_en:
            fund_el = _el(a_el, "fundings")
            if art.funding_ru:
                _el(fund_el, "funding", art.funding_ru, lang="RUS")
            if art.funding_en:
                _el(fund_el, "funding", art.funding_en, lang="ENG")

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
