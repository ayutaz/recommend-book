"""Contract tests for the read-only partial recovery projection."""

import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.world_novel_partial_recovery_v1 import (
    EXPORT_VERSION,
    PLAN_VERSION,
    PartialRecoveryError,
    build_partial_export,
    canonical_json,
    resolve_admitted_labels,
    sha256,
    validate_partition,
)


def _plan(tmp_path, *, completed=2, failed=1, pending=1):
    rows = {}
    for group, count, status in (("completed", completed, "completed"), ("failed", failed, "held"), ("unstarted", pending, "not_submitted")):
        rows[group] = [{"job_key": f"job-{group}-{i}", "work_id": f"work-{group}-{i}",
                        "input_sha256": hashlib.sha256(f"input-{group}-{i}".encode()).hexdigest(),
                        "qid": f"Q{1000 + i}", "original_status": status}
                       for i in range(count)]
    marker = tmp_path / "immutable-marker.json"
    marker.write_text("{\"frozen\":true}\n", encoding="utf-8")
    value = {"version": PLAN_VERSION, "original_gate_failed": True,
             "never_retry_failed": True, "never_reclassify_completed": True,
             "original_run_id": "run-original", "source": str(tmp_path / "source"),
             "artifact_sha256": {str(marker): sha256(marker)},
             "original_counts": {"selected": completed + failed + pending, "completed": completed,
                                 "failed": failed, "pending": pending,
                                 "cli_invocations": completed + failed}, **rows}
    return value


def _write_plan(tmp_path, value):
    path = tmp_path / "plan.json"
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    return path


def test_validate_partition_requires_every_original_identity(tmp_path):
    plan = _plan(tmp_path)
    result = validate_partition(plan)
    assert result["counts"] == {"selected": 4, "completed": 2, "failed": 1,
                                 "pending": 1, "cli_invocations": 3}
    assert len(result["by_work"]) == 4

    plan["unstarted"][0]["work_id"] = plan["completed"][0]["work_id"]
    with pytest.raises(PartialRecoveryError, match="Duplicate original partition identity"):
        validate_partition(plan)


def test_partial_export_keeps_source_gate_and_full_partition_counts(tmp_path):
    plan = _plan(tmp_path)
    plan_path = _write_plan(tmp_path, plan)
    review = {"review_result_sha256": "a" * 64, "selection_sha256": "b" * 64,
              "quality_sha256": "c" * 64}
    rows = [{"work_id": row["work_id"], "input_hash": row["input_sha256"],
             "job_key": row["job_key"], "qid": row["qid"], "source_record_ids": ["src-1"],
             "source_evidence": [{"evidence_id": "ev-1"}],
             "admitted_labels": [{"label_id": "genre.test"}],
             "native_result_value_sha256": "d" * 64}
            for row in plan["completed"]]
    export = build_partial_export(plan=plan, plan_path=plan_path, review=review,
                                  verified_rows=rows, protected_artifacts=plan["artifact_sha256"])
    assert export["version"] == EXPORT_VERSION
    assert export["gate"]["passed"] is False
    assert export["gate"]["selected"] == 4
    assert export["gate"]["completed"] == 2
    assert export["gate"]["failed"] == 1
    assert export["gate"]["pending"] == 1
    assert export["recovery"]["source_terminal"] is False
    assert export["recovery"]["adopted_completed"] == 2
    assert export["recovery"]["failed_retried"] == 0
    assert export["recovery"]["pending_reclassified"] == 0
    assert export["public_db_adoption_allowed"] is False
    assert export["verified_projection"]["publishable"] is False
    assert export["verified_projection"]["rows"][0]["work_id"] == "wd:Q1000"


def test_partial_export_rejects_rows_outside_completed_partition(tmp_path):
    plan = _plan(tmp_path)
    plan_path = _write_plan(tmp_path, plan)
    review = {"review_result_sha256": "a" * 64, "selection_sha256": "b" * 64,
              "quality_sha256": "c" * 64}
    row = plan["failed"][0]
    with pytest.raises(PartialRecoveryError, match="does not cover exactly"):
        build_partial_export(plan=plan, plan_path=plan_path, review=review,
                             verified_rows=[{"work_id": row["work_id"], "input_hash": row["input_sha256"]}],
                             protected_artifacts=plan["artifact_sha256"])


def test_partial_export_does_not_change_plan_bytes(tmp_path):
    plan = _plan(tmp_path)
    plan_path = _write_plan(tmp_path, plan)
    before = plan_path.read_bytes()
    review = {"review_result_sha256": "a" * 64, "selection_sha256": "b" * 64,
              "quality_sha256": "c" * 64}
    rows = [{"work_id": row["work_id"], "input_hash": row["input_sha256"],
             "job_key": row["job_key"], "qid": row["qid"]} for row in plan["completed"]]
    build_partial_export(plan=plan, plan_path=plan_path, review=review,
                         verified_rows=rows, protected_artifacts=plan["artifact_sha256"])
    assert plan_path.read_bytes() == before


def _result(*label_ids, conflicts=None):
    return {"conflicts": conflicts or [], "needs_human_review": False,
            "labels": [{"label_id": label_id, "decision": "present", "scope": "work"}
                       for label_id in label_ids]}


def test_quality_uncertain_excludes_only_finding_labels_from_admission():
    result = resolve_admitted_labels(
        _result("genre.mystery", "theme.memory"),
        {"status": "uncertain", "label_ids": ["theme.memory"]},
    )
    assert [label["label_id"] for label in result["admitted_labels"]] == ["genre.mystery"]
    assert result["held_labels"] == [{"label_id": "theme.memory", "reason": "quality_review_uncertain"}]


def test_quality_held_matches_frozen_whole_packet_hold():
    result = resolve_admitted_labels(
        _result("genre.mystery", "theme.memory"),
        {"status": "held", "label_ids": ["theme.memory"]},
    )
    assert result["admitted_labels"] == []
    assert {label["label_id"] for label in result["held_labels"]} == {"genre.mystery", "theme.memory"}


def test_conflict_holds_all_mechanical_candidates():
    result = resolve_admitted_labels(_result("genre.mystery", conflicts=["contradiction"]))
    assert result["admitted_labels"] == []
    assert result["feature_adoption"]["status"] == "held_conflict"
