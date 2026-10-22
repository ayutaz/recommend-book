import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from recommend_book.recommendation.scale_site_v1 import create_server


class PreferenceCatalogue:
    def __init__(self):
        self.preference_calls = []

    def preference_facets(self):
        return {
            'axes': {
                'theme': [{'label_id': 'theme.growth', 'label': '成長', 'count': 12,
                           'raw_path': 'PRIVATE-SENTINEL'}],
                'structure': [{'label_id': 'structure.mystery', 'label': '謎解き', 'count': 7}],
                'protagonist': [{'label_id': 'protagonist.detective', 'label': '探偵', 'count': 5}],
                'atmosphere': [{'label_id': 'atmosphere.dark', 'label': '重い雰囲気', 'count': 3}],
                'style': [{'label_id': 'style.private', 'label': '内部', 'count': 99}],
            },
            'private_proof': 'PRIVATE-SENTINEL',
        }

    def recommend_preferences(self, **payload):
        self.preference_calls.append(payload)
        return {
            'explanation': '明示された好みと公開済み特徴に基づく順位です。',
            'feature_insufficiency': [{'axis': 'style', 'status': 'unknown',
                                       'reason': 'axis_not_adopted_or_proof_unavailable'},
                                      {'axis': 'atmosphere', 'status': 'unknown',
                                       'reason': 'axis_not_adopted_or_proof_unavailable'}],
            'items': [{
                'qid': 'Q2', 'work_id': 'wd:Q2', 'title': '候補作品', 'original_language': 'ja',
                'authors': [{'qid': 'Q3', 'name': '著者'}], 'genres': [], 'year': 2001,
                'read_status': 'unread', 'recommendable': True, 'score': .75,
                'recommendation_mode': 'preference_recommendation',
                'reasons': [{'kind': 'native_feature_match', 'axis': 'theme', 'shared': ['theme.growth'],
                             'relation': 'match',
                             'source_url': 'https://example.org/source',
                             'raw_path': 'PRIVATE-SENTINEL'},
                            {'kind': 'avoided_feature', 'axis': 'atmosphere', 'shared': ['atmosphere.dark'],
                             'relation': 'penalty'},
                            {'kind': 'same_language', 'shared': ['en'], 'relation': 'context'},
                            {'kind': 'publication_period', 'shared': [2001], 'relation': 'context'}],
                'recommendation_provenance': [{'status': 'verified_native', 'source_attributions': [{
                    'source_url': 'https://example.org/source', 'license': 'CC-BY-SA-4.0',
                    'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
                    'history_url': 'https://example.org/history', 'attribution': {'credit': 'Example contributors'},
                    'revision': '42', 'changed_from_source': '分類用に要約', 'display_allowed': True,
                    'private_path': 'PRIVATE-SENTINEL',
                }]}],
                'feature_insufficiency': [{'axis': 'style', 'status': 'unknown',
                                           'reason': 'axis_not_adopted_or_proof_unavailable'}],
            }],
        }


@pytest.fixture
def site():
    catalogue = PreferenceCatalogue()
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', catalogue
    server.shutdown()
    server.server_close()
    thread.join()


def request_json(base, path, payload=None, *, headers=None):
    request = Request(base + path, headers=headers or {})
    if payload is not None:
        request = Request(base + path, data=json.dumps(payload).encode(), method='POST',
                          headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1',
                                   **(headers or {})})
    with urlopen(request) as response:
        return json.load(response)


def test_preference_facets_expose_only_adopted_public_choices(site):
    base, _ = site
    data = request_json(base, '/api/preference-facets')
    assert set(data['axes']) == {'theme', 'structure', 'protagonist', 'atmosphere'}
    assert data['axes']['theme'] == [{'label_id': 'theme.growth', 'label': '成長', 'count': 12}]
    assert 'style' not in data['axes']
    assert 'PRIVATE-SENTINEL' not in json.dumps(data)


