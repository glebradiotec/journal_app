#!/usr/bin/env python
"""Сброс пароля пользователя admin на admin2026 (для локальной разработки)."""
import os
import sys

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, User

with app.app_context():
    user = User.query.filter_by(username='admin').first()
    if not user:
        print('Пользователь admin не найден в БД. Создаём...')
        user = User(username='admin', display_name='Администратор', role='admin', is_active_user=True)
        user.set_password('admin2026')
        db.session.add(user)
        db.session.commit()
        print('Пользователь admin создан с паролем admin2026')
    else:
        user.set_password('admin2026')
        user.is_active_user = True
        db.session.commit()
        print('Пароль пользователя admin установлен на admin2026')
    print('Вход: admin / admin2026')
