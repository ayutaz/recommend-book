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
