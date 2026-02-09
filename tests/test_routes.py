"""
Тесты маршрутов (integration tests).
Проверяют логин, доступ к страницам, создание/удаление данных.
"""

from models import db, Article, Issue
from tests.conftest import login


class TestAuth:
    """Тесты аутентификации."""

    def test_login_page_loads(self, client):
        """Страница логина открывается."""
        resp = client.get('/login')
        assert resp.status_code == 200

    def test_login_success(self, client, admin_user):
        """Успешный логин перенаправляет на загрузочный экран."""
        resp = client.post('/login', data={
            'username': 'testadmin',
            'password': 'password123',
        })
        assert resp.status_code in (302, 200)

    def test_login_wrong_password(self, client, admin_user):
        """Неверный пароль — остаёмся на странице логина."""
        resp = client.post('/login', data={
            'username': 'testadmin',
            'password': 'wrong',
        }, follow_redirects=True)
        assert 'Неверный логин или пароль' in resp.data.decode()

    def test_protected_page_requires_login(self, client):
        """Без логина — редирект на /login."""
        resp = client.get('/')
        assert resp.status_code == 302
        assert '/login' in resp.headers.get('Location', '')

    def test_logout(self, client, admin_user):
        """После выхода — нет доступа к защищённым страницам."""
        login(client)
        client.get('/logout')
        resp = client.get('/')
        assert resp.status_code == 302


class TestPages:
    """Тесты доступности страниц."""

    def test_index_page(self, client, admin_user):
        """Главная страница загружается после логина."""
        login(client)
        resp = client.get('/')
        assert resp.status_code == 200

    def test_journals_page(self, client, admin_user, sample_data):
        """Страница журналов загружается."""
        login(client)
        resp = client.get('/journals')
        assert resp.status_code == 200
        assert 'Тестовый журнал' in resp.data.decode()

    def test_journal_page(self, client, admin_user, sample_data):
        """Страница конкретного журнала загружается."""
        login(client)
        journal_id = sample_data['journal'].id
        resp = client.get(f'/journal/{journal_id}')
        assert resp.status_code == 200

    def test_issue_page(self, client, admin_user, sample_data):
        """Страница выпуска со статьями загружается."""
        login(client)
        issue_id = sample_data['issue'].id
        resp = client.get(f'/issue/{issue_id}')
        assert resp.status_code == 200
        assert 'Тестовая статья' in resp.data.decode()


class TestArticleOperations:
    """Тесты CRUD-операций над статьями."""

    def test_add_article(self, client, admin_user, sample_data):
        """Создание новой статьи через POST."""
        login(client)
        issue_id = sample_data['issue'].id

        resp = client.post(f'/issue/{issue_id}/add-article', data={
            'title': 'Новая статья',
            'notes': 'Заметки',
        }, follow_redirects=True)

        assert resp.status_code == 200
        article = Article.query.filter_by(title='Новая статья').first()
        assert article is not None
        assert article.issue_id == issue_id

    def test_toggle_payment(self, client, admin_user, sample_data):
        """Переключение статуса оплаты."""
        login(client)
        article = sample_data['article']
        assert article.payment_received is False

        resp = client.post(
            f'/article/{article.id}/toggle/payment',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        assert resp.status_code == 200

        db.session.refresh(article)
        assert article.payment_received is True

    def test_toggle_review(self, client, admin_user, sample_data):
        """Переключение статуса рецензии."""
        login(client)
        article = sample_data['article']

        client.post(
            f'/article/{article.id}/toggle/review',
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )

        db.session.refresh(article)
        assert article.has_review is True

    def test_delete_article(self, client, admin_user, sample_data):
        """Удаление статьи."""
        login(client)
        article_id = sample_data['article'].id

        resp = client.post(f'/article/{article_id}/delete', follow_redirects=True)
        assert resp.status_code == 200
        assert Article.query.get(article_id) is None

    def test_update_notes(self, client, admin_user, sample_data):
        """Обновление заметок статьи через AJAX."""
        login(client)
        article = sample_data['article']

        resp = client.post(
            f'/article/{article.id}/update-notes',
            json={'notes': 'Новые заметки'},
        )
        assert resp.status_code == 200

        db.session.refresh(article)
        assert article.notes == 'Новые заметки'


class TestAdminAccess:
    """Тесты доступа к админ-панели."""

    def test_admin_accessible_for_admin(self, client, admin_user):
        """Администратор видит админ-панель."""
        login(client)
        resp = client.get('/admin/dashboard')
        assert resp.status_code == 200

    def test_admin_blocked_for_regular_user(self, client, regular_user):
        """Обычный пользователь не видит админ-панель."""
        login(client, username='testuser')
        resp = client.get('/admin/dashboard')
        # Должен быть редирект (302) — обычного пользователя перенаправляет на главную
        assert resp.status_code == 302
