import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


def test_native_feature_shape_displays_confidence_and_quoted_evidence():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = public_work({'qid': 'Q2', 'llm_features': [{
        'label_id': 'genre.mystery', 'axis': 'genre', 'raw_confidence': .82,
        'relevance': None, 'evidence': [{'evidence_id': 'internal-only',
            'locator': 'claims.P136', 'quote': 'detective fiction'}]}]})
    feature = row['llm_features'][0]
    assert feature['confidence'] == .82 and feature['relevance'] is None
    assert feature['label'] == 'Mystery' and feature['evidence'] == 'detective fiction'
    assert 'internal-only' not in json.dumps(row)


def test_axis_summary_exposes_counts_without_internal_review_text():
    from recommend_book.recommendation.scale_site_v1 import public_work
    result = public_work({'qid': 'Q2', 'classification_axes': [
        {'axis': 'theme', 'adopted_count': 2, 'held_count': 1, 'unknown': False,
         'review_path': 'PRIVATE-SENTINEL'},
        {'axis': 'style', 'adopted_count': 0, 'held_count': 0, 'unknown': True},
        {'axis': 'PRIVATE-SENTINEL', 'adopted_count': 99}]})
    assert result['classification_axes'] == [
        {'axis': 'theme', 'adopted_count': 2, 'held_count': 1, 'unknown': False},
        {'axis': 'style', 'adopted_count': 0, 'held_count': 0, 'unknown': True}]
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


def test_summary_only_content_is_distinct_from_a_verbatim_quote():
    from recommend_book.recommendation.scale_site_v1 import public_work
    result = public_work({'qid': 'Q2', 'content_sources': [
        {'evidence_ids': ['summary'], 'display_allowed': True, 'quote_allowed': False},
        {'evidence_ids': ['held'], 'display_allowed': False, 'quote_allowed': False}],
        'llm_features': [{'label_id': 'theme.growth', 'evidence': [
            {'evidence_id': 'summary', 'quote': None, 'summary': 'Reviewed model summary'},
            {'evidence_id': 'summary', 'quote': 'PRIVATE-SENTINEL'},
            {'evidence_id': 'held', 'summary': 'PRIVATE-SENTINEL'}]}]})
    assert result['llm_features'][0]['evidence'] == ''
    assert result['llm_features'][0]['evidence_summary'] == 'Reviewed model summary'
    assert 'PRIVATE-SENTINEL' not in json.dumps(result)


def test_content_attribution_is_public_and_nonpermitted_quotes_stay_private():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = public_work({'qid': 'Q2', 'content_sources': [
        {'evidence_ids': ['allowed'], 'source_url': 'https://en.wikipedia.org/w/index.php?oldid=123',
         'license': 'CC-BY-SA-4.0', 'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
         'attribution': {'credit': 'Wikipedia contributors: Example novel',
                         'permalink': 'https://en.wikipedia.org/w/index.php?oldid=123',
                         'history_url': 'https://en.wikipedia.org/w/index.php?title=Example&action=history',
                         'private_path': 'PRIVATE-SENTINEL'}, 'revision': 123, 'locator': 'Plot',
         'output_license': 'CC-BY-SA-4.0', 'output_license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
         'display_allowed': True, 'quote_allowed': True, 'changed_from_source': 'Excerpted and classified',
         'raw_path': 'PRIVATE-SENTINEL'},
        {'evidence_ids': ['private'], 'source_url': 'PRIVATE-SENTINEL',
         'display_allowed': False, 'quote_allowed': False},
        {'evidence_ids': ['no-quote'], 'source_url': 'javascript:alert(1)', 'license_url': 'http://[',
         'display_allowed': True, 'quote_allowed': False}],
        'llm_features': [{'label_id': 'theme.growth', 'evidence': [
            {'evidence_id': 'allowed', 'quote': 'Publicly quotable plot excerpt'},
            {'evidence_id': 'private', 'quote': 'PRIVATE-SENTINEL'},
            {'evidence_id': 'no-quote', 'quote': 'PRIVATE-SENTINEL'}]}]})
    assert 'PRIVATE-SENTINEL' not in json.dumps(row)
    assert row['llm_features'][0]['evidence'] == 'Publicly quotable plot excerpt'
    assert row['content_sources'][0]['revision'] == '123'
    assert row['content_sources'][0]['attribution'] == 'Wikipedia contributors: Example novel'
    assert row['content_sources'][0]['history_url'].endswith('&action=history')
    assert row['content_sources'][0]['permalink'].endswith('oldid=123')
    assert row['content_sources'][0]['output_license'] == 'CC-BY-SA-4.0'
    assert row['content_sources'][1]['source_url'] == row['content_sources'][1]['license_url'] == ''

from recommend_book.recommendation.scale_site_v1 import create_server


