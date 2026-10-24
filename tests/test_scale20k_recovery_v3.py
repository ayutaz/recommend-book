from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))


@pytest.fixture(autouse=True)
def restore_process_state_alias(monkeypatch):
    from recommend_book import scale20k_native_v1

    monkeypatch.setitem(sys.modules, 'recommend_book.scale20k_native_v1', scale20k_native_v1)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v3_canonical_lineage_and_hold_witnesses(tmp_path):
    from recommend_book import scale20k_native_v3 as state
    from recommend_book import scale20k_recovery_v2 as recovery

    assert state.canonical_run_name(161) == 'scale20k-content-6801-6900-v3'
    assert state.canonical_run_name(162) == 'scale20k-content-6901-7000-v3'
    def pin(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return {'path': str(path), 'sha256': recovery.sha(path)}

    processes = {}
    for pid, name in enumerate(('preflight', 'controller_worker', 'controller_observer', 'sequential_queue'), 1):
        identity = {'pid': pid, 'process_created_at': '2026-10-23T00:00:00+00:00'}
        processes[name] = {
            'start': pin(name + '-start.json', identity),
            'exit': pin(name + '-exit.json', {**identity, 'actual_exit_code': 1,
                                             'process_exit_observed': True}),
        }
    hold = {'processes': processes, 'preflight_request': pin('request.json', {'model_calls': 0})}
    witnesses = recovery._verify_hold_processes(hold)
    assert set(witnesses) == {'preflight', 'controller_worker', 'controller_observer',
                              'sequential_queue', 'preflight_request'}


def test_v4_controller_exposes_recovery_policy_verifier_and_policy4():
    driver = _load('scale20k_native_v4_fixture', ROOT / 'scripts/run_scale20k_native_v4.py')
    controller = driver._load_frozen_controller()
    assert hasattr(controller, 'verify_policy_control')
    assert controller.state.canonical_run_name(161) == 'scale20k-content-6801-6900-v3'
    constants = controller.run_window.__code__.co_consts
    assert 'native-recovery-policy-v4.json' in constants
    assert 'scale20k-source-recovery-eligibility-v4' in constants


def test_v3_parent_recipe_keeps_fixed_runtime_chunk1_child():
    preparer = _load('scale20k_chunk_v3_fixture', ROOT / 'scripts/prepare_scale20k_content_chunk_v3.py')
    constants = preparer.prepare.__code__.co_consts
    literal = next(value for value in constants if isinstance(value, str) and 'frozen_prepare' in value)
    assert 'from prepare_scale20k_content_chunk_v1 import frozen_prepare;' in literal
    assert 'scale20k_native_v3' not in literal


def test_fixed_runtime_child_import_has_no_new_state_dependency(tmp_path):
    """The emitted child import works with a regular package plus frozen v1 path."""
    package = tmp_path / 'recommend_book'
    package.mkdir()
    (package / '__init__.py').write_text('', encoding='utf-8')
    # A regular package with no state helpers reproduces the failed boundary.
    # Import the actual immutable child, without adding the workspace src path.
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join((str(tmp_path), str(ROOT / 'scripts')))
    result = subprocess.run(
        [sys.executable, '-c',
         'from prepare_scale20k_content_chunk_v1 import frozen_prepare; print(frozen_prepare.__name__)'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'frozen_prepare'
