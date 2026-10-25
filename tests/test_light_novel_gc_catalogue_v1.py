"""Acquired GC references remain partial, individually unbranded local listings."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_gc_references_v1 import fixture, change_raw, save


IDS = ['gc-product:1', 'gc-product:2']
SCOPE = {'index_pages_complete': False, 'pages_verified': 5, 'publisher_reported_total': 627,
         'references_verified': 600, 'unacquired_references': 27, 'unacquired_http_status': 500}


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_gc_references_v1 as source
    env = fixture(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    verify, raw = source.verify_gc_references, source._source
    calls, pages = [], []
    def checked(path, sha):
        calls.append(str(path))
        return verify(path, sha)
    def read(root, receipt, url):
        pages.append(url)
        return raw(root, receipt, url)
    monkeypatch.setattr(source, 'verify_gc_references', checked)
    monkeypatch.setattr(source, '_source', read)
    return cat, env, source, calls, pages


def adopt(e, ids=IDS):
    cat, env, *_ = e
    return cat.import_gc_publisher_references(env['path'], env['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_partial_native_frame_core_detail_and_public_preserve_unknowns_and_source_scope(bundle):
    cat, _, _, calls, pages = bundle
    result = adopt(bundle)
    assert result['source_references'] == 600 and result['requested_references'] == result['inserted'] == 2
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 5
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:gc:') and len(identity.rsplit(':', 1)[1]) == 32
    calls.clear(); pages.clear()
    row = cat.work_detail(identity)
    assert len(calls) == 1 and len(set(pages)) == 5
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['publication_label'] is None and row['publisher_product_reference']['form'] is None
    assert row['source_frame_scope'] == SCOPE
    assert row['publisher_product_reference']['credits'] == [
        {'role': 'author', 'name': '架空著者'}, {'role': 'illustrator', 'name': '架空絵師'}]
    assert row['publisher_product_reference']['credit_literals'] == [
        {'role_literal': '小説', 'name': '架空著者'}, {'role_literal': 'イラスト', 'name': '架空絵師'}]
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'gc_product_reference' and shown['isbn'] == []
    assert 'source_frame_scope' not in shown and 'PRIVATE_' not in json.dumps(shown)
    assert cat.search_catalogue('架空絵師')['total'] == 2
    assert cat.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert cat.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_reimport_keeps_uuid_and_all_old_tuples(bundle):
    cat, env, _, calls, _ = bundle
    with pytest.raises(TypeError): cat.import_gc_publisher_references(env['path'], env['sha'])
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['gc-product:X']):
        with pytest.raises(ValueError): adopt(bundle, ids)
    assert calls == []
    first = adopt(bundle)
    before = snapshot(cat); calls.clear()
    result = adopt(bundle)
    assert result['unchanged'] == 2 and result['inserted'] == result['updated'] == 0
    assert result['reference_entries'] == first['reference_entries'] and snapshot(cat) == before and len(calls) == 1


def test_unknown_literal_role_is_not_an_author_and_remains_name_searchable(bundle):
    cat, env, *_ = bundle
    change_raw(env, '小説<a', '未知役割<a')
    env['roster'][0]['credits'][0].update(role='unknown', role_literal='未知役割'); save(env)
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    row = cat.work_detail(identity)
    assert row['authors'] == [] and row['publisher_product_reference']['credits'][0]['role'] == 'unknown'
    assert row['publisher_product_reference']['credit_literals'][0]['role_literal'] == '未知役割'
    assert cat.search_catalogue('架空著者')['total'] == 1


def test_native_correspondence_facts_and_listing_projection_share_one_partial_replay(bundle):
    cat, env, _, calls, pages = bundle
    cache = {}
    native = cat._gc_native_references(env['path'], env['sha'], cache)
    references = cat._gc_publisher_references(env['path'], env['sha'], cache)
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 5
    assert native[0]['credits'] == references[IDS[0]]['credits']
    assert native[0]['source_frame_scope'] == references[IDS[0]]['source_frame_scope'] == SCOPE
    assert 'reference_scope' not in native[0] and 'credit_literals' not in native[0]
    assert native[0]['isbn'] is None and native[0]['label'] is None


def test_new_snapshot_retitled_listing_is_held_without_replacing_old_uuid(bundle):
    cat, env, *_ = bundle
    first = adopt(bundle, IDS[:1])
    before = snapshot(cat)
    root = env['root'] / 'next'; root.mkdir()
    revised = fixture(root)
    change_raw(revised, '完全題1　巻３【特装版】', '改題1　巻３【特装版】')
    revised['roster'][0]['title'] = '改題1　巻３【特装版】'; save(revised)
    result = cat.import_gc_publisher_references(revised['path'], revised['sha'], reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert result['inserted'] == result['updated'] == 0 and snapshot(cat) == before
    assert cat.work_detail(first['reference_entries'][IDS[0]])['title'] == '完全題1　巻３【特装版】'


@pytest.mark.parametrize('field', ['isbn', 'publisher', 'label', 'form', 'url', 'complete', 'missing', 'role'])
def test_formal_partial_scope_or_unknown_field_changes_are_atomic(bundle, monkeypatch, field):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, *_ = bundle
    verify = source.verify_publisher_references
    def invalid(*args):
        rows = verify(*args); row = rows[1]
        if field == 'isbn': row['isbn'] = '9784086310017'
        elif field == 'publisher': row['publisher'] = 'マイクロマガジン社'
        elif field == 'label': row['label'] = 'GCノベルズ'; row['label_literals'] = ['GCノベルズ']
        elif field == 'form': row['form'] = '文庫'
        elif field == 'url': row['source_url'] = 'https://gcnovels.jp/book/99999'
        elif field == 'complete': row['source_frame_scope']['index_pages_complete'] = True
        elif field == 'missing': row['source_frame_scope']['unacquired_references'] = 0
        else: row['credits'][0]['role_literal'] = '未知役割'
        return rows
    monkeypatch.setattr(source, 'verify_publisher_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before


def test_rehashed_partial_claim_or_credit_tampering_cannot_be_projected(bundle):
    cat, *_ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['source_frame_scope']['unacquired_references'] = 0
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): cat.work_detail(identity)


def test_source_later_page_failure_writes_nothing(bundle):
    cat, env, _, calls, _ = bundle
    before = snapshot(cat)
    (env['root'] / '5.bin').write_bytes(b'changed selected source page')
    with pytest.raises(ValueError): adopt(bundle)
    assert len(calls) == 1 and snapshot(cat) == before


def test_other_provider_preserved_and_target_namespace_race_is_atomic(bundle, monkeypatch):
    cat, *_ = bundle
    encoded = '{"absent_unrelated_raw":true}'
    old = ('lnr:pub:shueishadx:' + 'a' * 32, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        db.execute('DELETE FROM entries WHERE entry_id=?', (identity,))
    connect, calls, writes = cat._connect, [], []
    @contextmanager
    def interleaved():
        with connect() as db:
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(cat.db_path) as other: other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    monkeypatch.setattr(cat, '_connect', interleaved)
    with pytest.raises(ValueError, match='target set changed'): adopt(bundle)
    assert writes == []
    with connect() as db: assert sorted(tuple(r) for r in db.execute('SELECT * FROM entries')) == sorted([old, racing])
