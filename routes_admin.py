import csv
import io
import os
from datetime import datetime
from functools import wraps
from io import BytesIO

from flask import (
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    session,
    current_app,
)
from flask_login import login_required, current_user
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from werkzeug.utils import secure_filename
from sqlalchemy import or_, func
from flask import send_file

from sqlalchemy.orm import joinedload
from models import db, User, Journal, Issue, Article, ArticleAuthor, ArticleImage, ActivityLog


ALLOWED_EXTENSIONS = {'txt', 'pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png', 'gif', 'bmp'}


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if not current_user.is_admin:
            flash('У вас нет доступа к админ-панели', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)

    return decorated


def log_activity(action, entity_type, entity_id=None, entity_title=None, details=None):
    """Записывает действие в журнал активности."""
    entry = ActivityLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_title=entity_title,
        details=details,
        created_at=datetime.utcnow()
    )
    db.session.add(entry)


def register_admin_routes(app):
    @app.route('/journal/<int:journal_id>/issues/reorder', methods=['POST'])
    @login_required
    def reorder_issues(journal_id):
        data = request.json['order']
        for new_pos, issue_id in enumerate(data):
            issue = Issue.query.get(issue_id)
            if issue and issue.journal_id == journal_id:
                issue.position = new_pos
        db.session.commit()
        return jsonify({'status': 'ok'})

    @app.route("/journal/<int:journal_id>/issue/new", methods=['POST'])
    @login_required
    def new_issue(journal_id):
        number = request.form['number']
        year = request.form['year']
        issue = Issue(number=int(number), year=int(year), journal_id=journal_id)
        db.session.add(issue)
        db.session.flush()
        journal = Journal.query.get(journal_id)
        log_activity('created', 'issue', issue.id, f'№{number}/{year}', f'Журнал: {journal.name}' if journal else None)
        db.session.commit()
        return redirect(f'/journal/{journal_id}')

    @app.route("/issue/<int:issue_id>/add-article", methods=['POST'])
    @login_required
    def add_article(issue_id):
        title = request.form.get('title')
        notes = request.form.get('notes', '')

        # Формирование даты поступления
        day = request.form.get('submission_day', '')
        month = request.form.get('submission_month', '')
        year = request.form.get('submission_year', '')
        submission_date = f"{day}.{month}.{year}" if day and month and year else None

        article = Article(
            title=title,
            submission_date=submission_date,
            notes=notes,
            issue_id=issue_id
        )

        # Загрузка файлов
        files_to_process = {
            'manuscript_file': request.files.get('manuscript_file'),
            'review_file': request.files.get('review_file'),
            'title_pdf': request.files.get('title_pdf')
        }

        upload_folder = current_app.config['UPLOAD_FOLDER']
        for field_name, file in files_to_process.items():
            if file and file.filename and allowed_file(file.filename):
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filename = secure_filename(timestamp + file.filename)
                filepath = os.path.join(upload_folder, filename)
                file.save(filepath)
                setattr(article, field_name, filename)

        db.session.add(article)
        db.session.flush()
        
        # Загрузка изображений (несколько)
        images = request.files.getlist('article_images')
        for i, img in enumerate(images):
            if img and img.filename and allowed_file(img.filename):
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filename = secure_filename(timestamp + img.filename)
                filepath = os.path.join(upload_folder, filename)
                img.save(filepath)
                article_image = ArticleImage(
                    article_id=article.id,
                    filename=filename,
                    order=i
                )
                db.session.add(article_image)

        # Добавление авторов
        author_names = request.form.getlist('author_name[]')
        author_emails = request.form.getlist('author_email[]')
        author_orgs = request.form.getlist('author_organization[]')
        author_degrees = request.form.getlist('author_degree[]')
        author_positions = request.form.getlist('author_position[]')
        author_phones = request.form.getlist('author_phone[]')

        # Собираем имена авторов для строкового поля
        author_names_list = []
        for i, name in enumerate(author_names):
            if name:
                author_names_list.append(name)
                author = ArticleAuthor(
                    article_id=article.id,
                    full_name=name,
                    email=author_emails[i] if i < len(author_emails) else '',
                    organization=author_orgs[i] if i < len(author_orgs) else '',
                    degree=author_degrees[i] if i < len(author_degrees) else '',
                    position=author_positions[i] if i < len(author_positions) else '',
                    phone=author_phones[i] if i < len(author_phones) else '',
                    order=i
                )
                db.session.add(author)

        # Записываем ФИО авторов в строковое поле для совместимости
        if author_names_list:
            article.authors = ", ".join(author_names_list)

        log_activity('created', 'article', article.id, article.title)
        db.session.commit()
        return redirect(f'/issue/{issue_id}')

    @app.route("/article/<int:article_id>/toggle/<field>", methods=['POST'])
    @login_required
    def toggle_article(article_id, field):
        article = Article.query.get_or_404(article_id)
        issue_id = article.issue_id
        new_value = False

        if field == 'payment':
            article.payment_received = not article.payment_received
            new_value = article.payment_received
        elif field == 'edited':
            article.edited = not article.edited
            new_value = article.edited
        elif field == 'review':
            article.has_review = not article.has_review
            new_value = article.has_review

        field_labels = {'payment': 'Оплата', 'edited': 'Редакт.', 'review': 'Рецензия'}
        status_text = 'вкл' if new_value else 'выкл'
        log_activity('toggled', 'article', article.id, article.title, f'{field_labels.get(field, field)}: {status_text}')
        db.session.commit()
        
        # Если AJAX-запрос, возвращаем JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'field': field, 'value': new_value})
        
        return redirect(f'/issue/{issue_id}')

    @app.route('/article/<int:article_id>/update-notes', methods=['POST'])
    @login_required
    def update_notes(article_id):
        data = request.json
        article = Article.query.get(article_id)
        if article:
            article.notes = data.get('notes', '')
            db.session.commit()
            return jsonify({'success': True})
        return jsonify({'success': False}), 404

    @app.route("/article/<int:article_id>/delete", methods=['POST'])
    @login_required
    def delete_article(article_id):
        article = Article.query.get_or_404(article_id)
        issue_id = article.issue_id
        log_activity('deleted', 'article', article.id, article.title)
        db.session.delete(article)
        db.session.commit()
        return redirect(f'/issue/{issue_id}')

    @app.route("/issue/<int:issue_id>/delete", methods=['POST'])
    @login_required
    def delete_issue(issue_id):
        issue = Issue.query.get_or_404(issue_id)
        journal_id = issue.journal_id
        log_activity('deleted', 'issue', issue.id, f'№{issue.number}/{issue.year}', f'Журнал: {issue.journal.name}')
        for article in issue.articles:
            db.session.delete(article)
        db.session.delete(issue)
        db.session.commit()
        return redirect(f'/journal/{journal_id}')

    @app.route("/article/<int:article_id>/edit", methods=['GET', 'POST'])
    @login_required
    def edit_article(article_id):
        article = Article.query.options(
            joinedload(Article.article_authors),
            joinedload(Article.images)
        ).get_or_404(article_id)
        issue = Issue.query.options(joinedload(Issue.journal)).get(article.issue_id)
        journal = issue.journal if issue else None
        
        if request.method == 'POST':
            # Основные поля
            article.title = request.form.get('title', article.title)
            article.notes = request.form.get('notes', '')
            
            # Дата поступления
            day = request.form.get('submission_day', '')
            month = request.form.get('submission_month', '')
            year = request.form.get('submission_year', '')
            if day and month and year:
                article.submission_date = f"{day}.{month}.{year}"
            
            # Статусы
            article.payment_received = 'payment_received' in request.form
            article.has_review = 'has_review' in request.form
            article.edited = 'edited' in request.form
            
            # Загрузка файлов
            files_to_process = {
                'manuscript_file': request.files.get('manuscript_file'),
                'review_file': request.files.get('review_file'),
                'title_pdf': request.files.get('title_pdf')
            }
            
            upload_folder = current_app.config['UPLOAD_FOLDER']
            for field_name, file in files_to_process.items():
                if file and file.filename and allowed_file(file.filename):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                    filename = secure_filename(timestamp + file.filename)
                    filepath = os.path.join(upload_folder, filename)
                    file.save(filepath)
                    setattr(article, field_name, filename)
            
            # Удаление файлов
            if request.form.get('delete_manuscript'):
                article.manuscript_file = None
            if request.form.get('delete_review'):
                article.review_file = None
            if request.form.get('delete_title_pdf'):
                article.title_pdf = None
            
            # Удаление выбранных изображений
            delete_image_ids = request.form.getlist('delete_image[]')
            for img_id in delete_image_ids:
                img = ArticleImage.query.get(int(img_id))
                if img and img.article_id == article.id:
                    db.session.delete(img)
            
            # Загрузка новых изображений
            images = request.files.getlist('article_images')
            current_max_order = max([img.order for img in article.images], default=-1)
            for i, img in enumerate(images):
                if img and img.filename and allowed_file(img.filename):
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                    filename = secure_filename(timestamp + img.filename)
                    filepath = os.path.join(upload_folder, filename)
                    img.save(filepath)
                    article_image = ArticleImage(
                        article_id=article.id,
                        filename=filename,
                        order=current_max_order + i + 1
                    )
                    db.session.add(article_image)
            
            # Обновление авторов - удаляем старых и добавляем новых
            ArticleAuthor.query.filter_by(article_id=article.id).delete()
            
            author_names = request.form.getlist('author_name[]')
            author_emails = request.form.getlist('author_email[]')
            author_orgs = request.form.getlist('author_organization[]')
            author_degrees = request.form.getlist('author_degree[]')
            author_positions = request.form.getlist('author_position[]')
            author_phones = request.form.getlist('author_phone[]')
            
            author_names_list = []
            for i, name in enumerate(author_names):
                if name:
                    author_names_list.append(name)
                    author = ArticleAuthor(
                        article_id=article.id,
                        full_name=name,
                        email=author_emails[i] if i < len(author_emails) else '',
                        organization=author_orgs[i] if i < len(author_orgs) else '',
                        degree=author_degrees[i] if i < len(author_degrees) else '',
                        position=author_positions[i] if i < len(author_positions) else '',
                        phone=author_phones[i] if i < len(author_phones) else '',
                        order=i
                    )
                    db.session.add(author)
            
            if author_names_list:
                article.authors = ", ".join(author_names_list)
            else:
                article.authors = None
            
            log_activity('updated', 'article', article.id, article.title)
            db.session.commit()
            return redirect(f'/issue/{article.issue_id}')
        
        return render_template('edit_article.html', article=article, issue=issue, journal=journal)

    @app.route('/article/<int:article_id>/move', methods=['POST'])
    @login_required
    def move_article(article_id):
        data = request.get_json()
        new_issue_id = data.get('issue_id')

        article = Article.query.get(article_id)
        if not article:
            return jsonify({'error': 'Статья не найдена'}), 404

        issue = Issue.query.get(new_issue_id)
        if not issue:
            return jsonify({'error': 'Выпуск не найден'}), 400

        old_issue = article.issue
        article.issue_id = new_issue_id
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Статья перемещена из "{old_issue.journal.name} №{old_issue.number}/{old_issue.year}" в "{issue.journal.name} №{issue.number}/{issue.year}"'
        })

    @app.route('/issue/<int:issue_id>/export-excel')
    @login_required
    def export_issue_excel(issue_id):
        """Экспортирует все статьи выпуска в Excel"""
        issue = Issue.query.get(issue_id)
        if not issue:
            return "Выпуск не найден", 404

        # Создаём Excel
        wb = Workbook()
        ws = wb.active
        ws.title = f"Выпуск {issue.number}"

        # Стили
        header_fill = PatternFill(start_color="007BFF", end_color="007BFF", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=12)

        # Заголовки
        headers = ["№", "Название статьи", "Авторы", "Дата поступления", "Оплачено", "Рецензия", "Редактировано"]
        ws.append(headers)

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Данные статей
        for idx, article in enumerate(issue.articles, 1):
            authors = ", ".join([f"{a.full_name}" for a in article.article_authors]) if article.article_authors else article.authors or "-"

            ws.append([
                idx,
                article.title or "-",
                authors,
                article.submission_date or "-",
                "✓" if article.payment_received else "✗",
                "✓" if article.has_review else "✗",
                "✓" if article.edited else "✗"
            ])

        # Ширина колонок
        ws.column_dimensions['A'].width = 5
        ws.column_dimensions['B'].width = 40
        ws.column_dimensions['C'].width = 30
        ws.column_dimensions['D'].width = 18
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 12
        ws.column_dimensions['G'].width = 15

        # Отправляем файл
        output = BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f"Выпуск_{issue.number}_{issue.year}.xlsx"
        )

    @app.route('/issue/<int:issue_id>/export-csv')
    @login_required
    def export_issue_csv(issue_id):
        """Экспортирует все статьи выпуска в CSV"""
        issue = Issue.query.get(issue_id)
        if not issue:
            return "Выпуск не найден", 404

        # Создаём CSV в памяти
        output = io.StringIO()
        writer = csv.writer(output)

        # Заголовки
        headers = ["№", "Название статьи", "Авторы", "Дата поступления", "Оплачено", "Рецензия", "Редактировано"]
        writer.writerow(headers)

        # Данные статей
        for idx, article in enumerate(issue.articles, 1):
            authors = ", ".join([f"{a.full_name}" for a in article.article_authors]) if article.article_authors else article.authors or "-"

            writer.writerow([
                idx,
                article.title or "-",
                authors,
                article.submission_date or "-",
                "✓" if article.payment_received else "✗",
                "✓" if article.has_review else "✗",
                "✓" if article.edited else "✗"
            ])

        # Конвертируем в BytesIO для отправки
        output.seek(0)
        bytes_output = BytesIO(output.getvalue().encode('utf-8-sig'))

        return send_file(
            bytes_output,
            mimetype='text/csv; charset=utf-8',
            as_attachment=True,
            download_name=f"Выпуск_{issue.number}_{issue.year}.csv"
        )

    @app.route('/admin')
    @admin_required
    def admin_home():
        return redirect(url_for('admin_dashboard'))

    @app.route('/admin/logout')
    @login_required
    def admin_logout():
        return redirect(url_for('logout'))

    @app.route('/admin/dashboard')
    @admin_required
    def admin_dashboard():
        try:
            stats = {
                'journals': Journal.query.count(),
                'issues': Issue.query.count(),
                'articles': Article.query.count(),
                'unpaid': Article.query.filter_by(payment_received=False).count(),
                'no_review': Article.query.filter_by(has_review=False).count(),
                'not_edited': Article.query.filter_by(edited=False).count(),
            }
            recent_articles = (
                Article.query
                .options(joinedload(Article.issue).joinedload(Issue.journal))
                .order_by(Article.id.desc())
                .limit(10)
                .all()
            )
            recent_activity = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(15).all()
            
            # Данные для графика: статьи по журналам (один запрос вместо N)
            chart_query = (
                db.session.query(Journal.name, func.count(Article.id))
                .join(Issue, Issue.journal_id == Journal.id)
                .join(Article, Article.issue_id == Issue.id)
                .group_by(Journal.id, Journal.name)
                .having(func.count(Article.id) > 0)
                .order_by(func.count(Article.id).desc())
                .all()
            )
            chart_data = [{'name': name, 'count': count} for name, count in chart_query]
            max_articles = chart_data[0]['count'] if chart_data else 0
            
        except Exception:
            stats = {'journals': 0, 'issues': 0, 'articles': 0, 'unpaid': 0, 'no_review': 0, 'not_edited': 0}
            recent_articles = []
            recent_activity = []
            chart_data = []
            max_articles = 0

        return render_template('admin/dashboard.html', 
                               stats=stats, 
                               recent_articles=recent_articles,
                               recent_activity=recent_activity,
                               chart_data=chart_data,
                               max_articles=max_articles)

    @app.route('/admin/journals')
    @admin_required
    def admin_journals():
        """Страница управления журналами"""
        # Один запрос для всех журналов с подсчётами (вместо 2N запросов)
        journals_with_counts = (
            db.session.query(
                Journal,
                func.count(func.distinct(Issue.id)).label('issue_count'),
                func.count(Article.id).label('article_count')
            )
            .outerjoin(Issue, Issue.journal_id == Journal.id)
            .outerjoin(Article, Article.issue_id == Issue.id)
            .group_by(Journal.id)
            .order_by(Journal.id)
            .all()
        )
        journals_data = []
        for j, issue_count, article_count in journals_with_counts:
            journals_data.append({
                'id': j.id,
                'name': j.name,
                'issn': j.issn or '',
                'issues': issue_count,
                'articles': article_count,
            })
        return render_template('admin/journals.html', journals=journals_data)

    @app.route('/admin/journals/add', methods=['POST'])
    @admin_required
    def admin_journal_add():
        """Добавление нового журнала"""
        data = request.get_json()
        name = (data.get('name') or '').strip()
        issn = (data.get('issn') or '').strip()
        if not name:
            return jsonify({'success': False, 'message': 'Название не может быть пустым'}), 400
        journal = Journal(name=name, issn=issn or None)
        db.session.add(journal)
        db.session.flush()
        log_activity('created', 'journal', journal.id, journal.name)
        db.session.commit()
        return jsonify({'success': True, 'id': journal.id, 'name': journal.name, 'issn': journal.issn or ''})

    @app.route('/admin/journals/<int:journal_id>/rename', methods=['POST'])
    @admin_required
    def admin_journal_rename(journal_id):
        """Переименование журнала"""
        data = request.get_json()
        name = (data.get('name') or '').strip()
        issn = data.get('issn')
        if not name:
            return jsonify({'success': False, 'message': 'Название не может быть пустым'}), 400
        journal = Journal.query.get_or_404(journal_id)
        old_name = journal.name
        journal.name = name
        if issn is not None:
            journal.issn = issn.strip() or None
        log_activity('updated', 'journal', journal.id, journal.name, f'Было: {old_name}')
        db.session.commit()
        return jsonify({'success': True, 'name': journal.name, 'issn': journal.issn or ''})

    @app.route('/admin/journals/<int:journal_id>/delete', methods=['POST'])
    @admin_required
    def admin_journal_delete(journal_id):
        """Удаление журнала"""
        journal = Journal.query.get_or_404(journal_id)
        # Проверяем наличие выпусков
        issue_count = Issue.query.filter_by(journal_id=journal_id).count()
        if issue_count > 0:
            return jsonify({
                'success': False,
                'message': f'Невозможно удалить: журнал содержит {issue_count} выпуск(ов). Сначала удалите все выпуски.'
            }), 400
        log_activity('deleted', 'journal', journal.id, journal.name)
        db.session.delete(journal)
        db.session.commit()
        return jsonify({'success': True})

    @app.route('/admin/articles')
    @admin_required
    def admin_articles():
        """Страница управления всеми статьями"""
        # Фильтры
        journal_id = request.args.get('journal_id', type=int)
        issue_id = request.args.get('issue_id', type=int)
        status_filter = request.args.get('status', '')  # unpaid, no_review, not_edited
        search = request.args.get('search', '').strip()
        
        query = (
            Article.query
            .join(Issue).join(Journal)
            .options(
                joinedload(Article.issue).joinedload(Issue.journal),
                joinedload(Article.article_authors)
            )
        )
        
        if journal_id:
            query = query.filter(Issue.journal_id == journal_id)
        if issue_id:
            query = query.filter(Article.issue_id == issue_id)
        if status_filter == 'unpaid':
            query = query.filter(Article.payment_received == False)
        elif status_filter == 'no_review':
            query = query.filter(Article.has_review == False)
        elif status_filter == 'not_edited':
            query = query.filter(Article.edited == False)
        
        if search:
            search_lower = search.lower()
            all_articles = query.all()
            articles = [a for a in all_articles if search_lower in (a.title or '').lower() or search_lower in (a.authors or '').lower()]
        else:
            articles = query.order_by(Article.id.desc()).all()
        
        journals = Journal.query.all()
        issues = Issue.query.all()
        
        return render_template('admin/articles.html', 
                               articles=articles, 
                               journals=journals,
                               issues=issues,
                               current_journal=journal_id,
                               current_issue=issue_id,
                               current_status=status_filter,
                               search=search)

    @app.route('/admin/articles/bulk-delete', methods=['POST'])
    @admin_required
    def bulk_delete_articles():
        """Массовое удаление статей"""
        data = request.get_json()
        article_ids = data.get('ids', [])
        
        if not article_ids:
            return jsonify({'success': False, 'message': 'Не выбрано ни одной статьи'}), 400
        
        deleted_count = 0
        for article_id in article_ids:
            article = Article.query.get(article_id)
            if article:
                # Удаляем авторов статьи
                ArticleAuthor.query.filter_by(article_id=article_id).delete()
                db.session.delete(article)
                deleted_count += 1
        
        db.session.commit()
        return jsonify({'success': True, 'message': f'Удалено статей: {deleted_count}'})

    @app.route('/admin/articles/bulk-toggle', methods=['POST'])
    @admin_required
    def bulk_toggle_articles():
        """Массовое изменение статуса статей"""
        data = request.get_json()
        article_ids = data.get('ids', [])
        field = data.get('field', '')  # payment, review, edited
        value = data.get('value', True)
        
        if not article_ids:
            return jsonify({'success': False, 'message': 'Не выбрано ни одной статьи'}), 400
        
        updated_count = 0
        for article_id in article_ids:
            article = Article.query.get(article_id)
            if article:
                if field == 'payment':
                    article.payment_received = value
                elif field == 'review':
                    article.has_review = value
                elif field == 'edited':
                    article.edited = value
                updated_count += 1
        
        db.session.commit()
        
        field_names = {'payment': 'оплата', 'review': 'рецензия', 'edited': 'редактирование'}
        status = 'установлен' if value else 'снят'
        return jsonify({'success': True, 'message': f'{field_names.get(field, field)}: {status} для {updated_count} статей'})

    @app.route('/admin/articles/bulk-export')
    @admin_required
    def bulk_export_articles():
        """Экспорт выбранных статей в Excel"""
        article_ids = request.args.get('ids', '')
        
        if article_ids:
            ids_list = [int(x) for x in article_ids.split(',') if x.isdigit()]
            articles = Article.query.filter(Article.id.in_(ids_list)).all()
        else:
            articles = Article.query.all()
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Статьи"
        
        header_fill = PatternFill(start_color="007BFF", end_color="007BFF", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        
        headers = ["ID", "Название", "Авторы", "Журнал", "Выпуск", "Дата поступления", "Оплата", "Рецензия", "Редакт."]
        ws.append(headers)
        
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center")
        
        for article in articles:
            authors = ", ".join([a.full_name for a in article.article_authors]) if article.article_authors else (article.authors or "-")
            journal_name = article.issue.journal.name if article.issue and article.issue.journal else "-"
            issue_info = f"№{article.issue.number}/{article.issue.year}" if article.issue else "-"
            
            ws.append([
                article.id,
                article.title or "-",
                authors,
                journal_name,
                issue_info,
                article.submission_date or "-",
                "✓" if article.payment_received else "✗",
                "✓" if article.has_review else "✗",
                "✓" if article.edited else "✗"
            ])
        
        ws.column_dimensions['A'].width = 6
        ws.column_dimensions['B'].width = 40
        ws.column_dimensions['C'].width = 30
        ws.column_dimensions['D'].width = 20
        ws.column_dimensions['E'].width = 12
        ws.column_dimensions['F'].width = 15
        ws.column_dimensions['G'].width = 10
        ws.column_dimensions['H'].width = 10
        ws.column_dimensions['I'].width = 10
        
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        filename = f"articles_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                         as_attachment=True, download_name=filename)

    # ==================== USER MANAGEMENT ====================
    @app.route('/admin/users')
    @admin_required
    def admin_users():
        users = User.query.order_by(User.role.desc(), User.display_name).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/add', methods=['POST'])
    @admin_required
    def admin_user_add():
        username = request.form.get('username', '').strip()
        display_name = request.form.get('display_name', '').strip()
        password = request.form.get('password', '').strip()
        role = request.form.get('role', 'user')

        if not username or not display_name or not password:
            return jsonify({'success': False, 'error': 'Заполните все поля'}), 400

        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'error': 'Пользователь с таким логином уже существует'}), 400

        if role not in ('admin', 'user'):
            role = 'user'

        user = User(username=username, display_name=display_name, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        log_activity('created', 'user', user.id, display_name, f'Роль: {role}')
        db.session.commit()

        return jsonify({'success': True, 'user': {
            'id': user.id, 'username': user.username,
            'display_name': user.display_name, 'role': user.role
        }})

    @app.route('/admin/users/<int:user_id>/update', methods=['POST'])
    @admin_required
    def admin_user_update(user_id):
        user = User.query.get_or_404(user_id)
        display_name = request.form.get('display_name', '').strip()
        role = request.form.get('role', 'user')
        password = request.form.get('password', '').strip()
        is_active = request.form.get('is_active', 'true') == 'true'

        if not display_name:
            return jsonify({'success': False, 'error': 'Имя не может быть пустым'}), 400

        if role not in ('admin', 'user'):
            role = 'user'

        # Не дать лишить себя прав
        if user.id == current_user.id and role != 'admin':
            return jsonify({'success': False, 'error': 'Вы не можете снять с себя роль администратора'}), 400

        user.display_name = display_name
        user.role = role
        user.is_active_user = is_active

        if password:
            user.set_password(password)

        db.session.commit()

        log_activity('updated', 'user', user.id, display_name, f'Роль: {role}')
        db.session.commit()

        return jsonify({'success': True})

    @app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
    @admin_required
    def admin_user_delete(user_id):
        user = User.query.get_or_404(user_id)

        if user.id == current_user.id:
            return jsonify({'success': False, 'error': 'Вы не можете удалить самого себя'}), 400

        name = user.display_name
        db.session.delete(user)
        db.session.commit()

        log_activity('deleted', 'user', user_id, name)
        db.session.commit()        return jsonify({'success': True})
