"""Offline fixtures for the v7 recovery source wrapper builder."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_scale100k_review_recovery_sources_v1",
    ROOT / "scripts/prepare_scale100k_review_recovery_sources_v1.py",
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path, *, duplicate=False, baseline_overlap=False):
    old_root = tmp_path / "old-v2"
    old_root.mkdir()
    old_override = tmp_path / "old-override.json"
    old_map = {}
    for old_logical in (49, 50, 51, 52):
        source = old_root / f"window-{old_logical}"
        source.mkdir()
        rows = [
            {"wikidata_qid": f"Q{old_logical}01", "input_sha256": f"{old_logical:02d}01" * 16},
            {"wikidata_qid": f"Q{old_logical}02", "input_sha256": f"{old_logical:02d}02" * 16},
        ]
        if baseline_overlap and old_logical == 50:
            rows[0]["wikidata_qid"] = "QBASE"
        if duplicate and old_logical == 51:
            rows[0]["wikidata_qid"] = "Q5001"
        mapping = source / "work-mapping.json"
        mapping.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
        inputs = source / "inputs.jsonl"
        ledger = source / "ledger.json"
        database = source / "source.sqlite"
        inputs.write_text("input\n", encoding="utf-8")
        ledger.write_text("{}\n", encoding="utf-8")
        database.write_bytes(b"fixture-db")
        manifest = {
            "version": "scale-content-classification-source-v1",
            "stage": "prepared_not_started",
            "selected": len(rows),
            "records_path": str(inputs),
            "records_sha256": _sha(inputs),
            "official_conditions_path": str(tmp_path / "conditions.json"),
            "official_conditions_sha256": "c" * 64,
            "ordered_inputs_path": str(inputs),
            "ordered_inputs_sha256": _sha(inputs),
            "source_database": str(database),
            "source_database_sha256": _sha(database),
            "usage_ledger_path": str(ledger),
            "usage_ledger_sha256": _sha(ledger),
            "required_purpose_admissions_allowed": True,
            "all_six_purpose_admissions_allowed": True,
            "window_index": old_logical,
            "chunks": [{
                "index": 0,
                "run_name": f"scale100k-content-{old_logical:04d}-{old_logical + 99:04d}-v6",
                "inputs_path": str(inputs), "inputs_sha256": _sha(inputs),
                "usage_ledger_path": str(ledger), "usage_ledger_sha256": _sha(ledger),
                "selected": len(rows),
            }],
            "model_calls": 0,
            "web_requests": 0,
        }
        (source / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
        old_map[str(old_logical)] = str(source.resolve())
    old_override.write_text(json.dumps(old_map, sort_keys=True) + "\n", encoding="utf-8")

    baseline = tmp_path / "baseline-native-5000.jsonl"
    qids = ["QBASE" if baseline_overlap and i == 0 else f"QB{i}" for i in range(5000)]
    baseline.write_text("".join(json.dumps({"qid": qid}) + "\n" for qid in qids), encoding="utf-8")
    reservations = tmp_path / "reservations"
    reservations.mkdir()
    (reservations / "r.json").write_text(json.dumps({"qids": ["QRES"], "input_hashes": ["h-res"]}), encoding="utf-8")

    primary = tmp_path / "diagnostic.json"
    diagnostic = {
        "version": "scale100k-review-failure-diagnostic-v1",
        "run_name": "scale100k-content-0701-0800-v6",
        "selected_works": 100,
        "model_calls_added": 0,
        "original_artifacts_modified": False,
        "conservative_all_failed_group_hold": {"passes_unchanged_max_0_05": False},
    }
    primary.write_text(json.dumps(diagnostic, sort_keys=True), encoding="utf-8")
    control = tmp_path / "control48.json"
    control.write_text("control48\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    return old_override, tmp_path / "new-v3", tmp_path / "new-overrides.json", primary, control, baseline, reservations, runtime_root


def test_builds_canonical_v7_wrappers_without_copying_source_inputs(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path)
    before = {p: p.read_bytes() for p in old.parent.glob("old-v2/window-*/manifest.json")}
    result = builder.build_wrappers(
        old_override=old, output_root=new, output_override=override,
        diagnostic=diagnostic, successful_control=control,
        baseline=baseline, reservations=reservations, old_logical_end=52,
        runtime_root=runtime_root,
    )
    assert result["wrapper_count"] == 3
    paths = json.loads(override.read_bytes())
    assert sorted(paths) == ["49", "50", "51"]
    for logical, folder in paths.items():
        manifest = json.loads((Path(folder) / "manifest.json").read_bytes())
        start = (int(logical) - 42) * 100 + 1
        assert manifest["window_index"] == int(logical)
        assert manifest["chunks"][0]["run_name"] == f"scale100k-content-{start:04d}-{start + 99:04d}-v7"
        lineage = manifest["recovery_lineage"]
        assert lineage["failed_window_0701_0800_v6_preserved_unadopted"] is True
        assert lineage["failed_diagnostic_receipt_sha256"] == _sha(diagnostic)
        assert lineage["successful_control_sha256"] == _sha(control)
        assert lineage["helper_code_path"] == str(Path(builder.__file__).resolve())
        assert lineage["helper_code_sha256"] == _sha(Path(builder.__file__).resolve())
        assert "reserve_window" in lineage["old_v18_exclusion_responsibility"]
        assert manifest["source_database"].startswith(str(old.parent))
        assert (Path(folder) / "work-mapping.json").read_bytes()
    assert {p: p.read_bytes() for p in old.parent.glob("old-v2/window-*/manifest.json")} == before


def test_createonly_outputs_cannot_be_overwritten(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path)
    kwargs = dict(old_override=old, output_root=new, output_override=override,
                  diagnostic=diagnostic, successful_control=control,
                  baseline=baseline, reservations=reservations, old_logical_end=52,
                  runtime_root=runtime_root)
    builder.build_wrappers(**kwargs)
    before = override.read_bytes()
    with pytest.raises(FileExistsError):
        builder.build_wrappers(**kwargs)
    assert override.read_bytes() == before


def test_baseline_or_attempted_overlap_is_rejected(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path, baseline_overlap=True)
    with pytest.raises(ValueError, match="overlaps baseline"):
        builder.build_wrappers(old_override=old, output_root=new, output_override=override,
                               diagnostic=diagnostic, successful_control=control,
                               baseline=baseline, reservations=reservations, old_logical_end=52,
                               runtime_root=runtime_root)


def test_wrappers_must_not_overlap_each_other(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path, duplicate=True)
    with pytest.raises(ValueError, match="overlap each other"):
        builder.build_wrappers(old_override=old, output_root=new, output_override=override,
                               diagnostic=diagnostic, successful_control=control,
                               baseline=baseline, reservations=reservations, old_logical_end=52,
                               runtime_root=runtime_root)


def test_started_origin_runtime_is_rejected_before_wrapper_creation(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path)
    manifest = json.loads((tmp_path / "old-v2" / "window-50" / "manifest.json").read_bytes())
    run_name = manifest["chunks"][0]["run_name"]
    started = runtime_root / f"terminal-{run_name}"
    started.mkdir(parents=True)
    with pytest.raises(ValueError, match="origin already started"):
        builder.build_wrappers(old_override=old, output_root=new, output_override=override,
                               diagnostic=diagnostic, successful_control=control,
                               baseline=baseline, reservations=reservations, old_logical_end=52,
                               runtime_root=runtime_root)
    assert not new.exists()
    assert not override.exists()


def test_custom_helper_cannot_change_lineage_authority(tmp_path):
    old, new, override, diagnostic, control, baseline, reservations, runtime_root = _fixture(tmp_path)
    custom = tmp_path / "other-helper.py"
    custom.write_text("other\n", encoding="utf-8")
    with pytest.raises(ValueError, match="helper must be the recovery wrapper builder itself"):
        builder.build_wrappers(old_override=old, output_root=new, output_override=override,
                               diagnostic=diagnostic, successful_control=control, helper=custom,
                               baseline=baseline, reservations=reservations, old_logical_end=52,
                               runtime_root=runtime_root)
