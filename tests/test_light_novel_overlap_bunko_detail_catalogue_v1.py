"""BNK detail facts coexist with the native badge-only listing."""
import hashlib
import json
import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.light_novel_site_backend_v1 import UnifiedCatalogue
from types import SimpleNamespace
from recommend_book.recommendation.scale_site_v1 import public_work
from test_light_novel_overlap_references_v1 import fixture as index_fixture, save as index_save
from test_light_novel_overlap_details_v1 import fixture as detail_fixture, receipt, dump

REF = 'overlap-bnk-detail-product:id1'


def bundle(tmp_path):
    env = index_fixture(tmp_path)
    for page in env['manifest']['pages']:
        page['source_snapshot']['final_url'] = page['source_snapshot']['source_url']
    index_save(env)
    native = tmp_path / 'detail-template'
    native.mkdir()
    template = detail_fixture(native)
    html = (native / 'raw/detail1.html').read_text('utf8').replace('完全題1　３ 副題【合本】', '完全題1　巻３【合本】')
    url = 'https://over-lap.co.jp/title/product/0/id1/?cat=BNK&swrd='
    rec = receipt(tmp_path, 'own-detail', url, html)
    rp = tmp_path / 'own-detail-receipt.json'
    manifest = {'schema_version': 'overlap-product-details-proof-v1',
        'source_frames': [{'manifest_path': env['path'].name, 'manifest_sha256': env['sha']}],
        'details': [{'source_url': url, 'category': 'BNK', 'frame_index': 0, 'page': 1,
                     'card_ordinal': 1, 'receipt_path': rp.name, 'receipt_sha256': dump(rp, rec)}]}
    path = tmp_path / 'detail-proof.json'
    return env, manifest, path, dump(path, manifest)


def test_index_and_detail_coexist_and_public_search_preserve_roles(tmp_path):
    env, manifest, path, sha = bundle(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    old = cat.import_overlap_bunko_publisher_references(env['path'], env['sha'], reference_ids=['overlap-product:id1'])
    old_id = old['reference_entries']['overlap-product:id1']
    cat.set_read_status(old_id, 'reading')
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (old_id,)).fetchone())
        statuses = [tuple(x) for x in db.execute('SELECT * FROM read_status')]
    added = cat.import_overlap_bunko_detail_publisher_references(path, sha, reference_ids=[REF])
    identity = added['reference_entries'][REF]
    assert added['inserted'] == 1 and identity.startswith('lnr:pub:overlapbunkodetails:')
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM entries WHERE entry_id=?', (old_id,)).fetchone()) == before
        assert [tuple(x) for x in db.execute('SELECT * FROM read_status')] == statuses
        stored = json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0])
    assert stored['source_reference_assertions'][0]['local_reference_key']['native_publisher_product_reference_id'] == 'overlap-product:id1'
    row = cat.work_detail(identity)
    assert row['isbn'] == ['978-4-8240-1931-8']
    assert row['authors'] == row['members'] == []
    assert row['publisher_product_reference']['credits'] == [{'role': 'unknown', 'name': '人物'}, {'role': 'illustrator', 'name': '画家'}]
    assert not row['story_series_identity_asserted'] and not row['edition_identity_asserted']
    assert cat.search_catalogue('人物')['total'] == 1
    assert cat.search_catalogue('9784824019318')['total'] == 1
    assert cat.search_catalogue('978-4-8240-1931-8', entry_kind='publisher_reference')['total'] == 1
    unified = UnifiedCatalogue(SimpleNamespace(db_path=tmp_path / 'world.sqlite'), cat)
    assert unified.work_detail(identity)['isbn'] == row['isbn']
    shown = public_work(row)
    assert shown['publisher_reference_kind'] == 'overlap_bunko_detail_product_reference'
    assert shown['isbn'] == row['isbn']
    assert shown['publisher_product_reference']['publisher_reference_id'] == REF
    assert shown['publisher_product_reference']['credits'][0]['role'] == 'unknown'
    assert 'PRIVATE_' not in json.dumps(shown) and str(tmp_path) not in json.dumps(shown)
    assert cat.import_overlap_bunko_detail_publisher_references(path, sha, reference_ids=[REF])['unchanged'] == 1


@pytest.mark.parametrize('tamper', ['source_hash', 'receipt', 'title', 'role', 'category', 'native_ID'])
def test_invalid_evidence_never_writes(tmp_path, tamper):
    env, manifest, path, sha = bundle(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    pin = manifest['details'][0]
    rp = tmp_path / pin['receipt_path']
    rec = json.loads(rp.read_text('utf8'))
    raw = tmp_path / rec['raw_path']
    if tamper == 'source_hash': rec['sha256'] = '0' * 64
    elif tamper == 'receipt': rec['http_status'] = 503
    elif tamper in ('title', 'role'):
        text = raw.read_text('utf8').replace('完全題1', '別書名') if tamper == 'title' else raw.read_text('utf8').replace('人物', '著者：人物')
        raw.write_text(text, 'utf8'); rec.update(sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), byte_length=raw.stat().st_size)
    elif tamper == 'category': pin['category'] = 'NVL'
    else: pin['source_url'] = pin['source_url'].replace('/id1/', '/other/')
    pin['receipt_sha256'] = dump(rp, rec); sha = dump(path, manifest)
    with pytest.raises(ValueError): cat.import_overlap_bunko_detail_publisher_references(path, sha, reference_ids=[REF])
    with cat._connect() as db: assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == 0


def test_wrong_typed_ID_and_non_BNK_source_rejected(tmp_path):
    env = detail_fixture(tmp_path)
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    for refs in [['overlap-product:id1'], ['overlap-nvl-product:opaque1'], [REF, REF]]:
        with pytest.raises(ValueError): cat.import_overlap_bunko_detail_publisher_references(env['path'], env['sha'], reference_ids=refs)
    with pytest.raises(ValueError, match='BNK'): cat.import_overlap_bunko_detail_publisher_references(env['path'], env['sha'], reference_ids=[REF])


@pytest.mark.parametrize('wrong', ['overlap-product:id1', 'overlap-nvl-product:id1'])
def test_public_BNK_nested_reference_rejects_other_native_scopes(wrong):
    from recommend_book.recommendation.scale_site_v1 import _public_publisher_product_references
    rows = _public_publisher_product_references([{'publisher_reference_id': wrong, 'title': '完全題', 'credits': []}], source_kind='overlap_bunko_detail_product_reference')
    assert all('publisher_reference_id' not in row for row in rows)
