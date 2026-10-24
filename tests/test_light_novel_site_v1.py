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


def test_publisher_bibliography_exports_correspondence_without_claiming_edition_identity():
    row = {'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'title': 'Test', 'light_novel_confirmed': True,
        'admission_status': 'publisher_bibliographic_record_verified',
        'publisher_bibliography_matched': True,
        'bibliographic_correspondence_scope': 'publisher_reference_to_ndl_bibliographic_record',
        'bibliography_source_url': 'https://publisher.example/book/1',
        'bibliographic_correspondence_evidence': {'raw_path': 'PRIVATE-SENTINEL'}}
    row['members'] = [dict(row)]
    public = public_work(row)
    for item in [public, public['members'][0]]:
        assert item['publisher_bibliography_matched'] is True
        assert item['bibliographic_correspondence_scope'] == 'publisher_reference_to_ndl_bibliographic_record'
        assert item['bibliography_source_url'] == 'https://publisher.example/book/1'
        assert item['admission_status'] == 'publisher_bibliographic_record_verified'
        assert not item.get('edition_bibliography_confirmed')
        assert 'edition_identity_basis' not in item
    assert 'PRIVATE-SENTINEL' not in json.dumps(public)


@pytest.mark.parametrize(('kind', 'matched', 'scope'), [
    ('series', True, 'publisher_reference_to_ndl_bibliographic_record'),
    ('edition', False, 'publisher_reference_to_ndl_bibliographic_record'),
    ('edition', 'true', 'publisher_reference_to_ndl_bibliographic_record'),
    ('edition', True, 'same_product'),
])
def test_publisher_bibliography_public_scope_requires_verified_bibliographic_record(kind, matched, scope):
    row = {'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': kind,
        'publisher_bibliography_matched': matched, 'bibliographic_correspondence_scope': scope,
        'bibliography_source_url': 'file:///PRIVATE-SENTINEL'}
    public = public_work(row)
    assert 'publisher_bibliography_matched' not in public
    assert 'bibliographic_correspondence_scope' not in public
    assert 'bibliography_source_url' not in public


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


def test_overview_http_publishes_only_safe_counts_and_preference_choices(site, monkeypatch):
    base, catalogue = site
    calls = []
    def overview():
        calls.append('overview')
        return {'stats': {'works': 20, 'light_novel_publisher_bibliographic_records': 7,
                          'raw_path': 'PRIVATE-SENTINEL'},
                'preference_facets': {'genres': [{'qid': 'Q1', 'label': 'Fantasy', 'count': 3}],
                    'languages': [{'value': 'ja', 'count': 3}], 'raw_path': 'PRIVATE-SENTINEL'}}
    monkeypatch.setattr(catalogue, 'catalogue_overview', overview, raising=False)
    data = fetch(base, '/api/overview')
    assert calls == ['overview']
    assert data['stats']['works'] == 20
    assert data['stats']['light_novel_publisher_bibliographic_records'] == 7
    assert data['preference_facets']['languages'] == [{'value': 'ja', 'count': 3}]
    assert data['preference_facets']['genres'][0]['qid'] == 'Q1'
    assert 'PRIVATE-SENTINEL' not in json.dumps(data)


def test_overview_http_supports_catalogues_without_a_combined_operation(site):
    base, _ = site
    data = fetch(base, '/api/overview')
    assert data['stats']['works'] == 20
    assert data['preference_facets']['languages'] == []


@pytest.mark.parametrize(('path', 'kind', 'recommendable', 'expected_calls'), [
    ('/api/entries/lne%3Andl%3A000001/similar', 'edition', False, 0),
    ('/api/entries/lne%3Andl%3A000001/similar', 'edition', True, 1),
    ('/api/entries/lns%3Awd%3AQ5357270/similar', 'series', False, 1),
    ('/api/entries/lnw%3Awd%3AQ3/similar', 'work', False, 1),
    ('/api/works/Q2/similar', 'edition', False, 1),
])
def test_similarity_skips_only_featureless_typed_editions(site, monkeypatch, path, kind, recommendable, expected_calls):
    base, catalogue = site
    detail_calls, similar_calls = [], []
    def detail(identifier):
        detail_calls.append(identifier)
        return dict(catalogue.item(identifier), entry_kind=kind, recommendable=recommendable)
    def similar(identifier, **kwargs):
        similar_calls.append(identifier)
        if not expected_calls:
            raise AssertionError('featureless edition must not read the entire catalogue')
        return {'mode': 'source_similarity', 'items': [catalogue.item()]}
    monkeypatch.setattr(catalogue, 'work_detail', detail)
    monkeypatch.setattr(catalogue, 'similar_works', similar)
    data = fetch(base, path)
    assert len(detail_calls) == 1
    assert len(similar_calls) == expected_calls
    assert bool(data['items']) is bool(expected_calls)
    assert data['personalized'] is False


