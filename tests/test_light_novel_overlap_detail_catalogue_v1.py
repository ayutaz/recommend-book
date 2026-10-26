"""Own card/detail facts remain independently typed local product references."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_overlap_details_v1 import fixture, change


IDS = ['overlap-nvl-product:opaque1', 'overlap-nvl-product:opaque2']


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_overlap_details_v1 as source
    (tmp_path / 'source').mkdir()
    env = fixture(tmp_path / 'source', count=2)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    calls, raw_calls = [], []
    verify, raw = source.verify_overlap_product_details, source._source
    def checked(path, sha):
        calls.append((str(path), sha))
        return verify(path, sha)
    def read(root, receipt, url):
        raw_calls.append(url)
        return raw(root, receipt, url)
    monkeypatch.setattr(source, 'verify_overlap_product_details', checked)
    monkeypatch.setattr(source, '_source', read)
    return SimpleNamespace(cat=cat, env=env, source=source, verify=checked, calls=calls, raw_calls=raw_calls)


def adopt(e, ids=IDS):
    return e.cat.import_overlap_detail_publisher_references(e.env['path'], e.env['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_native_detail_source_core_public_unified_preserves_roles_and_explicit_fields(bundle, tmp_path):
    e = bundle
    result = adopt(e)
    assert result['inserted'] == result['requested_references'] == result['source_references'] == 2
    assert result['held'] == 0 and len(e.calls) == 1
    assert len(e.raw_calls) == len(set(e.raw_calls)) == 3  # One shared card page, two details.
    assert len(set(result['reference_entries'].values())) == 2  # Equal ISBN does not merge listings.
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:overlapdetails:') and len(identity.rsplit(':', 1)[1]) == 32
    def forbidden(*args, **kwargs): pytest.fail('independent reference cannot look up world catalogue')
    unified = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite', work_detail=forbidden,
        set_read_status=forbidden, similar_works=forbidden), e.cat)
    e.calls.clear(); e.raw_calls.clear()
    row = unified.work_detail(identity)
    assert len(e.calls) == 1 and len(set(e.raw_calls)) == 3
    assert row['isbn'] == ['978-4-8240-1931-8']
    assert row['authors'] == row['publishers'] == row['members'] == []
    assert row['isbn_basis'] == 'explicit_source_ISBN_label_value_not_product_ID'
    product = row['publisher_product_reference']
    assert product['credits'] == [{'role': 'unknown', 'name': '人物'}, {'role': 'illustrator', 'name': '画家'}]
    assert product['credit_literals'] == [{'role_literal': None, 'name': '人物'},
        {'role_literal': 'イラスト', 'role_marker_literal': 'イラスト／', 'name': '画家'}]
    assert product['label_source_scope'] == 'own_card_label_only_detail_own_label_not_observed'
    assert product['form'] == 'B6判' and product['label'] == 'オーバーラップノベルス'
    assert row['usage_permissions'] == {'purpose': 'private_bibliographic_discovery',
        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
        'images_permission': False, 'raw_redistribution_permission': False}
    assert row['reference_scope'] == 'private_bibliographic_discovery'
    assert all(row[k] is False for k in ('actual_work_identity_asserted', 'edition_identity_asserted',
        'original_work_identity_asserted', 'story_series_identity_asserted', 'light_novel_identity_asserted',
        'light_novel_confirmed', 'recommendable'))
    assert e.cat.search_catalogue('人物')['total'] == 2
    assert e.cat.search_catalogue('画家')['total'] == 2
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'overlap_detail_product_reference'
    assert shown['publisher_product_reference']['credit_literals'] == product['credit_literals']
    assert shown['isbn'] == row['isbn']
    assert str(tmp_path) not in json.dumps(shown) and 'PRIVATE_' not in json.dumps(shown)
    assert unified.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert unified.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_and_existing_source_reimport_keep_uuid_and_tuples(bundle):
    e = bundle
    with pytest.raises(TypeError): e.cat.import_overlap_detail_publisher_references(e.env['path'], e.env['sha'])
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['overlap-product:opaque1']):
        with pytest.raises(ValueError): adopt(e, ids)
    assert e.calls == []
    first = adopt(e)
    before = snapshot(e.cat)
    e.calls.clear()
    again = adopt(e)
    assert again['unchanged'] == 2 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries'] and snapshot(e.cat) == before
    assert len(e.calls) == 1


def test_source_unknown_isbn_and_form_stay_unknown(bundle):
    e = bundle
    change(e.env, '>ISBN<', '>仕様<', nth=2)
    change(e.env, '>判　型<', '>仕様<', nth=2)
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    row = e.cat.work_detail(identity)
    assert row['isbn'] == [] and row['publisher_product_reference']['form'] is None
    assert row['authors'] == [] and not row['light_novel_confirmed']


def test_source_explicit_isbn10_literal_is_not_replaced_by_the_lookup_key(bundle):
    e = bundle
    change(e.env, '978-4-8240-1931-8', '0-306-40615-2', nth=2)
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    row = e.cat.work_detail(identity)
    assert row['isbn'] == ['0-306-40615-2'] and not row['edition_identity_asserted']
    assert public_work(row)['isbn'] == ['0-306-40615-2']


def test_later_selected_detail_raw_failure_prevents_any_write(bundle):
    e = bundle
    before = snapshot(e.cat)
    (e.env['root'] / 'raw/detail2.html').write_bytes(b'changed own detail')
    with pytest.raises(ValueError): adopt(e)
    assert len(e.calls) == 1 and snapshot(e.cat) == before


def test_changed_title_is_held_without_replacing_local_identity(bundle):
    e = bundle
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    before = snapshot(e.cat)
    next_root = e.env['root'].parent / 'next'
    next_root.mkdir()
    env = fixture(next_root, count=2)
    change(env, '完全題1　３ 副題【合本】', '改題1　３ 副題【合本】', card=True)
    change(env, '完全題1　３ 副題【合本】', '改題1　３ 副題【合本】')
    result = e.cat.import_overlap_detail_publisher_references(env['path'], env['sha'], reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert result['inserted'] == result['updated'] == 0 and snapshot(e.cat) == before
    assert e.cat.work_detail(identity)['title'] == '完全題1　３ 副題【合本】'


@pytest.mark.parametrize('change', ['id', 'url', 'isbn', 'literal', 'role', 'marker', 'label', 'publisher', 'unit', 'identity'])
def test_formal_output_contract_mismatch_is_atomic(bundle, monkeypatch, change):
    e = bundle
    def malformed(*args):
        rows = e.verify(*args)
        row = rows[-1]
        if change == 'id': row['publisher_reference_id'] = IDS[0]
        elif change == 'url': row['source_url'] = row['source_url'].replace('opaque2', 'other')
        elif change == 'isbn': row['isbn'] = '9780000000000'
        elif change == 'literal': row['isbn_source_literal'] = 'not-ISBN'
        elif change == 'role': row['credits'][0]['role'] = 'author'
        elif change == 'marker': row['credits'][1]['role_marker_literal'] = '著者／'
        elif change == 'label': row['label'] = 'inferred label'
        elif change == 'publisher': row['publisher'] = 'OVERLAP'
        elif change == 'unit': row['source_unit'] = 'edition'
        else: row['edition_identity_asserted'] = True
        return rows
    monkeypatch.setattr(e.source, 'verify_overlap_product_details', malformed)
    before = snapshot(e.cat)
    with pytest.raises(ValueError): adopt(e)
    assert snapshot(e.cat) == before


@pytest.mark.parametrize('field', ['provider', 'title', 'permission', 'marker', 'source'])
def test_rehashed_stored_proof_cannot_be_publicly_projected(bundle, field):
    e = bundle
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    with e.cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        if field == 'provider': record['provider'] = 'overlap'
        elif field == 'title': record['title'] = 'forged title'
        elif field == 'permission': record['usage_permissions']['public_display'] = True
        elif field == 'marker': record['publisher_product_reference']['credit_literals'][1]['role_marker_literal'] = '著者／'
        else: record['source_reference_assertions'][0]['reference_id'] = IDS[1]
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): e.cat.work_detail(identity)


def test_second_candidate_failure_and_independent_target_race_never_partially_write(bundle, monkeypatch):
    e = bundle
    original = e.cat._project_publisher_reference
    def fail(provider, record, cache):
        if record['publisher_product_reference']['publisher_reference_id'] == IDS[1]:
            raise ValueError('second candidate fails')
        return original(provider, record, cache)
    monkeypatch.setattr(e.cat, '_project_publisher_reference', fail)
    before = snapshot(e.cat)
    with pytest.raises(ValueError, match='second candidate'): adopt(e)
    assert snapshot(e.cat) == before
    monkeypatch.setattr(e.cat, '_project_publisher_reference', original)
    identity = adopt(e, IDS[:1])['reference_entries'][IDS[0]]
    with e.cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries').fetchone())
        db.execute('DELETE FROM entries')
    connect, calls, writes = e.cat._connect, [], []
    @contextmanager
    def interleaved():
        with connect() as db:
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(e.cat.db_path) as other: other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    monkeypatch.setattr(e.cat, '_connect', interleaved)
    with pytest.raises(ValueError, match='target set changed'): adopt(e)
    assert writes == []
    with connect() as db: assert tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone()) == racing
