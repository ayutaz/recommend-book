import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_scale20k_usage_v2.py"


def module():
    spec = importlib.util.spec_from_file_location("summarize20k_v2_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    base = tmp_path / "base"
    summary_dir = base / "run-summaries"
    export = base / "classification-exports-v3" / "scale20k-content-6801-6900-v1" / "manifest.json"
    run = save(base / "runtime" / "run" / "manifest.json", {
        "selected": 2, "completed": 1, "failed": 1,
        "usage": {"input_tokens": 10, "output_tokens": 4,
                   "cached_input_tokens": 2, "reasoning_output_tokens": 1,
                   "cache_write_input_tokens": 0},
        "config": {"model": "failed-model"}, "elapsed_seconds": 7.0,
    })
    review_file = save(base / "review" / "quality.json", {"review": True})
    failed_gate = {"passed": False, "selected": 2, "accepted": 1,
                   "failure_rate": 0.5, "reasons": ["fixture-failure"]}
    export = save(export, {
        "run_manifest": str(run),
        "artifact_sha256": {str(run): sha(run), str(review_file): sha(review_file)},
        "verified_projection": {"rows": [{"qid": "Qfailed"}]},
        "gate": failed_gate,
        "separate_review": {
            "review_path": str(review_file),
            "observed_usage": {"input_tokens": 20, "output_tokens": 8,
                                "cached_input_tokens": 0, "reasoning_output_tokens": 2,
                                "cache_write_input_tokens": 0},
            "groups": [{"observed_usage": {"input_tokens": 9999}}],
        },
    })
    control = save(base / "window-controls" / "scale20k-content-6801-6900-v1.json", {
        "gate": failed_gate,
        "control_artifacts": {
            "export": {"path": str(export), "sha256": sha(export)},
            "run": {"path": str(run), "sha256": sha(run)},
            "review": {"path": str(review_file), "sha256": sha(review_file)},
        },
    })
    summary = save(summary_dir / "scale20k-content-6801-6900-v1.json", {
        "run_name": "scale20k-content-6801-6900-v1", "gate": failed_gate,
        "public_manifest": str(export), "public_manifest_sha256": sha(export),
        "control_path": str(control), "control_sha256": sha(control),
        "native_selected": 2, "native_completed": 1, "native_failed": 1,
        "native_usage": json.loads(run.read_text(encoding="utf-8"))["usage"],
        "separate_review": json.loads(export.read_text(encoding="utf-8"))["separate_review"],
        "native_terminal_pid_interval_seconds": 1.0,
        "pid_inclusive_model_stage_seconds": 2.0,
        "native_launch_through_public_import_wall_seconds": 3.0,
    })
    healthy = save(summary_dir / "scale20k-content-0001-0100-v1.json", {
        "run_name": "scale20k-content-0001-0100-v1", "gate": {"passed": True},
    })
    quarantine_rows = save(base / "quarantine" / "rows.json", {
        "control_path": str(control), "control_sha256": sha(control),
        "export_path": str(export), "export_sha256": sha(export),
        "rows": [{"qid": "Qfailed", "manifest_path": str(export),
                  "manifest_sha256": sha(export), "labels_json": "[]",
                  "provenance_json": "{}"}],
    })
    quarantine = save(base / "quarantine" / "receipt.json", {
        "status": "quarantined", "removed": 1, "remaining": 99,
        "failed_gate": failed_gate,
        "rows_before_path": str(quarantine_rows),
        "rows_before_sha256": sha(quarantine_rows),
    })
    audit = save(tmp_path / "audit-v2.json", {
        "status": "complete", "counts": {"eligible_native": 20000,
                                             "new_eligible_native": 10000},
        "proofs": {"manifests": [], "controls": []},
        "excluded_failed_windows": [{
            "summary": {"path": str(summary), "sha256": sha(summary)},
            "control": {"path": str(control), "sha256": sha(control)},
            "export": {"path": str(export), "sha256": sha(export)},
            "quarantine": {"path": str(quarantine), "sha256": sha(quarantine)},
            "quarantine_rows": {"path": str(quarantine_rows), "sha256": sha(quarantine_rows)},
            "removed": 1, "catalogue_rows_remaining": 0, "failed_gate": failed_gate,
        }],
    })
    return base, audit, healthy


def add_real_window(base, name, qid, *, add_to_audit=None):
    """Create the small authenticated shape consumed by frozen v1."""
    run = save(base / "runtime" / name / "manifest.json", {
        "selected": 1, "completed": 1, "failed": 0,
        "usage": {"input_tokens": 11, "output_tokens": 5,
                   "cached_input_tokens": 2, "reasoning_output_tokens": 1,
                   "cache_write_input_tokens": 0},
        "config": {"model": "healthy-model"}, "elapsed_seconds": 4.0,
    })
    export = save(base / "classification-exports-v3" / name / "manifest.json", {
        "run_manifest": str(run),
        "artifact_sha256": {str(run): sha(run)},
        "verified_projection": {"rows": [{"qid": qid}]},
        "gate": {"accepted": 1}, "separate_review": {},
    })
    control = save(base / "window-controls" / (name + ".json"), {
        "gate": {"accepted": 1},
        "control_artifacts": {
            "export": {"path": str(export), "sha256": sha(export)},
            "run": {"path": str(run), "sha256": sha(run)},
        },
    })
    summary = save(base / "run-summaries" / (name + ".json"), {
        "run_name": name, "gate": {"passed": True},
        "public_manifest": str(export), "public_manifest_sha256": sha(export),
        "control_path": str(control), "control_sha256": sha(control),
        "native_selected": 1, "native_completed": 1, "native_failed": 0,
        "native_usage": json.loads(run.read_text(encoding="utf-8"))["usage"],
        "separate_review": {},
        "native_terminal_pid_interval_seconds": 1.0,
        "pid_inclusive_model_stage_seconds": 2.0,
        "native_launch_through_public_import_wall_seconds": 3.0,
    })
    if add_to_audit is not None:
        add_to_audit["proofs"]["manifests"].append(
            {"path": str(export), "sha256": sha(export)})
        add_to_audit["proofs"]["controls"].append(
            {"path": str(control), "sha256": sha(control)})
    return summary


def test_failed_window_is_explicitly_quarantined_and_not_adopted(tmp_path, monkeypatch):
    base, audit, healthy = fixture(tmp_path)
    m = module()
    seen = []

    def fake_v1(**kwargs):
        seen.extend(path.name for path in m.V1._summary_files(kwargs["base_dir"]))
        return {"version": "scale20k-usage-summary-v1", "status": "complete",
                "totals": {"selected": 100, "completed": 100, "failed": 0, "adopted": 10000},
                "usage": {}, "usage_coverage": {}}

    monkeypatch.setattr(m.V1, "summarize", fake_v1)
    report = m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                         minimum_eligible_native=2, minimum_new_native=2)
    assert seen == [healthy.name]
    assert report["totals"]["adopted"] == 10000
    assert report["excluded_failed_totals"]["adopted"] is None
    assert report["excluded_failed_totals"]["failed"] == 1
    assert report["excluded_failed_windows"][0]["usage"]["review"]["input_tokens"] == 20
    assert report["excluded_failed_windows"][0]["usage"]["review"]["input_tokens"] != 9999


