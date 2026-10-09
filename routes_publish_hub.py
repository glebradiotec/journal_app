"""Модуль «Публикация на сайте»: загрузка выпуска целиком и страница выпуска со сверкой по PDF
и отправкой в ИС «Метафора». Маршруты отдают JSON — шагами управляет страница (templates/publish/
import.html и issue_detail.html), чтобы долгие операции шли по одной статье и был виден ход.

Доступ: все сотрудники (администраторы и пользователи), но не внешние авторы.
"""
import json
import os
import shutil
from datetime import datetime, timezone
from functools import wraps

from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

import export_metafora
import metafora_api
import metafora_check
import publish_import
from models import db
from models_publish import PubIssue

PUB_EXPORTS_FOLDER = os.path.join(os.path.dirname(__file__), 'instance', 'publish_exports')


def publish_required(f):
    """Раздел публикации открыт сотрудникам издательства; внешним авторам — нет."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if getattr(current_user, 'role', None) == 'author':
            if request.accept_mimetypes.best == 'application/json' or request.is_json:
                return jsonify({'error': 'Нет доступа'}), 403
            flash('У вас нет доступа к разделу публикации', 'error')
            return redirect(url_for('author.index'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------- состояние отправки в Метафору

def mf_state_path(issue_id):
    return os.path.join(PUB_EXPORTS_FOLDER, f'{int(issue_id)}_metafora_state.json')


def mf_load_state(issue_id):
    try:
        with open(mf_state_path(issue_id), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def mf_save_state(issue_id, state):
    os.makedirs(PUB_EXPORTS_FOLDER, exist_ok=True)
    with open(mf_state_path(issue_id), 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def issue_xml(issue):
    """Journal XML выпуска из черновика в БД (и сохраняет его рядом с остальными экспортами)."""
    from routes_publish import _IssueView
    xml = export_metafora.build_metafora_xml(_IssueView(issue), issue.journal_name or issue.issn)
    os.makedirs(PUB_EXPORTS_FOLDER, exist_ok=True)
    with open(os.path.join(PUB_EXPORTS_FOLDER, f'{issue.id}_metafora.xml'), 'wb') as f:
        f.write(xml)
    return xml


def issue_stage(issue):
    """Сводка по выпуску для карточек: что уже сделано на пути «разобран → в Метафоре → подписан»."""
    state = mf_load_state(issue.id)
    arts = state.get('articles') or []
    return {
        'articles': len(issue.articles),
        'pdfs': len(publish_import.list_issue_pdfs(issue.id)),
        'on_site': issue.is_pushed,
        'mf_sent': bool(state.get('file_uid')),
        'mf_processed': state.get('status_code') == 3,
        'mf_failed': state.get('status_code') == 4,
        'mf_articles': len(arts),
        'mf_pdfs': sum(1 for a in arts if a.get('has_pdf')),
        'mf_signed': sum(1 for a in arts if a.get('signed_at')),
    }


def _json_error(message, status=400, **extra):
    return jsonify({'error': message, **extra}), status


def _publish_css_version():
    """Версия файла стилей раздела для ссылки ?v=… — после обновления браузер не возьмёт старый из кэша."""
    try:
        return int(os.path.getmtime(os.path.join(os.path.dirname(__file__), 'static', 'publish.css')))
    except OSError:
        return 0


def register_publish_hub_routes(app):
    app.jinja_env.globals['publish_css_version'] = _publish_css_version

    # ============================================================ загрузка выпуска целиком

    @app.route('/admin/publish/import')
    @publish_required
    def admin_publish_import():
        return render_template('publish/import.html', token=publish_import.new_token())

    @app.route('/admin/publish/import/<token>/doc', methods=['POST'])
    @publish_required
    def admin_publish_import_doc(token):
        f = request.files.get('file')
        if not f or not f.filename:
            return _json_error('Нет файла')
        try:
            return jsonify(publish_import.add_doc(token, f))
        except publish_import.ImportError_ as e:
            return _json_error(str(e))

    @app.route('/admin/publish/import/<token>/pdf', methods=['POST'])
    @publish_required
    def admin_publish_import_pdf(token):
        f = request.files.get('file')
        if not f or not f.filename:
            return _json_error('Нет файла')
        try:
            return jsonify(publish_import.add_pdf(publish_import.session_pdf_dir(token), f))
        except publish_import.ImportError_ as e:
            return _json_error(str(e))

    @app.route('/admin/publish/import/<token>/summary')
    @publish_required
    def admin_publish_import_summary(token):
        try:
            parsed = publish_import.load_parsed(token)
        except publish_import.ImportError_ as e:
            return _json_error(str(e))
        issue, odd = publish_import.detect_issue(parsed)
        existing = None
        if issue:
            row = PubIssue.query.filter_by(issn=issue['issn'], year=issue['year'], number=issue['number']).first()
            existing = url_for('admin_publish_issue_detail', issue_id=row.id) if row else None
        return jsonify({'issue': issue, 'other_issue_files': odd, 'articles': len(parsed), 'existing_url': existing})

    @app.route('/admin/publish/import/<token>/create', methods=['POST'])
    @publish_required
    def admin_publish_import_create(token):
        fields = request.get_json(silent=True) or {}
        try:
            issue = publish_import.create_issue(token, fields, current_user.id)
        except publish_import.ImportError_ as e:
            existing_id = getattr(e, 'issue_id', None)
            return _json_error(str(e), 409 if existing_id else 400,
                               existing_url=url_for('admin_publish_issue_detail', issue_id=existing_id) if existing_id else None)
        return jsonify({'url': url_for('admin_publish_issue_detail', issue_id=issue.id)})

    @app.route('/admin/publish/import/<token>/cancel', methods=['POST'])
    @publish_required
    def admin_publish_import_cancel(token):
        try:
            shutil.rmtree(publish_import.session_dir(token), ignore_errors=True)
        except publish_import.ImportError_:
            pass
        return jsonify({'ok': True})

    # ============================================================ страница выпуска: PDF и сверка

    @app.route('/admin/publish/issue/<int:issue_id>/pdf', methods=['POST'])
    @publish_required
    def admin_publish_issue_add_pdf(issue_id):
        PubIssue.query.get_or_404(issue_id)
        f = request.files.get('file')
        if not f or not f.filename:
            return _json_error('Нет файла')
        try:
            return jsonify(publish_import.add_pdf(publish_import.issue_pdf_dir(issue_id), f))
        except publish_import.ImportError_ as e:
            return _json_error(str(e))

    @app.route('/admin/publish/issue/<int:issue_id>/check')
    @publish_required
    def admin_publish_issue_check(issue_id):
        """Сверка черновика выпуска с PDF статей. Ничего не меняет и никуда не отправляет."""
        issue = PubIssue.query.get_or_404(issue_id)
        if not issue.articles:
            return jsonify({'articles': [], 'problems': [], 'orphan_pdfs': [], 'ok': False})
        xml = issue_xml(issue)
        problems = metafora_api.validate_journal_xml(xml)
        pdfs = metafora_check.load_pdfs(publish_import.issue_pdf_dir(issue_id))
        rows, seen = [], set()
        for art, data in zip(issue.articles, metafora_check.articles_from_xml(xml)):
            row = {'id': art.id, 'title': art.title_ru, 'doi': art.doi or '', 'pages': art.pages or '',
                   'authors': len(data['authors']), 'refs': len(data['refs']),
                   'edit_url': url_for('admin_publish_article_edit', article_id=art.id)}
            hit = metafora_check.find_pdf(pdfs, art.doi)
            if len(hit) != 1:
                row.update(pdf='', errors=['нет PDF этой статьи — сверить не с чем' if not hit
                                           else f'по DOI подходит несколько PDF: {", ".join(hit)}'], warnings=[])
            else:
                seen.add(hit[0])
                errors, warnings = metafora_check.check_article(data, pdfs[hit[0]])
                row.update(pdf=hit[0], errors=errors, warnings=warnings)
            rows.append(row)
        orphans = [k for k, v in pdfs.items() if k not in seen and publish_import._DOI_RE.search(v['p1'])]
        return jsonify({'articles': rows, 'problems': problems, 'orphan_pdfs': orphans,
                        'ok': not problems and not orphans and all(not r['errors'] for r in rows)})

    @app.route('/admin/publish/issue/<int:issue_id>/delete', methods=['POST'])
    @publish_required
    def admin_publish_issue_delete(issue_id):
        """Удаляет черновик выпуска. Отправленное на сайт или в Метафору так не удаляется."""
        issue = PubIssue.query.get_or_404(issue_id)
        if issue.is_pushed or any(a.is_pushed for a in issue.articles):
            flash('Выпуск уже отправлен на сайт — удалить черновик нельзя.', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        if mf_load_state(issue.id).get('file_uid'):
            flash('Выпуск уже отправлен в Метафору — удалить черновик нельзя.', 'error')
            return redirect(url_for('admin_publish_issue_detail', issue_id=issue.id))
        issn = issue.issn
        db.session.delete(issue)
        db.session.commit()
        shutil.rmtree(publish_import.issue_pdf_dir(issue_id), ignore_errors=True)
        flash('Черновик выпуска удалён.', 'success')
        return redirect(url_for('admin_publish_journal_detail', issn=issn))

    # ============================================================ Метафора — по шагам

    @app.route('/admin/publish/issue/<int:issue_id>/metafora/start', methods=['POST'])
    @publish_required
    def admin_publish_metafora_start(issue_id):
        """Шаг 1: проверка и загрузка XML выпуска. PDF и подпись — отдельными шагами."""
        issue = PubIssue.query.get_or_404(issue_id)
        if not issue.articles:
            return _json_error('В выпуске нет статей.')
        replace = bool((request.get_json(silent=True) or {}).get('replace'))
        xml = issue_xml(issue)
        problems = metafora_api.validate_journal_xml(xml)
        if problems:
            return _json_error('XML не проходит проверку Метафоры.', 422, problems=problems)
        state = mf_load_state(issue_id)
        try:
            if state.get('file_uid'):
                if not replace:
                    return _json_error('Выпуск уже загружен в Метафору.', 409, already=True)
                uids = [a['uid'] for a in state.get('articles') or []]
                if any(metafora_api.publications_status(uids).values()):
                    return _json_error('В Метафоре есть подписанные публикации этого выпуска. '
                                       'Сначала снимите с них подпись — подписанное заменить нельзя.', 409)
                metafora_api.delete_file(state['file_uid'])
            else:
                # Выпуск мог попасть в Метафору другим путём (вручную или tools/metafora_issue.py) —
                # повторная загрузка создала бы дубликаты, поэтому сначала ищем статьи по DOI.
                found = []
                for art in issue.articles:
                    if not art.doi:
                        continue
                    try:
                        metafora_api._request('GET', f'/api/v2/publications/doi/{art.doi}')
                        found.append(art.doi)
                    except metafora_api.MetaforaError as e:
                        if e.status != 404:
                            raise
                if found:
                    return _json_error(
                        f'В Метафоре уже есть статьи этого выпуска ({len(found)} из {len(issue.articles)}), '
                        'загруженные не отсюда. Повторная загрузка создала бы дубликаты.', 409, dois=found)
            file_uid = metafora_api.upload_journal_xml(xml, filename=f'{issue_id}_metafora.xml')
        except metafora_api.MetaforaError as e:
            return _json_error(str(e), 502)
        mf_save_state(issue_id, {'file_uid': file_uid, 'sent_at': _now(), 'status_code': 1,
                                 'status_text': metafora_api.FILE_STATUS_TEXT[1], 'articles': []})
        return jsonify({'file_uid': file_uid})

    @app.route('/admin/publish/issue/<int:issue_id>/metafora/poll')
    @publish_required
    def admin_publish_metafora_poll(issue_id):
        """Шаг 2: состояние обработки файла; когда обработан — список публикаций с DOI и подписью."""
        PubIssue.query.get_or_404(issue_id)
        state = mf_load_state(issue_id)
        if not state.get('file_uid'):
            return _json_error('Выпуск ещё не отправлялся в Метафору.', 404)
        try:
            st = metafora_api.file_status(state['file_uid'])
            known = {a['uid']: a for a in state.get('articles') or []}
            arts = []
            if st['code'] == 3:
                signed = metafora_api.publications_status(st['articles'])
                for uid in st['articles']:
                    a = dict(known.get(uid) or {'uid': uid})
                    if not a.get('doi'):
                        pub = metafora_api.get_publication(uid)
                        loc = pub.get('RUS') or next(iter(pub.values()), {})
                        a.update(doi=loc.get('doi') or '', title=loc.get('title') or '')
                    a['signed_at'] = signed.get(uid)
                    arts.append(a)
        except metafora_api.MetaforaError as e:
            return _json_error(str(e), 502)
        state.update(status_code=st['code'], status_text=st['text'], checked_at=_now())
        if st['code'] == 3:
            state['articles'] = arts
        mf_save_state(issue_id, state)
        return jsonify({'code': st['code'], 'text': st['text'], 'articles': state.get('articles') or [],
                        'raw': st['raw'] if st['code'] == 4 else None})

    def _state_article(issue_id, uid):
        state = mf_load_state(issue_id)
        art = next((a for a in state.get('articles') or [] if a.get('uid') == uid), None)
        return state, art

    @app.route('/admin/publish/issue/<int:issue_id>/metafora/pdf', methods=['POST'])
    @publish_required
    def admin_publish_metafora_pdf(issue_id):
        """Шаг 3 (по одной статье): PDF к публикации; файл подбирается по DOI на первой странице."""
        PubIssue.query.get_or_404(issue_id)
        uid = (request.get_json(silent=True) or {}).get('uid') or ''
        state, art = _state_article(issue_id, uid)
        if not art:
            return _json_error('Публикация не найдена в выпуске.', 404)
        pdfs = metafora_check.load_pdfs(publish_import.issue_pdf_dir(issue_id))
        hit = metafora_check.find_pdf(pdfs, art.get('doi'))
        if len(hit) != 1:
            return _json_error('Нет PDF этой статьи.' if not hit else 'По DOI подходит несколько PDF.', 404)
        try:
            with open(pdfs[hit[0]]['path'], 'rb') as f:
                resp = metafora_api.upload_publication_pdf(uid, f.read(), hit[0])
        except metafora_api.MetaforaError as e:
            return _json_error(str(e), 502)
        art['has_pdf'] = bool((resp.get('data') or {}).get('has_pdf'))
        art['pdf'] = hit[0]
        mf_save_state(issue_id, state)
        return jsonify({'has_pdf': art['has_pdf'], 'pdf': hit[0]})

    @app.route('/admin/publish/issue/<int:issue_id>/metafora/verify')
    @publish_required
    def admin_publish_metafora_verify(issue_id):
        """Шаг 4 (по одной статье): то, что лежит в Метафоре, сверяется с PDF."""
        PubIssue.query.get_or_404(issue_id)
        uid = request.args.get('uid') or ''
        state, art = _state_article(issue_id, uid)
        if not art:
            return _json_error('Публикация не найдена в выпуске.', 404)
        try:
            data = metafora_check.article_from_api(uid)
        except metafora_api.MetaforaError as e:
            return _json_error(str(e), 502)
        pdfs = metafora_check.load_pdfs(publish_import.issue_pdf_dir(issue_id))
        hit = metafora_check.find_pdf(pdfs, data['doi'])
        if len(hit) != 1:
            errors, warnings = ['нет PDF этой статьи — сверить не с чем'], []
        else:
            errors, warnings = metafora_check.check_article(data, pdfs[hit[0]])
        art.update(verified_at=_now(), verify_errors=errors, has_pdf=bool(data.get('has_pdf')),
                   signed_at=data.get('signed_at'))
        mf_save_state(issue_id, state)
        return jsonify({'errors': errors, 'warnings': warnings, 'has_pdf': art['has_pdf'], 'signed_at': art['signed_at']})

    @app.route('/admin/publish/issue/<int:issue_id>/metafora/sign-one', methods=['POST'])
    @publish_required
    def admin_publish_metafora_sign_one(issue_id):
        """Шаг 5 (по одной статье): подпись. Только для публикаций, прошедших сверку без ошибок."""
        PubIssue.query.get_or_404(issue_id)
        uid = (request.get_json(silent=True) or {}).get('uid') or ''
        state, art = _state_article(issue_id, uid)
        if not art:
            return _json_error('Публикация не найдена в выпуске.', 404)
        if art.get('signed_at'):
            return jsonify({'signed_at': art['signed_at']})
        if 'verify_errors' not in art:
            return _json_error('Сначала сверьте публикацию с PDF.', 409)
        if art['verify_errors']:
            return _json_error('Сверка с PDF нашла ошибки — такую публикацию подписывать нельзя.', 409)
        try:
            metafora_api.sign_publication(uid)
            art['signed_at'] = metafora_api.publications_status([uid]).get(uid) or _now()
        except metafora_api.MetaforaError as e:
            return _json_error(str(e), 502)
        mf_save_state(issue_id, state)
        return jsonify({'signed_at': art['signed_at']})
