"""Resume the sealed content route after the failed 195 and held 196/197 windows."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FROZEN_COORDINATOR = ROOT / "scripts/run_scale20k_content_classification_v4.py"
FROZEN_COORDINATOR_SHA = "53c90957898b15462ece5c2c99b60897818f0b4393f23b1fe4ae35bdc70f8244"
RECOVERY_ENTRY_ENV = "SCALE20K_RECOVERY195_ENTRY"
RECOVERY_ENTRY_SHA_ENV = "SCALE20K_RECOVERY195_ENTRY_SHA256"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_recovery():
    from recommend_book import scale20k_recovery195_v3
    return scale20k_recovery195_v3


def _entry_arguments() -> tuple[Path, str]:
    raw_path = os.environ.get(RECOVERY_ENTRY_ENV)
    raw_sha = os.environ.get(RECOVERY_ENTRY_SHA_ENV)
    if not raw_path or not raw_sha:
        raise ValueError("Gate-195/196/197 recovery entry and SHA256 are required")
    path = Path(raw_path).resolve(strict=True)
    if len(raw_sha) != 64 or any(c not in "0123456789abcdef" for c in raw_sha):
        raise ValueError("Recovery entry SHA256 must be lower-case hexadecimal")
    return path, raw_sha


def _verify_resume_route(route_index: int, prior_control: dict,
                         prior_control_path: Path | str | None,
                         prior_control_sha: str | None) -> bool:
    if route_index != 198:
        return prior_control["window_index"] == route_index - 1
    if prior_control_path is None or prior_control_sha is None:
        return False
    entry_path, entry_sha = _entry_arguments()
    _load_recovery().verify_resume_route(route_index, prior_control_path,
                                         prior_control_sha, entry_path, entry_sha)
    return True


def _load_frozen_coordinator():
    if _sha(FROZEN_COORDINATOR) != FROZEN_COORDINATOR_SHA:
        raise ValueError("Frozen coordinator v4 bytes changed")
    spec = importlib.util.spec_from_file_location("scale20k_content_coordinator_v4_frozen",
                                                  FROZEN_COORDINATOR)
    if spec is None or spec.loader is None:
        raise ImportError("Frozen coordinator v4 cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if _sha(FROZEN_COORDINATOR) != FROZEN_COORDINATOR_SHA:
        raise ValueError("Frozen coordinator v4 changed during import")
    # The generated recipe hashes __file__ at export time.  Rebind its owner
    # to this new, independently pinned entry point without touching v4.
    owner = str(Path(__file__).resolve())
    module.__file__ = owner
    module.recipe.__file__ = owner
    module.recipe.execute_window.__globals__["__file__"] = owner
    module._execute_window.__globals__["__file__"] = owner
    module._verify_resume_route = _verify_resume_route
    module._execute_window.__globals__["_verify_resume_route"] = _verify_resume_route
    return module


@contextmanager
def runtime_import_aliases(chunk3=None):
    """Make late imports resolve to v3 for one real recipe execution only."""
    import recommend_book

    old_sys_path = list(sys.path)
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    missing = object()
    old_modules = {
        "recommend_book.scale20k_native_v1": sys.modules.get(
            "recommend_book.scale20k_native_v1", missing),
        "prepare_scale20k_content_chunk_v1": sys.modules.get(
            "prepare_scale20k_content_chunk_v1", missing),
    }
    old_package = getattr(recommend_book, "scale20k_native_v1", missing)
    try:
        if chunk3 is None:
            import prepare_scale20k_content_chunk_v3 as chunk3
        state3 = sys.modules.get("recommend_book.scale20k_native_v3")
        if state3 is None:
            from recommend_book import scale20k_native_v3 as state3
        sys.modules["recommend_book.scale20k_native_v1"] = state3
        sys.modules["prepare_scale20k_content_chunk_v1"] = chunk3
        setattr(recommend_book, "scale20k_native_v1", state3)
        yield
    finally:
        for name, old in old_modules.items():
            if old is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        if old_package is missing:
            try:
                delattr(recommend_book, "scale20k_native_v1")
            except AttributeError:
                pass
        else:
            setattr(recommend_book, "scale20k_native_v1", old_package)
        sys.path[:] = old_sys_path


_runtime_bindings = runtime_import_aliases


def _execute_with_runtime_bindings(frozen, *args, **kwargs):
    """Call v4 while preserving its process-local module isolation contract."""
    with _runtime_bindings():
        return frozen(*args, **kwargs)


_frozen = _load_frozen_coordinator()
_execute_window = _frozen._execute_window
recipe = _frozen.recipe


def execute_window(source_preparation, runtime_preparation, route_index, **kwargs):
    entry_path, entry_sha = _entry_arguments()
    recovery = _load_recovery()
    # v4's wrapper uses these globals while constructing its stage pins.  The
    # v5 entry must be the authority for the route and the exported proof.
    old_loader = _frozen._load_recovery
    old_underlying = _frozen._execute_window
    old_snapshot = _frozen.state.code_snapshot
    old_recipe_execute = _frozen.recipe.execute_window

    def snapshot_with_v5():
        snapshot = dict(old_snapshot())
        for path in (FROZEN_COORDINATOR, Path(__file__).resolve(), Path(recovery.__file__).resolve(), entry_path):
            snapshot[str(path)] = _sha(path)
        return snapshot

    def underlying_with_v5_pins(*args, **inner_kwargs):
        base_pins = _frozen.recipe.stage_process_pins

        def stage_process_pins():
            pins = list(base_pins())
            for path in (FROZEN_COORDINATOR, Path(__file__).resolve(), Path(recovery.__file__).resolve(), entry_path):
                path = path.resolve()
                if path not in pins:
                    pins.append(path)
            return pins

        _frozen.recipe.stage_process_pins = stage_process_pins
        try:
            return old_underlying(*args, **inner_kwargs)
        finally:
            _frozen.recipe.stage_process_pins = base_pins

    _frozen._load_recovery = _load_recovery
    _frozen.state.code_snapshot = snapshot_with_v5
    _frozen._execute_window = underlying_with_v5_pins
    try:
        with _runtime_bindings():
            return _frozen.execute_window(source_preparation, runtime_preparation,
                                          route_index, **kwargs)
    finally:
        _frozen._load_recovery = old_loader
        _frozen.state.code_snapshot = old_snapshot
        _frozen._execute_window = old_underlying
        _frozen.recipe.execute_window = old_recipe_execute


if __name__ == "__main__":
    _frozen.recipe.execute_window = execute_window
    _frozen.recipe.main()
