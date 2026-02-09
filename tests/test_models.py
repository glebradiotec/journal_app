"""
Тесты моделей — User, Journal, Issue, Article.
Проверяют создание, связи, пароли, свойства.
"""

from models import db, User, Journal, Issue, Article, ArticleAuthor


class TestUser:
    """Тесты модели User."""

    def test_create_user(self, app):
        """Пользователь создаётся с правильными полями."""
        user = User(username='ivan', display_name='Иванов', role='user')
        user.set_password('secret')
        db.session.add(user)
        db.session.commit()

        saved = User.query.filter_by(username='ivan').first()
        assert saved is not None
        assert saved.display_name == 'Иванов'
        assert saved.role == 'user'

    def test_password_hashing(self, app):
        """Пароль хешируется, а не хранится в открытом виде."""
        user = User(username='test', display_name='Test', role='user')
        user.set_password('mypassword')

        assert user.password_hash != 'mypassword'
        assert user.check_password('mypassword') is True
        assert user.check_password('wrongpassword') is False

    def test_is_admin_property(self, app):
        """Свойство is_admin корректно определяет роль."""
        admin = User(username='a', display_name='A', role='admin')
        regular = User(username='b', display_name='B', role='user')

        assert admin.is_admin is True
        assert regular.is_admin is False


class TestArticle:
    """Тесты модели Article."""

    def test_create_article(self, sample_data):
        """Статья создаётся и привязывается к выпуску."""
        article = sample_data['article']
        assert article.id is not None
        assert article.title == 'Тестовая статья'
        assert article.issue_id == sample_data['issue'].id

    def test_default_statuses(self, sample_data):
        """По умолчанию статусы статьи — False."""
        article = sample_data['article']
        assert article.payment_received is False
        assert article.has_review is False
        assert article.edited is False

    def test_article_authors_relationship(self, sample_data):
        """Связь статьи с авторами работает."""
        article = sample_data['article']
        author = ArticleAuthor(
            article_id=article.id,
            full_name='Сидоров С.С.',
            email='sidorov@test.ru',
            order=0,
        )
        db.session.add(author)
        db.session.commit()

        assert len(article.article_authors) == 1
        assert article.article_authors[0].full_name == 'Сидоров С.С.'

    def test_cascade_delete(self, sample_data):
        """При удалении выпуска — статьи удаляются каскадно."""
        issue = sample_data['issue']
        article_id = sample_data['article'].id

        db.session.delete(issue)
        db.session.commit()

        assert Article.query.get(article_id) is None


class TestJournal:
    """Тесты модели Journal."""

    def test_journal_issues_relationship(self, sample_data):
        """У журнала есть список выпусков."""
        journal = sample_data['journal']
        assert len(journal.issues) == 1
        assert journal.issues[0].number == 1