def test_preference_facets_accept_backend_native_feature_shape():
    from recommend_book.recommendation.scale_site_v1 import _public_preference_facets
    data = _public_preference_facets({'native_features': [
        {'axis': 'theme', 'label_id': 'theme.growth', 'label': '成長', 'availability': 'verified_on_demand'},
        {'axis': 'style', 'label_id': 'style.first_person', 'label': '一人称'},
    ]})
    assert data == {'axes': {'theme': [{'label_id': 'theme.growth', 'label': '成長'}],
                             'structure': [], 'protagonist': [], 'atmosphere': []},
                    'coverage_note': '分類体系の選択肢です。作品ごとの採用件数は未集計で、推薦時に個別検証します。'}


def test_native_source_attributions_reuse_public_content_source_boundary():
    from recommend_book.recommendation.scale_site_v1 import public_work

    row = public_work({'qid': 'Q2', 'source_attributions': [{
        'source_url': 'https://en.wikipedia.org/wiki/Example',
        'license': 'CC-BY-SA-4.0',
        'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
        'history_url': 'https://en.wikipedia.org/w/index.php?title=Example&action=history',
        'attribution': {'credit': 'Wikipedia contributors: Example'},
        'revision': 42, 'locator': 'Plot', 'changed_from_source': '分類用に要約',
        'display_allowed': True, 'quote_allowed': False,
        'private_path': 'PRIVATE-SENTINEL',
    }]})
    source = row['content_sources'][0]
    assert source['license'] == 'CC-BY-SA-4.0'
    assert source['revision'] == '42'
    assert source['history_url'].endswith('&action=history')
    assert source['attribution'] == 'Wikipedia contributors: Example'
    assert 'PRIVATE-SENTINEL' not in json.dumps(row)


def test_preference_recommendation_passes_explicit_inputs_and_public_reasons(site):
    base, catalogue = site
    payload = {
        'liked_qids': ['Q1'], 'preferred_features': ['theme.growth'],
        'avoided_features': ['atmosphere.dark'],
        'axis_weights': {'theme': 2, 'structure': 1}, 'genre': 'Q10', 'language': 'ja',
        'year_from': 1900, 'year_to': 2020, 'exclude_read': True, 'limit': 10,
    }
    data = request_json(base, '/api/recommendations', payload, headers={'Origin': base})
    assert data['mode'] == 'preference_recommendation'
    assert data['preference_based'] is True and data['personalized'] is False
    assert data['history_evaluation'] == 'unverified'
    assert data['items'][0]['recommendation_mode'] == 'preference_recommendation'
    assert data['items'][0]['reasons'][0]['detail'] == '指定した特徴と一致'
    assert data['items'][0]['reasons'][0]['direction'] == 'prefer'
    assert data['items'][0]['reasons'][0]['shared'] == [{'qid': 'theme.growth', 'label': '成長'}]
    assert data['items'][0]['reasons'][1]['direction'] == 'avoid'
    assert data['items'][0]['reasons'][1]['detail'] == '避けたい特徴に一致'
    assert data['items'][0]['reasons'][1]['shared'] == [{'qid': 'atmosphere.dark', 'label': '暗い雰囲気'}]
    assert data['items'][0]['reasons'][2]['detail'] == '原言語が共通'
    assert data['items'][0]['reasons'][3]['detail'] == '刊行年代が近い'
    assert 'axis_not_adopted_or_proof_unavailable' not in json.dumps(data)
    assert data['items'][0]['recommendation_provenance'][0]['revision'] == '42'
    assert data['items'][0]['content_sources'][0]['license'] == 'CC-BY-SA-4.0'
    assert data['items'][0]['content_sources'][0]['history_url'] == 'https://example.org/history'
    assert data['items'][0]['content_sources'][0]['attribution'] == 'Example contributors'
    assert data['feature_insufficiency'] == '文体・雰囲気は未確認です。避けたい特徴の有無を判断できません。'
    assert data['items'][0]['feature_insufficiency'] == '文体は未確認です。避けたい特徴の有無を判断できません。'
    assert 'PRIVATE-SENTINEL' not in json.dumps(data)
    assert catalogue.preference_calls == [{
        'liked_qids': ['Q1'], 'preferred_features': ['theme.growth'],
        'avoided_features': ['atmosphere.dark'], 'axis_weights': {'theme': 2.0, 'structure': 1.0},
        'genre': 'Q10', 'language': 'ja', 'year_from': 1900, 'year_to': 2020,
        'exclude_read': True, 'limit': 10,
    }]