class Catalogue:
    def __init__(self):
        self.status = 'unknown'
        self.calls = []

    def catalogue_stats(self):
        return {'works': 10001, 'feature_bearing_works': 5100, 'llm_works': 0, 'raw_classified_works': 100,
                'eligible_classified_works': 95,
                'languages': [{'value': '日本語', 'count': 10}],
                'genres': [{'qid': 'Q1', 'label': '空想', 'count': 5}],
                'private_history': 'PRIVATE-SENTINEL'}

    def catalogue_facets(self):
        return {'languages': [{'value': 'ja', 'count': 10, 'raw_path': 'PRIVATE-SENTINEL'}],
                'genres': [{'qid': 'Q1', 'label': '空想', 'count': 5,
                            'raw_path': 'PRIVATE-SENTINEL'}],
                'private_history': 'PRIVATE-SENTINEL'}

    def item(self):
        return {'qid': 'Q2', 'work_id': 'wd:Q2', 'title': '<img src=x onerror=alert(1)>',
                'aliases': ['別題'], 'authors': [{'qid': 'Q3', 'name': '著者'}],
                'genres': [{'qid': 'Q1', 'label': '空想'}], 'read_status': self.status,
                'source_url': 'https://www.wikidata.org/wiki/Q2',
                'private_history': 'PRIVATE-SENTINEL',
                'provenance': {'raw_path': 'PRIVATE-SENTINEL', 'retrieved_at': '2026-10-20'},
                'llm_features': [{'label': '幻想', 'relevance': .5, 'confidence': .6,
                                  'private_path': 'PRIVATE-SENTINEL'}]}

    def search_catalogue(self, **kwargs):
        self.calls.append(kwargs)
        return {'items': [self.item()], 'total': 10001, 'page': kwargs['page'],
                'page_size': kwargs['page_size'], 'pages': 334, 'filters': {}}

    def work_detail(self, qid):
        return self.item() if qid == 'Q2' else None

    def similar_works(self, qid, **kwargs):
        return {'mode': 'source_similarity', 'personalized': False,
                'items': [dict(self.item(), score=.6, reasons=[{
                    'kind': 'genre', 'shared': ['空想'], 'weight': .6,
                    'raw_path': 'PRIVATE-SENTINEL'}])]}

    def set_read_status(self, qid, status):
        if qid != 'Q2':
            return None
        self.status = status
        return self.item()


@pytest.fixture
def site():
    catalogue = Catalogue()
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', catalogue
    server.shutdown()
    server.server_close()
    thread.join()


def get(url):
    with urlopen(url) as response:
        return response.read().decode()


def test_paginated_search_parameters_and_public_details(site):
    base, catalogue = site
    data = json.loads(get(base + '/api/works?q=%E5%88%A5%E9%A1%8C&page=2&genre=Q1&language=ja&year_from=1900&year_to=2000&exclude_read=1'))
    assert data['total'] == 10001 and len(data['items']) == 1
    assert catalogue.calls[-1] == dict(query='別題', page=2, page_size=30, genre='Q1',
        language='ja', year_from=1900, year_to=2000, read_filter='exclude_read')
    assert data['items'][0]['read_status'] == 'unknown'
    for path in ['/api/stats', '/api/works/Q2', '/api/works/Q2/similar']:
        text = get(base + path)
        assert 'PRIVATE-SENTINEL' not in text
    detail = json.loads(get(base + '/api/works/Q2'))
    assert detail['provenance'] == {'retrieved_at': '2026-10-20'}
    assert detail['llm_features'][0]['confidence'] == .6
    stats = json.loads(get(base + '/api/stats'))
    assert stats['raw_classified_works'] == 100 and stats['llm_works'] == 0
    assert stats['eligible_classified_works'] == 95


def test_filter_choices_remain_available_while_native_statistics_are_waiting(site, monkeypatch):
    base, catalogue = site
    entered, release = threading.Event(), threading.Event()
    original_stats = catalogue.catalogue_stats
    def waiting_stats():
        entered.set()
        if not release.wait(10):
            raise RuntimeError('test did not release statistics')
        return original_stats()
    monkeypatch.setattr(catalogue, 'catalogue_stats', waiting_stats)
    outcomes = []
    def request_stats():
        with urlopen(base + '/api/stats', timeout=15) as response:
            outcomes.append(json.load(response))
    worker = threading.Thread(target=request_stats)
    worker.start()
    try:
        assert entered.wait(5)
        with urlopen(base + '/api/facets', timeout=3) as response:
            facets = json.load(response)
        assert not outcomes
        assert facets == {'languages': [{'value': 'ja', 'count': 10}],
                          'genres': [{'qid': 'Q1', 'label': '空想', 'count': 5}]}
        assert 'PRIVATE-SENTINEL' not in json.dumps(facets)
    finally:
        release.set()
        worker.join(15)
    assert not worker.is_alive()
    assert outcomes[0]['eligible_classified_works'] == 95


