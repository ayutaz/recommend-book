import hashlib
import json
import threading
import time


def test_feature_exports_verify_concurrently_within_request(tmp_path, monkeypatch):
    from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue

    catalogue = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    documents = []
    with catalogue._connect() as db:
        for index in range(2):
            entry_id = f'lns:wd:Q{index + 1}'
            record = {'id': f'Q{index + 1}', 'schema_version': 'light-novel-metadata-v1',
                      'source': 'wikidata', 'kind': 'series', 'title': str(index)}
            encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO entries(entry_id,kind,record_json,record_sha256) VALUES(?,?,?,?)',
                       (entry_id, 'series', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
            manifest = tmp_path / f'manifest-{index}.json'
            raw = json.dumps({'version': 'light-novel-series-feature-export-v1',
                              'rows': [{'entry_id': entry_id, 'scope': 'series'}]},
                             sort_keys=True).encode()
            manifest.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            db.execute('INSERT INTO features(entry_id,manifest_path,manifest_sha256) VALUES(?,?,?)',
                       (entry_id, str(manifest), digest))
            documents.append((entry_id, str(manifest), digest))

    lock = threading.Lock()
    active = peak = 0

    def fake_verify(path, expected):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        entry_id = next(entry_id for entry_id, manifest, _ in documents if manifest == path)
        return {'version': 'light-novel-series-feature-export-v1',
                'rows': [{'entry_id': entry_id, 'scope': 'series'}],
                'artifact_sha256': {path: expected}}

    monkeypatch.setattr('recommend_book.light_novel_feature_exports_v1.verify_feature_export', fake_verify)
    result = catalogue._feature_rows({entry_id for entry_id, _, _ in documents}, _request_cache={})

    assert set(result) == {'lns:wd:Q1', 'lns:wd:Q2'}
    assert peak == 2
