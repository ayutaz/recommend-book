"""Synthetic official-search snapshots; no private publisher source in CI."""
import hashlib
import json

import pytest

from recommend_book.light_novel_publisher_books_v1 import verify_kadokawa_books


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def publisher_books(tmp_path):
    raw = tmp_path / 'page.json'
    item = {'itemCode': '123456789012', 'title': '架空の冒険 1', 'isbn': '9784000000001',
            'label_id': 330, 'label_site_id': '1043', 'category_id': 12, 'subgenre_id': 86,
            'product_form_id': 3, 'release_date': '2020-01-01',
            'disp_author_name': '<li>著者&nbsp;架空　作家</li><li>イラスト&nbsp;別の人</li>',
            'catchphrase': 'This publisher prose must never be returned.',
            'bookCoverUrl': 'https://example.invalid/cover.jpg'}
    raw_hash = write(raw, {'count_raw': {'book': 1}, 'result': {'book': [item]}})
    manifest = tmp_path / 'manifest.json'
    receipt = {'source_url': 'https://www.kadokawa.co.jp/product/search/',
               'final_url': 'https://www.kadokawa.co.jp/product/search/',
               'http_status': 200, 'http_method': 'POST', 'content_type': 'application/json',
               'request_data': {'label': '330', 'releaseDate': '1', 'size': '20',
                                'pageno': '1', 'pageno_book': '1', 'pageno_media': '1'},
               'raw_path': 'page.json', 'sha256': raw_hash,
               'retrieved_at': '2026-10-25T00:00:00+00:00'}
    body = {'schema_version': 'kadokawa-publisher-books-v1',
            'rights_scope': 'reference_only_bibliography', 'pages': [receipt]}
    return manifest, write(manifest, body), raw, body


def test_reproduce_book_metadata_and_author_roles(publisher_books):
    path, sha, _, _ = publisher_books
    rows = verify_kadokawa_books(path, sha)
    assert len(rows) == 1
    row = rows[0]
    assert row['kind'] == 'edition'
    assert row['label_scope'] == '電撃文庫'
    assert row['isbn'] == '9784000000001'
    assert row['credits'] == [{'role': '著者', 'name': '架空 作家'},
                              {'role': 'イラスト', 'name': '別の人'}]
    assert row['title'] == '架空の冒険 1'
    assert row['publisher'] == 'KADOKAWA'  # legacy producer value, not a book assertion
    assert row['catalogue_owner'] == 'KADOKAWA'
    assert row['publisher_field_basis'] == 'catalogue_owner_context'
    assert row['individual_publisher'] is None
    assert row['official_series_id'] is None
    assert row['evidence']['json_pointer'] == '/result/book/0'
    assert 'catchphrase' not in json.dumps(rows)
    assert 'bookCoverUrl' not in json.dumps(rows)


def test_manifest_and_source_tampering_fail(publisher_books):
    path, sha, raw, _ = publisher_books
    with pytest.raises(ValueError, match='SHA'):
        verify_kadokawa_books(path, '0' * 64)
    raw.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA'):
        verify_kadokawa_books(path, sha)


def test_another_label_does_not_inherit_light_novel_scope(publisher_books):
    path, _, raw, body = publisher_books
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['result']['book'][0]['label_id'] = 47
    body['pages'][0]['sha256'] = write(raw, payload)
    with pytest.raises(ValueError, match='label'):
        verify_kadokawa_books(path, write(path, body))


def test_undeclared_query_or_changed_endpoint_fail(publisher_books):
    path, _, _, body = publisher_books
    body['pages'][0]['source_url'] = 'https://example.invalid/product/search/'
    with pytest.raises(ValueError, match='endpoint'):
        verify_kadokawa_books(path, write(path, body))
    body['pages'][0]['source_url'] = body['pages'][0]['final_url']
    body['pages'][0]['request_data']['kw'] = 'extra query'
    with pytest.raises(ValueError, match='query'):
        verify_kadokawa_books(path, write(path, body))


def test_raw_path_escape_rejected(publisher_books):
    path, _, _, body = publisher_books
    body['pages'][0]['raw_path'] = '../outside.json'
    with pytest.raises(ValueError, match='escapes'):
        verify_kadokawa_books(path, write(path, body))


