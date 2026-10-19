"""Сверка статьи с PDF: поиск раздела литературы в макете."""
import metafora_check as mc


def _article(refs, refs_en):
    return {"title": "Т", "title_en": "T", "fpage": 1, "lpage": 1, "authors": [], "abstract": "", "abstract_en": "",
            "keywords": [], "keywords_en": [], "udk": [], "received": None, "accepted": None,
            "refs": refs, "refs_en": refs_en, "raw": ""}


def _warns(text, refs, refs_en):
    P = {"p1": "", "all": text, "n": 1}
    return mc.check_article(_article(refs, refs_en), P)[1]


def test_latin_c_in_heading_is_found():
    """В макете заголовок бывает «Cписок» с латинской C — раздел всё равно находится."""
    ru = "Иванов И. Первая работа. 2020.\n"
    text = "Cписок источников\n1.\n" + ru + "Информация об авторах\nReferences\n1.\nIvanov I. First. 2020.\nInformation about the authors"
    w = _warns(text, ["Иванов И. Первая работа. 2020."], ["Ivanov I. First. 2020."])
    assert not [x for x in w if "раздел литературы" in x]


def test_unnumbered_list_is_checked_by_order():
    """Английский список без номеров: ссылки по порядку и ничего лишнего после последней — не замечание."""
    refs = ["Alpha A., Beta B. First long title of the work. 2020.", "Gamma G. Second long title of the work. 2021."]
    text = ("Список источников\n1.\nИванов И. Первая. 2020.\nИнформация об авторе\nReferences\n" + refs[0] + "\n" + refs[1]
            + "\nInformation about the author")
    w = _warns(text, ["Иванов И. Первая. 2020."], refs)
    assert not [x for x in w if "нумерацию" in x]


def test_unnumbered_list_with_extra_tail_warns():
    refs = ["Alpha A., Beta B. First long title of the work. 2020."]
    text = ("Список источников\n1.\nИванов И. Первая. 2020.\nИнформация об авторе\nReferences\n" + refs[0]
            + "\nОдна лишняя ссылка которой нет в рукописи и она длиннее любой допустимой погрешности.\nInformation about the author")
    assert [x for x in _warns(text, ["Иванов И. Первая. 2020."], refs) if "нумерацию" in x]


def test_pdf_numbering_does_not_count_the_tail_of_a_split_url_as_a_reference():
    """В печатном PDF вёрстка разорвала адрес и дала хвосту свой номер («6. _Rate (дата обращения…)») — это не ссылка."""
    seg = "1. \nПервая. 2021.\n2. \nВторая. 2022.\n3. \nURL: https://x.net/Resp\n4. \n_Rate (дата обращения: 16.07.2025).\n5. \nПятая. 2023.\n"
    assert mc._numbered(seg) == 4
    assert mc._numbered("1. A\n2. B\n3. C\n") == 3 and mc._numbered(None) is None


def test_pdf_numbering_ignores_a_page_range_wrapped_to_line_start():
    """«P. 278–⏎282.» — число с переноса строки в начале строки не должно проглатывать номер следующего пункта."""
    seg = "1. \nПервая. 2021.\n2. \nВторая. 2022.\n3. \nТретья // 2025. P. 278–\n282. \n4. \nЧетвёртая. 2023.\n5. \nПятая.\n6. \nШестая.\n"
    assert mc._numbered(seg) == 6
