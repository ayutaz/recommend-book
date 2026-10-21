from __future__ import annotations

import importlib.util
import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "collect_scale_wikidata_qlever_v1.py"
SPEC = importlib.util.spec_from_file_location("collect_scale_wikidata_qlever_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def artifact(tmp_path: Path):
    path = tmp_path / "raw.bin"
    path.write_bytes(b"fixture")
    return SimpleNamespace(path=str(path), sha256="a" * 64)


class FakeFetcher:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.result


def test_query_is_qlever_compatible_and_keeps_work_scope():
    query = MODULE.source_declared_query(limit=100, offset=200)
    assert "PREFIX wd:" in query and "PREFIX wdt:" in query
    assert "wd:Q8261" in query and "wd:Q747381" in query
    assert "wdt:P7937" in query and "wdt:P31" in query
    assert "wdt:P629" in query and "wd:Q3331189" in query
    assert "LIMIT 100 OFFSET 200" in query
    assert "ORDER BY ?work" in query
    assert "ORDER BY DESC(?work)" in MODULE.source_declared_query(limit=100, offset=200, descending=True)
    with pytest.raises(ValueError):
        MODULE.source_declared_query(limit=101, offset=0)


def test_parse_bindings_deduplicates_and_rejects_malformed_payload():
    payload = {"results": {"bindings": [
        {"work": {"value": "http://www.wikidata.org/entity/Q101"}},
        {"work": {"value": "http://www.wikidata.org/entity/Q101"}},
        {"work": {"value": "http://www.wikidata.org/entity/Q102"}},
    ]}}
    assert MODULE.parse_bindings(json.dumps(payload).encode()) == ["Q101", "Q102"]
    with pytest.raises(ValueError, match="no results object"):
        MODULE.parse_bindings(b"[]")


def test_qlever_success_response_keeps_raw_artifact_metadata(tmp_path):
    response = SimpleNamespace(
        status="success",
        content=json.dumps({"results": {"bindings": [{"work": {"value": "http://www.wikidata.org/entity/Q101"}}]}}).encode(),
        artifact=artifact(tmp_path),
    )
    fetcher = FakeFetcher(response)
    fetched = fetcher.get(MODULE.QLEVER_URL, source="wikidata", params={"query": "SELECT", "format": "json"})
    ids = MODULE.parse_bindings(fetched.content)
    assert ids == ["Q101"]
    assert fetched.artifact.sha256 == "a" * 64
    assert MODULE.QLEVER_URL.startswith("https://qlever.dev/")


def test_acquire_resume_restores_pending_skips_held_and_does_not_refetch(tmp_path, monkeypatch):
    output = tmp_path / "acquisition"
    catalogue = tmp_path / "catalogue.sqlite"
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    with sqlite3.connect(catalogue) as db:
        db.executescript("""
        create table works(qid text primary key, display_allowed integer);
        create table ingestion_failures(qid text);
        """)

    empty_exclusions = hashlib.sha256(b"[]").hexdigest()
    output.mkdir(parents=True)
    (output / "discovery.json").write_text(json.dumps({
        "frame": {"version": "scale-discovery-qlever-v1", "page_size": 100,
                   "exclude_sha256": empty_exclusions, "endpoint": MODULE.QLEVER_URL,
                   "order": "asc"},
        "pages": {"qlever-asc:0": {"status": "held", "error": "HTTP 429"}},
        "ids": [],
    }), encoding="utf-8")

    def record(value: str):
        return MODULE.SourceRecord(
            source="wikidata", source_id=value, source_url=f"https://www.wikidata.org/wiki/{value}",
            retrieved_at="2026-10-21T00:00:00+00:00", provider_id="wikidata", record_kind="work",
            raw_sha256="a" * 64, raw_path=str(tmp_path / f"{value}.json"), rights_policy_id="wikidata-cc0",
        )

    pending = record("Q100")
    (output / "pending-records.jsonl").write_text(pending.model_dump_json() + "\n", encoding="utf-8")
    fetch_calls: list[int] = []

    class FakeFetcher:
        def get(self, url, *, source, params=None, headers=None):
            query = params["query"]
            offset = int(query.rsplit(" OFFSET ", 1)[1])
            fetch_calls.append(offset)
            ids = [] if offset >= 2 else ["Q101"]
            content = json.dumps({"results": {"bindings": [
                {"work": {"value": f"http://www.wikidata.org/entity/{value}"}} for value in ids
            ]}}).encode()
            return SimpleNamespace(status="success", content=content, artifact=artifact(tmp_path))

    class FakeAdapter:
        def __init__(self, fetcher):
            pass

        def fetch_entities(self, ids, *, languages):
            return SimpleNamespace(status="complete", records=[record(value) for value in ids], errors=[])

    ingest_calls: list[list[str]] = []

    def fake_ingest(**kwargs):
        ingest_calls.append([r.source_id for r in kwargs["records"]])
        return {"inserted": len(kwargs["records"]), "skipped_existing": 0, "failed": 0, "failures": []}

    monkeypatch.setattr(MODULE, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(MODULE, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(MODULE, "make_fetcher", lambda output, v2_root: FakeFetcher())
    monkeypatch.setattr(MODULE, "WikidataAdapter", FakeAdapter)
    monkeypatch.setattr(MODULE, "ingest_records", fake_ingest)

    first = MODULE.acquire(output=output, catalogue=catalogue, v2_root=tmp_path / "v2",
                           target_display=100000, max_offset=3, page_size=1)
    assert first["entity_completed"] == 1
    assert fetch_calls == [1, 2]
    assert ingest_calls == [["Q100"], ["Q101"]]

    before = len(fetch_calls)
    second = MODULE.acquire(output=output, catalogue=catalogue, v2_root=tmp_path / "v2",
                            target_display=100000, max_offset=3, page_size=1)
    assert second["entity_completed"] == 1
    # The old held page and the successful page are never resent; the empty
    # offset-200 terminal page is also already successful on restart.
    assert fetch_calls[before:] == []


def test_reuse_conditions_preserves_existing_receipt_and_rejects_mismatch(tmp_path):
    v2 = tmp_path / "v2"
    source = v2 / "source-conditions-v2.json"
    source.parent.mkdir()
    source.write_text(json.dumps({"source": "wikidata", "policy_id": "wikidata-cc0",
                                  "policy_snapshot": {"sha256": "a"}, "usages": {},
                                  "metadataonly_scope": True, "scope": "structured",
                                  "request": {"raw_sha256": "a"}}), encoding="utf-8")
    output = tmp_path / "out"
    path = output / "source-conditions-v19-qlever.json"
    path.parent.mkdir()
    original = {"source": "wikidata", "policy_id": "wikidata-cc0",
                "policy_snapshot": {"sha256": "a"}, "usages": {},
                "metadataonly_scope": True, "scope": "structured",
                "request": {"raw_sha256": "a"}, "version": "old-receipt",
                "reused_from": str(source.resolve())}
    path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    assert MODULE.reuse_conditions(output, v2) == path
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "old-receipt"
    path.write_text(json.dumps({**original, "scope": "tampered"}), encoding="utf-8")
    with pytest.raises(ValueError, match="conditions receipt differs"):
        MODULE.reuse_conditions(output, v2)


def test_acquire_records_interrupted_entity_batch_reason_on_resume(tmp_path, monkeypatch):
    output = tmp_path / "acquisition"
    catalogue = tmp_path / "catalogue.sqlite"
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    with sqlite3.connect(catalogue) as db:
        db.executescript("create table works(qid text primary key, display_allowed integer); create table ingestion_failures(qid text);")
    digest = hashlib.sha256(b"[]").hexdigest()
    output.mkdir(parents=True)
    (output / "discovery.json").write_text(json.dumps({
        "frame": {"version": "scale-discovery-qlever-v1", "page_size": 1,
                   "exclude_sha256": digest, "endpoint": MODULE.QLEVER_URL, "order": "asc"},
        "pages": {"qlever-asc:0": {"status": "success", "returned": 0}}, "ids": [],
    }), encoding="utf-8")
    (output / "entity-state.json").write_text(json.dumps({
        "attempted": ["Q102"], "completed": [], "held": ["Q102"], "errors": [],
        "in_flight_batch": {"ids": ["Q102"], "started_at": "2026-10-21T00:00:00+00:00"},
    }), encoding="utf-8")
    monkeypatch.setattr(MODULE, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(MODULE, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(MODULE, "make_fetcher", lambda output, v2_root: FakeFetcher(SimpleNamespace(status="success", content=b'{"results":{"bindings":[]}}', artifact=artifact(tmp_path))))
    monkeypatch.setattr(MODULE, "WikidataAdapter", lambda fetcher: SimpleNamespace(fetch_entities=lambda ids, languages: SimpleNamespace(status="complete", records=[], errors=[])))
    monkeypatch.setattr(MODULE, "ingest_records", lambda **kwargs: {"inserted": 0, "skipped_existing": 0, "failed": 0, "failures": []})
    MODULE.acquire(output=output, catalogue=catalogue, v2_root=tmp_path / "v2", max_offset=1, page_size=1)
    state = json.loads((output / "entity-state.json").read_text(encoding="utf-8"))
    assert state["in_flight_batch"] is None
    assert state["interrupted_batches"][0]["batch"] == ["Q102"]


def test_success_records_are_persisted_before_completion_on_injected_crash(tmp_path, monkeypatch):
    output = tmp_path / "acquisition"
    catalogue = tmp_path / "catalogue.sqlite"
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    with sqlite3.connect(catalogue) as db:
        db.executescript("create table works(qid text primary key, display_allowed integer); create table ingestion_failures(qid text);")

    def record(value: str):
        return MODULE.SourceRecord(source="wikidata", source_id=value,
                                   source_url=f"https://www.wikidata.org/wiki/{value}",
                                   retrieved_at="2026-10-21T00:00:00+00:00", provider_id="wikidata",
                                   record_kind="work", raw_sha256="a" * 64,
                                   raw_path=str(tmp_path / f"{value}.json"), rights_policy_id="wikidata-cc0")

    class Fetcher:
        def get(self, url, *, source, params=None, headers=None):
            return SimpleNamespace(status="success", content=b'{"results":{"bindings":[{"work":{"value":"http://www.wikidata.org/entity/Q101"}}]}}', artifact=artifact(tmp_path))

    class Adapter:
        def __init__(self, fetcher):
            pass

        def fetch_entities(self, ids, *, languages):
            return SimpleNamespace(status="complete", records=[record(value) for value in ids], errors=[])

    monkeypatch.setattr(MODULE, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(MODULE, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(MODULE, "make_fetcher", lambda output, v2_root: Fetcher())
    monkeypatch.setattr(MODULE, "WikidataAdapter", Adapter)
    monkeypatch.setattr(MODULE, "ingest_records", lambda **kwargs: {"inserted": 0, "skipped_existing": 0, "failed": 0, "failures": []})
    real_write = MODULE.write_jsonl
    crashed = {"value": False}

    def crash_after_pending(records, path):
        count = real_write(records, path)
        if records and not crashed["value"]:
            crashed["value"] = True
            raise RuntimeError("injected crash after pending checkpoint")
        return count

    monkeypatch.setattr(MODULE, "write_jsonl", crash_after_pending)
    with pytest.raises(RuntimeError, match="injected crash"):
        MODULE.acquire(output=output, catalogue=catalogue, v2_root=tmp_path / "v2", max_offset=1, page_size=1)
    pending = [line for line in (output / "pending-records.jsonl").read_text(encoding="utf-8").splitlines() if line]
    state = json.loads((output / "entity-state.json").read_text(encoding="utf-8"))
    assert len(pending) == 1 and json.loads(pending[0])["source_id"] == "Q101"
    assert state["in_flight_batch"]["ids"] == ["Q101"]
    assert state["completed"] == [] and state["held"] == ["Q101"]
