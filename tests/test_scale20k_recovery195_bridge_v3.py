import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from recommend_book import scale20k_recovery195_v3 as proof
from recommend_book import scale_content_control_v4 as control

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge195_v3_test", ROOT / "scripts/run_scale20k_recovery195_v3.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_real_controller_and_child_command_target_fresh198(monkeypatch, tmp_path):
    bridge = module.configured_bridge()
    p = tmp_path / "prior.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(bridge, "_verify_bridge_entry", lambda *args, **kwargs: {
        "first_index": 198, "unstarted_indices": [196, 197],
        "prior_control": {"path": str(p), "sha256": "a" * 64}})
    c = bridge._load_driver()._load_frozen_controller()
    bridge._patch_run(c, tmp_path, "b" * 64)
    assert c.first_resume_index({"window_index": 194}, p, "a" * 64) == 198
    with pytest.raises(ValueError):
        c.first_resume_index({"window_index": 197}, p, "a" * 64)
    calls = []
    fake = SimpleNamespace(observed_command=lambda argv, *args, **kwargs: calls.append(argv), run_window=lambda **kwargs: None)
    bridge._install_guards(fake, tmp_path, "b" * 64)
    fake.observed_command(["python", "scripts/run_scale20k_content_classification_v1.py"], tmp_path)
    assert calls[0][1] == "scripts/run_scale20k_content_classification_v5.py"


def test_resume_route_rejects_both_unstarted_windows(monkeypatch, tmp_path):
    p = tmp_path / "prior.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(proof, "verify_entry", lambda *args, **kwargs: {"prior_control": {"path": str(p), "sha256": "a" * 64}})
    monkeypatch.setattr(control, "verify_control_receipt", lambda *args: {"window_index": 194})
    proof.verify_resume_route(198, p, "a" * 64, p, "b" * 64)
    for route in (195, 196, 197, 199):
        with pytest.raises(ValueError):
            proof.verify_resume_route(route, p, "a" * 64, p, "b" * 64)
    monkeypatch.setattr(control, "verify_control_receipt", lambda *args: {"window_index": 198})
    proof.verify_resume_route(199, p, "a" * 64, p, "b" * 64)
