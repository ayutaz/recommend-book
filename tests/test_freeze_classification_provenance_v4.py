import hashlib
import importlib.util
import json
from pathlib import Path

from recommend_book.models import WorkInput


ROOT = Path(__file__).resolve().parents[1]


def freeze_module():
    spec = importlib.util.spec_from_file_location(
        "freeze_classification_execution_provenance_v4",
        ROOT / "scripts/freeze_classification_execution.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nonbaseline_freeze_binds_provenance_v4_artifacts_without_model_call(tmp_path):
    module = freeze_module()
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    ordered = inputs / "ordered.jsonl"
    raw = (WorkInput(work_id="freeze-v4-work", title="Fixture").model_dump_json() + "\n").encode()
    ordered.write_bytes(raw)
    (inputs / "manifest.json").write_text(
        json.dumps({"ordered_inputs_sha256": hashlib.sha256(raw).hexdigest()}),
        encoding="utf-8",
    )

    output = tmp_path / "prepared"
    report = module.freeze_execution(
        ordered,
        output,
        model="fixture-model",
        cli_version="explicit-saved-cli",
        batch_size=5,
        execution_spec="provenance-v4",
    )
    config = report["execution_context"]["run_config"]
    assert config["execution_spec"] == "provenance-v4"
    assert config["prompt_version"] == "4.0-provenance"
    assert "schemas/classification-provenance-v4.schema.json" in report["execution_context"]["artifact_sha256"]
    assert "src/recommend_book/classification/prompt_v3.py" in report["execution_context"]["artifact_sha256"]
    assert "src/recommend_book/classification/specs.py" in report["execution_context"]["artifact_sha256"]
    assert report["model_run_started"] is False
    assert (output / "artifacts/schemas/classification-provenance-v4.schema.json").is_file()
    assert (output / "artifacts/src/recommend_book/classification/prompt_v3.py").is_file()