@pytest.mark.parametrize('provider', ['futaba', 'orange', 'ecobalt', 'sbcr', 'hj', 'arian', 'hjnovels', 'sbcrindex'])
def test_featureless_source_group_similarity_reuses_authenticated_detail(site, monkeypatch, provider):
    base, catalogue = site
    identifier = 'lng:pub:' + provider + ':' + 'a' * 32
    details = []
    def detail(key):
        details.append(key)
        return {'entry_id': key, 'entry_kind': 'publisher_group', 'recommendable': False,
                'genres': [], 'llm_features': []}
    def forbidden_similar(*args, **kwargs):
        raise AssertionError('already authenticated featureless group must not replay its raw source twice')
    monkeypatch.setattr(catalogue, 'work_detail', detail)
    monkeypatch.setattr(catalogue, 'similar_works', forbidden_similar)
    result = fetch(base, '/api/entries/' + quote(identifier, safe='') + '/similar')
    assert result == {'mode': 'insufficient_evidence', 'personalized': False, 'items': []}
    assert details == [identifier]


def test_similarity_shortcut_does_not_hide_absent_or_invalid_entries(site, monkeypatch):
    base, catalogue = site
    detail_calls = []
    def detail(identifier):
        detail_calls.append(identifier)
        return None
    def forbidden_similar(*args, **kwargs):
        raise AssertionError('no backend similarity call for an absent or invalid entry')
    monkeypatch.setattr(catalogue, 'work_detail', detail)
    monkeypatch.setattr(catalogue, 'similar_works', forbidden_similar)
    for path in ['/api/entries/lne%3Andl%3Amissing/similar', '/api/entries/lne%3Andl%3A..%2Fprivate/similar']:
        with pytest.raises(HTTPError) as error:
            fetch(base, path)
        assert error.value.code == 404
    assert detail_calls == ['lne:ndl:missing']


@pytest.mark.parametrize('marker', ['publisher_explicit_group', 'unverified', {'private': 'internal proof'}])
def test_series_title_origin_exports_only_the_supported_public_scope(marker):
    row = {'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
        'series_identity_basis': 'publisher_series_and_ndl_bibliography',
        'series_title_source': marker, 'title_origin': {'raw_path': 'internal-only'},
        'series_assertion_source_url': 'https://www.shueisha.co.jp/books/search/search.html?seriesid=52670'}
    result = public_work(row)
    assert ('series_title_source' in result) is (marker == 'publisher_explicit_group')
    assert 'title_origin' not in result


@pytest.mark.parametrize(('basis', 'marker', 'expected'), [
    ('publisher_work_group_and_ndl_bibliography', 'publisher_explicit_work_group', True),
    ('publisher_work_group_and_ndl_bibliography', 'publisher_explicit_group', False),
    ('publisher_series_and_ndl_bibliography', 'publisher_explicit_work_group', False),
    ('unknown', 'publisher_explicit_work_group', False),
    ({'private': 'proof'}, 'publisher_explicit_work_group', False),
    ('publisher_work_group_and_ndl_bibliography', {'private': 'proof'}, False),
])
def test_publisher_work_group_scope_cannot_be_exported_as_a_story_series(basis, marker, expected):
    result = public_work({'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
        'series_identity_basis': basis, 'series_title_source': marker,
        'title_origin': {'raw_path': 'PRIVATE-SENTINEL'}})
    assert ('series_title_source' in result) is expected
    if expected:
        assert result['story_series_identity_asserted'] is False
        assert result['original_work_identity_asserted'] is False
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


def test_mnovels_group_public_scope_keeps_unknowns_and_separates_reference_counts():
    row = {'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
        'series_identity_basis': 'publisher_work_group_and_ndl_bibliography',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'futaba_mnovels_grouping', 'light_novel_confirmed': False,
        'authors': [], 'source_frame_member_count': 21, 'verified_bibliographic_member_count': 3,
        'frame_membership_complete': True, 'exact_grouping_filter_complete': None,
        'global_membership_complete': None, 'grouping_source': {'raw_path': 'PRIVATE-SENTINEL'},
        'publisher_product_references': [{'title': '未対応の掲載書誌',
            'source_url': 'https://www.futabasha.co.jp/book/book1', 'raw_path': 'PRIVATE-SENTINEL'}]}
    result = public_work(row)
    for key in ('publisher_work_group_kind', 'source_frame_member_count',
                'verified_bibliographic_member_count', 'frame_membership_complete',
                'exact_grouping_filter_complete', 'global_membership_complete'):
        assert result[key] == row[key]
    assert result['light_novel_confirmed'] is False
    assert result['authors'] == []
    assert result['story_series_identity_asserted'] is False
    assert result['publisher_product_references'][0]['title'] == '未対応の掲載書誌'
    assert 'entry_id' not in result['publisher_product_references'][0]
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    parent = public_work({'entry_id': 'lne:ndl:R100000002-I2', 'entry_kind': 'edition',
                          'members': [row]})['members'][0]
    assert parent['publisher_work_group_kind'] == 'futaba_mnovels_grouping'
    assert parent['source_frame_member_count'] == 21
    assert parent['global_membership_complete'] is None
    assert 'PRIVATE-SENTINEL' not in json.dumps(parent)


