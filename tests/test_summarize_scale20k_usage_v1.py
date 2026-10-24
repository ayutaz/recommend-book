import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "summarize_scale20k_usage_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("summarize20k_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, native_usage=True, review_usage=True):
    base = tmp_path / "base"
    summaries = base / "run-summaries"
    run_dir = base / "runtime" / "run"
    review = base / "review.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("review", encoding="utf-8")
    run = save(run_dir / "manifest.json", {
        "selected": 2, "completed": 2, "failed": 0,
        "usage": ((native_usage if isinstance(native_usage, dict) else {"input_tokens": 10, "output_tokens": 4,
                    "cached_input_tokens": 3, "reasoning_output_tokens": 1,
                    "cache_write_input_tokens": 0}) if native_usage else None),
        "elapsed_seconds": 12.5,
        "config": {"model": "fixture-model"},
    })
    export = save(base / "classification-exports-v3" /
                  "scale20k-content-0001-0100-v1" / "manifest.json", {
        "run_manifest": str(run),
        "artifact_sha256": {str(run): sha(run)},
        "verified_projection": {"rows": [{"qid": "Q1"}, {"qid": "Q2"}]},
        "gate": {"accepted": 2},
        "separate_review": ({
            "review_path": str(review),
            "observed_usage": ({"input_tokens": 20, "output_tokens": 8,
                                "cached_input_tokens": 5, "reasoning_output_tokens": 2,
                                "cache_write_input_tokens": 0} if review_usage else None),
        } if review_usage else {}),
    })
    control = save(base / "controls" / "window.json", {
        "gate": {"accepted": 2},
        "control_artifacts": {
            "export": {"path": str(export), "sha256": sha(export)},
            "run": {"path": str(run), "sha256": sha(run)},
            "review": {"path": str(review), "sha256": sha(review)},
        }
    })
    summary = {
        "run_name": "scale20k-content-0001-0100-v1",
        "public_manifest": str(export), "public_manifest_sha256": sha(export),
        "control_path": str(control), "control_sha256": sha(control),
        "native_selected": 2, "native_completed": 2, "native_failed": 0,
        "native_usage": (json.loads(run.read_text(encoding="utf-8"))["usage"]
                         if native_usage else None),
        "separate_review": ({
            "review_path": str(review),
            "observed_usage": (json.loads(export.read_text(encoding="utf-8"))
                                .get("separate_review", {}).get("observed_usage")),
            # This deliberately disagrees with observed_usage. Groups are not
            # another usage source and must never be summed.
            "groups": [{"observed_usage": {"input_tokens": 9999}}],
        } if review_usage else {}),
        "native_terminal_pid_interval_seconds": 3.0,
        "pid_inclusive_model_stage_seconds": 4.0,
        "native_launch_through_public_import_wall_seconds": 5.0,
    }
    save(summaries / "scale20k-content-0001-0100-v1.json", summary)
    save(base / "attempt-reservations" / "scale20k-content-0001-0100-v1.json",
         {"run_name": summary["run_name"]})
    audit = save(tmp_path / "audit.json", {
        "version": "scale20k-catalogue-audit-v1", "status": "complete",
        "counts": {"eligible_native": 2, "new_eligible_native": 2, "new_display_feature_works": 1,
                    "new_display_content_non_genre_feature_works": 1},
        "proofs": {"manifests": [{"path": str(export), "sha256": sha(export)}],
                   "controls": [{"path": str(control), "sha256": sha(control)}]},
    })
    return base, audit


def test_summary_authenticates_pins_and_does_not_sum_review_groups(tmp_path):
    base, audit = fixture(tmp_path)
    m = module()
    report = m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                         minimum_eligible_native=2, minimum_new_native=2)
    assert report["totals"] == {"selected": 2, "completed": 2, "failed": 0,
                                "adopted": 2, "display": 1, "non_genre": 1}
    assert report["usage"]["native"]["input_tokens"] == 10
    assert report["usage"]["review"]["input_tokens"] == 20
    assert report["timing"]["wall_time_seconds"]["native_launch_through_public_import_sum"] == 5.0
    assert report["timing"]["elapsed_seconds"]["native_run_manifest_sum"] == 12.5
    assert report["usage"]["review"]["input_tokens"] != 9999


def test_missing_usage_is_unknown_with_coverage(tmp_path):
    base, audit = fixture(tmp_path, native_usage=False, review_usage=False)
    report = module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                                minimum_eligible_native=2, minimum_new_native=2)
    assert report["usage"]["native"]["input_tokens"] is None
    assert report["usage"]["review"]["input_tokens"] is None
    assert report["usage_coverage"]["native"] == {"runs": 1, "reported_runs": 0,
                                                     "coverage": 0.0}
    assert report["unknown"]["native_usage_runs"] == ["scale20k-content-0001-0100-v1"]


