"""Генерация CrossRef deposit XML (schema 4.4.2, journal_article) по данным выпуска.

Порт xml_import/export_crossref.py. Работает с "issue"-объектом (см. routes_publish.py:
_IssueView/_ArticleView/_AuthorView — простые объекты-обёртки над PubIssue/PubArticle/PubAuthor,
с теми же именами атрибутов, что были у dataclass-ов xml_import/parse_issue.py).
"""
import datetime
import uuid
from urllib.parse import quote

from lxml import etree

import publish_config

CR_NS = "http://www.crossref.org/schema/4.4.2"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

NSMAP = {None: CR_NS, "xsi": XSI_NS}


def _el(parent, tag, text=None, **attrs):
    e = etree.SubElement(parent, tag)
    if text is not None:
        e.text = text
    for k, v in attrs.items():
        e.set(k, v)
    return e


def build_crossref_xml(issue, journal_row, dois_by_article):
    """issue: объект с .issn/.year/.number/.articles; journal_row: (journ_id, menu_name,
    journ_name, link) из БД сайта; dois_by_article: список DOI по каждой статье в issue.articles
    (тот же порядок)."""
    root = etree.Element(f"{{{CR_NS}}}doi_batch", nsmap=NSMAP, version="4.4.2")
    root.set(f"{{{XSI_NS}}}schemaLocation", f"{CR_NS} https://www.crossref.org/schemas/crossref4.4.2.xsd")

    head = _el(root, "head")
    _el(head, "doi_batch_id", str(uuid.uuid4()))
    _el(head, "timestamp", datetime.datetime.utcnow().strftime("%Y%m%d%H%M%S"))
    depositor = _el(head, "depositor")
    _el(depositor, "depositor_name", publish_config.CROSSREF_DEPOSITOR_NAME)
    _el(depositor, "email_address", publish_config.CROSSREF_DEPOSITOR_EMAIL)
    _el(head, "registrant", publish_config.CROSSREF_REGISTRANT)

    body = _el(root, "body")
    journal = _el(body, "journal")
    jm = _el(journal, "journal_metadata")
    _el(jm, "full_title", journal_row[2] if journal_row else issue.issn)
    _el(jm, "issn", issue.issn, media_type="print")

    ji = _el(journal, "journal_issue")
    pub_date = _el(ji, "publication_date", media_type="online")
    _el(pub_date, "year", str(issue.year))
    if issue.number:
        issue_el = _el(ji, "issue", issue.number)

    for art, doi in zip(issue.articles, dois_by_article):
        ja = _el(journal, "journal_article", publication_type="full_text")
        titles = _el(ja, "titles")
        _el(titles, "title", art.title_ru or art.title_en)
        if art.title_en and art.title_ru:
            _el(titles, "original_language_title", art.title_en, language="en")

        contributors = _el(ja, "contributors")
        for i, a in enumerate(art.authors):
            seq = "first" if i == 0 else "additional"
            pn = _el(
                contributors, "person_name", sequence=seq, contributor_role="author"
            )
            given = a.initials_en or a.initials_ru
            surname = a.surname_en or a.surname_ru
            _el(pn, "given_name", given)
            _el(pn, "surname", surname)
            aff = a.org_en or a.org_ru
            if aff:
                _el(pn, "affiliation", aff)
            if a.orcid:
                orcid_val = a.orcid if a.orcid.startswith("http") else f"https://orcid.org/{a.orcid}"
                _el(pn, "ORCID", orcid_val, authenticated="false")

        if art.abstract_ru or art.abstract_en:
            jats_ns = "http://www.ncbi.nlm.nih.gov/JATS1"
            abstract_el = etree.SubElement(ja, f"{{{jats_ns}}}abstract")
            p = etree.SubElement(abstract_el, f"{{{jats_ns}}}p")
            p.text = art.abstract_en or art.abstract_ru

        art_pub_date = _el(ja, "publication_date")
        _el(art_pub_date, "year", str(issue.year))

        if art.pages:
            pages_el = _el(ja, "pages")
            parts = art.pages.replace(" ", "").split("-")
            _el(pages_el, "first_page", parts[0])
            if len(parts) > 1:
                _el(pages_el, "last_page", parts[1])

        doi_data = _el(ja, "doi_data")
        _el(doi_data, "doi", doi)
        journal_link = (journal_row[3] if journal_row and len(journal_row) > 3 else "") or ""
        _el(
            doi_data, "resource",
            publish_config.ARTICLE_URL_TEMPLATE.format(journal_link=quote(journal_link, safe=""), doi=quote(doi, safe="")),
        )

        if art.references:
            citation_list = _el(ja, "citation_list")
            for i, ref in enumerate(art.references, start=1):
                citation = _el(citation_list, "citation", key=f"ref{i}")
                _el(citation, "unstructured_citation", ref)

    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