@pytest.mark.parametrize('value', [True, -1, '2', 1.5, {'raw_path': 'PRIVATE-SENTINEL'}])
def test_mnovels_group_public_scope_rejects_noncounts_and_nonbooleans(value):
    result = public_work({'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
        'series_identity_basis': 'publisher_work_group_and_ndl_bibliography',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'futaba_mnovels_grouping',
        'source_frame_member_count': value, 'verified_bibliographic_member_count': value,
        'frame_membership_complete': value, 'exact_grouping_filter_complete': value,
        'global_membership_complete': value})
    assert 'source_frame_member_count' not in result
    assert 'verified_bibliographic_member_count' not in result
    for key in ('frame_membership_complete', 'exact_grouping_filter_complete', 'global_membership_complete'):
        assert (key in result) is isinstance(value, bool)
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


@pytest.mark.parametrize(('kind', 'basis', 'marker'), [
    ('edition', 'publisher_work_group_and_ndl_bibliography', 'publisher_explicit_work_group'),
    ('series', 'publisher_series_and_ndl_bibliography', 'publisher_explicit_work_group'),
    ('series', 'publisher_work_group_and_ndl_bibliography', 'publisher_explicit_group'),
])
def test_mnovels_group_scope_is_not_exported_for_a_different_identity(kind, basis, marker):
    result = public_work({'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': kind,
        'series_identity_basis': basis, 'series_title_source': marker,
        'publisher_work_group_kind': 'futaba_mnovels_grouping',
        'source_frame_member_count': 21, 'frame_membership_complete': True})
    assert 'publisher_work_group_kind' not in result
    assert 'source_frame_member_count' not in result
    assert 'frame_membership_complete' not in result


def test_publisher_source_group_http_keeps_source_references_separate_from_editions(site, monkeypatch):
    base, catalogue = site
    identifier = 'lng:pub:futaba:' + 'a' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'qid': None, 'title': '掲載グループ', 'original_language': None, 'authors': [],
        'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'futaba_mnovels_source_group',
        'admission_status': 'publisher_source_group_verified',
        'relation_resolution': 'publisher_source_group_verified', 'light_novel_confirmed': False,
        'source_frame_member_count': 1, 'verified_bibliographic_member_count': 0,
        'frame_membership_complete': True, 'exact_grouping_filter_complete': None,
        'global_membership_complete': None, 'members': [],
        'source_url': 'https://www.futabasha.co.jp/search?label_name=test',
        'publisher_product_references': [{'publisher_reference_id': 'futaba-product:book1',
            'title': '掲載書誌 完全題', 'source_url': 'https://www.futabasha.co.jp/book/book1?type=1',
            'label': 'Ｍノベルス', 'form': None, 'credits': [{'role': 'author', 'name': '著者'}],
            'media_references': [{'media_type_source_literal': 2, 'media_id_source_literal': 12,
                'source_url': 'https://www.futabasha.co.jp/book/book1?type=2',
                'raw_path': 'PRIVATE-SENTINEL'}], 'source_evidence': {'path': 'PRIVATE-SENTINEL'}}],
        'publisher_source_proof': {'path': 'PRIVATE-SENTINEL'}}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier and result['entry_kind'] == 'publisher_group'
    assert result['qid'] == '' and result['authors'] == [] and result['members'] == []
    assert result['series_identity_basis'] == 'publisher_source_group'
    assert result['relation_resolution'] == 'publisher_source_group_verified'
    assert result['admission_status'] == 'publisher_source_group_verified'
    assert result['publisher_work_group_kind'] == 'futaba_mnovels_source_group'
    assert result['light_novel_confirmed'] is False and result['global_membership_complete'] is None
    product = result['publisher_product_references'][0]
    assert product['title'] == '掲載書誌 完全題'
    assert product['credits'] == [{'role': 'author', 'name': '著者'}]
    assert product['form'] is None
    assert product['media_references'][0]['source_url'].endswith('?type=2')
    assert 'entry_id' not in product and 'read_status' not in product
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


