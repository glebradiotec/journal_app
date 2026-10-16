"""Модуль «Публикация на сайте»: подготовка статей и запись напрямую в БД сайта radiotec.ru,
генерация CrossRef/eLibrary XML. Полностью отдельный рабочий процесс от учёта статей
(models.Article/Issue) — свои таблицы (models_publish.py), свои маршруты.
"""
import io
import json
import os
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

from flask import request, render_template, redirect, url_for, flash, jsonify, send_file
from flask_login import current_user

from models import db
from models_publish import PubIssue, PubArticle, PubAuthor, PubSection
from routes_admin import admin_required
from routes_publish_hub import (
    publish_required, register_publish_hub_routes, issue_stage, mf_load_state)
import pdf_parser
import article_template_parser
import publish_config
import publish_doi
import publish_format_html
import publish_sanitize
import publish_titleid_store
import site_db
import export_crossref
import export_elibrary
import export_metafora
import metafora_api
import publish_import
import publish_journal_abbr


ARTICLE_TYPES = [
    ('RAR', 'Научная статья'),
    ('EDI', 'Редакторская заметка'),
    ('BRV', 'Рецензия'),
    ('CNF', 'Материалы конференции'),
    ('SCO', 'Краткое сообщение'),
    ('REV', 'Обзорная статья'),
    ('ABS', 'Аннотация'),
    ('REP', 'Научный отчёт'),
    ('COR', 'Переписка'),
    ('PER', 'Персоналии'),
    ('MIS', 'Разное'),
]
ARTICLE_TYPE_CODE_TO_ID = {
    "RAR": 6, "EDI": 7, "BRV": 8, "CNF": 9, "SCO": 10,
    "REV": 11, "ABS": 12, "REP": 13, "COR": 14, "PER": 15, "MIS": 16,
}

PUB_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads', 'publish_manuscripts')
PUB_EXPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'instance', 'publish_exports')

_AUTHOR_FIELDS = [
    'surname_ru', 'initials_ru', 'surname_en', 'initials_en',
    'org_ru', 'org_en', 'town_ru', 'town_en', 'country_ru', 'country_en',
    'position_ru', 'position_en', 'email', 'orcid', 'spin', 'researcherid', 'scopusid',
]


class _AuthorView:
    """Простая обёртка PubAuthor -> атрибуты с именами, которые ожидают
    export_crossref.py/export_elibrary.py (портированные из xml_import как есть)."""

    def __init__(self, a: PubAuthor):
        for f in _AUTHOR_FIELDS:
            setattr(self, f, getattr(a, f) or '')


class _ArticleView:
    def __init__(self, art: PubArticle):
        self.title_ru = art.title_ru or ''
        self.title_en = art.title_en or ''
        self.abstract_ru = art.abstract_ru or ''
        self.abstract_en = art.abstract_en or ''
        self.fulltext_ru = art.fulltext_ru or ''
        self.keywords_ru = art.keywords_ru_list
        self.keywords_en = art.keywords_en_list
        self.references = art.references_list
        self.references_en = art.references_en_list
        self.funding_ru = art.funding_ru or ''
        self.funding_en = art.funding_en or ''
        self.date_received = art.date_received or ''
        self.date_accepted = art.date_accepted or ''
        self.date_published = art.date_published or ''
        self.pages = art.pages or ''
        self.udk = art.udk or ''
        self.doi = art.doi or ''
        self.art_type = art.art_type or 'RAR'
        self.section_ru = art.section_ru or ''
        self.section_en = art.section_en or ''
        self.site_art_id = art.site_art_id
        self.authors = [_AuthorView(a) for a in art.authors]


class _IssueView:
    def __init__(self, issue: PubIssue):
        self.issn = issue.issn
        self.year = issue.year
        self.number = issue.number
        self.alt_number = issue.alt_number or ''
        self.part = issue.part or ''
        self.pages = issue.pages or ''
        self.elibrary_titleid = issue.elibrary_titleid or ''
        self.volume = issue.volume or ''
        self.articles = [_ArticleView(a) for a in issue.articles]


def _process_pub_authors(article: PubArticle, form):
    """Полностью пересоздаёт список авторов черновика статьи из полей формы
    pub_author_count / pub_author_{i}_{field} — по образцу _process_authors() в routes_admin.py."""
    PubAuthor.query.filter_by(article_id=article.id).delete()
    n = int(form.get('pub_author_count', '0') or '0')
    order = 0
    for i in range(n):
        p = f'pub_author_{i}_'
        surname_ru = form.get(p + 'surname_ru', '').strip()
        if not surname_ru:
            continue
        author = PubAuthor(article_id=article.id, order=order)
        for f in _AUTHOR_FIELDS:
            val = form.get(p + f, '').strip()
            if f in ('country_ru', 'country_en') and not val:
                val = 'Россия' if f == 'country_ru' else 'Russia'
            setattr(author, f, val)
        author.corresponding = form.get(p + 'corresponding') == 'on'
        db.session.add(author)
        order += 1


