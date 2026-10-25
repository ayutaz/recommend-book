"""BNK index cards provide independent listings, without detail-field guesses."""
import hashlib
import json

import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_overlap_references_v1 import fixture, change


IDS = ['overlap-product:id1', 'overlap-product:id2']


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    import recommend_book.light_novel_overlap_references_v1 as source
    env = fixture(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    calls, pages = [], []
    verify, raw = source.verify_overlap_references, source._source
    def checked(path, sha):
        calls.append(str(path)); return verify(path, sha)
    def read(root, receipt, url):
        pages.append(url); return raw(root, receipt, url)
    monkeypatch.setattr(source, 'verify_overlap_references', checked)
    monkeypatch.setattr(source, '_source', read)
    return cat, env, calls, pages


def adopt(e, ids=IDS):
    cat, env, *_ = e
    return cat.import_overlap_bunko_publisher_references(env['path'], env['sha'], reference_ids=ids)


def snapshot(cat):
    with cat._connect() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: [tuple(r) for r in db.execute('SELECT * FROM ' + t + ' ORDER BY 1')] for t in tables}


def test_full_native_index_core_and_public_keep_badge_only_and_unknown_fields(bundle):
    cat, _, calls, pages = bundle
    result = adopt(bundle)
    assert result['source_references'] == 1019 and result['requested_references'] == result['inserted'] == 2
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 34
    identity = result['reference_entries'][IDS[0]]
    assert identity.startswith('lnr:pub:overlap:') and len(identity.rsplit(':', 1)[1]) == 32
    calls.clear(); pages.clear()
    row = cat.work_detail(identity)
    assert len(calls) == 1 and len(pages) == len(set(pages)) == 34
    assert row['isbn'] == row['authors'] == row['publishers'] == row['members'] == []
    assert row['publication_label'] == 'オーバーラップ文庫'
    assert row['isbn_basis'] == 'unknown_no_explicit_ISBN_field'
    product = row['publisher_product_reference']
    assert product['publisher_reference_id'] == IDS[0] and product['credits'] == product['credit_literals'] == []
    assert product['form'] is None and product['label'] == 'オーバーラップ文庫'
    assert all(row[k] is False for k in ('light_novel_confirmed', 'light_novel_identity_asserted',
        'edition_identity_asserted', 'story_series_identity_asserted', 'original_work_identity_asserted', 'recommendable'))
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'overlap_bunko_product_reference' and shown['isbn'] == []
    assert 'PRIVATE_' not in json.dumps(shown) and 'FORGED_ROSTER' not in json.dumps(shown)
    assert cat.set_read_status(identity, 'reading')['read_status'] == 'reading'
    assert cat.similar_works(identity)['mode'] == 'insufficient_evidence'


def test_explicit_selection_and_reimport_keep_uuid_and_all_old_tuples(bundle):
    cat, env, calls, _ = bundle
    with pytest.raises(TypeError): cat.import_overlap_bunko_publisher_references(env['path'], env['sha'])
    for ids in (None, [], IDS[0], IDS[:1] * 2, [None], ['overlap-nvl-product:id1']):
        with pytest.raises(ValueError): adopt(bundle, ids)
    assert calls == []
    first = adopt(bundle); before = snapshot(cat); calls.clear()
    again = adopt(bundle)
    assert again['unchanged'] == 2 and again['inserted'] == again['updated'] == 0
    assert again['reference_entries'] == first['reference_entries'] and snapshot(cat) == before and len(calls) == 1


@pytest.mark.parametrize('field', ['isbn', 'publisher', 'form', 'label', 'credits', 'url', 'unit'])
def test_native_unknown_fields_or_category_mismatch_are_atomic(bundle, monkeypatch, field):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, *_ = bundle
    verify = source.verify_publisher_references
    def invalid(*args):
        rows = verify(*args); row = rows[1]
        if field == 'isbn': row['isbn'] = '9784086310017'
        elif field == 'publisher': row['publisher'] = 'オーバーラップ'
        elif field == 'form': row['form'] = '文庫'
        elif field == 'label': row['label'] = 'オーバーラップノベルス'; row['label_literals'] = [row['label']]
        elif field == 'credits': row['credits'] = [{'role': 'unknown', 'name': '推測人物'}]
        elif field == 'url': row['source_url'] = row['source_url'].replace('cat=BNK', 'cat=NVL')
        else: row['source_unit'] = 'edition'
        return rows
    monkeypatch.setattr(source, 'verify_publisher_references', invalid)
    before = snapshot(cat)
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before


def test_retitled_snapshot_is_held_and_rehashed_isbn_cannot_be_projected(bundle):
    cat, env, *_ = bundle
    identity = adopt(bundle, IDS[:1])['reference_entries'][IDS[0]]
    before = snapshot(cat)
    root = env['root'] / 'next'; root.mkdir()
    revised = fixture(root); change(revised, '完全題1　巻３【合本】', '改題1　巻３【合本】')
    result = cat.import_overlap_bunko_publisher_references(revised['path'], revised['sha'], reference_ids=IDS[:1])
    assert result['held_reasons'] == {'source_reference_scope_or_title_changed': 1}
    assert snapshot(cat) == before
    with cat._connect() as db:
        record = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
        record['isbn'] = ['9784086310017']
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE entries SET record_json=?,record_sha256=? WHERE entry_id=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest(), identity))
    with pytest.raises(ValueError): cat.work_detail(identity)


def test_last_source_page_failure_cannot_partially_save_or_touch_another_provider(bundle):
    cat, env, calls, _ = bundle
    encoded = '{"absent_other_provider_raw":true}'
    with cat._connect() as db: db.execute('INSERT INTO entries VALUES(?,?,?,?)',
        ('lnr:pub:overlapdetails:' + 'a' * 32, 'publisher_reference', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    before = snapshot(cat)
    (env['root'] / '34.bin').write_bytes(b'changed last card page')
    with pytest.raises(ValueError): adopt(bundle)
    assert snapshot(cat) == before and len(calls) == 1