def test_orange_source_group_http_preserves_explicit_credit_roles_without_edition_claims(site, monkeypatch):
    base, catalogue = site
    identifier = 'lng:pub:orange:' + 'b' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'title': '公式掲載グループ', 'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'orange_native_taxonomy_source_group',
        'light_novel_confirmed': False, 'authors': [], 'members': [],
        'source_frame_member_count': 1, 'verified_bibliographic_member_count': 0,
        'publisher_product_references': [{'publisher_reference_id': 'shueisha-orange-book:example-volume',
            'title': '掲載書名', 'source_url': 'https://orangebunko.shueisha.co.jp/book/example-volume',
            'label': None, 'form': None, 'media_references': None,
            'credits': [{'role': role, 'name': name} for role, name in
                [('author', '著者例'), ('cover_illustrator', '装画例'),
                 ('commentary_author', '解説例'), ('screenplay_author', '脚本例')]],
            'credit_literals': [{'role_literal': '装画', 'name': '装画例', 'source_selector': 'PRIVATE-SENTINEL'},
                {'role_literal': {'raw_path': 'PRIVATE-SENTINEL'}, 'name': '不正'},
                {'role_literal': None, 'name': '役割未表示例'}],
            'source_evidence': {'raw_path': 'PRIVATE-SENTINEL'}}]}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier and result['entry_kind'] == 'publisher_group'
    assert result['publisher_work_group_kind'] == 'orange_native_taxonomy_source_group'
    assert result['authors'] == [] and result['members'] == []
    assert result['light_novel_confirmed'] is False and result['story_series_identity_asserted'] is False
    product = result['publisher_product_references'][0]
    assert product['publisher_reference_id'] == 'shueisha-orange-book:example-volume'
    assert [c['role'] for c in product['credits']] == ['author', 'cover_illustrator', 'commentary_author', 'screenplay_author']
    assert product['credit_literals'] == [{'role_literal': '装画', 'name': '装画例'},
                                         {'role_literal': None, 'name': '役割未表示例'}]
    assert product['label'] is None and product['form'] is None and product['media_references'] == []
    assert not {'entry_id', 'isbn', 'read_status'} & product.keys()
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


def test_sbcr_group_http_keeps_unknown_roles_and_rights_explicit(site, monkeypatch):
    base, catalogue = site
    identifier = 'lng:pub:sbcr:' + 'f' * 32
    permissions = {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
        'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False}
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'title': '電子シリーズの掲載群', 'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group', 'publisher_work_group_kind': 'sbcr_ebook_source_group',
        'source_url': 'https://ebook.sbcr.jp/lightnovel/series/id/1',
        'light_novel_confirmed': False, 'authors': [], 'members': [],
        'robots_evidence': {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False,
                            'raw_path': 'PRIVATE-SENTINEL'},
        'usage_permissions': dict(permissions, raw_path='PRIVATE-SENTINEL'),
        'publisher_product_references': [{'publisher_reference_id': 'sbcr-product:1234567890123',
            'title': '掲載された完全題', 'source_url': 'https://ebook.sbcr.jp/product/1234567890123',
            'credits': [{'role': 'author', 'name': 'PRIVATE-SENTINEL'}],
            'label': '推定しない', 'form': '推定しない',
            'media_references': [{'source_url': 'https://example.com/PRIVATE-SENTINEL'}],
            'raw_path': 'PRIVATE-SENTINEL'}]}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier and result['entry_kind'] == 'publisher_group'
    assert result['publisher_work_group_kind'] == 'sbcr_ebook_source_group'
    assert result['authors'] == [] and result['members'] == [] and result['light_novel_confirmed'] is False
    assert result['usage_permissions'] == permissions
    assert result['robots_evidence'] == {'status': 'not_saved_in_existing_source_evidence', 'permission_inferred': False}
    product = result['publisher_product_references'][0]
    assert product['publisher_reference_id'] == 'sbcr-product:1234567890123'
    assert product['credits'] == [] and product['media_references'] == []
    assert product['label'] is None and product['form'] is None
    assert 'isbn' not in product and 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


