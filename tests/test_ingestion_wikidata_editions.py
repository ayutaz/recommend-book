import httpx

from recommend_book.ingestion import HTTPFetcher, RawStore
from recommend_book.ingestion.wikidata import WikidataAdapter


def _claim(value):
    return {"mainsnak": {"datavalue": {"value": value}}}


def test_edition_isbn_translation_and_work_relation_have_distinct_scope(tmp_path):
    entity = {"id": "Q100", "labels": {"en": {"value": "1990 translated edition", "language": "en"}}, "claims": {"P629": [_claim({"id": "Q1"})], "P212": [_claim("9780140328721")], "P957": [_claim("0140328726")], "P655": [_claim({"id": "Q2"})], "P407": [_claim({"id": "Q5287"})], "P1476": [_claim({"text": "Translated title", "language": "ja"})], "P577": [_claim({"time": "+1990-01-01T00:00:00Z", "precision": 9})]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q100": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q100"], kind="edition", linked_labels={"Q2": "Translator"}).records[0]
    assert record.record_kind == "edition"
    assert record.identifiers[0].value == "Q100" and record.identifiers[0].scope == "edition"
    assert record.work_refs[0].value == "Q1" and record.work_refs[0].scope == "work"
    assert {i.value for i in record.identifiers if i.source == "isbn"} == {"9780140328721", "0140328726"}
    assert all(i.scope == "edition" for i in record.identifiers)
    assert record.contributors[0].role == "translator" and record.contributors[0].name == "Translator"
    assert not any(c.role == "author" for c in record.contributors)
    assert record.language == "ja" and record.original_language is None
    assert record.publication_year == 1990 and record.first_publication_year is None
    assert record.titles[0].value == "Translated title" and record.titles[0].kind == "preferred"
    assert not any(t.kind == "original" for t in record.titles)
    assert record.extra["relationship_basis"] == "P629 version, edition or translation of"


def test_work_record_does_not_inherit_isbn_of_manifestations(tmp_path):
    entity = {"id": "Q1", "labels": {"en": {"value": "Work"}}, "claims": {"P212": [_claim("9780140328721")]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"entities": {"Q1": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q1"], kind="work").records[0]
    assert not any(i.source == "isbn" for i in record.identifiers)


def test_edition_query_is_bounded_to_target_works_with_raw_provenance(tmp_path):
    requests = []
    def response(req):
        requests.append(req)
        return httpx.Response(200, json={"results": {"bindings": [{"edition": {"value": "http://www.wikidata.org/entity/Q100"}, "work": {"value": "http://www.wikidata.org/entity/Q1"}}]}})
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(response)), min_interval=0)
    ids, result = WikidataAdapter(fetcher).query_edition_ids(["Q1", "Q2"], limit=1)
    assert ids == ["Q100"] and result.status == "truncated"
    query = requests[0].url.params["query"]
    assert "VALUES ?work { wd:Q1 wd:Q2 }" in query and "wdt:P629" in query and "LIMIT 1" in query
    assert result.metadata["relations"] == [{"edition": "Q100", "work": "Q1"}]
    assert result.metadata["raw_path"] and len(result.metadata["raw_sha256"]) == 64
