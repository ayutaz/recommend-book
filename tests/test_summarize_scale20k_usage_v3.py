"""Offline tests for separate model-zero hold accounting."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_scale20k_usage_v3.py"


def module():
    spec = importlib.util.spec_from_file_location("usage20k_v3_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def hold(index, status, selected):
    return {"window_index": index, "run_name": f"run-{index}", "selected": selected,
            "model_calls": 0, "native_failures": 0, "native_rows_for_selected": 0,
            "same_inputs_retried": 0, "status": status,
            "eligible_before": 18669, "eligible_after": 18669,
            "hold": {"path": f"hold-{index}.json", "sha256": "a" * 64}}


def audit_value():
    return {"version": "scale20k-catalogue-audit-v4", "excluded_unstarted_windows": [
        hold(161, "held_unstarted_runtime_import_failure", 81),
        hold(196, "held_unstarted_order_check", 93),
        hold(197, "held_unstarted_alias_check", 92),
    ]}


def test_validates_all_pre_model_holds_separately():
    m = module()
    result = m.validate_unstarted_windows(audit_value())
    assert {item["window_index"] for item in result} == {161, 196, 197}
    assert sum(item["selected"] for item in result) == 266


def test_actual_audit_shape_adds_legacy_hold_once():
    m = module()
    value = audit_value()
    legacy = value["excluded_unstarted_windows"].pop(0)
    legacy["processes"] = {name: {} for name in (
        "preflight", "controller_worker", "controller_observer", "sequential_queue")}
    value["legacy_recovery_proofs"] = {"unstarted": legacy}
    assert [item["window_index"] for item in m.validate_unstarted_windows(value)] == [161, 196, 197]


@pytest.mark.parametrize("mismatch", [False, True])
def test_display_gate_failure_with_zero_native_failures_stays_excluded(tmp_path, mismatch):
    support_spec = importlib.util.spec_from_file_location(
        "usage_v2_fixture", Path(__file__).with_name("test_summarize_scale20k_usage_v2.py"))
    support = importlib.util.module_from_spec(support_spec)
    support_spec.loader.exec_module(support)
    base, audit_file, _ = support.fixture(tmp_path)
    audit = json.loads(audit_file.read_bytes())
    item = audit["excluded_failed_windows"][0]
    docs = {name: json.loads(Path(item[name]["path"]).read_bytes()) for name in (
        "summary", "control", "export", "quarantine", "quarantine_rows")}
    run_path = Path(docs["export"]["run_manifest"])
    run = json.loads(run_path.read_bytes())
    run.update(selected=1, completed=1, failed=0)
    support.save(run_path, run)
    gate = {"passed": False, "selected": 1, "accepted": 1, "failure_rate": 0.0,
            "actual_display_feature_works": 0, "actual_display_adoption_rate": 0.0,
            "reasons": ["actual_display_feature_adoption_below_70_percent"]}
    docs["export"]["gate"] = gate
    docs["export"]["artifact_sha256"][str(run_path)] = support.sha(run_path)
    for name in ("export", "control", "summary", "quarantine_rows", "quarantine"):
        doc = docs[name]
        if name == "control":
            doc["gate"] = gate
            doc["control_artifacts"]["run"]["sha256"] = support.sha(run_path)
            doc["control_artifacts"]["export"] = item["export"].copy()
        elif name == "summary":
            doc.update(gate=gate, native_selected=1, native_completed=1,
                       native_failed=1 if mismatch else 0,
                       public_manifest_sha256=item["export"]["sha256"],
                       control_sha256=item["control"]["sha256"])
        elif name == "quarantine_rows":
            doc.update(control_sha256=item["control"]["sha256"],
                       export_sha256=item["export"]["sha256"])
        elif name == "quarantine":
            doc.update(failed_gate=gate, rows_before_sha256=item["quarantine_rows"]["sha256"])
        path = Path(item[name]["path"])
        support.save(path, doc)
        item[name]["sha256"] = support.sha(path)
    item["failed_gate"] = gate
    frozen = module()._load_v2()
    if mismatch:
        with pytest.raises(ValueError, match="counts differ"):
            frozen._validate_exclusions(audit, base)
    else:
        _, excluded = frozen._validate_exclusions(audit, base)
        assert excluded[0]["counts"]["failed"] == 0
        assert excluded[0]["counts"]["removed"] == 1
        assert excluded[0]["counts"]["adopted"] is None


@pytest.mark.parametrize("change", ["missing", "model", "catalogue"])
def test_rejects_incomplete_or_mutated_hold(change):
    m = module()
    value = audit_value()
    if change == "missing":
        value["excluded_unstarted_windows"] = value["excluded_unstarted_windows"][:2]
    elif change == "model":
        value["excluded_unstarted_windows"][1]["model_calls"] = 1
    else:
        value["excluded_unstarted_windows"][2]["eligible_after"] += 1
    with pytest.raises(ValueError):
        m.validate_unstarted_windows(value)


def test_summary_delegates_healthy_and_failed_totals_to_frozen_v2(tmp_path, monkeypatch):
    m = module()
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps(audit_value()), encoding="utf-8")

    class Frozen:
        def summarize(self, **kwargs):
            return {"version": "scale20k-usage-summary-v2", "status": "complete",
                    "totals": {"adopted": 20000}, "excluded_failed_totals": {"failed": 1}}

    monkeypatch.setattr(m, "_load_v2", lambda: Frozen())
    report = m.summarize(audit_path=audit, audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest(),
                         base_dir=tmp_path)
    assert report["version"] == "scale20k-usage-summary-v3"
    assert report["totals"]["adopted"] == 20000
    assert report["excluded_unstarted_totals"] == {
        "windows": 3, "selected": 266, "model_calls": 0,
        "native_failures": 0, "native_rows_for_selected": 0, "adopted": None,
        "usage": None,
        "note": "Pre-model holds are reported separately and never enter healthy or failed native totals.",
    }
