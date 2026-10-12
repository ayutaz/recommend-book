import json
from pathlib import Path
import runpy

from recommend_book.ingestion import CollectionResult
from recommend_book.models import SourceRecord, Title


def test_collector_counts_returns_rejects_and_distinct_editions_separately(tmp_path):
    collect = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "collect_ndl_poc.py"))["collect"]
    good = SourceRecord(source="ndl", source_id="R100000002-I1", source_url="https://ndlsearch.ndl.go.jp/books/R100000002-I1", retrieved_at="2026-10-12", provider_id="iss-ndl-opac", record_kind="edition", titles=[Title(value="Novel")], language="jpn", publication_year=2024, raw_sha256="a" * 64, raw_path="raw.bin", rights_policy_id="ndl-iss-ndl-opac", extra={"ndc_codes": ["913.6"]})
    bad = good.model_copy(update={"source_id": "R100000002-I2", "publication_year": 2023}, deep=True)
    class Adapter:
        def search_sru(self, query, **kwargs):
            return CollectionResult(records=[good.model_copy(deep=True), bad.model_copy(deep=True)], status="truncated", total_available=900)
    spec = {"name": "novels", "query": 'ndc="913.6"', "constraints": {"ndc_prefix": "913.6", "publication_year": 2024}}
    result = collect(tmp_path, limit_per_query=2, specs=[spec, {**spec, "name": "duplicate-query"}], adapter=Adapter())
    assert result["status"] == "truncated"
    assert result["accepted_unique_editions"] == 1
    assert result["returned_records"] == 4 and result["rejected_records"] == 2
    assert result["queries"][0]["total_available"] == 900
    records = [json.loads(s) for s in (tmp_path / "records.jsonl").read_text().splitlines()]
    assert len(records[0]["extra"]["poc_selection"]) == 2
    assert "not resolved works" in result["deduplication_unit"]


def test_saved_snapshot_can_be_reassessed_without_network_or_new_retrieval_time(tmp_path):
    api = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "collect_ndl_poc.py"))
    record = SourceRecord(source="ndl", source_id="R100000002-I1", source_url="https://ndlsearch.ndl.go.jp/books/R100000002-I1", retrieved_at="2026-10-01", provider_id="iss-ndl-opac", record_kind="edition", titles=[Title(value="Novel")], language="jpn", publication_year=2024, raw_sha256="a" * 64, raw_path="raw.bin", rights_policy_id="ndl-iss-ndl-opac", extra={"ndc_codes": ["913.6"]})
    class Adapter:
        def search_sru(self, query, **kwargs):
            return CollectionResult(records=[record], status="complete", total_available=1)
    spec = {"name": "novels", "query": 'ndc="913.6"', "constraints": {"ndc_prefix": "913.6"}}
    first = api["collect"](tmp_path, limit_per_query=1, specs=[spec], adapter=Adapter())
    second = api["reassess_saved_snapshot"](tmp_path)
    assert second["acquisition_mode"] == "reassess_saved_snapshot"
    assert second["source_snapshot_created_at"] == first["created_at"]
    saved = json.loads((tmp_path / "records.jsonl").read_text())
    assert saved["retrieved_at"] == "2026-10-01" and saved["raw_sha256"] == "a" * 64
    assert len(saved["extra"]["poc_selection"]) == 1
