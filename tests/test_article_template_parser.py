"""Разбор статьи по шаблону издательства. Каждый тест — случай из реальных статей,
на котором разбор раньше ошибался (журнал «Нелинейный мир», «Радиотехника», 2025–2026)."""
import article_template_parser as P

HEADER = """Научная статья
УДК 539.2
DOI: https://doi.org/10.18127/j20700970-202603-04
Исследование электронных свойств
{authors}
{orgs}
{emails}
Аннотация
Постановка проблемы. Текст аннотации.
Ключевые слова
слово один, слово два
Для цитирования
{citation} Исследование электронных свойств // Нелинейный мир. 2026. Т. 24. № 3. С. 29–40.
Введение
Текст статьи.
Список источников
{refs}
Информация об авторах
{info}
Статья поступила в редакцию 12.12.2025
Одобрена после рецензирования 08.02.2026
Принята к публикации 30.07.2026
{en_marker}
Study of the electronic properties
{authors_en}
{orgs_en}
{emails}
Abstract
English abstract text.
Keywords
word one, word two
For citation
Citation in English.
References
{refs_en}
Information about the authors
{info_en}
"""

DEFAULTS = dict(
    authors="А.А. Скрылев1, Д.В. Шестаков2",
    orgs="1,2 Нижегородский государственный университет (г. Нижний Новгород, Россия)",
    emails="1 skrylev@mail.ru, 2 shestakov@unn.ru",
    citation="Скрылев А.А., Шестаков Д.В.",
    refs="Иванов И.И. Основы теории. М.: Наука. 2010.\nПетров П.П. Методы расчёта. СПб.: Питер. 2015.",
    info="Алексей Андреевич Скрылев – мл. науч. сотрудник\nДанил Валерьевич Шестаков – аспирант",
    en_marker="Original article",
    authors_en="A.A. Skrylev1, D.V. Shestakov2",
    orgs_en="1,2 Nizhny Novgorod State University (Nizhny Novgorod, Russia)",
    refs_en="Ivanov I.I. Osnovy teorii. M.: Nauka. 2010.\nPetrov P.P. Metody raschyota. SPb.: Piter. 2015.",
    info_en="Alexey A. Skrylev – Junior Researcher\nDanil V. Shestakov – Postgraduate",
)


def parse(**kw):
    return P.parse_article_text(HEADER.format(**{**DEFAULTS, **kw}))


def surnames(result):
    return [(a["surname_ru"], a["surname_en"]) for a in result["authors"]]


def test_baseline_has_no_warnings():
    result, warnings = parse()
    assert warnings == []
    assert surnames(result) == [("Скрылев", "Skrylev"), ("Шестаков", "Shestakov")]
    assert result["pages"] == "29-40" and len(result["references"]) == len(result["references_en"]) == 2


def test_author_with_single_initial_is_kept():
    result, _ = parse(authors="Б. Балбашио1, А.А. Скрылев2", authors_en="B. Balbashio1, A.A. Skrylev2",
                      citation="Балбашио Б., Скрылев А.А.")
    assert surnames(result) == [("Балбашио", "Balbashio"), ("Скрылев", "Skrylev")]
    assert result["authors"][0]["initials_ru"] == "Б."


def test_authors_wrapped_to_next_line_keep_orgs_and_emails():
    result, warnings = parse(
        authors="О.В. Есиков1, В.Л. Румянцев2,\nЛ.Д. Щербаков3",
        orgs="1,2АО ЦКБА (г. Тула, Россия)\n3 Тульский государственный университет (г. Тула, Россия)",
        emails="1 eov@rambler.ru, 2 vl@yandex.ru,\n3 lev@yandex.ru",
        citation="Есиков О.В., Румянцев В.Л., Щербаков Л.Д.",
        authors_en="O.V. Esikov1, V.L. Rumyantsev2,\nL.D. Shcherbakov3",
        orgs_en="1,2JS CDBAE (Tula, Russia)\n3 Tula State University (Tula, Russia)")
    assert [a["surname_ru"] for a in result["authors"]] == ["Есиков", "Румянцев", "Щербаков"]
    assert [a["org_ru"] for a in result["authors"]] == ["АО ЦКБА", "АО ЦКБА", "Тульский государственный университет"]
    assert result["authors"][2]["email"] == "lev@yandex.ru"
    assert not [w for w in warnings if "автор" in w.lower()]


