"""Модуль «Публикация на сайте»: подготовка статей и запись напрямую в БД сайта radiotec.ru,
генерация CrossRef/eLibrary XML. Полностью отдельный рабочий процесс от учёта статей
(models.Article/Issue) — свои таблицы (models_publish.py), свои маршруты.
"""
import os
from datetime import datetime, timezone

from flask import request, render_template, redirect, url_for, flash, jsonify, send_file
from flask_login import current_user

from models import db
from models_publish import PubIssue, PubArticle, PubAuthor
from routes_admin import admin_required
import pdf_parser
import article_template_parser
import publish_config
import publish_doi
import publish_format_html
import publish_sanitize
import site_db
import export_crossref
import export_elibrary


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
        return [{'journ_id': r[0], 'name': r[1], 'issn': r[2]} for r in rows], None
    except Exception as e:
        return [], str(e)


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
    article.funding_ru = form.get('funding_ru', '').strip()
    article.funding_en = form.get('funding_en', '').strip()
    article.date_received = form.get('date_received', '').strip()
    article.date_accepted = form.get('date_accepted', '').strip()
    article.date_published = form.get('date_published', '').strip()


def register_publish_routes(app):

    @app.route('/admin/publish')
    @admin_required
    def admin_publish_index():
        issues = PubIssue.query.order_by(PubIssue.created_at.desc()).all()
        return render_template('publish/index.html', issues=issues)

    @app.route('/admin/publish/issue/new', methods=['GET', 'POST'])
    @admin_required
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
        )

    @app.route('/admin/publish/issue/<int:issue_id>')
    @admin_required
    def admin_publish_issue_detail(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        return render_template('publish/issue_detail.html', issue=issue)

    @app.route('/admin/publish/parse-doc', methods=['POST'])
    @admin_required
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
    @admin_required
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
    @admin_required
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
    @admin_required
    def admin_publish_article_delete(article_id):
        article = PubArticle.query.get_or_404(article_id)
        issue_id = article.issue_id
        if article.is_pushed:
            flash('Эта статья уже отправлена на сайт — удаление черновика её не затронет.', 'warning')
        db.session.delete(article)
        db.session.commit()
        flash('Статья удалена из черновика.', 'success')
        return redirect(url_for('admin_publish_issue_detail', issue_id=issue_id))

    @app.route('/admin/publish/issue/<int:issue_id>/preview')
    @admin_required
    def admin_publish_issue_preview(issue_id):
        issue = PubIssue.query.get_or_404(issue_id)
        if not issue.articles:
            flash('В выпуске пока нет ни одной статьи.', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))

        warnings = []
        journal_row = None
        num_id = None
        production = site_db.is_production_configured()
        if not production:
            warnings.append(
                'SITE_DATABASE_URL не задан — сейчас модуль работает с локальной тестовой БД, '
                'запись в боевую базу сайта не произойдёт.'
            )

        engine = site_db.get_engine()
        try:
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

        for i, art in enumerate(issue.articles):
            if not art.fulltext_ru:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: нет полного текста — для eLibrary будет временно подставлена аннотация.')
            if not art.abstract_ru and not art.abstract_en:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: нет аннотации.')
            if not art.authors:
                warnings.append(f'Статья №{i + 1} «{art.title_ru[:60]}»: не указано ни одного автора.')

        return render_template(
            'publish/preview.html', issue=issue, journal_row=journal_row,
            computed_dois=computed_dois, warnings=warnings, production=production,
        )

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
                return redirect(url_for('admin_publish_issue_preview', issue_id=issue.id))

        engine = site_db.get_engine()
        try:
            with engine.begin() as conn:
                journal_row = site_db.get_journal_by_issn(conn, issue.issn)
                if journal_row is None:
                    flash(f'Журнал с ISSN {issue.issn} не найден на сайте — отправка отменена.', 'error')
                    return redirect(url_for('admin_publish_issue_preview', issue_id=issue.id))
                jr_num = journal_row[0]

                num_id, _created = site_db.find_or_create_nomera(conn, jr_num, issue.year, issue.number)
                seq_start = site_db.count_articles_in_issue(conn, num_id)

                for i, art in enumerate(issue.articles, start=1):
                    razd_id, _ = site_db.find_or_create_section(
                        conn, num_id, art.section_ru or 'Без раздела', art.section_en or ''
                    )
                    doi = art.doi or publish_doi.build_doi(issue.issn, issue.year, issue.number, seq_start + i)

                    authors_ru = [
                        {'surname': a.surname_ru, 'initials': a.initials_ru, 'org': a.org_ru,
                         'town': a.town_ru, 'position': a.position_ru, 'email': a.email}
                        for a in art.authors
                    ]
                    authors_en = [
                        {'surname': a.surname_en, 'initials': a.initials_en, 'org': a.org_en,
                         'town': a.town_en, 'position': a.position_en, 'email': a.email}
                        for a in art.authors
                    ]

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
                        'literature_eng': '',
                        'keyword': publish_format_html.format_keywords(art.keywords_ru_list),
                        'keyword_eng': publish_format_html.format_keywords(art.keywords_en_list),
                        'article_type': ARTICLE_TYPE_CODE_TO_ID.get(art.art_type, 0),
                        'udk': art.udk or '',
                        'doi': doi,
                        'citata': '',
                        'data_recieved': art.date_received or '',
                        'data_approved': '',
                        'data_accepted': art.date_accepted or '',
                        'citata_eng': '',
                        'rubr_vak': '',
                        'article_text': '',
                        'file': '',
                        'price': art.price or publish_config.DEFAULT_ARTICLE_PRICE,
                    }
                    site_art_id = site_db.insert_article(conn, fields)
                    art.doi = doi
                    art.site_art_id = site_art_id
                    art.pushed_at = datetime.now(timezone.utc)

                issue.site_num_id = num_id
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при записи в БД сайта: {e}', 'error')
            return redirect(url_for('admin_publish_issue_preview', issue_id=issue.id))

        issue.pushed_at = datetime.now(timezone.utc)
        db.session.commit()

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

        flash(f'Выпуск {issue.year} №{issue.number} отправлен на сайт ({len(issue.articles)} стат.).', 'success')
        return redirect(url_for('admin_publish_issue_preview', issue_id=issue.id))

    @app.route('/admin/publish/issue/<int:issue_id>/download/<kind>')
    @admin_required
    def admin_publish_download(issue_id, kind):
        fname_map = {'crossref': f'{issue_id}_crossref.xml', 'elibrary': f'{issue_id}_elibrary.xml'}
        fname = fname_map.get(kind)
        if not fname:
            return 'unknown', 404
        path = os.path.join(PUB_EXPORTS_FOLDER, fname)
        if not os.path.exists(path):
            flash('Файл ещё не сгенерирован — сначала отправьте выпуск на сайт.', 'error')
            return redirect(url_for('admin_publish_issue_preview', issue_id=issue_id))
        return send_file(path, as_attachment=True, download_name=fname_map[kind])
