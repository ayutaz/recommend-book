import copy
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from recommend_book.ingestion import HTTPFetcher, RawStore
from recommend_book.ingestion.wikidata import WikidataAdapter


def statement(text, language, *, rank="normal", statement_id="Q1044713$title", **extra):
    return {"id": statement_id, "rank": rank, "mainsnak": {
        "snaktype": "value", "datavalue": {"value": {"text": text, "language": language}}}, **extra}


@pytest.mark.parametrize("kind", ["work", "edition"])
def test_multilingual_published_titles_keep_label_and_exact_source_assertions(tmp_path, kind):
    claims = [statement("Taken at the Flood", "en"),
              statement("Arrastado na Torrente", "pt", statement_id="Q1044713$pt",
                        qualifiers={"P3005": [{"mainsnak": {"datavalue": {"value": {"id": "Q45"}}}}]},
                        references=[{"snaks": {"P957": [{"datavalue": {"value": "972-38-1320-3"}}]}}])]
    entity = {"id": "Q1044713", "labels": {"en": {"value": "Taken at the Flood", "language": "en"}},
              "aliases": {"en": [{"value": "There is a Tide"}]}, "claims": {"P1476": claims}}
    original = copy.deepcopy(entity)
    raw = json.dumps({"entities": {"Q1044713": entity}}, ensure_ascii=False).encode("utf-8")
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=raw))), min_interval=0)
    result = WikidataAdapter(fetcher).fetch_entities(["Q1044713"], kind=kind)
    record = result.records[0]
    assert [(t.value, t.language, t.kind) for t in record.titles] == [
        ("Taken at the Flood", "en", "preferred"),
        ("Taken at the Flood", "en", "alternative"),
        ("Arrastado na Torrente", "pt", "alternative"),
        ("There is a Tide", "en", "alternative")]
    assert record.original_language is None
    assert record.extra["claims"]["P1476"] == claims
    assert Path(record.raw_path).read_bytes() == raw
    assert record.raw_sha256 == hashlib.sha256(raw).hexdigest()
    for index, proof in enumerate(record.extra["title_assertions"]):
        assert proof["statement"] == claims[index]
        assert proof["locator"] == f"claims.P1476[{index}]"
        assert proof["title_index"] == index + 1
        assert proof["title_kind"] == "alternative"
        assert proof["raw_path"] == record.raw_path
        assert proof["raw_sha256"] == record.raw_sha256
        assert proof["source_url"] == record.source_url
        assert proof["source_id"] == record.source_id
        assert proof["record_kind"] == kind
    assert entity == original


def test_normal_titles_are_retained_beside_preferred_and_deprecated_unknown_are_auditable(tmp_path):
    claims = [statement("  Preferred title  ", "en", rank="preferred"),
              statement("Titre publié", "fr"),
              statement("Deprecated", "de", rank="deprecated"),
              {"id": "Q1$unknown", "rank": "normal", "mainsnak": {"snaktype": "somevalue"}}]
    entity = {"id": "Q1", "labels": {"en": {"value": "Label", "language": "en"}},
              "claims": {"P1476": claims, "P407": [{"mainsnak": {"datavalue": {"value": {"id": "Q1860"}}}}]}}
    fetcher = HTTPFetcher(RawStore(tmp_path), client=httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"entities": {"Q1": entity}}))), min_interval=0)
    record = WikidataAdapter(fetcher).fetch_entities(["Q1"]).records[0]
    assert [(t.value, t.kind) for t in record.titles] == [
        ("Label", "preferred"), ("  Preferred title  ", "alternative"), ("Titre publié", "alternative")]
    assert record.original_language == "en"
    assert [p["statement"] for p in record.extra["title_assertions"]] == claims
    assert [p["title_index"] for p in record.extra["title_assertions"]] == [1, 2, None, None]
    assert all(p["title_kind"] is None for p in record.extra["title_assertions"][2:])
