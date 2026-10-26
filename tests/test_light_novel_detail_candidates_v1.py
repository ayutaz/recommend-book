import hashlib
import json
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue

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



def records():
    return {
        'lns:wd:Q1': {'id': 'Q1', 'source': 'wikidata', 'title': '本編',
            'aliases': [], 'relations': {'P527': ['Q4']}},
        'lnw:wd:Q4': {'id': 'Q4', 'source': 'wikidata', 'title': '関連する巻',
            'aliases': [], 'relations': {}},
        # A competing series title must still widen the verification scope
        # when reached through the work's title, rather than the requested ID.
        'lns:wd:Q2': {'id': 'Q2', 'source': 'wikidata', 'title': '別シリーズ',
            'aliases': ['関連する巻'], 'relations': {'P179': ['Q3']}},
        'lns:wd:Q3': {'id': 'Q3', 'source': 'wikidata', 'title': '上位シリーズ',
            'aliases': [], 'relations': {}},
        'lne:ndl:R100000002-E1': {'id': 'R100000002-E1', 'source': 'ndl',
            'title': '所属する版', 'aliases': [], 'relations': {}},
        'lne:ndl:R100000002-E2': {'id': 'R100000002-E2', 'source': 'ndl',
            'title': '無関係な版', 'aliases': [], 'relations': {}},
        'lnr:pub:tobooks:' + 'a' * 32: {'title': '関連する巻', 'aliases': [], 'relations': {}},
    }


def test_detail_scope_keeps_recursive_relations_memberships_and_competing_series(tmp_path):
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    with cat._connect() as db:
        for identity, kind in [('lne:ndl:R100000002-E1', 'edition'), ('lns:wd:Q1', 'series')]:
            payload = json.dumps(records()[identity])
            db.execute('INSERT INTO entries VALUES(?,?,?,?)',
                       (identity, kind, payload, hashlib.sha256(payload.encode()).hexdigest()))
        db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)',
                   ('lne:ndl:R100000002-E1', 'lns:wd:Q1', '{}', 'a' * 64))
    assert cat._candidate_ids(records(), entry_id='lns:wd:Q1') == {
        'lns:wd:Q1', 'lnw:wd:Q4', 'lns:wd:Q2', 'lns:wd:Q3', 'lne:ndl:R100000002-E1'}


def test_exact_detail_of_missing_entry_is_empty_and_search_still_finds_unrelated_title(tmp_path):
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    assert cat._candidate_ids(records(), entry_id='lns:wd:Q99') == set()
    assert cat._candidate_ids(records(), query='無関係な版') == {'lne:ndl:R100000002-E2'}
