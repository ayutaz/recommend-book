from contextlib import contextmanager
import json
import sqlite3

import pytest

from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue


class Store:
    def __init__(self, path, rows, *, light=False):
        self.db_path, self.rows, self.light, self.calls = path, rows, light, []
        with self._connect() as db:
            db.execute('CREATE TABLE read_status(' + ('entry_id' if light else 'qid') + ' TEXT PRIMARY KEY,status TEXT,updated_at TEXT)')
            if not light:
                db.execute('CREATE TABLE works(qid TEXT PRIMARY KEY,display_allowed INTEGER NOT NULL)')
                db.executemany('INSERT INTO works VALUES(?,?)', [(r['qid'], r.get('display_allowed', 1)) for r in rows])

    @contextmanager
    def _connect(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            yield db

    def search_catalogue(self, *, page=1, page_size=30, exclude_work_qids=None, entry_kind=None, **kw):
        rows = [r for r in self.rows if r.get('display_allowed', 1) and (not entry_kind or r.get('entry_kind', 'work') == entry_kind)]
        rows = [r for r in rows if not (r.get('entry_kind') == 'work' and r.get('qid') in (exclude_work_qids or set()))]
        return dict(items=rows[(page-1)*page_size:page*page_size], total=len(rows), page=page,
                    page_size=page_size, pages=(len(rows)+page_size-1)//page_size)

    def work_detail(self, key):
        return next((r for r in self.rows if r.get('entry_id', r['qid']) == key), None)

    def similar_works(self, key, **kw):
        self.calls.append(('similar', key, kw))
        return {'items': []}

    def set_read_status(self, key, status):
        self.calls.append(('status', key, status))
        return self.work_detail(key)

    def recommend_preferences(self, **kw):
        self.calls.append(('recommend', kw))
        return {'items': [dict(r, score=.9, reasons=[{'kind': 'source_genre'}]) for r in self.rows]}

    def catalogue_stats(self):
        return {'works': len(self.rows), 'searchable_works': len(self.rows), 'series': 2,
                'individual_works': 1, 'editions': 0, 'content_feature_series': 1}

    def catalogue_facets(self):
        return {'languages': [{'value': 'ja', 'count': len(self.rows)}],
                'genres': [{'qid': 'Q1', 'label': 'Fantasy', 'count': len(self.rows)}]}

    def preference_facets(self):
        return {'native_features': [{'label_id': 'theme.growth', 'axis': 'theme', 'label': 'Growth'}]}


@pytest.fixture
def stores(tmp_path):
    world = Store(tmp_path/'world.sqlite', [
        {'qid': 'Q10', 'title': 'World', 'genres': [{'qid': 'Q1', 'label': 'Fantasy'}],
         'source_url': 'https://www.wikidata.org/wiki/Q10'},
        {'qid': 'Q20', 'title': 'Existing volume', 'genres': []}])
    light = Store(tmp_path/'light.sqlite', [
        {'entry_id': 'lns:wd:Q30', 'qid': 'Q30', 'entry_kind': 'series', 'title': 'Series',
         'genres': [{'qid': 'Q1', 'label': 'Fantasy'}], 'llm_features': [],
         'source_url': 'https://www.wikidata.org/wiki/Q30', 'members': [{'qid': 'Q20'}]},
        {'entry_id': 'lns:wd:Q40', 'qid': 'Q40', 'entry_kind': 'series', 'title': 'Other',
         'genres': [], 'llm_features': []},
        {'entry_id': 'lnw:wd:Q20', 'qid': 'Q20', 'entry_kind': 'work', 'title': 'Existing volume',
         'genres': []}], light=True)
    return world, light, UnifiedCatalogue(world, light)


def test_partition_pagination_counts_and_no_duplicate_world_work(stores):
    world, light, combined = stores
    first = combined.search_catalogue(page=1, page_size=3)
    assert first['total'] == 4
    assert [r['title'] for r in first['items']] == ['Series', 'Other', 'World']
    second = combined.search_catalogue(page=2, page_size=3)
    assert [r['qid'] for r in second['items']] == ['Q20']
    assert combined.search_world_catalogue()['total'] == 2
    assert combined.search_catalogue(family='light_novel')['total'] == 3
    assert combined.search_catalogue(entry_kind='series')['total'] == 2
    assert combined.search_catalogue(family='world', entry_kind='series')['total'] == 0


def test_large_page_boundary_has_no_skipped_or_repeated_rows(tmp_path):
    rows = [{'qid': f'Q{i+1}', 'title': str(i)} for i in range(210)]
    world = Store(tmp_path/'world.sqlite', rows)
    light = Store(tmp_path/'light.sqlite', [], light=True)
    combined = UnifiedCatalogue(world, light)
    assert [r['qid'] for r in combined.search_catalogue(page=4, page_size=30)['items']] == [f'Q{i+1}' for i in range(90, 120)]


def test_light_novel_page_authenticates_only_the_requested_window(tmp_path, monkeypatch):
    rows = [{'entry_id': f'lns:wd:Q{i+1}', 'qid': f'Q{i+1}', 'entry_kind': 'series',
             'title': str(i)} for i in range(210)]
    world = Store(tmp_path/'world.sqlite', [])
    light = Store(tmp_path/'light.sqlite', rows, light=True)
    search = light.search_catalogue
    verified = []

    def record_window(**kwargs):
        result = search(**kwargs)
        verified.extend(row['entry_id'] for row in result['items'])
        return result

    monkeypatch.setattr(light, 'search_catalogue', record_window)
    result = UnifiedCatalogue(world, light).search_catalogue(family='light_novel', page=4, page_size=8)
    expected = [f'lns:wd:Q{i+1}' for i in range(24, 32)]
    assert [row['entry_id'] for row in result['items']] == expected
    assert result['total'] == 210 and result['pages'] == 27
    assert verified == expected


def test_light_novel_recommendation_does_not_expand_native_card_validation(stores, monkeypatch):
    _, light, combined = stores
    verified = []

    def recommend(**kwargs):
        # Each returned card stands for its authenticated classification chain.
        candidates = [dict(light.rows[1], entry_id=f'lns:wd:Q{i+100}', qid=f'Q{i+100}')
                      for i in range(100)]
        selected = candidates[:kwargs['limit']]
        verified.extend(row['entry_id'] for row in selected)
        return {'items': selected}

    monkeypatch.setattr(light, 'recommend_preferences', recommend)
    result = combined.recommend_preferences(liked_entry_ids=['lns:wd:Q30'], family='light_novel', limit=8)
    assert len(result['items']) == len(verified) == 8


def test_world_reference_keeps_candidates_for_post_filter_exclusion(stores, monkeypatch):
    _, light, combined = stores

    def recommend(**kwargs):
        candidates = [
            {'entry_id': 'lnw:wd:Q10', 'qid': 'Q10', 'entry_kind': 'work', 'title': 'Liked world work'},
            light.rows[1],
        ]
        return {'items': candidates[:kwargs['limit']]}

    monkeypatch.setattr(light, 'recommend_preferences', recommend)
    result = combined.recommend_preferences(liked_qids=['Q10'], family='light_novel', limit=1)
    assert [row['entry_id'] for row in result['items']] == ['lns:wd:Q40']


def test_hidden_world_row_does_not_hide_independently_admitted_light_novel(tmp_path):
    world = Store(tmp_path/'world.sqlite', [{'qid': 'Q20', 'title': 'Held', 'display_allowed': 0}])
    light = Store(tmp_path/'light.sqlite', [
        {'entry_id': 'lnw:wd:Q20', 'qid': 'Q20', 'entry_kind': 'work', 'title': 'Admitted'}], light=True)
    result = UnifiedCatalogue(world, light).search_catalogue()
    assert result['total'] == 1
    assert result['items'][0]['entry_id'] == 'lnw:wd:Q20'


def test_typed_routes_do_not_enter_legacy_qid_contract(stores):
    world, light, combined = stores
    assert combined.work_detail('lns:wd:Q30')['entry_kind'] == 'series'
    assert combined.work_detail('Q10')['title'] == 'World'
    combined.similar_works('lns:wd:Q30')
    combined.set_read_status('lns:wd:Q30', 'reading')
    assert not world.calls
    assert [x[0] for x in light.calls] == ['similar', 'status']
    with pytest.raises(ValueError):
        combined.work_detail('../../private')


def test_ndl_authority_backed_series_is_not_fabricated_as_a_wikidata_work(stores):
    world, light, combined = stores
    key = 'lns:ndl:R100000002-I032593374'
    light.rows.append({'entry_id': key, 'qid': None, 'entry_kind': 'series',
                       'title': 'NDL series', 'genres': [], 'original_language': None})
    row = combined.work_detail(key)
    assert row['entry_kind'] == 'series' and row['qid'] is None
    assert row['original_language'] is None
    combined.set_read_status(key, 'reading')
    assert light.calls[-1] == ('status', key, 'reading')
    assert not world.calls


def test_recommendation_bridge_never_promotes_series_features_to_volume_labels(stores):
    world, light, combined = stores
    result = combined.recommend_preferences(liked_entry_ids=['lns:wd:Q30'], family='light_novel')
    assert [r['entry_id'] for r in result['items']] == ['lns:wd:Q40']
    assert not world.calls
    assert light.calls[-1][1]['liked_entry_ids'] == ['lns:wd:Q30']
    result = combined.recommend_preferences(liked_qids=['Q10'], family='light_novel')
    references = light.calls[-1][1]['reference_genres']
    assert references[0]['qid'] == 'Q1' and references[0]['scope'] == 'work'
    assert all(r.get('qid') != 'Q10' for r in result['items'])


def test_world_stats_are_not_inflated_by_series_or_editions(stores):
    _, _, combined = stores
    values = combined.catalogue_stats()
    assert values['works'] == values['searchable_works'] == 2
    assert values['light_novel_series'] == 2
    assert values['light_novel_featured_series'] == 1
    assert combined.catalogue_facets()['languages'] == [{'value': 'ja', 'count': 5}]


def test_history_export_is_typed_private_and_deletion_is_fenced_across_both_databases(stores):
    world, light, combined = stores
    stamp = '2026-10-24T00:00:00+00:00'
    with world._connect() as db:
        db.execute('INSERT INTO read_status VALUES(?,?,?)', ('Q10', 'read', stamp))
    with light._connect() as db:
        db.execute('INSERT INTO read_status VALUES(?,?,?)', ('lns:wd:Q30', 'reading', stamp))
    exported = combined.export_reading_history()
    assert exported['record_count'] == 2
    assert {r['entry_id'] for r in exported['records']} == {'Q10', 'lns:wd:Q30'}
    assert str(world.db_path) not in json.dumps(exported)
    with light._connect() as db:
        db.execute("UPDATE read_status SET status='read'")
    with pytest.raises(ValueError):
        combined.delete_reading_history(export_sha256=exported['sha256'], confirmation='DELETE_READ_STATUS')
    assert combined.export_reading_history()['record_count'] == 2
    current = combined.export_reading_history()
    deleted = combined.delete_reading_history(export_sha256=current['sha256'], confirmation='DELETE_READ_STATUS')
    assert deleted['deleted_count'] == 2
    assert combined.export_reading_history()['record_count'] == 0
    assert world.search_catalogue()['total'] == 2
    assert light.search_catalogue()['total'] == 3
