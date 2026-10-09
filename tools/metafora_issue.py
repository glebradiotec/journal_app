#!/usr/bin/env python3
"""Выпуск журнала -> ИС «Метафора»: сборка XML из папки с .doc, сверка с PDF, отправка, проверка.

  build   --doc ПАПКА --issn 2070-0970 --year 2026 --number 3 --journal "Нелинейный мир" --xml ФАЙЛ.xml
  check   --xml ФАЙЛ.xml --pdf ПАПКА          сверка XML с напечатанными PDF, ничего не отправляет
  send    --xml ФАЙЛ.xml --pdf ПАПКА [--replace]   загрузка XML и PDF статей (подписи не ставит)
  verify  --xml ФАЙЛ.xml --pdf ПАПКА          сверка того, что лежит в Метафоре, с PDF

PDF — независимый источник: если разбор .doc где-то ошибся, расхождение с PDF это покажет.
Состояние отправки (file_uid, uid статей) хранится рядом с XML: ФАЙЛ.xml.state.json.
Ключ API берётся из METAFORA_API_KEY (.env приложения).
"""
import argparse
import glob
import json
import os
import re
import sys
import time
import unicodedata
from types import SimpleNamespace as NS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from lxml import etree  # noqa: E402

import article_template_parser  # noqa: E402
import export_metafora  # noqa: E402
import metafora_api  # noqa: E402
from metafora_check import (  # noqa: E402
    article_from_api, articles_from_xml, check_article, find_pdf, load_pdfs, nfc)
import pdf_parser  # noqa: E402
import publish_sanitize  # noqa: E402


# ---------------------------------------------------------------- build

def build(args):
    files = sorted(glob.glob(os.path.join(args.doc, "*.doc")) + glob.glob(os.path.join(args.doc, "*.docx")),
                   key=lambda p: nfc(os.path.basename(p)))
    articles, skipped, notes, volumes = [], [], [], []
    for path in files:
        name = nfc(os.path.basename(path))
        if name.startswith("~$"):
            continue
        try:
            raw = pdf_parser.extract_text_any(path)
            parsed, warns = article_template_parser.parse_article_text(raw)
            parsed = publish_sanitize.deep_clean_text(parsed)
        except Exception as e:  # noqa: BLE001
            skipped.append((name, f"не удалось разобрать: {e}"))
            continue
        # Статья — то, у чего есть DOI и авторы. Титул, реклама, юбилейные заметки сюда не попадают.
        if not parsed.get("doi") or not parsed.get("authors"):
            skipped.append((name, "не статья (нет DOI или авторов)"))
            continue
        for w in warns:
            notes.append(f"{name}: {w}")
        dates = parsed.get("dates") or {}
        vol = re.search(r"\bТ\.\s*(\d+)", parsed.get("citation_ru") or "")
        if vol:
            volumes.append(vol.group(1))
        articles.append(NS(
            section_ru="", section_en="", pages=parsed.get("pages") or "", art_type=parsed.get("art_type") or "RAR",
            authors=[_author(a) for a in parsed["authors"]],
            title_ru=parsed.get("title_ru", ""), title_en=parsed.get("title_en", ""),
            abstract_ru=parsed.get("abstract_ru", ""), abstract_en=parsed.get("abstract_en", ""),
            fulltext_ru=parsed.get("fulltext_ru", ""), doi=parsed.get("doi", ""), udk=parsed.get("udk", ""),
            keywords_ru=parsed.get("keywords_ru", []), keywords_en=parsed.get("keywords_en", []),
            references=parsed.get("references", []), references_en=parsed.get("references_en", []),
            date_received=dates.get("received", ""), date_accepted=dates.get("accepted", ""), date_published="",
            funding_ru=parsed.get("funding_ru", ""), funding_en=parsed.get("funding_en", ""),
        ))
    issue = NS(issn=args.issn, year=int(args.year), number=str(args.number), alt_number="", part="", pages="",
               elibrary_titleid="", volume=args.volume or (max(set(volumes), key=volumes.count) if volumes else ""),
               articles=articles)
    xml = export_metafora.build_metafora_xml(issue, args.journal)
    os.makedirs(os.path.dirname(os.path.abspath(args.xml)), exist_ok=True)
    with open(args.xml, "wb") as f:
        f.write(xml)
    print(f"Собрано статей: {len(articles)}, том {issue.volume or '—'} -> {args.xml}")
    for name, why in skipped:
        print(f"  пропущен {name}: {why}")
    for n in notes:
        print(f"  заметка парсера — {n}")
    problems = metafora_api.validate_journal_xml(xml)
    for p in problems:
        print(f"  ПРОБЛЕМА: {p}")
    return 1 if problems else 0


def _author(a):
    g = lambda k: (a.get(k) or "")  # noqa: E731
    return NS(orcid=g("orcid"), spin=g("spin"), researcherid=g("researcherid"), scopusid=g("scopusid"),
              surname_ru=g("surname_ru"), initials_ru=g("initials_ru"), org_ru=g("org_ru"), town_ru=g("town_ru"),
              country_ru=g("country_ru") or "Россия", position_ru=g("position_ru"), email=g("email"),
              surname_en=g("surname_en"), initials_en=g("initials_en"), org_en=g("org_en"), town_en=g("town_en"),
              country_en=g("country_en") or "Russia", position_en=g("position_en"))