def test_status_update_reversible_and_unknown_stays_distinct(site):
    base, _ = site
    for status in ['read', 'unread', 'reading', 'unknown']:
        request = Request(base + '/api/works/Q2/read-status',
            data=json.dumps({'status': status}).encode(),
            headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1', 'Origin': base})
        with urlopen(request) as response:
            assert json.load(response)['read_status'] == status
        assert json.loads(get(base + '/api/works/Q2'))['read_status'] == status


def test_static_routes_security_and_browser_safe_rendering(site):
    base, _ = site
    html = get(base + '/')
    script = get(base + '/app.js')
    assert 'innerHTML' not in script and 'textContent' in script
    assert '資料記載' in script and '校正' in script
    assert 'page-prev' in html and 'page-next' in html
    assert '<img src=x' not in html
    for path in ['/../pyproject.toml', '/%2e%2e/pyproject.toml', '/api/works/../../private', '/private']:
        with pytest.raises(HTTPError) as error:
            get(base + path)
        assert error.value.code == 404
    request = Request(base + '/api/works/Q2/read-status', data=b'{"status":"read"}',
                      headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1',
                               'Origin': 'https://evil.example'})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/', headers={'Host': 'evil.example'}))
    assert error.value.code == 403


def test_bad_filters_and_status_return_400(site):
    base, _ = site
    for path in ['/api/works?page=-1', '/api/works?page_size=10000', '/api/works?year_from=nope']:
        with pytest.raises(HTTPError) as error:
            get(base + path)
        assert error.value.code == 400
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base + '/api/works/Q2/read-status', data=b'{"status":"whatever"}',
            headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1'}))
    assert error.value.code == 400


