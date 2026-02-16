from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


def _utcnow():
    """UTC now, совместимо с Python 3.12+ (datetime.utcnow deprecated)."""
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(150), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user')  # 'admin' or 'user'
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=_utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'


class Journal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    issn = db.Column(db.String(20))
    is_hidden = db.Column(db.Boolean, default=False)


class Issue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.Integer, nullable=False)
    year = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, default=0)
    journal_id = db.Column(db.Integer, db.ForeignKey('journal.id'), nullable=False, index=True)
    journal = db.relationship('Journal', backref='issues')
    articles = db.relationship('Article', backref='issue', cascade='all, delete-orphan')
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)  # корзина: не None = удалён

    @classmethod
    def visible(cls):
        """Запрос только неудалённых выпусков (не в корзине)."""
        return cls.query.filter(cls.deleted_at.is_(None))


class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Text, nullable=False)
    authors = db.Column(db.Text)  # Строковое поле для ФИО авторов
    payment_received = db.Column(db.Boolean, default=False)
    edited = db.Column(db.Boolean, default=False)
    has_review = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    issue_id = db.Column(db.Integer, db.ForeignKey('issue.id'), nullable=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True, index=True)  # корзина: не None = удалён

    has_expertise_act = db.Column(db.Boolean, default=False)
    expertise_act_file = db.Column(db.String(500))

    @classmethod
    def visible(cls):
        """Запрос только неудалённых статей (не в корзине)."""
        return cls.query.filter(cls.deleted_at.is_(None))

    # Дополнительные поля
    submission_date = db.Column(db.String(50))
    manuscript_file = db.Column(db.String(500))
    review_file = db.Column(db.String(500))
    notes_image = db.Column(db.String(500))
    title_pdf = db.Column(db.String(500))

    # Связь с авторами (отдельная таблица для подробной информации)
    article_authors = db.relationship('ArticleAuthor', backref='article', cascade='all, delete-orphan')


class ArticleAuthor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)

    full_name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(100))
    organization = db.Column(db.String(300))
    degree = db.Column(db.String(100))
    position = db.Column(db.String(200))
    phone = db.Column(db.String(20))

    order = db.Column(db.Integer, default=0)

    def __repr__(self) -> str:
        return self.full_name or f"ArticleAuthor #{self.id}"

    def __str__(self) -> str:
        return self.full_name or ""


class ArticleImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)
    filename = db.Column(db.String(500), nullable=False)
    order = db.Column(db.Integer, default=0)
    
    article = db.relationship('Article', backref=db.backref('images', cascade='all, delete-orphan'))


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(50), nullable=False)  # created, updated, deleted, toggled
    entity_type = db.Column(db.String(50), nullable=False)  # article, issue, journal
    entity_id = db.Column(db.Integer)
    entity_title = db.Column(db.String(300))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_utcnow, index=True)


class ArticleHistory(db.Model):
    """Детальная история изменений статьи."""
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)
    user_name = db.Column(db.String(150))
    action = db.Column(db.String(50), nullable=False)  # created, updated, status, deleted, moved
    changes = db.Column(db.Text)  # JSON: [{"field": "...", "old": "...", "new": "..."}]
    created_at = db.Column(db.DateTime, default=_utcnow, index=True)

    article = db.relationship('Article', backref=db.backref('history', cascade='all, delete-orphan', lazy='dynamic'))


class ArticleComment(db.Model):
    """Комментарии к статье (тред)."""
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)
    user_name = db.Column(db.String(150), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=_utcnow, index=True)

    article = db.relationship('Article', backref=db.backref('comments', cascade='all, delete-orphan', lazy='dynamic'))
