"""Prepare a new CLI context inside the immutable runtime interpreter.

Only standard-library imports occur before loading the pinned complete
one-work recipe.  Parent-only orchestration state is intentionally not
imported by this child, whose package path is the frozen runtime.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/prepare_scale100k_content_chunk_v2.py"
PARENT_SHA = "d7805efa0598830be77a228dbfb76ce63ae12d1fcadf037d2bb42ac50643b9b0"


def frozen_prepare(preparation, inputs, ledger, name, concurrency):
    from recommend_book.scale_guard_context_v1 import enable_process_cache
    enable_process_cache()
    # The dependency is already independently pinned by the immutable state
    # recipe and is rechecked here without importing parent-only state.
    actual = hashlib.sha256(PARENT.read_bytes()).hexdigest()
    if actual != PARENT_SHA:
        raise ValueError("Immutable one-work preparer changed")
    spec = importlib.util.spec_from_file_location("_final_child_recipe", PARENT)
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    return recipe.load_bounded_preparer().prepare_chunk(
        preparation, inputs, ledger, cli_version="codex-cli 0.164.0",
        run_name=name, concurrency=concurrency)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("runtime-preparation", "inputs", "usage-ledger"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--concurrency", type=int, choices=(4, 8), required=True)
    args = parser.parse_args()
    print(json.dumps(frozen_prepare(args.runtime_preparation, args.inputs,
        args.usage_ledger, args.run_name, args.concurrency)))
