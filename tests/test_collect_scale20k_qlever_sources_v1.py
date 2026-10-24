from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from recommend_book.models import SourceRecord


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "collect_scale20k_qlever_sources_v1.py"
SPEC = importlib.util.spec_from_file_location("collect_scale_wikidata_qlever_genre_sitelink_v1", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_supplement_query_keeps_fixed_work_scope_and_adds_explicit_filters():
    query = MODULE.genre_sitelink_query(limit=100, offset=300, descending=True)
    assert query.count("UNION") == 3
    assert query.count("wdt:P7937 wd:Q8261.") == 1
    assert query.count("wdt:P7937 wd:Q747381.") == 1
    assert query.count("wdt:P31 wd:Q8261.") == 1
    assert query.count("wdt:P31 wd:Q747381.") == 1
    assert "?work wdt:P136 ?genre" in query
    assert "schema:about ?work" in query
    assert "VALUES ?wiki" in query
    for language in MODULE.WIKIPEDIA_LANGUAGES:
        assert f"<https://{language}.wikipedia.org/>" in query
    assert f'STR(?work) < "{MODULE.OLD_FINAL_BOUNDARY_URI}"' in query
    assert "wdt:P629" in query and "wd:Q3331189" in query
    assert "SELECT DISTINCT ?work" in query
    assert "ORDER BY DESC(?work) LIMIT 100 OFFSET 300" in query
    with pytest.raises(ValueError):
        MODULE.genre_sitelink_query(limit=101, offset=0)


def test_local_filter_deduplicates_and_preserves_old_exclusions():
    chosen = {"Q102"}
    fresh = MODULE.filter_unrecorded_ids(
        ["Q103", "Q103", "Q102", "Q101"], {"Q100"}, chosen
    )
    assert fresh == ["Q103", "Q101"]
    assert chosen == {"Q101", "Q102", "Q103"}


def test_prior_attempt_ids_includes_immutable_old_entity_ledger(tmp_path):
    state = tmp_path / "entity-state.json"
    state.write_text('{"attempted":["Q101"],"completed":["Q102"],"held":["Q103"]}', encoding="utf-8")
    native = tmp_path / "native-exclusions.json"
    native.write_text('{"qids":["Q104","Q101"]}', encoding="utf-8")
    assert MODULE.prior_attempt_ids(state, native) == {"Q101", "Q102", "Q103", "Q104"}


def test_wrapper_uses_isolated_route_and_thousand_bound(monkeypatch, tmp_path):
    old_state = tmp_path / "old-entity-state.json"
    old_state.write_text('{"attempted":["Q999"],"completed":[],"held":[]}', encoding="utf-8")
    native_state = tmp_path / "native-exclusions.json"
    native_state.write_text('{"qids":[]}', encoding="utf-8")
    catalogue = tmp_path / "db.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.executescript("create table works(qid text primary key, display_allowed integer); create table ingestion_failures(qid text);")
        db.executemany("insert into works values (?, 1)", [("Q1",), ("Q2",)])
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    base = MODULE._BASE_MODULE

    class NoNetwork:
        def get(self, *args, **kwargs):
            return SimpleNamespace(
                status="success", content=b'{"results":{"bindings":[]}}',
                artifact=SimpleNamespace(path=str(tmp_path / "qlever.json"), sha256="a" * 64),
            )

    monkeypatch.setattr(base, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(base, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(base, "make_fetcher", lambda output, v2_root: NoNetwork())
    monkeypatch.setattr(base, "WikidataAdapter", lambda fetcher: SimpleNamespace(
        fetch_entities=lambda ids, languages: SimpleNamespace(status="complete", records=[], errors=[])))
    result = MODULE.acquire(output=tmp_path / "out", catalogue=catalogue,
                            v2_root=tmp_path / "v2", old_entity_state=old_state,
                            native_exclusions=native_state)
    assert result["candidate_limit"] == 1000
    assert result["target_display"] == 1002
    state = json.loads((tmp_path / "out" / "discovery.json").read_text(encoding="utf-8"))
    assert state["frame"]["route"] == MODULE.ROUTE
    assert f"{MODULE.ROUTE}:0" in state["pages"]
    assert "wdt:P136 ?genre" in state["pages"][f"{MODULE.ROUTE}:0"]["query"]
    with pytest.raises(ValueError):
        MODULE.acquire(output=tmp_path / "out2", catalogue=catalogue,
                       v2_root=tmp_path / "v2", max_offset=1100,
                       old_entity_state=old_state, native_exclusions=native_state)


def test_route_stops_at_ten_pages_without_waiting_for_full_target(monkeypatch, tmp_path):
    old_state = tmp_path / "old.json"
    old_state.write_text('{"attempted":[],"completed":[],"held":[]}', encoding="utf-8")
    native_state = tmp_path / "native.json"
    native_state.write_text('{"qids":[]}', encoding="utf-8")
    catalogue = tmp_path / "db.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.executescript("create table works(qid text primary key, display_allowed integer); create table ingestion_failures(qid text);")
        db.execute("insert into works values ('Q1', 1)")
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    base = MODULE._BASE_MODULE
    calls = []

    class TenPages:
        def get(self, url, *, source, params=None, headers=None):
            offset = int(params["query"].rsplit(" OFFSET ", 1)[1])
            calls.append(offset)
            rows = [
                {"work": {"value": f"http://www.wikidata.org/entity/Q{200000000 + offset + i}"}}
                for i in range(100)
            ]
            return SimpleNamespace(
                status="success", content=json.dumps({"results": {"bindings": rows}}).encode(),
                artifact=SimpleNamespace(path=str(tmp_path / f"qlever-{offset}.json"), sha256="b" * 64),
            )

    monkeypatch.setattr(base, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(base, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(base, "make_fetcher", lambda output, v2_root: TenPages())
    monkeypatch.setattr(base, "WikidataAdapter", lambda fetcher: SimpleNamespace(
        fetch_entities=lambda ids, languages: SimpleNamespace(status="complete", records=[], errors=[])))
    result = MODULE.acquire(output=tmp_path / "out", catalogue=catalogue,
                            v2_root=tmp_path / "v2", old_entity_state=old_state,
                            native_exclusions=native_state)
    assert calls == list(range(0, 1000, 100))
    assert result["candidate_limit"] == 1000
    assert result["entity_held"] == 1000


def test_successful_record_crosses_pending_ingest_and_skips_existing_ledgers(monkeypatch, tmp_path):
    old_state = tmp_path / "old.json"
    old_state.write_text('{"attempted":["Q999"],"completed":[],"held":[]}', encoding="utf-8")
    native_state = tmp_path / "native.json"
    native_state.write_text('{"qids":[]}', encoding="utf-8")
    catalogue = tmp_path / "db.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.executescript("create table works(qid text primary key, display_allowed integer); create table ingestion_failures(qid text);")
        db.execute("insert into works values ('Q1', 1)")
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    base = MODULE._BASE_MODULE
    fetched: list[list[str]] = []
    ingested: list[list[str]] = []

    class OnePage:
        def get(self, url, *, source, params=None, headers=None):
            rows = [
                {"work": {"value": "http://www.wikidata.org/entity/Q300"}},
                {"work": {"value": "http://www.wikidata.org/entity/Q1"}},
                {"work": {"value": "http://www.wikidata.org/entity/Q999"}},
            ]
            return SimpleNamespace(
                status="success", content=json.dumps({"results": {"bindings": rows}}).encode(),
                artifact=SimpleNamespace(path=str(tmp_path / "qlever.json"), sha256="c" * 64),
            )

    def record(qid_value: str) -> SourceRecord:
        return SourceRecord(
            source="wikidata", source_id=qid_value,
            source_url=f"https://www.wikidata.org/wiki/{qid_value}",
            retrieved_at="2026-10-22T00:00:00+00:00", provider_id="wikidata",
            record_kind="work", raw_sha256="d" * 64,
            raw_path=str(tmp_path / f"{qid_value}.json"), rights_policy_id="wikidata-cc0",
        )

    class Adapter:
        def __init__(self, fetcher):
            pass

        def fetch_entities(self, ids, *, languages):
            fetched.append(list(ids))
            return SimpleNamespace(status="complete", records=[record("Q300")], errors=[])

    monkeypatch.setattr(base, "old_exclusions", lambda **kwargs: (set(), {"count": 0}))
    monkeypatch.setattr(base, "reuse_conditions", lambda output, v2_root: conditions)
    monkeypatch.setattr(base, "make_fetcher", lambda output, v2_root: OnePage())
    monkeypatch.setattr(base, "WikidataAdapter", Adapter)

    def fake_ingest(**kwargs):
        ingested.append([item.source_id for item in kwargs["records"]])
        return {"inserted": 1, "skipped_existing": 0, "failed": 0, "failures": []}

    monkeypatch.setattr(base, "ingest_records", fake_ingest)
    result = MODULE.acquire(output=tmp_path / "out", catalogue=catalogue,
                            v2_root=tmp_path / "v2", old_entity_state=old_state,
                            native_exclusions=native_state)
    assert fetched == [["Q300"]]
    assert ingested == [["Q300"]]
    assert result["entity_completed"] == 1
