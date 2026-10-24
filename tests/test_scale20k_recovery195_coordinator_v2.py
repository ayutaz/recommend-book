from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_scale20k_content_classification_v5.py"
spec = importlib.util.spec_from_file_location("recovery195_coordinator_v5_test", SCRIPT)
coordinator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(coordinator)


def test_actual_expanded_function_has_v5_owner_and_route198_guard():
    function = coordinator._execute_window
    assert coordinator.recipe.__file__ == str(SCRIPT.resolve())
    assert function.__globals__["__file__"] == str(SCRIPT.resolve())
    assert "_verify_resume_route" in function.__code__.co_names
    assert coordinator._verify_resume_route(199, {"window_index": 198}, None, None)
    assert not coordinator._verify_resume_route(199, {"window_index": 197}, None, None)


def test_late_import_uses_state3_during_call_and_restores_afterward():
    import recommend_book

    missing = object()
    old_module = sys.modules.get("recommend_book.scale20k_native_v1", missing)
    old_package = getattr(recommend_book, "scale20k_native_v1", missing)
    seen = []

    def fake_underlying():
        from recommend_book.scale20k_native_v1 import verify_reservation
        import prepare_scale20k_content_chunk_v1 as chunk
        seen.append((verify_reservation.__module__, chunk.__name__,
                     sys.modules["recommend_book.scale20k_native_v1"]))
        return "ok"

    assert coordinator._execute_with_runtime_bindings(fake_underlying) == "ok"
    assert seen == [("recommend_book.scale20k_native_v3",
                     "prepare_scale20k_content_chunk_v3",
                     sys.modules["recommend_book.scale20k_native_v3"])]
    assert sys.modules.get("recommend_book.scale20k_native_v1", missing) is old_module
    assert getattr(recommend_book, "scale20k_native_v1", missing) is old_package


def test_route198_delegates_only_to_sealed_v3_helper(monkeypatch, tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}", encoding="utf-8")
    calls = []

    class Recovery:
        @staticmethod
        def verify_resume_route(*args):
            calls.append(args)

    monkeypatch.setattr(coordinator, "_load_recovery", lambda: Recovery)
    monkeypatch.setenv(coordinator.RECOVERY_ENTRY_ENV, str(entry))
    monkeypatch.setenv(coordinator.RECOVERY_ENTRY_SHA_ENV, "a" * 64)
    prior = tmp_path / "control194.json"
    assert coordinator._verify_resume_route(198, {"window_index": 194}, prior, "b" * 64)
    assert calls == [(198, prior, "b" * 64, entry, "a" * 64)]


def test_route198_rejects_missing_entry(monkeypatch):
    monkeypatch.delenv(coordinator.RECOVERY_ENTRY_ENV, raising=False)
    monkeypatch.delenv(coordinator.RECOVERY_ENTRY_SHA_ENV, raising=False)
    with pytest.raises(ValueError, match="recovery entry"):
        coordinator._verify_resume_route(198, {"window_index": 194}, "control", "a" * 64)
