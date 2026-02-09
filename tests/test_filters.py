"""
Тесты Jinja2-фильтров и утилит.
"""

from app import pluralize_ru


class TestPluralizeRu:
    """Тест фильтра склонения русских слов."""

    def test_one(self):
        assert pluralize_ru(1, 'выпуск', 'выпуска', 'выпусков') == 'выпуск'
        assert pluralize_ru(21, 'выпуск', 'выпуска', 'выпусков') == 'выпуск'
        assert pluralize_ru(101, 'выпуск', 'выпуска', 'выпусков') == 'выпуск'

    def test_few(self):
        assert pluralize_ru(2, 'выпуск', 'выпуска', 'выпусков') == 'выпуска'
        assert pluralize_ru(3, 'выпуск', 'выпуска', 'выпусков') == 'выпуска'
        assert pluralize_ru(4, 'выпуск', 'выпуска', 'выпусков') == 'выпуска'
        assert pluralize_ru(24, 'выпуск', 'выпуска', 'выпусков') == 'выпуска'

    def test_many(self):
        assert pluralize_ru(5, 'выпуск', 'выпуска', 'выпусков') == 'выпусков'
        assert pluralize_ru(10, 'выпуск', 'выпуска', 'выпусков') == 'выпусков'
        assert pluralize_ru(20, 'выпуск', 'выпуска', 'выпусков') == 'выпусков'

    def test_teens(self):
        """11-19 всегда используют form5 (выпусков)."""
        for n in range(11, 20):
            assert pluralize_ru(n, 'выпуск', 'выпуска', 'выпусков') == 'выпусков'

    def test_zero(self):
        assert pluralize_ru(0, 'выпуск', 'выпуска', 'выпусков') == 'выпусков'

    def test_large_numbers(self):
        assert pluralize_ru(111, 'статья', 'статьи', 'статей') == 'статей'
        assert pluralize_ru(112, 'статья', 'статьи', 'статей') == 'статей'
        assert pluralize_ru(121, 'статья', 'статьи', 'статей') == 'статья'
        assert pluralize_ru(1000, 'статья', 'статьи', 'статей') == 'статей'
