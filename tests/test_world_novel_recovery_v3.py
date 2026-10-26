"""Pending salvage is a process checkpoint, never a successful original gate."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def module():
    spec = importlib.util.spec_from_file_location("world_recovery3_test", ROOT / "scripts/recover_world_novel_expansion_v3.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_checkpoint_never_promotes_pending_salvage_or_retries_old_scope(tmp_path, monkeypatch):
    m = module()
    plan = {"original_counts": {"selected": 54, "completed": 24, "failed": 8, "pending": 22, "cli_invocations": 32},
            "completed": [{"work_id": "old-complete"}], "failed": [{"work_id": "old-failed"}],
            "unstarted": [{"qid": "Q1", "work_id": "new", "input_sha256": "new-hash"}], "artifact_sha256": {},
            "source": str(tmp_path / "original-source"), "original_run_id": "old54"}
    put(tmp_path / "original-source/manifest.json", {"window_index": 1})
    monkeypatch.setattr(m.v2.v1, "verify_recovery_plan", lambda *a: plan)
    monkeypatch.setattr(m, "pending_review_evidence", lambda *a: {})
    public_path, run_path = tmp_path / "public.json", tmp_path / "run.json"
    put(public_path, {"recovery_lineage": {"plan_sha256": "plan-hash"},
        "verified_projection": {"rows": [{"qid": "Q1", "input_work_id": "new", "input_hash": "new-hash"}]}})
    put(run_path, {"selected": 1, "input_identities": [{"work_id": "new", "input_hash": "new-hash"}]})
    control = {"window_index": 1, "run_id": "new22", "gate": {"passed": True}, "control_artifacts": {
        "export": {"path": str(public_path), "sha256": m.sha(public_path)},
        "run": {"path": str(run_path), "sha256": m.sha(run_path)}}}
    control_path, plan_path, review, failed = (tmp_path / name for name in ("control.json", "plan.json", "review.json", "tail"))
    for path in (control_path, plan_path, review):
        put(path, {})
    failed.mkdir()
    monkeypatch.setattr(m, "verify_control", lambda *a: control)
    result = m.checkpoint_evidence(plan_path, "plan-hash", review, "review-hash", failed, "exit-hash", control_path, "control-hash")
    assert result["original_gate_failed"] is True
    assert result["salvage_adoption_claimed"] is False
    assert result["original_counts"]["failed"] == 8
    assert result["next_window_index"] == 2
    assert result["max_next_classification_concurrency"] == result["max_next_review_concurrency"] == 2
    control["window_index"] = 9
    with pytest.raises(ValueError, match="fresh"):
        m.checkpoint_evidence(plan_path, "plan-hash", review, "review-hash", failed, "exit-hash", control_path, "control-hash")
    control["window_index"] = 1
    control["run_id"] = "old54"
    with pytest.raises(ValueError, match="fresh"):
        m.checkpoint_evidence(plan_path, "plan-hash", review, "review-hash", failed, "exit-hash", control_path, "control-hash")


def test_fresh_control_cannot_contain_completed_or_failed_original_input(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m.v2.v1, "verify_recovery_plan", lambda *a: {"unstarted": [{"qid": "Q1", "work_id": "fresh", "input_sha256": "fresh-hash"}],
        "source": str(tmp_path / "original-source"), "original_run_id": "old54"})
    put(tmp_path / "original-source/manifest.json", {"window_index": 1})
    monkeypatch.setattr(m, "pending_review_evidence", lambda *a: {})
    public, run = tmp_path / "public.json", tmp_path / "run.json"
    put(public, {"recovery_lineage": {"plan_sha256": "sealed"}, "verified_projection": {"rows": [
        {"qid": "Q2", "input_work_id": "old-failed", "input_hash": "old-hash"}]}})
    put(run, {"selected": 1, "input_identities": [{"work_id": "fresh", "input_hash": "fresh-hash"}]})
    monkeypatch.setattr(m, "verify_control", lambda *a: {"window_index": 1, "gate": {"passed": True}, "control_artifacts": {
        "export": {"path": str(public), "sha256": m.sha(public)}, "run": {"path": str(run), "sha256": m.sha(run)}}})
    with pytest.raises(ValueError, match="fresh"):
        m.checkpoint_evidence(tmp_path / "plan", "sealed", tmp_path / "review", "review-sha", tmp_path / "tail", "exit-sha", tmp_path / "control", "control-sha")


def test_next_window_delegates_existing_scheduler_with_real_passed_control(tmp_path, monkeypatch):
    m = module()
    control = tmp_path / "control.json"
    checkpoint = {"fresh_control": str(control), "fresh_control_sha256": "passed-control-sha",
        "next_window_index": 2, "artifact_sha256": {}, "original_gate_failed": True}
    monkeypatch.setattr(m, "verify_checkpoint", lambda *a: checkpoint)
    monkeypatch.setattr(m.v2.runner, "load_sources", lambda *a, **k: [{}, {}, {"run_name": "next28", "selected": 28}])
    index, inventory = tmp_path / "index.json", tmp_path / "exclusions.json"
    put(inventory, {})
    put(index, {"exclusions": {"path": str(inventory), "sha256": m.sha(inventory)}})
    seen = []
    def run(runtime, window, pins, **kwargs):
        seen.append((window, kwargs))
        return {"selected": 28}
    monkeypatch.setattr(m.v2, "_run", run)
    assert m.run_next(tmp_path / "checkpoint", "checkpoint-sha", index, m.sha(index), tmp_path / "runtime", base=tmp_path) == {"selected": 28}
    assert len(seen) == 1
    assert seen[0][1]["prior_control"] == control
    assert seen[0][1]["prior_sha"] == "passed-control-sha"
    assert seen[0][1]["continuation"]["salvage_adoption_claimed"] is False


def test_pending_review_proof_requires_exact_completed_coverage_and_actual_failure(tmp_path):
    m = module()
    terminal = tmp_path / "terminal"
    proof = {"ordered_inputs_sha256": "inputs", "results_sha256": "results",
             "execution_context_manifest_sha256": "context", "manifest_sha256": "run"}
    put(terminal / "terminal-proof.json", proof)
    audit_path = tmp_path / "review/group-01/source-audit.json"
    audit = {"run_id": "original", "exit_code": 0, "reviews": [{"work_id": "w0"}, {"work_id": "w1"}],
             "source_sha256": {}, "artifact_sha256": {}}
    put(audit_path, audit)
    review_path = tmp_path / "review/quality-adoption-review.json"
    document = {"run_id": "original", **{key: proof[key] for key in (
        "ordered_inputs_sha256", "results_sha256", "execution_context_manifest_sha256")}, "run_manifest_sha256": "run",
        "source_audits": [{"path": str(audit_path), "sha256": m.sha(audit_path)}]}
    put(review_path, document)
    selection = tmp_path / "review.selection.json"
    put(selection, {"selected_work_ids": ["w0", "w1"]})
    receipt = tmp_path / "review.result.json"
    put(receipt, {"recovery_plan_sha256": "plan", "full": True, "reviewed_work_count": 2,
        "selection_path": str(selection), "selection_sha256": m.sha(selection), "review_path": str(review_path)})
    put(tmp_path / "review.process/process-exit.json", {"actual_exit_code": 0, "process_exit_observed": True})
    failed = tmp_path / "tail/stages/01"
    put(failed / "process-exit.json", {"actual_exit_code": 1, "process_exit_observed": True})
    (failed / "stderr.txt").write_text("Run is not terminal for exact selected input/config", encoding="utf-8")
    value = {"completed": [{"work_id": "w0"}, {"work_id": "w1"}], "terminal": str(terminal), "original_run_id": "original"}
    assert str(audit_path) in m.pending_review_evidence(value, "plan", receipt, m.sha(receipt), failed, m.sha(failed / "process-exit.json"))
    audit["reviews"] = [{"work_id": "w0"}, {"work_id": "w0"}]
    put(audit_path, audit)
    document["source_audits"][0]["sha256"] = m.sha(audit_path)
    put(review_path, document)
    with pytest.raises(ValueError, match="duplicate or missing"):
        m.pending_review_evidence(value, "plan", receipt, m.sha(receipt), failed, m.sha(failed / "process-exit.json"))
