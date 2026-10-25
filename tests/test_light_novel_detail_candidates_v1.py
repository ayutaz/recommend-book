import pytest
from recommend_book import light_novel_catalogue_v1 as module


@pytest.mark.parametrize('edition_title', ['同題 : 副題', '同題 = Foreign title', '同題'])
def test_detail_candidates_skip_search_text_but_keep_series_closure(tmp_path, monkeypatch, edition_title):
    cat = module.LightNovelCatalogue(tmp_path / 'detail.sqlite')
    records = {
        'lne:ndl:I1': {'title': edition_title, 'authors': ['著者']},
        'lns:wd:Q1': {'id': 'Q1', 'source': 'wikidata', 'title': '同題', 'relations': {'P527': ['Q2']}},
        'lns:wd:Q2': {'id': 'Q2', 'source': 'wikidata', 'title': '続編'},
        'lns:wd:Q3': {'id': 'Q3', 'source': 'wikidata', 'title': '別作品'},
    }
    monkeypatch.setattr(module, '_search_text', lambda *_: pytest.fail('detail must not build unused query text'))
    assert cat._candidate_ids(records, entry_id='lne:ndl:I1') == {'lne:ndl:I1', 'lns:wd:Q1', 'lns:wd:Q2'}
    assert cat._candidate_ids(records, entry_id='lne:ndl:absent') == set()


def test_query_candidates_still_match_author(tmp_path):
    cat = module.LightNovelCatalogue(tmp_path / 'query.sqlite')
    records = {'lne:ndl:I1': {'title': '題名', 'authors': ['筆名']}, 'lne:ndl:I2': {'title': '別題', 'authors': ['他者']}}
    assert cat._candidate_ids(records, query='筆名') == {'lne:ndl:I1'}


@pytest.mark.parametrize(('value', 'expected'), [
    ('　あそびのかんけい４\t', 'アソビノカンケイ4'),
    ('ＡＢＣ\u2003ß\n', 'abcss'),
    ('ｶﾞぎゔ', 'ガギヴ'),
    ('i\u0307\u001c𠀀', 'i\u0307𠀀'),
    (None, 'none'), (123, '123'),
])
def test_search_normalization_preserves_japanese_unicode_and_whitespace(value, expected):
    assert module.normalize_search(value) == expected
