"""Only raw-reproduced, saved KAD part-title confirmations widen discovery."""
from copy import deepcopy
import hashlib
import json

import pytest

from test_light_novel_catalogue_v1 import (
    edition_bundle, own_part_title, publisher_edition_v2, setup_catalogue, write,
)


@pytest.fixture
def confirmed_title(publisher_edition_v2, tmp_path):
    cat, record, book = publisher_edition_v2(title='正式主題', volume='3', label_id=154,
        official_title='正式主題３ 秘密の完全巻題', extra=own_part_title('秘密の完全巻題'))
    assert cat.import_editions([record])['inserted'] == 1
    raw = tmp_path / 'official-books.json'
    raw_sha = write(raw, {'count_raw': {'book': 1}, 'result': {'book': [{
        'itemCode': '123456789012', 'title': book['title'], 'isbn': book['isbn'],
        'label_id': 154, 'product_form_id': 3,
        'disp_author_name': '<li>著者 著者</li>'}]}})
    manifest = tmp_path / 'official-manifest.json'
    sha = write(manifest, {'schema_version': 'kadokawa-publisher-books-v1',
        'rights_scope': 'reference_only_bibliography', 'pages': [{
            'source_url': 'https://www.kadokawa.co.jp/product/search/',
            'final_url': 'https://www.kadokawa.co.jp/product/search/',
            'http_status': 200, 'http_method': 'POST', 'request_data': {
                'label': '154', 'releaseDate': '1', 'size': '20', 'pageno': '1',
                'pageno_book': '1', 'pageno_media': '1'},
            'raw_path': raw.name, 'sha256': raw_sha}]})
    assert cat.confirm_publisher_editions('kadokawa', manifest, sha)['confirmed'] == 1
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM edition_book_confirmations').fetchone()[0])
    assert proof['match']['title_match']['rule'] == 'own_primary_title_explicit_volume_and_unique_part_information_v1'
    return cat, record, book, raw, manifest, proof


def snapshot(cat):
    with cat._connect() as db:
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table)]
                for table in ('entries', 'edition_book_confirmations', 'edition_series_memberships', 'read_status')}


def replace_proof(cat, proof, *, rehash=True):
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest() if rehash else '0' * 64))


def test_full_official_title_selects_and_filters_verified_edition_without_rewriting(confirmed_title):
    cat, record, book, _, _, _ = confirmed_title
    before = snapshot(cat)
    old = cat.search_catalogue('正式主題')['items'][0]
    result = cat.search_catalogue(book['title'], entry_kind='edition', language='ja')
    assert result['total'] == 1
    row = result['items'][0]
    assert row == old
    assert row['entry_id'] == 'lne:ndl:' + record['id']
    assert row['title'] == record['title'] and row['aliases'] == old['aliases']
    assert row['authors'] == old['authors'] and row['relations'] == old['relations']
    assert row.get('series_entry_id') is None and row['llm_features'] == []
    assert snapshot(cat) == before


@pytest.mark.parametrize('change', ['bad_digest', 'record_sha', 'source_id', 'provider', 'rule',
                                  'rehashed_title', 'malformed_match'])
def test_saved_claim_never_becomes_a_public_search_title_without_raw_proof(confirmed_title, change):
    cat, _, book, _, _, proof = confirmed_title
    query = book['title']
    if change == 'record_sha': proof['edition_record_sha256'] = '0' * 64
    elif change == 'source_id': proof['publisher_book_id'] = 'kadokawa:999999999999'
    elif change == 'provider': proof['provider'] = 'shueisha-dx'
    elif change == 'rule': proof['match']['title_match']['rule'] = 'unapproved_title_rule'
    elif change == 'malformed_match': proof['match'] = []
    elif change == 'rehashed_title':
        query = '正式主題３ 捏造された完全巻題'
        proof['match']['title_match']['official_complete_title'] = query
    replace_proof(cat, proof, rehash=change != 'bad_digest')
    assert cat.search_catalogue(query)['total'] == 0


@pytest.mark.parametrize('change', ['publisher_raw', 'manifest'])
def test_expired_source_does_not_expose_saved_official_title(confirmed_title, change):
    cat, _, book, raw, manifest, _ = confirmed_title
    (raw if change == 'publisher_raw' else manifest).write_text('{}', encoding='utf-8')
    assert cat.search_catalogue(book['title'])['total'] == 0


