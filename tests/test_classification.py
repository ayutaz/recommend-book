from copy import deepcopy
import json
import subprocess

import pytest

from recommend_book.models import Evidence, WorkInput
from recommend_book.classification import (
    build_prompt, input_hash, load_taxonomy, validate_result, read_results,
)
from recommend_book.classification.runner import run_classification


@pytest.fixture
def work():
    return WorkInput(work_id="work-1", title="Not a memory source", original_language="en",
        evidence=[Evidence(evidence_id="e1", kind="synopsis", text="A detective solves a murder.",
            source_url="https://example.org/work", rights_policy_id="fixture", llm_input_allowed=True, locator="summary"),
            Evidence(evidence_id="secret", kind="excerpt", text="WITHHELD TEXT", source_url="https://example.org",
                rights_policy_id="unresolved", llm_input_allowed=False)])


def result_for(work):
    return {
        "work_id": work.work_id, "expression_id": work.expression_id, "target_language": "en",
        "taxonomy_version": "1.0", "prompt_version": "1.0", "model": "gpt-6.1-sol", "cli_version": "fake-1",
        "input_hash": input_hash(work), "labels": [{"label_id": "genre.mystery", "axis": "genre",
            "decision": "present", "scope": "work", "expression_id": None,
            "relevance": 0.9, "raw_confidence": 0.8, "calibrated_confidence": None,
            "calibration_status": "uncalibrated", "calibration_version": None,
            "evidence": [{"evidence_id": "e1", "locator": "summary", "quote": "detective solves a murder",
                "summary": None, "directness": "direct"}], "missing_reason": None, "spoiler": "premise"}],
        "missing_axes": [{"axis": axis, "reason": "Insufficient evidence", "decision": "unknown"}
            for axis in ["theme", "structure", "protagonist", "style", "atmosphere"]],
        "conflicts": [], "needs_human_review": True,
    }


def test_taxonomy_six_axes_and_quality_subset():
    taxonomy = load_taxonomy()
    assert len(taxonomy["axes"]) == 6
    assert 60 <= len(taxonomy["labels"]) <= 100
    assert 20 <= sum(label["primary"] for label in taxonomy["labels"]) <= 30
    assert len({label["label_id"] for label in taxonomy["labels"]}) == len(taxonomy["labels"])
    assert all(label["definition"] and label["include"] and label["exclude"] and label["examples"] for label in taxonomy["labels"])


def test_prompt_filters_unapproved_content_and_requires_data_only(work):
    prompt = build_prompt(work, cli_version="fake-1")
    assert "WITHHELD TEXT" not in prompt
    assert "secret" not in prompt
    assert "A detective solves a murder" in prompt
    assert "no tools" in prompt.lower()
    assert "memory" in prompt.lower()


def test_valid_result_and_reference_verification(work):
    valid = result_for(work)
    assert validate_result(valid, work) == valid
    for mutate in [
        lambda x: x.update(work_id="invented"),
        lambda x: x["labels"][0].update(label_id="invented"),
        lambda x: x["labels"][0]["evidence"][0].update(evidence_id="secret"),
        lambda x: x["labels"][0]["evidence"][0].update(quote="fabricated quotation"),
        lambda x: x["labels"][0]["evidence"][0].update(locator="wrong"),
        lambda x: x["labels"][0].update(calibrated_confidence=0.9),
        lambda x: x.update(missing_axes=[]),
        lambda x: x["labels"][0].update(decision="unknown", relevance=0.0),
        lambda x: x["labels"][0].update(axis="style"),
        lambda x: x["labels"][0].update(evidence=[]),
    ]:
        invalid = deepcopy(valid)
        mutate(invalid)
        with pytest.raises(ValueError):
            validate_result(invalid, work)


def test_original_style_rejects_translated_excerpt(work):
    work.evidence[0].kind = "excerpt"
    work.evidence[0].expression_ref = "translated-1"
    valid = result_for(work)
    label = valid["labels"][0]
    label.update(label_id="style.concise", axis="style", scope="original")
    valid["missing_axes"] = [x for x in valid["missing_axes"] if x["axis"] != "style"]
    valid["missing_axes"].append({"axis": "genre", "reason": "missing", "decision": "unknown"})
    with pytest.raises(ValueError, match="original|expression"):
        validate_result(valid, work)


