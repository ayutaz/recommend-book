import httpx
import pytest

from recommend_book.ingestion import HTTPFetcher, RawStore
from recommend_book.ingestion.ndl import NDLAdapter, assess_sru_record


STRUCTURED = '''<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
<numberOfRecords>1</numberOfRecords><records><record><recordData>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcndl="http://ndl.go.jp/dcndl/terms/" xmlns:foaf="http://xmlns.com/foaf/0.1/">
<dcndl:BibAdminResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I000000123"><dcterms:creator><foaf:Agent><foaf:name>National Diet Library</foaf:name></foaf:Agent></dcterms:creator></dcndl:BibAdminResource>
<dcndl:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I000000123#material">
<dcterms:title>こころ</dcterms:title><dc:title><rdf:Description><rdf:value>こころ</rdf:value><dcndl:transcription>ココロ</dcndl:transcription></rdf:Description></dc:title>
<dc:creator><foaf:Agent rdf:about="http://id.ndl.go.jp/auth/ndlna/00054222"><foaf:name>夏目, 漱石, 1867-1916</foaf:name><dcndl:transcription>ナツメ, ソウセキ</dcndl:transcription></foaf:Agent></dc:creator>
<dcterms:creator>夏目漱石 著</dcterms:creator>
<dcterms:contributor>田中一郎 訳</dcterms:contributor>
<dcterms:publisher><foaf:Agent><foaf:name>新潮社</foaf:name><dcndl:transcription>シンチョウシャ</dcndl:transcription></foaf:Agent></dcterms:publisher>
<dcterms:subject rdf:resource="http://id.ndl.go.jp/class/ndc9/913.6"/>
<dcterms:subject><rdf:Description><rdf:value>913.6</rdf:value></rdf:Description></dcterms:subject>
<dcterms:identifier rdf:datatype="http://ndl.go.jp/dcndl/terms/ISBN">9784101010137</dcterms:identifier>
<dcterms:issued>2024</dcterms:issued><dcterms:language rdf:datatype="http://purl.org/dc/terms/ISO639-2">jpn</dcterms:language>
<dcndl:materialType rdf:resource="http://ndl.go.jp/ndltype/Book"/>
</dcndl:BibResource></rdf:RDF></recordData></record></records></searchRetrieveResponse>'''


def _adapter(tmp_path, content=STRUCTURED):
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=content.encode()))), min_interval=0)
    return NDLAdapter(fetcher, approved_providers={"iss-ndl-opac"})


def test_structured_title_reading_and_administrative_author_do_not_leak(tmp_path):
    result = _adapter(tmp_path).search_sru('ndc="913.6"', provider="iss-ndl-opac")
    assert result.status == "complete"
    record = result.records[0]
    assert [t.value for t in record.titles] == ["こころ"]
    assert record.publisher == "新潮社"
    assert not any("National Diet Library" in c.name or "ナツメ" in c.name for c in record.contributors)
    assert any(c.role == "author" and c.name == "夏目, 漱石, 1867-1916" for c in record.contributors)
    assert any(c.role == "translator" and c.name == "田中一郎" for c in record.contributors)
    assert record.source_id == "R100000002-I000000123"
    assert "#material" not in record.source_url
    assert record.extra["ndc_codes"] == ["913.6"]
    assert record.extra["title_readings"] == ["ココロ"]
    assert assess_sru_record(record, ndc_prefix="913.6", publication_year=2024, title_contains="こころ", language="jpn")["accepted"]


def test_sru_false_matches_quarantined_against_returned_metadata(tmp_path):
    record = _adapter(tmp_path).search_sru('title="Kokoro"', provider="iss-ndl-opac").records[0]
    decision = assess_sru_record(record, title_contains="Kokoro", ndc_prefix="933", publication_year=2023)
    assert not decision["accepted"]
    assert set(decision["reasons"]) == {"title_not_matched", "ndc_not_matched", "publication_year_not_matched"}


def test_isbn_query_can_match_valid_isbn10_equivalent(tmp_path):
    payload = STRUCTURED.replace("9784101010137", "4-10-101013-7")
    record = _adapter(tmp_path, payload).search_sru('isbn="9784101010137"', provider="iss-ndl-opac").records[0]
    assert assess_sru_record(record, isbn="9784101010137")["accepted"]
    assert not assess_sru_record(record, isbn="9784101010138")["accepted"]


def test_sru_provider_overscope_and_or_queries_rejected_before_network(tmp_path):
    adapter = _adapter(tmp_path)
    with pytest.raises(ValueError, match="provider"):
        adapter.search_sru('title="A" OR dpid="jpro"', provider="iss-ndl-opac")
    with pytest.raises(ValueError, match="AND"):
        adapter.search_sru('title="A" OR title="B"', provider="iss-ndl-opac")


def test_sru_conjunction_uses_supported_flat_cql_without_parentheses(tmp_path):
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, content=STRUCTURED.encode())
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(respond)), min_interval=0)
    adapter = NDLAdapter(fetcher)
    adapter.search_sru('ndc="913.6" AND from="2024"', provider="iss-ndl-opac")
    assert requests[0].url.params["query"] == 'ndc="913.6" AND from="2024" AND dpid="iss-ndl-opac"'


def test_empty_provider_approval_never_enables_default_provider(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.approved_providers = set()
    with pytest.raises(ValueError, match="provider"):
        adapter.search_sru('title="A"', provider="iss-ndl-opac")


def test_record_no_longer_implicitly_assumes_other_repository_provider(tmp_path):
    other = STRUCTURED.replace("R100000002-I000000123", "R100000137-I000000123")
    result = _adapter(tmp_path, other).search_sru('title="A"', provider="iss-ndl-opac")
    assert result.status == "failed" and "repository" in result.errors[0]
