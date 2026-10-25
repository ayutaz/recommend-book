import pytest
from recommend_book.light_novel_catalogue_v1 import (
    normalize_search, _search_text, _search_without_punctuation, _search_terms, _search_contains,
)


def matches(stored, query):
    key = normalize_search(query)
    return _search_contains(_search_text([stored]), key, _search_without_punctuation(key), _search_terms(query))


@pytest.mark.parametrize('separator', ['～', '~', '〜'])
@pytest.mark.parametrize(('volume', 'expected'), [('2', True), ('3', False)])
def test_decorative_subtitle_boundaries_find_the_complete_title_and_correct_volume(separator, volume, expected):
    assert matches('主書名 : 完全な副書名. ' + volume, '主書名' + separator + '完全な副書名' + separator + '２') is expected


@pytest.mark.parametrize('separator', ['～', '~', '〜'])
def test_subtitle_decoration_does_not_discard_the_subtitle(separator):
    assert not matches('主書名. 2', '主書名' + separator + '完全な副書名' + separator + '２')


def test_discovery_relaxation_preserves_the_base_normalization_and_other_symbols():
    assert normalize_search('主題～副題～') == '主題~副題~'
    assert _search_without_punctuation('主題+副題') == '主題+副題'
    assert not matches('主題副題', '~~~')