def _safe_list_journals():
    """Список журналов сайта для выпадающего списка. Возвращает ([], ошибка) если
    БД сайта недоступна/не настроена — форма в этом случае просто просит ISSN вручную."""
    try:
        engine = site_db.get_engine()
        with engine.connect() as conn:
            rows = site_db.list_journals(conn)
        known_titleids = publish_titleid_store.load()
        return [
            {'journ_id': r[0], 'name': r[1], 'issn': r[2], 'titleid': known_titleids.get(r[2] or '', '')}
            for r in rows
        ], None
    except Exception as e:
        return [], str(e)


def _journal_name_en(journal_row):
    """Английского названия журнала в таблице journals нет, но есть поле link — это оно же
    со словами через подчёркивание ('Information-measuring_and_Control_Systems'). Сайт и в
    ручных строках цитирования использует именно его."""
    return (journal_row[3] or '').replace('_', ' ') if journal_row else ''


def _apply_article_fields(article: PubArticle, form):
    article.section_ru = form.get('section_ru', '').strip()
    article.section_en = form.get('section_en', '').strip()
    article.pages = form.get('pages', '').strip()
    article.art_type = form.get('art_type', 'RAR').strip() or 'RAR'
    article.udk = form.get('udk', '').strip()
    article.doi = form.get('doi', '').strip()
    price = form.get('price', '').strip()
    article.price = int(price) if price.isdigit() else None
    article.title_ru = form.get('title_ru', '').strip()
    article.title_en = form.get('title_en', '').strip()
    article.abstract_ru = form.get('abstract_ru', '').strip()
    article.abstract_en = form.get('abstract_en', '').strip()
    article.fulltext_ru = form.get('fulltext_ru', '').strip()
    article.keywords_ru = form.get('keywords_ru', '').strip()
    article.keywords_en = form.get('keywords_en', '').strip()
    article.references_text = form.get('references_text', '').strip()
    article.references_en_text = form.get('references_en_text', '').strip()
    funding_ru = form.get('funding_ru', '').strip()
    funding_en = form.get('funding_en', '').strip()
    article.funding_ru = '' if funding_ru.lower() == 'none' else funding_ru
    article.funding_en = '' if funding_en.lower() == 'none' else funding_en
    article.citation_ru = form.get('citation_ru', '').strip()
    article.citation_en = form.get('citation_en', '').strip()
    article.date_received = form.get('date_received', '').strip()
    article.date_approved = form.get('date_approved', '').strip()
    article.date_accepted = form.get('date_accepted', '').strip()
    article.date_published = form.get('date_published', '').strip()


def _issue_sort(number):
    """'3' -> 3, '5(1)' -> 5 — для сортировки выпусков по номеру."""
    m = re.match(r"\d+", str(number or ""))
    return int(m.group(0)) if m else 0


# Журналы сайта (journals.journ_id), которых нет в списке раздела «Публикация»: Радиосистемы, Экология - XXI век,
# Космическая съёмка Земли, Вопросы биологической, медицинской и фармацевтической химии, Вакуумная, плазменная и
# твердотельная электроника. Это только скрытие в разделе — в БД сайта они остаются; журнал с загруженными сюда
# выпусками всё равно показывается. Вернуть журнал в список — убрать его номер отсюда.
HIDDEN_SITE_JOURNAL_IDS = {2, 10, 13, 19, 20}


