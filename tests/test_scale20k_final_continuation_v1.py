import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "_final_continuation_test", ROOT / "scripts/run_scale20k_final_continuation_v1.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_controller_keeps_adjacent_loop_and_routes_children():
    module = load()
    spec = importlib.util.spec_from_file_location("_final_driver_test", module.DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    controller = driver._load_frozen_controller()
    calls = []
    controller.observed_command = lambda argv, directory, **kwargs: calls.append((argv, kwargs))
    original_run = controller.run
    controller = module.configure_controller(controller)
    assert controller.run is not original_run
    controller.observed_command(["python", str(module.DRIVER), "--worker"], "unused")
    assert Path(calls[-1][0][1]).name == "run_scale20k_final_continuation_v1.py"
    controller.observed_command(["python", "scripts/run_scale20k_content_classification_v1.py"], "unused")
    assert calls[-1][0][1] == str(module.COORDINATOR)
    assert calls[-1][1]["request_fields"]["recovery195_entry_sha256"] == module.ENTRY_SHA
    # Python -c payloads are never treated as filesystem paths or rewritten.
    controller.observed_command(["python", "-c", "print('classification_v1.py')"], "unused")
    assert calls[-1][0][1:] == ["-c", "print('classification_v1.py')"]


def test_requires_passed211_before_original_run(monkeypatch):
    module = load()
    monkeypatch.setattr(module, "verify_chain", lambda: {})
    import recommend_book.scale_content_control_v4 as control
    monkeypatch.setattr(control, "verify_control_receipt", lambda *args: {
        "window_index": 210, "gate": {"passed": True}})
    controller = SimpleNamespace(observed_command=lambda *a, **k: None,
                                 run=lambda *a: pytest.fail("must reject first"))
    module.configure_controller(controller)
    with pytest.raises(ValueError, match="passed211"):
        controller.run(None, None, None, None, None, None)
