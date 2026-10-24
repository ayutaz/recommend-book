import json
import threading
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from recommend_book.recommendation.scale_site_v1 import create_server, public_work


class TypedCatalogue:
    def __init__(self):
        self.calls = []
        self.status = 'unknown'

    def item(self, entry_id='lns:wd:Q5357270'):
        return {'entry_id': entry_id, 'entry_kind': 'series', 'family': 'light_novel',
                'title': '魔法科高校の劣等生', 'qid': 'Q5357270', 'read_status': self.status,
                'source_url': 'https://www.wikidata.org/wiki/Q5357270',
                'members': [{'entry_id': 'lne:ndl:000001', 'entry_kind': 'edition',
                    'title': '第1巻', 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I000001',
                    'raw_path': 'PRIVATE-SENTINEL'}], 'raw_path': 'PRIVATE-SENTINEL'}

    def search_catalogue(self, **kwargs):
        self.calls.append(kwargs)
        return dict(items=[self.item()], total=1, page=1, page_size=30, pages=1)

    def search_world_catalogue(self, **kwargs):
        self.calls.append(dict(world=True, **kwargs))
        return dict(items=[], total=0, page=1, page_size=30, pages=0)

    def work_detail(self, identifier):
        return self.item(identifier) if identifier in ('lns:wd:Q5357270', 'lne:ndl:000001', 'lns:ndl:R100000002-I032593374', 'Q2') else None

    def similar_works(self, identifier, **kwargs):
        return dict(mode='source_similarity', items=[self.item()])

    def set_read_status(self, identifier, status):
        self.status = status
        return self.item(identifier)

    def recommend_preferences(self, **kwargs):
        self.calls.append(kwargs)
        return dict(items=[self.item()])

    def catalogue_stats(self):
        return dict(works=20, light_novel_series=1000, light_novel_featured_series=500,
                    light_novel_works=50, light_novel_editions=900, raw_path='PRIVATE-SENTINEL')

    def export_reading_history(self):
        return {'sha256': 'fixture', 'records': [{'entry_id': 'lns:wd:Q5357270', 'status': self.status},
            {'entry_id': 'lns:ndl:R100000002-I032593374', 'status': self.status}]}


@pytest.fixture
def site():
    catalogue = TypedCatalogue()
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', catalogue
    server.shutdown()
    server.server_close()
    thread.join()


def fetch(base, path, payload=None, headers=None):
    request = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers or ({'Content-Type': 'application/json', 'X-Catalogue-Request': '1'} if payload is not None else {}))
    with urlopen(request) as response:
        return json.load(response)


def test_typed_search_detail_similarity_status_and_world_compatibility(site):
    base, catalogue = site
    data = fetch(base, '/api/entries?family=light_novel&entry_kind=series')
    assert catalogue.calls[-1]['family'] == 'light_novel'
    assert catalogue.calls[-1]['entry_kind'] == 'series'
    assert data['items'][0]['entry_id'] == 'lns:wd:Q5357270'
    entry = '/api/entries/' + quote('lns:wd:Q5357270', safe='')
    assert fetch(base, entry)['members'][0]['title'] == '第1巻'
    assert fetch(base, entry + '/similar')['items']
    assert fetch(base, entry + '/read-status', {'status': 'reading'})['read_status'] == 'reading'
    assert fetch(base, '/api/works')['items'] == []
    assert catalogue.calls[-1]['world'] is True
    assert fetch(base, '/api/works/Q2')['qid'] == 'Q5357270'
    assert fetch(base, '/api/stats')['light_novel_series'] == 1000
    assert fetch(base, '/api/history/export')['records'][0]['entry_id'] == 'lns:wd:Q5357270'


def test_typed_preferences_keep_qids_distinct_and_reject_unsafe_identifiers(site):
    base, catalogue = site
    result = fetch(base, '/api/recommendations', {'liked_qids': ['Q2'],
        'liked_entry_ids': ['lns:wd:Q5357270', 'lne:ndl:000001'], 'family': 'light_novel'})
    assert result['items'][0]['entry_kind'] == 'series'
    assert catalogue.calls[-1]['liked_qids'] == ['Q2']
    assert catalogue.calls[-1]['liked_entry_ids'] == ['lns:wd:Q5357270', 'lne:ndl:000001']
    for payload in [{'liked_entry_ids': ['../../private']}, {'liked_entry_ids': ['Q2']}, {'family': 'private'}]:
        with pytest.raises(HTTPError) as error:
            fetch(base, '/api/recommendations', payload)
        assert error.value.code == 400


def test_ndl_only_series_supports_detail_likes_and_status_without_fake_qid(site):
    base, catalogue = site
    identifier = 'lns:ndl:R100000002-I032593374'
    path = '/api/entries/' + quote(identifier, safe='')
    assert fetch(base, path)['entry_id'] == identifier
    assert fetch(base, path + '/similar')['items']
    assert fetch(base, path + '/read-status', {'status': 'reading'})['read_status'] == 'reading'
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier], 'family': 'light_novel'})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]
    public = public_work({'entry_id': identifier, 'entry_kind': 'series', 'qid': None,
        'original_language': None, 'source_url': 'https://ndlsearch.ndl.go.jp/books/R100000002-I032593374',
        'series_assertion_source_url': 'https://example.org/novel-series',
        'series_identity_basis': 'publisher_series_and_ndl_bibliography',
        'admission_status': 'ndl_series_identity_verified'})
    assert public['entry_id'] == identifier and public['qid'] == '' and public['original_language'] is None
    assert public['series_assertion_source_url'] == 'https://example.org/novel-series'
    assert public['admission_status'] == 'ndl_series_identity_verified'
    assert fetch(base, '/api/history/export')['records'][1]['entry_id'] == identifier
    for path in ['/api/entries/lns%3Awd%3AQ2%2F..%2Fprivate', '/api/entries/lns:wd:Q0',
                 '/api/entries/lne:ndl:..', '/api/entries/lns%253Awd%253AQ2']:
        with pytest.raises(HTTPError) as error:
            fetch(base, path)
        assert error.value.code == 404
    for path in ['/api/entries?family=private', '/api/entries?entry_kind=private']:
        with pytest.raises(HTTPError) as error:
            fetch(base, path)
        assert error.value.code == 400