def _site_fields(art, issue, doi, journal_row, razd_id=0, limits=None):
    """Поля строки таблицы articles сайта для статьи. Одна сборка на проверку перед записью и на саму запись,
    чтобы они не расходились. -> (fields, notes): notes — что пришлось сократить (строки «Для цитирования»)."""
    authors_ru = [
        {'surname': a.surname_ru, 'initials': a.initials_ru, 'org': a.org_ru, 'town': a.town_ru,
         'country': a.country_ru, 'position': a.position_ru, 'email': a.email}
        for a in art.authors
    ]
    authors_en = [
        {'surname': a.surname_en, 'initials': a.initials_en, 'org': a.org_en, 'town': a.town_en,
         'country': a.country_en, 'position': a.position_en, 'email': a.email}
        for a in art.authors
    ]
    limits = limits or {}
    notes = []
    citata, shown_ru, cut_ru = publish_format_html.format_citation_fit(
        authors_ru, art.title_ru, journal_row[1], issue.year, issue.volume, issue.number, art.pages, doi,
        lang='ru', max_len=limits.get('citata'))
    citata_eng, shown_en, cut_en = publish_format_html.format_citation_fit(
        authors_en, art.title_en, _journal_name_en(journal_row), issue.year, issue.volume, issue.number,
        art.pages, doi, lang='en', max_len=limits.get('citata_eng'))
    if cut_ru:
        notes.append(f'«Для цитирования» (русская) не влезает в {limits.get("citata")} знаков — будут указаны '
                     f'первые авторы ({shown_ru} из {len(authors_ru)}) и «и др.»')
    if cut_en:
        notes.append(f'«Для цитирования» (английская) не влезает в {limits.get("citata_eng")} знаков — будут '
                     f'указаны первые авторы ({shown_en} из {len(authors_en)}) и «et al.»')
    fields = {
        'razd_id': razd_id,
        'art_page': art.pages or '',
        'authors': publish_format_html.format_authors_html(authors_ru, 'ru'),
        'art_name': art.title_ru,
        'descript': publish_format_html.format_paragraphs_html(art.abstract_ru),
        'literature': publish_format_html.format_references_html(art.references_list),
        'authors_eng': publish_format_html.format_authors_html(authors_en, 'en'),
        'art_name_eng': art.title_en or '',
        'descript_eng': publish_format_html.format_paragraphs_html(art.abstract_en),
        'literature_eng': publish_format_html.format_references_html(art.references_en_list),
        'keyword': publish_format_html.format_keywords(art.keywords_ru_list),
        'keyword_eng': publish_format_html.format_keywords(art.keywords_en_list),
        'article_type': ARTICLE_TYPE_CODE_TO_ID.get(art.art_type, 0),
        'udk': art.udk or '',
        # На сайте DOI хранится ссылкой целиком (так и заносят руками), а у нас в PubArticle — «голый» DOI.
        'doi': f'https://doi.org/{doi}',
        'citata': citata,
        'data_recieved': art.date_received or '',
        'data_approved': art.date_approved or '',
        'data_accepted': art.date_accepted or '',
        'citata_eng': citata_eng,
        'rubr_vak': '',
        'article_text': '',
        'file': '',
        'price': art.price or publish_config.DEFAULT_ARTICLE_PRICE,
    }
    return fields, notes


