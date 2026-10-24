import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "final_continuation_v2_test", ROOT / "scripts/run_scale20k_final_continuation_v2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_driver_routes_only_new_namespace(monkeypatch):
    module = load()
    monkeypatch.setattr(module, "verify_chain", lambda: None)
    class Controller:
        def __init__(self):
            self.calls = []
            self.observed_command = self.observe
            self.run = self.original_run
        def observe(self, argv, directory, *args, **kwargs):
            self.calls.append((argv, kwargs))
            return 0
        def original_run(self, *args):
            return "ok"
    controller = module.configure_controller(Controller())
    controller.observed_command(["python", str(module.DRIVER)], "unused")
    assert Path(controller.calls[-1][0][1]).name == "run_scale20k_final_continuation_v2.py"
    controller.observed_command(["python", "scripts/run_scale20k_content_classification_v5.py"], "unused")
    assert Path(controller.calls[-1][0][1]).name == "run_scale20k_content_classification_v6.py"


def test_real_policy_controller_uses_the_fresh_builder_and_state():
    import inspect
    module = load()
    controller = module.load_controller()
    context = inspect.getclosurevars(controller.run_window).nonlocals
    stable = inspect.getclosurevars(context["stable_code"]).nonlocals
    assert stable["builder"].name == "prepare_scale20k_content_sources_v5.py"
    assert stable["controller_sha"] == module.sha(Path(module.__file__))
    assert context["state"].canonical_run_name(212) == "scale20k-content-11901-12000-v4"
