import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.runner import build_run_config
from recommend_book.models import WorkInput


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ["config/sources.json", "config/taxonomy.json", "schemas/classification.schema.json",
             "src/recommend_book/classification/prompt.py", "src/recommend_book/classification/validation.py",
             "src/recommend_book/classification/runner.py", "src/recommend_book/models.py",
             "src/recommend_book/jobs.py", "pyproject.toml", "uv.lock"]


def module():
    spec = importlib.util.spec_from_file_location("freeze_execution", ROOT / "scripts/freeze_classification_execution.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def fixture(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    path = inputs / "ordered.jsonl"
    raw = (WorkInput(work_id="w1", title="Fixture").model_dump_json() + "\r\n").encode()
    path.write_bytes(raw)
    context = {"validation": "legacy input policy", "prompt_hash": "old-context-only"}
    manifest = {"ordered_inputs_sha256": hashlib.sha256(raw).hexdigest(), "processing_context": context}
    (inputs / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    code = tmp_path / "code"
    for relative in ARTIFACTS:
        target = code / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return path, tmp_path / "prepared", code, manifest


def invoke(m, fixture, monkeypatch, **overrides):
    path, output, code, _ = fixture
    monkeypatch.setattr(m, "ROOT", code)
    kwargs = {"model": "fixture-model", "cli_version": "explicit-saved-cli", "batch_size": 5,
              "concurrency": 2, "timeout": 123, "max_run_seconds": 30,
              "max_observed_tokens": 500, "stop_on_rate_limit": True}
    kwargs.update(overrides)
    return m.freeze_execution(path, output, **kwargs)


def test_prepared_namespace_keeps_exact_bytes_old_context_and_new_contract(fixture, monkeypatch):
    m = module()
    import recommend_book.classification.runner as runner
    def prohibited(*args, **kwargs): raise AssertionError("CLI invocation or discovery prohibited")
    monkeypatch.setattr(runner, "resolve_executable", prohibited)
    monkeypatch.setattr(runner, "cli_version", prohibited)
    monkeypatch.setattr(runner.subprocess, "run", prohibited)
    path, output, code, old = fixture
    report = invoke(m, fixture, monkeypatch)
    assert report["execution_state"] == "prepared_not_started"
    assert report["cli_version_verification"] == "supplied_unverified"
    assert report["model_run_started"] is False and report["network_requests"] == 0
    assert report["input_provenance"]["processing_context"] == old["processing_context"]
    expected = build_run_config("fixture-model", "explicit-saved-cli", json.loads((code / "config/taxonomy.json").read_text()), 5)
    assert report["execution_context"]["run_config"] == expected
    assert report["execution_context"]["run_config"]["validation_version"] == "2.0"
    assert report["execution_context"]["controls"] == {"batch_size": 5, "concurrency": 2, "timeout": 123,
        "max_run_seconds": 30, "max_observed_tokens": 500, "stop_on_rate_limit": True}
    assert (output / "inputs/ordered.jsonl").read_bytes() == path.read_bytes()
    assert (output / "inputs/manifest.json").read_bytes() == path.with_name("manifest.json").read_bytes()
    for relative in ARTIFACTS:
        raw = (code / relative).read_bytes()
        assert (output / "artifacts" / relative).read_bytes() == raw
        assert report["execution_context"]["artifact_sha256"][relative] == hashlib.sha256(raw).hexdigest()
    assert json.loads((output / "manifest.json").read_text()) == report


@pytest.mark.parametrize("property_name", ["ordered_inputs_sha256", "work_inputs_sha256"])
def test_manifest_hash_is_exact_input_bytes(fixture, monkeypatch, property_name):
    m = module()
    path, output, _, old = fixture
    old[property_name] = old.pop("ordered_inputs_sha256")
    path.with_name("manifest.json").write_text(json.dumps(old))
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    with pytest.raises(ValueError, match="input SHA"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()


def test_missing_or_contradictory_declared_input_hash_is_rejected(fixture, monkeypatch):
    m = module()
    path, output, _, old = fixture
    old["work_inputs_sha256"] = "0" * 64
    path.with_name("manifest.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="input SHA"):
        invoke(m, fixture, monkeypatch)
    path.with_name("manifest.json").write_text(json.dumps({"processing_context": {}}))
    with pytest.raises(ValueError, match="input SHA"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()


def test_duplicate_work_expression_identity_rejected(fixture, monkeypatch):
    m = module()
    path, output, _, old = fixture
    raw = path.read_bytes() * 2
    path.write_bytes(raw)
    old["ordered_inputs_sha256"] = hashlib.sha256(raw).hexdigest()
    path.with_name("manifest.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="Duplicate work/expression"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()


@pytest.mark.parametrize("invalid_json", ["duplicate", "nan", "overflow"])
def test_input_json_rejects_duplicate_properties_and_nonfinite_values(fixture, monkeypatch, invalid_json):
    m = module()
    path, output, _, old = fixture
    raw = path.read_bytes()
    if invalid_json == "duplicate":
        raw = raw.replace(b'"title":"Fixture"', b'"title":"Fixture","title":"Fixture"')
    else:
        raw = raw.replace(b'"first_publication_year":null', b'"first_publication_year":NaN' if invalid_json == "nan" else b'"first_publication_year":1e999')
    path.write_bytes(raw)
    old["ordered_inputs_sha256"] = hashlib.sha256(raw).hexdigest()
    path.with_name("manifest.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="Duplicate JSON property|Non-finite JSON"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()


def test_manifest_duplicate_properties_are_rejected(fixture, monkeypatch):
    m = module()
    path, output, _, old = fixture
    digest = old["ordered_inputs_sha256"]
    path.with_name("manifest.json").write_text('{"ordered_inputs_sha256":"' + digest + '","ordered_inputs_sha256":"' + digest + '"}')
    with pytest.raises(ValueError, match="Duplicate JSON property"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()


@pytest.mark.parametrize("changing", ["input", "new_input_and_manifest", "manifest", "validation", "taxonomy", "config_values"])
def test_change_during_preparation_refuses_publish_and_cleans_only_own_stage(fixture, monkeypatch, changing):
    m = module()
    path, output, code, _ = fixture
    unrelated = output.parent / ".unrelated-stage"
    unrelated.mkdir()
    (unrelated / "keep").write_text("untouched")
    original = m.build_run_config
    calls = []
    def change_before_publish(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            if changing == "new_input_and_manifest":
                raw = (WorkInput(work_id="new-work", title="Different input").model_dump_json() + "\n").encode()
                path.write_bytes(raw)
                path.with_name("manifest.json").write_text(json.dumps({"ordered_inputs_sha256": hashlib.sha256(raw).hexdigest()}))
                return original(*args, **kwargs)
            if changing == "config_values":
                return {**original(*args, **kwargs), "model": "different-model"}
            target = {"input": path, "manifest": path.with_name("manifest.json"),
                      "validation": code / "src/recommend_book/classification/validation.py",
                      "taxonomy": code / "config/taxonomy.json"}[changing]
            target.write_bytes(target.read_bytes() + b"\n")
        return original(*args, **kwargs)
    monkeypatch.setattr(m, "build_run_config", change_before_publish)
    with pytest.raises(ValueError, match="changed during preparation"):
        invoke(m, fixture, monkeypatch)
    assert not output.exists()
    assert (unrelated / "keep").read_text() == "untouched"
    assert not list(output.parent.glob(".prepared.prepare-*"))


def test_existing_or_input_directory_output_is_rejected(fixture, monkeypatch):
    m = module()
    path, output, code, manifest = fixture
    output.mkdir()
    (output / "keep").write_text("original")
    with pytest.raises(ValueError, match="already exists"):
        invoke(m, fixture, monkeypatch)
    assert (output / "keep").read_text() == "original"
    nested = (path, path.parent / "nested", code, manifest)
    with pytest.raises(ValueError, match="input directory"):
        invoke(m, nested, monkeypatch)
    assert not nested[1].exists()


def test_invalid_controls_rejected_without_output(fixture, monkeypatch):
    m = module()
    with pytest.raises(ValueError, match="batch_size"):
        invoke(m, fixture, monkeypatch, batch_size=0)
    assert not fixture[1].exists()


def test_cli_prepares_only_supplied_controls(fixture, monkeypatch, capsys):
    m = module()
    path, output, code, _ = fixture
    monkeypatch.setattr(m, "ROOT", code)
    m.main(["--input", str(path), "--output", str(output), "--model", "fixture-model",
            "--cli-version", "supplied-cli", "--batch-size", "2", "--concurrency", "3",
            "--timeout", "99", "--max-run-seconds", "0", "--max-observed-tokens", "0",
            "--stop-on-rate-limit"])
    report = json.loads((output / "manifest.json").read_text())
    assert report["execution_context"]["run_config"]["cli_version"] == "supplied-cli"
    assert report["execution_context"]["controls"] == {"batch_size": 2, "concurrency": 3, "timeout": 99,
        "max_run_seconds": 0, "max_observed_tokens": 0, "stop_on_rate_limit": True}
    assert json.loads(capsys.readouterr().out)["execution_state"] == "prepared_not_started"
