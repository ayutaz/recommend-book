"""Explicit nonempty standalone searches authenticate a bounded metadata scope."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

import pytest

from recommend_book.light_novel_catalogue_v1 import normalize_search
from test_light_novel_gagaga_kodansha_catalogue_v1 import bundle as finite_bundle, adopt


@pytest.fixture(params=['gagaga', 'kodansha'])
def target(tmp_path, monkeypatch, request):
    e = finite_bundle.__wrapped__(tmp_path, monkeypatch, SimpleNamespace(param=request.param))
    e.identities = adopt(e)['reference_entries']
    e.query = '題' if e.provider == 'gagaga' else '試験作品'
    return e


def test_scoped_query_matches_old_safe_rows_without_world_metadata_or_relation_feature_reads(target, monkeypatch):
    e = target
    old_rows = e.cat._filtered(e.cat._rows(include_features=False, candidate_query=e.query),
                              query=e.query, entry_kind='publisher_reference')
    old_rows.sort(key=lambda row: (0 if normalize_search(row['title']) == normalize_search(e.query) else 1,
                                   row['title'], row['entry_id']))
    with e.cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('lne:ndl:unrelated', 'edition', 'not JSON', 'bad SHA'))
    def forbidden(*args, **kwargs): pytest.fail('explicit standalone query must not load the catalogue graph')
    for name in ('_rows', '_candidate_ids', '_families', '_feature_rows', '_saved_confirmation_search_titles',
                 '_attach_memberships', '_attach_book_confirmations', '_attach_bibliographic_correspondences'):
        monkeypatch.setattr(e.cat, name, forbidden)
    connect, queries = e.cat._connect, []
    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(queries.append)
            yield db
    monkeypatch.setattr(e.cat, '_connect', traced)
    e.calls.clear()
    result = e.cat.search_catalogue(e.query, entry_kind='publisher_reference')
    assert result['items'] == old_rows and result['total'] == 2
    assert e.calls == [e.provider]  # Shared finite-source pin, several selected rows.
    selects = [q for q in queries if q.lstrip().upper().startswith('SELECT')]
    assert len(selects) == 1 and "kind='publisher_reference'" in selects[0]
    assert "entry_id LIKE 'lnr:pub:%'" in selects[0]


@pytest.mark.parametrize('change', ['hash', 'kind', 'schema', 'provider', 'uuid', 'namespace', 'non_namespace'])
def test_all_scoped_metadata_keeps_hash_kind_schema_provider_and_namespace_guards(target, change):
    e = target
    identity = e.identities[e.ids[1]]
    # This second record is not a title match: all scoped metadata still matters.
    first = next(r for r in e.rows if r['publisher_reference_id'] == e.ids[0])['title']
    with e.cat._connect() as db:
        entry = db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone()
        record = json.loads(entry['record_json']); kind = entry['kind']
        if change == 'kind': kind = 'edition'
        elif change == 'schema': record['schema_version'] = 'not-a-reference-schema'
        elif change == 'provider': record['provider'] = 'tobooks'
        elif change == 'uuid': record['id'] = 'a' * 32
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        digest = '0' * 64 if change == 'hash' else hashlib.sha256(encoded.encode()).hexdigest()
        new_id = ('lnr:pub:unknown:' + 'a' * 32 if change == 'namespace'
                  else 'lne:ndl:wrong-kind' if change == 'non_namespace' else identity)
        db.execute('UPDATE entries SET entry_id=?,kind=?,record_json=?,record_sha256=? WHERE entry_id=?',
                   (new_id, kind, encoded, digest, identity))
    with pytest.raises(ValueError): e.cat.search_catalogue(first, entry_kind='publisher_reference')


def test_credit_literal_query_read_filters_and_source_mutation_keep_existing_semantics(target):
    e = target
    identity = e.identities[e.ids[0]]
    e.cat.set_read_status(identity, 'read')
    row = e.cat.work_detail(identity)
    name = row['publisher_product_reference']['credits'][0]['name']
    found = e.cat.search_catalogue(name, entry_kind='publisher_reference', read_filter='read')
    assert [r['entry_id'] for r in found['items']] == [identity]
    read = e.cat.search_catalogue(e.query, entry_kind='publisher_reference', read_filter='read')
    unread = e.cat.search_catalogue(e.query, entry_kind='publisher_reference', read_filter='unknown')
    assert read['total'] == unread['total'] == 1
    e.raw.write_bytes(b'changed own raw')
    with pytest.raises(ValueError): e.cat.search_catalogue(e.query, entry_kind='publisher_reference')


def test_scoped_relaxed_title_prefilter_uses_the_existing_normalized_key(target):
    e = target
    query = '完全な題：' if e.provider == 'gagaga' else '試験作品１：'
    old = e.cat._filtered(e.cat._rows(include_features=False, candidate_query=query),
                          query=query, entry_kind='publisher_reference')
    assert old
    scoped = e.cat.search_catalogue(query, entry_kind='publisher_reference')
    assert {r['entry_id'] for r in scoped['items']} == {r['entry_id'] for r in old}


@pytest.mark.parametrize('kind,query', [(None, '題'), ('edition', '題'), ('series', '題'),
                                      ('publisher_reference', ''), ('publisher_reference', '  ')])
def test_other_search_modes_keep_the_unchanged_graph_path(target, monkeypatch, kind, query):
    calls = []
    def rows(**kwargs): calls.append(kwargs); return []
    def forbidden(*args, **kwargs): pytest.fail('only explicit reference + nonempty query may use the bounded path')
    monkeypatch.setattr(target.cat, '_rows', rows)
    monkeypatch.setattr(target.cat, '_publisher_reference_query_rows', forbidden)
    assert target.cat.search_catalogue(query, entry_kind=kind)['total'] == 0
    assert calls == [{'include_features': False, 'candidate_query': query}]
