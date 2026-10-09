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
import pdf_parser  # noqa: E402
import publish_sanitize  # noqa: E402


def nfc(s):
    return unicodedata.normalize("NFC", s)


def norm(s):
    """Для сравнения текста с PDF: без регистра, пробелов, знаков и переносов."""
    s = unicodedata.normalize("NFKC", s or "").lower().replace("ё", "е")
    return re.sub(r"[^0-9a-zа-я]", "", s)


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


# ---------------------------------------------------------------- данные для сверки

def articles_from_xml(path):
    doc = etree.parse(path)
    out = []
    for a in doc.iter("article"):
        pages = (a.findtext("pages") or "").split("-")
        authors = []
        for x in a.iter("author"):
            ru, en = x.find("individInfo[@lang='RUS']"), x.find("individInfo[@lang='ENG']")
            authors.append({
                "surname": ru.findtext("surname") if ru is not None else "",
                "surname_en": en.findtext("surname") if en is not None else "",
                "org": (ru.findtext("orgName") or "") if ru is not None else "",
                "org_en": (en.findtext("orgName") or "") if en is not None else "",
                "email": (ru.findtext("email") or "") if ru is not None else "",
                "orcid": x.findtext("authorCodes/orcid") or "", "spin": x.findtext("authorCodes/spin") or "",
            })
        refs = a.findall("references/reference")
        out.append({
            "title": a.findtext("artTitles/artTitle[@lang='RUS']") or "",
            "title_en": a.findtext("artTitles/artTitle[@lang='ENG']") or "",
            "doi": a.findtext("codes/doi") or "", "udk": [a.findtext("codes/udk") or ""],
            "fpage": _int(pages[0]), "lpage": _int(pages[-1]),
            "abstract": a.findtext("abstracts/abstract[@lang='RUS']") or "",
            "abstract_en": a.findtext("abstracts/abstract[@lang='ENG']") or "",
            "keywords": [k.text or "" for k in a.findall("keywords/kwdGroup[@lang='RUS']/keyword")],
            "keywords_en": [k.text or "" for k in a.findall("keywords/kwdGroup[@lang='ENG']/keyword")],
            "refs": [r.findtext("refInfo[@lang='RUS']/text") for r in refs],
            "refs_en": [r.findtext("refInfo[@lang='ENG']/text") for r in refs],
            "received": _iso(a.findtext("dates/dateReceived")), "accepted": _iso(a.findtext("dates/dateAccepted")),
            "authors": authors, "raw": etree.tostring(a, encoding="unicode"),
        })
    return out


def article_from_api(uid):
    art = metafora_api.get_publication(uid)
    ru, en = art.get("RUS") or {}, art.get("ENG") or {}
    en_authors = en.get("authors") or []
    authors = []
    for i, x in enumerate(ru.get("authors") or []):
        e = en_authors[i] if i < len(en_authors) else {}
        authors.append({
            "surname": x.get("surname") or "", "surname_en": e.get("surname") or "",
            "org": ((x.get("aff") or [{}])[0].get("institution") or ""),
            "org_en": ((e.get("aff") or [{}])[0].get("institution") or ""),
            "email": None, "orcid": x.get("orcid") or "", "spin": x.get("spin") or "",
        })
    data = {
        "title": ru.get("title") or "", "title_en": en.get("title") or "", "doi": ru.get("doi") or "",
        "udk": ru.get("udk") or [], "fpage": ru.get("fpage"), "lpage": ru.get("lpage"),
        "abstract": ru.get("abstract") or "", "abstract_en": en.get("abstract") or "",
        "keywords": [k.get("value") or "" for k in ru.get("keywords") or []],
        "keywords_en": [k.get("value") or "" for k in en.get("keywords") or []],
        "refs": [(r or {}).get("text") for r in ru.get("references") or []],
        "refs_en": [(r or {}).get("text") for r in en.get("references") or []],
        "received": ru.get("date_received"), "accepted": ru.get("date_accepted"),
        "authors": authors, "raw": json.dumps(art, ensure_ascii=False),
        "n_authors_en": len(en_authors),
    }
    try:
        d = metafora_api._request("GET", f"/api/v2/publications/doi/{data['doi']}").json()
        d = d.get("data") or d
        d = d[0] if isinstance(d, list) else d
        data["has_pdf"], data["signed_at"] = bool(d.get("has_pdf")), d.get("signed_at")
    except metafora_api.MetaforaError as e:
        data["has_pdf_error"] = str(e)
    return data


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _iso(d):
    """'12.05.2026' -> '2026-05-12'; ISO оставляем как есть."""
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else (d or None)


