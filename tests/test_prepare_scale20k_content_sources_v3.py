import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from recommend_book.models import SourceRecord, Title


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "scripts" / "prepare_scale20k_content_sources_v3.py"
    spec = importlib.util.spec_from_file_location("prepare_scale20k_content_sources_v3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def origin_fixture(tmp_path, module, *, origin=162):
    old_name = "scale20k-content-6901-7000-v1"
    old = tmp_path / "old-source"
    manifest = old / "manifest.json"
    write(manifest, {"version": "scale-content-classification-source-v1", "stage": "prepared_not_started",
                     "window_index": origin, "run_name": old_name, "selected": 1})
    write(old / "work-mapping.json", [{"work_id": "w1", "wikidata_qid": "Q1",
                                       "input_sha256": "a" * 64}])
    index = tmp_path / "source-index-v55.json"
    write(index, {"windows": [{"window_index": origin, "run_name": old_name,
                               "source_path": str(old.resolve()),
                               "source_sha256": module.sha(manifest), "selected": 1}]})
    return old, manifest, index, old_name


def test_recovery_mapping_uses_fresh_global_and_canonical_v2_name():
    module = load()
    assert module.origin_window_for(161) == 162
    assert module.origin_window_for(214) == 215
    assert module.canonical_run_name(161) == "scale20k-content-6801-6900-v2"
    assert module.canonical_run_name(214) == "scale20k-content-12101-12200-v2"
    with pytest.raises(ValueError):
        module.canonical_run_name(160)


def test_origin_requires_exact_source_and_index_pins_before_output(tmp_path):
    module = load()
    old, manifest, index, old_name = origin_fixture(tmp_path, module)
    source_sha = module.sha(manifest)
    index_sha = module.sha(index)
    lineage = module.validate_origin(origin_source=old, origin_source_sha256=source_sha,
        origin_index=index, origin_index_sha256=index_sha, origin_window_index=162,
        origin_run_name=old_name, new_window_index=161)
    assert lineage["window_index"] == 162
    assert lineage["source_sha256"] == source_sha
    with pytest.raises(ValueError, match="origin source manifest SHA"):
        module.validate_origin(origin_source=old, origin_source_sha256="0" * 64,
            origin_index=index, origin_index_sha256=index_sha, origin_window_index=162,
            origin_run_name=old_name, new_window_index=161)
    with pytest.raises(ValueError, match="origin source-index"):
        module.validate_origin(origin_source=old, origin_source_sha256=source_sha,
            origin_index=index, origin_index_sha256="f" * 64, origin_window_index=162,
            origin_run_name=old_name, new_window_index=161)


def test_prepare_pins_actual_new_manifest_and_keeps_origin_unchanged(tmp_path, monkeypatch):
    module = load()
    old, old_manifest, index, old_name = origin_fixture(tmp_path, module)
    source_sha = module.sha(old_manifest)
    index_sha = module.sha(index)
    records = tmp_path / "records.jsonl"
    raw = tmp_path / "raw.json"; raw.write_text("{}", encoding="utf-8")
    record = SourceRecord(source="wikidata", source_id="Q1", provider_id="wikidata",
        record_kind="work", work_kind="novel", source_url="https://www.wikidata.org/wiki/Q1",
        retrieved_at="2026-10-23", titles=[Title(value="Q1")], raw_path=str(raw),
        raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), rights_policy_id="wikidata-cc0")
    records.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    catalogue = tmp_path / "catalogue.sqlite"
    catalogue.write_bytes(b"catalogue")
    conditions = tmp_path / "conditions.json"
    conditions.write_text("{}", encoding="utf-8")
    observer = tmp_path / "run_scale20k_native_v3.py"
    observer.write_text("# fixture driver3\n", encoding="utf-8")

    def fake_prepare(records_path, catalogue_path, output, conditions_path, **kwargs):
        output.mkdir(parents=True, exist_ok=False)
        ordered = output / "ordered-inputs.jsonl"; ordered.write_text("input\n", encoding="utf-8")
        ledger = output / "usage-ledger.json"; write(ledger, {"version": "fixture-ledger"})
        database = output / "dataset.sqlite"; database.write_bytes(b"fixture-db")
        mapping = output / "work-mapping.json"
        write(mapping, [{"work_id": "w1", "wikidata_qid": "Q1", "input_sha256": "a" * 64}])
        write(output / "manifest.json", {
            "version": "scale-content-classification-source-v1", "stage": "prepared_not_started",
            "window_index": kwargs["window_index"], "run_name": kwargs["run_name"], "selected": 1,
            "ordered_inputs_path": str(ordered), "usage_ledger_path": str(ledger),
            "source_database": str(database), "chunks": [{"run_name": kwargs["run_name"]}],
        })
        return {"selected": 1, "required_purpose_admissions_allowed": True}

    import sys
    import types
    fake_v2 = types.ModuleType("prepare_scale20k_content_sources_v2")
    fake_v2.prepare_native_window = fake_prepare
    monkeypatch.setitem(sys.modules, "prepare_scale20k_content_sources_v2", fake_v2)
    monkeypatch.setattr(module, "_catalogue_snapshot", lambda _path, qids: {
        qid: {"version_id": "v", "display_allowed": 1, "recommendable": 0,
              "genres": [], "native": None} for qid in qids})

    output = tmp_path / "new-source"
    result = module.prepare_native_window(records, catalogue, output, conditions,
        window_index=161, run_name="scale20k-content-6801-6900-v2",
        origin_source=old, origin_source_sha256=source_sha,
        origin_index=index, origin_index_sha256=index_sha,
        origin_window_index=162, origin_run_name=old_name, observer=observer)
    policy = json.loads((output / "native-recovery-policy-v3.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert result["selected"] == 1
    assert policy["new_window_index"] == 161
    assert policy["origin"]["window_index"] == 162
    assert policy["qids"] == ["Q1"]
    assert manifest["recovery_generation"] == "scale20k-recovery-gate161-v1"
    assert manifest["upstream_window_index"] == 162
    assert manifest["upstream_source_index_sha256"] == index_sha
    assert policy["source_builder_sha256"] == module.sha(module.__file__)
    assert policy["observer_sha256"] == module.sha(observer)
    assert module.sha(old_manifest) == source_sha
    assert module.sha(index) == index_sha
    with pytest.raises(FileExistsError):
        module.prepare_native_window(records, catalogue, output, conditions,
            window_index=161, run_name="scale20k-content-6801-6900-v2",
            origin_source=old, origin_source_sha256=source_sha,
            origin_index=index, origin_index_sha256=index_sha,
            origin_window_index=162, origin_run_name=old_name, observer=observer)


def test_origin_prepared_stage_is_required_and_failed_stage_is_never_recovered(tmp_path):
    module = load()
    old, manifest, index, old_name = origin_fixture(tmp_path, module)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["stage"] = "classified"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    index_doc = json.loads(index.read_text(encoding="utf-8"))
    index_doc["windows"][0]["source_sha256"] = module.sha(manifest)
    index.write_text(json.dumps(index_doc), encoding="utf-8")
    with pytest.raises(ValueError, match="prepared, unstarted"):
        module.validate_origin(origin_source=old, origin_source_sha256=module.sha(manifest),
            origin_index=index, origin_index_sha256=module.sha(index), origin_window_index=162,
            origin_run_name=old_name, new_window_index=161)


def test_strict_subset_requires_explicit_guard_hold_rows(tmp_path):
    module = load()
    proof = tmp_path / "holds.json"
    write(proof, [{"qid": "Q1", "status": "scope_held"}])
    with pytest.raises(ValueError, match="UsageGuard-held"):
        module._validate_subset_holds(proof, {"Q1"})
    write(proof, [{"qid": "Q1", "status": "guard_held"}])
    result = module._validate_subset_holds(proof, {"Q1"})
    assert result["qids"] == ["Q1"] and result["sha256"] == module.sha(proof)


def test_exact_shortfall_receipt_authorizes_only_real_final_subset(tmp_path, monkeypatch):
    module = load()
    receipt = tmp_path / "exact-shortfall.json"
    receipt_sha = write(receipt, {
        "version": "scale20k-exact-shortfall-v1",
        "run_index": 161,
        "run_name": "scale20k-content-6801-6900-v2",
        "target_eligible_native": 20000,
        "eligible_native_count": 19999,
        "remaining": 1,
        "origin_index_sha256": "i" * 64,
        "origin_source_sha256": "s" * 64,
        "selected": [{"qid": "Q1", "input_sha256": "a" * 64}],
        "existing_native_overlap_count": 0,
        "existing_attempt_overlap_count": 0,
        "prior_control": {"path": str(tmp_path / "prior.json"), "sha256": "p" * 64},
    })
    catalogue = tmp_path / "catalogue.sqlite"
    catalogue.write_bytes(b"fixture")
    monkeypatch.setattr(module, "_catalogue_eligible_count", lambda _path: 19999)
    monkeypatch.setattr(module, "_catalogue_native_overlap", lambda _path, _qids: set())
    monkeypatch.setattr(module, "_verify_prior_control", lambda _receipt, _window: {
        "path": str(tmp_path / "prior.json"), "sha256": "p" * 64,
        "window_index": 160, "run_name": "scale20k-content-6701-6800-v1"})
    attempts = tmp_path / "attempt-reservations"
    write(attempts / "one.json", {"qids": ["Q9"], "input_hashes": ["z" * 64]})
    monkeypatch.setattr(module, "ATTEMPT_RESERVATIONS_ROOT", attempts)
    origin = {"selected": 2, "qids": ["Q1", "Q2"],
              "input_identity": {"Q1": "a" * 64, "Q2": "b" * 64}}
    value, selected, omitted = module._validate_exact_shortfall(
        receipt, receipt_sha, catalogue=catalogue, origin=origin,
        origin_index_sha256="i" * 64, origin_source_sha256="s" * 64,
        window_index=161, record_qids=["Q1"])
    assert value["remaining"] == 1
    assert selected == {"Q1": "a" * 64}
    assert omitted == {"Q2"}


def test_exact_shortfall_receipt_rejects_overlap_and_malformed_pair(tmp_path, monkeypatch):
    module = load()
    catalogue = tmp_path / "catalogue.sqlite"
    catalogue.write_bytes(b"fixture")
    origin = {"selected": 2, "qids": ["Q1", "Q2"],
              "input_identity": {"Q1": "a" * 64, "Q2": "b" * 64}}
    receipt = tmp_path / "exact-shortfall.json"
    receipt_sha = write(receipt, {
        "version": "scale20k-exact-shortfall-v1", "run_index": 161,
        "run_name": "scale20k-content-6801-6900-v2",
        "target_eligible_native": 20000, "eligible_native_count": 19999,
        "remaining": 1, "origin_index_sha256": "i" * 64,
        "origin_source_sha256": "s" * 64,
        "selected": [{"qid": "Q1", "input_sha256": "a" * 64}],
        "existing_native_overlap_count": 0, "existing_attempt_overlap_count": 0,
        "prior_control": {"path": str(tmp_path / "prior.json"), "sha256": "p" * 64},
    })
    monkeypatch.setattr(module, "_catalogue_eligible_count", lambda _path: 19999)
    monkeypatch.setattr(module, "_catalogue_native_overlap", lambda _path, _qids: set())
    monkeypatch.setattr(module, "_verify_prior_control", lambda _receipt, _window: {
        "path": str(tmp_path / "prior.json"), "sha256": "p" * 64,
        "window_index": 160, "run_name": "scale20k-content-6701-6800-v1"})
    attempts = tmp_path / "attempt-reservations"
    write(attempts / "one.json", {"qids": ["Q1"], "input_hashes": []})
    monkeypatch.setattr(module, "ATTEMPT_RESERVATIONS_ROOT", attempts)
    with pytest.raises(ValueError, match="existing attempt"):
        module._validate_exact_shortfall(receipt, receipt_sha, catalogue=catalogue,
            origin=origin, origin_index_sha256="i" * 64, origin_source_sha256="s" * 64,
            window_index=161, record_qids=["Q1"])


def test_origin_index_cannot_reuse_failed_or_wrong_window(tmp_path):
    module = load()
    old, manifest, index, old_name = origin_fixture(tmp_path, module, origin=162)
    source_sha, index_sha = module.sha(manifest), module.sha(index)
    with pytest.raises(ValueError, match="origin and recovery window"):
        module.validate_origin(origin_source=old, origin_source_sha256=source_sha,
            origin_index=index, origin_index_sha256=index_sha, origin_window_index=162,
            origin_run_name=old_name, new_window_index=162)
    document = json.loads(index.read_text(encoding="utf-8"))
    document["windows"][0]["run_name"] = "scale20k-content-6801-6900-v1"
    index.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="origin run name"):
        module.validate_origin(origin_source=old, origin_source_sha256=source_sha,
            origin_index=index, origin_index_sha256=module.sha(index), origin_window_index=162,
            origin_run_name=old_name, new_window_index=161)
