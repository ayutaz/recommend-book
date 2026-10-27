"""CLI context for the fixed one-work recipe, importable inside old runtimes.

Only the orchestration state loader and child entry differ from the existing
CLI adapter. Model inputs, frozen runtime verification and admission stay fixed.
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path

from prepare_scale20k_content_chunk_v1 import load_state


def load_recipe():
    owner = load_state()
    original_compile = builtins.compile

    def compile_context(source, filename, mode, *args, **kwargs):
        if isinstance(source, ast.AST) and Path(filename).resolve() == Path(__file__).resolve():
            counts = [0, 0]
            for node in ast.walk(source):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if node.value == 'codex-cli 0.163.0':
                    node.value = 'codex-cli 0.164.0'
                    counts[0] += 1
                elif 'from prepare_scale20k_content_chunk_v1 import frozen_prepare;' in node.value:
                    node.value = node.value.replace(
                        'from prepare_scale20k_content_chunk_v1 import frozen_prepare;',
                        'from prepare_world_novel_chunk_v1 import frozen_prepare;')
                    counts[1] += 1
            if counts != [1, 1]:
                raise ValueError('Fixed world preparation context boundaries differ')
        return original_compile(source, filename, mode, *args, **kwargs)

    owner.compile = compile_context
    return owner.load_recipe('preparer', __file__)


recipe = load_recipe()
frozen_prepare, prepare, trial_control = recipe.frozen_prepare, recipe.prepare, recipe.trial_control


if __name__ == '__main__':
    recipe.main()
