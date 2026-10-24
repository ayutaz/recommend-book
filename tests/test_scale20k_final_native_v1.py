import importlib
import json
from pathlib import Path

import pytest


def test_final_namespace_and_hold_gate(monkeypatch, tmp_path):
    state = importlib.import_module("recommend_book.scale20k_final_native_v1")
    assert state.canonical_run_name(212) == "scale20k-content-11901-12000-v4"
    with pytest.raises(ValueError, match="global-212"):
        state.canonical_run_name(211)
    hold = tmp_path / "hold.json"
    hold.write_bytes(b"not the pinned production hold")
    monkeypatch.setattr(state, "FINAL_HOLD", hold)
    with pytest.raises(ValueError, match="bytes differ"):
        state._assert_final_hold_is_excluded()


def test_final_source_index_requires_origin_214(tmp_path):
    state = importlib.import_module("recommend_book.scale20k_final_native_v1")
    path = tmp_path / "index.json"
    value = {"requirements_sha256": state.REQUIREMENTS_SHA, "windows": [
        {"window_index": 212, "run_name": state.canonical_run_name(212),
         "upstream_window_index": 214, "source_path": "fresh", "source_sha256": "sha"}
    ]}
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="bytes"):
        state.source_entry(path, "0" * 64, 212)
    digest = state.sha(path)
    assert state.source_entry(path, digest, 212)["upstream_window_index"] == 214
    value["windows"][0]["upstream_window_index"] = 213
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="origin214"):
        state.source_entry(path, state.sha(path), 212)
