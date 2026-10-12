import gzip
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from recommend_book.ingestion import RawStore, HTTPFetcher, iter_openlibrary_dump, write_jsonl
from recommend_book.ingestion.ndl import NDLAdapter
from recommend_book.ingestion.wikidata import WikidataAdapter
from recommend_book.ingestion.wikipedia import WikipediaAdapter


def test_raw_bytes_are_hash_preserved_and_deduplicated(tmp_path):
    store = RawStore(tmp_path)
    payload = b'{ "title": "raw" }\r\n'
    artifact = store.save(payload, source="test", url="https://example.org")
    assert Path(artifact.path).read_bytes() == payload
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert store.save(payload, source="test", url="https://example.org").path == artifact.path


def test_http_retry_and_explicit_failure(tmp_path):
    calls = []
    def response(req):
        calls.append(req)
        return httpx.Response(429 if len(calls) == 1 else 200, content=b'raw', headers={"Retry-After": "0"})
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(response)), min_interval=0, sleep=lambda _: None)
    result = fetcher.get("https://example.org", source="test")
    assert result.status == "success" and result.attempts == 2
    assert Path(result.artifact.path).read_bytes() == b'raw'
    bad = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503))), min_interval=0, sleep=lambda _: None)
    failed = bad.get("https://example.org", source="test")
    assert failed.status == "failed" and failed.attempts == 3


def test_openlibrary_stream_preserves_dump_line_and_edition_relationships(tmp_path):
    work = {"key": "/works/OL1W", "title": "Novel", "description": {"value": "copyright unresolved"}, "subjects": ["Fiction"], "authors": [{"author": {"key": "/authors/OL1A"}}]}
    edition = {"key": "/books/OL1M", "title": "Translated", "works": [{"key": "/works/OL1W"}], "isbn_13": ["9780140328721"], "publish_date": "1988", "languages": [{"key": "/languages/jpn"}]}
    lines = [f'/type/{kind}\t{item["key"]}\t1\t2026-01-01\t{json.dumps(item)}\n'.encode() for kind, item in [("work", work), ("edition", edition)]]
    path = tmp_path / "dump.gz"
    with gzip.open(path, "wb") as stream:
        stream.write(b"".join(lines))
    records = list(iter_openlibrary_dump(path, RawStore(tmp_path / "raw"), source_url="https://example.org/dump.gz"))
    assert len(records) == 2
    assert Path(records[0].raw_path).read_bytes() == lines[0]
    assert records[0].contributors[0].identifiers[0].value == "/authors/OL1A"
    assert not next(e for e in records[0].evidence if e.kind == "description").llm_input_allowed
    assert records[1].work_refs[0].value == "/works/OL1W" and records[1].publication_year == 1988
    assert records[1].original_language is None
    output = tmp_path / "records.jsonl"
    assert write_jsonl(records, output) == 2
    assert len(output.read_text().splitlines()) == 2


def test_wikidata_description_not_synopsis_and_relationship_fields(tmp_path):
    entity = {"id": "Q1", "labels": {"en": {"value": "Example novel", "language": "en"}}, "descriptions": {"en": {"value": "1900 novel", "language": "en"}}, "claims": {"P50": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q2"}}}}], "P577": [{"rank": "normal", "mainsnak": {"datavalue": {"value": {"time": "+1900-01-01T00:00:00Z", "precision": 9}}}}]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q1": entity, "Q2": {"id": "Q2", "labels": {"en": {"value": "Writer", "language": "en"}}}}}))), min_interval=0)
    result = WikidataAdapter(fetcher).fetch_entities(["Q1"])
    assert result.status == "complete"
    record = result.records[0]
    assert record.first_publication_year == 1900
    assert record.contributors[0].identifiers[0].value == "Q2"
    assert record.evidence[0].kind == "description" and record.evidence[0].llm_input_allowed


def test_wikidata_query_supports_verified_novel_subtypes(tmp_path):
    requests = []
    def respond(req):
        requests.append(req)
        return httpx.Response(200, json={"results": {"bindings": [{"work": {"value": "http://www.wikidata.org/entity/Q1"}}]}})
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(respond)), min_interval=0)
    ids, result = WikidataAdapter(fetcher).query_work_ids(limit=10, instance_qids=["Q8261", "Q747381", "Q49084"], language_qids=["Q5287"])
    assert ids == ["Q1"] and result.status == "complete"
    query = requests[0].url.params["query"]
    assert "wd:Q747381" in query and "wd:Q49084" in query and "wd:Q5287" in query
    assert "wdt:P7937" in query and "wdt:P31" in query
    assert "FILTER NOT EXISTS" in query and "wdt:P629" in query


def test_wikidata_linked_topics_and_characters_are_not_prose_invented(tmp_path):
    entity = {"id": "Q1", "labels": {"en": {"value": "Novel"}}, "claims": {"P921": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}], "P674": [{"mainsnak": {"datavalue": {"value": {"id": "Q3"}}}}], "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q747381"}}}}]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q1": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q1"], linked_labels={"Q2": "Revenge", "Q3": "Detective"}).records[0]
    assert record.work_kind == "light_novel"
    assert "Revenge" in record.subjects
    assert any("Detective" in e.text and e.locator == "claims.P674" for e in record.evidence)


