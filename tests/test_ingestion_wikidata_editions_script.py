from pathlib import Path
import runpy

from recommend_book.ingestion import CollectionResult, write_jsonl
from recommend_book.models import Identifier, SourceRecord, Title


def _record(qid, kind="work", work=None):
    return SourceRecord(source="wikidata", source_id=qid, source_url=f"https://www.wikidata.org/wiki/{qid}", retrieved_at="2026-10-12", provider_id="wikidata", record_kind=kind, titles=[Title(value=qid)], identifiers=[Identifier(source="wikidata", value=qid, scope=kind)], work_refs=[Identifier(source="wikidata", value=work, scope="work")] if work else [], raw_sha256="a"*64, raw_path="raw.bin", rights_policy_id="wikidata-cc0")


def test_fixed_work_frame_editions_cap_is_not_reported_as_added_works(tmp_path):
    api = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "collect_wikidata_editions.py"))
    frame = tmp_path / "frame.jsonl"
    write_jsonl([_record("Q1"), _record("Q2"), _record("Q3")], frame)
    class Adapter:
        def query_edition_ids(self, work_ids, **kwargs):
            return ["Q100", "Q101"], CollectionResult(status="truncated", metadata={"relations": [{"edition":"Q100","work":"Q1"},{"edition":"Q101","work":"Q2"}], "relation_rows":2,"raw_path":"query.json","raw_sha256":"b"*64,"next_offset":2})
        def fetch_entities(self, ids, **kwargs):
            assert kwargs["kind"] == "edition"
            return CollectionResult(records=[_record("Q100", "edition", "Q1"), _record("Q101", "edition", "Q2")])
    result = api["collect"](frame, tmp_path / "out", limit=2, adapter=Adapter())
    assert result["status"] == "truncated" and result["accepted_edition_records"] == 2
    assert result["added_work_count"] == 0
    assert result["frame_work_count"] == 3
    assert result["author_metadata_not_copied_from_works"]
    assert result["editions_without_author_claim"] == 2


def test_out_of_frame_relation_is_quarantined(tmp_path):
    api = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "collect_wikidata_editions.py"))
    frame = tmp_path / "frame.jsonl"
    write_jsonl([_record("Q1")], frame)
    class Adapter:
        def query_edition_ids(self, work_ids, **kwargs):
            return ["Q100"], CollectionResult(metadata={"relations":[{"edition":"Q100","work":"Q1"}],"relation_rows":1,"next_offset":1})
        def fetch_entities(self, ids, **kwargs):
            return CollectionResult(records=[_record("Q100", "edition", "Q99")])
    result = api["collect"](frame, tmp_path / "out", limit=10, adapter=Adapter())
    assert result["accepted_edition_records"] == 0 and result["quarantined_edition_records"] == 1
    assert result["added_work_count"] == 0
