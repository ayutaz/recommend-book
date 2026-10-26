"""TO listing references preserve unknown roles and normalized source ISBNs."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_publisher_references_v1 import to_fixture, to_card, receipt, write_json, ISBN


IDS = ['tobooks-product:1', 'tobooks-product:2', 'tobooks-product:3']


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_publisher_references_v1 as source
    def capture(name, *, title=None):
        root = tmp_path / name
        root.mkdir()
        path, manifest, old, rows = to_fixture(root)
        text = (root / old['raw_path']).read_text(encoding='utf-8')
        third = to_card('3', 'ISBNなし掲載参照', None).replace('<p>Celicaノベルス</p>', '').replace('<p><b>判型：</b>B6判</p>', '')
        text = text.replace('</ul>', third + '</ul>', 1).replace('検索結果 2 件', '検索結果 3 件')
        if title: text = text.replace('作品２', title)
        raw = receipt(root, old['source_url'], text)
        for row in rows: row['index_source_snapshot'] = raw
        if title:
            for row in rows: row['title'] = row['title'].replace('作品２', title)
        rows.append({**rows[0], 'publisher_product_reference_id': IDS[2], 'official_product_id': '3',
            'source_url': 'https://www.tobooks.jp/contents/3', 'title': 'ISBNなし掲載参照',
            'isbn': None, 'label_display': [], 'format_display': None})
        manifest['roster_sha256'] = write_json(root / 'roster.json', rows)
        page = {'page': 1, 'source_snapshot': raw, 'product_cards': 3,
                'next_literal_hrefs': [], 'linked_max_page': 1, 'current_page': [1]}
        pages = root / 'pages.jsonl'
        pages.write_text(json.dumps(page) + '\n', encoding='utf-8')
        manifest.update(publisher_reported_total_items=3, observed_card_occurrences=3,
            unique_product_references=3, pages_checkpoint_sha256=hashlib.sha256(pages.read_bytes()).hexdigest())
        sha = write_json(path, manifest)
        return path, sha, root / raw['raw_path']
    path, sha, raw = capture('initial')
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    calls = {'verify': [], 'pages': []}
    original, page = source.verify_publisher_references, source._source
    def verified(provider, path, sha):
        calls['verify'].append(provider)
        return original(provider, path, sha)
    def checked(root, receipt, provider):
        calls['pages'].append(receipt['source_url'])
        return page(root, receipt, provider)
    monkeypatch.setattr(source, 'verify_publisher_references', verified)
    monkeypatch.setattr(source, '_source', checked)
    return cat, path, sha, raw, capture, calls


def adopt(bundle, ids=IDS):
    cat, path, sha, *_ = bundle
    return cat.import_tobooks_publisher_references(path, sha, reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_real_to_source_core_public_and_unified_are_listing_references_only(bundle, tmp_path):
    cat, _, _, _, _, calls = bundle
    result = adopt(bundle)
    assert result['inserted'] == result['requested_references'] == 3 and result['held'] == 0
    assert calls['verify'] == ['tobooks'] and len(calls['pages']) == 1
    identities = result['reference_entries']
    assert len(set(identities.values())) == 3  # Shared ISBN / bonus products stay separate.
    identity = identities[IDS[0]]
    assert identity.startswith('lnr:pub:tobooks:') and len(identity.rsplit(':', 1)[1]) == 32
    for value in calls.values(): value.clear()
    def forbidden(*args, **kwargs): pytest.fail('no world catalogue lookup for a TO reference')
    combined = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite', work_detail=forbidden,
        set_read_status=forbidden, similar_works=forbidden), cat)
    row = combined.work_detail(identity)
    assert calls['verify'] == ['tobooks'] and len(calls['pages']) == 1
    assert row['authors'] == row['publishers'] == row['members'] == []
    assert row['publisher_reference_kind'] == 'tobooks_product_reference'
    assert row['isbn'] == [ISBN] and row['isbn_basis'] == 'source_normalized_isbn13_not_raw_literal'
    assert row['publisher_product_reference']['credits'] == [
        {'role': 'unknown', 'name': '名前一'}, {'role': 'unknown', 'name': '名前二'}]
    assert row['publisher_product_reference']['credit_literals'] == [
        {'role_literal': None, 'name': '名前一'}, {'role_literal': None, 'name': '名前二'}]
    assert row['usage_permissions']['public_display'] == row['usage_permissions']['commercial_reuse'] == 'unknown'
    assert not row['light_novel_confirmed'] and not row['edition_identity_asserted'] and not row['story_series_identity_asserted']
    assert not row['recommendable'] and row['llm_features'] == []
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'tobooks_product_reference' and shown['isbn'] == row['isbn']
    assert 'PRIVATE_PROSE' not in json.dumps(shown) and str(tmp_path) not in json.dumps(shown)
    assert cat.search_catalogue('名前一')['total'] == 3
    unknown = cat.work_detail(identities[IDS[2]])
    assert unknown['isbn'] == [] and unknown['publication_label'] is None
    assert unknown['publisher_product_reference']['form'] is None and unknown['publisher_product_reference']['label'] is None
    assert combined.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert combined.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_validation_precedes_source_replay(bundle):
    cat, path, sha, _, _, calls = bundle
    with pytest.raises(TypeError): cat.import_tobooks_publisher_references(path, sha)
    for ids in (None, [], IDS[:1] * 2, 'tobooks-product:1', ['other:1'], [None]):
        with pytest.raises(ValueError): adopt(bundle, ids)
    assert calls['verify'] == []
    with pytest.raises(ValueError): adopt(bundle, ['tobooks-product:999'])
    assert calls['verify'] == ['tobooks']


def test_reimport_retains_uuid_and_all_old_tuples_with_one_frame_verification(bundle):
    cat, _, _, _, _, calls = bundle
    first = adopt(bundle)
    before = snapshot(cat)
    for value in calls.values(): value.clear()
    again = adopt(bundle)
    assert again['unchanged'] == 3 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries']
    assert calls['verify'] == ['tobooks'] and len(calls['pages']) == 1
    assert snapshot(cat) == before


def test_new_snapshot_preserves_uuid_and_read_state_but_changed_title_is_held(bundle):
    cat, _, _, _, capture, _ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    cat.set_read_status(identity, 'reading')
    path, sha, _ = capture('next')
    result = cat.import_tobooks_publisher_references(path, sha, reference_ids=IDS[:1])
    assert result['updated'] == 1 and result['reference_entries'][IDS[0]] == identity
    before = snapshot(cat)
    path, sha, _ = capture('retitled', title='別の完全題')
    result = cat.import_tobooks_publisher_references(path, sha, reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert result['inserted'] == result['updated'] == 0 and snapshot(cat) == before
    assert cat.work_detail(identity)['read_status'] == 'reading'


@pytest.mark.parametrize('change', ['role', 'isbn', 'unit', 'story', 'conflict', 'label'])
def test_incompatible_existing_source_output_is_rejected_atomically(bundle, monkeypatch, change):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, *_ = bundle
    original = source.verify_publisher_references
    def invalid(*args):
        rows = original(*args)
        row = rows[-1]
        if change == 'role': row['credits'][0]['role'] = 'author'
        elif change == 'isbn': row['isbn'] = '978-4-04-429101-3'
        elif change == 'unit': row['source_unit'] = 'story_series'
        elif change == 'story': row['story_series_asserted'] = True
        elif change == 'conflict': row['metadata_conflict'] = True
        else: row['label'] = '原資料にない推定label'
        return rows
    monkeypatch.setattr(source, 'verify_publisher_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before


def test_source_or_stored_proof_tampering_cannot_be_publicly_projected(bundle):
    cat, _, _, raw, _, _ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['publisher_product_reference']['credits'][0]['role'] = 'author'
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    with pytest.raises(ValueError): cat.work_detail(identity)
    raw.write_text('changed own raw', encoding='utf-8')
    with pytest.raises(ValueError): cat.similar_works(identity)


def test_other_provider_is_not_read_and_second_candidate_failure_writes_nothing(bundle, monkeypatch):
    cat, *_ = bundle
    encoded = '{"raw_path":"absent-other-provider"}'
    old = ('lnr:pub:kadokawaprint:' + 'a' * 32, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest())
    with cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)', old)
    before = snapshot(cat)
    original = cat._project_publisher_reference
    def fail(provider, record, cache):
        if record['publisher_product_reference']['publisher_reference_id'] == IDS[1]:
            raise ValueError('second candidate fails')
        return original(provider, record, cache)
    monkeypatch.setattr(cat, '_project_publisher_reference', fail)
    with pytest.raises(ValueError, match='second candidate'): adopt(bundle)
    assert snapshot(cat) == before


def test_independent_connection_race_is_detected_before_write(bundle, monkeypatch):
    cat, *_ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        racing = tuple(db.execute('SELECT * FROM entries').fetchone())
        db.execute('DELETE FROM entries')
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
    with connect() as db: assert [tuple(r) for r in db.execute('SELECT * FROM entries')] == [racing]
