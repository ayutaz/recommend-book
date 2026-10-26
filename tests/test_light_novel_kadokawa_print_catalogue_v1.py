"""Print listing references stay separate from editions and electronic listings."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_kadokawa_print_references_v1 import source as print_source, IDS


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    import recommend_book.light_novel_publisher_books_v1 as legacy
    e = print_source.__wrapped__(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    calls = {'verify': [], 'index': [], 'pages': []}
    original, scope, checked = source.verify_kadokawa_print_references, source._scope, legacy._checked_bytes
    def verify(*args, **kwargs):
        calls['verify'].append(tuple(kwargs['reference_ids']))
        return original(*args, **kwargs)
    def indexed(*args):
        calls['index'].append(True)
        return scope(*args)
    def page(path, sha):
        calls['pages'].append(path.name)
        return checked(path, sha)
    monkeypatch.setattr(source, 'verify_kadokawa_print_references', verify)
    monkeypatch.setattr(source, '_scope', indexed)
    monkeypatch.setattr(legacy, '_checked_bytes', page)
    return cat, e, calls


def import_refs(bundle, ids=IDS):
    cat, e, _ = bundle
    return cat.import_kadokawa_print_publisher_references(e['root'] / 'print-scope.json', e['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')] for table in tables}


def rewrite(cat, identity, change):
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        change(record)
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))


def test_print_real_source_core_public_and_unified_replay_preserve_unknowns(bundle, tmp_path):
    cat, e, calls = bundle
    (e['root'] / 'page2.json').write_bytes(b'unselected source must stay unread')
    result = import_refs(bundle, IDS[:2])
    assert result['source_references'] == result['requested_references'] == result['inserted'] == 2
    assert result['held'] == 0 and calls == {'verify': [tuple(IDS[:2])], 'index': [True], 'pages': ['page1.json']}
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:kadokawaprint:') and len(identity.rsplit(':', 1)[1]) == 32
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert record['provider'] == 'kadokawa-print' and record['isbn'] == ['978-4-04-429101-3']
    assert record['schema_version'] == 'light-novel-kadokawa-print-publisher-reference-v1'
    assert record['source_reference_assertions'][0]['local_reference_key'] == {
        'provider': 'kadokawa', 'source_scope': 'form3_valid_isbn_product_reference',
        'publisher_reference_id': IDS[0], 'official_product_url': record['publisher_product_reference']['source_url'],
        'product_form_id': 3, 'label': record['publication_label']}
    for v in calls.values(): v.clear()
    def world_forbidden(*args, **kwargs):
        pytest.fail('publisher references must not query the world catalogue')
    combined = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite',
        work_detail=world_forbidden, set_read_status=world_forbidden, similar_works=world_forbidden), cat)
    row = combined.work_detail(identity)
    assert calls == {'verify': [(IDS[0],)], 'index': [True], 'pages': ['page1.json']}
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'kadokawa_print_product_reference'
    assert shown['publisher_product_reference']['form'] == '文庫'
    assert shown['isbn'] == ['978-4-04-429101-3'] and shown['authors'] == shown['members'] == []
    assert shown['usage_permissions']['public_display'] == shown['usage_permissions']['commercial_reuse'] == 'unknown'
    assert all(shown['usage_permissions'][k] is False for k in
        ('prose_ai_permission', 'images_permission', 'raw_redistribution_permission'))
    assert all(row[k] is False for k in ('light_novel_confirmed', 'edition_identity_asserted',
        'story_series_identity_asserted', 'original_work_identity_asserted', 'paper_electronic_same_edition_asserted'))
    assert row['publishers'] == [] and row['llm_features'] == []
    assert not any(s in json.dumps(shown, ensure_ascii=False) for s in ('PRIVATE_', 'raw_sha256', str(tmp_path)))
    assert cat.search_catalogue('表紙 画家')['total'] == 2  # A literal credit is not a global author identity.
    assert cat.search_catalogue('役割未記載')['total'] == 2
    assert any(credit['role'] == 'unknown' for credit in row['publisher_product_reference']['credits'])
    assert combined.set_read_status(identity, 'reading')['entry_id'] == identity
    assert combined.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_selection_is_required_and_invalid_inputs_never_call_source(bundle):
    cat, e, calls = bundle
    with pytest.raises(TypeError):
        cat.import_kadokawa_print_publisher_references(e['root'] / 'print-scope.json', e['sha'])
    for ids in (None, [], 'wrong', ['wrong'], IDS[:1] * 2, IDS * 67):
        with pytest.raises(ValueError): import_refs(bundle, ids)
    assert calls['verify'] == []


def test_reimport_retains_uuid_and_does_not_read_other_saved_product_pages(bundle):
    cat, e, calls = bundle
    first = import_refs(bundle)
    before = snapshot(cat)
    (e['root'] / 'page2.json').write_bytes(b'unrelated old print raw must stay unread')
    for v in calls.values(): v.clear()
    again = import_refs(bundle, IDS[:2])
    assert again['unchanged'] == 2 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == {i: first['reference_entries'][i] for i in IDS[:2]}
    assert calls == {'verify': [tuple(IDS[:2])], 'index': [True], 'pages': ['page1.json']}
    assert snapshot(cat) == before


def test_source_isbn10_literal_survives_core_and_public_without_identity_assertion(bundle):
    from test_light_novel_kadokawa_print_references_v1 import save
    cat, e, _ = bundle
    e['proof']['scope_predicates']['isbn'] = 'explicit non-null literal with valid ISBN-10/13 check digit; identity unasserted'
    path = e['root'] / 'page1.json'
    payload = json.loads(path.read_bytes())
    payload['result']['book'][0]['isbn'] = '4-04-429101-2'
    raw = json.dumps(payload, ensure_ascii=False).encode()
    path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    for manifest in (e['source_manifest'], e['terminal']):
        manifest['pages'][0].update(sha256=sha, byte_length=len(raw))
    for member in e['proof']['members'] + e['handoff']['members']:
        if member['evidence']['request_data']['pageno_book'] == '1':
            member['evidence']['source_sha256'] = sha
        if member['publisher_reference_id'] == IDS[0]:
            member.update(isbn='4-04-429101-2', isbn_literal='4-04-429101-2')
    e['input']['lookup_targets'][0]['isbn_literal'] = '4-04-429101-2'
    save(e)
    identity = import_refs(bundle, IDS[:1])['reference_entries'][IDS[0]]
    row = public_work(cat.work_detail(identity))
    assert row['isbn'] == ['4-04-429101-2'] and row['edition_identity_asserted'] is False


@pytest.mark.parametrize('change', ['title', 'label'])
def test_changed_title_or_scope_is_held_without_replacing_old_assertions(bundle, monkeypatch, change):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    cat, e, _ = bundle
    import_refs(bundle, IDS[:1])
    before = snapshot(cat)
    original = source.verify_kadokawa_print_references
    next_path = e['root'] / 'new-snapshot.json'
    next_path.write_text('{}', encoding='utf-8')
    def changed(path, sha, **kwargs):
        rows = original(e['root'] / 'print-scope.json', e['sha'], **kwargs)
        if str(path) == str(next_path):
            rows[0][change] = '新しい完全題' if change == 'title' else 'MF文庫J'
            if change == 'label':
                rows[0]['official_label_id'] = 55
                rows[0]['local_reference_key']['label'] = 'MF文庫J'
        return rows
    monkeypatch.setattr(source, 'verify_kadokawa_print_references', changed)
    result = cat.import_kadokawa_print_publisher_references(next_path, 'a' * 64, reference_ids=IDS[:1])
    assert result['held'] == 1 and result['inserted'] == result['updated'] == 0
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert snapshot(cat) == before


@pytest.mark.parametrize('change', ['form', 'isbn', 'lookup', 'permissions', 'permission_type', 'identity', 'key', 'kind'])
def test_consumer_rejects_incompatible_safe_rows_atomically(bundle, monkeypatch, change):
    import recommend_book.light_novel_kadokawa_print_references_v1 as source
    cat, _, _ = bundle
    original = source.verify_kadokawa_print_references
    def invalid(*args, **kwargs):
        rows = original(*args, **kwargs)
        row = rows[-1]
        if change == 'form': row['product_form_id'] = 13
        elif change == 'isbn': row['isbn'] = row['isbn_literal'] = '9784044291014'
        elif change == 'lookup': row['normalized_isbn13_lookup_key'] = '9784044291014'
        elif change == 'permissions': row['usage_permissions']['public_display'] = True
        elif change == 'permission_type': row['usage_permissions']['prose_ai_permission'] = 0
        elif change == 'identity': row['edition_identity_asserted'] = True
        elif change == 'kind': row['kind'] = 'edition'
        else: row['local_reference_key']['provider'] = 'kadokawa-print'
        return rows
    monkeypatch.setattr(source, 'verify_kadokawa_print_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): import_refs(bundle)
    assert snapshot(cat) == before


def test_source_failure_on_later_selected_page_has_no_partial_writes(bundle):
    cat, e, _ = bundle
    (e['root'] / 'page2.json').write_bytes(b'changed selected source')
    before = snapshot(cat)
    with pytest.raises(ValueError): import_refs(bundle)
    assert snapshot(cat) == before


@pytest.mark.parametrize('change', ['kind', 'schema', 'credit', 'ancestor_title'])
def test_public_replay_rejects_rehashed_typed_or_assertion_mutations(bundle, change):
    cat, _, _ = bundle
    identity = import_refs(bundle, IDS[:1])['reference_entries'][IDS[0]]
    if change == 'kind':
        with cat._connect() as db: db.execute("UPDATE entries SET kind='edition' WHERE entry_id=?", (identity,))
    else:
        def mutate(record):
            if change == 'schema': record['schema_version'] = 'light-novel-kadokawa-publisher-reference-v1'
            elif change == 'credit': record['publisher_product_reference']['credit_literals'][0]['name'] = '偽造名'
            else: record['source_reference_assertions'][0]['title'] = '別の題'
        rewrite(cat, identity, mutate)
    with pytest.raises(ValueError): cat.work_detail(identity)


def test_current_raw_is_reauthenticated_at_detail_and_similarity(bundle):
    cat, e, _ = bundle
    identity = import_refs(bundle, IDS[:1])['reference_entries'][IDS[0]]
    (e['root'] / 'page1.json').write_bytes(b'changed own source')
    with pytest.raises(ValueError): cat.work_detail(identity)
    with pytest.raises(ValueError): cat.similar_works(identity)


@pytest.mark.parametrize('change', ['kind', 'digest'])
def test_unrequested_stored_print_metadata_is_checked_without_loading_its_raw(bundle, change):
    cat, e, _ = bundle
    identity = import_refs(bundle)['reference_entries'][IDS[2]]
    (e['root'] / 'page2.json').write_bytes(b'not selected for raw authentication')
    with cat._connect() as db:
        if change == 'kind': db.execute("UPDATE entries SET kind='edition' WHERE entry_id=?", (identity,))
        else: db.execute('UPDATE entries SET record_sha256=? WHERE entry_id=?', ('0' * 64, identity))
    before = snapshot(cat)
    with pytest.raises(ValueError): import_refs(bundle, IDS[:1])
    assert snapshot(cat) == before


def test_paper_import_does_not_read_or_rewrite_old_electronic_entry(bundle):
    cat, e, _ = bundle
    identity = 'lnr:pub:kadokawa:' + 'e' * 32
    # An old typed electronic row need not be reprojected by this print-only import.
    encoded = json.dumps({'schema_version': 'light-novel-kadokawa-publisher-reference-v1',
        'provider': 'kadokawa', 'source': 'publisher', 'kind': 'publisher_reference', 'id': 'e' * 32,
        'title': '既存の電子掲載参照', 'provenance': {'raw_path': 'absent-old-electronic.json'}})
    old = (identity, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
        db.execute('INSERT INTO read_status VALUES(?,?,?)', (identity, 'read', 'old-timestamp'))
    before = snapshot(cat)
    assert import_refs(bundle, IDS[:1])['inserted'] == 1
    after = snapshot(cat)
    assert old in after['entries'] and len(after['entries']) == len(before['entries']) + 1
    assert {k:v for k,v in before.items() if k != 'entries'} == {k:v for k,v in after.items() if k != 'entries'}


def test_concurrent_new_print_target_is_detected_before_any_importer_write(bundle, monkeypatch):
    cat, e, _ = bundle
    identity = import_refs(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
        db.execute('DELETE FROM entries WHERE entry_id=?', (identity,))
    original, calls, writes = cat._connect, [], []
    @contextmanager
    def interleave():
        with original() as db:
            calls.append(True)
            if len(calls) == 2:
                with sqlite3.connect(cat.db_path) as other: other.execute('INSERT INTO entries VALUES(?,?,?,?)', racing)
                db.set_trace_callback(lambda sql: writes.append(sql) if sql.startswith(('INSERT', 'UPDATE', 'DELETE')) else None)
            yield db
    monkeypatch.setattr(cat, '_connect', interleave)
    with pytest.raises(ValueError, match='target set changed'): import_refs(bundle, IDS[:2])
    assert writes == []
    with original() as db: assert [tuple(r) for r in db.execute('SELECT * FROM entries')] == [racing]