def run_checks(articles, pdfs, label):
    bad, seen = 0, set()
    print(f"\n===== {label}: статей {len(articles)}")
    for a in articles:
        hit = find_pdf(pdfs, a["doi"])
        if len(hit) != 1:
            print(f"  ✗ {a['title'][:50]} — PDF по DOI {a['doi']} не найден однозначно: {hit}")
            bad += 1
            continue
        seen.add(hit[0])
        errs, warn = check_article(a, pdfs[hit[0]])
        bad += bool(errs)
        r, re_, c_ru, c_en = a["_counts"]
        mark = "✗" if errs else ("!" if warn else "✓")
        signed = " подписана" if a.get("signed_at") else ""
        print(f"  {mark} {hit[0][:28]:28} авт.{len(a['authors'])} лит.{r}/{re_} (PDF {c_ru}/{c_en}) "
              f"стр.{a['fpage']}-{a['lpage']}{signed}")
        for e in errs:
            print(f"       ОШИБКА: {e}")
        for w in warn:
            print(f"       заметка: {w}")
    # PDF со своим DOI, для которого нет статьи, — потерянная статья.
    for k, v in pdfs.items():
        if k in seen:
            continue
        m = re.search(r"10\.18127/[\w.\-]+", v["p1"])
        if m:
            print(f"  ✗ в папке есть PDF статьи без пары: {k} (DOI {m.group(0)})")
            bad += 1
    print(f"ИТОГО: статей {len(articles)}, с ошибками {bad}")
    return bad


def check(args):
    return 1 if run_checks(articles_from_xml(args.xml), load_pdfs(args.pdf), f"XML {os.path.basename(args.xml)}") else 0


# ---------------------------------------------------------------- отправка

def _state_path(xml_path):
    return xml_path + ".state.json"


def _load_state(xml_path):
    try:
        with open(_state_path(xml_path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def send(args):
    with open(args.xml, "rb") as f:
        xml = f.read()
    problems = metafora_api.validate_journal_xml(xml)
    if problems:
        print("XML не проходит проверку, отправка отменена:", *problems, sep="\n  ")
        return 1
    pdfs = load_pdfs(args.pdf)
    if run_checks(articles_from_xml(args.xml), pdfs, "сверка XML с PDF перед отправкой"):
        print("Есть расхождения с PDF — отправка отменена. Исправьте или запустите с --force.")
        if not args.force:
            return 1
    state = _load_state(args.xml)
    if state.get("file_uid"):
        if not args.replace:
            print(f"Выпуск уже отправлен (file_uid {state['file_uid']}). Для замены добавьте --replace.")
            return 1
        signed = {u: s for u, s in metafora_api.publications_status(state.get("articles") or []).items() if s}
        if signed:
            print(f"Заменить нельзя: {len(signed)} публикаций подписано. Сначала снимите подпись в Метафоре.")
            return 1
        metafora_api.delete_file(state["file_uid"])
        print("прежний файл удалён:", state["file_uid"])
    uid = metafora_api.upload_journal_xml(xml, filename=os.path.basename(args.xml))
    for _ in range(30):
        st = metafora_api.file_status(uid)
        if st["code"] in (3, 4):
            break
        time.sleep(4)
    state = {"file_uid": uid, "articles": st["articles"], "code": st["code"]}
    with open(_state_path(args.xml), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(f"file_uid {uid}: {st['text']}, статей {len(st['articles'])}")
    if st["code"] != 3:
        print(json.dumps(st["raw"], ensure_ascii=False)[:2000])
        return 1
    ok = 0
    for auid in st["articles"]:
        pub = metafora_api.get_publication(auid)
        loc = pub.get("RUS") or next(iter(pub.values()))
        hit = find_pdf(pdfs, loc.get("doi"))
        if len(hit) != 1:
            print(f"  нет однозначного PDF для «{(loc.get('title') or '')[:50]}»: {hit}")
            continue
        with open(pdfs[hit[0]]["path"], "rb") as f:
            r = metafora_api.upload_publication_pdf(auid, f.read(), hit[0])
        ok += bool((r.get("data") or {}).get("has_pdf"))
    print(f"PDF загружено: {ok} из {len(st['articles'])}")
    return 0 if ok == len(st["articles"]) else 1


def verify(args):
    state = _load_state(args.xml)
    if not state.get("file_uid"):
        print("Выпуск ещё не отправлялся.")
        return 1
    uids = metafora_api.file_status(state["file_uid"])["articles"]
    arts = [article_from_api(u) for u in uids]
    bad = run_checks(arts, load_pdfs(args.pdf), f"Метафора, файл {state['file_uid']}")
    print(f"подписано: {sum(1 for a in arts if a.get('signed_at'))} из {len(arts)}")
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--doc", required=True); b.add_argument("--issn", required=True)
    b.add_argument("--year", required=True); b.add_argument("--number", required=True)
    b.add_argument("--journal", required=True); b.add_argument("--xml", required=True)
    b.add_argument("--volume", default="", help="том; по умолчанию берётся из строки «Для цитирования»")
    for name in ("check", "send", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--xml", required=True); p.add_argument("--pdf", required=True)
        if name == "send":
            p.add_argument("--replace", action="store_true"); p.add_argument("--force", action="store_true")
    args = ap.parse_args()
    sys.exit({"build": build, "check": check, "send": send, "verify": verify}[args.cmd](args))


if __name__ == "__main__":
    main()
