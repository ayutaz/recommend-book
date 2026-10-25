from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue, _light_id
from test_light_novel_catalogue_v1 import (
    futaba_source_group_bundle, futaba_source_raw, membership_bundle, edition_bundle, setup_catalogue,
    orange_source_group_bundle, ecobalt_source_group_bundle, sbcr_source_group_bundle, hj_source_group_bundle,
    arian_source_group_bundle, hjnovels_source_group_bundle, sbcrindex_source_group_bundle,
)


@pytest.mark.parametrize('identifier', [
    'lnr:pub:ecobalt:' + 'a' * 32,
    'lnr:pub:futaba:' + 'b' * 32,
])
def test_publisher_reference_identity_accepts_only_admitted_provider(identifier):
    assert _light_id(identifier) is True
    with pytest.raises(ValueError):
        _light_id('lnr:pub:unknown:' + 'c' * 32)


@pytest.mark.parametrize('source_keys', [('genres', 'languages'), (), ('genres',)])
def test_preference_facets_reuses_validated_world_filters_without_duplicate_aggregation(source_keys):
    calls = {'preference': 0, 'world_filters': 0, 'light_filters': 0}
    native = [{'label_id': 'theme.growth', 'axis': 'theme', 'label': 'Growth'}]
    filters = {'genres': [{'qid': 'Q1', 'label': 'Fantasy', 'count': 2}],
               'languages': [{'value': 'ja', 'count': 2}]}
    preference = {'native_features': native, **{key: filters[key] for key in source_keys}}
    def world_preference():
        calls['preference'] += 1
        return preference
    def world_filters():
        calls['world_filters'] += 1
        if len(source_keys) == 2:
            raise AssertionError('validated world filters must not be aggregated again')
        return filters
    def light_filters():
        calls['light_filters'] += 1
        return {'genres': [{'qid': 'Q1', 'label': 'Fantasy', 'count': 3},
                           {'qid': 'Q2', 'label': 'Mystery', 'count': 1}],
                'languages': [{'value': 'ja', 'count': 3}, {'value': 'en', 'count': 1}]}
    world = SimpleNamespace(db_path=None, preference_facets=world_preference, catalogue_facets=world_filters)
    light = SimpleNamespace(catalogue_facets=light_filters)
    result = UnifiedCatalogue(world, light).preference_facets()
    assert result == {'native_features': native,
        'genres': [{'qid': 'Q1', 'label': 'Fantasy', 'count': 5}, {'qid': 'Q2', 'label': 'Mystery', 'count': 1}],
        'languages': [{'value': 'ja', 'count': 5}, {'value': 'en', 'count': 1}]}
    assert result['native_features'] is native
    assert calls == {'preference': 1, 'world_filters': 0 if len(source_keys) == 2 else 1, 'light_filters': 1}
    assert filters['genres'][0]['count'] == 2 and filters['languages'][0]['count'] == 2


class Store:
    def __init__(self, path, rows, *, light=False):
        self.db_path, self.rows, self.light, self.calls = path, rows, light, []
        self.display = 'allowed'
        with self._connect() as db:
            db.execute('CREATE TABLE read_status(' + ('entry_id' if light else 'qid') + ' TEXT PRIMARY KEY,status TEXT,updated_at TEXT)')
            if not light:
                db.execute('CREATE TABLE works(qid TEXT PRIMARY KEY,display_allowed INTEGER NOT NULL,search_text TEXT,language TEXT,year INTEGER)')
                db.executemany('INSERT INTO works VALUES(?,?,?,?,?)', [
                    (r['qid'], r.get('display_allowed', 1), self._search_text(r),
                     r.get('original_language'), r.get('year')) for r in rows])
                db.execute('CREATE TABLE work_genres(qid TEXT,genre_qid TEXT)')
                db.executemany('INSERT INTO work_genres VALUES(?,?)', [
                    (r['qid'], g['qid']) for r in rows for g in r.get('genres', [])])

    @staticmethod
    def _search_text(row):
        return ' '.join([row['title'], *row.get('aliases', []),
                         *[a.get('name', '') for a in row.get('authors', [])]]).casefold()

    def _conditions(self):
        return {}, {'display': self.display}

    @contextmanager
    def _connect(self):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            yield db

    def search_catalogue(self, *, page=1, page_size=30, exclude_work_qids=None, entry_kind=None, **kw):
        rows = [r for r in self.rows if r.get('display_allowed', 1) and (not entry_kind or r.get('entry_kind', 'work') == entry_kind)]
        if not self.light and self.display != 'allowed':
            rows = []
        if kw.get('query', '').strip():
            rows = [r for r in rows if kw['query'].strip().casefold() in self._search_text(r)]
        if kw.get('genre'):
            rows = [r for r in rows if kw['genre'] in {g['qid'] for g in r.get('genres', [])}]
        if kw.get('language'):
            rows = [r for r in rows if r.get('original_language') == kw['language']]
        for key, operator in [('year_from', lambda a, b: a >= b), ('year_to', lambda a, b: a <= b)]:
            if kw.get(key) not in (None, ''):
                rows = [r for r in rows if r.get('year') is not None and operator(r['year'], int(kw[key]))]
        with self._connect() as db:
            statuses = dict(db.execute('SELECT ' + ('entry_id' if self.light else 'qid') + ',status FROM read_status'))
        read_filter = kw.get('read_filter', 'all')
        if read_filter != 'all':
            rows = [r for r in rows if (statuses.get(r.get('entry_id', r['qid']), 'unknown') != 'read'
                    if read_filter == 'exclude_read' else statuses.get(r.get('entry_id', r['qid']), 'unknown') == read_filter)]
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


