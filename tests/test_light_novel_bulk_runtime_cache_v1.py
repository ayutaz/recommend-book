"""Real bulk-verifier checks around the bounded parsed-document cache."""
import hashlib
import json
from pathlib import Path

import pytest

from recommend_book import light_novel_ndl_bulk_v1 as bulk
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue


def bundle(tmp_path):
    raw = tmp_path / 'bulk.xml'
    def doc(n):
        url = f'https://ndlsearch.ndl.go.jp/books/R100000002-I{n}'
        return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
          xmlns:n="http://ndl.go.jp/dcndl/terms/" xmlns:d="http://purl.org/dc/terms/">
          <n:BibAdminResource rdf:about="{url}"><n:bibRecordCategory>R100000002</n:bibRecordCategory>
          <n:record rdf:resource="{url}#material"/></n:BibAdminResource>
          <n:BibResource rdf:about="{url}#material"><d:title>日本語小説{n}</d:title></n:BibResource></rdf:RDF>'''
    raw.write_bytes((doc(1) + doc(2)).encode())
    receipt = {'version': 'ndl-official-bulk-ui-probe-v2', 'source_provider': 'iss-ndl-opac-national',
        'retrieved_at': '2026-10-26T00:00:00Z',
        'conditions_path': 'fixture-conditions.json', 'conditions_sha256': '0' * 64,
        'source_url': 'https://ndlsearch.ndl.go.jp/bib?cs=marc&keyword=test',
        'official_help': 'https://ndlsearch.ndl.go.jp/bib/help',
        **dict.fromkeys(('ui_total', 'documents', 'admin_records', 'bibliographic_records', 'unique_ids'), 2),
        'bytes': raw.stat().st_size, 'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'repository_ids': ['R100000002'], 'all_count_match': True, 'maximum_selected': 5000}
    path = tmp_path / 'receipt.json'; path.write_text(json.dumps(receipt), encoding='utf-8')
    proof = {'receipt_path': str(path), 'receipt_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return LightNovelCatalogue(tmp_path / 'catalogue.sqlite'), proof, path, raw


def count_replays(monkeypatch):
    calls = []; original = bulk.verify_bulk_receipt
    def observed(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(bulk, 'verify_bulk_receipt', observed)
    return calls


def test_reuses_parsing_but_returns_independent_nodes_and_keeps_all_indices(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path); calls = count_replays(monkeypatch)
    cache = {}
    _, first = cat._national_document(proof, 'R100000002-I1', cache)
    _, second = cat._national_document(proof, 'R100000002-I2', cache)
    assert (first['index'], second['index']) == (0, 1)
    first['node'].clear()
    _, again = cat._national_document(proof, 'R100000002-I1', {})
    assert '日本語小説1' in ''.join(again['node'].itertext())
    assert len(calls) == 1


@pytest.mark.parametrize('changed', ['receipt', 'raw'])
def test_new_request_rehashes_pins_even_on_cache_hit(tmp_path, monkeypatch, changed):
    cat, proof, receipt, raw = bundle(tmp_path); count_replays(monkeypatch)
    cat._national_document(proof, 'R100000002-I1', {})
    (receipt if changed == 'receipt' else raw).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA mismatch'):
        cat._national_document(proof, 'R100000002-I1', {})


def test_new_document_misses_cached_subset_and_replays_full_receipt(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path); calls = count_replays(monkeypatch)
    cat._national_document(proof, 'R100000002-I1', {})
    _, second = cat._national_document(proof, 'R100000002-I2', {})
    assert second['index'] == 1 and len(calls) == 2
    with pytest.raises(ValueError, match='identity absent'):
        cat._national_document(proof, 'R100000002-ABSENT', {})


def test_cache_is_bounded_and_isolated_between_catalogues(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path); calls = count_replays(monkeypatch)
    monkeypatch.setattr(cat, '_bulk_node_limit', 1)
    cat._national_document(proof, 'R100000002-I1', {})
    cat._national_document(proof, 'R100000002-I2', {})
    cat._national_document(proof, 'R100000002-I1', {})
    other = LightNovelCatalogue(tmp_path / 'other.sqlite')
    other._national_document(proof, 'R100000002-I1', {})
    assert len(calls) == 4


def test_verifier_change_invalidates_parsed_document_cache(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path)
    cat._national_document(proof, 'R100000002-I1', {})
    def changed(*args):
        raise ValueError('new verifier refuses receipt')
    monkeypatch.setattr(bulk, 'verify_bulk_receipt', changed)
    with pytest.raises(ValueError, match='new verifier'):
        cat._national_document(proof, 'R100000002-I1', {})


def test_code_file_change_replays_before_reusing_node(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path); calls = count_replays(monkeypatch)
    cat._national_document(proof, 'R100000002-I1', {})
    authority = tmp_path / 'bulk-authority.py'
    authority.write_bytes(Path(bulk.__file__).read_bytes() + b'\n# different authority\n')
    monkeypatch.setattr(bulk, '__file__', str(authority))
    cat._national_document(proof, 'R100000002-I1', {})
    assert len(calls) == 2


def test_changed_receipt_pin_does_not_bypass_full_admission(tmp_path):
    cat, proof, receipt, _ = bundle(tmp_path)
    cat._national_document(proof, 'R100000002-I1', {})
    invalid = json.loads(receipt.read_text(encoding='utf-8')); invalid['ui_total'] = 3
    receipt.write_text(json.dumps(invalid), encoding='utf-8')
    proof['receipt_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='counts'):
        cat._national_document(proof, 'R100000002-I1', {})


def test_oversized_nodes_are_not_retained(tmp_path, monkeypatch):
    cat, proof, _, _ = bundle(tmp_path); calls = count_replays(monkeypatch)
    monkeypatch.setattr(cat, '_bulk_node_bytes_limit', 1)
    cat._national_document(proof, 'R100000002-I1', {})
    cat._national_document(proof, 'R100000002-I1', {})
    assert len(calls) == 2


@pytest.mark.parametrize('changed', ['document_index', 'conditions_sha256', 'raw_sha256'])
def test_cached_node_keeps_per_edition_index_and_provenance_checks(tmp_path, monkeypatch, changed):
    cat, proof, path, _ = bundle(tmp_path)
    receipt, document = cat._national_document(proof, 'R100000002-I1', {})
    record = bulk.bulk_metadata_records(path, proof['receipt_sha256'], receipt, [document])[0]
    # Source-policy validation is separate; this checks the edition binding
    # guards after an authentic national document has already warmed the cache.
    monkeypatch.setattr(cat, '_conditions', lambda *args, **kwargs: {})
    assert cat._project_edition(record, {})['entry_id'] == 'lne:ndl:R100000002-I1'
    record['provenance'][changed] = 1 if changed == 'document_index' else 'f' * 64
    with pytest.raises(ValueError):
        cat._project_edition(record, {})