@pytest.mark.parametrize(('provider', 'kind', 'source_url'), [
    ('hj', 'hj_native_index_source_group', 'https://firecross.jp/hjbunko/series/71'),
    ('arian', 'arian_native_index_source_group', 'https://arianrose.jp/novel/?series_id=1'),
    ('hjnovels', 'hj_native_index_source_group', 'https://firecross.jp/hjnovels/series/1'),
])
def test_native_index_group_http_keeps_unknown_membership_and_index_credits(
        site, monkeypatch, provider, kind, source_url):
    base, catalogue = site
    identifier = 'lng:pub:' + provider + ':' + 'a' * 32
    credits = [{'role_literal': '著者', 'name': '索引の表示名',
                'role_scope': 'publisher_index_card', 'raw_path': 'PRIVATE-SENTINEL'},
               {'role_literal': None, 'name': '役割未記載の表示名',
                'role_scope': 'publisher_index_card'},
               {'role_literal': '著者', 'name': 'PRIVATE-SENTINEL',
                'role_scope': 'unverified_inferred_role'}]
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'title': '索引で確認した掲載単位', 'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': kind, 'source_url': source_url,
        'authors': [], 'members': [], 'light_novel_confirmed': False,
        'source_frame_member_count': 0, 'frame_membership_complete': None,
        'native_index_card_complete': True, 'index_frame_pages_complete': True,
        'total_product_count': None, 'index_credit_literals': credits,
        'publisher_product_references': [], 'raw_path': 'PRIVATE-SENTINEL'}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier
    assert result['publisher_work_group_kind'] == kind
    assert result['native_index_card_complete'] is True
    assert result['index_frame_pages_complete'] is True
    assert result['frame_membership_complete'] is None
    assert result['total_product_count'] is None
    assert result['source_frame_member_count'] == 0
    assert result['authors'] == result['members'] == result['publisher_product_references'] == []
    assert result['index_credit_literals'] == [
        {'role_literal': '著者', 'name': '索引の表示名', 'role_scope': 'publisher_index_card'},
        {'role_literal': None, 'name': '役割未記載の表示名', 'role_scope': 'publisher_index_card'}]
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


def test_sbcr_index_http_preserves_literal_name_cautions_without_proxy_credits(site, monkeypatch):
    base, catalogue = site
    identifier = 'lng:pub:sbcrindex:' + 'a' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'title': '（シリーズ：掲載表示名）', 'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'sbcr_native_index_source_group',
        'source_url': 'https://ebook.sbcr.jp/lightnovel/series/id/1',
        'authors': [], 'members': [], 'light_novel_confirmed': False,
        'source_frame_member_count': 0, 'frame_membership_complete': None,
        'native_index_card_complete': True, 'index_frame_pages_complete': True,
        'total_product_count': None,
        'index_credit_literals': [{'role_literal': '著者', 'name': 'PRIVATE-SENTINEL',
                                  'role_scope': 'publisher_index_card'}],
        'index_name_cautions': ['wrapped_index_display_title', 'publication_type_unconfirmed',
                                'PRIVATE-SENTINEL', {'raw_path': 'PRIVATE-SENTINEL'}],
        'publisher_product_references': [], 'raw_path': 'PRIVATE-SENTINEL'}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier
    assert result['title'] == row['title']
    assert result['publisher_work_group_kind'] == 'sbcr_native_index_source_group'
    assert result['index_name_cautions'] == ['wrapped_index_display_title', 'publication_type_unconfirmed']
    assert result['index_credit_literals'] == result['authors'] == result['members'] == []
    assert result['publisher_product_references'] == []
    assert result['native_index_card_complete'] is True and result['light_novel_confirmed'] is False
    assert result['frame_membership_complete'] is None and result['total_product_count'] is None
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


def test_ecobalt_http_keeps_author_index_context_separate_from_book_authorship(site, monkeypatch):
    base, catalogue = site
    identifier = 'lng:pub:ecobalt:' + 'c' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_group', 'family': 'light_novel',
        'title': '掲載見出し', 'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'ecobalt_author_index_source_group',
        'authors': [], 'members': [], 'light_novel_confirmed': False,
        'heading_author_display': '表示名 / 表示名B',
        'role_context': {'literal': '著者50音別書籍リスト', 'scope': 'author_index_display_only',
                         'locator': 'PRIVATE-SENTINEL'},
        'publisher_product_references': [{'publisher_reference_id': 'shueisha-ebook:sc000001',
            'title': '電子書籍の掲載題', 'source_url': 'https://www.shueisha.co.jp/books/items/contents.html?jdcn=sc000001',
            'author_display': '表示名 / 表示名B', 'media_references': None,
            'credits': [{'role': 'unknown', 'name': '表示名 / 表示名B'},
                        {'role': 'author', 'name': '個別著者と推定しない'}],
            'credit_literals': [{'name': '表示名 / 表示名B', 'entry_role_literal': None,
                'role_context_literal': '著者50音別書籍リスト', 'role_scope': 'author_index_display_only',
                'source_selector': 'PRIVATE-SENTINEL'},
                {'name': 'PRIVATE-SENTINEL', 'entry_role_literal': '著者',
                 'role_context_literal': '著者50音別書籍リスト', 'role_scope': 'individual_book_author'}]}]}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['heading_author_display'] == '表示名 / 表示名B'
    assert result['role_context'] == {'literal': '著者50音別書籍リスト', 'scope': 'author_index_display_only'}
    assert result['authors'] == [] and result['members'] == [] and result['light_novel_confirmed'] is False
    product = result['publisher_product_references'][0]
    assert product['publisher_reference_id'] == 'shueisha-ebook:sc000001'
    assert product['author_display'] == '表示名 / 表示名B'
    assert product['credits'] == [{'role': 'unknown', 'name': '表示名 / 表示名B'}]
    assert product['credit_literals'] == [{'name': '表示名 / 表示名B', 'entry_role_literal': None,
        'role_context_literal': '著者50音別書籍リスト', 'role_scope': 'author_index_display_only'}]
    assert product['media_references'] == [] and product['label'] is None and product['form'] is None
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert catalogue.calls[-1]['liked_entry_ids'] == [identifier]


