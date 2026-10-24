"""Current CLI context using the complete immutable one-work preparer."""
from __future__ import annotations
import ast
import argparse
import builtins
from pathlib import Path
from recommend_book import scale20k_native_v3 as state

ROOT = Path(__file__).resolve().parents[1]
CLI_VERSION = "codex-cli 0.164.0"

def load_recipe():
    owner = state._v1
    missing = object()
    old = owner.__dict__.get("compile", missing)
    def scoped_compile(source, filename, mode, *args, **kwargs):
        if isinstance(source, ast.AST) and str(Path(filename).resolve()) == str(Path(__file__).resolve()):
            counts = [0, 0]
            for node in ast.walk(source):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value == "codex-cli 0.163.0":
                        node.value = CLI_VERSION; counts[0] += 1
                    elif "from prepare_scale20k_content_chunk_v1 import frozen_prepare;" in node.value:
                        node.value = node.value.replace("from prepare_scale20k_content_chunk_v1 import frozen_prepare;", "from prepare_scale_content_chunk_v5 import frozen_prepare;")
                        counts[1] += 1
            if counts != [1, 1]:
                raise ValueError("Frozen CLI/context adapter boundaries differ: " + str(counts))
        return builtins.compile(source, filename, mode, *args, **kwargs)
    owner.compile = scoped_compile
    try:
        return state.load_recipe("preparer", __file__)
    finally:
        if old is missing: owner.__dict__.pop("compile", None)
        else: owner.compile = old

recipe = load_recipe()
frozen_prepare, prepare, trial_control = recipe.frozen_prepare, recipe.prepare, recipe.trial_control

if __name__ == "__main__":
    recipe.main()
