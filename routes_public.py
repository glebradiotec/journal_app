import os

from flask import (
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    flash,
    send_from_directory,
)
from flask_login import login_user, logout_user, login_required, current_user
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
        # Загружаем статьи с журналами и авторами за один запрос
        articles = (
            Article.query
            .join(Issue)
            .join(Journal)
            .options(
                joinedload(Article.issue).joinedload(Issue.journal),
                subqueryload(Article.article_authors)
            )
            .order_by(Article.id.desc())
            .all()
        )
        return render_template('index.html', articles=articles)

    @app.route("/journals")
    @login_required
    def journals():
        journals = Journal.query.options(
            subqueryload(Journal.issues).subqueryload(Issue.articles)
        ).all()
        return render_template("journals.html", journals=journals)

    @app.route("/journal/<int:journal_id>")
    @login_required
    def journal_page(journal_id):
        journal = Journal.query.get_or_404(journal_id)
        issues = (
            Issue.query
            .filter_by(journal_id=journal_id)
            .options(joinedload(Issue.articles))
            .order_by(Issue.position.asc(), Issue.id.desc())
            .all()
        )
        return render_template('journal.html', journal=journal, issues=issues)

    @app.route("/issue/<int:issue_id>")
    @login_required
    def issue_articles(issue_id):
        issue = Issue.query.options(
            joinedload(Issue.journal)
        ).get_or_404(issue_id)
        journal = issue.journal
        search = request.args.get('search', '').strip()

        all_articles = (
            Article.query
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
    @login_required
    def download_file(filename):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

    @app.route('/uploads/<filename>')
    @login_required
    def uploads_file(filename):
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

