from pathlib import Path
import os

from flask import Flask
from flask_compress import Compress
from flask_login import LoginManager

from backup import create_backup
from models import db, User, Journal, Article, ArticleImage
from routes_public import register_public_routes
from routes_admin import register_admin_routes


# Создаём папку instance если её нет
instance_path = Path(__file__).parent / 'instance'
instance_path.mkdir(exist_ok=True)


app = Flask(__name__)
Compress(app)  # Gzip/Brotli сжатие ответов (~60-80% меньше трафика)

# Безопасные настройки через переменные окружения
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-insecure-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///journal.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # Кэш статики на 1 год

# Кэширование шаблонов Jinja2
app.jinja_env.auto_reload = False
app.jinja_env.cache = {}

# Конфиг для загрузки файлов статей
UPLOAD_FOLDER = os.path.join(Path(__file__).parent, 'uploads', 'articles')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите в систему.'
login_manager.login_message_category = 'info'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Инициализация БД и регистрация маршрутов
db.init_app(app)
register_public_routes(app)
register_admin_routes(app)




# Jinja2 фильтр для склонения слов
def pluralize_ru(number, form1, form2, form5):
    """
    Склонение слов по числам.
    form1 - для 1 (выпуск)
    form2 - для 2-4 (выпуска)
    form5 - для 5-20 (выпусков)
    """
    n = abs(number) % 100
    if 11 <= n <= 19:
        return form5
    n = n % 10
    if n == 1:
        return form1
    if 2 <= n <= 4:
        return form2
    return form5


app.jinja_env.filters['pluralize_ru'] = pluralize_ru


def migrate_notes_images():
    """Миграция notes_image в новую таблицу ArticleImage."""
    with app.app_context():
        articles_with_images = Article.query.filter(Article.notes_image.isnot(None)).all()
        migrated = 0
        for article in articles_with_images:
            # Проверяем, не мигрировано ли уже
            existing = ArticleImage.query.filter_by(
                article_id=article.id, 
                filename=article.notes_image
            ).first()
            if not existing:
                img = ArticleImage(
                    article_id=article.id,
                    filename=article.notes_image,
                    order=0
                )
                db.session.add(img)
                migrated += 1
        if migrated > 0:
            db.session.commit()
            print(f"Migrated {migrated} images to new table")


def init_journals():
    """Создание таблиц и начальное заполнение журналами."""
    with app.app_context():
        db.create_all()
        migrate_notes_images()
        print(f"Журналов в БД: {Journal.query.count()}")
        if Journal.query.count() == 0:
            print("Создаём 14 реальных журналов...")
            journals_data = [
                ("Антенны", "0320-9601"),
                ("Биомедицинская радиоэлектроника", "2074-4118"),
                ("Динамика сложных систем - XXI век", "2220-3510"),
                ("Информационно-измерительные и управляющие системы", "1996-5981"),
                ("Нанотехнологии: разработка, применение - XXI век", "2075-1417"),
                ("Наукоемкие технологии", "2220-3508"),
                ("Нелинейный мир", "2073-4407"),
                ("Нейрокомпьютеры: разработка, применение", "2073-0565"),
                ("Радиотехника", "0033-8494"),
                ("Системы высокой доступности", "2072-0505"),
                ("Спутниковые системы связи и вещания", "2072-8735"),
                ("Технологии живых систем", "1998-4927"),
                ("Успехи современной радиоэлектроники", "2070-0784"),
                ("Электромагнитные волны и электронные системы", "1560-4128"),
            ]
            for name, issn in journals_data:
                journal = Journal(name=name, issn=issn)
                db.session.add(journal)
            db.session.commit()
            print("14 journals added!")
        else:
            print("Журналы уже есть в БД")


def init_users():
    """Создание начальных пользователей если их нет."""
    with app.app_context():
        if User.query.count() == 0:
            print("Создаём пользователей...")
            users_data = [
                # (username, display_name, password, role)
                ('admin', 'Администратор', 'admin2026', 'admin'),
                ('editor', 'Редактор', 'editor2026', 'admin'),
                ('ivanov', 'Иванов И.И.', 'user2026', 'user'),
                ('petrova', 'Петрова А.С.', 'user2026', 'user'),
                ('sidorov', 'Сидоров В.М.', 'user2026', 'user'),
                ('kozlova', 'Козлова Е.Д.', 'user2026', 'user'),
            ]
            for username, display_name, password, role in users_data:
                user = User(username=username, display_name=display_name, role=role)
                user.set_password(password)
                db.session.add(user)
            db.session.commit()
            print("6 пользователей создано (2 админа + 4 сотрудника)")
        else:
            print(f"Пользователей в БД: {User.query.count()}")


if __name__ == "__main__":
    print("Starting app...")
    create_backup()
    init_journals()
    init_users()
    print("Сервер стартует...")
    app.run(debug=True)
