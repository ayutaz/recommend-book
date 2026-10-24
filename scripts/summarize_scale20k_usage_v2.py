"""Authenticated v20 usage summary with explicit failed-window quarantine.

Version 2 keeps the v1 summarizer immutable and delegates the adopted/native
denominator to it.  A failed window is accepted only when the final audit
explicitly pins its summary, control, export, run, quarantine, and quarantine
rows artifacts.  Failed-window usage is reported separately and never enters
the adopted totals.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "scripts" / "summarize_scale20k_usage_v1.py"
# Frozen at the v1 implementation used by this v2 namespace.
V1_SHA256 = "1c1a9f837ef8efa0ca8f3b658e88093a63a0291c52895f1dd62ed4e0f82131d5"
TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cached_input_tokens",
    "reasoning_output_tokens", "cache_write_input_tokens",
)
TIMING_FIELDS = (
    "native_terminal_pid_interval_seconds",
    "pid_inclusive_model_stage_seconds",
    "native_launch_through_public_import_wall_seconds",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(value: Any) -> Path:
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str) or not value:
        raise ValueError("Pinned path is missing")
    return Path(value).resolve(strict=True)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON artifact is not an object: " + str(path))
    return value


def _pin(value: Any, *, label: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(label + " pin is missing")
    path = _path(value.get("path"))
    expected = value.get("sha256")
    if not isinstance(expected, str) or sha256(path) != expected:
        raise ValueError(label + " SHA differs: " + str(path))
    return path, _json(path)


def _artifact_pin(artifacts: Any, name: str) -> dict[str, Any] | None:
    value = artifacts.get(name) if isinstance(artifacts, dict) else None
    return value if isinstance(value, dict) else None


def _load_v1():
    if sha256(V1_PATH) != V1_SHA256:
        raise ValueError("Frozen v1 summarizer SHA differs")
    spec = importlib.util.spec_from_file_location("summarize_scale20k_usage_v1_frozen", V1_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load frozen v1 summarizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V1 = _load_v1()


def _summary_bytes(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Run summary is not an object: " + str(path))
    return hashlib.sha256(raw).hexdigest(), value


def _usage_report(value: dict[str, int] | None) -> dict[str, int | None]:
    return {field: value.get(field) if value is not None else None for field in TOKEN_FIELDS}


def _validate_exclusions(audit: dict[str, Any], base: Path) -> tuple[set[str], list[dict[str, Any]]]:
    raw = audit.get("excluded_failed_windows", [])
    if not isinstance(raw, list):
        raise ValueError("excluded_failed_windows is not a list")
    summary_dir = (base / "run-summaries").resolve(strict=True)
    excluded_paths: set[str] = set()
    excluded: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("Failed-window exclusion is not an object")
        summary_path, summary = _pin(entry.get("summary"), label="excluded summary")
        if not summary_path.is_relative_to(summary_dir):
            raise ValueError("Excluded summary is outside run-summaries")
        summary_sha, summary_value = _summary_bytes(summary_path)
        if summary_sha != entry["summary"]["sha256"] or summary != summary_value:
            raise ValueError("Excluded summary changed")
        name = summary_value.get("run_name")
        if not isinstance(name, str) or name in names:
            raise ValueError("Duplicate excluded failed run")
        names.add(name)
        if summary_value.get("gate", {}).get("passed") is not False:
            raise ValueError("Excluded window does not have a failed gate")
        control_path, control = _pin(entry.get("control"), label="excluded control")
        export_path, export = _pin(entry.get("export"), label="excluded export")
        quarantine_path, quarantine_doc = _pin(entry.get("quarantine"), label="excluded quarantine")
        quarantine_rows_path, quarantine_rows_doc = _pin(entry.get("quarantine_rows"), label="excluded quarantine rows")
        if _path(summary_value.get("control_path")) != control_path \
                or summary_value.get("control_sha256") != entry["control"].get("sha256"):
            raise ValueError("Excluded summary/control binding differs")
        if _path(summary_value.get("public_manifest")) != export_path \
                or summary_value.get("public_manifest_sha256") != entry["export"].get("sha256"):
            raise ValueError("Excluded summary/export binding differs")
        artifacts = control.get("control_artifacts")
        export_pin = _artifact_pin(artifacts, "export")
        if not export_pin or _path(export_pin.get("path")) != export_path \
                or export_pin.get("sha256") != entry["export"].get("sha256"):
            raise ValueError("Excluded control/export binding differs")
        run_path = _path(export.get("run_manifest"))
        run_pin = _artifact_pin(artifacts, "run")
        if not run_pin:
            raise ValueError("Excluded control/run pin is missing")
        if _path(run_pin.get("path")) != run_path:
            raise ValueError("Excluded control/run binding differs")
        run_path, run = _pin(run_pin, label="excluded run")
        review = export.get("separate_review")
        review_pin = _artifact_pin(artifacts, "review")
        review_path = review_sha = None
        if isinstance(review, dict) and review.get("review_path"):
            if not review_pin:
                raise ValueError("Excluded review pin is missing")
            review_path, _ = _pin(review_pin, label="excluded review")
            if review_path != _path(review.get("review_path")):
                raise ValueError("Excluded control/review binding differs")
            review_sha = review_pin.get("sha256")
        gate = entry.get("failed_gate")
        if not isinstance(gate, dict) or gate.get("passed") is not False:
            raise ValueError("Excluded failed_gate is not an authenticated failure")
        if gate != summary_value.get("gate") or gate != control.get("gate"):
            raise ValueError("Excluded failed_gate does not match pinned summary/control")
        removed = entry.get("removed")
        remaining = entry.get("catalogue_rows_remaining")
        if (type(removed) is not int or removed <= 0
                or type(remaining) is not int or remaining != 0):
            raise ValueError("Excluded quarantine counts are invalid")
        if quarantine_doc.get("status") != "quarantined" \
                or type(quarantine_doc.get("removed")) is not int \
                or quarantine_doc.get("removed") != removed \
                or quarantine_doc.get("failed_gate") != gate:
            raise ValueError("Excluded quarantine receipt does not match failed window")
        if _path(quarantine_doc.get("rows_before_path")) != quarantine_rows_path \
                or quarantine_doc.get("rows_before_sha256") != entry["quarantine_rows"].get("sha256"):
            raise ValueError("Excluded quarantine rows binding differs")
        if quarantine_rows_doc.get("control_path") is not None \
                and _path(quarantine_rows_doc["control_path"]) != control_path:
            raise ValueError("Quarantine rows control binding differs")
        if quarantine_rows_doc.get("control_sha256") is not None \
                and quarantine_rows_doc["control_sha256"] != entry["control"].get("sha256"):
            raise ValueError("Quarantine rows control SHA differs")
        if quarantine_rows_doc.get("export_path") is not None \
                and _path(quarantine_rows_doc["export_path"]) != export_path:
            raise ValueError("Quarantine rows export binding differs")
        if quarantine_rows_doc.get("export_sha256") is not None \
                and quarantine_rows_doc["export_sha256"] != entry["export"].get("sha256"):
            raise ValueError("Quarantine rows export SHA differs")
        rows = quarantine_rows_doc.get("rows")
        export_rows = export.get("verified_projection", {}).get("rows")
        accepted = gate.get("accepted")
        row_qids: set[str] = set()
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("qid"), str) \
                        or not row["qid"]:
                    raise ValueError("Excluded quarantine row identity is invalid")
                row_qids.add(row["qid"])
        if (not isinstance(rows, list) or not isinstance(export_rows, list)
                or type(accepted) is not int or accepted < 0
                or len(rows) != removed or len(row_qids) != removed
                or len(export_rows) != removed or accepted != removed):
            raise ValueError("Excluded quarantine rows do not match failed gate/export")
        selected = run.get("selected")
        completed = run.get("completed")
        failed = run.get("failed")
        summary_selected = summary_value.get("native_selected")
        summary_completed = summary_value.get("native_completed")
        summary_failed = summary_value.get("native_failed")
        if (type(selected) is not int or selected < 0
                or type(completed) is not int or completed < 0
                or type(failed) is not int or failed < 0
                or selected < completed + failed
                or type(summary_selected) is not int or summary_selected < 0
                or type(summary_completed) is not int or summary_completed < 0
                or type(summary_failed) is not int or summary_failed < 0
                or summary_selected < summary_completed + summary_failed
                or selected != summary_selected
                or completed != summary_completed
                or failed != summary_failed
                or failed <= 0):
            raise ValueError("Excluded run counts differ from summary")
        if isinstance(control.get("gate"), dict) and control["gate"].get("passed") is not False:
            raise ValueError("Excluded control gate is not failed")
        for timing_field in TIMING_FIELDS:
            timing_value = summary_value.get(timing_field)
            if timing_value is not None:
                V1._sum_number([timing_value])
        native = V1._usage(summary_value.get("native_usage"))
        if not V1._usage_equal(summary_value.get("native_usage"), run.get("usage")):
            raise ValueError("Excluded native usage differs from run")
        review_usage = V1._usage(review.get("observed_usage")) if isinstance(review, dict) else None
        if isinstance(review, dict) and not V1._usage_equal(review.get("observed_usage"),
                                                             summary_value.get("separate_review", {}).get("observed_usage")):
            raise ValueError("Excluded review usage differs from summary")
        if isinstance(review, dict) and review.get("review_path") \
                != summary_value.get("separate_review", {}).get("review_path"):
            raise ValueError("Excluded review path differs from summary")
        excluded_paths.add(str(summary_path))
        excluded.append({
            "run_name": name,
            "counts": {"selected": selected, "completed": completed, "failed": failed,
                       "removed": removed, "catalogue_rows_remaining": remaining,
                       "adopted": None},
            "failed_gate": gate,
            "usage": {"native": _usage_report(native), "review": _usage_report(review_usage)},
            "bindings": {
                "summary": {"path": str(summary_path), "sha256": summary_sha},
                "control": entry["control"], "export": entry["export"],
                "run": {"path": str(run_path), "sha256": run_pin.get("sha256")},
                "review": ({"path": str(review_path), "sha256": review_sha}
                            if review_path else None),
                "quarantine": entry["quarantine"],
                "quarantine_rows": entry["quarantine_rows"],
            },
            "recorded_window_timings": {
                field: summary_value.get(field) for field in V1.TIMING_FIELDS
            },
        })
    return excluded_paths, excluded


def summarize(*, audit_path: Path, audit_sha256: str, base_dir: Path,
              output: Path | None = None, minimum_eligible_native: int = 20_000,
              minimum_new_native: int = 10_000) -> dict[str, Any]:
    audit_file = _path(audit_path)
    if sha256(audit_file) != audit_sha256:
        raise ValueError("audit SHA differs")
    audit = _json(audit_file)
    if audit.get("status") != "complete":
        raise ValueError("Final catalogue audit is not complete")
    base = Path(base_dir).resolve(strict=True)
    excluded_paths, excluded = _validate_exclusions(audit, base)

    # Any failed gate in the bounded summary directory must be named by the
    # final audit.  This prevents silently dropping a failed attempt.
    for summary_path in V1._summary_files(base):
        _, summary = _summary_bytes(summary_path)
        if summary.get("gate", {}).get("passed") is False and str(summary_path.resolve()) not in excluded_paths:
            raise ValueError("Failed summary is not explicitly excluded: " + summary.get("run_name", str(summary_path)))

    original_summary_files = V1._summary_files
    V1._summary_files = lambda _base: [
        path for path in original_summary_files(_base)
        if str(path.resolve()) not in excluded_paths
    ]
    try:
        healthy = V1.summarize(audit_path=audit_file, audit_sha256=audit_sha256,
                               base_dir=base, output=None,
                               minimum_eligible_native=minimum_eligible_native,
                               minimum_new_native=minimum_new_native)
    finally:
        V1._summary_files = original_summary_files

    native_values = [item["usage"]["native"] for item in excluded]
    review_values = [item["usage"]["review"] for item in excluded]
    native_raw = [V1._usage(value) for value in native_values]
    review_raw = [V1._usage(value) for value in review_values]
    native_total, native_reported = V1._sum_usage(native_raw)
    review_total, review_reported = V1._sum_usage(review_raw)
    excluded_selected = sum(item["counts"]["selected"] for item in excluded)
    excluded_completed = sum(item["counts"]["completed"] for item in excluded)
    excluded_failed = sum(item["counts"]["failed"] for item in excluded)
    excluded_removed = sum(item["counts"]["removed"] for item in excluded)
    result = dict(healthy)
    result.update({
        "version": "scale20k-usage-summary-v2",
        "excluded_failed_windows": excluded,
        "excluded_failed_totals": {
            "selected": excluded_selected, "completed": excluded_completed,
            "failed": excluded_failed, "removed": excluded_removed,
            "adopted": None,
            "usage": {"native": _usage_report(native_total), "review": _usage_report(review_total)},
            "usage_coverage": {
                "native": {"runs": len(native_raw), "reported_runs": native_reported,
                            "coverage": native_reported / len(native_raw) if native_raw else 1.0},
                "review": {"runs": len(review_raw), "reported_runs": review_reported,
                           "coverage": review_reported / len(review_raw) if review_raw else 1.0},
            },
            "note": "Excluded failed windows are reported separately and are never added to adopted or healthy totals.",
        },
        "v1_frozen_sha256": V1_SHA256,
    })
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--audit-sha256", required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(audit_path=args.audit, audit_sha256=args.audit_sha256,
                       base_dir=args.base_dir, output=args.output)
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "excluded_failed_windows": len(report["excluded_failed_windows"])},
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
