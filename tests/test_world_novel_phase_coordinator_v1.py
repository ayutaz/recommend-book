import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start_world_novel_phase_v1.py"
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("world_novel_phase_v1_test", SCRIPT)
phase = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = phase
SPEC.loader.exec_module(phase)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def _fixture(tmp_path):
    base = tmp_path / "data" / "world-21000-delivery-20261027-v1"
    source = base / "sources" / "w000-v1"
    exclusion = tmp_path / "prior-input-exclusions.json"
    _write(exclusion, {"qids": [], "input_hashes": []})
    inputs = _write(source / "inputs.jsonl", b"fixture\n")
    ledger = _write(source / "ledger.json", {"version": "fixture"})
    database = _write(source / "dataset.sqlite", b"fixture database")
    mapping = [{"work_id": "work-1", "input_work_id": "work-1", "wikidata_qid": "Q1"}]
    mapping_path = _write(source / "work-mapping.json", mapping)
    manifest = {
        "version": "scale-content-classification-source-v1",
        "required_purpose_admissions_allowed": True,
        "window_index": 0,
        "selected": 1,
        "source_database": str(database),
        "source_database_sha256": _sha(database),
        "chunks": [{
            "run_name": "world21000-20261027-w000-v1",
            "inputs_path": str(inputs), "inputs_sha256": _sha(inputs),
            "usage_ledger_path": str(ledger), "usage_ledger_sha256": _sha(ledger),
            "selected": 1,
        }],
    }
    manifest_path = _write(source / "manifest.json", manifest)
    document = {
        "version": phase.INDEX_VERSION,
        "phase": phase.PHASE_VERSION,
        "namespace": str(base),
        "operator": {"name": "fixture", "script": str(SCRIPT), "sha256": _sha(SCRIPT)},
        "exclusions": {"path": str(exclusion), "sha256": _sha(exclusion)},
        "windows": [{"window_index": 0, "run_name": "world21000-20261027-w000-v1",
            "source_path": str(source), "source_sha256": _sha(manifest_path), "mapping_sha256": _sha(mapping_path)}],
        "catalogue_writes": 0, "model_calls": 0, "network_requests": 0,
    }
    index = _write(base / "source-index-v1.json", document)
    return base, index, document, source


def test_load_sources_seals_new_namespace_and_bounded_window(tmp_path, monkeypatch):
    base, index, _, _ = _fixture(tmp_path)
    monkeypatch.setattr(phase, "_read_work_inputs", lambda path: [{"work_id": "work-1"}])
    import recommend_book.scale_content_recovery_v1 as recovery
    import recommend_book.usage_guard as usage
    monkeypatch.setattr(recovery, "_verify_mapping_identity", lambda database, mapping, works: None)
    monkeypatch.setattr(usage, "model_hash", lambda work: "input-hash-1")

    windows = phase.load_sources(index, _sha(index), base=base)

    assert windows[0]["run_name"] == "world21000-20261027-w000-v1"
    assert windows[0]["selected"] == 1
    assert Path(windows[0]["source_path"]).is_relative_to(base)


def test_load_sources_rejects_old_or_mismatched_namespace(tmp_path, monkeypatch):
    base, index, document, source = _fixture(tmp_path)
    monkeypatch.setattr(phase, "_read_work_inputs", lambda path: [{"work_id": "work-1"}])
    import recommend_book.scale_content_recovery_v1 as recovery
    import recommend_book.usage_guard as usage
    monkeypatch.setattr(recovery, "_verify_mapping_identity", lambda database, mapping, works: None)
    monkeypatch.setattr(usage, "model_hash", lambda work: "input-hash-1")

    bad = dict(document)
    bad["namespace"] = str(tmp_path / "old-namespace")
    bad_index = _write(tmp_path / "bad-index.json", bad)
    with pytest.raises(ValueError, match="namespace"):
        phase.load_sources(bad_index, _sha(bad_index), base=base)

    bad = json.loads(json.dumps(document))
    bad["windows"][0]["run_name"] = "world21000-20261026-w000-v1"
    bad_index = _write(tmp_path / "bad-run-index.json", bad)
    with pytest.raises(ValueError, match="run name"):
        phase.load_sources(bad_index, _sha(bad_index), base=base)


def test_frozen_index_schema_requires_phase_prefix_receipt(tmp_path):
    base, index, document, _ = _fixture(tmp_path)
    old = json.loads(json.dumps(document))
    old["version"] = "world-novel-expansion-source-index-v1"
    for key in ("phase", "namespace", "operator", "catalogue_writes", "model_calls", "network_requests"):
        old.pop(key, None)
    old_index = _write(tmp_path / "old-schema-index.json", old)
    with pytest.raises(FileNotFoundError, match="prefix validation receipt"):
        phase.load_sources(old_index, _sha(old_index), base=base)


def test_execute_binds_two_slot_v2_to_phase_namespace_without_prior_control(tmp_path, monkeypatch):
    base, index, document, source = _fixture(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    exclusion = Path(document["exclusions"]["path"])
    window = {"window_index": 0, "run_name": "world21000-20261027-w000-v1",
        "source_path": str(source), "source_manifest_path": str(source / "manifest.json"),
        "mapping_path": str(source / "work-mapping.json"), "source_sha256": _sha(source / "manifest.json"),
        "mapping_sha256": _sha(source / "work-mapping.json"), "selected": 1}
    captured = {}
    monkeypatch.setattr(phase, "load_sources", lambda index, digest, *, base: [window])
    import recover_world_novel_expansion_v2 as recovery
    def fake_run(runtime_arg, window_arg, pins, **kwargs):
        captured.update(runtime=runtime_arg, window=window_arg, pins=pins, kwargs=kwargs)
        return {"stage": "actual_native_export_verified", "gate": {"passed": True}}
    monkeypatch.setattr(recovery, "_run", fake_run)

    result = phase.run_initial(index, _sha(index), runtime, base=base, operator="test-operator")

    assert result["phase_lineage"]["namespace"] == str(base)
    assert result["phase_lineage"]["prior_control_required"] is False
    assert result["phase_lineage"]["old_w003_gate_claimed_passed"] is False
    assert captured["kwargs"]["base"] == base
    assert captured["kwargs"]["prior_control"] is None
    assert captured["kwargs"]["prior_sha"] is None
    assert captured["kwargs"]["lineage"] is None
    assert captured["kwargs"]["continuation"] == result["phase_lineage"]
    assert captured["window"]["run_name"] == "world21000-20261027-w000-v1"
    assert Path(phase.__file__).resolve() in {Path(p).resolve() for p in captured["pins"]}
    assert Path(document["exclusions"]["path"]).resolve() in {Path(p).resolve() for p in captured["pins"]}


def test_check_is_explicitly_read_only_summary(tmp_path, monkeypatch):
    base, index, _, _ = _fixture(tmp_path)
    monkeypatch.setattr(phase, "load_sources", lambda index, digest, *, base: [{"selected": 3}])
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = phase.check(index, _sha(index), base=base)
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert result["selected"] == 3
    assert result["model_calls"] == result["network_requests"] == result["catalogue_writes"] == 0
    assert before == after
