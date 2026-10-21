import hashlib
import json
import sqlite3
import sys
import pytest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recommend_book.scale_catalogue_v1 import ScaleCatalogue
from recommend_book.scale_linked_metadata_v2 import (
    LinkedMetadataHold,
    checkpoint_default,
    fetch_missing_labels,
    load_cached_proofs,
    process_batch,
    referenced_ids,
    scan_candidates,
)
from scripts import enrich_scale_qlever_metadata_v1 as worker
from test_scale_catalogue_v1 import claim, record, save


def conditions(tmp_path):
    policy = tmp_path / "license.html"
    policy.write_text("Wikidata structured main namespace data CC0", encoding="utf-8")
    path = tmp_path / "conditions.json"
    digest = save(path, {
        "version": "scale-source-conditions-v1", "source": "wikidata", "policy_id": "wikidata-cc0",
        "metadataonly_scope": True, "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "policy_snapshot": {"path": str(policy), "sha256": hashlib.sha256(policy.read_bytes()).hexdigest()},
        "allowed_domains": ["www.wikidata.org", "query.wikidata.org"],
        "usages": dict.fromkeys(["acquire", "local_storage", "display", "external_llm", "redistribution", "commercial"], "allowed"),
    })
    return path, digest


def authority(tmp_path, qid, label):
    path = tmp_path / f"{qid}.json"
    digest = save(path, {"entities": {qid: {"id": qid, "labels": {"en": {"value": label, "language": "en"}}, "claims": {}}}})
    return path, digest


def add_cache(path, entries):
    path.write_text(json.dumps({qid: {"entity": {"id": qid, "labels": {"en": {"value": label, "language": "en"}}, "claims": {}},
                                 "raw_path": str(raw), "raw_sha256": digest}
                                for qid, label, raw, digest in entries}, ensure_ascii=False), encoding="utf-8")


def make_catalogue(tmp_path, qid="Q100"):
    source_conditions, digest = conditions(tmp_path)
    catalogue = ScaleCatalogue(tmp_path / "catalogue.sqlite", source_conditions_path=source_conditions,
                               source_conditions_sha256=digest)
    source = record(tmp_path, qid, genre="Q3", extra_claims={"P50": [claim("Q2")]})
    raw = json.loads(Path(source.raw_path).read_bytes())
    raw["entities"].pop("Q3")
    source.raw_sha256 = save(Path(source.raw_path), raw)
    assert catalogue.ingest([source])["inserted"] == 1
    return catalogue, source


def test_cached_linked_labels_update_search_and_preserve_old_version(tmp_path):
    catalogue, source = make_catalogue(tmp_path)
    author_raw, author_sha = authority(tmp_path, "Q2", "Author Two")
    genre_raw, genre_sha = authority(tmp_path, "Q3", "Mystery")
    cache = tmp_path / "linked-entities.json"
    add_cache(cache, [("Q2", "Author Two", author_raw, author_sha), ("Q3", "Mystery", genre_raw, genre_sha)])
    catalogue.set_read_status("Q100", "read")
    checkpoint = tmp_path / "checkpoint.json"
    candidates = scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids=set())
    result = process_batch(catalogue.db_path, candidates, checkpoint_default(), checkpoint_path=checkpoint, cache_paths=[cache])
    assert result["updated"] == 1
    with sqlite3.connect(catalogue.db_path) as db:
        row = db.execute("SELECT search_text,version_id FROM works WHERE qid='Q100'").fetchone()
        assert "author two" in row[0]
        assert db.execute("SELECT count(*) FROM source_versions WHERE qid='Q100'").fetchone()[0] == 2
        assert db.execute("SELECT status FROM read_status WHERE qid='Q100'").fetchone()[0] == "read"
        assert db.execute("SELECT label FROM work_genres WHERE qid='Q100' AND genre_qid='Q3'").fetchone()[0] == "Mystery"


def test_primary_raw_alteration_is_held_without_new_version(tmp_path):
    catalogue, source = make_catalogue(tmp_path)
    raw_path = Path(source.raw_path)
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    candidates = scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids=set())
    result = process_batch(catalogue.db_path, candidates, checkpoint_default(), checkpoint_path=checkpoint)
    assert result["held"] == 1
    with sqlite3.connect(catalogue.db_path) as db:
        assert db.execute("SELECT count(*) FROM source_versions WHERE qid='Q100'").fetchone()[0] == 1
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["held_versions"]["Q100"]["reason"].startswith("source raw hash changed")


def test_extra_claims_cannot_bypass_primary_raw_hash_check(tmp_path):
    _, source = make_catalogue(tmp_path)
    raw_path = Path(source.raw_path)
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(LinkedMetadataHold, match="source raw hash changed"):
        referenced_ids(source)