@pytest.mark.parametrize('provider, marker, title', [
    ('kadokawa', 'kadokawa_product_reference', '電子掲載書誌'),
    ('kadokawaprint', 'kadokawa_print_product_reference', '紙掲載書誌'),
    ('kadokawatrade', 'kadokawa_trade_product_reference', '単行本掲載書誌'),
    ('tobooks', 'tobooks_product_reference', 'TO掲載書誌'),
    ('gagaga', 'gagaga_product_reference', 'ガガガ掲載書誌'),
    ('kodansha', 'kodansha_product_reference', '講談社掲載書誌'),
    ('overlapdetails', 'overlap_detail_product_reference', 'OVERLAP掲載書誌'),
    ('shueishadx', 'shueisha_dx_product_reference', 'DX掲載書誌'),
    ('gc', 'gc_product_reference', 'GC掲載書誌'),
    ('overlap', 'overlap_bunko_product_reference', 'OVL文庫掲載書誌'),
    ('alphapolis', 'alphapolis_commercial_book_reference', 'Alpha掲載書誌'),
    ('hifumi', 'hifumi_product_reference', 'Hifumi掲載書誌'),
])
def test_kadokawa_reference_detail_routes_through_unified_http_without_world_lookup(stores, provider, marker, title):
    import threading
    from urllib.parse import quote
    from urllib.request import Request, urlopen
    from recommend_book.recommendation.scale_site_v1 import create_server

    world, light, combined = stores
    identity = 'lnr:pub:' + provider + ':' + 'd' * 32
    light.rows.append({'entry_id': identity, 'qid': '', 'entry_kind': 'publisher_reference',
        'title': title, 'reference_identity_basis': 'publisher_product_reference',
        'publisher_reference_kind': marker,
        'publisher_product_reference': {'title': title, 'form': '単行本' if provider == 'kadokawatrade' else None,
            'publisher_reference_id': 'hifumi-product:opaque_1' if provider == 'hifumi' else 'alphapolis-commercial-book:1/2' if provider == 'alphapolis' else 'overlap-product:opaque_1' if provider == 'overlap' else
                'overlap-nvl-product:opaque_1' if provider == 'overlapdetails' else '978-0-306-40615-7',
            'label': 'サーガフォレスト' if provider == 'hifumi' else 'アルファポリス' if provider == 'alphapolis' else 'オーバーラップ文庫' if provider == 'overlap' else 'ダッシュエックス文庫' if provider == 'shueishadx' else None,
            'title_literal': title, 'subtitle_literal': '', 'complete_display_title': title},
        'reference_scope': 'private_bibliographic_discovery',
        'source_url': {'tobooks': 'https://www.tobooks.jp/contents/12345',
                       'gagaga': 'https://gagagabunko.jp/lineup/202609.html#02',
                       'overlapdetails': 'https://over-lap.co.jp/test/product/0/opaque_1/?cat=NVL&swrd=',
                       'shueishadx': 'https://dash.shueisha.co.jp/bookDetail/index/978-0-306-40615-7',
                       'gc': 'https://gcnovels.jp/book/123',
                       'overlap': 'https://over-lap.co.jp/test/product/0/opaque_1/?cat=BNK&swrd=',
                       'alphapolis': 'https://www.alphapolis.co.jp/book/detail/1/2',
                       'hifumi': 'https://hifumi.co.jp/lineup/opaque_1/',
                       'kodansha': 'https://www.kodansha.co.jp/comic/products/0000431602'}.get(
                           provider, 'https://www.kadokawa.co.jp/product/123456789012/')})
    world.work_detail = lambda key: pytest.fail('KAD reference must not use the world store')
    world.set_read_status = lambda *args: pytest.fail('KAD reference read state must not use the world store')
    server = create_server(catalogue=combined, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/entries/' + quote(identity, safe=''), timeout=5) as response:
            result = json.load(response)
        assert result['entry_id'] == identity
        assert result['entry_kind'] == 'publisher_reference'
        assert result['reference_scope'] == 'private_bibliographic_discovery'
        assert result['title'] == title
        assert result['publisher_reference_kind'] == marker
        request = Request(f'http://127.0.0.1:{server.server_port}/api/entries/' + quote(identity, safe='') + '/read-status',
            data=b'{"status":"reading"}', headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1'})
        with urlopen(request, timeout=5) as response:
            assert json.load(response)['entry_id'] == identity
        assert light.calls == [('status', identity, 'reading')]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


@pytest.mark.parametrize('filters,world_fields,light_fields', [
    ({'query': '日本語別題'}, {}, {'aliases': ['日本語別題']}),
    ({'query': '100%'}, {'title': '100 percent'}, {'title': '100%'}),
    ({'query': 'a_b'}, {'title': 'aXb'}, {'title': 'a_b'}),
    ({'genre': 'Q1'}, {'genres': [{'qid': 'Q2'}]}, {}),
    ({'language': 'ja'}, {'original_language': 'en'}, {}),
    ({'year_from': 2000}, {'year': 1990}, {}),
    ({'year_to': 2020}, {'year': 2030}, {}),
    ({'read_filter': 'exclude_read'}, {'read_status': 'read'}, {}),
    ({'read_filter': 'unknown'}, {'read_status': 'reading'}, {}),
])
def test_only_matching_world_works_suppress_light_novel_duplicates(tmp_path, filters, world_fields, light_fields):
    base = {'qid': 'Q20', 'title': 'Shared', 'genres': [{'qid': 'Q1'}],
            'original_language': 'ja', 'year': 2010}
    world = Store(tmp_path/'world.sqlite', [{**base, **world_fields}])
    light = Store(tmp_path/'light.sqlite', [
        {**base, 'entry_id': 'lnw:wd:Q20', 'entry_kind': 'work', **light_fields}], light=True)
    if world_fields.get('read_status'):
        with world._connect() as db:
            db.execute('INSERT INTO read_status VALUES(?,?,?)', ('Q20', world_fields['read_status'], '2026-10-25'))
    combined = UnifiedCatalogue(world, light)
    result = combined.search_catalogue(page_size=1, **filters)
    assert result['total'] == result['pages'] == 1
    assert [r['entry_id'] for r in result['items']] == ['lnw:wd:Q20']
    assert combined.search_catalogue(page=2, page_size=1, **filters)['items'] == []


def test_matching_world_work_still_suppresses_duplicate_and_honors_display_policy(tmp_path):
    base = {'qid': 'Q20', 'title': 'Shared', 'genres': [{'qid': 'Q1'}],
            'original_language': 'ja', 'year': 2010}
    world = Store(tmp_path/'world.sqlite', [base])
    light = Store(tmp_path/'light.sqlite', [
        {**base, 'entry_id': 'lnw:wd:Q20', 'entry_kind': 'work'}], light=True)
    combined = UnifiedCatalogue(world, light)
    filters = dict(query='shared', genre='Q1', language='ja', year_from=2000, year_to=2020,
                   read_filter='exclude_read')
    result = combined.search_catalogue(**filters)
    assert result['total'] == 1
    assert 'entry_id' not in result['items'][0]
    assert combined._world_qids(genre='wd:Q1') == {'Q20'}
    world.display = 'hold'
    result = combined.search_catalogue(**filters)
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


def test_source_publisher_group_real_catalogue_typed_search_preferences_and_history(
        futaba_source_group_bundle, membership_bundle, tmp_path):
    cat, _, path, sha, _ = futaba_source_group_bundle
    _, series, edition, _, _ = membership_bundle
    assert cat.ingest([series])['inserted'] == 1
    assert cat.import_editions([edition])['inserted'] == 1
    cat.set_read_status('lns:wd:Q1', 'reading')
    cat.set_read_status('lne:ndl:R100000002-I1', 'read')
    identities = cat.import_futaba_source_groups(path, sha)['reference_entries']
    identity = next(iter(identities.values()))
    world = Store(tmp_path / 'source-group-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    before = combined.export_reading_history()
    assert before['version'] == 'catalogue-history-export-v2'
    assert before['record_count'] == 2
    assert combined.search_catalogue(family='light_novel', entry_kind='publisher_group')['total'] == 4
    assert combined.search_catalogue(family='light_novel', entry_kind='series')['total'] == 5
    assert combined.work_detail(identity)['entry_kind'] == 'publisher_group'
    assert combined.recommend_preferences(liked_entry_ids=[identity], family='light_novel')['items'] == []
    combined.set_read_status(identity, 'reading')
    exported = combined.export_reading_history()
    assert exported['version'] == 'catalogue-history-export-v3'
    assert [r for r in exported['records'] if r['entry_id'] != identity] == before['records']
    group_status = next(r for r in exported['records'] if r['entry_id'] == identity)
    assert group_status['entry_kind'] == 'publisher_group'
    assert str(path.parent) not in json.dumps(exported)
    with pytest.raises(ValueError):
        combined.delete_reading_history(export_sha256=before['sha256'], confirmation='DELETE_READ_STATUS')
    combined.delete_reading_history(export_sha256=exported['sha256'], confirmation='DELETE_READ_STATUS')
    assert combined.export_reading_history()['version'] == 'catalogue-history-export-v2'
    assert combined.export_reading_history()['record_count'] == 0
    assert cat.work_detail(identity)['read_status'] == 'unknown'
    assert not world.calls


def test_source_publisher_group_similar_stops_before_full_catalogue_or_unrelated_raw(
        futaba_source_group_bundle, membership_bundle, tmp_path, monkeypatch):
    from pathlib import Path
    cat, env, path, sha, _ = futaba_source_group_bundle
    identity = next(iter(cat.import_futaba_source_groups(path, sha)['reference_entries'].values()))
    _, _, edition, _, _ = membership_bundle
    assert cat.import_editions([edition])['inserted'] == 1
    Path(edition['provenance']['raw_path']).write_bytes(b'unrelated raw must remain unread')
    def forbidden(*args, **kwargs):
        raise AssertionError('featureless publisher group cannot scan catalogue or world')
    monkeypatch.setattr(cat, '_rows', forbidden)
    monkeypatch.setattr(cat, 'recommend_preferences', forbidden)
    world = Store(tmp_path / 'early-source-group-world.sqlite', [])
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'search_catalogue', forbidden)
    combined = UnifiedCatalogue(world, cat)
    result = combined.similar_works(identity, limit=8)
    assert result['mode'] == 'insufficient_evidence' and result['items'] == []
    assert result['classification_scope'] == 'publisher_group'
    assert not world.calls
    (env['root'] / 'm-page0.json').write_bytes(b'changed own group witness')
    with pytest.raises(ValueError):
        combined.similar_works(identity, limit=8)


def test_orange_source_group_typed_history_and_early_similar_preserve_other_provider(
        orange_source_group_bundle, futaba_source_group_bundle, tmp_path, monkeypatch):
    _, _, path, sha, groups = orange_source_group_bundle
    cat, env, fpath, fsha, _ = futaba_source_group_bundle
    fids = cat.import_futaba_source_groups(fpath, fsha)['reference_entries']
    cat.set_read_status(next(iter(fids.values())), 'reading')
    world = Store(tmp_path / 'orange-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    before = combined.export_reading_history()
    result = cat.import_orange_source_groups(path, sha)
    identity = result['reference_entries'][groups[0]['reference_id']]
    assert combined.work_detail(identity)['publisher_product_references'][0]['credit_literals'][1]['role_literal'] == '装画'
    combined.set_read_status(identity, 'read')
    after = combined.export_reading_history()
    assert after['version'] == 'catalogue-history-export-v3'
    assert [r for r in after['records'] if r['entry_id'] != identity] == before['records']
    for invalid in ['lng:pub:other:' + 'a'*32, 'lng:pub:orange:' + 'A'*32, 'lng:pub:orange:Q1']:
        with pytest.raises(ValueError):
            combined.work_detail(invalid)
    def forbidden(*args, **kwargs):
        raise AssertionError('Orange similar cannot replay Futaba or scan full catalogues')
    (env['root'] / 'm-page0.json').write_bytes(b'old provider raw must remain unread')
    monkeypatch.setattr(cat, '_futaba_workgroups', forbidden)
    monkeypatch.setattr(cat, '_rows', forbidden)
    monkeypatch.setattr(cat, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    assert combined.similar_works(identity)['mode'] == 'insufficient_evidence'
    assert not world.calls


def test_ecobalt_source_group_typed_display_history_and_early_similar(
        ecobalt_source_group_bundle, tmp_path, monkeypatch):
    cat, env, path, sha, groups = ecobalt_source_group_bundle
    world = Store(tmp_path / 'ecobalt-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    assert combined.export_reading_history()['version'] == 'catalogue-history-export-v2'
    identity = cat.import_ecobalt_source_groups(path, sha)['reference_entries'][groups[2]['reference_id']]
    row = combined.work_detail(identity)
    assert row['heading_author_display'] == '甲/乙' and row['authors'] == []
    assert row['publisher_product_references'][0]['publisher_reference_id'] == 'shueisha-ebook:9781234567897'
    combined.set_read_status(identity, 'read')
    history = combined.export_reading_history()
    assert history['version'] == 'catalogue-history-export-v3'
    assert history['records'][0]['entry_id'] == identity and history['records'][0]['entry_kind'] == 'publisher_group'
    for invalid in ['lng:pub:ecobalt:' + 'A'*32, 'lng:pub:ecobalt:sc1', 'lng:pub:ecobalt:' + 'a'*31]:
        with pytest.raises(ValueError):
            combined.work_detail(invalid)
    def forbidden(*args, **kwargs):
        raise AssertionError('eCobalt similar must authenticate its own source without other catalogues')
    monkeypatch.setattr(cat, '_futaba_workgroups', forbidden)
    monkeypatch.setattr(cat, '_orange_workgroups', forbidden)
    monkeypatch.setattr(cat, '_rows', forbidden)
    monkeypatch.setattr(cat, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'search_catalogue', forbidden)
    assert combined.similar_works(identity)['mode'] == 'insufficient_evidence'
    assert not world.calls
    (env['root'] / 'page.html').write_bytes(b'changed own source')
    with pytest.raises(ValueError):
        combined.similar_works(identity)


def test_ecobalt_publisher_reference_history_v4_retains_group_v3_and_old_typed_records(
        ecobalt_source_group_bundle, tmp_path):
    cat, _, path, sha, _ = ecobalt_source_group_bundle
    world = Store(tmp_path / 'ecobalt-ref-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    assert combined.export_reading_history()['version'] == 'catalogue-history-export-v2'
    group = next(iter(cat.import_ecobalt_source_groups(path, sha)['reference_entries'].values()))
    combined.set_read_status(group, 'read')
    before = combined.export_reading_history()
    assert before['version'] == 'catalogue-history-export-v3'
    identity = next(iter(cat.import_ecobalt_publisher_references(path, sha)['reference_entries'].values()))
    assert combined.export_reading_history() == before  # Merely importing cannot upgrade old history.
    assert combined.work_detail(identity)['entry_kind'] == 'publisher_reference'
    assert combined.search_catalogue(family='light_novel', entry_kind='publisher_reference')['total'] == 3
    assert combined.catalogue_stats()['light_novel_publisher_references'] == 3
    combined.set_read_status(identity, 'reading')
    after = combined.export_reading_history()
    assert after['version'] == 'catalogue-history-export-v4'
    assert [r for r in after['records'] if r['entry_id'] != identity] == before['records']
    assert next(r for r in after['records'] if r['entry_id'] == identity)['entry_kind'] == 'publisher_reference'
    assert combined.work_detail(group)['read_status'] == 'read'
    for invalid in ['lnr:pub:orange:' + 'a'*32, 'lnr:pub:ecobalt:' + 'A'*32, 'lnr:pub:ecobalt:sc0']:
        with pytest.raises(ValueError):
            combined.work_detail(invalid)


def test_ecobalt_publisher_reference_similar_authenticates_only_own_frame_before_early_exit(
        ecobalt_source_group_bundle, tmp_path, monkeypatch):
    cat, env, path, sha, _ = ecobalt_source_group_bundle
    world = Store(tmp_path / 'ecobalt-ref-similar-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    identity = next(iter(cat.import_ecobalt_publisher_references(path, sha)['reference_entries'].values()))
    def forbidden(*args, **kwargs):
        raise AssertionError('leaf similarity cannot replay groups, features, or full/world catalogues')
    for name in ['_rows', '_futaba_workgroups', '_orange_workgroups', '_ecobalt_workgroups',
                 '_project_publisher_source_group', '_feature_rows', 'recommend_preferences']:
        monkeypatch.setattr(cat, name, forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'search_catalogue', forbidden)
    result = combined.similar_works(identity)
    assert result['mode'] == 'insufficient_evidence' and result['items'] == []
    assert result['classification_scope'] == 'publisher_reference' and not world.calls
    (env['root'] / 'page.html').write_bytes(b'changed own product reference witness')
    with pytest.raises(ValueError):
        combined.similar_works(identity)


def test_sbcr_source_group_typed_history_unknown_rights_and_early_similar(sbcr_source_group_bundle, tmp_path, monkeypatch):
    cat, env, path, sha, _ = sbcr_source_group_bundle
    world = Store(tmp_path / 'sbcr-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    assert combined.export_reading_history()['version'] == 'catalogue-history-export-v2'
    identity = next(iter(cat.import_sbcr_source_groups(path, sha)['reference_entries'].values()))
    row = combined.work_detail(identity)
    assert row['entry_kind'] == 'publisher_group' and row['publisher_work_group_kind'] == 'sbcr_ebook_source_group'
    assert row['robots_evidence']['permission_inferred'] is False
    assert row['usage_permissions']['commercial_reuse'] == 'unknown'
    combined.set_read_status(identity, 'read')
    history = combined.export_reading_history()
    assert history['version'] == 'catalogue-history-export-v3'
    assert history['records'][0]['entry_id'] == identity and history['records'][0]['entry_kind'] == 'publisher_group'
    assert combined.search_catalogue(family='light_novel', entry_kind='series', query='完全な巻題 2')['total'] == 1
    for invalid in ['lng:pub:sbcr:' + 'A'*32, 'lng:pub:sbcr:7', 'lnr:pub:sbcr:' + 'a'*32]:
        with pytest.raises(ValueError):
            combined.work_detail(invalid)
    def forbidden(*args, **kwargs):
        raise AssertionError('SB similar cannot replay other providers or full/world catalogues')
    for name in ['_rows', '_futaba_workgroups', '_orange_workgroups', '_ecobalt_workgroups', '_ecobalt_source_frame',
                 '_feature_rows', 'recommend_preferences']:
        monkeypatch.setattr(cat, name, forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    monkeypatch.setattr(world, 'search_catalogue', forbidden)
    result = combined.similar_works(identity)
    assert result['mode'] == 'insufficient_evidence' and result['classification_scope'] == 'publisher_group'
    assert not world.calls
    (env['root'] / 'coverage' / 'parent.html').write_bytes(b'changed selected SB source')
    with pytest.raises(ValueError):
        combined.similar_works(identity)


@pytest.mark.parametrize('provider', ['hj', 'arian', 'hjnovels'])
def test_hj_index_source_group_typed_history_and_early_similar_keep_index_scope(request, tmp_path, monkeypatch, provider):
    cat, env, path, sha, groups = request.getfixturevalue(provider + '_source_group_bundle')
    world = Store(tmp_path / 'hj-index-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    identity = next(iter(getattr(cat, 'import_' + provider + '_index_source_groups')(path, sha)['reference_entries'].values()))
    row = combined.work_detail(identity)
    assert row['publisher_work_group_kind'] == ('arian' if provider == 'arian' else 'hj') + '_native_index_source_group'
    assert row['total_product_count'] is row['frame_membership_complete'] is None
    assert combined.search_catalogue(family='light_novel', entry_kind='series', query=row['index_credit_literals'][0]['name'])['total'] == len(groups)
    combined.set_read_status(identity, 'read')
    history = combined.export_reading_history()
    assert history['version'] == 'catalogue-history-export-v3' and history['records'][0]['entry_id'] == identity
    def forbidden(*args, **kwargs):
        raise AssertionError('HJ index similar cannot replay older providers or full/world catalogue')
    for name in ['_rows', '_futaba_workgroups', '_orange_workgroups', '_ecobalt_source_frame', '_sbcr_workgroups',
                 '_feature_rows', 'recommend_preferences']:
        monkeypatch.setattr(cat, name, forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    result = combined.similar_works(identity)
    assert result['mode'] == 'insufficient_evidence' and result['classification_scope'] == 'publisher_group'
    assert not world.calls
    (env['root'] / 'coverage' / ('index.html' if provider == 'arian' else 'page1.html')).write_bytes(b'changed own native index card')
    with pytest.raises(ValueError):
        combined.similar_works(identity)


def test_sbcrindex_source_group_history_and_similarity_keep_index_cautions_without_complete_parent_scan(
        sbcrindex_source_group_bundle, tmp_path, monkeypatch):
    cat, env, path, sha, _ = sbcrindex_source_group_bundle
    world = Store(tmp_path / 'sbcr-index-world.sqlite', [])
    combined = UnifiedCatalogue(world, cat)
    identity = next(iter(cat.import_sbcr_index_source_groups(path, sha)['reference_entries'].values()))
    row = combined.work_detail(identity)
    assert row['publisher_work_group_kind'] == 'sbcr_native_index_source_group'
    assert row['index_name_cautions'] == ['wrapped_index_display_title']
    assert row['title'] == '（シリーズ：完全な索引題！）' and row['total_product_count'] is None
    combined.set_read_status(identity, 'read')
    history = combined.export_reading_history()
    assert history['version'] == 'catalogue-history-export-v3' and history['records'][0]['entry_id'] == identity
    for invalid in ['lng:pub:sbcrindex:' + 'A'*32, 'lnr:pub:sbcrindex:' + 'a'*32, 'lng:pub:sbcrindex:7']:
        with pytest.raises(ValueError):
            combined.work_detail(invalid)
    def forbidden(*args, **kwargs):
        raise AssertionError('SB index-only similar cannot scan complete parents or other catalogues')
    for name in ['_rows', '_sbcr_workgroups', '_hj_index_workgroups', '_hjnovels_index_workgroups',
                 '_arian_index_workgroups', '_ecobalt_source_frame', '_feature_rows', 'recommend_preferences']:
        monkeypatch.setattr(cat, name, forbidden)
    monkeypatch.setattr(world, 'recommend_preferences', forbidden)
    result = combined.similar_works(identity)
    assert result['mode'] == 'insufficient_evidence' and result['classification_scope'] == 'publisher_group'
    assert not world.calls
    (env['root'] / 'coverage' / 'index.html').write_bytes(b'changed own SB index witness')
    with pytest.raises(ValueError):
        combined.similar_works(identity)


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


def test_overview_uses_one_light_novel_projection_and_preserves_world_counts(stores, monkeypatch):
    world, light, combined = stores
    world_stats = {**world.catalogue_stats(), **world.catalogue_facets()}
    light_stats, light_facets = light.catalogue_stats(), light.catalogue_facets()
    calls = []
    def overview():
        calls.append('light')
        return {'stats': light_stats, 'facets': light_facets}
    monkeypatch.setattr(light, 'catalogue_overview', overview, raising=False)
    monkeypatch.setattr(world, 'catalogue_stats', lambda: world_stats)
    monkeypatch.setattr(light, 'catalogue_stats', lambda: pytest.fail('duplicate light stats'))
    monkeypatch.setattr(light, 'catalogue_facets', lambda: pytest.fail('duplicate light facets'))
    monkeypatch.setattr(world, 'catalogue_facets', lambda: pytest.fail('duplicate world filters'))
    result = combined.catalogue_overview()
    assert calls == ['light']
    assert result['stats']['works'] == result['stats']['searchable_works'] == 2
    assert result['stats']['light_novel_series'] == 2
    assert result['preference_facets']['languages'] == [{'value': 'ja', 'count': 5}]
    assert result['preference_facets']['native_features'][0]['label_id'] == 'theme.growth'


def test_publisher_work_group_statistics_are_separate_from_series_and_world(stores, monkeypatch):
    _, light, combined = stores
    stats = light.catalogue_stats()
    monkeypatch.setattr(light, 'catalogue_stats', lambda: {**stats, 'publisher_work_groups': 7,
                                                         'publisher_bibliographic_correspondences': 11})
    values = combined.catalogue_stats()
    assert values['light_novel_publisher_work_groups'] == 7
    assert values['light_novel_publisher_bibliographic_records'] == 11
    assert values['light_novel_series'] == 2
    assert values['works'] == values['searchable_works'] == 2


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
