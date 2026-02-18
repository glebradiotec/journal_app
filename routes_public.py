import os

from flask import (
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    send_from_directory,
    current_app,
    Response,
    abort,
)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload, subqueryload
from models import db, User, Journal, Issue, Article, ArticleAuthor


def normalize_text(text):
    """Нормализация текста: убираем регистр и заменяем похожие символы."""
    if not text:
        return ""
    text = text.lower()
    text = text.replace('ё', 'е')
    return text


def register_public_routes(app):
    # ==================== AUTH ====================
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for('index'))
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.is_active_user and user.check_password(password):
                login_user(user, remember=True)
                next_page = request.args.get('next') or url_for('index')
                return redirect(url_for('loading', next=next_page))
            flash('Неверный логин или пароль', 'error')
        return render_template('login.html')

    @app.route('/loading')
    @login_required
    def loading():
        next_url = request.args.get('next') or url_for('index')
        return render_template('loading.html', next_url=next_url)

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('Вы вышли из системы', 'info')
        return redirect(url_for('login'))

    # ==================== PAGES ====================
    @app.route("/")
    @login_required
    def index():
        # Статьи загружаются через AJAX-поиск, а не при открытии страницы
        total_articles = Article.visible().count()
        total_authors = db.session.query(db.func.count(db.distinct(ArticleAuthor.full_name))).scalar()
        # Журналы с выпусками 2026 для быстрого доступа (только нужные данные, без загрузки всех выпусков)
        journals = Journal.query.filter(
            db.or_(Journal.is_hidden == False, Journal.is_hidden.is_(None))
        ).order_by(Journal.name).all()
        journal_ids = [j.id for j in journals]
        issues_2026 = (
            Issue.visible()
            .filter(Issue.journal_id.in_(journal_ids), Issue.year == 2026)
            .order_by(Issue.journal_id, Issue.position, Issue.number)
            .all()
        )
        by_journal = {}
        for j in journals:
            by_journal[j.id] = []
        for i in issues_2026:
            by_journal[i.journal_id].append({'id': i.id, 'number': i.number})
        journals_data = [
            {'id': j.id, 'name': j.name, 'issues_2026': by_journal[j.id]}
            for j in journals
        ]
        return render_template(
            'index.html',
            total_articles=total_articles,
            total_authors=total_authors,
            journals_quick=journals_data,
        )

    @app.route('/api/search')
    @login_required
    def api_search():
        """Серверный поиск статей по названию, авторам, журналу."""
        q = request.args.get('q', '').strip()
        limit = min(int(request.args.get('limit', 50)), 200)

        if not q or len(q) < 2:
            return jsonify({'results': [], 'total': 0})

        search_term = f'%{q}%'
        search_norm = f'%{q.lower().replace("ё", "е")}%'

        # Ищем по названию статьи, авторам (строка), авторам (таблица), журналу
        # Используем подзапрос для авторов
        author_article_ids = (
            db.session.query(ArticleAuthor.article_id)
            .filter(db.func.lower(ArticleAuthor.full_name).like(search_norm))
            .subquery()
        )

        query = (
            Article.visible()
            .join(Issue)
            .join(Journal)
            .options(
                joinedload(Article.issue).joinedload(Issue.journal),
                subqueryload(Article.article_authors)
            )
            .filter(
                db.or_(
                    db.func.lower(Article.title).like(search_norm),
                    db.func.lower(Article.authors).like(search_norm),
                    db.func.lower(Journal.name).like(search_norm),
                    Article.id.in_(author_article_ids),
                )
            )
            .order_by(Article.id.desc())
        )

        total = query.count()
        articles = query.limit(limit).all()

        results = []
        for art in articles:
            authors_list = []
            if art.article_authors:
                for a in sorted(art.article_authors, key=lambda x: x.order):
                    authors_list.append(a.full_name)

            results.append({
                'id': art.id,
                'title': art.title,
                'authors': authors_list if authors_list else [art.authors or ''],
                'journal': art.issue.journal.name if art.issue and art.issue.journal else '',
                'issue_id': art.issue.id if art.issue else None,
                'issue_number': art.issue.number if art.issue else None,
                'issue_year': art.issue.year if art.issue else None,
                'payment_received': art.payment_received,
                'has_review': art.has_review,
                'edited': art.edited,
            })

        return jsonify({'results': results, 'total': total})

    @app.route("/journals")
    @login_required
    def journals():
        journals = Journal.query.options(
            subqueryload(Journal.issues)
        ).filter(db.or_(Journal.is_hidden == False, Journal.is_hidden.is_(None))).all()
        # Подсчитываем статьи через запрос, а не загрузку всех объектов
        article_counts = dict(
            db.session.query(Issue.journal_id, db.func.count(Article.id))
            .join(Article)
            .group_by(Issue.journal_id)
            .all()
        )
        return render_template("journals.html", journals=journals, article_counts=article_counts)

    @app.route("/journal/<int:journal_id>")
    @login_required
    def journal_page(journal_id):
        from datetime import datetime
        journal = Journal.query.get_or_404(journal_id)
        default_year = datetime.now().year

        # Выпуск для статей без номера (number=0, year=0) — привязка к выпуску позже
        solyanka_issue = Issue.visible().filter_by(
            journal_id=journal_id, number=0, year=0
        ).first()
        if not solyanka_issue:
            solyanka_issue = Issue(
                number=0, year=0, journal_id=journal_id, position=-1
            )
            db.session.add(solyanka_issue)
            db.session.commit()
        solyanka_article_count = Article.visible().filter_by(issue_id=solyanka_issue.id).count()

        # Годы только обычных выпусков (без блока «без номера»)
        years = [
            row[0] for row in
            Issue.visible()
            .filter_by(journal_id=journal_id)
            .filter(Issue.year != 0)
            .with_entities(Issue.year)
            .distinct()
            .order_by(Issue.year.desc())
            .all()
        ]
        # Выпуски года по умолчанию (без блока «без номера»)
        rows = (
            db.session.query(Issue, func.count(Article.id).label('article_count'))
            .select_from(Issue)
            .outerjoin(Article, Article.issue_id == Issue.id)
            .filter(
                Issue.deleted_at.is_(None),
                Issue.journal_id == journal_id,
                Issue.year == default_year,
                Issue.year != 0,
            )
            .group_by(Issue.id)
            .order_by(Issue.position.asc(), Issue.id.desc())
            .all()
        )
        issues_default = [{'issue': issue, 'article_count': count} for issue, count in rows]
        # Количество обычных выпусков (без блока «без номера»)
        total_issues = (
            Issue.visible()
            .filter_by(journal_id=journal_id)
            .filter(Issue.year != 0)
            .count()
        )
        total_articles = Article.visible().join(Issue).filter(Issue.journal_id == journal_id).count()
        last_issue = (
            Issue.visible()
            .filter_by(journal_id=journal_id)
            .filter(Issue.year != 0)
            .order_by(Issue.position.asc(), Issue.id.desc())
            .first()
        )
        issue_count_by_year = dict(
            Issue.visible()
            .filter_by(journal_id=journal_id)
            .filter(Issue.year != 0)
            .with_entities(Issue.year, func.count(Issue.id))
            .group_by(Issue.year)
            .all()
        )
        return render_template(
            'journal.html',
            journal=journal,
            years=years,
            default_year=default_year,
            issues_default=issues_default,
            total_issues=total_issues,
            total_articles=total_articles,
            last_issue=last_issue,
            issue_count_by_year=issue_count_by_year,
            solyanka_issue=solyanka_issue,
            solyanka_article_count=solyanka_article_count,
        )

    @app.route("/api/journal/<int:journal_id>/issues")
    @login_required
    def api_journal_issues(journal_id):
        """Возвращает выпуски журнала за указанный год (для ленивой подгрузки по годам)."""
        year = request.args.get('year', type=int)
        if year is None:
            return jsonify({'error': 'year required'}), 400
        Journal.query.get_or_404(journal_id)
        rows = (
            db.session.query(Issue, func.count(Article.id).label('article_count'))
            .select_from(Issue)
            .outerjoin(Article, Article.issue_id == Issue.id)
            .filter(Issue.deleted_at.is_(None), Issue.journal_id == journal_id, Issue.year == year)
            .group_by(Issue.id)
            .order_by(Issue.position.asc(), Issue.id.desc())
            .all()
        )
        issues = [
            {'id': issue.id, 'number': issue.number, 'year': issue.year, 'article_count': count}
            for issue, count in rows
        ]
        return jsonify({'issues': issues})

    @app.route("/issue/<int:issue_id>")
    @login_required
    def issue_articles(issue_id):
        issue = Issue.visible().options(
            joinedload(Issue.journal)
        ).filter_by(id=issue_id).first_or_404()
        journal = issue.journal
        search = request.args.get('search', '').strip()

        all_articles = (
            Article.visible()
            .filter_by(issue_id=issue_id)
            .options(
                joinedload(Article.article_authors),
                joinedload(Article.images)
            )
            .all()
        )
        
        if search:
            search_term = normalize_text(search)
            articles = []
            
            for article in all_articles:
                # Проверяем название статьи
                if search_term in normalize_text(article.title or ''):
                    articles.append(article)
                    continue
                
                # Проверяем строковое поле авторов
                if search_term in normalize_text(article.authors or ''):
                    articles.append(article)
                    continue
                
                # Проверяем авторов из таблицы ArticleAuthor
                for author in article.article_authors:
                    if search_term in normalize_text(author.full_name or ''):
                        articles.append(article)
                        break
        else:
            articles = all_articles
        
        return render_template('articles.html', issue=issue, journal=journal, articles=articles, search=search)

    @app.route('/author/<path:author_name>')
    @login_required
    def author_page(author_name):
        """Страница автора со списком всех его статей."""
        # Находим все записи автора (по имени, регистронезависимо)
        author_records = (
            ArticleAuthor.query
            .filter(db.func.lower(ArticleAuthor.full_name) == author_name.lower())
            .all()
        )
        if not author_records:
            from flask import abort
            abort(404)

        # Берём данные автора из первой записи с максимумом информации
        author_info = {
            'name': author_records[0].full_name,
            'email': None,
            'organization': None,
            'degree': None,
            'position': None,
            'phone': None,
        }
        for rec in author_records:
            if rec.email and not author_info['email']:
                author_info['email'] = rec.email
            if rec.organization and not author_info['organization']:
                author_info['organization'] = rec.organization
            if rec.degree and not author_info['degree']:
                author_info['degree'] = rec.degree
            if rec.position and not author_info['position']:
                author_info['position'] = rec.position
            if rec.phone and not author_info['phone']:
                author_info['phone'] = rec.phone

        # Получаем все статьи автора
        article_ids = [r.article_id for r in author_records]
        articles = (
            Article.visible()
            .filter(Article.id.in_(article_ids))
            .options(
                joinedload(Article.issue).joinedload(Issue.journal),
                subqueryload(Article.article_authors)
            )
            .order_by(Article.id.desc())
            .all()
        )

        return render_template('author.html', author=author_info, articles=articles)

    @app.route('/api/journals-and-issues')
    @login_required
    def get_journals_and_issues():
        journals = Journal.query.options(
            subqueryload(Journal.issues)
        ).all()
        data = []
        for journal in journals:
            data.append({
                'id': journal.id,
                'name': journal.name,
                'issues': [{'id': i.id, 'number': i.number, 'year': i.year} for i in journal.issues]
            })
        return jsonify(data)

    @app.route('/download/<filename>')
    @app.route('/uploads/<filename>')
    @login_required
    def download_file(filename):
        upload_folder = current_app.config['UPLOAD_FOLDER']
        # Безопасность: только имя файла, без path traversal
        safe_name = os.path.basename(filename)
        filepath = os.path.join(upload_folder, safe_name)
        if not os.path.isfile(filepath):
            abort(404)

        is_download = request.path.startswith('/download/')

        # X-Accel-Redirect: Nginx отдаёт файл напрямую (быстрее, не блокирует воркеры)
        if os.environ.get('USE_X_ACCEL_REDIRECT') == '1':
            # Путь для Nginx internal location
            internal_path = '/internal-uploads/' + safe_name
            response = Response(status=200)
            response.headers['X-Accel-Redirect'] = internal_path
            response.headers['X-Accel-Charset'] = 'utf-8'
            if is_download:
                # Скачивание, а не отображение в браузере
                response.headers['Content-Disposition'] = f'attachment; filename="{safe_name}"'
            return response

        return send_from_directory(
            upload_folder, safe_name,
            as_attachment=is_download,
        )