def test_org_name_spanning_several_lines():
    result, _ = parse(orgs=(
        "1 Технологический университет имени дважды Героя Советского Союза, летчика-космонавта\n"
        "А.А. Леонова – Филиал федерального ГБОУ ВО «Московский государственный университет геодезии\n"
        "и картографии» (г. Королев, Московская область, Россия)\n"
        "2 Поволжский государственный университет сервиса (г. Тольятти, Россия)"))
    a1, a2 = result["authors"]
    assert "Леонова" in a1["org_ru"] and "картографии" in a1["org_ru"]
    assert a2["org_ru"] == "Поволжский государственный университет сервиса" and a2["town_ru"] == "г. Тольятти"


def test_missing_period_in_initials_does_not_shift_english_names():
    """«Н.А Волков» без точки: раньше автор выпадал, а остальные получали чужие английские фамилии."""
    result, _ = parse(authors="А.А. Николаев1, Н.А Волков2, М.С. Глушков3", orgs="1–3 Концерн «Агат» (Москва, Россия)",
                      emails="1 a@ya.ru, 2 b@ya.ru, 3 c@ya.ru", citation="Николаев А.А., Волков Н.А., Глушков М.С.",
                      authors_en="A.A. Nikolaev1, N.A. Volkov2, M.S. Glushkov3", orgs_en="1–3 Agat Concern (Moscow, Russia)")
    assert surnames(result) == [("Николаев", "Nikolaev"), ("Волков", "Volkov"), ("Глушков", "Glushkov")]
    assert result["authors"][1]["initials_ru"] == "Н.А."


def test_authors_are_paired_by_footnote_number_not_position():
    """Если английский список разобран не полностью, остальные авторы не съезжают."""
    result, warnings = parse(authors="А.А. Николаев1, Н.А. Волков2, М.С. Глушков3", orgs="1–3 Концерн (Москва, Россия)",
                             emails="1 a@ya.ru", citation="Николаев А.А., Волков Н.А., Глушков М.С.",
                             authors_en="A.A. Nikolaev1, M.S. Glushkov3", orgs_en="1–3 Concern (Moscow, Russia)")
    assert surnames(result) == [("Николаев", "Nikolaev"), ("Волков", ""), ("Глушков", "Glushkov")]
    assert any("английских" in w for w in warnings)


def test_orcid_and_spin_are_read_in_any_label_order():
    result, _ = parse(info=(
        "Алексей Андреевич Скрылев – мл. науч. сотрудник\n"
        "ORCID SPIN-код: не представлен; 0000-0002-5399-6038\n"
        "Данил Валерьевич Шестаков – аспирант\n"
        "SPIN-код автора: 7900-7413; ORCID: 0009-0006-4778-1002"))
    a1, a2 = result["authors"]
    assert (a1["orcid"], a1["spin"], a1["position_ru"]) == ("0000-0002-5399-6038", "", "мл. науч. сотрудник")
    assert (a2["orcid"], a2["spin"]) == ("0009-0006-4778-1002", "7900-7413")


def test_same_orcid_for_two_authors_is_dropped_with_warning():
    result, warnings = parse(info=(
        "Алексей Андреевич Скрылев – мл. науч. сотрудник\nORCID: 0000-0001-9836-0394\n"
        "Данил Валерьевич Шестаков – аспирант\nORCID: 0000-0001-9836-0394"))
    assert [a["orcid"] for a in result["authors"]] == ["", ""]
    assert any("0000-0001-9836-0394" in w for w in warnings)