def test_same_isbn_and_source_card_without_binding_do_not_add_search_title(confirmed_title):
    cat, _, book, _, _, _ = confirmed_title
    with cat._connect() as db:
        db.execute('DELETE FROM edition_book_confirmations')
    assert cat.search_catalogue(book['title'])['total'] == 0


@pytest.mark.parametrize('change', ['kind', 'typed_id', 'record_sha', 'source_id', 'rule', 'digest'])
def test_invalid_saved_candidate_metadata_does_not_trigger_raw_projection(confirmed_title, monkeypatch, change):
    cat, _, book, _, _, proof = confirmed_title
    query = '保存proofだけにある捏造候補３'
    proof['match']['title_match']['official_complete_title'] = query
    replace_proof(cat, proof)
    if change in {'kind', 'typed_id'}:
        with cat._connect() as db:
            if change == 'kind':
                db.execute("UPDATE entries SET kind='work'")
            else:
                record = json.loads(db.execute('SELECT record_json FROM entries').fetchone()[0])
                record['id'] = record['source_record']['source_id'] = 'R100000002-I9'
                encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                db.execute('UPDATE entries SET record_json=?,record_sha256=?', (encoded, digest))
                proof['edition_record_sha256'] = digest
        if change == 'typed_id':
            replace_proof(cat, proof)
    else:
        if change == 'record_sha': proof['edition_record_sha256'] = '0' * 64
        elif change == 'source_id': proof['publisher_book_id'] = 'kadokawa:999999999999'
        elif change == 'rule': proof['match']['title_match']['rule'] = 'primary_isbd_title_and_explicit_volume'
        replace_proof(cat, proof, rehash=change != 'digest')
    monkeypatch.setattr(cat, '_project', lambda *a: pytest.fail('invalid saved title must not select raw'))
    monkeypatch.setattr(cat, '_publisher_books', lambda *a: pytest.fail('no selected binding to replay'))
    assert cat.search_catalogue(query)['total'] == 0


def test_saved_title_widens_candidate_superset_without_becoming_an_alias(confirmed_title):
    cat, record, book, _, _, _ = confirmed_title
    identity = 'lne:ndl:' + record['id']
    with cat._connect() as db:
        stored = db.execute('SELECT * FROM entries').fetchall()
    records = {identity: record}
    titles = cat._saved_confirmation_search_titles(stored, records, {})
    assert titles == {identity: [book['title']]}
    # Exercise selection independently from the normalizer's optional nested
    # title candidates; neither variant is a public projection or identity.
    selection_records = deepcopy(records)
    selection_records[identity]['source_record']['titles'] = [{'value': record['title']}]
    assert cat._candidate_ids(selection_records, query=book['title']) == set()
    assert cat._candidate_ids(selection_records, query=book['title'], confirmation_titles=titles) == {identity}
    assert record['title'] != book['title'] and book['title'] not in record.get('aliases', [])


def test_selected_ndl_raw_must_still_authenticate(confirmed_title):
    cat, record, book, _, _, _ = confirmed_title
    from pathlib import Path
    Path(record['provenance']['raw_path']).write_text('<changed/>', encoding='utf-8')
    with pytest.raises(ValueError):
        cat.search_catalogue(book['title'])


def test_candidate_selection_reads_proofs_once_and_never_projects_unrelated_raw(confirmed_title, monkeypatch):
    cat, record, book, _, _, _ = confirmed_title
    unrelated = deepcopy(record)
    unrelated['id'] = unrelated['source_record']['source_id'] = 'R100000002-I999'
    unrelated['title'] = '無関係な題'
    unrelated['source_record']['titles'] = [{'value': '無関係な題'}]
    unrelated['provenance']['raw_path'] = 'unrelated-absent-raw.xml'
    encoded = json.dumps(unrelated, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('lne:ndl:R100000002-I999',
            'edition', encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    queries, projects = [], []
    connect, project = cat._connect, cat._project
    from contextlib import contextmanager
    @contextmanager
    def traced():
        with connect() as db:
            db.set_trace_callback(queries.append)
            yield db
    def limited(record, cache):
        projects.append(record['id'])
        assert record['id'] != 'R100000002-I999'
        return project(record, cache)
    monkeypatch.setattr(cat, '_connect', traced)
    monkeypatch.setattr(cat, '_project', limited)
    assert cat.search_catalogue(book['title'])['total'] == 1
    assert projects == [record['id']]
    assert sum('FROM edition_book_confirmations' in sql for sql in queries) == 1
