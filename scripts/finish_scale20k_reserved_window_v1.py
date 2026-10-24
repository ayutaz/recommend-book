"""Finish a reserved window after its first successful native execution.

The prefix of the fixed controller (reservation, preparation and native
execution) is never repeated.  Only its unchanged export/import/summary tail
is executed, with the actual saved before-classification context restored.
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import inspect
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/run_scale20k_native_v1.py"
PARENT_SHA = "59ceada510c757c9bba223af8a4affdb4d6f6c767410261042940774ed9e59f7"


def build_tail(controller):
    if hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA:
        raise ValueError("Immutable controller changed")
    function = next(n for n in ast.parse(PARENT.read_bytes()).body
                    if isinstance(n, ast.FunctionDef) and n.name == "run_window")
    first = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "parent" for t in n.targets))
    # Keep the original verification import, then bind the already sealed
    # reservation and chunk without creating another reservation or preflight.
    setup = ast.parse("reservation = state.BASE / 'attempt-reservations' / (fixed['chunks'][0]['run_name'] + '.json')\nchunk = fixed['chunks'][0]\nname = chunk['run_name']").body
    function.body = [function.body[0], *setup, *function.body[first:]]
    function.name = "finish_reserved_window"
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])),
                 str(Path(__file__).resolve()), "exec"), controller.__dict__)
    return controller.finish_reserved_window


def main():
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_scale20k_final_continuation_v3 import load_adapter
    from recommend_book import scale20k_final_native_v1 as state
    adapter = load_adapter()
    controller = adapter.load_controller()
    name = state.canonical_run_name(212)
    reservation_path = state.BASE / "attempt-reservations" / (name + ".json")
    reservation = state.read(reservation_path)
    source = Path(reservation["source_path"])
    state.verify_reservation(source, 212)
    fixed = state.read(source / "manifest.json")
    pre_path = state.BASE / "native-eligibility" / name / "before-classification.json"
    pre = state.read(pre_path)
    actual = controller.run_window.__globals__["catalogue_snapshot"](state.DATABASE, pre["catalogue"])
    if actual != pre["catalogue"] or state.eligible_count() != 19993:
        raise ValueError("Selected catalogue changed since the saved reservation")
    closure = inspect.getclosurevars(controller.run_window).nonlocals
    closure["contexts"][name] = {
        "before_classification": pre["catalogue"], "concurrency": pre["requested_concurrency"],
        "first_unknown_probe": any(not row["genres"] for row in pre["catalogue"].values()),
        "pre_path": pre_path, "source_policy": source / state.FINAL_POLICY_NAME}
    prior = state.BASE / "window-controls/scale20k-content-11801-11900-v3.json"
    finish = build_tail(controller)
    finish(source=source, source_sha=reservation["source_sha256"], global_index=212,
           fixed=fixed, inventory_path=Path(reservation["inventory_path"]),
           inventory_sha=reservation["inventory_sha256"], previous_control=prior,
           previous_sha=state.sha(prior), concurrency=pre["requested_concurrency"],
           publication=controller.load_publication(), source_index=Path(reservation["source_index_path"]),
           source_index_sha=reservation["source_index_sha256"])


if __name__ == "__main__":
    main()
