import pytest

from recommend_book.light_novel_ndl_bulk_v1 import parse_bulk_documents


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
])
def test_ambiguous_foreign_unlinked_or_unparsed_bulk_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_bulk_documents(raw.encode())