def test_duplicate_receipts_do_not_inflate_editions(publisher_books):
    path, _, _, body = publisher_books
    body['pages'].append(dict(body['pages'][0]))
    with pytest.raises(ValueError, match='duplicate page'):
        verify_kadokawa_books(path, write(path, body))


def test_assessed_publisher_history_contract_has_public_facts_and_no_mutable_alias_state():
    from recommend_book.light_novel_kadokawa_publisher_context_v1 import assessed_history_context
    first=assessed_history_context()
    assert len(first['named_subsidiaries']) == 9
    assert 'メディアワークス' not in first['named_subsidiaries']
    assert first['source_pages'] == [1]
    assert first['scope'] == 'fixed_assessed_names_for_same_isbn_edition_catalogue_context_only'
    assert not first['all_era_alias_asserted']
    assert not first['individual_book_publisher_asserted']
    assert not first['rights_or_licence_succession_asserted']
    encoded=json.dumps(first)
    assert 'raw_path' not in encoded and 'pdf_path' not in encoded and 'C:' not in encoded
    first['named_subsidiaries'].append('メディアワークス')
    first['source_pages'].append(3)
    second=assessed_history_context()
    assert 'メディアワークス' not in second['named_subsidiaries']
    assert second['source_pages'] == [1]


_ADDITIONAL_KAD_LABELS = [('53', 'MFブックス'), ('104', 'カドカワBOOKS'),
    ('197', 'ファミ通文庫'), ('114', '角川ビーンズ文庫'), ('178', 'ビーズログ文庫')]


@pytest.mark.parametrize('label_id,label', _ADDITIONAL_KAD_LABELS)
def test_kadokawa_additional_label_acceptance_preserves_explicit_filter_scope(publisher_books, label_id, label):
    path, _, raw, body = publisher_books
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['result']['book'][0]['label_id'] = int(label_id)
    body['pages'][0]['request_data']['label'] = label_id
    body['pages'][0]['sha256'] = write(raw, payload)
    row, = verify_kadokawa_books(path, write(path, body))
    assert row['label'] == row['label_scope'] == label
    assert row['official_label_id'] == int(label_id)
    assert row['evidence']['request_data']['label'] == label_id
    assert row['individual_publisher'] is None and row['catalogue_owner'] == 'KADOKAWA'
    assert row['official_series_id'] is None and row['explicit_series_membership'] is None
    assert row['identity_to_catalogue'] == 'unassessed'
    assert 'catchphrase' not in json.dumps(row) and 'bookCoverUrl' not in json.dumps(row)


@pytest.mark.parametrize('label_id,label', _ADDITIONAL_KAD_LABELS)
def test_kadokawa_additional_label_query_cannot_admit_another_item_label(publisher_books, label_id, label):
    path, _, raw, body = publisher_books
    payload = json.loads(raw.read_text(encoding='utf-8'))
    payload['result']['book'][0]['label_id'] = 330
    body['pages'][0]['request_data']['label'] = label_id
    body['pages'][0]['sha256'] = write(raw, payload)
    with pytest.raises(ValueError, match='label'):
        verify_kadokawa_books(path, write(path, body))


def test_kadokawa_paper_form_raw_dictionary_supplies_literal_without_inferring_size(publisher_books):
    path, _, raw, body = publisher_books
    payload = json.loads(raw.read_text(encoding='utf-8'))
    for form_id, dictionary, expected in [
        (1, {'1': {'name': '単行本'}, '13': {'name': '電子専売'}}, '単行本'),
        (13, {'1': {'name': '単行本'}, '13': {'name': '電子専売'}}, '電子専売'),
        (1, {}, None),
    ]:
        payload['result']['book'][0]['product_form_id'] = form_id
        payload['product_form'] = dictionary
        body['pages'][0]['sha256'] = write(raw, payload)
        row, = verify_kadokawa_books(path, write(path, body))
        assert row['official_product_form_name'] == expected
        assert row['product_form_id'] == form_id
        assert 'B6' not in json.dumps(row, ensure_ascii=False)