def register_publish_routes(app):
    register_publish_hub_routes(app)

    @app.route('/admin/publish')
    @publish_required
    def admin_publish_index():
        known_journals, journals_error = _safe_list_journals()
        by_issn = {j['issn']: j for j in known_journals if j['issn']}
        drafted_issn = {i.issn for i in PubIssue.query.with_entities(PubIssue.issn).all()}
        known_journals = [j for j in known_journals
                          if j['journ_id'] not in HIDDEN_SITE_JOURNAL_IDS or (j['issn'] and j['issn'] in drafted_issn)]

        # Считаем выпуски/статьи по ISSN, встречающимся в наших черновиках — включая те,
        # которых нет в списке с сайта (например, ISSN введён вручную).
        issue_rows = PubIssue.query.all()
        stats = defaultdict(lambda: {'issues': 0, 'articles': 0, 'name': ''})
        for iss in issue_rows:
            s = stats[iss.issn]
            s['issues'] += 1
            s['articles'] += len(iss.articles)
            if not s['name']:
                s['name'] = iss.journal_name or (by_issn.get(iss.issn) or {}).get('name') or iss.issn

        issues_by_issn = defaultdict(list)
        for iss in sorted(issue_rows, key=lambda i: (-i.year, -_issue_sort(i.number))):
            issues_by_issn[iss.issn].append({'issue': iss, 'stage': issue_stage(iss)})

        journals = []
        seen_issn = set()
        for j in known_journals:
            issn = j['issn']
            s = stats.get(issn, {'issues': 0, 'articles': 0})
            journals.append({'issn': issn, 'name': j['name'], 'issues': s['issues'], 'articles': s['articles'],
                             'recent': issues_by_issn.get(issn, [])[:4]})
            seen_issn.add(issn)
        for issn, s in stats.items():
            if issn in seen_issn:
                continue
            journals.append({'issn': issn, 'name': s['name'], 'issues': s['issues'], 'articles': s['articles'],
                             'recent': issues_by_issn.get(issn, [])[:4]})
        # журналы, с которыми работают, — первыми
        journals.sort(key=lambda j: (0 if j['issues'] else 1, j['name'] or ''))

        # выпуски, про которые неизвестно, лежат ли они уже в Метафоре (кнопка «Сверить с Метафорой»)
        mf_unknown = [i.id for i in issue_rows if i.articles and not mf_load_state(i.id).get('file_uid')]
        mf_key_set = bool(os.environ.get('METAFORA_API_KEY', '').strip())
        return render_template('publish/index.html', journals=journals, journals_error=journals_error,
                               total_issues=len(issue_rows), total_articles=sum(len(i.articles) for i in issue_rows),
                               token=publish_import.new_token(), mf_unknown=mf_unknown if mf_key_set else [])

    @app.route('/admin/publish/journal/<path:issn>')
    @publish_required
    def admin_publish_journal_detail(issn):
        known_journals, _err = _safe_list_journals()
        journal_name = next((j['name'] for j in known_journals if j['issn'] == issn), None)
        issues = PubIssue.query.filter_by(issn=issn).order_by(PubIssue.year.desc(), PubIssue.number).all()
        if not journal_name:
            journal_name = issues[0].journal_name if issues else issn
        years = sorted({iss.year for iss in issues}, reverse=True)
        issues.sort(key=lambda i: (-i.year, -_issue_sort(i.number)))
        return render_template(
            'publish/journal_detail.html', issn=issn, journal_name=journal_name,
            issues=issues, years=years, stages={i.id: issue_stage(i) for i in issues},
        )

    @app.route('/admin/publish/issue/new', methods=['GET', 'POST'])
    @publish_required
    def admin_publish_issue_new():
        if request.method == 'POST':
            issn = request.form.get('issn', '').strip()
            year = request.form.get('year', '').strip()
            number = request.form.get('number', '').strip()
            part = request.form.get('part', '').strip()
            if not issn or not year or not number:
                flash('Заполните ISSN, год и номер выпуска.', 'error')
                return redirect(url_for('admin_publish_issue_new'))
            # "5" + часть "1" -> "5(1)" — так сайт хранит номера выпусков с частями.
            stored_number = f'{number}({part})' if part else number
            issue = PubIssue(
                issn=issn,
                journal_name=request.form.get('journal_name', '').strip(),
                year=int(year),
                number=stored_number,
                volume=request.form.get('volume', '').strip(),
                alt_number=request.form.get('alt_number', '').strip(),
                part=part,
                pages=request.form.get('pages', '').strip(),
                elibrary_titleid=request.form.get('elibrary_titleid', '').strip(),
                created_by_user_id=current_user.id,
            )
            db.session.add(issue)
            db.session.commit()
            flash(f'Выпуск {year} №{stored_number} создан. Теперь добавьте статьи.', 'success')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        journals, journals_error = _safe_list_journals()
        return render_template(
            'publish/issue_form.html', journals=journals, journals_error=journals_error,
            current_year=datetime.now(timezone.utc).year,
            preselect_issn=request.args.get('issn', ''),
        )

    @app.route('/admin/publish/issue/<int:issue_id>')
    @publish_required
    def admin_publish_issue_detail(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        return render_template(
            'publish/issue_detail.html', issue=issue, stage=issue_stage(issue), mf_state=mf_load_state(issue.id),
            mf_key_set=bool(os.environ.get('METAFORA_API_KEY', '').strip()),
            pdf_files=publish_import.list_issue_pdfs(issue.id),
            exports={k: os.path.exists(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_{k}.xml'))
                     for k in ('elibrary', 'crossref', 'metafora')},
            is_admin=current_user.is_admin,
            site_record=os.path.exists(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_site_push.json')),
        )

    @app.route('/admin/publish/issue/<int:issue_id>/section/add', methods=['POST'])
    @publish_required
    def admin_publish_issue_add_section(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        title_ru = request.form.get('title_ru', '').strip()
        title_en = request.form.get('title_en', '').strip()
        if not title_ru:
            flash('Укажите название раздела (ru).', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        db.session.add(PubSection(issue_id=issue.id, title_ru=title_ru, title_en=title_en))
        db.session.commit()
        flash(f'Раздел «{title_ru}» добавлен.', 'success')
        return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

    @app.route('/admin/publish/issue/<int:issue_id>/sections/parse-pdf', methods=['POST'])
    @publish_required
    def admin_publish_issue_parse_sections(issue_id):
        PubIssue.query.get_or_404(issue_id)
        pdf_file = request.files.get('pdf_file')
        if not pdf_file or not pdf_file.filename:
            return jsonify({'error': 'Нет файла'}), 400
        os.makedirs(PUB_UPLOAD_FOLDER, exist_ok=True)
        from werkzeug.utils import secure_filename
        fname = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_') + secure_filename(pdf_file.filename)
        fpath = os.path.join(PUB_UPLOAD_FOLDER, fname)
        pdf_file.save(fpath)
        try:
            result = pdf_parser.extract_toc_sections(fpath)
        except Exception as e:
            return jsonify({'error': f'Не удалось разобрать PDF: {e}'}), 400
        return jsonify(result)

    @app.route('/admin/publish/issue/<int:issue_id>/sections/bulk-add', methods=['POST'])
    @publish_required
    def admin_publish_issue_add_sections_bulk(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        data = request.get_json(silent=True) or {}
        sections = data.get('sections') or []
        added = 0
        for s in sections:
            title_ru = (s.get('title_ru') or '').strip()
            title_en = (s.get('title_en') or '').strip()
            if not title_ru:
                continue
            db.session.add(PubSection(issue_id=issue.id, title_ru=title_ru, title_en=title_en))
            added += 1
        if added:
            db.session.commit()
        return jsonify({'added': added})

    @app.route('/admin/publish/parse-doc', methods=['POST'])
    @publish_required
    def admin_publish_parse_doc():
        doc_file = request.files.get('doc_file')
        if not doc_file or not doc_file.filename:
            return jsonify({'error': 'Нет файла'}), 400
        os.makedirs(PUB_UPLOAD_FOLDER, exist_ok=True)
        from werkzeug.utils import secure_filename
        fname = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_') + secure_filename(doc_file.filename)
        fpath = os.path.join(PUB_UPLOAD_FOLDER, fname)
        doc_file.save(fpath)
        try:
            raw_text = pdf_parser.extract_text_any(fpath)
        except Exception as e:
            return jsonify({'error': f'Не удалось прочитать файл: {e}'}), 400
        try:
            parsed, warnings = article_template_parser.parse_article_text(raw_text)
        except Exception as e:
            return jsonify({'error': f'Ошибка разбора: {e}'}), 400
        parsed = publish_sanitize.deep_clean_text(parsed)
        parsed['saved_filename'] = fname
        return jsonify({'parsed': parsed, 'warnings': warnings})

    @app.route('/admin/publish/issue/<int:issue_id>/article/new', methods=['GET', 'POST'])
    @publish_required
    def admin_publish_article_new(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        if request.method == 'POST':
            article = PubArticle(issue_id=issue.id, title_ru='(без названия)', created_by_user_id=current_user.id)
            _apply_article_fields(article, request.form)
            manuscript_filename = request.form.get('parsed_manuscript_filename', '').strip()
            if manuscript_filename:
                article.manuscript_file = manuscript_filename
            if not article.title_ru:
                flash('Укажите заглавие статьи (ru).', 'error')
                return redirect(url_for('admin_publish_article_new', issue_id=issue.id))
            db.session.add(article)
            db.session.flush()
            _process_pub_authors(article, request.form)
            db.session.commit()
            flash(f'Статья «{article.title_ru[:60]}» добавлена в черновик.', 'success')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        return render_template('publish/article_form.html', issue=issue, article=None,
                                article_types=ARTICLE_TYPES, existing_authors=[])

    @app.route('/admin/publish/article/<int:article_id>/edit', methods=['GET', 'POST'])
    @publish_required
    def admin_publish_article_edit(article_id):
        article = PubArticle.query.get_or_404(article_id)
        issue = article.issue
        if request.method == 'POST':
            _apply_article_fields(article, request.form)
            manuscript_filename = request.form.get('parsed_manuscript_filename', '').strip()
            if manuscript_filename:
                article.manuscript_file = manuscript_filename
            if not article.title_ru:
                flash('Укажите заглавие статьи (ru).', 'error')
                return redirect(url_for('admin_publish_article_edit', article_id=article.id))
            _process_pub_authors(article, request.form)
            db.session.commit()
            flash('Статья обновлена.', 'success')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        existing_authors = [{f: getattr(a, f) for f in _AUTHOR_FIELDS} for a in article.authors]
        return render_template('publish/article_form.html', issue=issue, article=article,
                                article_types=ARTICLE_TYPES, existing_authors=existing_authors)

    @app.route('/admin/publish/article/<int:article_id>/delete', methods=['POST'])
    @publish_required
    def admin_publish_article_delete(article_id):
        article = PubArticle.query.get_or_404(article_id)
        issue_id = article.issue_id
        if article.is_pushed:
            flash('Эта статья уже отправлена на сайт — удаление черновика её не затронет.', 'warning')
        db.session.delete(article)
        db.session.commit()
        flash('Статья удалена из черновика.', 'success')
        return redirect(url_for('admin_publish_issue_detail', issue_id=issue_id))

    def _site_push_record_path(issue_id):
        return os.path.join(PUB_EXPORTS_FOLDER, f'{int(issue_id)}_site_push.json')

    def _site_push_record_save(issue, created):
        """Что создала запись на сайт: по этому файлу работает «Откатить запись на сайт»."""
        try:
            os.makedirs(PUB_EXPORTS_FOLDER, exist_ok=True)
            record = {
                'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'nomera_id': created['nomera_id'], 'nomera_created': created['nomera_created'],
                'sections': [{'id': k, 'created': v} for k, v in created['sections'].items()],
                'articles': [{'art_id': a.site_art_id, 'doi': a.doi} for a in issue.articles if a.site_art_id],
            }
            with open(_site_push_record_path(issue.id), 'w', encoding='utf-8') as f:
                json.dump(record, f, ensure_ascii=False, indent=1)
        except OSError as e:
            flash(f'Запись на сайт выполнена, но не удалось сохранить сведения для отката: {e}', 'warning')

    @app.route('/admin/publish/issue/<int:issue_id>/site-rollback', methods=['POST'])
    @admin_required
    def admin_publish_issue_site_rollback(issue_id):
        """Отменяет запись выпуска на сайт: удаляет записанные статьи и созданные при записи пустые раздел и
        выпуск. Только по сохранённым сведениям о том, что именно было записано."""
        issue = PubIssue.query.get_or_404(issue_id)
        back = redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        if not issue.is_pushed:
            flash('Выпуск не записан на сайт — откатывать нечего.', 'error')
            return back
        try:
            with open(_site_push_record_path(issue.id), encoding='utf-8') as f:
                record = json.load(f)
        except (OSError, ValueError):
            flash('Нет сведений о том, что именно было записано, поэтому автоматически откатить нельзя — '
                  'удалять нужно вручную на сайте.', 'error')
            return back
        try:
            with site_db.get_engine().begin() as conn:
                deleted, skipped, removed = site_db.rollback_push(conn, record)
        except Exception as e:
            flash(f'Откат не выполнен: {e}', 'error')
            return back
        if skipped:
            flash(f'Часть статей не удалена, потому что на сайте под их номерами уже лежит другое '
                  f'({len(skipped)} шт.) — проверьте вручную.', 'warning')
        for art in issue.articles:
            art.site_art_id, art.pushed_at = None, None
        issue.pushed_at, issue.site_num_id = None, None
        db.session.commit()
        for kind in ('crossref',):
            try:
                os.remove(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_{kind}.xml'))
            except OSError:
                pass
        os.replace(_site_push_record_path(issue.id), _site_push_record_path(issue.id) + '.done')
        flash(f'Запись на сайт отменена: статей удалено {len(deleted)}'
              + (f', раздел и выпуск, созданные при записи, убраны' if removed.get('nomera') else '') + '.', 'success')
        return back

    def _site_precheck(issue):
        """Проверка перед записью на сайт (только чтение): в какую БД пишем, что не так, какие будут DOI."""
        warnings = []
        journal_row = None
        num_id = None
        production = site_db.is_production_configured()
        if not production:
            warnings.append(
                'SITE_DATABASE_URL не задан — сейчас модуль работает с локальной тестовой БД, '
                'запись в боевую базу сайта не произойдёт.'
            )

        engine = None
        try:
            engine = site_db.get_engine()
            with engine.connect() as conn:
                journal_row = site_db.get_journal_by_issn(conn, issue.issn)
                if journal_row is None:
                    warnings.append(f'Журнал с ISSN {issue.issn} не найден в БД — проверьте ISSN.')
                else:
                    num_id = site_db.find_nomera(conn, journal_row[0], issue.year, issue.number)
                    if num_id is None:
                        warnings.append(
                            f'Выпуск {issue.year} №{issue.number} ещё не существует — будет создан при подтверждении.'
                        )
                seq_start = site_db.count_articles_in_issue(conn, num_id) if num_id else 0
        except Exception as e:
            warnings.append(f'Не удалось подключиться к БД сайта: {e}')
            seq_start = 0

        computed_dois = []
        for i, art in enumerate(issue.articles, start=1):
            if art.doi:
                computed_dois.append(art.doi)
            else:
                computed_dois.append(publish_doi.build_doi(issue.issn, issue.year, issue.number, seq_start + i))

        if journal_row is not None:
            try:
                with engine.connect() as conn:
                    for doi in computed_dois:
                        if site_db.existing_doi(conn, doi):
                            warnings.append(f'DOI {doi} уже существует в БД сайта — проверьте статью.')
            except Exception:
                pass

        if journal_row is not None:
            try:
                with engine.connect() as conn:
                    limits = site_db.column_limits(conn)
                # Те же поля, что уйдут при записи, и те же пределы колонок — проверка не расходится с записью.
                for i, (art, doi) in enumerate(zip(issue.articles, computed_dois), start=1):
                    fields, notes = _site_fields(art, issue, doi, journal_row, 0, limits)
                    label = f'Статья №{i} «{art.title_ru[:60]}»'
                    for note in notes:
                        warnings.append(f'{label}: {note}.')
                    for col, length, limit in site_db.check_field_lengths(limits, fields):
                        warnings.append(f'{label}: поле {col} длиннее, чем допускает сайт ({length} из {limit}) — '
                                        'отправка будет отклонена, сократите текст.')
            except Exception:
                pass

        for i, art in enumerate(issue.articles):
            if not art.fulltext_ru:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: нет полного текста — для eLibrary будет временно подставлена аннотация.')
            if not art.abstract_ru and not art.abstract_en:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: нет аннотации.')
            if not art.authors:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: не указано ни одного автора.')
        return {'production': production, 'warnings': warnings, 'dois': computed_dois, 'journal_row': journal_row}

    @app.route('/admin/publish/issue/<int:issue_id>/preview')
    @publish_required
    def admin_publish_issue_preview(issue_id):
        # Отдельной страницы предпросмотра больше нет: проверка и запись на сайт — на странице выпуска.
        return redirect(url_for('admin_publish_issue_detail', issue_id=issue_id))

    @app.route('/admin/publish/issue/<int:issue_id>/site-check')
    @publish_required
    def admin_publish_issue_site_check(issue_id):
        """Проверка перед записью на сайт — только чтение, ничего не пишет."""
        issue = PubIssue.query.get_or_404(issue_id)
        if not issue.articles:
            return jsonify({'error': 'В выпуске нет статей.'}), 400
        res = _site_precheck(issue)
        return jsonify({
            'production': res['production'], 'pushed': issue.is_pushed, 'warnings': res['warnings'],
            'articles': [{'title': art.title_ru, 'doi': doi} for art, doi in zip(issue.articles, res['dois'])],
        })

    @app.route('/admin/publish/issue/<int:issue_id>/export/<kind>')
    @publish_required
    def admin_publish_export(issue_id, kind):
        """XML для eLibrary / Метафоры — собирается в момент нажатия из текущих данных выпуска и сразу
        скачивается (поэтому выгрузка не может устареть). CrossRef собирается при записи на сайт — отдаётся он."""
        issue = PubIssue.query.get_or_404(issue_id)
        back = redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        if kind == 'crossref':
            return admin_publish_download(issue_id, kind)
        if kind not in ('elibrary', 'metafora'):
            return 'unknown', 404
        if not issue.articles:
            flash('В выпуске нет статей.', 'error')
            return back
        issue_view = _IssueView(issue)
        name = issue.journal_name or issue.issn
        try:
            xml = (export_elibrary.build_elibrary_xml(issue_view, name) if kind == 'elibrary'
                   else export_metafora.build_metafora_xml(issue_view, name))
        except export_elibrary.ElibraryExportError as e:
            flash(f'Не удалось собрать eLibrary XML: {e}', 'error')
            return back
        download_name = publish_journal_abbr.export_filename(issue, kind)
        if kind == 'elibrary':
            # Восстановление проекта в Articulus принимает только zip-архив с XML внутри.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(download_name, xml)
            buf.seek(0)
            return send_file(buf, as_attachment=True, download_name=f'{download_name}.zip', mimetype='application/zip')
        return send_file(io.BytesIO(xml), as_attachment=True, download_name=download_name, mimetype='application/xml')

    # Запись в БД боевого сайта — только администраторам (остальной раздел открыт всем сотрудникам).
    @app.route('/admin/publish/issue/<int:issue_id>/confirm', methods=['POST'])
    @admin_required
    def admin_publish_issue_confirm(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        if not issue.articles:
            flash('В выпуске нет статей.', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        for art in issue.articles:
            if not art.authors:
                flash(f'У статьи «{art.title_ru[:60]}» нет авторов — отправка отменена.', 'error')
                return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

        engine = site_db.get_engine()
        # Что эта запись создала на сайте (выпуск, разделы): таблицы сайта MyISAM, откатить транзакцией
        # нельзя — поэтому создаваемое запоминаем и при любом сбое убираем сами (см. site_db.delete_created).
        created = {'nomera_id': None, 'nomera_created': False, 'sections': {}}
        try:
            with engine.begin() as conn:
                journal_row = site_db.get_journal_by_issn(conn, issue.issn)
                if journal_row is None:
                    flash(f'Журнал с ISSN {issue.issn} не найден на сайте — отправка отменена.', 'error')
                    return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
                jr_num = journal_row[0]

                num_id, nomera_created = site_db.find_or_create_nomera(conn, jr_num, issue.year, issue.number)
                created['nomera_id'], created['nomera_created'] = num_id, nomera_created
                seq_start = site_db.count_articles_in_issue(conn, num_id)

                limits = site_db.column_limits(conn)
                prepared = []
                for i, art in enumerate(issue.articles, start=1):
                    razd_id, section_created = site_db.find_or_create_section(
                        conn, num_id, art.section_ru or 'Без раздела', art.section_en or ''
                    )
                    created['sections'][razd_id] = created['sections'].get(razd_id, False) or section_created
                    doi = art.doi or publish_doi.build_doi(issue.issn, issue.year, issue.number, seq_start + i)

                    fields, _notes = _site_fields(art, issue, doi, journal_row, razd_id, limits)
                    prepared.append((art, doi, fields))

                # Таблицы сайта — MyISAM, транзакций на них нет, поэтому сначала проверяем все
                # статьи целиком и только потом пишем: иначе ошибка на середине выпуска оставит
                # часть статей на сайте без возможности отката.
                for i, (art, _doi, fields) in enumerate(prepared, start=1):
                    too_long = site_db.check_field_lengths(limits, fields)
                    if too_long:
                        details = ', '.join(f'{col} ({length} символов при пределе {limit})'
                                            for col, length, limit in too_long)
                        site_db.delete_created(conn, created['nomera_id'], created['nomera_created'],
                                               [k for k, v in created['sections'].items() if v])
                        flash(f'Статья №{i} «{art.title_ru[:60]}»: не влезает в поля сайта — '
                              f'{details}. Ничего не отправлено, сократите текст.', 'error')
                        return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

                inserted_ids = []
                try:
                    for art, doi, fields in prepared:
                        site_art_id = site_db.insert_article(conn, fields)
                        inserted_ids.append(site_art_id)
                        art.doi = doi
                        art.site_art_id = site_art_id
                        art.pushed_at = datetime.now(timezone.utc)
                except Exception:
                    # Ручная компенсация вместо ROLLBACK (см. выше про MyISAM).
                    site_db.delete_articles(conn, inserted_ids)
                    site_db.delete_created(conn, created['nomera_id'], created['nomera_created'],
                                           [k for k, v in created['sections'].items() if v])
                    raise

                issue.site_num_id = num_id
        except Exception as e:
            db.session.rollback()
            # Сбой мог случиться до вставки статей (например, при создании раздела) — убираем созданное.
            try:
                if created['nomera_id']:
                    with engine.begin() as conn:
                        site_db.delete_created(conn, created['nomera_id'], created['nomera_created'],
                                               [k for k, v in created['sections'].items() if v])
            except Exception:
                pass
            flash(f'Ошибка при записи в БД сайта: {e}', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

        issue.pushed_at = datetime.now(timezone.utc)
        db.session.commit()
        _site_push_record_save(issue, created)

        # Генерируем и сохраняем CrossRef/eLibrary XML
        os.makedirs(PUB_EXPORTS_FOLDER, exist_ok=True)
        issue_view = _IssueView(issue)
        dois = [a.doi for a in issue.articles]
        try:
            crossref_xml = export_crossref.build_crossref_xml(issue_view, journal_row, dois)
            with open(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_crossref.xml'), 'wb') as f:
                f.write(crossref_xml)
        except Exception as e:
            flash(f'Статьи записаны на сайт, но не удалось сгенерировать CrossRef XML: {e}', 'warning')

        try:
            elibrary_xml = export_elibrary.build_elibrary_xml(
                issue_view, journal_row[2] if journal_row else issue.issn
            )
            with open(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_elibrary.xml'), 'wb') as f:
                f.write(elibrary_xml)
        except export_elibrary.ElibraryExportError as e:
            flash(f'Статьи записаны на сайт, но eLibrary XML не сгенерирован: {e}', 'warning')

        try:
            metafora_xml = export_metafora.build_metafora_xml(
                issue_view, journal_row[2] if journal_row else issue.issn
            )
            with open(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_metafora.xml'), 'wb') as f:
                f.write(metafora_xml)
        except Exception as e:
            flash(f'Статьи записаны на сайт, но XML для Метафоры не сгенерирован: {e}', 'warning')

        flash(f'Выпуск {issue.year} №{issue.number} отправлен на сайт ({len(issue.articles)} стат.).', 'success')
        return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

    @app.route('/admin/publish/issue/<int:issue_id>/download/<kind>')
    @publish_required
    def admin_publish_download(issue_id, kind):
        fname_map = {
            'crossref': f'{issue_id}_crossref.xml',
            'elibrary': f'{issue_id}_elibrary.xml',
            'metafora': f'{issue_id}_metafora.xml',
        }
        fname = fname_map.get(kind)
        if not fname:
            return 'unknown', 404
        path = os.path.join(PUB_EXPORTS_FOLDER, fname)
        if not os.path.exists(path):
            flash('CrossRef XML появится после записи выпуска на сайт.' if kind == 'crossref' else 'Файл ещё не сгенерирован.', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue_id))
        issue = PubIssue.query.get_or_404(issue_id)
        download_name = publish_journal_abbr.export_filename(issue, kind)

        if kind == 'elibrary':
            # Восстановление проекта в Articulus принимает только zip-архив с XML внутри.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.write(path, arcname=download_name)
            buf.seek(0)
            return send_file(buf, as_attachment=True, download_name=f'{download_name}.zip', mimetype='application/zip')

        return send_file(path, as_attachment=True, download_name=download_name)
