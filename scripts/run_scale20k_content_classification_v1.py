"""V20 namespace entry; unchanged native, five-work review and admission."""
import importlib.util
import json
import os
from pathlib import Path
import sys


def load_state():
    path = Path(__file__).resolve().parents[1] / 'src/recommend_book/scale20k_native_v1.py'
    spec = importlib.util.spec_from_file_location('scale20k_coordinator_state', path)
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    return state


state = load_state()
recipe = state.load_recipe('coordinator', __file__)
_execute_window = recipe.execute_window


def execute_window(source_preparation, runtime_preparation, route_index, **kwargs):
    from run_scale20k_native_v1 import observed_command
    name = state.read(Path(source_preparation) / 'manifest.json')['chunks'][0]['run_name']
    if name != state.canonical_run_name(route_index):
        raise ValueError('Exact v20 window name required before writing observations')
    stage_root = state.BASE / 'operational-processes' / name / 'frozen-stages'
    context = stage_root.parent / 'new-code-context.json'
    snapshot = state.code_snapshot()
    state.save(context, {'artifact_sha256': snapshot, 'coordinator_sha256': state.sha(__file__),
                         'model_started': False, 'source_producer_or_ui_code_included': False})
    sequence = 0

    def frozen_call(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        output = stage_root / f'{sequence:02d}'
        env = dict(os.environ, PYTHONPATH=str(Path(runtime) / 'runtime/src'), PYTHONDONTWRITEBYTECODE='1')
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
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
