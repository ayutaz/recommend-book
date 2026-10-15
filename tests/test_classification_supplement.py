"""Small native runner fixtures; CLI is mocked and no real run is inspected."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from recommend_book import next_database
from recommend_book.classification import supplement
from recommend_book.classification.runner import run_classification
from recommend_book.jobs import canonical_json
from test_classification_guarded import allowed_many_fixture, event_bytes, install_native
from test_import_classification_database import classification


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


@pytest.fixture
def ready(tmp_path, monkeypatch, request):
    fixtures = tmp_path / "sources"
    fixtures.mkdir()
    works, ledger_path, binary, ledger = allowed_many_fixture(fixtures, 2)
    ledger["feature_recipes"] = {"work-present-relevance-max-v1": {"path": str(Path(next_database.__file__).resolve()), "sha256": supplement.file_sha(Path(next_database.__file__))}}
    _save(ledger_path, ledger)
    source = tmp_path / "inputs" / "selected.jsonl"
    source.parent.mkdir()
    source.write_text("".join(canonical_json(w.model_dump(mode="json")) + "\n" for w in works), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/run_guarded_classification.py"
    spec = importlib.util.spec_from_file_location("supplement_fixture_prepare", script)
    preparation_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparation_api)
    monkeypatch.setattr(preparation_api, "OUTPUT_ROOT", tmp_path / "classification")
    namespace = tmp_path / "classification" / "fixture"
    preparation = preparation_api.prepare(source, ledger_path, namespace, cli_version="fake-1", batch_size=5, concurrency=1)
    conflict = getattr(request, "param", None) == "conflict"
    def responder(argv, kwargs, number):
        results = []
        for index, work in enumerate(works):
            result = classification(work)
            result.update(model="gpt-6.1-sol", cli_version="fake-1")
            if conflict and index == 0:
                result["conflicts"] = ["Synthetic contradictory sources require review"]
            results.append(result)
        value = {"results": results}
        Path(argv[argv.index("-o") + 1]).write_bytes(json.dumps(value).encode())
        return subprocess.CompletedProcess(argv, 0, event_bytes(value), b"")
    calls = install_native(monkeypatch, works[0], responder=responder)
    manifest = run_classification(works, namespace / "run", executable=str(binary), usage_ledger=ledger_path,
                                  expected_run_config=preparation["config"], **preparation["controls"])
    assert manifest["completed"] == 2
    assert len(calls) == 1
    terminal_path = tmp_path / "terminal" / "terminal.json"
    _save(terminal_path, {"actual_exit_code": 0, "run_manifest_sha256": supplement.file_sha(namespace / "run/manifest.json"),
                          "run_id": manifest["run_id"], "selected": 2, "completed": 2})
    return {"namespace": namespace, "preparation_path": namespace / "preparation-manifest.json", "terminal_path": terminal_path,
            "preparation": preparation, "terminal": json.loads(terminal_path.read_bytes()), "manifest": manifest,
            "output": tmp_path / "adopted", "works": works, "calls": calls}


def _adopt(ready):
    return supplement.adopt_guarded_supplement(ready["preparation_path"], supplement.file_sha(ready["preparation_path"]),
        ready["terminal_path"], supplement.file_sha(ready["terminal_path"]), ready["output"])


def _reseal_manifest(ready, manifest):
    run = ready["namespace"] / "run"
    _save(run / "manifest.json", manifest)
    _save(run / ("manifest." + manifest["run_id"] + ".json"), manifest)
    ready["terminal"]["run_manifest_sha256"] = supplement.file_sha(run / "manifest.json")
    _save(ready["terminal_path"], ready["terminal"])


def test_small_sidecar_retains_native_batch_lineage_and_real_receipts(ready):
    report = _adopt(ready)
    assert report["selected"] == report["completed"] == report["feature_bearing_n"] == 2
    assert report["quality_verified"] is False and report["human_gold"] is False
    assert report["main_database_updated"] is False
    assert len(ready["calls"]) == 1  # Adoption never invokes the mocked CLI again.
    packets = [json.loads(line) for line in (ready["output"] / "feature-packets.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(packets) == 2
    assert all(len(p["classification_originating_input_hashes"]) == 2 for p in packets)
    assert all(len(p["purpose_receipts"]) == 5 for p in packets)
    assert all(p["purpose_receipts"][-1]["decision"]["kind"] == "supplement-literal-local-storage-facts-v1" for p in packets)
    assert all("allowed" not in p["purpose_receipts"][-1]["decision"] for p in packets)
    assert all(p["features"] == {"genre.mystery": 1.} for p in packets)
    with sqlite3.connect(ready["output"] / "supplement.sqlite") as database:
        assert database.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
        assert database.execute("SELECT count(*) FROM supplement_receipts").fetchone()[0] == 10
        assert database.execute("SELECT count(*) FROM supplement_source_refs").fetchone()[0] == 2
    artifact_manifest = json.loads((ready["output"] / "manifest.json").read_bytes())
    assert "manifest.json" not in artifact_manifest["output_sha256"]
    assert all(supplement.file_sha(ready["output"] / name) == digest for name, digest in artifact_manifest["output_sha256"].items())


@pytest.mark.parametrize("change", ["terminal_exit", "immutable_manifest", "job_state", "durable_result", "history_sha", "foreign_batch", "input_copy", "prepared_current_config", "source_raw", "source_record"])
def test_adoption_rejects_incomplete_or_detached_terminal_proof(ready, change):
    run = ready["namespace"] / "run"
    key = next(iter(ready["manifest"]["jobs"]))
    manifest = deepcopy(ready["manifest"])
    if change == "terminal_exit":
        ready["terminal"]["actual_exit_code"] = 1;_save(ready["terminal_path"], ready["terminal"])
    elif change == "immutable_manifest":
        path = run / ("manifest." + manifest["run_id"] + ".json");path.write_bytes(path.read_bytes() + b" ")
    elif change == "job_state":
        path = run / "jobs" / (key + ".json");value = json.loads(path.read_bytes());value["input_hash"] = "f" * 64;_save(path, value)
    elif change == "durable_result":
        path = run / manifest["jobs"][key]["result_path"];value = json.loads(path.read_bytes());value["needs_human_review"] = False;_save(path, value)
    elif change in ("history_sha", "foreign_batch"):
        state = manifest["jobs"][key]
        history = next(h for h in reversed(state["history"]) if h.get("source_manifest_path"))
        if change == "history_sha":history["source_manifest_sha256"] = "f" * 64
        else:
            descriptor_path = run / history["source_manifest_path"];descriptor = json.loads(descriptor_path.read_bytes())
            descriptor["originating_input_hashes"].append("f" * 64);_save(descriptor_path, descriptor)
            history["source_manifest_sha256"] = supplement.file_sha(descriptor_path)
        _save(run / "jobs" / (key + ".json"), state);_reseal_manifest(ready, manifest)
    elif change == "input_copy":
        path = ready["namespace"] / "inputs.jsonl";path.write_bytes(path.read_bytes() + b" ")
    elif change == "prepared_current_config":
        ready["preparation"]["config"]["cli_version"] = "foreign-cli";_save(ready["preparation_path"], ready["preparation"])
    else:
        ledger = json.loads(Path(ready["preparation"]["usage_ledger_path"]).read_bytes())
        source_binding = next(iter(next(iter(ledger["inputs"].values()))["sources"].values()))
        if change == "source_raw":
            path = Path(source_binding["raw_path"]);path.write_bytes(path.read_bytes() + b" ")
        else:
            with sqlite3.connect(source_binding["database_path"]) as source:
                source.execute("DROP TRIGGER immutable_source_update")  # Deliberate tampering in this temporary fixture only.
                source.execute("UPDATE source_records SET record_json='{}' WHERE version_id=?", (source_binding["source_version_id"],))
    with pytest.raises(ValueError):
        _adopt(ready)
    assert not ready["output"].exists()


@pytest.mark.parametrize("ready", ["conflict"], indirect=True)
def test_conflicts_empty_features_without_rewriting_native_unknowns(ready):
    report = _adopt(ready)
    assert report["conflict_held_n"] == 1 and report["feature_bearing_n"] == 1
    packets = [json.loads(line) for line in (ready["output"] / "feature-packets.jsonl").read_text(encoding="utf-8").splitlines()]
    conflicted, = [p for p in packets if p["native_result"]["conflicts"]]
    assert conflicted["features"] == {} and conflicted["feature_provenance"] == {}
    assert any(axis["axis"] == "style" and axis["decision"] == "unknown" for axis in conflicted["native_result"]["missing_axes"])


def test_fresh_namespace_and_no_full_source_database_hash(ready, monkeypatch):
    original = supplement.file_sha
    def no_source_database(path):
        assert not (Path(path).suffix == ".sqlite" and Path(path).name != "supplement.sqlite")
        return original(path)
    monkeypatch.setattr(supplement, "file_sha", no_source_database)
    _adopt(ready)
    with pytest.raises(ValueError, match="fresh|exist"):
        _adopt(ready)


def test_boolean_usage_decision_cannot_authorize_adoption(ready, monkeypatch):
    monkeypatch.setattr("recommend_book.usage_guard.UsageGuard.check_derived", lambda *args, **kwargs: True)
    with pytest.raises(ValueError, match="decision"):
        _adopt(ready)
    assert not ready["output"].exists()


def test_display_denial_with_whole_batch_is_not_skipped(ready, monkeypatch):
    from recommend_book.usage_guard import UsageGuard
    original = UsageGuard.check_derived
    received = []
    def checking(self, payload, origins, usage, **kwargs):
        received.append((usage, list(origins)))
        decision = original(self, payload, origins, usage, **kwargs)
        if usage == "display":
            return decision.model_copy(update={"allowed": False, "reasons": ["Synthetic other batch input held"]})
        return decision
    monkeypatch.setattr(UsageGuard, "check_derived", checking)
    with pytest.raises(ValueError, match="decision"):
        _adopt(ready)
    assert [usage for usage, _ in received] == ["external_llm", "display"]
    assert all(len(origins) == 2 for _, origins in received)
    assert not ready["output"].exists()


@pytest.mark.parametrize("state", ["denied", "unresolved", "conditional"])
def test_real_external_display_success_cannot_replace_local_literal_condition(ready, monkeypatch, state):
    from recommend_book.usage_guard import UsageGuard
    original = UsageGuard._load
    def load(self):
        ledger, registry, digest = original(self)
        registry.policies["wikidata-cc0"].usages["local_storage"] = state
        return ledger, registry, digest
    monkeypatch.setattr(UsageGuard, "_load", load)
    with pytest.raises(ValueError, match="literal local_storage"):
        _adopt(ready)
    assert not ready["output"].exists()


def test_uncited_unknown_batch_source_cannot_hide_behind_selected_input(ready):
    from recommend_book.usage_guard import UsageGuard
    preparation = ready["preparation"]
    guard = UsageGuard(preparation["usage_ledger_path"], expected_ledger_sha256=preparation["usage_ledger_sha256"])
    ledger, registry, _ = guard._load()
    first_origin = preparation["input_identities"][0]["input_hash"]
    with pytest.raises(ValueError, match="batch.*unknown"):
        supplement._literal_local_facts([first_origin, "f" * 64], guard, ledger, registry, {})
