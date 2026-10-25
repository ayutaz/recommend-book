"""Old accepted KAD title compositions widen discovery after full raw replay."""
import hashlib
import json

import pytest

from test_light_novel_catalogue_v1 import edition_bundle, publisher_edition_v2, setup_catalogue, write


@pytest.fixture(params=['v2', 'v5', 'v6'])
def confirmed(request, publisher_edition_v2, tmp_path):
    version = request.param
    form = 1 if version == 'v6' else 3
    publisher = '角川書店' if version == 'v5' else 'KADOKAWA'
    cat, record, _ = publisher_edition_v2(title='正式主題 : 完全副題', volume='3', publisher=publisher)
    assert cat.import_editions([record])['inserted'] == 1
    title = '正式主題３～完全副題～'
    raw = tmp_path / 'official-books.json'
    raw_sha = write(raw, {'count_raw': {'book': 1}, 'product_form': {'1': {'name': '単行本'}},
        'result': {'book': [{'itemCode': '123456789012', 'title': title, 'isbn': '9784798613161',
            'label_id': 154, 'product_form_id': form, 'disp_author_name': '<li>著者 著者</li>'}]}})
    path = tmp_path / 'manifest.json'
    sha = write(path, {'schema_version': 'kadokawa-publisher-books-v1', 'rights_scope': 'reference_only_bibliography',
        'pages': [{'source_url': 'https://www.kadokawa.co.jp/product/search/',
            'final_url': 'https://www.kadokawa.co.jp/product/search/', 'http_status': 200, 'http_method': 'POST',
            'request_data': {'label': '154', 'releaseDate': '1', 'size': '20', 'pageno': '1',
                'pageno_book': '1', 'pageno_media': '1'}, 'raw_path': raw.name, 'sha256': raw_sha}]})
    assert cat.confirm_publisher_editions('kadokawa', path, sha)['confirmed'] == 1
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM edition_book_confirmations').fetchone()[0])
    assert proof['match']['rule_version'] == 'publisher-ndl-edition-confirmation-' + version
    assert proof['match']['title_match']['rule'] == 'primary_isbd_title_and_explicit_volume'
    return cat, record, title, raw, proof


def snapshot(cat):
    with cat._connect() as db:
        return {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)] for table in
                ('entries', 'edition_book_confirmations', 'edition_series_memberships', 'read_status')}


def test_old_confirmed_composition_finds_query_without_rewriting_any_saved_field(confirmed):
    cat, record, title, _, _ = confirmed
    before = snapshot(cat)
    result = cat.search_catalogue(title, entry_kind='edition')
    assert result['total'] == 1
    row = result['items'][0]
    assert row['title'] == record['title'] and row['entry_id'] == 'lne:ndl:' + record['id']
    assert title not in row['aliases'] and row.get('series_entry_id') is None
    assert row['edition_bibliography_confirmed'] and row['llm_features'] == []
    assert snapshot(cat) == before


@pytest.mark.parametrize('change', ['digest', 'record_sha', 'source_id', 'source_url', 'version',
    'rule', 'fields', 'roundtrip', 'type', 'volume', 'extra_field', 'malformed_match'])
def test_unknown_or_forged_saved_shapes_never_supply_composed_search_text(confirmed, monkeypatch, change):
    cat, _, title, _, proof = confirmed
    if change == 'record_sha': proof['edition_record_sha256'] = '0' * 64
    elif change == 'source_id': proof['publisher_book_id'] = 'kadokawa:999999999999'
    elif change == 'source_url': proof['match']['sources'][1] = 'https://www.kadokawa.co.jp/product/999999999999/'
    elif change == 'version': proof['match']['rule_version'] = 'publisher-ndl-edition-confirmation-v99'
    elif change == 'rule': proof['match']['title_match']['rule'] = 'direct_primary_title'
    elif change == 'fields': proof['match']['title_match'].pop('main')
    elif change == 'roundtrip': proof['match']['title_match']['subtitle'] = '捏造副題'
    elif change == 'type': proof['match']['title_match']['volume'] = 3
    elif change == 'volume': proof['match']['title_match']['volume'] = '4'
    elif change == 'extra_field': proof['match']['title_match']['invented_alias'] = title
    elif change == 'malformed_match': proof['match'] = []
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
            (encoded, '0' * 64 if change == 'digest' else hashlib.sha256(encoded.encode()).hexdigest()))
    monkeypatch.setattr(cat, '_project', lambda *a: pytest.fail('invalid saved composition must not select raw'))
    assert cat.search_catalogue(title)['total'] == 0


def test_rehashed_but_structurally_consistent_fake_title_still_requires_raw_match(confirmed):
    cat, _, title, _, proof = confirmed
    values = proof['match']['title_match']
    values['source_value'] = values['source_value'].replace('完全副題', '偽造副題')
    values['subtitle'] = '偽造副題'
    proof['match']['matched_ndl_title'] = values['source_value']
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    assert cat.search_catalogue(title.replace('完全副題', '偽造副題'))['total'] == 0


def test_source_expiry_cannot_publish_saved_compositions(confirmed):
    cat, _, title, raw, _ = confirmed
    raw.write_text('{}', encoding='utf-8')
    assert cat.search_catalogue(title)['total'] == 0


def test_same_isbn_without_saved_confirmation_does_not_add_compositions(confirmed):
    cat, _, title, _, _ = confirmed
    with cat._connect() as db: db.execute('DELETE FROM edition_book_confirmations')
    assert cat.search_catalogue(title)['total'] == 0
