"""Finite existing source frames become independent local listing references."""
from contextlib import contextmanager
import copy
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_publisher_references_v1 import gagaga_fixture, write_json, ISBN
from test_light_novel_kodansha_references_v1 import fixture as kodansha_fixture


@pytest.fixture(params=['gagaga', 'kodansha'])
def bundle(tmp_path, monkeypatch, request):
    import recommend_book.light_novel_publisher_references_v1 as source
    provider = request.param
    root = tmp_path / 'source'
    root.mkdir()
    if provider == 'gagaga':
        path, manifest, pages = gagaga_fixture(root)
        sha = write_json(path, manifest)
        raw = root / pages[0]['raw_path']
    else:
        env = kodansha_fixture(root)
        path, sha = env['path'], env['sha']
        raw = root / env['pages'][0]['source_receipt']['raw_path']
    # Only synthetic fixture evidence is replayed, never the actual source frame.
    original = source.verify_publisher_references
    rows = original(provider, path, sha)
    ids = [row['publisher_reference_id'] for row in rows[:2]]
    calls = []
    def verify(provider, path, sha):
        calls.append(provider)
        return original(provider, path, sha)
    monkeypatch.setattr(source, 'verify_publisher_references', verify)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    return SimpleNamespace(cat=cat, provider=provider, path=path, sha=sha, raw=raw,
        rows=rows, ids=ids, calls=calls, source=source, verify=verify)


def adopt(e, ids=None):
    return getattr(e.cat, 'import_' + e.provider + '_publisher_references')(
        e.path, e.sha, reference_ids=e.ids if ids is None else ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_existing_source_core_public_unified_and_literal_credit_search(bundle, tmp_path):
    e = bundle
    result = adopt(e)
    assert result['source_references'] == len(e.rows)
    assert result['requested_references'] == result['inserted'] == 2 and result['held'] == 0
    assert e.calls == [e.provider]
    identity = result['reference_entries'][e.ids[0]]
    assert identity.startswith('lnr:pub:' + e.provider + ':') and len(identity.rsplit(':', 1)[1]) == 32
    e.calls.clear()
    def forbidden(*args, **kwargs): pytest.fail('independent reference must not look up the world catalogue')
    unified = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite', work_detail=forbidden,
        set_read_status=forbidden, similar_works=forbidden), e.cat)
    row = unified.work_detail(identity)
    assert e.calls == [e.provider]
    assert row['title'] == e.rows[0]['title']
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['publisher_reference_kind'] == e.provider + '_product_reference'
    assert row['reference_scope'] == 'private_bibliographic_discovery'
    assert row['usage_permissions'] == {'purpose': 'private_bibliographic_discovery',
        'public_display': 'unknown', 'commercial_reuse': 'unknown', 'prose_ai_permission': False,
        'images_permission': False, 'raw_redistribution_permission': False}
    assert all(row[key] is False for key in ('actual_work_identity_asserted', 'edition_identity_asserted',
        'story_series_identity_asserted', 'original_work_identity_asserted', 'light_novel_confirmed',
        'light_novel_identity_asserted', 'recommendable'))
    assert row['llm_features'] == [] and row['content_features_adopted'] is False
    product = row['publisher_product_reference']
    if e.provider == 'gagaga':
        assert product['credit_literals'] == [{'role_literal': '著', 'name': '書き手'},
            {'role_literal': 'イラスト', 'name': '絵の人'}]
        assert all(c['role'] == 'unknown' for c in product['credits'])
    else:
        assert product['credit_literals'] == []
        assert product['credits'] == e.rows[0]['credits']
    name = product['credits'][0]['name']
    assert e.cat.search_catalogue(name)['total'] >= 1
    assert e.cat.search_catalogue(row['title'])['total'] >= 1
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == row['publisher_reference_kind']
    assert shown['publisher_product_reference']['title'] == row['title']
    assert str(tmp_path) not in json.dumps(shown) and 'PRIVATE_PROSE' not in json.dumps(shown)
    assert unified.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert unified.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_is_required_before_any_source_authentication(bundle):
    e = bundle
    method = getattr(e.cat, 'import_' + e.provider + '_publisher_references')
    with pytest.raises(TypeError): method(e.path, e.sha)
    for ids in (None, [], e.ids[:1] * 2, e.ids[0], [None], ['other:1']):
        with pytest.raises(ValueError): method(e.path, e.sha, reference_ids=ids)
    assert e.calls == [] and snapshot(e.cat)['entries'] == []
    absent = ('publisher-lineup:' + '0' * 24 if e.provider == 'gagaga' else 'kodansha-product:999999999999')
    with pytest.raises(ValueError, match='unknown or duplicate'): method(e.path, e.sha, reference_ids=[absent])
    assert e.calls == [e.provider] and snapshot(e.cat)['entries'] == []


def test_reimport_uuid_cache_and_old_tuples_are_stable(bundle):
    e = bundle
    first = adopt(e)
    identity = first['reference_entries'][e.ids[0]]
    e.cat.set_read_status(identity, 'reading')
    before = snapshot(e.cat)
    e.calls.clear()
    again = adopt(e)
    assert again['unchanged'] == 2 and again['inserted'] == again['updated'] == 0
    assert first['reference_entries'] == again['reference_entries']
    assert snapshot(e.cat) == before and e.calls == [e.provider]