def test_email_without_footnote_number_and_with_space_after_at():
    result, _ = parse(authors="Л.А. Калуцкий1", orgs="1 Институт гидродинамики (г. Новосибирск, Россия)",
                      emails="leon@ gmail.com", citation="Калуцкий Л.А.", authors_en="L.A. Kalutsky1",
                      orgs_en="1 Institute of Hydrodynamics (Novosibirsk, Russia)",
                      info="Леонид Александрович Калуцкий – аспирант", info_en="Leonid A. Kalutsky – Postgraduate")
    assert result["authors"][0]["email"] == "leon@gmail.com"


def test_reference_tail_after_soft_break_is_glued():
    """«…2020. V. 82.» / «P. 103985.» — перенос внутри ссылки, а не новая ссылка."""
    result, warnings = parse(
        refs="Mashat D.S. A quasi-3D plate theory. European Journal of Mechanics. 2020. V. 82.\nP. 103985.\n"
             "Петров П.П. Методы расчёта. СПб.: Питер. 2015.",
        refs_en="Mashat D.S. A quasi-3D plate theory. European Journal of Mechanics. 2020. V. 82.\nP. 103985.\n"
                "Petrov P.P. Metody raschyota. SPb.: Piter. 2015.")
    assert len(result["references"]) == 2 and result["references"][0].endswith("V. 82. P. 103985.")
    assert warnings == []


def test_reference_without_final_period_is_not_merged_with_next():
    result, _ = parse(refs="Гельфман Т.Э., Пирхавка А.П. Анализ эффективности методов. 2020\n"
                           "Гельфман Т.Э., Пирхавка А.П. Оценка эффективности резервирования. 2021.",
                      refs_en="Gel'fman T.E., Pirhavka A.P. Analiz effektivnosti metodov. 2020\n"
                              "Gel'fman T.E., Pirhavka A.P. Ocenka effektivnosti rezervirovaniya. 2021.")
    assert len(result["references"]) == 2 and len(result["references_en"]) == 2


def test_authors_after_slash_stay_in_the_same_reference():
    result, _ = parse(refs="Патент № 2325666. Разностно-дальномерный способ пеленгования / А.Г. Сайбель,\n"
                           "П.А. Сидоров. 2008.\nПетров П.П. Методы расчёта. СПб.: Питер. 2015.")
    assert len(result["references"]) == 2 and result["references"][0].endswith("П.А. Сидоров. 2008.")


def test_url_only_line_is_its_own_reference():
    result, _ = parse(refs="Wu S. et al. Bloomberggpt // arXiv preprint arXiv:2303.17564. 2023.\n"
                           "URL: https://huggingface.co/DragonLLM/Llama-Open-Finance-8B (дата обращения: 16.02.2026)")
    assert len(result["references"]) == 2


def test_orphan_fragment_is_glued_back_using_the_other_language_list():
    """Обрывок, который по виду строки не распознать, находится сверкой русского списка с английским."""
    result, warnings = parse(
        refs="Иванов И.И. Основы теории. М.: Наука. 2010.\nПетров П.П. Методы расчёта. СПб.: Питер. 2015.",
        refs_en="Ivanov I.I. Osnovy teorii.\nM.: Nauka. 2010.\nPetrov P.P. Metody raschyota. SPb.: Piter. 2015.")
    assert result["references_en"] == ["Ivanov I.I. Osnovy teorii. M.: Nauka. 2010.",
                                       "Petrov P.P. Metody raschyota. SPb.: Piter. 2015."]
    assert not [w for w in warnings if "списке" in w]