# ---------------------------------------------------------------- PDF

def load_pdfs(folder):
    import fitz
    pdfs = {}
    for p in glob.glob(os.path.join(folder, "*.pdf")):
        d = fitz.open(p)
        pages = [pg.get_text() for pg in d]
        pdfs[nfc(os.path.basename(p))] = {"path": p, "n": len(d), "p1": pages[0] if pages else "", "all": "\n".join(pages)}
    return pdfs


def find_pdf(pdfs, doi):
    """PDF статьи по её DOI. DOI статьи стоит на первой странице её PDF; PDF целого номера
    (в нём DOI всех статей, но не на первой странице) в расчёт не берём."""
    doi = (doi or "").lower()
    if not doi:
        return []
    first = [k for k, v in pdfs.items() if doi in v["p1"].lower()]
    return first or [k for k, v in pdfs.items() if doi in v["all"].lower()]


_NAME = re.compile(r"[А-ЯЁ][А-Яа-яё\-]+\.?\s+[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?")  # терпит опечатку «Маслов. И.В.»
_ORCID = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _section(text, start_re, end_re):
    m = re.search(start_re, text)
    if not m:
        return None
    seg = text[m.end():]
    e = re.search(end_re, seg)
    return seg[:e.start()] if e else seg


def _numbered(seg):
    if seg is None:
        return None
    exp = 0
    for v in (int(x) for x in re.findall(r"(?m)^\s*(\d{1,3})\.\s", seg)):
        if v == exp + 1:
            exp = v
    return exp or None