def test_ecobalt_standalone_reference_http_preserves_product_scope_and_unknown_roles(site, monkeypatch):
    base, catalogue = site
    identifier = 'lnr:pub:ecobalt:' + 'd' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_reference', 'family': 'light_novel',
        'title': '独立した電子書誌', 'reference_identity_basis': 'publisher_product_reference',
        'publisher_reference_kind': 'ecobalt_author_index_product_reference',
        'author_display': '表示名A / 表示名B',
        'role_context': {'literal': '著者50音別書籍リスト', 'scope': 'author_index_display_only',
                         'locator': 'PRIVATE-SENTINEL'},
        'publisher_product_reference': {'title': '独立した電子書誌',
            'publisher_reference_id': 'shueisha-ebook:1234567890123',
            'source_url': 'https://ebooks.shueisha.co.jp/detail.php?book=1234567890123',
            'author_display': '表示名A / 表示名B',
            'credits': [{'role': 'unknown', 'name': '表示名A / 表示名B'},
                        {'role': 'author', 'name': '推定しない'}],
            'source_evidence': {'raw_path': 'PRIVATE-SENTINEL'}},
        'source_url': 'https://ebooks.shueisha.co.jp/detail.php?book=1234567890123',
        'authors': [{'name': '推定しない'}], 'recommendable': True,
        'isbn': ['1234567890123'], 'light_novel_confirmed': True,
        'members': [{'title': 'PRIVATE-SENTINEL'}],
        'llm_features': [{'label': 'PRIVATE-SENTINEL'}]}
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: row if key == identifier else None)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_kind'] == 'publisher_reference' and result['entry_id'] == identifier
    assert result['reference_identity_basis'] == 'publisher_product_reference'
    assert result['publisher_reference_kind'] == 'ecobalt_author_index_product_reference'
    assert result['author_display'] == '表示名A / 表示名B'
    assert result['role_context'] == {'literal': '著者50音別書籍リスト', 'scope': 'author_index_display_only'}
    assert result['authors'] == [] and result['members'] == [] and result['isbn'] == []
    assert result['light_novel_confirmed'] is False and result['recommendable'] is False
    assert result['story_series_identity_asserted'] is False
    assert result['original_work_identity_asserted'] is False
    assert result['paper_electronic_same_edition_asserted'] is False
    product = result['publisher_product_reference']
    assert product['publisher_reference_id'] == 'shueisha-ebook:1234567890123'
    assert product['credits'] == [{'role': 'unknown', 'name': '表示名A / 表示名B'}]
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    fetch(base, '/api/entries?family=light_novel&entry_kind=publisher_reference&q=Test')
    assert catalogue.calls[-1]['entry_kind'] == 'publisher_reference'


def test_ecobalt_standalone_reference_similar_authenticates_once_and_skips_global_scan(site, monkeypatch):
    base, catalogue = site
    identifier = 'lnr:pub:ecobalt:' + 'e' * 32
    details = []
    def detail(key):
        details.append(key)
        return {'entry_id': key, 'entry_kind': 'publisher_reference', 'recommendable': False}
    def forbidden_similar(*args, **kwargs):
        raise AssertionError('unclassified product reference cannot trigger global recommendation scans')
    monkeypatch.setattr(catalogue, 'work_detail', detail)
    monkeypatch.setattr(catalogue, 'similar_works', forbidden_similar)
    assert fetch(base, '/api/entries/' + quote(identifier, safe='') + '/similar') == {
        'mode': 'insufficient_evidence', 'personalized': False, 'items': []}
    assert details == [identifier]