def test_partial_matching_usage_identifies_the_incomplete_run(tmp_path):
    base, audit = fixture(tmp_path, native_usage={"input_tokens": 10})
    report = module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                               minimum_eligible_native=2, minimum_new_native=2)
    assert report["usage_coverage"]["native"]["reported_runs"] == 0
    assert report["usage"]["native"]["input_tokens"] is None
    assert report["unknown"]["native_usage_runs"] == ["scale20k-content-0001-0100-v1"]


def test_mismatch_and_duplicates_are_rejected(tmp_path):
    base, audit = fixture(tmp_path)
    summary_path = base / "run-summaries" / "scale20k-content-0001-0100-v1.json"
    value = json.loads(summary_path.read_text(encoding="utf-8"))
    value["native_usage"]["input_tokens"] = 11
    summary_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="native usage"):
        module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                           minimum_eligible_native=2, minimum_new_native=2)

    base, audit = fixture(tmp_path / "duplicate")
    duplicate = json.loads((base / "run-summaries" /
                            "scale20k-content-0001-0100-v1.json").read_text(encoding="utf-8"))
    save(base / "run-summaries" / "copy.json", duplicate)
    with pytest.raises(ValueError, match="Duplicate run"):
        module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                           minimum_eligible_native=2, minimum_new_native=2)


def test_runtime_count_mismatch_and_missing_new_summary_are_rejected(tmp_path):
    base, audit = fixture(tmp_path)
    summary_path = base / "run-summaries" / "scale20k-content-0001-0100-v1.json"
    value = json.loads(summary_path.read_text(encoding="utf-8"))
    value["native_completed"] = 1
    summary_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="counts differ"):
        module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                           minimum_eligible_native=2, minimum_new_native=2)


def test_partial_usage_does_not_hide_known_value_mismatch(tmp_path):
    base, audit = fixture(tmp_path)
    summary_path = base / "run-summaries" / "scale20k-content-0001-0100-v1.json"
    value = json.loads(summary_path.read_text(encoding="utf-8"))
    value["native_usage"].pop("output_tokens")
    value["native_usage"]["input_tokens"] = 11
    summary_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="native usage"):
        module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                           minimum_eligible_native=2, minimum_new_native=2)

    base, audit = fixture(tmp_path / "missing")
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    export = Path(audit_value["proofs"]["manifests"][0]["path"])
    extra = export.parent / "scale20k-content-0101-0200-v1" / "manifest.json"
    extra.parent.mkdir(parents=True)
    extra.write_bytes(export.read_bytes())
    audit_value["proofs"]["manifests"].append({"path": str(extra), "sha256": sha(extra)})
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly the new audit manifests"):
        module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                           minimum_eligible_native=2, minimum_new_native=2)


def test_old_audit_manifests_do_not_dilute_new_manifest_coverage(tmp_path):
    base, audit = fixture(tmp_path)
    audit_value = json.loads(audit.read_text(encoding="utf-8"))
    old = base / "old-baseline-manifest.json"
    save(old, {"legacy": True})
    audit_value["proofs"]["manifests"].append({"path": str(old), "sha256": sha(old)})
    audit.write_text(json.dumps(audit_value), encoding="utf-8")
    report = module().summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base,
                                minimum_eligible_native=2, minimum_new_native=2)
    assert report["audit_reconciliation"]["audit_manifest_count"] == 2
    assert report["audit_reconciliation"]["new_audit_manifest_count"] == 1
    assert report["audit_reconciliation"]["new_manifest_coverage"] == 1.0


def test_invalid_timing_is_not_hidden_by_missing_window_value():
    with pytest.raises(ValueError, match="Invalid timing"):
        module()._sum_number([None, -1])


def test_audit_sha_and_output_are_create_only(tmp_path):
    base, audit = fixture(tmp_path)
    m = module()
    with pytest.raises(ValueError, match="audit SHA"):
        m.summarize(audit_path=audit, audit_sha256="0" * 64, base_dir=base)
    output = tmp_path / "report.json"
    m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base, output=output,
                minimum_eligible_native=2, minimum_new_native=2)
    with pytest.raises(FileExistsError):
        m.summarize(audit_path=audit, audit_sha256=sha(audit), base_dir=base, output=output,
                    minimum_eligible_native=2, minimum_new_native=2)