@pytest.mark.parametrize('change', ['title', 'url'])
def test_changed_title_or_source_scope_cannot_replace_old_reference(bundle, monkeypatch, change):
    e = bundle
    adopt(e)
    before = snapshot(e.cat)
    original = getattr(e.cat, '_' + e.provider + '_publisher_references')
    def revised(path, sha, cache):
        rows = copy.deepcopy(original(e.path, sha, cache))
        # A separately pinned source may legitimately change, but continuity may not.
        if str(path).endswith('next.json'):
            for row in rows.values():
                if change == 'title': row['title'] += ' 改題'
                else:
                    row['source_url'] += 'changed'
                    row['local_reference_key']['official_product_url'] = row['source_url']
        return rows
    monkeypatch.setattr(e.cat, '_' + e.provider + '_publisher_references', revised)
    result = getattr(e.cat, 'import_' + e.provider + '_publisher_references')(
        e.path.parent / 'next.json', e.sha, reference_ids=e.ids)
    assert result['held'] == 2 and result['held_reasons'] == {'source_reference_scope_or_title_changed': 2}
    assert result['inserted'] == result['updated'] == 0 and snapshot(e.cat) == before


@pytest.mark.parametrize('change', ['isbn', 'unit', 'story', 'role', 'label', 'conflict'])
def test_invalid_selected_contract_is_atomic(bundle, monkeypatch, change):
    e = bundle
    def invalid(*args):
        rows = e.verify(*args)
        row = rows[1]
        if change == 'isbn': row['isbn'] = 'not-an-ISBN'
        elif change == 'unit': row['source_unit'] = 'story_series'
        elif change == 'story': row['story_series_asserted'] = True
        elif change == 'role': row['credits'][0]['role'] = 'inferred-author'
        elif change == 'label': row['label'] = 'guessed label'
        else: row['metadata_conflict'] = True
        return rows
    monkeypatch.setattr(e.source, 'verify_publisher_references', invalid)
    before = snapshot(e.cat)
    with pytest.raises(ValueError): adopt(e)
    assert snapshot(e.cat) == before


def test_nonselected_conflict_is_not_admitted_or_used_to_block_selected_refs(bundle, monkeypatch):
    e = bundle
    def conflict(*args):
        rows = e.verify(*args)
        rows[-1]['metadata_conflict'] = True
        rows[-1]['bibliographic_variants'] = [{'title': 'conflicting title'}]
        return rows
    monkeypatch.setattr(e.source, 'verify_publisher_references', conflict)
    assert adopt(e, e.ids[:1])['inserted'] == 1
    before = snapshot(e.cat)
    with pytest.raises(ValueError): adopt(e, [e.rows[-1]['publisher_reference_id']])
    assert snapshot(e.cat) == before


def test_explicit_source_isbn_is_preserved_without_edition_identity(bundle):
    e = bundle
    if e.provider != 'gagaga':
        assert all(r['isbn'] is None for r in e.rows)
        return
    release = next(r for r in e.rows if r['isbn'])
    identity = adopt(e, [release['publisher_reference_id']])['reference_entries'][release['publisher_reference_id']]
    row = e.cat.work_detail(identity)
    assert row['isbn'] == [ISBN] and row['edition_identity_asserted'] is False
    assert public_work(row)['isbn'] == [ISBN]


@pytest.mark.parametrize('change', ['digest', 'typed_provider', 'assertion_id', 'permission', 'title'])
def test_stored_identity_proof_permissions_and_digest_must_replay(bundle, change):
    e = bundle
    identity = adopt(e)['reference_entries'][e.ids[0]]
    with e.cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        if change == 'typed_provider': record['provider'] = 'tobooks'
        elif change == 'assertion_id': record['source_reference_assertions'][-1]['reference_id'] = e.ids[1]
        elif change == 'permission': record['usage_permissions']['public_display'] = True
        elif change == 'title': record['title'] = 'forged title'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = '0' * 64 if change == 'digest' else hashlib.sha256(encoded.encode()).hexdigest()
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?', (encoded, digest, identity))
    with pytest.raises(ValueError): e.cat.work_detail(identity)


def test_tampered_saved_fields_or_raw_are_rejected_at_projection(bundle):
    e = bundle
    identity = adopt(e)['reference_entries'][e.ids[0]]
    with e.cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['publisher_product_reference']['credits'][0]['name'] = 'forged name'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): e.cat.work_detail(identity)
    e.raw.write_bytes(b'changed raw')
    with pytest.raises(ValueError): e.cat.similar_works(identity)


def test_unrelated_provider_not_replayed_and_independent_connection_race_is_atomic(bundle, monkeypatch):
    e = bundle
    encoded = '{"raw_path":"absent-unrelated-provider"}'
    old = ('lnr:pub:tobooks:' + 'a' * 32, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with e.cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
    first = adopt(e, e.ids[:1])
    identity = first['reference_entries'][e.ids[0]]
    with e.cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        db.execute('DELETE FROM entries WHERE entry_id=?', (identity,))
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
    with connect() as db: assert sorted(tuple(r) for r in db.execute('SELECT * FROM entries')) == sorted([old, racing])
