"""Audit genuine local fake-CLI witnesses without counting a projection twice."""
import importlib.util
import json
from pathlib import Path

from test_light_novel_classification_v1 import build_verified_export_fixture
from recommend_book.light_novel_classification_v1 import reproject_feature_export, sha


def test_audit_keeps_native_and_projection_history_but_counts_calls_once(tmp_path, monkeypatch):
    original = build_verified_export_fixture(tmp_path, monkeypatch)
    corrected = reproject_feature_export(original["manifest_path"], original["manifest_sha256"], tmp_path / "projection")
    index = tmp_path / "history.json"
    index.write_text(json.dumps({"version": "light-novel-series-history-index-v1", "histories": [
        {"manifest_path": original["manifest_path"], "manifest_sha256": original["manifest_sha256"], "role": "native", "adopted_as_current": False},
        {"manifest_path": corrected["manifest_path"], "manifest_sha256": corrected["manifest_sha256"], "role": "projection", "adopted_as_current": True}]}), encoding="utf-8")
    path = Path(__file__).parents[1] / "scripts/summarize_light_novel_classification_v1.py"
    spec = importlib.util.spec_from_file_location("series_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "audit.json"
    result = module.summarize(index, output)
    assert result["actual_unique_classification_calls"] == result["actual_unique_review_calls"] == 1
    assert result["content_feature_series"] == 1 and result["non_genre_content_feature_series"] == 0
    assert result["observed_input_plus_output_tokens"] == 4 and result["model_calls_during_audit"] == 0
    audit = json.loads(output.read_bytes())
    assert audit["histories"][1]["inherited_classification_calls"] == 1
    assert audit["histories"][1]["new_classification_calls"] == 0
    assert audit["raw_axes"]["style"]["unknown_series"] == 1
    assert audit["raw_axes"]["genre"]["known_series"] == 1
    assert not audit["independent_accuracy_evaluated"] and not audit["confidence_calibrated"]