def test_tampered_cached_author_raw_is_held(tmp_path):
    catalogue, source = make_catalogue(tmp_path)
    author_raw, author_sha = authority(tmp_path, "Q2", "Author Two")
    genre_raw, genre_sha = authority(tmp_path, "Q3", "Mystery")
    cache = tmp_path / "linked-entities.json"
    add_cache(cache, [("Q2", "Author Two", author_raw, author_sha), ("Q3", "Mystery", genre_raw, genre_sha)])
    state = checkpoint_default()
    state["reference_proofs"] = load_cached_proofs({"Q2", "Q3"}, [cache])
    author_raw.write_text(author_raw.read_text(encoding="utf-8").replace("Author Two", "Forged"), encoding="utf-8")
    checkpoint = tmp_path / "checkpoint.json"
    candidates = scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids=set())
    result = process_batch(catalogue.db_path, candidates, state, checkpoint_path=checkpoint, cache_paths=[cache])
    assert result["held"] == 1
    assert "raw hash changed" in json.loads(checkpoint.read_text(encoding="utf-8"))["held_versions"]["Q100"]["reason"]


def test_native_and_baseline_rows_are_excluded(tmp_path):
    catalogue, _ = make_catalogue(tmp_path)
    with sqlite3.connect(catalogue.db_path) as db:
        db.execute("INSERT INTO native_classifications VALUES(?,?,?,?,?)", ("Q100", "m", "s", "[]", "{}"))
    assert scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids={"Q100"}) == []
    assert scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids=set()) == []


def test_sqlite_scan_skips_excluded_prefix_before_limit(tmp_path):
    catalogue, _ = make_catalogue(tmp_path, qid="Q100")
    for qid in ("Q101", "Q102"):
        source = record(tmp_path, qid, genre="Q3", extra_claims={"P50": [claim("Q2")]})
        raw_path = Path(source.raw_path)
        raw = json.loads(raw_path.read_bytes())
        raw["entities"].pop("Q3")
        source.raw_sha256 = save(raw_path, raw)
        assert catalogue.ingest([source])["inserted"] == 1
    with sqlite3.connect(catalogue.db_path) as db:
        excluded = db.execute("SELECT version_id FROM works WHERE qid='Q100'").fetchone()[0]
    result = scan_candidates(catalogue.db_path, qlever_roots=[tmp_path], baseline_ids=set(), limit=1,
                             excluded_versions={"Q100": excluded})
    assert [item["qid"] for item in result] == ["Q101"]


def test_missing_reference_labels_are_attempted_once_without_language_filter(tmp_path):
    raw = tmp_path / "labels.json"
    payload = {"entities": {"Q2": {"id": "Q2", "labels": {}, "claims": {}}}}
    digest = save(raw, payload)

    class FakeFetcher:
        def __init__(self):
            self.calls = []

        def get(self, url, *, source, params):
            self.calls.append(params)
            return SimpleNamespace(status="success", content=json.dumps(payload).encode(),
                                   artifact=SimpleNamespace(path=str(raw), sha256=digest))

    fake = FakeFetcher()
    state = checkpoint_default()
    checkpoint = tmp_path / "checkpoint.json"
    assert fetch_missing_labels({"Q2"}, state, checkpoint_path=checkpoint, fetcher=fake) == {}
    assert fake.calls == [{"action": "wbgetentities", "ids": "Q2", "props": "labels", "format": "json"}]
    assert "Q2" in state["reference_held"]
    assert fetch_missing_labels({"Q2"}, state, checkpoint_path=checkpoint, fetcher=fake) == {}
    assert len(fake.calls) == 1


def test_worker_once_processes_exactly_one_filtered_batch(tmp_path, monkeypatch):
    candidates = [{"qid": f"Q{index}", "version_id": f"v{index}"} for index in range(1, 4)]
    scans = []
    batches = []

    monkeypatch.setattr(worker, "baseline_ids", lambda path: set())
    monkeypatch.setattr(worker, "scan_candidates", lambda *args, **kwargs: scans.append(kwargs["limit"]) or candidates)

    def fake_process(*args, **kwargs):
        batches.append(args[1])
        return {"selected": len(args[1]), "updated": len(args[1]), "held": 0,
                "stale": 0, "native": 0}

    monkeypatch.setattr(worker, "process_batch", fake_process)
    result = worker.run_worker(catalogue=tmp_path / "catalogue.sqlite", baseline=tmp_path / "baseline.jsonl",
                               checkpoint=tmp_path / "checkpoint.json", limit=2, once=True)
    assert scans == [2]
    assert [[item["qid"] for item in batch] for batch in batches] == [["Q1", "Q2"]]
    assert result["polls"] == 1


def test_worker_filters_held_prefix_before_batch_limit(tmp_path, monkeypatch):
    candidates = [{"qid": f"Q{index}", "version_id": f"v{index}"} for index in range(1, 4)]
    state = checkpoint_default()
    state["held_versions"]["Q1"] = {"version_id": "v1", "reason": "missing"}
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(state), encoding="utf-8")
    batches = []

    monkeypatch.setattr(worker, "baseline_ids", lambda path: set())
    monkeypatch.setattr(worker, "scan_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(worker, "process_batch", lambda *args, **kwargs: batches.append(args[1]) or
                         {"selected": len(args[1]), "updated": len(args[1]), "held": 0,
                          "stale": 0, "native": 0})
    worker.run_worker(catalogue=tmp_path / "catalogue.sqlite", baseline=tmp_path / "baseline.jsonl",
                      checkpoint=checkpoint, limit=1, once=True)
    assert [[item["qid"] for item in batch] for batch in batches] == [["Q2"]]
