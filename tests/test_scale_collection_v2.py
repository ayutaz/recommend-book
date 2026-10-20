import importlib.util
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).parents[1] / "scripts" / "collect_scale_wikidata_v2.py"
spec = importlib.util.spec_from_file_location("scale_collect_v2", PATH)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_v2_discovery_frame_starts_after_v1_and_keeps_novel_scope():
    query = collector.discovery_query(limit=500, offset=12000)
    assert "OFFSET 12000" in query
    assert "wd:Q8261" in query and "wd:Q747381" in query
    assert "wdt:P136" in query and "wdt:P629" in query
    assert "Q3331189" in query
    language_query = collector.language_discovery_query("Q5287", limit=500, offset=0)
    assert "wd:Q5287" in language_query and "wdt:P407" in language_query
    broad_query = collector.broad_discovery_query(limit=500, offset=0)
    assert "wd:Q8261" in broad_query and "wdt:P136" not in broad_query


def test_load_existing_ids_restores_selection_and_catalogue(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"ids": ["Q1", "Q2"]}), encoding="utf-8")
    catalogue = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.execute("CREATE TABLE works(qid TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO works VALUES(?)", [("Q2",), ("Q3",)])
    assert collector.load_existing_ids(baseline_selection=selection, catalogue=catalogue) == {"Q1", "Q2", "Q3"}


def test_discover_filters_existing_ids_and_does_not_repeat_completed_page(tmp_path):
    calls = []

    class Fetcher:
        def get(self, url, **kwargs):
            calls.append(kwargs["params"]["query"])
            payload = {"results": {"bindings": [
                {"work": {"value": "http://www.wikidata.org/entity/Q1"}},
                {"work": {"value": "http://www.wikidata.org/entity/Q9"}},
            ]}}
            return SimpleNamespace(status="success", content=json.dumps(payload).encode(),
                                   artifact=SimpleNamespace(path="saved.bin", sha256="a" * 64),
                                   error=None, status_code=200)

    first = collector.discover(tmp_path, Fetcher(), existing_ids={"Q1"}, target_new=1,
                               start_offset=12000, max_offset=12500)
    second = collector.discover(tmp_path, Fetcher(), existing_ids={"Q1"}, target_new=1,
                                start_offset=12000, max_offset=12500)
    assert first["ids"] == second["ids"] == ["Q9"]
    assert len(calls) == 1


def test_discovery_holds_failed_page_without_retry(tmp_path):
    calls = []

    class Fetcher:
        def get(self, *args, **kwargs):
            calls.append(1)
            return SimpleNamespace(status="failed", error="HTTP 502", status_code=502)

    for _ in range(2):
        state = collector.discover(tmp_path, Fetcher(), existing_ids=set(), target_new=1,
                                   start_offset=12000, max_offset=12500)
        assert state["ids"] == []
    assert len(calls) == 1
    assert json.loads((tmp_path / "discovery.json").read_text())["pages"]["12000"]["status"] == "held"


def test_fresh_conditions_reuses_pinned_bytes_on_resume(tmp_path):
    policy = tmp_path / "conditions" / "wikidata-licensing.bin"
    policy.parent.mkdir()
    policy.write_bytes(b"Wikidata CC0-1.0 licensing")
    receipt = tmp_path / "source-conditions-v2.json"
    saved = {
        "version": collector.CONDITIONS_VERSION,
        "policy_snapshot": {"sha256": hashlib.sha256(policy.read_bytes()).hexdigest(), "path": str(policy)},
    }
    receipt.write_text(json.dumps(saved), encoding="utf-8")

    class Fetcher:
        def get(self, *args, **kwargs):
            raise AssertionError("resume must not refetch the licensing policy")

    assert collector.fresh_conditions(tmp_path, Fetcher())["policy_snapshot"]["sha256"] == hashlib.sha256(policy.read_bytes()).hexdigest()


def test_discovery_rejects_changed_frame(tmp_path):
    state = {"frame": {"version": "old"}, "pages": {}, "ids": [], "held_ids": []}
    (tmp_path / "discovery.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="frame"):
        collector.discover(tmp_path, SimpleNamespace(), existing_ids=set(), target_new=1,
                           start_offset=12000, max_offset=12500)
