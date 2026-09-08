"""Модели модуля «Публикация на сайте» — черновики выпусков/статей, которые в итоге пишутся
напрямую в БД сайта radiotec.ru (см. site_db.py) и используются для генерации CrossRef/eLibrary
XML (export_crossref.py/export_elibrary.py).

Намеренно ОТДЕЛЬНЫЕ таблицы от Article/Issue/ArticleAuthor (models.py) — это самостоятельный
рабочий процесс, не пересекающийся с существующим учётом статей.
"""
from models import db, _utcnow


class PubIssue(db.Model):
    """Черновик выпуска сайта (соответствует таблице nomera на боевом сайте)."""
    id = db.Column(db.Integer, primary_key=True)

    issn = db.Column(db.String(20), nullable=False)
    journal_name = db.Column(db.String(200))  # для удобства отображения, не источник истины
    year = db.Column(db.Integer, nullable=False)
    number = db.Column(db.String(20), nullable=False)  # "5" или "1(1)" — сайт допускает часть в скобках
    alt_number = db.Column(db.String(20))
    part = db.Column(db.String(20))
    pages = db.Column(db.String(50))
    elibrary_titleid = db.Column(db.String(20))

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=_utcnow)

    # Заполняется после успешной отправки на сайт
    site_num_id = db.Column(db.Integer, nullable=True)
    pushed_at = db.Column(db.DateTime, nullable=True)

    articles = db.relationship('PubArticle', backref='issue', cascade='all, delete-orphan',
                                order_by='PubArticle.id')

    @property
    def is_pushed(self):
        return self.pushed_at is not None


class PubArticle(db.Model):
    """Черновик статьи (соответствует таблице articles на боевом сайте)."""
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('pub_issue.id'), nullable=False, index=True)

    section_ru = db.Column(db.String(300))
    section_en = db.Column(db.String(300))
    pages = db.Column(db.String(20))
    art_type = db.Column(db.String(10), default='RAR')
    udk = db.Column(db.String(50))
    doi = db.Column(db.String(100))
    price = db.Column(db.Integer)

    title_ru = db.Column(db.Text, nullable=False)
    title_en = db.Column(db.Text)
    abstract_ru = db.Column(db.Text)
    abstract_en = db.Column(db.Text)
    fulltext_ru = db.Column(db.Text)
    keywords_ru = db.Column(db.Text)  # через запятую
    keywords_en = db.Column(db.Text)
    references_text = db.Column(db.Text)  # по одной ссылке на строку
    funding_ru = db.Column(db.Text)
    funding_en = db.Column(db.Text)

    date_received = db.Column(db.String(20))
    date_accepted = db.Column(db.String(20))
    date_published = db.Column(db.String(20))

    manuscript_file = db.Column(db.String(500))

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    created_at = db.Column(db.DateTime, default=_utcnow)

    site_art_id = db.Column(db.Integer, nullable=True)
    pushed_at = db.Column(db.DateTime, nullable=True)

    authors = db.relationship('PubAuthor', backref='article', cascade='all, delete-orphan',
                               order_by='PubAuthor.order')

    @property
    def is_pushed(self):
        return self.pushed_at is not None

    @property
    def keywords_ru_list(self):
        return [k.strip() for k in (self.keywords_ru or '').split(',') if k.strip()]

    @property
    def keywords_en_list(self):
        return [k.strip() for k in (self.keywords_en or '').split(',') if k.strip()]

    @property
    def references_list(self):
        return [r.strip() for r in (self.references_text or '').split('\n') if r.strip()]


class PubAuthor(db.Model):
    """Автор черновика статьи."""
    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(db.Integer, db.ForeignKey('pub_article.id'), nullable=False, index=True)

    order = db.Column(db.Integer, default=0)

    surname_ru = db.Column(db.String(150))
    initials_ru = db.Column(db.String(20))
    surname_en = db.Column(db.String(150))
    initials_en = db.Column(db.String(20))

    org_ru = db.Column(db.String(300))
    org_en = db.Column(db.String(300))
    town_ru = db.Column(db.String(100))
    town_en = db.Column(db.String(100))
    country_ru = db.Column(db.String(100), default='Россия')
    country_en = db.Column(db.String(100), default='Russia')
    position_ru = db.Column(db.String(200))
    position_en = db.Column(db.String(200))

    email = db.Column(db.String(150))
    orcid = db.Column(db.String(50))
    spin = db.Column(db.String(20))
    researcherid = db.Column(db.String(50))
    scopusid = db.Column(db.String(50))

    corresponding = db.Column(db.Boolean, default=False)
