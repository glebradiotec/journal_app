"""Переносит авторские подачи из выпуска «без номера» (0,0) в очередь авторских подач (-1,0). Запустить один раз: python migrate_author_holding.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, Issue, Article

with app.app_context():
    # Выпуски «без номера» (0, 0) по журналам
    zero_issues = Issue.visible().filter_by(number=0, year=0).all()
    moved = 0
    for zero in zero_issues:
        # Авторские подачи в этом выпуске
        articles = Article.visible().filter_by(
            issue_id=zero.id
        ).filter(Article.submitted_by_user_id.isnot(None)).all()
        if not articles:
            continue
        # Создаём или находим очередь авторских подач (-1, 0)
        holding = Issue.visible().filter_by(
            journal_id=zero.journal_id, number=-1, year=0
        ).first()
        if not holding:
            holding = Issue(
                number=-1, year=0, journal_id=zero.journal_id, position=-2
            )
            db.session.add(holding)
            db.session.flush()
        for art in articles:
            art.issue_id = holding.id
            moved += 1
    if moved:
        db.session.commit()
        print(f"Перенесено авторских подач в очередь: {moved}")
    else:
        print("Нет авторских подач в выпусках (0,0) для переноса.")
print("Готово.")
