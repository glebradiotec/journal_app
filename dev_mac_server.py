"""Локальный запуск для проверки интерфейса на macOS. Только для разработки.

Отличия от dev_test_server.py:
  - порт 5055 (5000 на macOS занят системным AirPlay);
  - своя БД instance/dev_mac.db — копия test_ui.db, создаётся при первом запуске;
  - тестовый администратор DEV_USER / DEV_PASSWORD ниже: системный Python 3.9 на macOS собран без
    hashlib.scrypt, поэтому пароли обычных пользователей (scrypt) здесь проверить нельзя, а у
    этого пользователя хэш pbkdf2. Учётная запись существует только в локальной dev_mac.db;
  - ключ Метафоры и БД сайта отключены: из локального интерфейса ничего не уходит ни в РЦНИ,
    ни на radiotec.ru, даже если нажать «Отправить»;
  - вместо Метафоры — имитация в памяти процесса (_install_fake_metafora): принимает XML и PDF,
    «обрабатывает» файл со второго опроса, хранит подписи. Нужна, чтобы пройти в браузере все
    шаги страницы выпуска. После перезапуска сервера её память пуста.
"""
import os
import shutil
from pathlib import Path

DEV_USER = "dev"
DEV_PASSWORD = "dev-local-only"
PORT = 5055

BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)
instance = BASE_DIR / "instance"
instance.mkdir(exist_ok=True)
db_path = instance / "dev_mac.db"
if not db_path.exists() and (instance / "test_ui.db").exists():
    shutil.copy(instance / "test_ui.db", db_path)

os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
os.environ["METAFORA_API_KEY"] = ""      # load_dotenv не перезаписывает уже заданные переменные
os.environ["SITE_DATABASE_URL"] = ""

from werkzeug.security import generate_password_hash  # noqa: E402

from app import app  # noqa: E402
from models import User, db  # noqa: E402

with app.app_context():
    db.create_all()
    user = User.query.filter_by(username=DEV_USER).first()
    if not user:
        user = User(username=DEV_USER, display_name="Разработка", role="admin")
        db.session.add(user)
    user.password_hash = generate_password_hash(DEV_PASSWORD, method="pbkdf2:sha256")
    db.session.commit()

def _install_fake_metafora():
    import uuid
    from datetime import datetime, timezone

    import metafora_api
    import metafora_check

    files, pubs = {}, {}

    def iso(d):
        return d or None

    def upload(xml, filename="issue.xml"):
        uid = str(uuid.uuid4())
        arts = []
        for a in metafora_check.articles_from_xml(xml):
            auid = str(uuid.uuid4())
            pubs[auid] = {"data": a, "has_pdf": False, "signed_at": None}
            arts.append(auid)
        files[uid] = {"articles": arts, "polls": 0}
        return uid

    def file_status(uid):
        f = files.get(uid)
        if f is None:
            raise metafora_api.MetaforaError("Метафора (имитация): файл не найден", status=404)
        f["polls"] += 1
        code = 3 if f["polls"] >= 2 else 2
        return {"code": code, "text": metafora_api.FILE_STATUS_TEXT[code], "articles": f["articles"] if code == 3 else [],
                "raw": {}}

    def locale(a, en):
        sfx = "_en" if en else ""
        return {
            "title": a["title" + sfx], "abstract": a["abstract" + sfx], "doi": a["doi"], "udk": a["udk"],
            "fpage": a["fpage"], "lpage": a["lpage"], "date_received": iso(a["received"]), "date_accepted": iso(a["accepted"]),
            "keywords": [{"value": k} for k in a["keywords" + sfx]],
            "references": [{"text": r} if r else None for r in a["refs" + sfx]],
            "authors": [{"surname": x["surname" + sfx], "orcid": x["orcid"], "spin": x["spin"],
                         "aff": [{"institution": x["org" + sfx]}]} for x in a["authors"]],
        }

    def get_publication(uid):
        if uid not in pubs:
            raise metafora_api.MetaforaError("Метафора (имитация): публикация не найдена", status=404)
        return {"RUS": locale(pubs[uid]["data"], False), "ENG": locale(pubs[uid]["data"], True)}

    class Resp:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    def request(method, path, **kw):
        if "/publications/doi/" in path:
            doi = path.rsplit("/doi/", 1)[-1]
            for p in pubs.values():
                if p["data"]["doi"] == doi:
                    return Resp({"data": {"has_pdf": p["has_pdf"], "signed_at": p["signed_at"]}})
        raise metafora_api.MetaforaError("Метафора (имитация): не найдено", status=404)

    def upload_pdf(uid, data, filename="a.pdf"):
        pubs[uid]["has_pdf"] = True
        return {"data": {"has_pdf": True}}

    def sign(uid):
        pubs[uid]["signed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {}

    def delete(uid):
        for auid in files.pop(uid, {}).get("articles", []):
            pubs.pop(auid, None)

    metafora_api.upload_journal_xml = upload
    metafora_api.file_status = file_status
    metafora_api.get_publication = get_publication
    metafora_api._request = request
    metafora_api.upload_publication_pdf = upload_pdf
    metafora_api.sign_publication = sign
    metafora_api.delete_file = delete
    metafora_api.publications_status = lambda uids: {u: pubs[u]["signed_at"] for u in uids if u in pubs}
    os.environ["METAFORA_API_KEY"] = "fake-local"   # только чтобы страница считала ключ заданным


if __name__ == "__main__":
    _install_fake_metafora()
    app.jinja_env.auto_reload = True
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    print(f"http://127.0.0.1:{PORT}  (вход: {DEV_USER}, пароль — в этом файле)")
    app.run(host="127.0.0.1", port=PORT, debug=False)