def check_article(a, P):
    """Сверка одной статьи с её PDF -> (ошибки, заметки)."""
    errs, warn = [], []
    n1, nall = norm(P["p1"]), norm(P["all"])
    if norm(a["title"]) not in n1:
        errs.append("русское название не совпадает с PDF")
    if not a["title_en"]:
        errs.append("нет английского названия")
    elif norm(a["title_en"]) not in nall:
        errs.append("английское название не совпадает с PDF")

    fp, lp = a["fpage"], a["lpage"]
    if not (fp and lp) or lp - fp + 1 != P["n"]:
        errs.append(f"страницы {fp}-{lp}, а в PDF {P['n']} стр.")

    A = a["authors"]
    cit = P["all"].split("Для цитирования", 1)[1][:1200] if "Для цитирования" in P["all"] else ""
    w = a["title"].split()[0] if a["title"].split() else ""
    head = cit.split(w, 1)[0] if w and w in cit else cit
    cnt = len(_NAME.findall(head.replace("\n", " ")))
    if cnt and cnt != len(A):
        errs.append(f"авторов {len(A)}, а в «Для цитирования» PDF их {cnt}")
    if not cnt:
        warn.append("не смог посчитать авторов в PDF")
    if a.get("n_authors_en") is not None and a["n_authors_en"] != len(A):
        errs.append(f"английских авторов {a['n_authors_en']}, русских {len(A)}")
    pdf_emails = {e.lower().rstrip(".") for e in _EMAIL.findall(P["all"])}
    for x in A:
        if norm(x["surname"]) not in n1:
            errs.append(f"автора «{x['surname']}» нет на первой странице PDF")
        if not x["surname_en"]:
            errs.append(f"у автора «{x['surname']}» нет английской фамилии")
        elif norm(x["surname_en"]) not in nall:
            errs.append(f"англ. фамилии «{x['surname_en']}» нет в PDF")
        if not x["org"].strip():
            errs.append(f"у автора «{x['surname']}» нет организации")
        elif not all(norm(part)[:22] in nall for part in x["org"].split(";") if part.strip()):
            warn.append(f"организация «{x['org'][:50]}» не найдена в PDF дословно")
        if not x["org_en"].strip():
            errs.append(f"у автора «{x['surname']}» нет английской организации")
        if x["orcid"] and x["orcid"] not in P["all"]:
            errs.append(f"ORCID {x['orcid']} ({x['surname']}) не найден в PDF")
        if x["spin"] and x["spin"] not in P["all"]:
            errs.append(f"SPIN {x['spin']} ({x['surname']}) не найден в PDF")
        if x["email"] is not None:
            if not x["email"]:
                pass  # часто почту указывает только один автор — см. общую проверку ниже
            elif x["email"].lower() not in pdf_emails and norm(x["email"]) not in nall:
                errs.append(f"e-mail {x['email']} ({x['surname']}) не найден в PDF")
    if A and A[0]["email"] is not None:
        got = {x["email"].lower() for x in A if x["email"]}
        if not got:
            warn.append("ни у одного автора нет e-mail")
        # в PDF номер сноски бывает приклеен к адресу («1xeal91@yandex.ru») — сравниваем по вхождению
        lost = sorted(e for e in pdf_emails if not any(g in e for g in got) and norm(e) in n1)
        if lost and len(got) < len(A):
            warn.append(f"e-mail есть на первой странице PDF, но не записан: {lost}")
    po, mo = set(_ORCID.findall(P["all"])), {x["orcid"] for x in A if x["orcid"]}
    if po - mo:
        warn.append(f"ORCID есть в PDF, но не записан: {sorted(po - mo)}")

    ab, abe = norm(a["abstract"]), norm(a["abstract_en"])
    if len(ab) < 200:
        errs.append("русская аннотация пустая или слишком короткая")
    elif ab[:120] not in n1:
        errs.append("начало русской аннотации не совпадает с PDF")
    if len(abe) < 150:
        errs.append("английская аннотация пустая или слишком короткая")
    elif abe[:100] not in nall:
        errs.append("начало английской аннотации не совпадает с PDF")
    if not a["keywords"]:
        errs.append("нет ключевых слов")
    for k in a["keywords"]:
        if norm(k) not in nall:
            errs.append(f"ключевое слово «{k[:40]}» не найдено в PDF")
    if not a["keywords_en"]:
        errs.append("нет английских ключевых слов")
    if not [u for u in a["udk"] if u]:
        errs.append("нет УДК")
    for u in a["udk"]:
        if u and norm(u) not in n1:
            errs.append(f"УДК {u} не найден в PDF")
    for fld, lab in (("received", "поступила"), ("accepted", "принята")):
        v = a[fld]
        if not v:
            warn.append(f"нет даты «{lab}»")
            continue
        y, mo_, d_ = v.split("-")
        if f"{d_}.{mo_}.{y}" not in P["all"]:
            errs.append(f"дата «{lab}» {d_}.{mo_}.{y} не найдена в PDF")

    s_ru = _section(P["all"], r"Список источников", r"Информация об автор")
    s_en = _section(P["all"], r"(?mi)^\s*References\s*$", r"(?i)Information about the author")
    c_ru, c_en = _numbered(s_ru), _numbered(s_en)
    for lab, lst, sec, cnt_pdf in (("русск.", a["refs"], s_ru, c_ru), ("англ.", a["refs_en"], s_en, c_en)):
        empty = [i + 1 for i, r in enumerate(lst) if not (r or "").strip()]
        if empty:
            errs.append(f"пустые {lab} ссылки №{empty}")
        good = [r for r in lst if (r or "").strip()]
        if not good:
            errs.append(f"нет списка литературы ({lab})")
            continue
        if sec is None:
            warn.append(f"не нашёл в PDF раздел литературы ({lab})")
            continue
        nsec = norm(sec)
        # каждая ссылка должна стоять в СВОЁМ разделе PDF — и началом, и концом (ловит обрывки и склейки)
        miss = [r[:60] for r in good if norm(r)[:45] not in nsec or norm(r)[-25:] not in nsec]
        if miss:
            errs.append(f"{len(miss)} {lab} ссылок не совпали со списком в PDF, напр.: {miss[0]}")
        if cnt_pdf and cnt_pdf != len(lst):
            errs.append(f"{lab} ссылок {len(lst)}, по нумерации PDF {cnt_pdf}")
        if not cnt_pdf:
            warn.append(f"нумерацию {lab} ссылок в PDF посчитать не удалось")
    a["_counts"] = (len(a["refs"]), len(a["refs_en"]), c_ru, c_en)

    junk = [w_ for w_ in ("HYPERLINK", "MERGEFORMAT") if w_ in a["raw"]]
    if junk:
        errs.append(f"остались служебные коды Word: {junk}")
    if "has_pdf" in a and not a["has_pdf"]:
        errs.append("PDF не привязан")
    if a.get("has_pdf_error"):
        warn.append(f"не удалось проверить привязку PDF: {a['has_pdf_error']}")
    return errs, warn


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
