"""
Общие фикстуры для тестов.
Создают тестовое приложение с БД в памяти (не трогает реальную базу).
"""

import pytest
from app import app as flask_app
from models import db, User, Journal, Issue, Article


@pytest.fixture()
def app():
    """Тестовое приложение с отдельной БД в памяти."""
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'WTF_CSRF_ENABLED': False,  # Отключаем CSRF в тестах
        'SERVER_NAME': 'localhost',
    })

    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """Тестовый HTTP-клиент."""
    return app.test_client()


@pytest.fixture()
def admin_user(app):
    """Создаёт и возвращает администратора."""
    user = User(username='testadmin', display_name='Тест Админ', role='admin')
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def regular_user(app):
    """Создаёт и возвращает обычного пользователя."""
    user = User(username='testuser', display_name='Тест Юзер', role='user')
    user.set_password('password123')
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def sample_data(app):
    """Создаёт журнал, выпуск и статью для тестов."""
    journal = Journal(name='Тестовый журнал', issn='1234-5678')
    db.session.add(journal)
    db.session.flush()

    issue = Issue(number=1, year=2026, journal_id=journal.id)
    db.session.add(issue)
    db.session.flush()

    article = Article(
        title='Тестовая статья',
        authors='Иванов И.И., Петров П.П.',
        issue_id=issue.id,
        payment_received=False,
        has_review=False,
        edited=False,
    )
    db.session.add(article)
    db.session.commit()

    return {'journal': journal, 'issue': issue, 'article': article}


def login(client, username='testadmin', password='password123'):
    """Вспомогательная функция для логина в тестах."""
    return client.post('/login', data={
        'username': username,
        'password': password,
    }, follow_redirects=True)