def test_futaba_standalone_reference_keeps_listing_scope_and_unknown_status(site, monkeypatch):
    base, catalogue = site
    identifier = 'lnr:pub:futaba:' + 'f' * 32
    row = {'entry_id': identifier, 'entry_kind': 'publisher_reference', 'family': 'light_novel',
        'title': '双葉社の掲載書誌', 'reference_identity_basis': 'publisher_product_reference',
        'publisher_reference_kind': 'futaba_mnovels_product_reference',
        'publisher_provider': 'futaba-mnovels', 'source_url': 'https://www.futabasha.co.jp/book/f',
        'authors': [{'name': '推定しない'}], 'isbn': ['9780000000000'], 'light_novel_confirmed': False,
        'read_status': 'unknown', 'recommendable': True,
        'publisher_product_reference': {'publisher_reference_id': 'futaba-product:book-f',
            'title': '双葉社の掲載書誌', 'source_url': 'https://www.futabasha.co.jp/book/f',
            'label': 'Ｍノベルス', 'form': '電子',
            'credits': [{'role': 'author', 'name': '推定しない'}],
            'credit_literals': [{'role_literal': '著者', 'name': '表示名'}, {'role_literal': None, 'name': '役割未記載'}],
            'media_references': [{'source_url': 'https://www.futabasha.co.jp/book/f?type=2'}]},
        'publication_label': 'Ｍノベルス',
        'usage_permissions': {'bibliographic_reference_only': True, 'commercial_reuse': 'unknown',
            'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False},
        'source_attributions': [{'source': 'publisher', 'provider': 'futaba-mnovels'}],
        'members': [{'entry_id': 'lne:ndl:PRIVATE', 'title': '推定しない'}]}
    details = []
    def detail(key):
        details.append(key)
        return row if key == identifier else None
    monkeypatch.setattr(catalogue, 'work_detail', detail)
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['entry_id'] == identifier and result['work_id'] == identifier
    assert result['publisher_reference_kind'] == 'futaba_mnovels_product_reference'
    assert result['publisher_provider'] == 'futaba-mnovels'
    assert result['light_novel_confirmed'] is None and result['recommendable'] is False
    assert result['authors'] == [] and result['isbn'] == ['9780000000000'] and result['members'] == []
    assert result['publication_label'] == 'Ｍノベルス'
    assert result['usage_permissions']['bibliographic_reference_only'] is True
    product = result['publisher_product_reference']
    assert product['publisher_reference_id'] == 'futaba-product:book-f'
    assert product['credit_literals'] == [{'role_literal': '著者', 'name': '表示名'},
                                         {'role_literal': None, 'name': '役割未記載'}]
    assert product['credits'] == [{'role': 'author', 'name': '推定しない'}]
    assert 'source_attributions' not in result and 'PRIVATE' not in json.dumps(result)
    assert fetch(base, '/api/entries/' + quote(identifier, safe='') + '/similar') == {
        'mode': 'insufficient_evidence', 'personalized': False, 'items': []}
    assert details == [identifier, identifier]


def test_source_group_product_reference_boundary_rejects_nested_values_and_unsafe_links():
    result = public_work({'entry_id': 'lng:pub:futaba:' + 'a' * 32, 'entry_kind': 'publisher_group',
        'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'futaba_mnovels_source_group',
        'publisher_product_references': [{'publisher_reference_id': {'raw_path': 'PRIVATE-SENTINEL'},
            'title': '書誌', 'source_url': 'javascript:alert(1)', 'label': {'path': 'PRIVATE-SENTINEL'},
            'form': None, 'credits': [{'role': 'author', 'name': {'path': 'PRIVATE-SENTINEL'}}],
            'media_references': [{'media_type_source_literal': True, 'media_id_source_literal': -1,
                'source_url': 'https://www.futabasha.co.jp/book/book1?type=2'},
                {'source_url': 'file:///PRIVATE-SENTINEL'}]},
            {'title': {'path': 'PRIVATE-SENTINEL'}}]})
    product = result['publisher_product_references'][0]
    assert product['source_url'] == '' and product['label'] is None
    assert product['credits'] == []
    assert product['media_references'] == [{'source_url': 'https://www.futabasha.co.jp/book/book1?type=2'}]
    assert 'publisher_reference_id' not in product
    assert len(result['publisher_product_references']) == 1
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    wrong_kind = public_work({'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
        'series_identity_basis': 'publisher_source_group',
        'series_title_source': 'publisher_explicit_work_group',
        'publisher_work_group_kind': 'futaba_mnovels_source_group',
        'publisher_product_references': [{'title': 'PRIVATE-SENTINEL'}]})
    assert 'publisher_product_references' not in wrong_kind


@pytest.mark.parametrize('identifier', [
    'lng:pub:unknown:' + 'a' * 32, 'lng:pub:futaba:' + 'A' * 32,
    'lng:pub:futaba:' + 'a' * 31, 'lng:pub:futaba:' + 'a' * 32 + '/other',
])
def test_source_group_typed_id_rejects_malformed_or_unknown_provider(site, identifier):
    base, _ = site
    with pytest.raises(HTTPError) as error:
        fetch(base, '/api/recommendations', {'liked_entry_ids': [identifier]})
    assert error.value.code == 400


@pytest.mark.parametrize('identifier', [
    'lnr:pub:unknown:' + 'a' * 32, 'lnr:pub:futaba:' + 'A' * 32,
    'lnr:pub:futaba:' + 'a' * 31, 'lnr:pub:futaba:' + 'a' * 32 + '/other',
])
def test_publisher_reference_typed_id_rejects_malformed_or_unknown_provider(site, identifier):
    base, _ = site
    with pytest.raises(HTTPError) as error:
        fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert error.value.code == 404


def test_publisher_work_group_http_preserves_scope_members_and_separate_statistics(site, monkeypatch):
    base, catalogue = site
    identifier = 'lns:ndl:R100000002-I032593374'
    monkeypatch.setattr(catalogue, 'work_detail', lambda key: {
        **catalogue.item(key), 'title': '出版社の作品グループ',
        'series_identity_basis': 'publisher_work_group_and_ndl_bibliography',
        'series_title_source': 'publisher_explicit_work_group',
        'admission_status': 'publisher_work_group_verified',
        'relation_resolution': 'publisher_work_group_and_anchor_verified',
        'series_assertion_source_url': 'https://earthstar-novel.com/book/detail/esn017/',
        'publisher_series_proof': {'raw_path': 'PRIVATE-SENTINEL'}})
    result = fetch(base, '/api/entries/' + quote(identifier, safe=''))
    assert result['admission_status'] == 'publisher_work_group_verified'
    assert result['relation_resolution'] == 'publisher_work_group_and_anchor_verified'
    assert result['series_title_source'] == 'publisher_explicit_work_group'
    assert result['story_series_identity_asserted'] is False
    assert len(result['members']) == 1
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)
    monkeypatch.setattr(catalogue, 'catalogue_stats', lambda: {
        'light_novel_series': 1000, 'light_novel_publisher_work_groups': 3})
    stats = fetch(base, '/api/stats')
    assert stats['light_novel_series'] == 1000
    assert stats['light_novel_publisher_work_groups'] == 3


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