def test_preference_recommendation_rejects_invalid_or_cross_origin_requests(site):
    base, _ = site
    invalid = {'liked_qids': ['not-a-qid'], 'preferred_features': [], 'avoided_features': [],
               'axis_weights': {}, 'exclude_read': True, 'limit': 10}
    with pytest.raises(HTTPError) as error:
        request_json(base, '/api/recommendations', invalid)
    assert error.value.code == 400
    request = Request(base + '/api/recommendations', data=b'{}', method='POST',
                      headers={'Content-Type': 'application/json', 'X-Catalogue-Request': '1',
                               'Origin': 'https://evil.example'})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403


def test_http_recommendation_uses_real_scale_preference_backend(tmp_path):
    """Keep the HTTP contract connected to the deterministic backend entrypoints."""
    from datetime import datetime, timezone
    from hashlib import sha256

    from recommend_book.scale_catalogue_v1 import ScaleCatalogue
    from recommend_book.scale_preferences_v1 import preference_facets, recommend_preferences
    from test_scale_catalogue_v1 import record, save

    policy = tmp_path / 'license.html'
    policy.write_text('Wikidata structured main namespace data CC0', encoding='utf-8')
    conditions = tmp_path / 'conditions.json'
    policy_sha = sha256(policy.read_bytes()).hexdigest()
    receipt_sha = save(conditions, {
        'version': 'scale-source-conditions-v1', 'source': 'wikidata', 'policy_id': 'wikidata-cc0',
        'metadataonly_scope': True, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'policy_snapshot': {'path': str(policy), 'sha256': policy_sha},
        'allowed_domains': ['www.wikidata.org', 'query.wikidata.org'],
        'usages': dict.fromkeys(['acquire', 'local_storage', 'display', 'external_llm',
                                 'redistribution', 'commercial'], 'allowed'),
    })
    catalogue = ScaleCatalogue(tmp_path / 'catalogue.sqlite', source_conditions_path=conditions,
                               source_conditions_sha256=receipt_sha)
    catalogue.ingest([record(tmp_path, 'Q100', genre='Q200'), record(tmp_path, 'Q101', genre='Q200')])
    catalogue.recommend_preferences = lambda **kwargs: recommend_preferences(catalogue, **kwargs)
    catalogue.preference_facets = lambda: preference_facets(catalogue)
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        facets = request_json(base, '/api/preference-facets')
        assert facets['axes']['theme']
        assert '採用件数は未集計' in facets['coverage_note']
        data = request_json(base, '/api/recommendations', {
            'liked_qids': ['Q100'], 'preferred_features': [], 'avoided_features': [],
            'axis_weights': {}, 'exclude_read': True, 'limit': 1,
        }, headers={'Origin': base})
        assert data['items'][0]['qid'] == 'Q101'
        assert any(reason['kind'] == 'source_genre' and reason['direction'] == 'context'
                   for reason in data['items'][0]['reasons'])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_preference_controls_are_present_without_unsafe_rendering(site):
    base, _ = site
    with urlopen(base + '/') as response:
        html = response.read().decode()
    with urlopen(base + '/app.js') as response:
        script = response.read().decode()
    for text in ('好みから探す', '好きな作品に追加', '推薦', 'preference-facets', 'preference-exclude-read', 'axis-weights'):
        assert text in html or text in script
    assert 'innerHTML' not in script
    assert '/api/recommendations' in script and '/api/preference-facets' in script
