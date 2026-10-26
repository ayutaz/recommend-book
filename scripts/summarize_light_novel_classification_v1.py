"""Read-only finite audit of series CLI histories; never execute a model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from recommend_book.classification.runner import bind_final_message, parse_events
from recommend_book.light_novel_classification_v1 import (
    AXES, canonical_cli_path, digest, save, sha, verify_feature_export,
)


def summarize(index_path, output):
    index_path = Path(index_path).resolve(strict=True)
    index = json.loads(index_path.read_bytes())
    if index.get("version") != "light-novel-series-history-index-v1":
        raise ValueError("An explicit pinned history index is required")
    attempts, manifest_calls, histories, valid_native = {}, {}, [], set()
    input_series, approved_rows, review_values, valid_reviews = {}, {}, {}, set()
    failures, held_labels = [], Counter()
    approved_hold_series = defaultdict(set)
    for item in index["histories"]:
        path = Path(item["manifest_path"]).resolve(strict=True)
        expected = item["manifest_sha256"]
        export = verify_feature_export(path, expected)
        call_paths = set()
        for parent_field in ("parent_manifest", "continuation_manifest"):
            if export.get(parent_field):
                key = canonical_cli_path(export[parent_field])
                if key not in manifest_calls:
                    raise ValueError("History index must include immutable parents before descendants")
                call_paths.update(manifest_calls[key])
        inputs_path = path.parent / "inputs.json"
        if inputs_path.is_file():
            for row in json.loads(inputs_path.read_bytes()):
                key = digest(row)
                if key in input_series and input_series[key] != row["series_id"]:
                    raise ValueError("History input hash has inconsistent series identity")
                input_series[key] = row["series_id"]
        for raw_path in export["artifact_sha256"]:
            process_start = Path(raw_path)
            if process_start.name != "process-start.json" or process_start.parent.parent.name not in ("classification", "review"):
                continue
            directory = process_start.parent
            key = canonical_cli_path(directory)
            call_paths.add(key)
            if key in attempts:
                continue
            request_path = directory / "request.json"
            request = json.loads(request_path.read_bytes())
            exit_doc = json.loads((directory / "process-exit.json").read_bytes())
            start_doc = json.loads(process_start.read_bytes())
            if exit_doc.get("process_exit_observed") is not True or exit_doc.get("pid") != start_doc.get("pid"):
                raise ValueError("Every audited CLI call needs an actual matching terminal witness")
            hashes = request["input_hashes"]
            kind = request["kind"]
            if kind not in ("classification", "separate_review") or not 1 <= len(hashes) <= (1 if kind == "classification" else 5):
                raise ValueError("Classification/review target bounds differ")
            if not set(hashes) <= input_series.keys():
                raise ValueError("Actual CLI inputs are not present in pinned complete history")
            stdout = (directory / "stdout.jsonl").read_bytes()
            events = parse_events(stdout.decode("utf-8"))
            binding, binding_error = None, None
            try:
                binding = bind_final_message(stdout, (directory / "final.json").read_bytes())
            except (ValueError, OSError) as exc:
                binding_error = str(exc)
            if kind == "separate_review" and binding:
                review_values[key] = binding["value"]
            attempts[key] = {"directory": str(directory), "kind": kind, "input_hashes": hashes,
                "series_ids": [input_series[h] for h in hashes], "model": request["model"], "cli_version": request["cli_version"],
                "prompt_sha256": request["stdin_sha256"], "schema_sha256": request["schema_sha256"],
                "actual_exit_code": exit_doc["actual_exit_code"], "elapsed_seconds": exit_doc["elapsed_seconds"],
                "timed_out": exit_doc.get("timed_out", False), "started_at": start_doc["started_at"],
                "usage": events["usage"], "tool_events": events["tool_calls"], "error_events": events["event_errors"],
                "final_value_sha256": binding["final_value_sha256"] if binding else None,
                "binding_error": binding_error, "first_history": str(path)}
        for attempt in export.get("classification_attempts", []):
            valid_native.add(canonical_cli_path(attempt["path"]))
        for attempt in export.get("review_attempts", []):
            valid_reviews.add(canonical_cli_path(attempt["path"]))
        prior_calls = set().union(*manifest_calls.values()) if manifest_calls else set()
        new = call_paths - prior_calls
        histories.append({"manifest_path": str(path), "manifest_sha256": expected, "role": item["role"],
            "adopted_as_current": item["adopted_as_current"], "gate": export["gate"],
            "new_classification_calls": sum(attempts[key]["kind"] == "classification" for key in new),
            "new_review_calls": sum(attempts[key]["kind"] == "separate_review" for key in new),
            "inherited_classification_calls": sum(attempts[key]["kind"] == "classification" for key in call_paths - new),
            "inherited_review_calls": sum(attempts[key]["kind"] == "separate_review" for key in call_paths - new),
            "observed_total_classification_calls": sum(attempts[key]["kind"] == "classification" for key in call_paths),
            "observed_native_validation_failures": sum(attempts[key]["kind"] == "classification" and key not in valid_native for key in call_paths),
            "observed_unstarted_series": export["gate"]["selected"] - sum(attempts[key]["kind"] == "classification" for key in call_paths)})
        manifest_calls[canonical_cli_path(path)] = call_paths
        failures.extend({"manifest_path": str(path), **row} for row in export.get("failures", []))
        if item["adopted_as_current"]:
            if not export["gate"]["passed"]:
                raise ValueError("A current adopted export must pass its actual verified gate")
            for row in export["rows"]:
                if row["series_id"] in approved_rows:
                    raise ValueError("Current adopted manifests must not repeat a series")
                approved_rows[row["series_id"]] = row
                for held in row["held_labels"]:
                    held_labels[held["reason"]] += 1
                    approved_hold_series[held["reason"]].add(row["series_id"])
    usage, axis_raw = Counter(), {axis: Counter({"known_series": 0, "unknown_series": 0,
        "raw_present_labels": 0, "raw_absent_labels": 0, "raw_unknown_labels": 0}) for axis in AXES}
    native_counts = Counter()
    all_starts, all_ends = [], []
    for key, attempt in attempts.items():
        usage.update(attempt["usage"])
        start = datetime.fromisoformat(attempt["started_at"])
        all_starts.append(start)
        all_ends.append(start + timedelta(seconds=attempt["elapsed_seconds"]))
        if attempt["kind"] == "classification":
            native_counts.update(attempt["series_ids"])
        if key not in valid_native:
            continue
        result = json.loads((Path(attempt["directory"]) / "final.json").read_bytes())
        for axis in AXES:
            labels = [label for label in result["labels"] if label["axis"] == axis]
            known = any(label["decision"] in ("present", "absent") for label in labels)
            axis_raw[axis]["known_series" if known else "unknown_series"] += 1
            for label in labels:
                axis_raw[axis]["raw_" + label["decision"] + "_labels"] += 1
    adopted_axes = {axis: Counter({"adopted_labels": 0, "series_with_adopted_labels": 0, "unknown_series": 0}) for axis in AXES}
    content, non_genre, display = set(), set(), set()
    for series_id, row in approved_rows.items():
        labels = row["admitted_labels"]
        content_ids = {item["evidence_id"] for item in row["source_attributions"] if item["kind"] in ("synopsis", "note", "excerpt")}
        has_content = any(citation["evidence_id"] in content_ids for label in labels for citation in label["evidence"])
        has_non_genre = any(label["axis"] != "genre" and any(c["evidence_id"] in content_ids for c in label["evidence"]) for label in labels)
        if has_content != row["content_features_adopted"]:
            raise ValueError("Published content adoption differs from actual cited evidence scope")
        if "non_genre_content_features_adopted" in row and has_non_genre != row["non_genre_content_features_adopted"]:
            raise ValueError("Published non-genre content adoption differs")
        if labels:
            display.add(series_id)
        if has_content:
            content.add(series_id)
        if has_non_genre:
            non_genre.add(series_id)
        for axis in AXES:
            selected = [label for label in labels if label["axis"] == axis]
            adopted_axes[axis]["adopted_labels"] += len(selected)
            adopted_axes[axis]["series_with_adopted_labels"] += bool(selected)
            adopted_axes[axis]["unknown_series"] += any(m["axis"] == axis for m in row["unknown_axes"])
    review_findings, review_finding_series = Counter(), defaultdict(set)
    for review in (review for key in valid_reviews for review in review_values[key]["reviews"]):
        for finding in review["findings"]:
            review_findings[finding["reason_code"]] += len(finding["label_ids"])
            review_finding_series[finding["reason_code"]].add(review["series_id"])
    duplicates = {series_id: count for series_id, count in native_counts.items() if count > 1}
    actual_native = sum(a["kind"] == "classification" for a in attempts.values())
    report = {"version": "light-novel-series-classification-audit-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage_status": index.get("coverage_status", "complete_for_explicit_history_index"),
        "history_index": str(index_path), "history_index_sha256": sha(index_path), "histories": histories,
        "actual_unique_classification_calls": actual_native,
        "actual_unique_review_calls": sum(a["kind"] == "separate_review" for a in attempts.values()),
        "actual_reviewed_target_occurrences": sum(len(a["input_hashes"]) for a in attempts.values() if a["kind"] == "separate_review"),
        "valid_native_series": len(valid_native), "actual_duplicate_classification_series": duplicates,
        "actual_native_validation_failures": actual_native - len(valid_native),
        "actual_native_validation_failure_rate": (actual_native - len(valid_native)) / actual_native if actual_native else None,
        "valid_review_calls": len(valid_reviews),
        "observed_usage": dict(usage), "observed_input_plus_output_tokens": usage["input_tokens"] + usage["output_tokens"],
        "wall_span_seconds": (max(all_ends) - min(all_starts)).total_seconds() if all_starts else 0,
        "cumulative_cli_process_seconds": sum(a["elapsed_seconds"] for a in attempts.values()),
        "model_versions": sorted({a["model"] for a in attempts.values()}), "cli_versions": sorted({a["cli_version"] for a in attempts.values()}),
        "raw_axes_valid_native_denominator": len(valid_native), "raw_axes": axis_raw,
        "current_exported_series": len(approved_rows), "display_feature_series": len(display), "content_feature_series": len(content),
        "non_genre_content_feature_series": len(non_genre), "metadata_only_feature_series": len(display - content),
        "metadata_genre_only_feature_series": sum(series_id not in content and bool(row["admitted_labels"])
            and all(label["axis"] == "genre" for label in row["admitted_labels"]) for series_id, row in approved_rows.items()),
        "adopted_axes": adopted_axes, "held_label_reason_counts": dict(held_labels),
        "held_reason_series_counts": {key: len(ids) for key, ids in approved_hold_series.items()},
        "raw_review_finding_label_counts": dict(review_findings),
        "raw_review_finding_series_counts": {key: len(ids) for key, ids in review_finding_series.items()},
        "failed_history_cases": failures, "attempts": [{**attempt, "validated_native": key in valid_native}
            for key, attempt in attempts.items()], "model_calls_during_audit": 0,
        "independent_accuracy_evaluated": False, "confidence_calibrated": False, "user_satisfaction_evaluated": False}
    if duplicates:
        raise ValueError("Audit found repeated native calls for a series")
    save(output, report)
    return {key: report[key] for key in ("version", "actual_unique_classification_calls", "actual_unique_review_calls",
        "valid_native_series", "display_feature_series", "content_feature_series", "non_genre_content_feature_series",
        "metadata_only_feature_series", "observed_input_plus_output_tokens", "model_calls_during_audit")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.history_index, args.output), ensure_ascii=True))


if __name__ == "__main__":
    main()
