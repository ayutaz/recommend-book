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
            'feature_insufficiency': '一部作品では詳細特徴が不足しています。',
            'items': [{
                'qid': 'Q2', 'work_id': 'wd:Q2', 'title': '候補作品', 'original_language': 'ja',
                'authors': [{'qid': 'Q3', 'name': '著者'}], 'genres': [], 'year': 2001,
                'read_status': 'unread', 'recommendable': True, 'score': .75,
                'recommendation_mode': 'preference_recommendation',
                'reasons': [{'kind': 'preferred_feature', 'axis': 'theme', 'label': '成長',
                             'detail': '重視したテーマと一致', 'direction': 'prefer',
                             'source_url': 'https://example.org/source',
                             'raw_path': 'PRIVATE-SENTINEL'}],
                'recommendation_provenance': [{'source_url': 'https://example.org/source',
                                               'revision': '42', 'private_path': 'PRIVATE-SENTINEL'}],
                'feature_insufficiency': '文体は不明',
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
    assert data['items'][0]['reasons'][0]['detail'] == '重視したテーマと一致'
    assert data['items'][0]['recommendation_provenance'][0]['revision'] == '42'
    assert data['feature_insufficiency'] == '一部作品では詳細特徴が不足しています。'
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


def test_preference_controls_are_present_without_unsafe_rendering(site):
    base, _ = site
    with urlopen(base + '/') as response:
        html = response.read().decode()
    with urlopen(base + '/app.js') as response:
        script = response.read().decode()
    for text in ('好みから探す', '好きな作品に追加', '推薦', 'preference-facets', 'preference-exclude-read'):
        assert text in html or text in script
    assert 'innerHTML' not in script
    assert '/api/recommendations' in script and '/api/preference-facets' in script
