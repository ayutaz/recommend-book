import hashlib
import json

import pytest

from recommend_book.light_novel_ndl_bulk_v1 import parse_bulk_documents, verify_bulk_receipt


def document(identity='R100000002-I1', extra=''):
    return f'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:n="http://ndl.go.jp/dcndl/terms/" xmlns:d="http://purl.org/dc/terms/">
      <n:BibAdminResource rdf:about="https://ndlsearch.ndl.go.jp/books/{identity}">
       <n:bibRecordCategory>R100000002</n:bibRecordCategory>
       <n:record rdf:resource="https://ndlsearch.ndl.go.jp/books/{identity}#material"/>
      </n:BibAdminResource>
      <n:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/{identity}#material">
       <d:title>日本語小説</d:title><d:publisher>出版社</d:publisher></n:BibResource>
      <n:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000001-OTHER">
       <d:title>他館の書名</d:title><d:publisher>他館</d:publisher></n:BibResource>{extra}</rdf:RDF>'''


def test_concatenated_roots_only_project_admin_linked_own_bibliography():
    raw = ('<?xml version="1.0" encoding="UTF-8"?>' + document() + document('R100000002-I2')).encode()
    rows = parse_bulk_documents(raw)
    assert [r['source_id'] for r in rows] == ['R100000002-I1', 'R100000002-I2']
    assert [r['index'] for r in rows] == [0, 1]
    assert all('他館の書名' not in ''.join(r['node'].itertext()) for r in rows)


@pytest.mark.parametrize('raw', [
    document()+document(),
    document().replace('R100000002-I1#material"/>', 'R100000002-OTHER#material"/>'),
    document().replace('<n:bibRecordCategory>R100000002', '<n:bibRecordCategory>R100000001'),
    document()+'unparsed garbage',
    document(extra='<n:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I1#material"><d:title>別主書誌</d:title></n:BibResource>'),
    document().replace('</n:BibResource>', '<d:relation><n:BibResource><d:title>埋込他館書誌</d:title></n:BibResource></d:relation></n:BibResource>', 1),
])
def test_ambiguous_foreign_unlinked_or_unparsed_bulk_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_bulk_documents(raw.encode())


def test_receipt_counts_scope_and_source_hash_are_replayed(tmp_path):
    raw = tmp_path / 'raw.xml'
    raw.write_bytes(document().encode())
    receipt = {'version': 'ndl-official-bulk-ui-probe-v2', 'source_provider': 'iss-ndl-opac-national',
        'source_url': 'https://ndlsearch.ndl.go.jp/bib?cs=marc&keyword=test',
        'official_help': 'https://ndlsearch.ndl.go.jp/bib/help', 'ui_total': 1, 'documents': 1,
        'admin_records': 1, 'bibliographic_records': 1, 'unique_ids': 1, 'bytes': raw.stat().st_size,
        'raw_path': str(raw), 'raw_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
        'repository_ids': ['R100000002'], 'all_count_match': True, 'maximum_selected': 5000}
    path = tmp_path / 'receipt.json'
    def artifact(filename, expected, cache):
        data = __import__('pathlib').Path(filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('SHA mismatch')
        return data
    def invoke():
        path.write_text(json.dumps(receipt))
        return verify_bulk_receipt(path, hashlib.sha256(path.read_bytes()).hexdigest(), artifact, {})
    assert len(invoke()[1]) == 1
    receipt['ui_total'] = 2
    with pytest.raises(ValueError):
        invoke()
    receipt['ui_total'] = 1
    receipt['source_provider'] = 'iss-ndl-opac'
    with pytest.raises(ValueError):
        invoke()
    receipt['source_provider'] = 'iss-ndl-opac-national'
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError):
        invoke()
