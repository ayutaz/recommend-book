"""Recovery never resends completed/held inputs or rewrites their terminal proof."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location("world_recovery_test", ROOT / "scripts/recover_world_novel_expansion_v1.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def fixture(tmp_path, m):
    runtime, source = tmp_path / "runtime", tmp_path / "source"
    source.mkdir()
    name = "world21000-synthetic-stopped"
    run = runtime / "runtime/data/autonomous-next-v3/classification" / name / "run"
    terminal = runtime / ("terminal-" + name)
    works = [WorkInput(work_id=f"w{i}", title=f"Novel {i}", source_record_ids=[f"s{i}"]) for i in range(4)]
    inputs = source / "inputs.jsonl"
    inputs.write_text("".join(w.model_dump_json() + "\n" for w in works), encoding="utf-8")
    ledger = source / "ledger.json"
    put(ledger, {})
    db = source / "source.sqlite"
    db.write_bytes(b"synthetic pinned database; never opened in census")
    mapping = [{"input_work_id": w.work_id, "work_id": w.work_id, "wikidata_qid": f"Q{i+1}",
                "input_sha256": model_hash(w), "source_record_ids": w.source_record_ids} for i, w in enumerate(works)]
    put(source / "work-mapping.json", mapping)
    put(source / "manifest.json", {"version": "scale-content-classification-source-v1", "selected": 4, "window_index": 1,
        "source_database": str(db), "source_database_sha256": m.sha(db), "required_purpose_admissions_allowed": True,
        "chunks": [{"run_name": name, "inputs_path": str(inputs), "inputs_sha256": m.sha(inputs),
                    "usage_ledger_path": str(ledger), "usage_ledger_sha256": m.sha(ledger)}]})
    jobs = {f"job{i}": {"work_id": w.work_id, "status": status, **({"attempts": 1} if status != "not_submitted" else {})}
            for i, (w, status) in enumerate(zip(works, ["completed", "completed", "held", "not_submitted"]))}
    for key, state in jobs.items():
        if state["status"] == "not_submitted":
            continue
        put(run / "jobs" / (key + ".json"), state)
        put(run / "attempts" / (key + ".1.synthetic.request.json"), {"job_keys": [key]})
        if state["status"] == "completed":
            put(run / "results" / (key + ".json"), {"work_id": state["work_id"]})
    (run / "results.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    manifest = {"run_id": "stopped-original", "selected": 4, "completed": 2, "failed": 1, "pending": 1,
        "cli_invocations": 3, "jobs": jobs, "stopped": True, "stop_reasons": ["rate_limit"],
        "input_identities": [{"work_id": w.work_id, "input_hash": model_hash(w)} for w in works]}
    put(run / "manifest.json", manifest)
    put(run / "manifest.stopped-original.json", manifest)
    coordinator = tmp_path / "original-coordinator.py"
    coordinator.write_text("# original immutable native launcher\n", encoding="utf-8")
    put(terminal / "request.json", {"coordinator_path": str(coordinator), "coordinator_sha256": m.sha(coordinator)})
    put(terminal / "process-exit.json", {"actual_exit_code": 0, "process_exit_observed": True})
    put(terminal / "terminal-proof.json", {"run_id": manifest["run_id"], "manifest_sha256": m.sha(run / "manifest.json"),
        "results_sha256": m.sha(run / "results.jsonl"), "exit_code": 0, "process_exit_observed": True,
        "parent_request_sha256": m.sha(terminal / "request.json"), "parent_exit_observation_sha256": m.sha(terminal / "process-exit.json")})
    context = tmp_path / "context.json"
    put(context, {})
    put(terminal / "adoption-arguments.json", {"exit_code": 0, "run_dir": str(run), "ordered_inputs": str(inputs),
        "usage_ledger": str(ledger), "execution_context_manifest": str(context), "terminal_proof": str(terminal / "terminal-proof.json")})
    reservation = tmp_path / "reservation.json"
    put(reservation, {"source_path": str(source), "source_sha256": m.sha(source / "manifest.json"),
                      "input_hashes": [model_hash(w) for w in works], "run_name": name})
    return runtime, source, reservation, run


def test_seal_distinguishes_success_failure_and_actually_unsubmitted(tmp_path):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    before = m.sha(run / "manifest.json")
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    value = m.read(plan)
    assert value["original_counts"] == {"selected": 4, "completed": 2, "failed": 1, "pending": 1, "cli_invocations": 3}
    assert len(value["completed"]) == 2
    assert len(value["failed"]) == len(value["unstarted"]) == 1
    assert value["original_gate_failed"] is True
    assert m.sha(run / "manifest.json") == before
    with pytest.raises(FileExistsError):
        m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")


def test_not_submitted_with_actual_request_is_not_fresh(tmp_path):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    put(run / "attempts/job3.1.hidden.request.json", {"job_keys": ["job3"]})
    with pytest.raises(ValueError, match="unsubmitted"):
        m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")


def test_sealed_run_cannot_be_changed_or_relabelled(tmp_path):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    digest = m.sha(plan)
    value = m.read(run / "manifest.json")
    value["failed"] = 0
    put(run / "manifest.json", value)
    with pytest.raises(ValueError, match="bytes"):
        m.verify_recovery_plan(plan, digest)


def test_resume_tail_has_no_native_execution_and_keeps_original_scope():
    m = module()
    source = "def execute_window():\n    observed = terminal.execute(runtime, name, db)\n    return observed\n"
    updated = m.review_only_source(source)
    assert "terminal.execute" not in updated
    assert "RECOVERY_OBSERVED" in updated
    with pytest.raises(ValueError, match="one terminal"):
        m.review_only_source("def execute_window():\n    pass\n")


def test_review_request_has_only_completed_unchanged_native_scope(tmp_path):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    args = m.review_request(plan, m.sha(plan), tmp_path / "review", concurrency=2)
    selected = m.read(args["selection_path"])
    assert selected["selected_work_ids"] == ["w0", "w1"]
    assert selected["classification_requests"] == 0
    assert selected["original_gate_failed"] is True
    assert args["run_dir"] == str(run)
    assert args["ordered_inputs"] == str(source / "inputs.jsonl")
    assert args["max_review_concurrency"] == 2
    with pytest.raises(ValueError, match="one or two"):
        m.review_request(plan, m.sha(plan), tmp_path / "wide-review", concurrency=4)


def test_new_orphan_request_after_seal_also_blocks_recovery(tmp_path):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    put(run / "attempts/job3.1.later.request.json", {"job_keys": ["job3"]})
    with pytest.raises(ValueError, match="unsubmitted"):
        m.verify_recovery_plan(plan, m.sha(plan))


def test_review_child_adapts_scheduling_only_without_mutating_frozen_files(tmp_path, monkeypatch):
    m = module()
    calls = []
    fake = SimpleNamespace()
    def execute(args):
        calls.append(args)
        assert fake.review_concurrency(args) == 1
        return {"reviewed_work_count": 2}
    fake.execute = execute
    monkeypatch.setitem(sys.modules, "review_scale_content_v6", fake)
    arguments = {field: str(tmp_path / field) for field in (
        "runtime_root", "ordered_inputs", "run_dir", "execution_context_manifest", "terminal_proof", "ledger", "output", "selection_path")}
    arguments["max_review_concurrency"] = 1
    assert m._review_child(arguments) == {"reviewed_work_count": 2}
    assert len(calls) == 1
    assert isinstance(calls[0].ordered_inputs, Path)


def test_review_seals_process_result_and_never_reaches_classifier(tmp_path, monkeypatch):
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    monkeypatch.setattr(m, "_chain", lambda value: [])
    seen = []
    def observed(runtime_arg, code, arguments, output, *, verify):
        verify()
        assert "_review_child" in code
        assert "run_classification" not in code and "terminal.execute" not in code
        seen.append(json.loads(arguments[0]))
        return {"reviewed_work_count": 2, "review_path": str(tmp_path / "quality.json")}
    monkeypatch.setattr(m, "_observed_call", observed)
    receipt = m.review_completed(plan, m.sha(plan), tmp_path / "review", concurrency=2)
    assert len(seen) == 1
    assert m.read(receipt)["original_gate_failed"] is True
    assert m.read(receipt)["recovery_plan_sha256"] == m.sha(plan)


def fake_tail(tmp_path, m, monkeypatch, *, passed=False):
    """Only orchestration is fake: the real immutable native records stay pinned."""
    runtime, source, reservation, run = fixture(tmp_path, m)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    selection = tmp_path / "review.selection.json"
    put(selection, {"selected_work_ids": ["w0", "w1"]})
    review_result = tmp_path / "review.result.json"
    put(review_result, {"recovery_plan_sha256": m.sha(plan), "full": True, "reviewed_work_count": 2,
                       "selection_path": str(selection), "selection_sha256": m.sha(selection)})
    monkeypatch.setattr(m, "_chain", lambda value: [])
    def load_coordinator(runtime, reservation_checker, *, base):
        source_text = (
            "def execute_window(source, runtime, number):\n"
            "    verify_reservation(source, number)\n"
            "    observed = terminal.execute(runtime, 'old', 'db')\n"
            "    review = review_content_window(runtime, observed, 'old', full=True)\n"
            "    assert observed['run_dir'] == EXPECTED_RUN\n"
            "    assert review['reviewed_work_count'] == 2\n"
            f"    report = {{'version':'scale-content-native-classification-export-v1','gate':{{'passed':{passed}}}}}\n"
            "    save(DESTINATION, report)\n"
            "    return report\n")
        # Any accidentally retained classifier call raises immediately.
        terminal = SimpleNamespace(execute=lambda *a: pytest.fail("native classification was retried"))
        return SimpleNamespace(_expansion_source=source_text, verify_reservation=reservation_checker,
            terminal=terminal, EXPECTED_RUN=str(run), DESTINATION=Path(base) / "export.json", save=m.save)
    monkeypatch.setitem(sys.modules, "run_world_novel_expansion_v1", SimpleNamespace(load_coordinator=load_coordinator))
    return plan, review_result, run


def test_tail_adopts_only_saved_successes_preserves_failed_original_scope(tmp_path, monkeypatch):
    m = module()
    plan, review_result, run = fake_tail(tmp_path, m, monkeypatch)
    before = m.sha(run / "manifest.json")
    result = m.recover_completed(plan, m.sha(plan), review_result, tmp_path / "tail")
    assert result["gate"]["passed"] is False
    assert result["recovery"]["original_counts"] == m.read(plan)["original_counts"]
    assert result["recovery"]["completed_reclassified"] == 0
    assert result["recovery"]["failed_retried"] == 0
    assert m.sha(run / "manifest.json") == before


def test_tail_cannot_present_salvage_as_successful_original_window(tmp_path, monkeypatch):
    m = module()
    plan, review_result, run = fake_tail(tmp_path, m, monkeypatch, passed=True)
    with pytest.raises(ValueError, match="original failed"):
        m.recover_completed(plan, m.sha(plan), review_result, tmp_path / "tail")
    assert not (tmp_path / "tail/export.json").exists()


def test_tail_rejects_failed_or_unstarted_work_in_review_scope(tmp_path, monkeypatch):
    m = module()
    plan, review_result, run = fake_tail(tmp_path, m, monkeypatch)
    selection = tmp_path / "other.selection.json"
    put(selection, {"selected_work_ids": ["w0", "w2"]})
    review = m.read(review_result)
    review.update(selection_path=str(selection), selection_sha256=m.sha(selection))
    put(review_result, review)
    with pytest.raises(ValueError, match="Review scope"):
        m.recover_completed(plan, m.sha(plan), review_result, tmp_path / "tail")


def test_unstarted_subset_keeps_exact_input_identity_and_rebinds_only_its_ledger(tmp_path, monkeypatch):
    from recommend_book import scale_content_recovery_v1, usage_guard
    m = module()
    runtime, source, reservation, run = fixture(tmp_path, m)
    works = m._works(source / "inputs.jsonl")
    ledger = {"inputs": {model_hash(work): {"input_ref": {"path": str(source / "inputs.jsonl"),
               "sha256": m.sha(source / "inputs.jsonl"), "line_number": index + 1}}
               for index, work in enumerate(works)}, "derived_artifacts": {
        "keep": {"originating_input_hashes": [model_hash(works[3])]},
        "remove": {"originating_input_hashes": [model_hash(works[0])]}}}
    put(source / "ledger.json", ledger)
    records = source / "records.jsonl"
    records.write_text("".join(json.dumps({"source_id": f"Q{i+1}"}) + "\n" for i in range(4)), encoding="utf-8")
    manifest = m.read(source / "manifest.json")
    manifest.update(records_path=str(records), records_sha256=m.sha(records), required_input_usages=["external_llm", "display"])
    manifest["chunks"][0]["usage_ledger_sha256"] = m.sha(source / "ledger.json")
    put(source / "manifest.json", manifest)
    reserved = m.read(reservation)
    reserved["source_sha256"] = m.sha(source / "manifest.json")
    put(reservation, reserved)
    plan = m.seal_recovery_plan(source, runtime, reservation, tmp_path / "recovery")
    seen = []
    class Guard:
        def __init__(self, *args, **kwargs):
            pass
        def check_input(self, work, purpose):
            seen.append((work.work_id, purpose))
            return SimpleNamespace(model_dump=lambda **kwargs: {"allowed": True})
    monkeypatch.setattr(usage_guard, "UsageGuard", Guard)
    monkeypatch.setattr(scale_content_recovery_v1, "_verify_mapping_identity", lambda db, mapping, works: None)
    output = tmp_path / "fresh-unstarted"
    prepared = m.prepare_unstarted(plan, m.sha(plan), output, "fresh-unstarted-r1")
    result = m.read(prepared)
    assert [work.work_id for work in m._works(result["ordered_inputs_path"])] == ["w3"]
    assert model_hash(m._works(result["ordered_inputs_path"])[0]) == model_hash(works[3])
    assert seen == [("w3", "external_llm"), ("w3", "display")]
    new_ledger = m.read(result["usage_ledger_path"])
    assert set(new_ledger["inputs"]) == {model_hash(works[3])}
    assert set(new_ledger["derived_artifacts"]) == {"keep"}
    ref = new_ledger["inputs"][model_hash(works[3])]["input_ref"]
    assert ref == {"path": str(output / "ordered-inputs.jsonl"), "sha256": m.sha(output / "ordered-inputs.jsonl"), "line_number": 1}
    assert result["chunks"][0]["run_name"] == "fresh-unstarted-r1"
    assert result["source_database"] == manifest["source_database"]
    assert result["recovery_lineage"]["original_gate_failed"] is True
    assert result["recovery_lineage"]["max_classification_concurrency"] == 2
    assert m.read(source / "ledger.json") == ledger
    with pytest.raises(FileExistsError):
        m.prepare_unstarted(plan, m.sha(plan), output, "fresh-unstarted-r1")
