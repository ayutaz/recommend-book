import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from recommend_book.recommendation.scale_site_v1 import create_server
from test_scale_history_management_v1 import HistoryCatalogue


@pytest.fixture
def history_site(tmp_path):
    catalogue = HistoryCatalogue(tmp_path / 'catalogue.sqlite')
    catalogue.add_state('Q100', 'unknown', '2026-10-22T00:00:00+00:00')
    catalogue.add_state('Q101', 'unread', '2026-10-22T00:01:00+00:00')
    server = create_server(catalogue=catalogue, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', catalogue
    server.shutdown()
    server.server_close()
    thread.join()


def get_json(url):
    with urlopen(url) as response:
        return json.load(response)


def post_json(url, payload, *, origin=None, include_request_header=True):
    headers = {'Content-Type': 'application/json'}
    if include_request_header:
        headers['X-Catalogue-Request'] = '1'
    if origin is not None:
        headers['Origin'] = origin
    request = Request(url, data=json.dumps(payload).encode(), method='POST', headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def test_export_endpoint_is_path_free_and_delete_is_bounded(history_site):
    base, catalogue = history_site
    exported = get_json(base + '/api/history/export')
    assert exported['kind'] == 'read-status'
    assert exported['record_count'] == 2
    assert str(catalogue.db_path) not in json.dumps(exported)
    assert {item['status'] for item in exported['records']} == {'unknown', 'unread'}
    result = post_json(base + '/api/history/read-status/delete', {
        'confirmation': 'DELETE_READ_STATUS', 'export_sha256': exported['sha256'],
    }, origin=base)
    assert result['deleted_count'] == 2
    assert get_json(base + '/api/history/export')['record_count'] == 0


def test_delete_requires_csrf_header_and_current_export(history_site):
    base, _ = history_site
    with pytest.raises(HTTPError) as error:
        post_json(base + '/api/history/read-status/delete', {
            'confirmation': 'DELETE_READ_STATUS', 'export_sha256': '0' * 64,
        }, origin=base, include_request_header=False)
    assert error.value.code == 403
    with pytest.raises(HTTPError) as error:
        post_json(base + '/api/history/read-status/delete', {
            'confirmation': 'DELETE_READ_STATUS', 'export_sha256': '0' * 64,
        }, origin=base)
    assert error.value.code == 400
    with pytest.raises(HTTPError) as error:
        post_json(base + '/api/history/read-status/delete', {
            'confirmation': 'DELETE_READ_STATUS', 'export_sha256': '0' * 64,
        }, origin='https://evil.example')
    assert error.value.code == 403
