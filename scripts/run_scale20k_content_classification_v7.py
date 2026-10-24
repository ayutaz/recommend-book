"""Execute the first model calls after a witnessed pre-model preparation repair."""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/run_scale20k_content_classification_v6.py"
PARENT_SHA = "41944a867bebb3a62736d852603e4682fa7ac80f59d1374b6202774bf6429f8e"
CHILD = ROOT / "scripts/prepare_scale_content_child_v1.py"
PROOF = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1/final-shortfall-v2/actual-child-preparation-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_adapter():
    if sha(PARENT) != PARENT_SHA:
        raise ValueError("Frozen final coordinator changed")
    spec = importlib.util.spec_from_file_location("_final_coordinator6_fixed", PARENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.__file__ = str(Path(__file__).resolve())
    module.recipe.__file__ = str(Path(__file__).resolve())
    original_snapshot = module.state.code_snapshot
    def snapshot():
        result = dict(original_snapshot())
        stopped = module.state.BASE / "operational-processes" / module.state.canonical_run_name(212) / "preflight"
        for path in (Path(__file__).resolve(), CHILD, *sorted(PROOF.iterdir()), *sorted(stopped.iterdir())):
            if path.is_file(): result[str(path.resolve())] = sha(path)
        return result
    module.state.code_snapshot = snapshot
    return module


if __name__ == "__main__":
    adapter = load_adapter()
    adapter.recipe.execute_window = adapter.execute_window
    adapter.recipe.main()