def test_original_title_series_and_narrative_location_remain_distinct(tmp_path):
    def claim(value):
        return {"mainsnak": {"datavalue": {"value": value}}}
    entity = {"id": "Q1", "labels": {"en": {"value": "Translated title"}}, "claims": {"P1476": [claim({"text": "原題", "language": "ja"})], "P179": [claim({"id": "Q2"})], "P840": [claim({"id": "Q3"})]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q1": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q1"], linked_labels={"Q2": "Series A", "Q3": "Paris"}).records[0]
    assert any(t.kind == "original" and t.value == "原題" and t.language == "ja" for t in record.titles)
    assert record.series == "Series A" and record.extra["series_qids"] == ["Q2"]
    assert record.region == []
    assert any(e.locator == "claims.P840" and "Paris" in e.text for e in record.evidence)


def test_current_wikidata_literary_work_form_is_novel(tmp_path):
    entity = {"id": "Q170583", "labels": {"en": {"value": "Pride and Prejudice"}}, "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7725634"}}}}], "P7937": [{"mainsnak": {"datavalue": {"value": {"id": "Q8261"}}}}]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q170583": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q170583"]).records[0]
    assert record.work_kind == "novel"
    assert any("Q8261" in e.text and "P7937" in (e.locator or "") for e in record.evidence)


SRU = b'''<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>501</numberOfRecords><records><record><recordData><RDF xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"><Description about="https://ndlsearch.ndl.go.jp/books/R100000002-I000000001"><dc:title>Novel</dc:title><dc:creator>Writer</dc:creator><dcndl:materialType>Book</dcndl:materialType><dcterms:description>Note</dcterms:description><dcndl:dataProvider>iss-ndl-opac</dcndl:dataProvider></Description></RDF></recordData></record></records></searchRetrieveResponse>'''


def test_ndl_500_cutoff_and_provider_gate(tmp_path):
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=SRU))), min_interval=0)
    adapter = NDLAdapter(fetcher, approved_providers={"iss-ndl-opac"})
    result = adapter.search_sru('title="Novel"', provider="iss-ndl-opac", max_records=1)
    assert result.status == "truncated" and result.total_available == 501
    assert result.records[0].record_kind == "edition"
    assert result.records[0].provider_id == "iss-ndl-opac"
    assert not result.records[0].evidence[0].llm_input_allowed
    with pytest.raises(ValueError, match="provider"):
        adapter.search_sru('title="Novel"', provider="unreviewed")


def test_oai_resume_token_is_exact_and_persisted(tmp_path):
    requests = []
    pages = [b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords><resumptionToken>next+token/1</resumptionToken></ListRecords></OAI-PMH>', b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords><resumptionToken/></ListRecords></OAI-PMH>']
    def respond(req):
        requests.append(req)
        return httpx.Response(200, content=pages[len(requests)-1])
    fetcher = HTTPFetcher(RawStore(tmp_path / "raw"), client=httpx.Client(transport=httpx.MockTransport(respond)), min_interval=0)
    adapter = NDLAdapter(fetcher, approved_providers={"iss-ndl-opac"})
    checkpoint = tmp_path / "checkpoint.json"
    first = adapter.harvest_oai(provider="iss-ndl-opac", checkpoint=checkpoint, max_pages=1)
    assert first.status == "truncated" and first.next_token == "next+token/1"
    second = adapter.harvest_oai(provider="iss-ndl-opac", checkpoint=checkpoint, max_pages=1)
    assert second.status == "complete"
    assert requests[1].url.params["resumptionToken"] == "next+token/1"
    assert "set" not in requests[1].url.params


def test_wikipedia_exact_revision_optin_and_text_license_inheritance(tmp_path):
    payload = {"query": {"pages": [{"pageid": 1, "title": "Example", "revisions": [{"revid": 123, "timestamp": "2026-01-01T00:00:00Z", "slots": {"main": {"content": "Lead.\n== Plot ==\nReviewed plot text.\n== Reception ==\nReview."}}}]}]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))), min_interval=0)
    with pytest.raises(ValueError, match="opt-in"):
        WikipediaAdapter(fetcher).fetch_revision("en", 123, work_id="Q1", section="Plot")
    result = WikipediaAdapter(fetcher, enabled=True).fetch_revision("en", 123, work_id="Q1", section="Plot", reviewed_no_third_party_text=True, allow_licensed_llm=True)
    record = result.records[0]
    assert record.evidence[0].text == "Reviewed plot text."
    assert record.evidence[0].locator == "revision:123;section:Plot"
    assert "oldid=123" in record.evidence[0].source_url
    assert record.evidence[0].rights_policy_id == "wikipedia-text"
    assert record.evidence[0].llm_input_allowed
    assert record.extra["text_license"] == "CC-BY-SA-4.0"