def test_marked_soft_breaks_are_followed_exactly():
    """Текст из textutil: строка-продолжение помечена U+2028, абзац = ссылка, без догадок."""
    text = HEADER.format(**{**DEFAULTS,
                            "refs": "Иванов И.И. Основы теории.\n Том первый. М.: Наука. 2010.\nJ. Wiley. 1994. 386 p.",
                            "refs_en": "Ivanov I.I. Osnovy teorii.\n Tom pervyj. M.: Nauka. 2010.\nJ. Wiley. 1994. 386 p."})
    result, _ = P.parse_article_text(text + "\n ")
    assert result["references"] == ["Иванов И.И. Основы теории. Том первый. М.: Наука. 2010.", "J. Wiley. 1994. 386 p."]


def test_appendix_after_references_is_not_a_reference():
    result, _ = parse(refs="Иванов И.И. Основы теории. М.: Наука. 2010.\nПетров П.П. Методы расчёта. СПб.: Питер. 2015.\n"
                           "ПРИЛОЖЕНИЕ 1\nВывод алгоритма оценки координат.\nВведем обозначение.")
    assert len(result["references"]) == 2


def test_word_field_codes_are_removed_without_eating_the_next_heading():
    result, _ = parse(emails='1 HYPERLINK "mailto:skrylev@mail.ru" \\h skrylev@mail.ru, 2 shestakov@unn.ru '
                             'HYPERLINK "mailto:post@cnirti.ru"',
                      refs='Указ Президента РФ № 145. URL: HYPERLINK "http://kremlin.ru/acts/1" \\t "_blank" http://kremlin.ru/acts/1\n'
                           "Петров П.П. Методы расчёта. СПб.: Питер. 2015.")
    assert result["authors"][0]["email"] == "skrylev@mail.ru"
    assert result["abstract_ru"] and result["abstract_en"] == "English abstract text."
    assert "HYPERLINK" not in " ".join(result["references"])
    assert result["references"][0].endswith("http://kremlin.ru/acts/1")


def test_review_article_marker_opens_the_english_part():
    text = HEADER.format(**{**DEFAULTS, "en_marker": "Review article"}).replace("Научная статья", "Обзорная статья", 1)
    result, warnings = P.parse_article_text(text)
    assert result["title_en"] == "Study of the electronic properties" and result["art_type"] == "REV"
    assert warnings == []


def test_author_count_mismatch_with_citation_is_reported():
    _, warnings = parse(citation="Скрылев А.А., Шестаков Д.В., Виноградова Л.М.")
    assert any("Для цитирования" in w for w in warnings)


def test_repeated_footnote_number_in_emails_does_not_give_someone_elses_address():
    """«1a@…, 2b@…, 2c@…»: раньше второй автор получал адрес третьего."""
    result, warnings = parse(authors="А.А. Петров1, А.А. Ермаков2, И.В. Маслов3", orgs="1–3 Елецкий университет (г. Елец, Россия)",
                             emails="1xeal@yandex.ru, 2ermakov@list.ru, 2maslov@gmail.com",
                             citation="Петров А.А., Ермаков А.А., Маслов И.В.",
                             authors_en="A.A. Petrov1, A.A. Ermakov2, I.V. Maslov3", orgs_en="1–3 Yelets University (Yelets, Russia)")
    assert [a["email"] for a in result["authors"]] == ["xeal@yandex.ru", "ermakov@list.ru", "maslov@gmail.com"]
    assert any("номер сноски" in w for w in warnings)


def test_one_email_for_a_range_of_authors():
    result, _ = parse(authors="С.Г. Ворона1, Т.В. Калинин2, М.С. Ворона3", orgs="1−3Военно-космическая академия (Санкт-Петербург, Россия)",
                      emails="1−3vka@mil.ru", citation="Ворона С.Г., Калинин Т.В., Ворона М.С.",
                      authors_en="S.G. Vorona1, T.V. Kalinin2, M.S. Vorona3", orgs_en="1−3Military Space Academy (Saint Petersburg, Russia)")
    assert [a["email"] for a in result["authors"]] == ["vka@mil.ru"] * 3


# ---------- чтение .doc (doc_text) ----------
import io
import struct

import pytest

import doc_text


