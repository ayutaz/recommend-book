"""Independent reference detail authenticates its target, never the catalogue graph."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest

from test_light_novel_gagaga_kodansha_catalogue_v1 import bundle as finite_bundle, adopt


@pytest.fixture(params=['gagaga', 'kodansha'])
def target(tmp_path, monkeypatch, request):
    e = finite_bundle.__wrapped__(tmp_path, monkeypatch, SimpleNamespace(param=request.param))
    e.identity = adopt(e, e.ids[:1])['reference_entries'][e.ids[0]]
    return e


def forbid_graph(cat, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('standalone reference detail must not read the catalogue graph')
    for name in ('_rows', '_candidate_ids', '_families', '_feature_rows', '_attach_memberships',
                 '_attach_book_confirmations', '_attach_bibliographic_correspondences'):
        monkeypatch.setattr(cat, name, forbidden)


def test_target_only_detail_matches_previous_projection_and_ignores_unrelated_corruption(target, monkeypatch):
    e = target
    # Explicitly use the unchanged graph path as the pre-optimization baseline.
    previous = next(row for row in e.cat._rows(feature_entry_ids={e.identity}, detail_entry_id=e.identity)
                    if row['entry_id'] == e.identity)
    previous['members'] = []
    with e.cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('lne:ndl:unrelated', 'edition', 'not JSON', 'not a SHA'))
        db.execute('INSERT INTO entries VALUES(?,?,?,?)',
            ('lnr:pub:' + e.provider + ':' + 'f' * 32, 'publisher_reference', '{"absent_raw":true}', 'bad SHA'))
    forbid_graph(e.cat, monkeypatch)
    queries, connect = [], e.cat._connect
    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(queries.append)
            yield db
    monkeypatch.setattr(e.cat, '_connect', traced)
    e.calls.clear()
    row = e.cat.work_detail(e.identity)
    assert row == previous and row['members'] == []
    assert e.calls == [e.provider]
    selects = [q for q in queries if q.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1 and 'WHERE e.entry_id=' in selects[0]
    assert 'LEFT JOIN read_status' in selects[0]


def test_missing_detail_does_not_authenticate_source_and_read_status_is_always_latest(target, monkeypatch):
    e = target
    forbid_graph(e.cat, monkeypatch)
    e.calls.clear()
    assert e.cat.work_detail('lnr:pub:' + e.provider + ':' + '0' * 32) is None
    assert e.calls == []
    assert e.cat.work_detail(e.identity)['read_status'] == 'unknown'
    assert e.cat.set_read_status(e.identity, 'reading')['read_status'] == 'reading'
    assert e.cat.work_detail(e.identity)['read_status'] == 'reading'
    with e.cat._connect() as db: db.execute('UPDATE read_status SET status=? WHERE entry_id=?', ('read', e.identity))
    assert e.cat.work_detail(e.identity)['read_status'] == 'read'
    assert e.calls == [e.provider] * 4  # No TTL or cross-operation source cache.


@pytest.mark.parametrize('change', ['kind', 'schema', 'provider', 'uuid', 'digest', 'assertion', 'permission'])
def test_target_typed_metadata_proof_and_permissions_are_still_authenticated(target, monkeypatch, change):
    e = target
    forbid_graph(e.cat, monkeypatch)
    with e.cat._connect() as db:
        entry = db.execute('SELECT * FROM entries WHERE entry_id=?', (e.identity,)).fetchone()
        record = json.loads(entry['record_json'])
        kind = entry['kind']
        if change == 'kind': kind = 'edition'
        elif change == 'schema': record['schema_version'] = 'unknown'
        elif change == 'provider': record['provider'] = 'tobooks'
        elif change == 'uuid': record['id'] = 'a' * 32
        elif change == 'assertion': record['source_reference_assertions'][-1]['reference_id'] = e.ids[1]
        elif change == 'permission': record['usage_permissions']['commercial_reuse'] = True
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = '0' * 64 if change == 'digest' else hashlib.sha256(encoded.encode()).hexdigest()
        db.execute('UPDATE entries SET kind=?,record_json=?,record_sha256=? WHERE entry_id=?',
            (kind, encoded, digest, e.identity))
    with pytest.raises(ValueError): e.cat.work_detail(e.identity)


def test_source_revalidated_each_operation_and_raw_mutation_is_not_hidden(target, monkeypatch):
    e = target
    forbid_graph(e.cat, monkeypatch)
    e.calls.clear()
    assert e.cat.work_detail(e.identity)['entry_id'] == e.identity
    e.raw.write_bytes(b'changed source')
    with pytest.raises(ValueError): e.cat.work_detail(e.identity)
    assert e.calls == [e.provider, e.provider]


def test_other_identity_kinds_keep_existing_detail_graph_path(target, monkeypatch):
    calls = []
    def rows(**kwargs):
        calls.append(kwargs)
        return []
    monkeypatch.setattr(target.cat, '_rows', rows)
    assert target.cat.work_detail('lne:ndl:absent') is None
    assert calls == [{'feature_entry_ids': {'lne:ndl:absent'}, 'detail_entry_id': 'lne:ndl:absent'}]