def test_real_sqlite_catalogue_http_pagination_and_persistent_status(tmp_path):
    """Exercise the actual database, proof-backed ingest, HTTP filters and reopen."""
    import hashlib
    from datetime import datetime, timezone
    from urllib.parse import urlencode
    from recommend_book.models import SourceRecord, Title
    from recommend_book.scale_catalogue_v1 import ScaleCatalogue

    def save(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    now = datetime.now(timezone.utc).isoformat()
    policy = tmp_path / 'license.html'
    policy.write_text('Wikidata structured data CC0', encoding='utf-8')
    receipt = tmp_path / 'conditions.json'
    sha = save(receipt, {'version': 'scale-source-conditions-v1', 'source': 'wikidata',
        'policy_id': 'wikidata-cc0', 'metadataonly_scope': True, 'retrieved_at': now,
        'policy_snapshot': {'path': str(policy), 'sha256': hashlib.sha256(policy.read_bytes()).hexdigest()},
        'allowed_domains': ['www.wikidata.org', 'query.wikidata.org'],
        'usages': dict.fromkeys(['acquire', 'local_storage', 'display', 'external_llm',
                                'redistribution', 'commercial'], 'allowed')})
    database = tmp_path / 'catalogue.sqlite'
    catalogue = ScaleCatalogue(database, source_conditions_path=receipt, source_conditions_sha256=sha)
    def claim(value):
        return {'rank': 'normal', 'mainsnak': {'datavalue': {'value': value}}}
    records = []
    for index in range(35):
        qid = f'Q{1000 + index}'
        title = f'作品{index:02}'
        entity = {'id': qid, 'labels': {'ja': {'value': title}},
            'aliases': {'ja': [{'value': '特別な別題'}] if index == 0 else []},
            'claims': {'P31': [claim({'id': 'Q8261'})], 'P136': [claim({'id': 'Q200'})],
                       'P50': [claim({'id': 'Q300'})], 'P407': [claim({'id': 'Q5287'})],
                       'P577': [claim({'time': '+2001-01-01T00:00:00Z', 'precision': 11})]}}
        raw = tmp_path / f'{qid}.json'
        digest = save(raw, {'entities': {qid: entity,
            'Q200': {'id': 'Q200', 'labels': {'ja': {'value': '幻想小説'}}},
            'Q300': {'id': 'Q300', 'labels': {'ja': {'value': 'テスト著者'}}}}})
        records.append(SourceRecord(source='wikidata', source_id=qid, provider_id='wikidata',
            record_kind='work', source_url=f'https://www.wikidata.org/wiki/{qid}', retrieved_at=now,
            titles=[Title(value=title, language='ja')], raw_path=str(raw), raw_sha256=digest,
            rights_policy_id='wikidata-cc0'))
    assert catalogue.ingest(records)['inserted'] == 35
    server = create_server(database, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        first = json.loads(get(base + '/api/works'))
        second = json.loads(get(base + '/api/works?page=2'))
        assert (len(first['items']), len(second['items']), first['total']) == (30, 5, 35)
        assert not ({v['qid'] for v in first['items']} & {v['qid'] for v in second['items']})
        for query, expected in [('特別な別題', 1), ('テスト著者', 35), ('作品00', 1)]:
            result = json.loads(get(base + '/api/works?' + urlencode({'q': query})))
            assert result['total'] == expected
        filtered = json.loads(get(base + '/api/works?genre=Q200&language=ja&year_from=2000&year_to=2002'))
        assert filtered['total'] == 35
        detail = get(base + '/api/works/Q1000')
        assert str(tmp_path) not in detail and 'raw_path' not in detail and 'source_record_json' not in detail
        similar = json.loads(get(base + '/api/works/Q1000/similar'))
        assert len(similar['items']) == 8
        assert similar['items'][0]['reasons'][0]['shared'][0]['label'] == '幻想小説'
        request = Request(base + '/api/works/Q1000/read-status', data=b'{"status":"read"}',
            headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1'})
        with urlopen(request) as response:
            assert json.load(response)['read_status'] == 'read'
        assert json.loads(get(base + '/api/works?exclude_read=1'))['total'] == 34
        assert ScaleCatalogue(database).work_detail('Q1000')['read_status'] == 'read'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('version', ['v1', 'v2'])
def test_wikipedia_membership_credit_is_public_bibliography_not_content_classification(version):
    from recommend_book.recommendation.scale_site_v1 import public_work
    credit = {'credit': 'Wikipedia contributors: Example novel',
        'source_url': 'https://ja.wikipedia.org/w/index.php?oldid=17',
        'history_url': 'https://ja.wikipedia.org/w/index.php?title=Example&action=history',
        'license': 'CC-BY-SA-4.0', 'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
        'changes': 'Bibliographic fields extracted', 'raw_path': 'PRIVATE_ARTICLE_PATH'}
    row = public_work({'entry_id': 'lne:ndl:R100000002-I1', 'entry_kind': 'edition',
        'relation_resolution': 'edition_series_membership_verified', 'content_sources': [],
        'series_membership_evidence': {'rule_version': 'ndl-edition-wikipedia-novel-credit-membership-' + version,
            'article_attribution': credit, 'manifest_path': 'PRIVATE_MANIFEST_PATH'}})
    assert row['membership_source']['attribution'] == credit['credit']
    assert row['membership_source']['source_url'] == credit['source_url']
    assert row['membership_source']['history_url'] == credit['history_url']
    assert row['membership_source']['license'] == 'CC-BY-SA-4.0'
    assert row['content_sources'] == [] and not row.get('llm_features')
    assert 'PRIVATE_' not in json.dumps(row)


@pytest.mark.parametrize('relation', ['original_work_and_story_series_unresolved', None])
def test_unverified_membership_does_not_publish_article_credit(relation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = public_work({'relation_resolution': relation, 'series_membership_evidence': {
        'rule_version': 'ndl-edition-wikipedia-novel-credit-membership-v1',
        'article_attribution': {'credit': 'UNVERIFIED', 'source_url': 'javascript:alert(1)'}}})
    assert 'membership_source' not in row


def orange_listing_association():
    return {'rule_version': 'orange-explicit-child-ndl-bibliographic-reference-association-v1',
        'scope': 'bibliographic_reference_association', 'group_entry_id': 'lng:pub:orange:' + 'a' * 32,
        'source_group_title': '掲載グループ', 'official_title': '完全な子書名',
        'official_parent_url': 'https://orangebunko.shueisha.co.jp/book/series/example',
        'official_child_url': 'https://orangebunko.shueisha.co.jp/book/opaque-reference',
        'official_credit_literals': [{'role_literal': '著者', 'name': '著者名', 'raw_path': 'PRIVATE'}],
        'edition_identity_asserted': False, 'story_series_identity_asserted': False,
        'original_work_identity_asserted': False, 'light_novel_identity_asserted': False,
        'manifest_path': 'PRIVATE_MANIFEST', 'publisher_source_evidence': [{'raw_path': 'PRIVATE_RAW'}]}


def test_orange_listing_public_fields_are_separate_from_identity_and_content():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = public_work({'publisher_listing_associations': [orange_listing_association()]})
    association, = row['publisher_listing_associations']
    assert association['title'] == '完全な子書名' and association['group_title'] == '掲載グループ'
    assert association['credit_literals'] == [{'role_literal': '著者', 'name': '著者名'}]
    assert association['scope'] == 'bibliographic_reference_association'
    assert association['edition_identity_asserted'] is False
    assert not row.get('members') and not row.get('content_sources')
    assert 'PRIVATE' not in json.dumps(row)


@pytest.mark.parametrize('mutation', ['version', 'identity', 'url', 'group'])
def test_unverified_orange_listing_claims_are_not_published(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    value = orange_listing_association()
    if mutation == 'version': value['rule_version'] = 'unknown-rule'
    elif mutation == 'identity': value['edition_identity_asserted'] = True
    elif mutation == 'url': value['official_child_url'] = 'javascript:alert(1)'
    elif mutation == 'group': value['group_entry_id'] = 'PRIVATE_PATH'
    row = public_work({'publisher_listing_associations': [value]})
    assert not row.get('publisher_listing_associations')


def kadokawa_electronic_listing():
    return {'entry_id': 'lnr:pub:kadokawa:' + 'a' * 32, 'entry_kind': 'publisher_reference',
        'title': '公式の完全題', 'reference_identity_basis': 'publisher_product_reference',
        'publisher_reference_kind': 'kadokawa_product_reference',
        'reference_scope': 'private_bibliographic_discovery',
        'source_url': 'https://www.kadokawa.co.jp/product/322401000001/', 'read_status': 'reading',
        'publication_label': '電撃文庫', 'isbn': [],
        'publisher_product_reference': {'publisher_reference_id': 'kadokawa-product:322401000001',
            'title': '公式の完全題', 'source_url': 'https://www.kadokawa.co.jp/product/322401000001/',
            'label': '電撃文庫', 'form': '電子専売', 'credits': [],
            'credit_literals': [{'role_literal': '著者', 'name': '公式表示名', 'raw_path': 'PRIVATE'}],
            'media_references': [], 'evidence': {'raw_path': 'PRIVATE'}},
        'usage_permissions': {'purpose': 'private_bibliographic_discovery',
            'public_display': 'unknown', 'commercial_reuse': 'unknown',
            'prose_ai_permission': False, 'images_permission': False, 'raw_redistribution_permission': False},
        'authors': [{'name': '推定しない'}], 'members': [{'title': '推定しない'}],
        'light_novel_confirmed': True, 'recommendable': True, 'edition_bibliography_confirmed': True,
        'story_series_identity_asserted': True, 'original_work_identity_asserted': True,
        'edition_identity_asserted': True, 'light_novel_identity_asserted': True,
        'source_reference_assertions': [{'manifest_path': 'PRIVATE'}], 'llm_features': [{'label': '推定しない'}]}


def test_kadokawa_electronic_listing_is_a_safe_reference_without_identity_or_isbn_inference():
    from recommend_book.recommendation.scale_site_v1 import _ENTRY_ID, public_work
    import re
    row = kadokawa_electronic_listing()
    assert re.fullmatch(_ENTRY_ID, row['entry_id'])
    public = public_work(row)
    assert public['entry_id'] == public['work_id'] == row['entry_id']
    assert public['publisher_reference_kind'] == 'kadokawa_product_reference'
    assert public['reference_identity_basis'] == 'publisher_product_reference'
    assert public['reference_scope'] == 'private_bibliographic_discovery'
    assert public['entry_kind'] == 'publisher_reference' and public['read_status'] == 'reading'
    assert public['authors'] == public['members'] == public['isbn'] == []
    assert public['light_novel_confirmed'] is False and public['recommendable'] is False
    for flag in ('edition_identity_asserted', 'story_series_identity_asserted',
                 'original_work_identity_asserted', 'light_novel_identity_asserted'):
        assert public[flag] is False
    assert not public.get('edition_bibliography_confirmed') and not public.get('llm_features')
    product = public['publisher_product_reference']
    assert product['title'] == row['title'] and product['form'] == '電子専売'
    assert product['label'] == public['publication_label'] == '電撃文庫'
    assert product['publisher_reference_id'] == 'kadokawa-product:322401000001'
    assert product['credit_literals'] == [{'role_literal': '著者', 'name': '公式表示名'}]
    assert public['usage_permissions']['public_display'] == 'unknown'
    assert public['usage_permissions']['commercial_reuse'] == 'unknown'
    assert public['usage_permissions']['prose_ai_permission'] is False
    assert 'PRIVATE' not in json.dumps(public, ensure_ascii=False)


@pytest.mark.parametrize('mutation', ['bad_id', 'wrong_kind', 'wrong_basis', 'wrong_scope'])
def test_kadokawa_reference_requires_its_typed_identity_and_reference_scope(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_electronic_listing()
    if mutation == 'bad_id': row['entry_id'] = 'lnr:pub:kadokawa:' + 'A' * 32
    elif mutation == 'wrong_kind': row['publisher_reference_kind'] = 'futaba_mnovels_product_reference'
    elif mutation == 'wrong_basis': row['reference_identity_basis'] = 'publisher_source_group'
    else: row['reference_scope'] = 'public_display_allowed'
    public = public_work(row)
    assert 'publisher_product_reference' not in public
    assert 'publisher_reference_kind' not in public


def test_kadokawa_reference_drops_nested_proof_and_unknown_permissions_never_become_allowed():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_electronic_listing()
    row['isbn'] = ['9780000000000']  # The electronic listing contract has no ISBN.
    row['publisher_product_reference'].update(source_url='javascript:alert(1)',
        credits=[{'role': 'author', 'name': {'raw_path': 'PRIVATE'}}],
        credit_literals=[{'role_literal': {'path': 'PRIVATE'}, 'name': '表示名'}])
    row['usage_permissions']['prose_ai_permission'] = 'allowed'
    public = public_work(row)
    assert public['isbn'] == []
    assert public['publisher_product_reference']['source_url'] == ''
    assert public['publisher_product_reference']['credit_literals'] == []
    assert public.get('usage_permissions', {}).get('prose_ai_permission') != 'allowed'
    assert 'PRIVATE' not in json.dumps(public)


def kadokawa_paper_listing():
    row = kadokawa_electronic_listing()
    row.update(entry_id='lnr:pub:kadokawaprint:' + 'b' * 32,
               publisher_reference_kind='kadokawa_print_product_reference', isbn=['978-0-306-40615-7'])
    row['publisher_product_reference']['form'] = '文庫'
    return row


def test_kadokawa_paper_listing_keeps_only_valid_isbn_literals_without_asserting_an_edition():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_paper_listing()
    row['isbn'] += ['0-306-40615-2', '978-0-306-40615-8', 9780306406157, None, {'path': 'PRIVATE'}]
    public = public_work(row)
    assert public['entry_id'] == public['work_id'] == row['entry_id']
    assert public['entry_kind'] == 'publisher_reference'
    assert public['publisher_reference_kind'] == 'kadokawa_print_product_reference'
    assert public['publisher_provider'] == 'kadokawa-print'
    assert public['reference_scope'] == 'private_bibliographic_discovery'
    assert public['isbn'] == ['978-0-306-40615-7', '0-306-40615-2']
    assert public['publisher_product_reference']['form'] == '文庫'
    assert public['publisher_product_reference']['credit_literals'] == [{'role_literal': '著者', 'name': '公式表示名'}]
    assert public['members'] == public['authors'] == [] and public['recommendable'] is False
    assert public['light_novel_confirmed'] is False
    assert all(public[key] is False for key in ('edition_identity_asserted', 'story_series_identity_asserted',
        'original_work_identity_asserted', 'light_novel_identity_asserted'))
    assert public['usage_permissions']['public_display'] == public['usage_permissions']['commercial_reuse'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(public)
    electronic = kadokawa_electronic_listing()
    electronic['isbn'] = row['isbn']
    assert public_work(electronic)['isbn'] == [], 'paper ISBN support must not supplement electronic listings'


@pytest.mark.parametrize('mutation', ['namespace', 'marker', 'basis', 'scope'])
def test_kadokawa_paper_reference_cannot_reuse_a_different_profile_or_purpose(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_paper_listing()
    if mutation == 'namespace': row['entry_id'] = 'lnr:pub:kadokawa:' + 'b' * 32
    elif mutation == 'marker': row['publisher_reference_kind'] = 'kadokawa_product_reference'
    elif mutation == 'basis': row['reference_identity_basis'] = 'edition'
    else: row['reference_scope'] = 'public_display_allowed'
    public = public_work(row)
    assert 'publisher_product_reference' not in public
    assert 'publisher_reference_kind' not in public
    assert public['isbn'] == []


def tobooks_listing():
    row = kadokawa_electronic_listing()
    row.update(entry_id='lnr:pub:tobooks:' + 'c' * 32, publisher_reference_kind='tobooks_product_reference',
               source_url='https://www.tobooks.jp/contents/12345', isbn=['9780306406157'], publication_label=None)
    row['publisher_product_reference'].update(publisher_reference_id='tobooks-product:12345',
        source_url=row['source_url'], label=None, form=None,
        credits=[{'role': 'unknown', 'name': '掲載名'}],
        credit_literals=[{'role_literal': None, 'name': '掲載名', 'locator': 'PRIVATE'}])
    return row


def test_tobooks_listing_keeps_source_isbn_and_unknown_roles_without_creating_identity():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = tobooks_listing()
    public = public_work(row)
    assert public['entry_id'] == row['entry_id'] and public['publisher_provider'] == 'tobooks'
    assert public['publisher_reference_kind'] == 'tobooks_product_reference'
    assert public['isbn'] == ['9780306406157']
    assert public['authors'] == public['members'] == [] and public['recommendable'] is False
    product = public['publisher_product_reference']
    assert product['source_url'] == row['source_url']
    assert product['publisher_reference_id'] == 'tobooks-product:12345'
    assert product['label'] is product['form'] is None
    assert product['credits'] == [{'role': 'unknown', 'name': '掲載名'}]
    assert product['credit_literals'] == [{'role_literal': None, 'name': '掲載名'}]
    assert all(public[key] is False for key in ('edition_identity_asserted', 'story_series_identity_asserted',
        'original_work_identity_asserted', 'light_novel_identity_asserted'))
    assert public['usage_permissions']['public_display'] == public['usage_permissions']['commercial_reuse'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(public)


@pytest.mark.parametrize('mutation', ['marker', 'scope', 'isbn'])
def test_tobooks_reference_rejects_other_profiles_purposes_or_non_source_isbn_values(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = tobooks_listing()
    if mutation == 'marker': row['publisher_reference_kind'] = 'kadokawa_product_reference'
    elif mutation == 'scope': row['reference_scope'] = 'public_display_allowed'
    else: row['isbn'] = ['978-0-306-40615-7', '0306406152', '9780306406158', 9780306406157, {'path': 'PRIVATE'}]
    public = public_work(row)
    assert public['isbn'] == []
    if mutation != 'isbn':
        assert 'publisher_product_reference' not in public


def kadokawa_trade_listing():
    row = kadokawa_paper_listing()
    row.update(entry_id='lnr:pub:kadokawatrade:' + 'd' * 32,
               publisher_reference_kind='kadokawa_trade_product_reference')
    row['publisher_product_reference'].update(form='単行本', binding='PRIVATE_HARDCOVER', dimensions='PRIVATE_B6')
    return row


def test_kadokawa_trade_reference_keeps_explicit_form_and_isbn_without_binding_or_identity_inference():
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_trade_listing()
    row['isbn'] += ['0-306-40615-2', '9780306406158', 9780306406157]
    public = public_work(row)
    assert public['entry_id'] == row['entry_id'] and public['publisher_provider'] == 'kadokawa-trade'
    assert public['publisher_reference_kind'] == 'kadokawa_trade_product_reference'
    assert public['publisher_product_reference']['form'] == '単行本'
    assert public['isbn'] == ['978-0-306-40615-7', '0-306-40615-2']
    assert public['publisher_product_reference']['credit_literals'] == [{'role_literal': '著者', 'name': '公式表示名'}]
    assert public['authors'] == public['members'] == [] and public['recommendable'] is False
    assert all(public[key] is False for key in ('edition_identity_asserted', 'story_series_identity_asserted',
        'original_work_identity_asserted', 'light_novel_identity_asserted'))
    assert public['usage_permissions']['public_display'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(public)


@pytest.mark.parametrize('mutation', ['namespace', 'id', 'marker', 'form', 'scope', 'rights'])
def test_kadokawa_trade_reference_profile_and_permission_boundaries(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = kadokawa_trade_listing()
    if mutation == 'namespace': row['entry_id'] = 'lnr:pub:kadokawaprint:' + 'd' * 32
    elif mutation == 'id': row['entry_id'] = 'lnr:pub:kadokawatrade:' + 'D' * 32
    elif mutation == 'marker': row['publisher_reference_kind'] = 'kadokawa_print_product_reference'
    elif mutation == 'form': row['publisher_product_reference']['form'] = 'ハードカバー'
    elif mutation == 'scope': row['reference_scope'] = 'public_display_allowed'
    else: row['usage_permissions']['public_display'] = 'allowed'
    public = public_work(row)
    if mutation == 'rights':
        assert public.get('usage_permissions', {}).get('public_display') != 'allowed'
    else:
        assert 'publisher_reference_kind' not in public and 'publisher_product_reference' not in public
        assert public['isbn'] == []


def finite_lineup_listing(provider):
    row = tobooks_listing()
    row.update(entry_id='lnr:pub:' + provider + ':' + 'e' * 32,
               publisher_reference_kind=provider + '_product_reference', isbn=['9780306406157'])
    product = row['publisher_product_reference']
    if provider == 'gagaga':
        row['source_url'] = 'https://gagagabunko.jp/lineup/202609.html#02'
        product.update(publisher_reference_id='publisher-lineup:' + 'a' * 24, label='ガガガ文庫',
            credits=[{'role': 'unknown', 'name': '掲載名'}], credit_literals=[{'role_literal': '著', 'name': '掲載名'}])
    else:
        row['source_url'] = 'https://www.kodansha.co.jp/comic/products/0000431602'
        product.update(publisher_reference_id='kodansha-product:0000431602', label='講談社ラノベ文庫',
            credits=[{'role': 'author', 'name': '掲載名'}, {'role': 'unknown', 'name': '役割不明の掲載名'}], credit_literals=[])
    product['source_url'] = row['source_url']
    return row


@pytest.mark.parametrize('provider', ['gagaga', 'kodansha'])
def test_finite_lineup_references_keep_credit_semantics_and_unknown_isbn_without_identity(provider):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = finite_lineup_listing(provider)
    public = public_work(row)
    assert public['entry_id'] == row['entry_id'] and public['publisher_provider'] == provider
    assert public['publisher_reference_kind'] == provider + '_product_reference'
    assert public['isbn'] == (['9780306406157'] if provider == 'gagaga' else [])
    assert public['authors'] == public['members'] == []
    assert public['recommendable'] is public['light_novel_confirmed'] is False
    assert public['publisher_product_reference']['source_url'] == row['source_url']
    assert public['publisher_product_reference']['publisher_reference_id'] == row['publisher_product_reference']['publisher_reference_id']
    if provider == 'gagaga':
        assert public['publisher_product_reference']['credit_literals'] == [{'role_literal': '著', 'name': '掲載名'}]
    else:
        assert public['publisher_product_reference']['credit_literals'] == []
        assert public['publisher_product_reference']['credits'] == row['publisher_product_reference']['credits']
    assert all(public[key] is False for key in ('edition_identity_asserted', 'story_series_identity_asserted',
        'original_work_identity_asserted', 'light_novel_identity_asserted'))
    assert public['usage_permissions']['public_display'] == public['usage_permissions']['commercial_reuse'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(public)


@pytest.mark.parametrize('provider', ['gagaga', 'kodansha'])
@pytest.mark.parametrize('mutation', ['marker', 'scope'])
def test_finite_lineup_references_require_matching_profile_and_private_purpose(provider, mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = finite_lineup_listing(provider)
    if mutation == 'marker': row['publisher_reference_kind'] = 'tobooks_product_reference'
    else: row['reference_scope'] = 'public_display_allowed'
    public = public_work(row)
    assert 'publisher_product_reference' not in public
    assert public['isbn'] == []


def overlap_detail_listing(category='nvl'):
    row = kadokawa_paper_listing()
    row.update(entry_id='lnr:pub:overlapdetails:' + 'a' * 32,
               publisher_reference_kind='overlap_detail_product_reference',
               source_url='https://over-lap.co.jp/test/product/0/opaque_1/?cat=' + category.upper() + '&swrd=')
    row['publisher_product_reference'].update(publisher_reference_id='overlap-' + category + '-product:opaque_1',
        source_url=row['source_url'], label='掲載カードのレーベル', form=None,
        credits=[{'role': 'unknown', 'name': '無印の掲載名'}, {'role': 'illustrator', 'name': '明示の掲載名'}],
        credit_literals=[{'role_literal': None, 'name': '無印の掲載名'},
            {'role_literal': 'イラスト', 'role_marker_literal': 'イラスト/' if category == 'nvf' else 'イラスト／',
             'name': '明示の掲載名', 'locator': 'PRIVATE'}])
    return row


@pytest.mark.parametrize('category', ['nvl', 'nvf', 'nvr'])
def test_overlap_detail_reference_keeps_literal_isbn_and_explicit_illustrator_without_author_inference(category):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = overlap_detail_listing(category)
    public = public_work(row)
    assert public['entry_id'] == row['entry_id'] and public['publisher_provider'] == 'overlap-details'
    assert public['publisher_reference_kind'] == 'overlap_detail_product_reference'
    assert public['isbn'] == ['978-0-306-40615-7']
    product = public['publisher_product_reference']
    assert product['publisher_reference_id'] == row['publisher_product_reference']['publisher_reference_id']
    assert product['credit_literals'] == [{'role_literal': None, 'name': '無印の掲載名'},
        {'role_literal': 'イラスト', 'role_marker_literal': 'イラスト/' if category == 'nvf' else 'イラスト／', 'name': '明示の掲載名'}]
    assert product['credits'] == row['publisher_product_reference']['credits'] and product['form'] is None
    assert public['authors'] == public['members'] == [] and public['recommendable'] is False
    assert all(public[key] is False for key in ('edition_identity_asserted', 'story_series_identity_asserted',
        'original_work_identity_asserted', 'light_novel_identity_asserted'))
    assert public['usage_permissions']['public_display'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(public)


@pytest.mark.parametrize('mutation', ['marker', 'scope', 'bnk'])
def test_overlap_detail_reference_does_not_widen_provider_or_source_scope(mutation):
    from recommend_book.recommendation.scale_site_v1 import public_work
    row = overlap_detail_listing()
    if mutation == 'marker': row['publisher_reference_kind'] = 'tobooks_product_reference'
    elif mutation == 'scope': row['reference_scope'] = 'public_display_allowed'
    else: row['publisher_product_reference']['publisher_reference_id'] = 'overlap-product:opaque_1'
    public = public_work(row)
    assert 'publisher_product_reference' not in public
    assert public['isbn'] == []
