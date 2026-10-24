"""Run the immutable content recipe with the sealed gate-195/196 gap."""
from __future__ import annotations

import ast
import builtins
import json
import os
from pathlib import Path
import sys

from recommend_book import scale20k_native_v3 as state

ROOT = Path(__file__).resolve().parents[1]
FROZEN_COORDINATOR_SHA = "1682e4776d578202b877b607ac98217152881696e89ee5b467433dae1635de00"
RECOVERY_ENTRY_ENV = "SCALE20K_RECOVERY195_ENTRY"
RECOVERY_ENTRY_SHA_ENV = "SCALE20K_RECOVERY195_ENTRY_SHA256"


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_recovery():
    from recommend_book import scale20k_recovery195_v2
    return scale20k_recovery195_v2


def _entry_arguments() -> tuple[Path, str]:
    raw_path = os.environ.get(RECOVERY_ENTRY_ENV)
    raw_sha = os.environ.get(RECOVERY_ENTRY_SHA_ENV)
    if not raw_path or not raw_sha:
        raise ValueError("Gate-195/196 recovery entry and SHA256 are required")
    path = Path(raw_path).resolve(strict=True)
    if len(raw_sha) != 64 or any(c not in "0123456789abcdef" for c in raw_sha):
        raise ValueError("Gate-195/196 recovery entry SHA256 must be lower-case hexadecimal")
    return path, raw_sha


def _verify_resume_route(route_index: int, prior_control: dict,
                         prior_control_path: Path | str | None,
                         prior_control_sha: str | None) -> bool:
    """Preserve ordinary adjacency and delegate the one authenticated gap."""
    if route_index != 197:
        return prior_control["window_index"] == route_index - 1
    if prior_control_path is None or prior_control_sha is None:
        return False
    entry_path, entry_sha = _entry_arguments()
    recovery = _load_recovery()
    recovery.verify_resume_route(route_index, prior_control_path, prior_control_sha,
                                 entry_path, entry_sha)
    return True


def _adapt_recipe_tree(tree: ast.AST) -> ast.AST:
    """Adapt exactly the generated recipe's continuation comparison."""
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == "execute_window"]
    if len(functions) != 1:
        raise ValueError("Exact coordinator execute_window boundary differs")
    function = functions[0]
    replacements = 0
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.NotEq) and len(test.comparators) == 1):
            continue
        left = test.left
        right = test.comparators[0]
        is_prior_window = (
            isinstance(left, ast.Subscript)
            and isinstance(left.value, ast.Name) and left.value.id == "prior_control"
            and isinstance(left.slice, ast.Constant) and left.slice.value == "window_index"
        )
        is_previous_route = (
            isinstance(right, ast.BinOp) and isinstance(right.op, ast.Sub)
            and isinstance(right.left, ast.Name) and right.left.id == "route_index"
            and isinstance(right.right, ast.Constant) and right.right.value == 1
        )
        has_exact_error = any(
            isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and isinstance(child.exc.func, ast.Name) and child.exc.func.id == "ValueError"
            and child.exc.args and isinstance(child.exc.args[0], ast.Constant)
            and child.exc.args[0].value == "Content continuation window order differs"
            for child in node.body
        )
        if is_prior_window and is_previous_route and has_exact_error:
            node.test = ast.Call(
                func=ast.Name(id="_verify_resume_route", ctx=ast.Load()),
                args=[ast.Name(id="route_index", ctx=ast.Load()),
                      ast.Name(id="prior_control", ctx=ast.Load()),
                      ast.Name(id="control_receipt", ctx=ast.Load()),
                      ast.Name(id="control_receipt_sha256", ctx=ast.Load())],
                keywords=[],
            )
            # The original branch raises on a false result.  The adapter
            # therefore keeps the recipe's error and every surrounding check.
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            replacements += 1
    if replacements != 1:
        raise ValueError("Exact coordinator continuation guard boundary differs")
    return ast.fix_missing_locations(tree)


def _load_recipe_with_gap_adapter():
    if _sha(ROOT / "scripts/run_scale20k_content_classification_v3.py") != FROZEN_COORDINATOR_SHA:
        raise ValueError("Frozen coordinator recipe bytes changed")
    scripts = Path(__file__).resolve().parent
    original_path = list(sys.path)
    missing = object()
    aliases = {name: sys.modules.get(name, missing) for name in
               ("prepare_scale20k_content_chunk_v1", "recommend_book.scale20k_native_v1")}
    target = str(Path(__file__).resolve())
    original_compile = builtins.compile
    owner = state._v1
    previous_compile = owner.__dict__.get("compile", missing)

    def compile_adapter(source, filename, mode, *args, **kwargs):
        if isinstance(source, ast.AST) and str(Path(filename).resolve()) == target:
            source = _adapt_recipe_tree(source)
        return original_compile(source, filename, mode, *args, **kwargs)

    try:
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import prepare_scale20k_content_chunk_v3 as _chunk_v3
        sys.modules["prepare_scale20k_content_chunk_v1"] = _chunk_v3
        sys.modules["recommend_book.scale20k_native_v1"] = state
        # Scope the compile hook to this recipe owner's globals, not Python's
        # process-wide compiler or another module's imports.
        owner.compile = compile_adapter
        recipe = state.load_recipe("coordinator", __file__)
    finally:
        if previous_compile is missing:
            owner.__dict__.pop("compile", None)
        else:
            owner.compile = previous_compile
        sys.path[:] = original_path
        for name, old in aliases.items():
            if old is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    recipe.execute_window.__globals__["_verify_resume_route"] = _verify_resume_route
    return recipe


recipe = _load_recipe_with_gap_adapter()
_execute_window = recipe.execute_window


def execute_window(source_preparation, runtime_preparation, route_index, **kwargs):
    from run_scale20k_native_v4 import observed_command

    name = state.read(Path(source_preparation) / "manifest.json")["chunks"][0]["run_name"]
    if name != state.canonical_run_name(route_index):
        raise ValueError("Exact recovery v4 window name required before observations")
    stage_root = state.BASE / "operational-processes" / name / "frozen-stages"
    context = stage_root.parent / "new-code-context.json"
    snapshot = state.code_snapshot()
    state.save(context, {"artifact_sha256": snapshot, "coordinator_sha256": state.sha(__file__),
                         "model_started": False, "source_producer_or_ui_code_included": False,
                         "generation": state.RECOVERY_GENERATION})
    sequence = 0

    def frozen_call(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        output = stage_root / f"{sequence:02d}"
        env = dict(os.environ, PYTHONPATH=str(Path(runtime) / "runtime/src"),
                   PYTHONDONTWRITEBYTECODE="1")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_BASE_API"):
            env.pop(key, None)
        code = "from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); " + code
        observed_command([sys.executable, "-c", code, *map(str, arguments)], output,
                         env=env, timeout=1800)
        return json.loads((output / "stdout.txt").read_bytes())

    recipe.frozen_call = frozen_call

    def stage_process_pins():
        state.verify_code_snapshot(snapshot)
        entry_path, entry_sha = _entry_arguments()
        recovery = _load_recovery()
        recovery.verify_entry(entry_path, entry_sha)
        helper_path = Path(recovery.__file__).resolve()
        return [context, Path(__file__).resolve(), helper_path, entry_path,
                *sorted(path for path in stage_root.glob("*/*") if path.is_file())]

    recipe.stage_process_pins = stage_process_pins
    return _execute_window(source_preparation, runtime_preparation, route_index, **kwargs)


if __name__ == "__main__":
    recipe.execute_window = execute_window
    recipe.main()