def test_native_match_with_unknown_relevance_explains_zero_contribution(site, monkeypatch):
    base, catalogue = site
    candidate = {**catalogue.item(), 'score': 0,
        'reasons': [{'kind': 'series_content_feature', 'label': 'Fantasy', 'relevance_unknown': True}]}
    monkeypatch.setattr(catalogue, 'recommend_preferences', lambda **kwargs: {'items': [candidate]})
    result = fetch(base, '/api/recommendations', {'liked_entry_ids': ['lns:wd:Q5357270'], 'family': 'light_novel'})
    item = result['items'][0]
    assert item['score'] == 0
    assert '関連度は未確認' in item['reasons'][0]['detail']
    assert '加算していません' in item['reasons'][0]['detail']
    assert 'relevance_unknown' not in item['reasons'][0]


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


@pytest.mark.parametrize('marker', ['publisher_explicit_work_group', {'private': 'PRIVATE-SENTINEL'}])
def test_parent_link_keeps_work_group_scope_without_exporting_internal_proof(marker):
    result = public_work({'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'admission_status': 'publisher_work_group_membership_verified',
        'relation_resolution': 'publisher_work_group_membership_verified',
        'members': [{'entry_id': 'lns:ndl:R100000002-I1', 'entry_kind': 'series',
            'title': '作品グループ', 'series_identity_basis': 'publisher_work_group_and_ndl_bibliography',
            'series_title_source': marker, 'publisher_series_proof': {'raw_path': 'PRIVATE-SENTINEL'}}]})
    parent = result['members'][0]
    assert parent['series_identity_basis'] == 'publisher_work_group_and_ndl_bibliography'
    assert parent['story_series_identity_asserted'] is False
    assert ('series_title_source' in parent) is (marker == 'publisher_explicit_work_group')
    assert result['relation_resolution'] == 'publisher_work_group_membership_verified'
    assert result['admission_status'] == 'publisher_work_group_membership_verified'
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


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
