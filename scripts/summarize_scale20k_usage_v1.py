"""Summarize authenticated v20 native/review runtime usage.

This is a bounded, read-only companion to the final catalogue audit.  It reads
the audit, its pinned export/control/run metadata, and the small
``run-summaries`` directory.  It does not replay native proofs, inspect the
catalogue, invoke a model, or contact a network service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "reasoning_output_tokens",
    "cache_write_input_tokens",
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


def _norm(path: Any) -> str:
    return str(_path(path))


def _pin_entries(value: Any) -> dict[str, str]:
    """Normalize audit manifest/control pins without interpreting raw data."""
    if not isinstance(value, list):
        raise ValueError("Audit proof pins are not a list")
    result: dict[str, str] = {}
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("Audit proof pin is not an object")
        nested = entry.get("audit_control_pin")
        if isinstance(nested, dict):
            entry = nested
        path = entry.get("path")
        expected = entry.get("sha256")
        if path is None:
            path = entry.get("manifest_path") or entry.get("control_path")
        if expected is None:
            expected = entry.get("manifest_sha256") or entry.get("control_sha256")
        key = _norm(path)
        if not isinstance(expected, str) or not expected:
            raise ValueError("Audit proof pin has no SHA: " + key)
        if key in result:
            raise ValueError("Duplicate audit proof pin: " + key)
        result[key] = expected
    return result


def _usage(value: Any) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Usage is not an object")
    result: dict[str, int] = {}
    for field in TOKEN_FIELDS:
        number = value.get(field)
        if number is None:
            continue
        if type(number) is not int or number < 0:
            raise ValueError("Invalid usage field: " + field)
        result[field] = number
    if "cached_input_tokens" in result and "input_tokens" in result \
            and result["cached_input_tokens"] > result["input_tokens"]:
        raise ValueError("cached input usage exceeds input usage")
    if "reasoning_output_tokens" in result and "output_tokens" in result \
            and result["reasoning_output_tokens"] > result["output_tokens"]:
        raise ValueError("reasoning usage exceeds output usage")
    return result


def _usage_equal(left: Any, right: Any) -> bool:
    return _usage(left) == _usage(right)


def _usage_complete(value: dict[str, int] | None) -> bool:
    return value is not None and set(value) == set(TOKEN_FIELDS)


def _sum_usage(values: list[dict[str, int] | None]) -> tuple[dict[str, int] | None, int]:
    present = [value for value in values if _usage_complete(value)]
    if not present:
        return None, 0
    if len(present) != len(values):
        return None, len(present)
    return {field: sum(value[field] for value in present) for field in TOKEN_FIELDS}, len(present)


def _sum_number(values: list[Any]) -> float | None:
    if not values:
        return None
    missing = False
    for value in values:
        if value is None:
            missing = True
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid timing value")
    if missing:
        return None
    return sum(values)


def _report_usage(value: dict[str, int] | None) -> dict[str, int | None]:
    return {field: value.get(field) if value is not None else None for field in TOKEN_FIELDS}


def _artifact_pin(artifacts: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(artifacts, dict):
        return None
    value = artifacts.get(name)
    return value if isinstance(value, dict) else None


def _audit_count(counts: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in counts:
            return counts[name]
    return None


def _summary_files(base_dir: Path) -> list[Path]:
    directory = base_dir / "run-summaries"
    if not directory.is_dir():
        raise ValueError("run-summaries directory is missing: " + str(directory))
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError("No run summaries found: " + str(directory))
    return files


def summarize(*, audit_path: Path, audit_sha256: str, base_dir: Path,
              output: Path | None = None, minimum_eligible_native: int = 20_000,
              minimum_new_native: int = 10_000) -> dict[str, Any]:
    """Build one authenticated report; ``output`` is create-only when given."""
    audit_file = _path(audit_path)
    if sha256(audit_file) != audit_sha256:
        raise ValueError("audit SHA differs")
    audit = _json(audit_file)
    if audit.get("status") != "complete":
        raise ValueError("Final catalogue audit is not complete")
    proofs = audit.get("proofs")
    if not isinstance(proofs, dict):
        raise ValueError("Final audit proofs are missing")
    manifests = _pin_entries(proofs.get("manifests"))
    controls = _pin_entries(proofs.get("controls"))
    base = Path(base_dir).resolve(strict=True)
    new_export_root = (base / "classification-exports-v3").resolve(strict=True)
    counts = audit.get("counts") if isinstance(audit.get("counts"), dict) else {}
    eligible_native = _audit_count(counts, "eligible_native")
    new_native = _audit_count(counts, "new_eligible_native", "new_accepted_works")
    if type(eligible_native) is not int or eligible_native < minimum_eligible_native:
        raise ValueError("Final audit eligible_native target is not met")
    if type(new_native) is not int or new_native < minimum_new_native:
        raise ValueError("Final audit new native target is not met")
    new_manifest_pins = {
        path: expected for path, expected in manifests.items()
        if Path(path).is_relative_to(new_export_root)
    }
    if not new_manifest_pins:
        raise ValueError("Final audit has no new export manifests")

    summaries: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_manifests: set[str] = set()
    native_usages: list[dict[str, int] | None] = []
    review_usages: list[dict[str, int] | None] = []
    selected = completed = failed = 0
    native_wall: dict[str, list[Any]] = {field: [] for field in TIMING_FIELDS}
    native_elapsed: list[Any] = []
    review_elapsed: list[Any] = []
    model_names: set[str] = set()
    witness: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    new_qids: set[str] = set()
    new_authenticated_rows = 0
    new_gate_accepted = 0

    for summary_path in _summary_files(base):
        summary_bytes = summary_path.read_bytes()
        summary_sha = hashlib.sha256(summary_bytes).hexdigest()
        summary = json.loads(summary_bytes.decode("utf-8"))
        if not isinstance(summary, dict):
            raise ValueError("Run summary is not an object: " + str(summary_path))
        name = summary.get("run_name")
        if not isinstance(name, str) or not name:
            raise ValueError("Run summary has no run_name: " + str(summary_path))
        if name in seen_names:
            raise ValueError("Duplicate run summary: " + name)
        seen_names.add(name)
        export_path = _path(summary.get("public_manifest"))
        export_sha = summary.get("public_manifest_sha256")
        if not export_path.is_relative_to(new_export_root):
            raise ValueError("Run summary export is outside new v20 export namespace: " + name)
        if not isinstance(export_sha, str) or new_manifest_pins.get(str(export_path)) != export_sha:
            raise ValueError("Run summary export is not in final audit proofs: " + name)
        if str(export_path) in seen_manifests:
            raise ValueError("Duplicate export manifest: " + str(export_path))
        seen_manifests.add(str(export_path))
        if sha256(export_path) != export_sha:
            raise ValueError("Run summary export SHA differs: " + name)
        export = _json(export_path)
        projection = export.get("verified_projection")
        rows = projection.get("rows") if isinstance(projection, dict) else None
        if not isinstance(rows, list):
            raise ValueError("New export authenticated rows are missing: " + name)
        row_qids = {row.get("qid") for row in rows if isinstance(row, dict)}
        if len(row_qids) != len(rows) or any(not isinstance(qid, str) or not qid for qid in row_qids):
            raise ValueError("New export row identities are invalid: " + name)
        new_qids.update(row_qids)
        new_authenticated_rows += len(rows)
        control_path = _path(summary.get("control_path"))
        control_sha = summary.get("control_sha256")
        if not isinstance(control_sha, str) or controls.get(str(control_path)) != control_sha:
            raise ValueError("Run summary control is not in final audit proofs: " + name)
        if sha256(control_path) != control_sha:
            raise ValueError("Run summary control SHA differs: " + name)
        control = _json(control_path)
        artifacts = control.get("control_artifacts")
        export_pin = _artifact_pin(artifacts, "export")
        if not export_pin or _norm(export_pin.get("path")) != str(export_path) \
                or export_pin.get("sha256") != export_sha:
            raise ValueError("Control/export binding differs: " + name)
        control_gate = control.get("gate")
        accepted = control_gate.get("accepted") if isinstance(control_gate, dict) else None
        if type(accepted) is not int or accepted < 0 or accepted != len(rows):
            raise ValueError("Control gate accepted count differs from authenticated rows: " + name)
        new_gate_accepted += accepted

        run_path = _path(export.get("run_manifest"))
        run_sha = None
        run_pin = _artifact_pin(artifacts, "run")
        if run_pin:
            if _norm(run_pin.get("path")) != str(run_path):
                raise ValueError("Control/run binding differs: " + name)
            run_sha = run_pin.get("sha256")
        artifact_hashes = export.get("artifact_sha256")
        if not isinstance(artifact_hashes, dict):
            raise ValueError("Export artifact SHA map is missing: " + name)
        run_sha = run_sha or artifact_hashes.get(str(run_path))
        if not isinstance(run_sha, str) or sha256(run_path) != run_sha:
            raise ValueError("Run manifest SHA differs: " + name)
        run = _json(run_path)
        if run.get("selected") != summary.get("native_selected") \
                or run.get("completed") != summary.get("native_completed") \
                or run.get("failed") != summary.get("native_failed"):
            raise ValueError("Run summary counts differ from run manifest: " + name)
        if not _usage_equal(summary.get("native_usage"), run.get("usage")):
            raise ValueError("native usage differs from run manifest: " + name)
        native_usage = _usage(summary.get("native_usage"))

        review = export.get("separate_review")
        review_summary = summary.get("separate_review")
        if not isinstance(review, dict) or not isinstance(review_summary, dict):
            review_usage = None
        else:
            if review.get("review_path") != review_summary.get("review_path"):
                raise ValueError("Review path differs from export: " + name)
            if not _usage_equal(review.get("observed_usage"), review_summary.get("observed_usage")):
                raise ValueError("review usage differs from export: " + name)
            review_usage = _usage(review_summary.get("observed_usage"))
            review_pin = _artifact_pin(artifacts, "review")
            if review_pin and review.get("review_path"):
                if _norm(review_pin.get("path")) != _norm(review.get("review_path")) \
                        or sha256(_path(review.get("review_path"))) != review_pin.get("sha256"):
                    raise ValueError("Control/review binding differs: " + name)
            elif review.get("review_path"):
                review_path = _path(review.get("review_path"))
                review_sha = artifact_hashes.get(str(review_path))
                if not isinstance(review_sha, str) or sha256(review_path) != review_sha:
                    raise ValueError("Review path has no authenticated SHA: " + name)

        native_usages.append(native_usage)
        review_usages.append(review_usage)
        selected += int(run.get("selected", 0))
        completed += int(run.get("completed", 0))
        failed += int(run.get("failed", 0))
        native_elapsed.append(run.get("elapsed_seconds"))
        review_elapsed.append(review.get("elapsed_seconds") if isinstance(review, dict) else None)
        model = run.get("config", {}).get("model") if isinstance(run.get("config"), dict) else None
        if isinstance(model, str):
            model_names.add(model)
        for field in TIMING_FIELDS:
            value = summary.get(field)
            if value is not None and (type(value) not in (int, float)
                                      or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid timing value: " + field)
            native_wall[field].append(summary.get(field))
        witness.append({"run_name": name, "model": model,
                        **{field: summary.get(field) for field in TIMING_FIELDS}})
        bindings.append({"run_name": name,
                         "summary": {"path": str(summary_path.resolve()), "sha256": summary_sha},
                         "export": {"path": str(export_path), "sha256": export_sha},
                         "control": {"path": str(control_path), "sha256": control_sha},
                         "run": {"path": str(run_path), "sha256": run_sha},
                         "review": ({"path": str(_path(review.get("review_path"))),
                                     "sha256": _artifact_pin(artifacts, "review").get("sha256")}
                                    if isinstance(review, dict) and review.get("review_path")
                                    and _artifact_pin(artifacts, "review") else None)})
        summaries.append(summary)

    if set(seen_manifests) != set(new_manifest_pins):
        raise ValueError("Run summary export set does not cover exactly the new audit manifests")
    if len(new_qids) != new_native or new_authenticated_rows != new_native \
            or new_gate_accepted != new_native:
        raise ValueError("New export rows/gates do not match final audit new native count")
    native_total, native_reported = _sum_usage(native_usages)
    review_total, review_reported = _sum_usage(review_usages)
    run_names = {summary["run_name"] for summary in summaries}
    reservations = base / "attempt-reservations"
    reserved_names: set[str] = set()
    if reservations.is_dir():
        for path in sorted(reservations.glob("*.json")):
            value = _json(path)
            if isinstance(value.get("run_name"), str):
                reserved_names.add(value["run_name"])
    unaggregated = sorted(reserved_names - run_names)
    report = {
        "version": "scale20k-usage-summary-v1",
        "status": "complete",
        "audit": {"path": str(audit_file), "sha256": audit_sha256,
                  "status": audit.get("status")},
        "base_dir": str(base),
        "run_names": sorted(run_names),
        "bindings": bindings,
        "totals": {"selected": selected, "completed": completed, "failed": failed,
                   "adopted": _audit_count(counts, "new_accepted_works", "new_eligible_native"),
                   "display": _audit_count(counts, "new_display_feature_works"),
                   "non_genre": _audit_count(counts, "new_display_content_non_genre_feature_works")},
        "audit_reconciliation": {
            "new_native_accepted": _audit_count(counts, "new_accepted_works", "new_eligible_native"),
            "audit_manifest_count": len(manifests),
            "new_audit_manifest_count": len(new_manifest_pins),
            "summary_manifest_count": len(seen_manifests),
            "bound_manifest_count": len(seen_manifests),
            "new_manifest_coverage": len(seen_manifests) / len(new_manifest_pins),
            "new_authenticated_rows": new_authenticated_rows,
            "new_unique_qids": len(new_qids),
            "new_gate_accepted": new_gate_accepted,
            "unaggregated_attempts": unaggregated,
        },
        "usage": {"native": _report_usage(native_total), "review": _report_usage(review_total)},
        "usage_coverage": {
            "native": {"runs": len(native_usages), "reported_runs": native_reported,
                        "coverage": native_reported / len(native_usages)},
            "review": {"runs": len(review_usages), "reported_runs": review_reported,
                       "coverage": review_reported / len(review_usages)},
        },
        "timing": {
            "wall_time_seconds": {
                "native_terminal_pid_interval_sum": _sum_number(native_wall[TIMING_FIELDS[0]]),
                "pid_inclusive_model_stage_sum": _sum_number(native_wall[TIMING_FIELDS[1]]),
                "native_launch_through_public_import_sum": _sum_number(native_wall[TIMING_FIELDS[2]]),
            },
            "elapsed_seconds": {
                "native_run_manifest_sum": _sum_number(native_elapsed),
                "separate_review_sum": _sum_number(review_elapsed),
            },
            "parallelism_note": "Sums are reported separately; parallel or subset intervals are not added into an overall elapsed claim.",
        },
        "recorded_window_timings": {
            "models": sorted(model_names), "runs": witness,
            "provenance_note": "Timing fields are copied from the pinned run-summary bytes. The final catalogue audit verifies its terminal chain; this companion does not independently reverify timestamps.",
        },
        "unknown": {
            "native_usage_runs": sorted(summary["run_name"] for summary, usage in zip(summaries, native_usages) if not _usage_complete(usage)),
            "review_usage_runs": sorted(summary["run_name"] for summary, usage in zip(summaries, review_usages) if not _usage_complete(usage)),
            "unaggregated_attempts": unaggregated,
        },
        "usage_source": "Codex CLI turn.completed events; not API billing",
        "subset_notes": [
            "cached_input_tokens is a subset of input_tokens and reasoning_output_tokens is a subset of output_tokens; neither is added twice.",
            "separate_review.observed_usage is the aggregate; separate_review.groups is not summed.",
            "Missing usage is unknown (null) and represented by coverage, never synthetic zero.",
        ],
    }
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
    return report


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
                      "runs": len(report["run_names"])}, ensure_ascii=True))


if __name__ == "__main__":
    main()
