"""Resume the v3 content controller across the authenticated gate-195 gap.

The v4 controller and all classification recipes stay frozen.  This entry
point changes one in-memory expression in the frozen controller's ``run``
function: the first index is obtained from the independently sealed
gate-195 entry (194 -> 196).  Once window 196 has completed, the original
controller loop advances normally.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts" / "run_scale20k_native_v4.py"
FROZEN_RUN = ROOT / "scripts" / "run_scale20k_native_v1.py"
DRIVER_SHA = "e34e14982f62df66e847dc8b4859cb95e4722ecfbd2880c7de4ad17ab61f77b5"
FROZEN_RUN_SHA = "59ceada510c757c9bba223af8a4affdb4d6f6c767410261042940774ed9e59f7"


def _load_driver():
    if _sha(DRIVER) != DRIVER_SHA:
        raise ValueError("Frozen v4 native controller bytes changed")
    spec = importlib.util.spec_from_file_location("scale20k_native_v4_bridge_driver", DRIVER)
    if spec is None or spec.loader is None:
        raise ImportError("v4 native controller cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if _sha(DRIVER) != DRIVER_SHA:
        raise ValueError("Frozen v4 native controller changed during import")
    return module


def _load_recovery():
    from recommend_book import scale20k_recovery195_v1
    return scale20k_recovery195_v1


def _pin_path(value: str | Path) -> Path:
    return Path(value).resolve(strict=True)


def _verify_bridge_entry(path: Path, expected_sha: str, *, before_launch: bool):
    if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise ValueError("Gate-195 entry SHA256 must be lower-case hexadecimal")
    recovery = _load_recovery()
    entry = recovery.verify_entry(path, expected_sha, before_launch=before_launch)
    if not isinstance(entry, dict):
        raise ValueError("Gate-195 entry verifier must return an object")
    code_pins = entry.get("code_pins")
    if not isinstance(code_pins, dict):
        raise ValueError("Gate-195 entry must pin bridge code")
    relative = str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/")
    if code_pins.get(relative) != _sha(Path(__file__)):
        raise ValueError("Gate-195 entry does not pin this bridge")
    return entry


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pin_from_entry(entry: dict, name: str) -> tuple[Path, str]:
    # Recovery195 keeps these authority pins at the entry root (the older
    # gate161 receipt uses a nested ``pins`` object).
    value = entry.get(name)
    if value is None:
        value = entry.get("pins", {}).get(name)
    if not isinstance(value, dict) or not isinstance(value.get("path"), str) or not isinstance(value.get("sha256"), str):
        raise ValueError(f"Gate-195 entry is missing pin: {name}")
    return _pin_path(value["path"]), value["sha256"]


def _patch_run(controller, entry_path: Path, entry_sha: str):
    """Install the sole authenticated initial-index bridge into ``controller``."""
    if not FROZEN_RUN.is_file() or _sha(FROZEN_RUN) != FROZEN_RUN_SHA:
        raise ValueError("Frozen v1 controller bytes changed")
    tree = ast.parse(FROZEN_RUN.read_bytes())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(functions) != 1:
        raise ValueError("Exact frozen run function boundary differs")
    function = functions[0]
    replacements = 0
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if not (isinstance(target, ast.Name) and target.id == "global_index"
                and isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add)
                and isinstance(value.left, ast.Subscript)
                and isinstance(value.left.value, ast.Name) and value.left.value.id == "prior"
                and isinstance(value.left.slice, ast.Constant) and value.left.slice.value == "window_index"
                and isinstance(value.right, ast.Constant) and value.right.value == 1):
            continue
        node.value = ast.Call(
            func=ast.Name(id="first_resume_index", ctx=ast.Load()),
            args=[ast.Name(id="prior", ctx=ast.Load()),
                  ast.Name(id="previous_control", ctx=ast.Load()),
                  ast.Name(id="previous_sha", ctx=ast.Load())],
            keywords=[])
        replacements += 1
    if replacements != 1:
        raise ValueError("Exact frozen initial global-index boundary differs")

    def first_resume_index(prior, previous_control, previous_sha):
        entry = _verify_bridge_entry(entry_path, entry_sha, before_launch=True)
        if prior.get("window_index") != entry.get("last_passed_index"):
            raise ValueError("Gate-195 bridge prior control index differs")
        prior_pin, prior_pin_sha = _pin_from_entry(entry, "prior_control")
        if _pin_path(previous_control) != prior_pin or previous_sha != prior_pin_sha:
            raise ValueError("Gate-195 bridge prior control pin differs")
        if entry.get("failed_index") != entry.get("last_passed_index") + 1:
            raise ValueError("Gate-195 bridge failed index is not adjacent to prior control")
        if entry.get("first_index") != entry.get("failed_index") + 1:
            raise ValueError("Gate-195 bridge first index is not after the quarantined window")
        return entry["first_index"]

    controller.first_resume_index = first_resume_index
    compiled = ast.Module(body=[function], type_ignores=[])
    exec(compile(ast.fix_missing_locations(compiled), str(Path(__file__).resolve()), "exec"), controller.__dict__)
    return controller


def _install_guards(controller, entry_path: Path, entry_sha: str):
    """Pin the bridge in every worker request and check it before every window."""
    original_observed = controller.observed_command
    driver_path = DRIVER.resolve()
    bridge_path = Path(__file__).resolve()

    def observed_command(argv, directory, *args, **kwargs):
        rewritten = [str(bridge_path) if index == 1 and Path(item).resolve() == driver_path else item
                     for index, item in enumerate(map(str, argv))]
        fields = dict(kwargs.pop("request_fields", {}) or {})
        fields.update({
            "recovery195_entry_path": str(entry_path),
            "recovery195_entry_sha256": entry_sha,
            "recovery195_bridge_path": str(bridge_path),
            "recovery195_bridge_sha256": _sha(bridge_path),
        })
        kwargs["request_fields"] = fields
        return original_observed(rewritten, directory, *args, **kwargs)

    controller.observed_command = observed_command
    original_window = controller.run_window

    def run_window(**kwargs):
        _verify_bridge_entry(entry_path, entry_sha, before_launch=False)
        return original_window(**kwargs)

    controller.run_window = run_window
    return controller


def main() -> None:
    driver = _load_driver()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-entry", type=Path,
                        default=Path(os.environ["SCALE20K_RECOVERY195_ENTRY"])
                        if os.environ.get("SCALE20K_RECOVERY195_ENTRY") else None)
    parser.add_argument("--bridge-entry-sha256",
                        default=os.environ.get("SCALE20K_RECOVERY195_ENTRY_SHA256"))
    parser.add_argument("--recovery-entry", type=Path,
                        default=Path(os.environ["SCALE20K_RECOVERY_ENTRY"])
                        if os.environ.get("SCALE20K_RECOVERY_ENTRY") else None)
    parser.add_argument("--recovery-entry-sha256",
                        default=os.environ.get("SCALE20K_RECOVERY_ENTRY_SHA256"))
    parser.add_argument("--prior-control", type=Path, default=driver.state.PRIOR_CONTROL)
    parser.add_argument("--prior-control-sha256", default=driver.state.PRIOR_CONTROL_SHA)
    args, remaining = parser.parse_known_args()
    if not args.bridge_entry or not args.bridge_entry_sha256:
        parser.error("Gate-195 bridge entry and SHA256 are required")
    if not args.recovery_entry or not args.recovery_entry_sha256:
        parser.error("The original sealed recovery entry and SHA256 are required")
    args.bridge_entry = args.bridge_entry.resolve(strict=True)
    args.recovery_entry = args.recovery_entry.resolve(strict=True)
    # Authenticate the gap before constructing the worker/controller process.
    _verify_bridge_entry(args.bridge_entry, args.bridge_entry_sha256, before_launch=True)
    driver._verify_recovery_args(args)
    driver.state.ACTIVE_RECOVERY_ENTRY = (args.recovery_entry, args.recovery_entry_sha256)
    os.environ["SCALE20K_RECOVERY_ENTRY"] = str(args.recovery_entry)
    os.environ["SCALE20K_RECOVERY_ENTRY_SHA256"] = args.recovery_entry_sha256
    os.environ["SCALE20K_RECOVERY195_ENTRY"] = str(args.bridge_entry)
    os.environ["SCALE20K_RECOVERY195_ENTRY_SHA256"] = args.bridge_entry_sha256

    controller = _install_guards(_patch_run(driver._load_frozen_controller(), args.bridge_entry,
                                             args.bridge_entry_sha256), args.bridge_entry,
                                 args.bridge_entry_sha256)
    original_next = controller.next_concurrency
    first_call = True

    def recovery_slots(previous_run, *, first):
        nonlocal first_call
        force_first = first_call
        first_call = False
        return original_next(previous_run, first=(first or force_first))

    controller.next_concurrency = recovery_slots
    original_argv = sys.argv
    try:
        # Bridge/recovery flags remain environment-pinned and are intentionally
        # excluded from the frozen controller's argparse surface.
        sys.argv = [original_argv[0], *remaining,
                    "--prior-control", str(args.prior_control.resolve()),
                    "--prior-control-sha256", args.prior_control_sha256]
        controller.main()
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main()
