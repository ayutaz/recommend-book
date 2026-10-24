from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gate195_bridge_test", ROOT / "scripts/run_scale20k_recovery195_v1.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def fake_recovery(monkeypatch, prior_path=None):
    prior_path = (prior_path or Path("prior.json").resolve()).resolve()
    entry = {
        "last_passed_index": 194,
        "failed_index": 195,
        "first_index": 196,
        "code_pins": {
            "scripts/run_scale20k_recovery195_v1.py": bridge._sha(Path(bridge.__file__).resolve())
        },
        "pins": {
            "prior_control": {"path": str(prior_path), "sha256": "a" * 64}
        },
    }

    class Recovery:
        @staticmethod
        def verify_entry(path, expected_sha, *, before_launch):
            assert Path(path).is_absolute()
            assert expected_sha == "b" * 64
            assert before_launch in (True, False)
            return entry

    monkeypatch.setattr(bridge, "_load_recovery", lambda: Recovery)
    return entry


def test_patch_run_accepts_only_authenticated_194_to_196_bridge(monkeypatch, tmp_path):
    prior_path = (tmp_path / "prior.json").resolve()
    prior_path.write_text("{}", encoding="utf-8")
    fake_recovery(monkeypatch, prior_path)
    controller = SimpleNamespace(DRIVER_V1_SHA=bridge._sha(bridge.FROZEN_RUN))
    bridge._patch_run(controller, Path("entry.json").resolve(), "b" * 64)

    prior = {"window_index": 194}
    assert controller.first_resume_index(prior, prior_path, "a" * 64) == 196
    with pytest.raises(ValueError, match="prior control index"):
        controller.first_resume_index({"window_index": 195}, prior_path, "a" * 64)
    other_path = tmp_path / "other.json"
    other_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="prior control pin"):
        controller.first_resume_index(prior, other_path, "a" * 64)


def test_observed_worker_is_rebound_to_bridge_and_pins_entry(monkeypatch, tmp_path):
    fake_recovery(monkeypatch)
    calls = []

    def observed(argv, directory, *args, **kwargs):
        calls.append((list(argv), kwargs))
        return {"actual_exit_code": 0}

    controller = SimpleNamespace(observed_command=observed, run_window=lambda **kwargs: "ok")
    bridge._install_guards(controller, Path("entry.json").resolve(), "b" * 64)
    result = controller.observed_command(
        [sys.executable, bridge.DRIVER, "--worker"], tmp_path / "child",
        request_fields={"existing": True})
    assert result["actual_exit_code"] == 0
    argv, kwargs = calls[0]
    assert str(bridge.DRIVER.resolve()) not in argv
    assert str(Path(bridge.__file__).resolve()) in argv
    assert kwargs["request_fields"]["existing"] is True
    assert kwargs["request_fields"]["recovery195_entry_sha256"] == "b" * 64
    assert kwargs["request_fields"]["recovery195_bridge_sha256"] == bridge._sha(Path(bridge.__file__).resolve())

    assert controller.run_window() == "ok"


def test_bridge_patches_actual_frozen_controller_without_model_or_database(monkeypatch, tmp_path):
    prior = tmp_path / "prior.json"
    prior.write_text("{}", encoding="utf-8")
    fake_recovery(monkeypatch, prior)
    controller = bridge._load_driver()._load_frozen_controller()
    # The real expanded controller does not export the v2 adapter's hash constant.
    assert not hasattr(controller, "DRIVER_V1_SHA")
    bridge._patch_run(controller, (tmp_path / "entry.json").resolve(), "b" * 64)
    assert controller.first_resume_index({"window_index": 194}, prior, "a" * 64) == 196
