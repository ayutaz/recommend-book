import importlib.util
import inspect
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v1_defaults_remain_fixed_and_v2_adds_pinned_recipe_source(monkeypatch):
    v1 = load("review_world_pilot_v1")
    v2 = load("review_world_pilot_v2")
    signature = inspect.signature(v1.execute)
    assert signature.parameters["instructions"].default == v1.INSTRUCTIONS
    assert signature.parameters["protocol"].default == "complete-input-native-output-internal-review-v1"
    assert v2.INSTRUCTIONS.startswith(v1.INSTRUCTIONS)
    assert "content-addressed raw_path" in v2.INSTRUCTIONS

    captured = {}

    def fake_execute(args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(v2.v1, "execute", fake_execute)
    assert v2.execute(object()) == {"ok": True}
    assert captured["instructions"] == v2.INSTRUCTIONS
    assert captured["protocol"] == "complete-input-native-output-internal-review-v2"
    assert captured["extra_source_paths"] == (Path(v2.__file__).resolve(),)
