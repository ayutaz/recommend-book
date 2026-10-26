"""A form-1 listing never changes the form-3 or electronic reference contract."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_kadokawa_print_references_v1 import source as print_source, trade_source, IDS


@pytest.fixture
def trade(tmp_path, monkeypatch):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    import recommend_book.light_novel_publisher_books_v1 as legacy
    e = trade_source.__wrapped__(print_source.__wrapped__(tmp_path))
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    calls = {'verify': [], 'index': [], 'pages': []}
    verifier, index, page = source.verify_kadokawa_trade_references, source._scope, legacy._checked_bytes
    def verified(*args, **kwargs):
        calls['verify'].append(tuple(kwargs['reference_ids']))
        return verifier(*args, **kwargs)
    def indexed(*args, **kwargs):
        calls['index'].append(True)
        return index(*args, **kwargs)
    def checked(path, sha):
        calls['pages'].append(path.name)
        return page(path, sha)
    monkeypatch.setattr(source, 'verify_kadokawa_trade_references', verified)
    monkeypatch.setattr(source, '_scope', indexed)
    monkeypatch.setattr(legacy, '_checked_bytes', checked)
    return cat, e, calls


def adopt(trade, ids=IDS):
    cat, e, _ = trade
    return cat.import_kadokawa_trade_publisher_references(e['root'] / 'print-scope.json', e['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')] for table in tables}


def test_trade_import_requires_explicit_selection_before_any_source_access(tmp_path):
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    with pytest.raises(TypeError):
        cat.import_kadokawa_trade_publisher_references('absent.json', 'a' * 64)
    for ids in (None, [], 'kadokawa-product:123456789012', ['wrong']):
        with pytest.raises(ValueError):
            cat.import_kadokawa_trade_publisher_references('absent.json', 'a' * 64, reference_ids=ids)


def test_real_trade_source_core_public_unified_and_literal_credit_search(trade, tmp_path):
    cat, e, calls = trade
    (e['root'] / 'page2.json').write_bytes(b'not selected')
    result = adopt(trade, IDS[:2])
    assert result['inserted'] == result['requested_references'] == 2 and result['held'] == 0
    assert calls == {'verify': [tuple(IDS[:2])], 'index': [True], 'pages': ['page1.json']}
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:kadokawatrade:') and len(identity.rsplit(':', 1)[1]) == 32
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert record['schema_version'] == 'light-novel-kadokawa-trade-publisher-reference-v1'
    assert record['provider'] == 'kadokawa-trade' and record['isbn'] == ['978-4-04-429101-3']
    key = record['source_reference_assertions'][0]['local_reference_key']
    assert key['provider'] == 'kadokawa' and key['source_scope'] == 'form1_valid_isbn_product_reference'
    assert key['product_form_id'] == 1
    for values in calls.values(): values.clear()
    def forbidden(*args, **kwargs): pytest.fail('no world lookup for trade reference')
    combined = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite', work_detail=forbidden,
        set_read_status=forbidden, similar_works=forbidden), cat)
    row = combined.work_detail(identity)
    assert calls == {'verify': [(IDS[0],)], 'index': [True], 'pages': ['page1.json']}
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'kadokawa_trade_product_reference'
    assert shown['publisher_product_reference']['form'] == '単行本' and shown['isbn'] == record['isbn']
    assert row['authors'] == row['publishers'] == row['members'] == row['llm_features'] == []
    assert all(row[key] is False for key in ('light_novel_confirmed', 'edition_identity_asserted',
        'story_series_identity_asserted', 'paper_electronic_same_edition_asserted'))
    assert shown['usage_permissions']['public_display'] == shown['usage_permissions']['commercial_reuse'] == 'unknown'
    assert 'PRIVATE_' not in json.dumps(shown) and str(tmp_path) not in json.dumps(shown)
    assert cat.search_catalogue('役割未記載')['total'] == 2
    assert combined.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert combined.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_trade_reimport_preserves_uuid_and_never_reads_unselected_saved_pages(trade):
    cat, e, calls = trade
    first = adopt(trade)
    before = snapshot(cat)
    for values in calls.values(): values.clear()
    (e['root'] / 'page2.json').write_bytes(b'old unselected source not replayed')
    result = adopt(trade, IDS[:1])
    assert result['unchanged'] == 1 and result['inserted'] == result['updated'] == 0
    assert result['reference_entries'][IDS[0]] == first['reference_entries'][IDS[0]]
    assert calls == {'verify': [tuple(IDS[:1])], 'index': [True], 'pages': ['page1.json']}
    assert snapshot(cat) == before


@pytest.mark.parametrize('field, value', [('product_form_id', 3), ('product_form_id', 13),
    ('product_form_id', True), ('form', 'B6判'), ('form', 'hardcover'),
    ('isbn_literal', '9784044291014'), ('source_scope', 'form3_valid_isbn_product_reference')])
def test_trade_form_and_explicit_literal_boundaries_are_atomic(trade, monkeypatch, field, value):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    cat, _, _ = trade
    original = source.verify_kadokawa_trade_references
    def invalid(*args, **kwargs):
        rows = original(*args, **kwargs)
        rows[-1][field] = value
        return rows
    monkeypatch.setattr(source, 'verify_kadokawa_trade_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(trade)
    assert snapshot(cat) == before


def test_trade_title_revision_holds_and_other_provider_tuples_are_unchanged(trade, monkeypatch):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    cat, e, _ = trade
    identity = adopt(trade, IDS[:1])['reference_entries'][IDS[0]]
    old = ('lnr:pub:kadokawaprint:' + 'e' * 32, 'publisher_reference',
           '{"raw_path":"absent-old-print"}', hashlib.sha256(b'{"raw_path":"absent-old-print"}').hexdigest())
    with cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
    before = snapshot(cat)
    path = e['root'] / 'revision.json'
    path.write_text('{}', encoding='utf-8')
    original = source.verify_kadokawa_trade_references
    def revised(request_path, sha, **kwargs):
        rows = original(e['root'] / 'print-scope.json', e['sha'], **kwargs)
        if str(request_path) == str(path): rows[0]['title'] = '改訂された別題'
        return rows
    monkeypatch.setattr(source, 'verify_kadokawa_trade_references', revised)
    result = cat.import_kadokawa_trade_publisher_references(path, 'b' * 64, reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert snapshot(cat) == before and old in before['entries']


def test_trade_proof_mutation_and_changed_source_fail_reauthentication(trade):
    cat, e, _ = trade
    identity = adopt(trade, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries').fetchone()[0])
        record['source_reference_assertions'][0]['local_reference_key']['product_form_id'] = 3
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError): cat.work_detail(identity)
    (e['root'] / 'page1.json').write_bytes(b'changed own source')
    with pytest.raises(ValueError): cat.similar_works(identity)


def test_trade_later_raw_failure_has_no_partial_writes(trade):
    cat, e, _ = trade
    (e['root'] / 'page2.json').write_bytes(b'changed selected page')
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(trade)
    assert snapshot(cat) == before


def test_trade_race_guard_compares_target_set_before_any_write(trade, monkeypatch):
    cat, _, _ = trade
    adopt(trade, IDS[:1])
    with cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries').fetchone())
        db.execute('DELETE FROM entries')
    connect, calls, writes = cat._connect, [], []
    @contextmanager
    def interleave():
        with connect() as db:
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(cat.db_path) as other: other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    monkeypatch.setattr(cat, '_connect', interleave)
    with pytest.raises(ValueError, match='target set changed'): adopt(trade)
    assert writes == []
    with connect() as db: assert [tuple(row) for row in db.execute('SELECT * FROM entries')] == [racing]
