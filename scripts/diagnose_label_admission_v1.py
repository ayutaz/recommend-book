"""Diagnose a bounded label-level retrospective projection.

This reads the frozen phase13 native result and independent review only.  It
does not call a model, change a classification database, or publish a view.
The generated report contains label IDs and hashes, never source quotations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_bytes())


def write_new(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def diagnose(args) -> dict:
    from recommend_book.label_admission_v1 import admit_label_level, validate_request

    results_path = args.results.resolve(strict=True)
    quality_path = args.quality.resolve(strict=True)
    review_final_path = args.review.resolve(strict=True)
    source_audit_path = review_final_path.with_name("source-audit.json").resolve(strict=True)
    review_path = source_audit_path
    review_admission_path = review_path.with_name("review-admission.json").resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("diagnostic output must be a fresh namespace")
    quality = read_json(quality_path)
    review_document = read_json(review_path)
    review_final = read_json(review_final_path)
    results = [json.loads(line) for line in results_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    results_by_work = {result["work_id"]: result for result in results}
    reviews_by_work = {entry["work_id"]: entry for entry in review_final["reviews"]}

    review_sha = sha(review_path)
    request = {
        "schema_version": "label-admission-request-v1",
        "review_path": str(review_path),
        "review_sha256": review_sha,
        "review_admission_path": str(review_admission_path),
        "review_admission_sha256": sha(review_admission_path),
        "quality_review_path": str(quality_path),
        "quality_review_sha256": sha(quality_path),
        "source_audit_path": str(source_audit_path),
        "source_audit_sha256": sha(source_audit_path),
        "review_final_path": str(review_final_path),
        "review_final_sha256": sha(review_final_path),
        "cases": [
            {"work_id": "work_5a0dc650fcfc19393f495dad", "admit_label_ids": ["structure.journey"]},
            {"work_id": "work_074e3d43aab40277563b5cc1", "admit_label_ids": ["genre.fantasy"]},
        ],
    }
    normalized_request = validate_request(request)
    quality_cases = {case["work_id"]: case for case in quality["cases"]}
    projections = []
    for requested in normalized_request["cases"]:
        work_id = requested["work_id"]
        quality_case = quality_cases.get(work_id)
        if quality_case is None:
            raise ValueError("requested case is absent from the immutable quality review")
        decision = admit_label_level(
            results_by_work[work_id], quality_case, reviews_by_work[work_id],
            admit_label_ids=requested["admit_label_ids"])
        projections.append({
            "work_id": work_id,
            "native_label_ids": decision["native_label_ids"],
            "affected_label_ids": decision["affected_label_ids"],
            "admitted_label_ids": decision["admitted_label_ids"],
            "withheld_label_ids": decision["withheld_label_ids"],
            "source_evidence_ids": sorted({
                evidence["evidence_id"]
                for label in decision["labels"]
                for evidence in label["evidence"]
            }),
        })

    whole_work_admitted = len(results) - len(quality_cases)
    report = {
        "schema_version": "phase14-label-policy-diagnostic-v1",
        "policy_version": "label-level-admission-v1",
        "model_calls": 0,
        "source_bindings": {
            "results": {"path": str(results_path), "sha256": sha(results_path)},
            "quality_review": {"path": str(quality_path), "sha256": sha(quality_path)},
            "review_final": {"path": str(review_final_path), "sha256": sha(review_final_path)},
            "source_audit": {"path": str(source_audit_path), "sha256": sha(source_audit_path)},
            "review_admission": {"path": str(review_admission_path), "sha256": sha(review_admission_path)},
        },
        "baseline_whole_work_policy": {
            "classified_work_n": len(results),
            "admitted_work_n": whole_work_admitted,
            "withheld_work_n": len(quality_cases),
            "admitted_feature_work_n": whole_work_admitted,
        },
        "retrospective_label_projection": {
            "requested_case_n": len(projections),
            "projected_work_n": len(projections),
            "projected_label_n": sum(len(item["admitted_label_ids"]) for item in projections),
            "projected_feature_work_n": whole_work_admitted + len(projections),
            "whole_work_adoption_rate_unchanged": f"{whole_work_admitted}/{len(results)}",
            "projected_feature_work_rate": f"{whole_work_admitted + len(projections)}/{len(results)}",
        },
        "cases": projections,
        "guardrails": {
            "native_results_unchanged": True,
            "quality_review_unchanged": True,
            "old_database_written": False,
            "old_usage_ledger_written": False,
            "source_quotes_exported": False,
            "human_gold": False,
            "confidence_calibrated": False,
            "authority_merge_approved": False,
        },
    }
    write_new(output / "label-admission-request.json", request)
    write_new(output / "report.json", report)
    write_new(output / "report.md", render_markdown(report))
    return report


def render_markdown(report: dict) -> str:
    projection = report["retrospective_label_projection"]
    lines = [
        "# Phase14 label-level admission diagnostic",
        "",
        "This is a retrospective projection from frozen phase13 artifacts; it does not rewrite the native result, quality review, database, or usage ledger.",
        "",
        f"- Whole-work baseline: {report['baseline_whole_work_policy']['admitted_work_n']}/{report['baseline_whole_work_policy']['classified_work_n']} admitted.",
        f"- Explicit label-level projection: {projection['projected_label_n']} labels across {projection['projected_work_n']} works.",
        f"- Retrospective feature-bearing projection: {projection['projected_feature_work_n']}/{report['baseline_whole_work_policy']['classified_work_n']} works.",
        f"- Model calls: {report['model_calls']}.",
        "",
        "| work_id | affected labels | admitted labels | withheld labels |",
        "|---|---|---|---|",
    ]
    for case in report["cases"]:
        lines.append("| {work_id} | {affected_label_ids} | {admitted_label_ids} | {withheld_label_ids} |".format(**case))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--quality", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(diagnose(parser.parse_args()), ensure_ascii=False))
