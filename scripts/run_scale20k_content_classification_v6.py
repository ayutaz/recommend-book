"""Complete immutable content recipe with the fresh final state and CLI context."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from recommend_book import scale20k_final_native_v1 as state

ROOT = Path(__file__).resolve().parents[1]

@contextmanager
def final_aliases():
    import recommend_book
    missing = object()
    names = ("recommend_book.scale20k_native_v1", "prepare_scale20k_content_chunk_v1")
    old = {name: sys.modules.get(name, missing) for name in names}
    old_attr = getattr(recommend_book, "scale20k_native_v1", missing)
    old_path = list(sys.path)
    try:
        if str(ROOT / "scripts") not in sys.path: sys.path.insert(0, str(ROOT / "scripts"))
        import prepare_scale_content_chunk_v5 as chunk
        sys.modules[names[0]], sys.modules[names[1]] = state, chunk
        setattr(recommend_book, "scale20k_native_v1", state)
        yield
    finally:
        for name, value in old.items():
            if value is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = value
        if old_attr is missing:
            if hasattr(recommend_book, "scale20k_native_v1"): delattr(recommend_book, "scale20k_native_v1")
        else: setattr(recommend_book, "scale20k_native_v1", old_attr)
        sys.path[:] = old_path

with final_aliases():
    recipe = state.load_recipe("coordinator", __file__)
_execute_window = recipe.execute_window

def execute_window(source_preparation, runtime_preparation, route_index, **kwargs):
    from run_scale20k_native_v4 import observed_command
    from run_scale20k_final_continuation_v2 import verify_chain, ENTRY, ENTRY_SHA256
    verify_chain()
    manifest = state.read(Path(source_preparation) / "manifest.json")
    name = manifest["chunks"][0]["run_name"]
    if route_index != 212 or name != state.canonical_run_name(212):
        raise ValueError("Final coordinator requires the exact fresh212 source")
    stage_root = state.BASE / "operational-processes" / name / "frozen-stages"
    context = stage_root.parent / "new-code-context.json"
    snapshot = state.code_snapshot()
    state.save(context, {"artifact_sha256": snapshot, "coordinator_sha256": state.sha(__file__),
                         "model_started": False, "generation": state.RECOVERY_GENERATION})
    sequence = 0
    def frozen_call(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        output = stage_root / f"{sequence:02d}"
        env = dict(os.environ, PYTHONPATH=str(Path(runtime) / "runtime/src"), PYTHONDONTWRITEBYTECODE="1")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_BASE_API"):
            env.pop(key, None)
        code = "from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); " + code
        observed_command([sys.executable, "-c", code, *map(str, arguments)], output, env=env, timeout=1800)
        return json.loads((output / "stdout.txt").read_bytes())
    def stage_process_pins():
        state.verify_code_snapshot(snapshot)
        verify_chain()
        return [context, Path(__file__).resolve(), ENTRY,
                *sorted(p for p in stage_root.glob("*/*") if p.is_file())]
    recipe.frozen_call, recipe.stage_process_pins = frozen_call, stage_process_pins
    with final_aliases():
        result = _execute_window(source_preparation, runtime_preparation, route_index, **kwargs)
    if isinstance(result, dict) and result.get("stage") == "actual_run_failed":
        raise ValueError("Actual final native process failed; preserve without retry")
    return result

if __name__ == "__main__":
    recipe.execute_window = execute_window
    recipe.main()