def _make_doc(text):
    """Минимальный .doc: один несжатый (UTF-16) кусок текста. Достаточно, чтобы проверить
    разбор составного файла, таблицы кусков и служебных знаков."""
    sec = 512
    body = text.encode("utf-16-le")
    word = bytearray(0x800)
    struct.pack_into("<HH", word, 0, 0xA5EC, 0x00C1)
    struct.pack_into("<H", word, 0x0A, 0x0200)                      # таблица — в потоке 1Table
    struct.pack_into("<I", word, 0x4C, len(text))                   # ccpText
    word += body
    word += b"\0" * max(4096 - len(word), 0)                        # не меньше порога мини-потока
    word += b"\0" * (-len(word) % sec)
    plc = struct.pack("<II", 0, len(text)) + struct.pack("<HIH", 0, 0x800, 0)
    clx = b"\x02" + struct.pack("<I", len(plc)) + plc
    struct.pack_into("<II", word, 0x01A2, 0, len(clx))              # fcClx, lcbClx
    table = clx + b"\0" * (4096 - len(clx))                         # >= порога мини-потока

    def sectors(b):
        return [b[i:i + sec] for i in range(0, len(b), sec)]

    w_secs, t_secs = sectors(bytes(word)), sectors(table)
    # раскладка секторов: 0 — FAT, 1 — каталог, дальше WordDocument и 1Table
    fat = [0xFFFFFFFD, 0xFFFFFFFE]
    w_start = len(fat)
    fat += [w_start + i + 1 for i in range(len(w_secs) - 1)] + [0xFFFFFFFE]
    t_start = len(fat)
    fat += [t_start + i + 1 for i in range(len(t_secs) - 1)] + [0xFFFFFFFE]
    fat += [0xFFFFFFFF] * (sec // 4 - len(fat))

    def entry(name, kind, start, size, child=0xFFFFFFFF, right=0xFFFFFFFF):
        e = bytearray(128)
        raw = (name + "\0").encode("utf-16-le")
        e[:len(raw)] = raw
        struct.pack_into("<HB", e, 0x40, len(raw), kind)
        struct.pack_into("<III", e, 0x44, 0xFFFFFFFF, right, child)
        struct.pack_into("<IQ", e, 0x74, start, size)
        return bytes(e)

    directory = (entry("Root Entry", 5, 0xFFFFFFFE, 0, child=1)
                 + entry("WordDocument", 2, w_start, len(word), right=2)
                 + entry("1Table", 2, t_start, len(table)) + bytes(128))
    header = bytearray(sec)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<I", header, 0x2C, 1)                          # один сектор FAT
    struct.pack_into("<I", header, 0x30, 1)                          # каталог — сектор 1
    struct.pack_into("<5I", header, 0x38, 4096, 0xFFFFFFFE, 0, 0xFFFFFFFE, 0)
    struct.pack_into("<109I", header, 0x4C, 0, *([0xFFFFFFFF] * 108))
    return bytes(header) + struct.pack(f"<{sec // 4}I", *fat) + directory + b"".join(w_secs) + b"".join(t_secs)


def test_doc_reader_marks_soft_breaks_and_drops_field_codes(tmp_path):
    text = ("Заголовок\rПервая строка,\x0bвторая строка\r"
            "Почта: \x13HYPERLINK \"mailto:a@b.ru\" \\h\x14a@b.ru\x15 конец\r")
    path = tmp_path / "a.doc"
    path.write_bytes(_make_doc(text))
    out = doc_text.extract_text(str(path))
    assert out.split("\n")[:4] == ["Заголовок", "Первая строка,", "\u2028вторая строка", "Почта: a@b.ru конец"]
    assert "HYPERLINK" not in out and out.endswith("\n\u2028")


def test_doc_reader_rejects_non_word_file(tmp_path):
    path = tmp_path / "b.doc"
    path.write_bytes(b"not a word file at all")
    with pytest.raises(doc_text.DocFormatError):
        doc_text.extract_text(str(path))