def test_failed_summary_cannot_be_omitted_or_tampered(tmp_path):
    base, audit, _ = fixture(tmp_path)
    m = module()
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    audit_value["excluded_failed_windows"] = []
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="not explicitly excluded"):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                    minimum_eligible_native=2, minimum_new_native=2)

    base, audit, _ = fixture(tmp_path / "tampered")
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    audit_value["excluded_failed_windows"][0]["quarantine"]["sha256"] = "0" * 64
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="excluded quarantine SHA"):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                    minimum_eligible_native=2, minimum_new_native=2)


def test_exclusion_counts_and_native_count_types_are_authenticated(tmp_path):
    base, audit, _ = fixture(tmp_path)
    m = module()
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    audit_value["excluded_failed_windows"][0]["catalogue_rows_remaining"] = False
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="quarantine counts"):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                    minimum_eligible_native=2, minimum_new_native=2)

    base, audit, _ = fixture(tmp_path / "summary-type")
    summary_path = base / "run-summaries" / "scale20k-content-6801-6900-v1.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["native_failed"] = True
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    audit_value["excluded_failed_windows"][0]["summary"]["sha256"] = sha(summary_path)
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="run counts"):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                    minimum_eligible_native=2, minimum_new_native=2)


def test_v1_namespace_is_sha_frozen():
    m = module()
    assert sha(m.V1_PATH) == m.V1_SHA256


def test_real_frozen_v1_integration_requires_all_healthy_windows(tmp_path):
    base, audit, incomplete = fixture(tmp_path)
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    audit_value["counts"]["new_eligible_native"] = 1
    audit_value["proofs"] = {"manifests": [], "controls": []}
    # Replace the deliberately incomplete fixture summary with an authenticated
    # healthy summary, while retaining the separately quarantined failed run.
    incomplete.unlink()
    add_real_window(base, "scale20k-content-0001-0100-v1", "Qhealthy",
                    add_to_audit=audit_value)
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    m = module()
    report = m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                         minimum_eligible_native=2, minimum_new_native=1)
    assert report["totals"] == {"selected": 1, "completed": 1, "failed": 0,
                                 "adopted": 1, "display": None, "non_genre": None}
    assert report["excluded_failed_totals"]["failed"] == 1

    # A second real healthy summary cannot be silently omitted from the pinned
    # final audit manifest set.
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    add_real_window(base, "scale20k-content-0101-0200-v1", "Qhealthy2")
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="not in final audit proofs"):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                    minimum_eligible_native=2, minimum_new_native=1)
