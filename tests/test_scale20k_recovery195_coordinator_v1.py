from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_scale20k_content_classification_v4.py"
spec = importlib.util.spec_from_file_location("recovery195_coordinator_v4_test", SCRIPT)
coordinator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(coordinator)


def test_real_expanded_coordinator_uses_new_owner_and_adapted_guard():
    function = coordinator._execute_window
    assert Path(function.__code__.co_filename).resolve() == SCRIPT.resolve()
    assert "_verify_resume_route" in function.__code__.co_names
    assert coordinator.recipe.__file__ == str(SCRIPT.resolve())


def test_normal_routes_keep_original_consecutive_rule():
    assert coordinator._verify_resume_route(198, {"window_index": 197}, "ignored", "ignored")
    assert not coordinator._verify_resume_route(198, {"window_index": 196}, "ignored", "ignored")


def test_route_197_delegates_the_authenticated_gap(monkeypatch, tmp_path):
    entry = tmp_path / "entry.json"
    entry.write_text("{}", encoding="utf-8")
    calls = []

    class Recovery:
        @staticmethod
        def verify_resume_route(route_index, prior_path, prior_sha, entry_path, entry_sha):
            calls.append((route_index, Path(prior_path), prior_sha, Path(entry_path), entry_sha))

    monkeypatch.setattr(coordinator, "_load_recovery", lambda: Recovery)
    monkeypatch.setenv(coordinator.RECOVERY_ENTRY_ENV, str(entry))
    monkeypatch.setenv(coordinator.RECOVERY_ENTRY_SHA_ENV, "a" * 64)

    assert coordinator._verify_resume_route(197, {"window_index": 194},
                                             tmp_path / "control194.json", "b" * 64)
    assert calls == [(197, tmp_path / "control194.json", "b" * 64, entry, "a" * 64)]


def test_route_197_requires_the_sealed_entry_before_continuation(monkeypatch):
    monkeypatch.delenv(coordinator.RECOVERY_ENTRY_ENV, raising=False)
    monkeypatch.delenv(coordinator.RECOVERY_ENTRY_SHA_ENV, raising=False)
    with pytest.raises(ValueError, match="recovery entry"):
        coordinator._verify_resume_route(197, {"window_index": 194}, "control", "a" * 64)
