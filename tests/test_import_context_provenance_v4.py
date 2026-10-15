import hashlib
import importlib.util
import json
from pathlib import Path

from recommend_book.models import WorkInput


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nonbaseline_freeze_context_reaches_import_helper(tmp_path):
    freeze = load_script("freeze_classification_execution")
    importer = load_script("import_classification_database")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    ordered = inputs / "ordered.jsonl"
    raw = (WorkInput(work_id="import-v4-work", title="Fixture").model_dump_json() + "\n").encode()
    ordered.write_bytes(raw)
    (inputs / "manifest.json").write_text(
        json.dumps({"ordered_inputs_sha256": hashlib.sha256(raw).hexdigest()}),
        encoding="utf-8",
    )

    context_path = tmp_path / "prepared" / "manifest.json"
    freeze.freeze_execution(
        ordered,
        context_path.parent,
        model="fixture-model",
        cli_version="explicit-saved-cli",
        batch_size=1,
        execution_spec="provenance-v4",
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    config, _ = importer.check_context(context, ordered)

    assert config["execution_spec"] == "provenance-v4"
    artifact_names = importer.artifact_names_for_config(config)
    assert len(artifact_names) == context["execution_context"]["artifact_count"]
    assert set(context["execution_context"]["artifact_sha256"]) == set(artifact_names)
    assert "schemas/classification-provenance-v4.schema.json" in artifact_names
    assert "src/recommend_book/classification/prompt_v3.py" in artifact_names
    assert "src/recommend_book/classification/specs.py" in artifact_names
