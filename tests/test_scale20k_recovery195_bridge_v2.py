import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge195_v2_test", ROOT / "scripts/run_scale20k_recovery195_v2.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_actual_frozen_controller_uses197_and_new_coordinator(monkeypatch, tmp_path):
    bridge = module.configured_bridge()
    prior = tmp_path / "prior.json"
    prior.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bridge, "_verify_bridge_entry", lambda *args, **kwargs: {
        "first_index": 197, "failed_index": 195, "unstarted_index": 196,
        "prior_control": {"path": str(prior), "sha256": "a" * 64}})
    controller = bridge._load_driver()._load_frozen_controller()
    bridge._patch_run(controller, tmp_path, "b" * 64)
    assert controller.first_resume_index({"window_index": 194}, prior, "a" * 64) == 197
    with pytest.raises(ValueError):
        controller.first_resume_index({"window_index": 195}, prior, "a" * 64)
    calls = []
    fake = SimpleNamespace(observed_command=lambda argv, *args, **kwargs: calls.append(argv),
                           run_window=lambda **kwargs: None)
    bridge._install_guards(fake, tmp_path, "b" * 64)
    fake.observed_command(["python", "scripts/run_scale20k_content_classification_v1.py"], tmp_path)
    assert calls[0][1] == "scripts/run_scale20k_content_classification_v4.py"