def test_runner_resume_and_raw_events_separate(monkeypatch, tmp_path, work):
    calls = []
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        calls.append(command)
        assert kwargs["shell"] is False
        assert command[-1] == "-"
        assert "--ignore-user-config" in command
        assert "WITHHELD TEXT" not in kwargs["input"]
        path = command[command.index("-o") + 1]
        from pathlib import Path
        Path(path).write_text(json.dumps(result_for(work)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":9}}\n', "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    first = run_classification([work], tmp_path, executable="fixture-executable")
    second = run_classification([work], tmp_path, executable="fixture-executable")
    assert first["completed"] == 1
    assert first["usage"]["input_tokens"] == 12
    assert second["reused"] == 1
    assert len(calls) == 1
    assert len(read_results(tmp_path)) == 1
    assert len(list((tmp_path / "attempts").glob("*.stdout.jsonl"))) == 1


def test_failure_never_fabricates_result_and_retry_capped(monkeypatch, tmp_path, work):
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed"}\n', "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    monkeypatch.setattr("recommend_book.classification.runner.time.sleep", lambda _: None)
    manifest = run_classification([work], tmp_path, executable="fixture-executable")
    assert manifest["failed"] == 1
    assert manifest["attempts"] == 3
    assert read_results(tmp_path) == []
    assert next(iter(manifest["jobs"].values()))["error_category"] == "missing_output"


def test_changed_input_is_new_job(monkeypatch, tmp_path, work):
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        from pathlib import Path
        Path(command[command.index("-o")+1]).write_text(json.dumps(result_for(work)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    run_classification([work], tmp_path, executable="fixture-executable")
    work.evidence[0].text += " More evidence."
    manifest = run_classification([work], tmp_path, executable="fixture-executable")
    assert manifest["reused"] == 0
    assert len(read_results(tmp_path)) == 1


def test_small_batch_exact_ids_and_partial_recovery(monkeypatch, tmp_path, work):
    other = work.model_copy(deep=True)
    other.work_id = "work-2"
    calls = []
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        from pathlib import Path
        calls.append(command)
        Path(command[command.index("-o")+1]).write_text(json.dumps({"results":[result_for(other), result_for(work)]}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"type":"turn.completed","usage":{"input_tokens":12}}', "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    manifest = run_classification([work, other], tmp_path, executable="fixture-executable", batch_size=5)
    assert manifest["completed"] == 2
    assert len(calls) == 1
    assert manifest["usage"]["input_tokens"] == 12
    assert len(read_results(tmp_path)) == 2


def test_tools_are_a_permanent_failed_classification(monkeypatch, tmp_path, work):
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        from pathlib import Path
        Path(command[command.index("-o")+1]).write_text(json.dumps(result_for(work)), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"type":"item.completed","item":{"type":"command_execution"}}', "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    manifest = run_classification([work], tmp_path, executable="fixture-executable")
    assert manifest["failed"] == 1
    assert manifest["attempts"] == 1
    assert read_results(tmp_path) == []


def test_batch_rejects_duplicate_ids(monkeypatch, tmp_path, work):
    other = work.model_copy(deep=True)
    other.work_id = "work-2"
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        from pathlib import Path
        Path(command[command.index("-o")+1]).write_text(json.dumps({"results":[result_for(work), result_for(work)]}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    monkeypatch.setattr("recommend_book.classification.runner.time.sleep", lambda _: None)
    manifest = run_classification([work, other], tmp_path, executable="fixture-executable", batch_size=5)
    assert manifest["failed"] == 2
    assert manifest["cli_invocations"] == 3
    assert read_results(tmp_path) == []


def test_usage_limit_stops_new_work(monkeypatch, tmp_path, work):
    other = work.model_copy(deep=True)
    other.work_id = "work-2"
    def fake(command, **kwargs):
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "fake-1", "")
        return subprocess.CompletedProcess(command, 1, "", "usage limit reached")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    manifest = run_classification([work, other], tmp_path, executable="fixture-executable", concurrency=1)
    assert manifest["failed"] == 1
    assert manifest["pending"] == 1
    assert manifest["attempts"] == 1
    assert manifest["stopped"] is True
