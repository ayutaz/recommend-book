import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_SPEC = importlib.util.spec_from_file_location("repair_scale_author_labels_v1", Path("scripts/repair_scale_author_labels_v1.py"))
repair = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(repair)


def test_label_proof_uses_exact_saved_label_and_locator(tmp_path):
    raw = tmp_path / "raw.bin"
    payload = {"entities": {"Q2": {"id": "Q2", "labels": {"en": {"language": "en", "value": "Ada Lovelace"}}}}}
    raw.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    proof = repair.label_proof("Q2", {"raw_path": str(raw), "raw_sha256": digest, "entity": payload["entities"]["Q2"]})
    assert proof == {"label": "Ada Lovelace", "raw_path": str(raw.resolve()), "raw_sha256": digest, "locator": "entities.Q2.labels.en"}


def test_label_proof_rejects_empty_labels():
    assert repair.label_proof("Q2", {"raw_path": "x", "raw_sha256": "x", "entity": {"id": "Q2", "labels": {}}}) is None


def test_build_records_creates_new_version_without_mutating_source(tmp_path, monkeypatch):
    source = tmp_path / "source.jsonl"
    original = {
        "source": "wikidata", "source_id": "Q10", "source_url": "https://www.wikidata.org/wiki/Q10",
        "retrieved_at": "2026-01-01T00:00:00+00:00", "provider_id": "wikidata", "record_kind": "work",
        "titles": [{"value": "Example", "language": "en", "kind": "preferred"}],
        "contributors": [{"name": "Q2", "role": "author", "identifiers": [{"source": "wikidata", "value": "Q2", "scope": "agent"}]}],
        "identifiers": [{"source": "wikidata", "value": "Q10", "scope": "work"}], "work_refs": [],
        "original_language": "en", "language": None, "first_publication_year": 2020, "publication_year": None,
        "publisher": None, "series": None, "volume": None, "work_kind": "novel", "region": [], "subjects": [],
        "evidence": [], "contents": [], "raw_sha256": "raw", "raw_path": "raw.bin", "rights_policy_id": "wikidata-cc0",
        "extra": {"claims": {}, "linked_label_qids": []},
    }
    raw_line = json.dumps(original, ensure_ascii=False, separators=(",", ":"))
    source.write_text(raw_line + "\n", encoding="utf-8")
    request = {
        "version": "scale-content-linked-label-repair-request-v1", "distinct_agent_qids": 1,
        "rows": [{"work_qid": "Q10", "supported_source_ref": {"qid": "Q10", "path": str(source), "file_sha256": "unused", "line": 1, "record_sha256": hashlib.sha256(raw_line.encode()).hexdigest()}, "unresolved_contributors": [{"agent_qid": "Q2", "role": "author", "current_name": "Q2"}]}],
    }
    monkeypatch.setattr(repair, "REQUEST", tmp_path / "request.json")
    repair.REQUEST.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    repair.save_json(out / "fetch-state.json", {"completed": [], "held": [], "errors": []})
    monkeypatch.setattr(repair, "request_digest", lambda: "request-sha")
    report = repair.build_records(out, request, {"Q2": {"label": "Ada Lovelace", "raw_path": str(source), "raw_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "locator": "entities.Q2.labels.en"}})
    repaired = json.loads((out / "repaired-source-records.jsonl").read_text(encoding="utf-8"))
    assert report["repaired_work_count"] == 1
    assert repaired["contributors"][0]["name"] == "Ada Lovelace"
    assert repaired["extra"]["linked_label_qids"] == ["Q2"]
    assert source.read_text(encoding="utf-8") == raw_line + "\n"


def test_run_deduplicates_agent_ids_before_fetching(tmp_path, monkeypatch):
    request = {
        "rows": [
            {"unresolved_contributors": [{"agent_qid": "Q2"}, {"agent_qid": "Q3"}]},
            {"unresolved_contributors": [{"agent_qid": "Q2"}]},
        ]
    }
    fetched = []
    monkeypatch.setattr(repair, "load_request", lambda: request)
    monkeypatch.setattr(repair, "output_dir", lambda: tmp_path / "out")
    monkeypatch.setattr(repair, "request_digest", lambda: "request-sha")
    monkeypatch.setattr(repair, "load_cache", lambda target_ids: {})
    monkeypatch.setattr(repair, "fetch_missing", lambda out, missing: fetched.append(list(missing)) or {"held": [], "errors": []})
    monkeypatch.setattr(repair, "load_fetched", lambda out: {})
    monkeypatch.setattr(repair, "build_records", lambda out, request, proofs: {"repaired_work_count": 0})

    repair.run()

    assert fetched == [["Q2", "Q3"]]


@pytest.mark.parametrize('failed_id', ['Q1', 'Q51'])
def test_fetch_failure_is_held_and_never_resent(tmp_path, monkeypatch, failed_id):
    output = tmp_path / "out"
    missing = [f"Q{index}" for index in range(1, 52)]
    calls = []

    class FakeFetcher:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url, *, source, params):
            ids = params["ids"].split("|")
            calls.append(ids)
            if failed_id in ids:
                return SimpleNamespace(status="failed", error="synthetic failure", status_code=503, artifact=None, content=b"")
            payload = {"entities": {qid: {"id": qid, "labels": {"en": {"language": "en", "value": f"Label {qid}"}}} for qid in ids}}
            content = json.dumps(payload).encode("utf-8")
            raw = tmp_path / "raw.json"
            raw.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            artifact = SimpleNamespace(path=str(raw), sha256=digest)
            return SimpleNamespace(status="success", error=None, status_code=200, artifact=artifact, content=content)

    monkeypatch.setattr(repair, "RawStore", lambda root: object())
    monkeypatch.setattr(repair, "HTTPFetcher", FakeFetcher)
    monkeypatch.setattr(repair, "request_digest", lambda: "request-sha")

    first = repair.fetch_missing(output, missing)
    second = repair.fetch_missing(output, missing)
    assert len(calls) == 2
    assert calls[0] == missing[:50]
    assert calls[1] == ["Q51"]
    held = missing[:50] if failed_id == 'Q1' else missing[50:]
    assert set(first["completed"]) == set(missing) - set(held)
    assert first["held"] == sorted(held)
    assert second["completed"] == first["completed"]
    assert second["held"] == sorted(held)
