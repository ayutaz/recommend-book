import json
from pathlib import Path
import pytest
from recommend_book.cli import main
from recommend_book.models import WorkInput
from recommend_book.classification.runner import run_classification


def snapshot(tmp_path):
    path = tmp_path / "inputs.jsonl"
    path.write_text(json.dumps(WorkInput(work_id="w", title="Fixture").model_dump()) + "\n", encoding="utf-8")
    return path


def test_classify_cli_forwards_resource_controls(monkeypatch, tmp_path):
    seen = {}
    def fake(inputs, output, **kwargs):
        seen.update(kwargs)
        assert inputs[0].work_id == "w"
        return {"completed":0, "pending":1}
    monkeypatch.setattr("recommend_book.classification.run_classification", fake)
    assert main(["classify", "--input", str(snapshot(tmp_path)), "--output", str(tmp_path / "run"),
        "--batch-size", "5", "--timeout", "180", "--max-run-seconds", "60", "--max-observed-tokens", "5000"]) == 0
    assert seen["batch_size"] == 5
    assert seen["timeout"] == 180
    assert seen["max_run_seconds"] == 60
    assert seen["max_observed_tokens"] == 5000


def test_zero_limit_is_empty_run_in_cli_and_api(monkeypatch, tmp_path):
    monkeypatch.setattr("recommend_book.classification.run_classification", run_classification)
    assert main(["classify", "--input", str(snapshot(tmp_path)), "--output", str(tmp_path / "run"), "--limit", "0"]) == 0
    assert json.loads((tmp_path / "run/manifest.json").read_text())["selected"] == 0


@pytest.mark.parametrize("option,value,kwargs", [
    ("--batch-size","0",{"batch_size":0}), ("--batch-size","11",{"batch_size":11}),
    ("--timeout","0",{"timeout":0}), ("--timeout","nan",{"timeout":float("nan")}),
    ("--max-run-seconds","-1",{"max_run_seconds":-1}),
    ("--max-observed-tokens","-1",{"max_observed_tokens":-1}),
])
def test_cli_api_share_invalid_control_boundaries(tmp_path, option, value, kwargs):
    work = WorkInput(work_id="w",title="Fixture")
    with pytest.raises(ValueError):
        run_classification([work], tmp_path / "api", executable="must-not-run", **kwargs)
    assert main(["classify","--input",str(snapshot(tmp_path)),"--output",str(tmp_path / "cli"),option,value]) == 1
