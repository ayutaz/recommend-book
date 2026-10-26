"""Hifumi formal listing facts do not turn opaque IDs or LN nav into identities."""
import hashlib
import json

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_hifumi_references_v1 import frame


IDS = ['hifumi-product:9780000000000', 'hifumi-product:9780000000001', 'hifumi-product:9780000000002']


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_hifumi_references_v1 as source
    path, sha, proof = frame.__wrapped__(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    verify, raw = source.verify_hifumi_references, source._source
    calls, pages = [], []
    def checked(path, sha): calls.append(str(path)); return verify(path, sha)
    def read(root, pin, url): pages.append(url); return raw(root, pin, url)
    monkeypatch.setattr(source, 'verify_hifumi_references', checked)
    monkeypatch.setattr(source, '_source', read)
    return cat, path, sha, proof, calls, pages


def adopt(e, ids=IDS):
    cat, path, sha, *_ = e
    return cat.import_hifumi_publisher_references(path, sha, reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_native_three_label_nav_core_public_and_scoped_search_preserve_unknowns(bundle):
    cat, _, _, _, calls, pages = bundle
    result = adopt(bundle)
    assert result['source_references'] == result['requested_references'] == result['inserted'] == 3
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 4
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:hifumi:') and len(identity.rsplit(':', 1)[1]) == 32
    calls.clear(); pages.clear()
    row = cat.work_detail(identity)
    assert len(calls) == 1 and len(pages) == 4
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['publisher_product_reference']['form'] is None and row['publication_label'] == 'サーガフォレスト'
    assert row['publisher_product_reference']['credits'] == [{'role': 'author', 'name': '架空著者'}]
    assert row['publisher_product_reference']['credit_literals'] == []  # Native verifier omits role_display.
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    assert row['reference_scope'] == 'private_bibliographic_discovery'
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'hifumi_product_reference' and shown['isbn'] == []
    assert shown['publisher_product_reference']['credit_literals'] == []
    assert 'PRIVATE-PROSE-SENTINEL' not in json.dumps(shown)
    found = cat.search_catalogue('架空著者', entry_kind='publisher_reference')
    assert found['total'] == 2
    unknown = cat.work_detail(result['reference_entries'][IDS[2]])
    assert unknown['publisher_product_reference']['credits'] == [] and unknown['authors'] == []
    assert cat.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert cat.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_idempotency_and_native_cache_keep_old_shapes(bundle):
    cat, path, sha, _, calls, pages = bundle
    with pytest.raises(TypeError): cat.import_hifumi_publisher_references(path, sha)
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['hifumi-book-reference:9780000000000']):
        with pytest.raises(ValueError): adopt(bundle, ids)
    assert calls == []
    first = adopt(bundle); before = snapshot(cat); calls.clear(); pages.clear()
    again = adopt(bundle)
    assert again['unchanged'] == 3 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries'] and snapshot(cat) == before
    assert len(calls) == 1 and len(pages) == 4
    calls.clear(); pages.clear(); cache = {}
    native = cat._hifumi_native_references(path, sha, cache)
    safe = cat._hifumi_publisher_references(path, sha, cache)
    assert native[0]['credits'] == safe[IDS[0]]['credits'] and 'credit_literals' not in native[0]
    assert len(calls) == 1 and len(pages) == 4


@pytest.mark.parametrize('field', ['isbn', 'publisher', 'form', 'label', 'url', 'role'])
def test_unknowns_and_native_own_label_role_contract_are_atomic(bundle, monkeypatch, field):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, *_ = bundle
    verify = source.verify_publisher_references
    def invalid(*args):
        rows = verify(*args); row = rows[1]
        if field == 'isbn': row['isbn'] = '9784086310017'
        elif field == 'publisher': row['publisher'] = '一二三書房'
        elif field == 'form': row['form'] = '文庫'
        elif field == 'label': row['label'] = 'コミック'; row['label_literals'] = ['コミック']
        elif field == 'url': row['source_url'] += '?wrong=1'
        else: row['credits'][0]['role'] = 'inferred-author'
        return rows
    monkeypatch.setattr(source, 'verify_publisher_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before


def test_stored_rehashed_url_isbn_claim_and_later_source_raw_failure_are_rejected(bundle):
    cat, path, _, proof, *_ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['isbn'] = [IDS[0].split(':')[1]]
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): cat.work_detail(identity)
    # A fresh target catalogue cannot retain a first proposal after a later source fails.
    clean = LightNovelCatalogue(path.parent / 'fresh.sqlite')
    (path.parent / proof['brands'][-1]['raw_path']).write_bytes(b'changed last brand source')
    with pytest.raises(ValueError): clean.import_hifumi_publisher_references(path, bundle[2], reference_ids=IDS)
    assert snapshot(clean)['entries'] == []
