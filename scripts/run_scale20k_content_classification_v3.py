"""Recovery v3 coordinator using the immutable classification recipe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from recommend_book import scale20k_native_v3 as state

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_scale20k_content_chunk_v3 as _chunk_v3

sys.modules['prepare_scale20k_content_chunk_v1'] = _chunk_v3
sys.modules['recommend_book.scale20k_native_v1'] = state
recipe = state.load_recipe('coordinator', __file__)
_execute_window = recipe.execute_window


def execute_window(source_preparation, runtime_preparation, route_index, **kwargs):
    from run_scale20k_native_v4 import observed_command
    name = state.read(Path(source_preparation) / 'manifest.json')['chunks'][0]['run_name']
    if name != state.canonical_run_name(route_index):
        raise ValueError('Exact recovery v3 window name required before observations')
    stage_root = state.BASE / 'operational-processes' / name / 'frozen-stages'
    context = stage_root.parent / 'new-code-context.json'
    snapshot = state.code_snapshot()
    state.save(context, {'artifact_sha256': snapshot, 'coordinator_sha256': state.sha(__file__),
                         'model_started': False, 'source_producer_or_ui_code_included': False,
                         'generation': state.RECOVERY_GENERATION})
    sequence = 0

    def frozen_call(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        output = stage_root / f'{sequence:02d}'
        env = dict(os.environ, PYTHONPATH=str(Path(runtime) / 'runtime/src'), PYTHONDONTWRITEBYTECODE='1')
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_BASE_API'):
            env.pop(key, None)
        code = 'from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); ' + code
        observed_command([sys.executable, '-c', code, *map(str, arguments)], output, env=env, timeout=1800)
        return json.loads((output / 'stdout.txt').read_bytes())

    recipe.frozen_call = frozen_call

    def stage_process_pins():
        state.verify_code_snapshot(snapshot)
        return [context, *sorted(path for path in stage_root.glob('*/*') if path.is_file())]

    recipe.stage_process_pins = stage_process_pins
    return _execute_window(source_preparation, runtime_preparation, route_index, **kwargs)


if __name__ == '__main__':
    recipe.execute_window = execute_window
    recipe.main()
