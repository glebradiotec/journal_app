# Кабинет автора: подача и редактирование статей (роль author).
# Доступ только к /author/*; главная и админка для авторов закрыты.

import os
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
    abort,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from sqlalchemy.orm import joinedload
from models import db, User, Journal, Issue, Article, ArticleAuthor, ArticleImage

ALLOWED_EXTENSIONS = {
    'txt', 'pdf', 'doc', 'docx', 'odt', 'rtf', 'xls', 'xlsx', 'csv',
    'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg', 'tiff', 'tif',
    'zip', 'rar', '7z', 'tar', 'gz', 'html', 'htm', 'xml', 'json', 'md', 'tex', 'djvu', 'epub', 'fb2',
}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_file(file, upload_folder):
    if file and file.filename and allowed_file(file.filename):
        name = datetime.now().strftime('%Y%m%d_%H%M%S_') + secure_filename(file.filename)
        file.save(os.path.join(upload_folder, name))
        return name
    return None


def _get_or_create_author_holding_issue(journal_id):
    """Очередь авторских подач: отдельный выпуск (number=-1, year=0). Не показывается в «Статьи без номера» — только во вкладке «От авторов» до назначения сотрудником."""
    issue = Issue.visible().filter_by(journal_id=journal_id, number=-1, year=0).first()
    if not issue:
        issue = Issue(number=-1, year=0, journal_id=journal_id, position=-2)
        db.session.add(issue)
        db.session.commit()
    return issue


def _process_author_form(article):
    """Обрабатывает авторов из формы (author_name[], author_email[], ...)."""
    names = request.form.getlist('author_name[]')
    emails = request.form.getlist('author_email[]')
    orgs = request.form.getlist('author_organization[]')
    degrees = request.form.getlist('author_degree[]')
    positions = request.form.getlist('author_position[]')
    phones = request.form.getlist('author_phone[]')
    ArticleAuthor.query.filter_by(article_id=article.id).delete()
    parts = []
    for i, name in enumerate(names):
        if not (name and name.strip()):
            continue
        name = name.strip()
        parts.append(name)
        db.session.add(ArticleAuthor(
            article_id=article.id,
            full_name=name,
            email=emails[i].strip() if i < len(emails) else '',
            organization=orgs[i].strip() if i < len(orgs) else '',
            degree=degrees[i].strip() if i < len(degrees) else '',
            position=positions[i].strip() if i < len(positions) else '',
            phone=phones[i].strip() if i < len(phones) else '',
            order=i,
        ))
    return ", ".join(parts) if parts else None


def _process_author_uploads(article, upload_folder):
    """Загрузка файлов статьи в кабинете автора."""
    for field in ('manuscript_file', 'review_file', 'title_pdf', 'expertise_act_file'):
        f = request.files.get(field)
        name = _save_file(f, upload_folder)
        if name:
            setattr(article, field, name)
            if field == 'expertise_act_file':
                article.has_expertise_act = True
    for f in request.files.getlist('article_images') or []:
        name = _save_file(f, upload_folder)
        if name:
            order = max([img.order for img in article.images], default=-1) + 1
            db.session.add(ArticleImage(article_id=article.id, filename=name, order=order))


author_bp = Blueprint('author', __name__, url_prefix='/author')


def author_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('login'))
        if not getattr(current_user, 'role', None) == 'author':
            flash('Доступ только для авторов', 'error')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


@author_bp.route('/')
@login_required
@author_required
def index():
    """Список моих подач."""
    items = (
        Article.visible()
        .filter_by(submitted_by_user_id=current_user.id)
        .join(Issue)
        .join(Journal)
        .options(
            joinedload(Article.issue).joinedload(Issue.journal),
        )
        .order_by(Article.id.desc())
        .all()
    )
    return render_template('author/index.html', submissions=items)


@author_bp.route('/new', methods=['GET', 'POST'])
@login_required
@author_required
def new():
    """Новая подача: выбор журнала, название, авторы, файлы."""
    journals = Journal.query.filter(
        db.or_(Journal.is_hidden == False, Journal.is_hidden.is_(None))
    ).order_by(Journal.name).all()

    if request.method == 'POST':
        journal_id = request.form.get('journal_id', type=int)
        title = (request.form.get('title') or '').strip()
        if not journal_id or not title:
            flash('Укажите журнал и название статьи', 'error')
            return render_template('author/form.html', journals=journals, article=None)

        journal = Journal.query.get(journal_id)
        if not journal:
            flash('Журнал не найден', 'error')
            return render_template('author/form.html', journals=journals, article=None)

        holding = _get_or_create_author_holding_issue(journal_id)
        article = Article(
            title=title,
            notes=(request.form.get('notes') or '').strip() or None,
            issue_id=holding.id,
            submitted_by_user_id=current_user.id,
        )
        day = request.form.get('submission_day', '')
        month = request.form.get('submission_month', '')
        year = request.form.get('submission_year', '')
        if day and month and year:
            article.submission_date = f"{day}.{month}.{year}"

        upload_folder = current_app.config['UPLOAD_FOLDER']
        _process_author_uploads(article, upload_folder)
        db.session.add(article)
        db.session.flush()
        article.authors = _process_author_form(article)
        db.session.commit()
        flash('Статья подана в журнал «{}»'.format(journal.name), 'success')
        return redirect(url_for('author.index'))

    return render_template('author/form.html', journals=journals, article=None)


@author_bp.route('/<int:article_id>/', methods=['GET', 'POST'])
@login_required
@author_required
def edit(article_id):
    """Редактирование своей подачи: дополнение полей, догрузка файлов."""
    article = (
        Article.visible()
        .options(
            joinedload(Article.article_authors),
            joinedload(Article.images),
            joinedload(Article.issue).joinedload(Issue.journal),
        )
        .filter_by(id=article_id, submitted_by_user_id=current_user.id)
        .first()
    )
    if not article:
        abort(404)
    issue = article.issue
    journal = issue.journal if issue else None
    journals = [journal] if journal else Journal.query.order_by(Journal.name).all()

    if request.method == 'POST':
        article.title = (request.form.get('title') or article.title or '').strip()
        article.notes = (request.form.get('notes') or '').strip() or None
        day = request.form.get('submission_day', '')
        month = request.form.get('submission_month', '')
        year = request.form.get('submission_year', '')
        if day and month and year:
            article.submission_date = f"{day}.{month}.{year}"

        upload_folder = current_app.config['UPLOAD_FOLDER']
        # Удаление отмеченных файлов
        if request.form.get('delete_manuscript'):
            article.manuscript_file = None
        if request.form.get('delete_review'):
            article.review_file = None
        if request.form.get('delete_title_pdf'):
            article.title_pdf = None
        if request.form.get('delete_expertise_act'):
            article.expertise_act_file = None
            article.has_expertise_act = False
        for img_id in request.form.getlist('delete_image[]') or []:
            img = ArticleImage.query.get(int(img_id))
            if img and img.article_id == article.id:
                db.session.delete(img)
        _process_author_uploads(article, upload_folder)
        article.authors = _process_author_form(article)
        db.session.commit()
        flash('Изменения сохранены', 'success')
        return redirect(url_for('author.index'))

    return render_template('author/form.html', article=article, journal=journal, journals=journals)
