from __future__ import annotations

import json
import importlib.util
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "collect_scale_wikidata_v3.py"
_SPEC = importlib.util.spec_from_file_location("collect_scale_wikidata_v3", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
_load_state = _MODULE._load_state
_parse_bindings = _MODULE._parse_bindings
discover_page = _MODULE.discover_page
source_declared_query = _MODULE.source_declared_query
catalogue_writer_lock = _MODULE.catalogue_writer_lock
ingest_records = _MODULE._ingest_records
resume_candidates = _MODULE.resume_candidates
load_pending_records = _MODULE.load_pending_records


def _artifact(tmp_path: Path):
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"fixture")
    return SimpleNamespace(path=str(raw), sha256="a" * 64)


class FakeFetcher:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.result


def test_source_declared_query_preserves_work_scope_and_period_partition():
    query = source_declared_query(limit=500, offset=1000, start_year=1900, end_year=1925)
    assert "wd:Q8261" in query and "wd:Q747381" in query
    assert "wdt:P7937" in query and "wdt:P31" in query
    assert "wdt:P629" in query and "wd:Q3331189" in query
    assert "1900-01-01T00:00:00Z" in query
    assert "1925-01-01T00:00:00Z" in query
    assert "LIMIT 500 OFFSET 1000" in query


def test_discovery_held_page_is_never_requested_again(tmp_path):
    output = tmp_path / "acquisition"
    state = {"frame": {"version": "scale-discovery-v3"}, "pages": {}, "ids": []}
    failed = SimpleNamespace(status="failed", error="HTTP 429", status_code=429,
                             attempts=1, artifact=_artifact(tmp_path))
    fetcher = FakeFetcher(failed)
    fresh, ok = discover_page(output=output, fetcher=fetcher, state=state,
                              key="period:1900-1925:0", query="SELECT", exclusions=set())
    assert fresh == [] and not ok and fetcher.calls == 1
    assert state["pages"]["period:1900-1925:0"]["status"] == "held"

    success = SimpleNamespace(status="success", content=json.dumps({"results": {"bindings": [
        {"work": {"value": "http://www.wikidata.org/entity/Q101"}}
    ]}}).encode(), artifact=_artifact(tmp_path))
    fetcher.result = success
    fresh, ok = discover_page(output=output, fetcher=fetcher, state=state,
                              key="period:1900-1925:0", query="SELECT", exclusions=set())
    assert fresh == [] and not ok and fetcher.calls == 1


def test_discovery_filters_all_old_ids_and_keeps_new_ids(tmp_path):
    output = tmp_path / "acquisition"
    state = {"frame": {"version": "scale-discovery-v3"}, "pages": {}, "ids": []}
    response = SimpleNamespace(status="success", content=json.dumps({"results": {"bindings": [
        {"work": {"value": "http://www.wikidata.org/entity/Q101"}},
        {"work": {"value": "http://www.wikidata.org/entity/Q102"}},
    ]}}).encode(), artifact=_artifact(tmp_path))
    fetcher = FakeFetcher(response)
    fresh, ok = discover_page(output=output, fetcher=fetcher, state=state,
                              key="period:1900-1925:0", query="SELECT", exclusions={"Q101"})
    assert ok and fresh == ["Q102"]
    assert state["ids"] == ["Q102"]


def test_malformed_sparql_payload_is_held():
    with pytest.raises(ValueError, match="no results object"):
        _parse_bindings(b"[]")
    with pytest.raises(ValueError, match="no bindings list"):
        _parse_bindings(b'{"results": {}}')


def test_writer_wait_timeout_keeps_other_owner_lock(tmp_path):
    catalogue = tmp_path / "db" / "catalogue.sqlite"
    lock = catalogue.parent.parent / "catalogue-writer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"pid": 123, "owner": "native"}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="wait expired"):
        with catalogue_writer_lock(catalogue, wait_seconds=0.01, poll_seconds=0.001):
            pass
    assert lock.exists()


def test_incremental_ingest_is_idempotent_on_resume(tmp_path, monkeypatch):
    db_path = tmp_path / "db" / "catalogue.sqlite"
    db_path.parent.mkdir()
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    with sqlite3.connect(db_path) as db:
        db.executescript("""
        create table source_versions(version_id text primary key,qid text,record_sha256 text,record_json text,raw_path text,raw_sha256 text,retrieved_at text,conditions_sha256 text);
        create table works(qid text primary key,version_id text,title text,search_text text,language text,year integer,recommendable integer,display_allowed integer,row_json text);
        create table work_genres(qid text,genre_qid text,label text,primary key(qid,genre_qid));
        create table raw_artifacts(path text primary key,sha256 text);
        """)

    class FakeCatalogue:
        def __init__(self, path):
            self.path = path

        def _conditions(self):
            return {}, {"display": "allowed"}

        def _projection(self, record, conditions, rights):
            normalized = SimpleNamespace(
                model_dump=lambda mode=None: {"source_id": record.source_id},
                raw_path="/tmp/raw", raw_sha256="a" * 64, retrieved_at="2026-10-21T00:00:00+00:00",
            )
            return normalized, {"provenance": {"artifacts": []}, "aliases": [], "title": "Novel",
                                "authors": [], "original_language": None, "year": None,
                                "recommendable": True, "genres": []}

    monkeypatch.setattr(_MODULE, "ScaleCatalogue", FakeCatalogue)
    record = SimpleNamespace(source_id="Q101")
    first = ingest_records(output=tmp_path, catalogue_path=db_path, records=[record], conditions_path=conditions)
    second = ingest_records(output=tmp_path, catalogue_path=db_path, records=[record], conditions_path=conditions)
    assert first["inserted"] == 1 and second["inserted"] == 0
    with sqlite3.connect(db_path) as db:
        assert db.execute("select count(*) from works").fetchone()[0] == 1


def test_restart_resumes_selected_unattempted_ids_and_source_records(tmp_path):
    selected = ["Q101", "Q102", "Q103"]
    assert resume_candidates(selected, attempted={"Q101"}, held={"Q103"}, exclusions=set()) == ["Q102"]
    path = tmp_path / "pending-records.jsonl"
    path.write_text(json.dumps({"source": "wikidata", "provider_id": "wikidata",
                                "source_id": "Q102", "source_url": "https://www.wikidata.org/wiki/Q102",
                                "retrieved_at": "2026-10-21T00:00:00+00:00", "record_kind": "work",
                                "titles": [], "contributors": [], "identifiers": [], "work_kind": "novel",
                                "evidence": [], "raw_sha256": "a" * 64, "raw_path": str(tmp_path / "raw"),
                                "rights_policy_id": "wikidata-cc0"}) + "\n", encoding="utf-8")
    records = load_pending_records(path)
    assert len(records) == 1 and records[0].source_id == "Q102"
