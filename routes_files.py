"""Раздел «Файлы»: просмотр файлового сервера издательства (SFTP, только чтение) и загрузка выпуска оттуда в «Публикацию».

Доступ — всем сотрудникам (как у «Публикации»), внешним авторам закрыт. Ничего не пишется на файловый сервер и не удаляется.
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import Response, abort, jsonify, redirect, render_template, request, send_file, stream_with_context, url_for

import file_marks
import file_store as fs
from routes_publish_hub import publish_required

MSK = timezone(timedelta(hours=3))
KINDS = {".pdf": "PDF", ".doc": "DOC", ".docx": "DOC", ".rtf": "DOC", ".xml": "XML", ".zip": "ZIP", ".png": "IMG", ".jpg": "IMG",
         ".jpeg": "IMG", ".xls": "XLS", ".xlsx": "XLS", ".cdr": "CDR", ".txt": "TXT"}


def fs_time(mtime):
    """'20261019090000' (UTC) -> '19.10.2026 12:00' (Москва)."""
    try:
        return datetime.strptime(mtime[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).astimezone(MSK).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return ""


def fs_size(n):
    n = int(n or 0)
    if n < 1024:
        return f"{n} Б"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} КБ"
    return f"{n / 1048576:.1f} МБ"


def _crumbs(rel):
    out, acc = [], ""
    for part in [p for p in rel.split("/") if p]:
        acc = f"{acc}/{part}" if acc else part
        out.append((part, url_for("files_browse", rel=acc)))
    return out


def register_files_routes(app):
    app.jinja_env.filters["fs_time"] = fs_time
    app.jinja_env.filters["fs_size"] = fs_size

    def _state(mark):
        label, tone = file_marks.LABELS[mark[0]]
        return {"label": label, "tone": tone, "url": url_for("admin_publish_issue_detail", issue_id=mark[1]) if mark[1] else None}

    def _summary(c):
        parts = []
        if c[file_marks.MISSING]:
            parts.append(f"нет в разделе: {c[file_marks.MISSING]}")
        if c[file_marks.LOADED] + c[file_marks.PUSHED]:
            parts.append(f"в разделе: {c[file_marks.LOADED] + c[file_marks.PUSHED]}")
        if c[file_marks.NO_JOURNAL]:
            parts.append("журнал не заведён в разделе")
        return " · ".join(parts)

    def _root_rows():
        """Только журналы (папки со свежим годом): название, сколько выпусков и сколько из них ещё не в разделе."""
        index = file_marks.load_index()
        rows = []
        for j in fs.journals():
            st = file_marks.marks(index, j["name"], j["year"], j["issues"])
            c = file_marks.counts(st)
            rows.append({"name": j["name"], "is_dir": True, "size": 0, "mtime": j["mtime"], "new": fs.recent(j["mtime"]), "kind": "",
                         "url": url_for("files_browse", rel=f"{j['name']}/{j['year']}"), "download": None, "zip": None, "viewable": False,
                         "sub": f"{j['year']} · выпусков: {len(j['issues'])}" + (" · " + _summary(c) if st else ""),
                         "missing": c[file_marks.MISSING], "nojournal": c[file_marks.NO_JOURNAL] > 0, "state": None, "import": None})
        return rows

    @app.route("/files/", defaults={"rel": ""})
    @app.route("/files/<path:rel>")
    @publish_required
    def files_browse(rel):
        error, entries, rows, subtitle = "", [], [], ""
        try:
            rel = fs.clean_rel(rel)
            if not fs.configured():
                raise fs.NotConfigured("Файловый сервер не подключён.")
            parts = rel.split("/") if rel else []
            if len(parts) == 1:                                   # журнал: сразу в папку его свежего года, остальное не нужно
                year = fs.latest_year(fs.list_folder(rel, refresh=bool(request.args.get("refresh"))))
                if year:
                    return redirect(url_for("files_browse", rel=f"{rel}/{year}"))
            if not rel:
                rows = _root_rows()
                rows.sort(key=lambda r: fs.natural_key(r["name"]))
                subtitle = f"Журналы {fs.current_year()} года" if rows else ""
                n_missing = sum(r["missing"] for r in rows)
                if n_missing:
                    subtitle += f" · выпусков, которых нет в разделе: {n_missing}"
            else:
                entries = fs.list_folder(rel, refresh=bool(request.args.get("refresh")))
        except fs.FileStoreError as e:
            error = str(e)
            rel = fs.clean_rel(rel) if not isinstance(e, fs.BadPath) else ""
        states = {}
        if len(rel.split("/")) == 2 and fs.is_year(rel.split("/")[1]) and entries:
            dirs = [e["name"] for e in entries if e["is_dir"]]
            states = file_marks.marks(file_marks.load_index(), rel.split("/")[0], rel.split("/")[1], dirs)
            c = file_marks.counts(states)
            subtitle = _summary(c)
        for e in entries:
            path = f"{rel}/{e['name']}" if rel else e["name"]
            ext = os.path.splitext(e["name"])[1].lower()
            mark = states.get(e["name"])
            rows.append({
                "name": e["name"], "is_dir": e["is_dir"], "size": e["size"], "mtime": e["mtime"], "new": fs.recent(e["mtime"]),
                "kind": "" if e["is_dir"] else KINDS.get(ext, ext.lstrip(".").upper()[:4] or "—"),
                "url": url_for("files_browse", rel=path) if e["is_dir"] else url_for("files_raw", rel=path),
                "download": None if e["is_dir"] else url_for("files_raw", rel=path, download=1),
                "zip": url_for("files_zip", rel=path) if e["is_dir"] else None,
                "viewable": (not e["is_dir"]) and bool(fs.mimetype_for(e["name"])),
                "sub": "", "missing": 0, "nojournal": False, "state": _state(mark) if mark else None,
                "import": url_for("admin_publish_import", **{"from": path}) if mark else None,
            })
        parts = rel.split("/") if rel else []
        if len(parts) >= 2 and fs.is_year(parts[1]):
            title = (f"{parts[0]} {parts[1]}" + (f" №{parts[2]}" if len(parts) == 3 else "")) if len(parts) <= 3 else parts[-1]
        else:
            title = parts[-1] if parts else "Файлы"
        return render_template(
            "files/browse.html", rel=rel, title=title, rows=rows, error=error, crumbs=_crumbs(rel), subtitle=subtitle,
            can_import=bool(rel) and fs.is_issue_folder(rel), can_zip=bool(rel) and not error,
            import_url=url_for("admin_publish_import", **{"from": rel}) if rel else "",
            zip_url=url_for("files_zip", rel=rel) if rel else "",
            n_dirs=sum(1 for r in rows if r["is_dir"]), n_files=sum(1 for r in rows if not r["is_dir"]))

    @app.route("/files/raw/<path:rel>")
    @publish_required
    def files_raw(rel):
        try:
            rel = fs.clean_rel(rel)
            size = fs.file_size(rel)
        except fs.FileStoreError:
            abort(404)
        name = rel.rsplit("/", 1)[-1]
        mime = fs.mimetype_for(name)
        inline = bool(mime) and not request.args.get("download")
        resp = Response(stream_with_context(fs.stream_file(rel)), mimetype=mime or "application/octet-stream")
        resp.headers["Content-Length"] = str(size)
        resp.headers["Content-Disposition"] = f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(name)}"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp

    @app.route("/files/zip/<path:rel>")
    @publish_required
    def files_zip(rel):
        try:
            path, name = fs.make_zip(rel)
        except fs.BadPath:
            abort(404)
        except fs.FileStoreError as e:
            return render_template("files/browse.html", rel="", title="Файлы", rows=[], error=str(e), crumbs=[], subtitle="", can_import=False,
                                   can_zip=False, import_url="", zip_url="", n_dirs=0, n_files=0), 413
        # Архив открываем и сразу удаляем из папки: пока файл открыт, он читается, а после ответа места не занимает —
        # чистка не зависит от того, закроет ли сервер ответ.
        f = open(path, "rb")
        size = os.fstat(f.fileno()).st_size
        os.remove(path)
        resp = send_file(f, as_attachment=True, download_name=name, mimetype="application/zip")
        resp.headers["Content-Length"] = str(size)
        return resp

    @app.route("/files/api/issue/<path:rel>")
    @publish_required
    def files_api_issue(rel):
        """Файлы выпуска для загрузки в «Публикацию» — по тем же правилам отбора, что при перетаскивании папки."""
        try:
            files = fs.issue_files(rel)
        except fs.FileStoreError as e:
            return jsonify({"error": str(e)}), 502
        if not files:
            return jsonify({"error": "В этой папке нет файлов .doc, .docx, .rtf и .pdf."}), 404
        return jsonify({"files": files})