def test_public_typed_entry_retains_only_safe_member_fields():
    row = TypedCatalogue().item()
    row['members'].append({'entry_id': '../../private', 'title': 'PRIVATE-SENTINEL'})
    result = public_work(row)
    assert result['family'] == 'light_novel'
    assert len(result['members']) == 1
    assert result['members'][0]['entry_kind'] == 'edition'
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    edition = public_work({'qid': 'lne:ndl:000001', 'entry_id': 'lne:ndl:000001',
                          'entry_kind': 'edition', 'source_url': 'javascript:alert(1)'})
    assert edition['qid'] == '' and edition['source_url'] == ''


def test_ndl_candidates_preserve_uncertain_kind_language_and_relation():
    result = public_work({'entry_id': 'lne:ndl:000001', 'entry_kind': 'edition',
        'family': 'light_novel', 'qid': None, 'original_language': None, 'language': 'ja',
        'light_novel_confirmed': False, 'admission_status': 'bibliographic_candidate_only',
        'label_candidate': True, 'publication_label': '電撃文庫', 'volume': '1',
        'relation_resolution': 'original_work_and_story_series_unresolved', 'isbn': ['9780000000000'],
        'private_path': 'PRIVATE-SENTINEL'})
    assert result['original_language'] is None and result['language'] == 'ja'
    assert result['light_novel_confirmed'] is False and result['label_candidate'] is True
    assert result['admission_status'] == 'bibliographic_candidate_only'
    assert result['relation_resolution'] == 'original_work_and_story_series_unresolved'
    assert result['publication_label'] == '電撃文庫'
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    verified = public_work({'entry_id': 'lne:ndl:000001', 'entry_kind': 'edition',
        'light_novel_confirmed': True, 'admission_status': 'edition_series_membership_verified',
        'relation_resolution': 'edition_series_membership_verified'})
    assert verified['light_novel_confirmed'] is True
    assert verified['admission_status'] == 'edition_series_membership_verified'


def test_series_content_match_has_a_reader_facing_reason():
    result = public_work({'entry_id': 'lns:wd:Q2', 'entry_kind': 'series',
                          'reasons': [{'kind': 'series_content_feature', 'label': 'ファンタジー'}]})
    assert result['reasons'][0]['detail'] == '基準シリーズと内容特徴が共通'


def test_confirmed_publisher_edition_exposes_credit_without_private_proof():
    result = public_work({'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'light_novel_confirmed': True, 'edition_bibliography_confirmed': True,
        'admission_status': 'publisher_ndl_edition_verified',
        'confirmation_source_url': 'https://www.kadokawa.co.jp/product/000001/',
        'confirmation_proof': {'raw_path': 'PRIVATE-SENTINEL'}})
    assert result['edition_bibliography_confirmed'] is True
    assert result['admission_status'] == 'publisher_ndl_edition_verified'
    assert result['confirmation_source_url'] == 'https://www.kadokawa.co.jp/product/000001/'
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    unsafe = public_work({'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'edition_bibliography_confirmed': True, 'confirmation_source_url': 'javascript:alert(1)'})
    assert not unsafe.get('confirmation_source_url')


def test_content_sources_keep_distinct_sections_of_one_revision():
    result = public_work({'entry_id': 'lns:wd:Q2', 'content_sources': [
        {'source_url': 'https://en.wikipedia.org/w/index.php?oldid=123', 'revision': '123',
         'locator': section, 'display_allowed': True, 'quote_allowed': False}
        for section in ('lead', 'Synopsis', 'Synopsis')]})
    assert [source['locator'] for source in result['content_sources']] == ['lead', 'Synopsis']


def test_legacy_catalogue_accepts_ui_default_payload_and_entries_fallback():
    from test_scale_site_v1 import Catalogue

    catalogue = Catalogue()
    received = []
    catalogue.recommend_preferences = lambda **kwargs: received.append(kwargs) or {'items': []}
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        assert fetch(base, '/api/entries?family=all')['items'][0]['qid'] == 'Q2'
        assert fetch(base, '/api/entries?family=light_novel')['items'] == []
        assert fetch(base, '/api/entries?entry_kind=series')['items'] == []
        fetch(base, '/api/recommendations', {'liked_qids': ['Q2'], 'liked_entry_ids': [], 'family': 'all'})
        assert 'family' not in received[-1] and 'liked_entry_ids' not in received[-1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_typed_status_keeps_local_origin_and_csrf_protection(site):
    base, _ = site
    path = '/api/entries/lns%3Awd%3AQ5357270/read-status'
    for headers in [{'Content-Type': 'application/json'},
                    {'Content-Type': 'application/json', 'X-Catalogue-Request': '1', 'Origin': 'https://evil.example'}]:
        with pytest.raises(HTTPError) as error:
            fetch(base, path, {'status': 'read'}, headers)
        assert error.value.code == 403
