"""Iris title compositions require accepted proofs and fresh source replay."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from test_light_novel_catalogue_v1 import (
    edition_bundle, iris_neo_edition, publisher_edition_v2, setup_catalogue, write,
)


@pytest.fixture(params=[None, '3'])
def confirmed(request, iris_neo_edition, tmp_path, monkeypatch):
    volume = request.param
    title = '正式主題' + (volume or '') + '～完全副題～'
    cat, record, book = iris_neo_edition(title='正式主題 : 完全副題', volume=volume,
        official_title=title, publisher_agent=True)
    book['source_url'] = 'https://www.ichijinsha.co.jp/iris/title/example/'
    book['publisher_book_id'] = 'publisher-book:' + hashlib.sha256(book['source_url'].encode()).hexdigest()[:24]
    raw = tmp_path / 'publisher-source.json'
    sha = write(raw, {'books': [book]})
    def source(provider, path, expected):
        assert provider == 'ichijinsha-iris' and str(path) == str(raw.resolve()) and expected == sha
        if hashlib.sha256(raw.read_bytes()).hexdigest() != expected:
            raise ValueError('fixture source expired')
        return json.loads(raw.read_text(encoding='utf-8'))['books']
    monkeypatch.setattr(cat, '_publisher_books', source)
    assert cat.import_editions([record])['inserted'] == 1
    assert cat.confirm_publisher_editions('ichijinsha-iris', raw, sha)['confirmed'] == 1
    with cat._connect() as db:
        proof = json.loads(db.execute('SELECT proof_json FROM edition_book_confirmations').fetchone()[0])
    assert proof['match']['rule_version'] == 'publisher-ndl-edition-confirmation-iris-neo-v2'
    return SimpleNamespace(cat=cat, record=record, title=title, raw=raw, sha=sha, proof=proof)


def snapshot(cat):
    with cat._connect() as db:
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
            for table in ('entries', 'edition_book_confirmations', 'edition_series_memberships', 'read_status')}


def test_confirmed_iris_complete_title_is_discoverable_without_rewriting_records(confirmed):
    e = confirmed; before = snapshot(e.cat)
    result = e.cat.search_catalogue(e.title, entry_kind='edition')
    assert result['total'] == 1
    row = result['items'][0]
    assert row['title'] == e.record['title'] and e.title not in row['aliases']
    assert row['edition_bibliography_confirmed'] and row.get('series_entry_id') is None
    assert snapshot(e.cat) == before


@pytest.mark.parametrize('change', ['digest', 'record_sha', 'source_id', 'source_url', 'version',
    'rule', 'fields', 'roundtrip', 'type', 'volume', 'extra_field', 'primary_locator', 'malformed_match'])
def test_invalid_iris_saved_composition_does_not_select_raw(confirmed, monkeypatch, change):
    e = confirmed; proof = e.proof
    if change == 'record_sha': proof['edition_record_sha256'] = '0' * 64
    elif change == 'source_id': proof['publisher_book_id'] = 'publisher-book:' + '0' * 24
    elif change == 'source_url': proof['match']['sources'][1] = 'https://www.ichijinsha.co.jp/iris/title/other/'
    elif change == 'version': proof['match']['rule_version'] = 'publisher-ndl-edition-confirmation-iris-neo-v99'
    elif change == 'rule': proof['match']['title_match']['rule'] = 'direct_own_complete_title'
    elif change == 'fields': proof['match']['title_match'].pop('main')
    elif change == 'roundtrip': proof['match']['title_match']['subtitle'] = '捏造副題'
    elif change == 'type': proof['match']['title_match']['volume'] = 3
    elif change == 'volume': proof['match']['title_match']['volume'] = '4'
    elif change == 'extra_field': proof['match']['title_match']['invented_alias'] = e.title
    elif change == 'primary_locator': proof['match']['primary_title_locator'] = 'other-resource/title[1]'
    elif change == 'malformed_match': proof['match'] = []
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with e.cat._connect() as db:
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
            (encoded, '0' * 64 if change == 'digest' else hashlib.sha256(encoded.encode()).hexdigest()))
    monkeypatch.setattr(e.cat, '_project', lambda *a: pytest.fail('invalid Iris composition must not select raw'))
    assert e.cat.search_catalogue(e.title)['total'] == 0


def test_iris_same_isbn_without_confirmation_does_not_supply_composed_titles(confirmed, monkeypatch):
    e = confirmed
    with e.cat._connect() as db: db.execute('DELETE FROM edition_book_confirmations')
    monkeypatch.setattr(e.cat, '_project', lambda *a: pytest.fail('unconfirmed composition must not select raw'))
    assert e.cat.search_catalogue(e.title)['total'] == 0


def test_iris_source_expiry_cannot_publish_saved_compositions(confirmed):
    e = confirmed; e.raw.write_text('{}', encoding='utf-8')
    assert e.cat.search_catalogue(e.title)['total'] == 0


def test_consistent_forged_iris_composition_still_requires_actual_source_match(confirmed):
    e = confirmed; proof = e.proof
    parts = proof['match']['title_match']
    parts['source_value'] = parts['source_value'].replace('完全副題', '捏造副題')
    parts['subtitle'] = '捏造副題'
    proof['match']['matched_ndl_title'] = parts['source_value']
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with e.cat._connect() as db:
        db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',
            (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    assert e.cat.search_catalogue(e.title.replace('完全副題', '捏造副題'))['total'] == 0
