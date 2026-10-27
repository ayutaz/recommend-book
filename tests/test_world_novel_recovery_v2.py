"""Explicit two-slot scheduling preserves source/attempt/publication boundaries."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def module():
    spec = importlib.util.spec_from_file_location("world_recovery2_test", ROOT / "scripts/recover_world_novel_expansion_v2.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_review_adapter_passes_only_completed_to_two_slots(tmp_path):
    m = module()
    run = tmp_path / "run"
    put(run / "manifest.json", {"jobs": {"a": {"work_id": "success", "status": "completed"},
        "b": {"work_id": "failed", "status": "held"}}, "completed": 1,
        "elapsed_seconds": 10, "usage": {}, "stop_reasons": []})
    actual = {key: str(tmp_path / key) for key in ("ordered_inputs", "execution_context_manifest", "terminal_proof", "usage_ledger")}
    actual["run_dir"] = str(run)
    Path(actual["usage_ledger"]).write_text("{}")
    calls = []
    def frozen(runtime, code, arguments):
        args = json.loads(arguments[0])
        assert args["max_review_concurrency"] == 2
        assert args["timeout"] == 900
        assert m.read(args["selection_path"])["selected_work_ids"] == ["success"]
        assert "_review_child" in code
        calls.append(args)
        return {"reviewed_work_count": 1, "observed_usage": {}, "elapsed_seconds": 1}
    review = m.low_concurrency_review(tmp_path, frozen, verify=lambda: None, lineage={"original_gate_failed": True})
    result = review(tmp_path, actual, "fresh-name", full=True)
    assert result["full"] is True and len(calls) == 1
    assert result["reviewed_work_count"] == 1


def test_review_budget_exhaustion_never_starts_model(tmp_path):
    m = module()
    run = tmp_path / "run"
    put(run / "manifest.json", {"elapsed_seconds": 3500, "usage": {}, "stop_reasons": ["rate_limit"]})
    review = m.low_concurrency_review(tmp_path, lambda *a: pytest.fail("review started"), verify=lambda: None, lineage=None)
    with pytest.raises(ValueError, match="budget"):
        review(tmp_path, {"run_dir": str(run)}, "stopped", full=True)


def test_recovery_source_cannot_resend_any_completed_or_failed_input(tmp_path, monkeypatch):
    m = module()
    plan = {"unstarted": [{"work_id": "fresh", "input_sha256": "hash", "qid": "Q1"}],
            "original_counts": {"selected": 54, "completed": 24, "failed": 8, "pending": 22, "cli_invocations": 32},
            "original_gate_failed": True}
    monkeypatch.setattr(m.v1, "verify_recovery_plan", lambda *a: plan)
    source = tmp_path / "source"
    source.mkdir()
    put(source / "manifest.json", {"recovery_lineage": {"plan": str(tmp_path / "plan"), "plan_sha256": "sealed",
        "original_counts": plan["original_counts"], "original_gate_failed": True,
        "unsubmitted_work_ids": ["fresh"], "max_classification_concurrency": 2,
        "completed_reclassified": 0, "failed_retried": 0, "prior_reservations_unchanged": True}})
    monkeypatch.setattr(m, "source_window", lambda *a: {"source_path": str(source), "run_name": "new-run",
        "window_index": 1, "selected": 1, "input_hashes": ["wrong-completed-hash"], "qids": ["Q1"], "work_ids": ["fresh"]})
    with pytest.raises(ValueError, match="unsubmitted"):
        m.check_unstarted(tmp_path / "plan", "sealed", source, m.sha(source / "manifest.json"))


def test_existing_actual_run_is_never_reentered(tmp_path):
    m = module()
    runtime = tmp_path / "runtime"
    (runtime / "terminal-fresh").mkdir(parents=True)
    with pytest.raises(FileExistsError, match="native"):
        m.require_fresh_execution(runtime, {"run_name": "fresh"}, tmp_path)


def test_no_native_run_or_external_call_is_available_in_check(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m.runner, "prepare_window", lambda *a, **k: pytest.fail("preflight ran"))
    monkeypatch.setattr(m.runner, "execute_window", lambda *a, **k: pytest.fail("classification ran"))
    # Source byte verification happens before loading any real external source.
    manifest = tmp_path / "manifest.json"
    put(manifest, {})
    with pytest.raises(ValueError, match="bytes"):
        m.source_window(tmp_path, "0" * 64)
