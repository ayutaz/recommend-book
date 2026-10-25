"""TO confirmed title variants are discovery text, never persisted aliases."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_light_novel_catalogue_v1 import edition_bundle, publisher_edition_v2, setup_catalogue


@pytest.fixture(params=[None, '3'])
def confirmed(request, publisher_edition_v2, monkeypatch):
    import recommend_book.light_novel_publisher_references_v1 as source
    volume = request.param
    cat, record, _ = publisher_edition_v2(publisher='TOブックス', imprint='TOブックス',
        title='正式主題 : 完全副題', volume=volume)
    title = '正式主題' + (volume or '') + '～完全副題～'
    reference = {'publisher_reference_id': 'tobooks-product:123', 'source_unit': 'publisher_product_reference',
        'source_url': 'https://www.tobooks.jp/contents/123', 'title': title,
        'isbn': '9784798613161', 'publisher': 'TOブックス', 'label': None, 'label_literals': [], 'form': None,
        'credits': [{'role': 'unknown', 'name': '名前表示'}], 'official_lightnovel_tag': True,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_evidence': [{'source_url': 'https://www.tobooks.jp/contents', 'raw_sha256': 'a' * 64,
            'locator': 'ul#search_ul .search_ul_a[1]'}]}
    calls = []
    def verify(provider, path, sha):
        calls.append(provider)
        return [deepcopy(reference)]
    monkeypatch.setattr(source, 'verify_publisher_references', verify)
    assert cat.import_editions([record])['inserted'] == 1
    assert cat.confirm_publisher_editions('tobooks', 'fixture-manifest', 'a' * 64)['confirmed'] == 1
    calls.clear()
    return cat, record, reference, calls


def snapshot(cat):
    with cat._connect() as db:
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)] for table in
                ('entries', 'edition_book_confirmations', 'edition_series_memberships', 'read_status')}


def test_only_existing_to_match_compositions_find_the_official_query_without_mutation(confirmed):
    cat, record, reference, calls = confirmed
    before = snapshot(cat)
    result = cat.search_catalogue(reference['title'], entry_kind='edition')
    assert result['total'] == 1 and calls == ['tobooks']
    row = result['items'][0]
    assert row['entry_id'] == 'lne:ndl:' + record['id'] and row['title'] == record['title']
    assert reference['title'] not in row['aliases'] and row.get('series_entry_id') is None
    assert row['edition_confirmation_evidence']['author_identity_asserted'] is False
    assert row['edition_confirmation_evidence']['product_sku_identity_asserted'] is False
    assert snapshot(cat) == before


@pytest.mark.parametrize('change', ['digest', 'record_sha', 'source_id', 'source_url', 'rule', 'forged_words', 'malformed_match'])
def test_saved_to_claim_cannot_supply_unreproduced_search_text(confirmed, change):
    cat, _, reference, calls = confirmed
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM edition_book_confirmations').fetchone()[0])
        query = reference['title']
        if change == 'record_sha': proof['edition_record_sha256'] = '0' * 64
        elif change == 'source_id': proof['publisher_book_id'] = 'tobooks-product:999'
        elif change == 'source_url': proof['match']['official_source_url'] = 'https://www.tobooks.jp/contents/999'
        elif change == 'rule': proof['match']['title_match']['rule'] = 'unapproved_rule'
        elif change == 'malformed_match': proof['match'] = []
        elif change == 'forged_words':
            proof['match']['title_match']['subtitle'] = '捏造巻題'
            query = query.replace('完全副題', '捏造巻題')
        encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
            (encoded, '0' * 64 if change == 'digest' else hashlib.sha256(encoded.encode()).hexdigest()))
    assert cat.search_catalogue(query)['total'] == 0
    if change != 'forged_words': assert calls == []


def test_to_listing_and_confirmed_edition_share_one_frame_verification(confirmed):
    cat, _, reference, calls = confirmed
    cat.import_tobooks_publisher_references('fixture-manifest', 'a' * 64,
        reference_ids=[reference['publisher_reference_id']])
    calls.clear()
    result = cat.search_catalogue(reference['title'])
    assert result['total'] == 2 and {r['entry_kind'] for r in result['items']} == {'edition', 'publisher_reference'}
    assert calls == ['tobooks']
    edition = next(r for r in result['items'] if r['entry_kind'] == 'edition')
    listing = next(r for r in result['items'] if r['entry_kind'] == 'publisher_reference')
    assert edition['edition_bibliography_confirmed'] is True and listing['light_novel_confirmed'] is False


def test_same_isbn_without_formal_to_binding_cannot_supply_title_variants(confirmed):
    cat, _, reference, calls = confirmed
    with cat._connect() as db: db.execute('DELETE FROM edition_book_confirmations')
    assert cat.search_catalogue(reference['title'])['total'] == 0 and calls == []


def test_to_source_expiry_cannot_publish_saved_composed_title(confirmed, monkeypatch):
    import recommend_book.light_novel_publisher_references_v1 as source
    cat, _, reference, _ = confirmed
    def expired(*args): raise ValueError('pinned source expired')
    monkeypatch.setattr(source, 'verify_publisher_references', expired)
    assert cat.search_catalogue(reference['title'])['total'] == 0
