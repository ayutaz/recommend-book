from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


@pytest.fixture(autouse=True)
def isolate_recovery_process_aliases():
    from recommend_book import scale20k_native_v2 as state

    aliases = ('recommend_book.scale20k_native_v1', 'prepare_scale20k_content_chunk_v1')
    modules = {name: sys.modules.get(name) for name in aliases}
    variables = ('SCALE20K_RECOVERY_ENTRY', 'SCALE20K_RECOVERY_ENTRY_SHA256')
    environment = {name: os.environ.get(name) for name in variables}
    active = state.ACTIVE_RECOVERY_ENTRY
    try:
        yield
    finally:
        state.ACTIVE_RECOVERY_ENTRY = active
        for name, original in modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
        for name, original in environment.items():
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original


def _load_driver():
    spec = importlib.util.spec_from_file_location('scale20k_native_v3_fixture', ROOT / 'scripts/run_scale20k_native_v3.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_expansion_binds_v2_state_and_canonical_names():
    from recommend_book import scale20k_native_v2 as state

    controller = _load_driver()._load_frozen_controller()
    assert state.canonical_run_name(161) == 'scale20k-content-6801-6900-v2'
    assert state.canonical_run_name(162) == 'scale20k-content-6901-7000-v2'
    assert controller.state.canonical_run_name(161) == state.canonical_run_name(161)
    assert controller.state.reserve_window.__globals__['__name__'] == 'recommend_book.scale20k_native_v2'
    assert controller.state.verify_reservation.__globals__['__name__'] == 'recommend_book.scale20k_native_v2'
    assert hasattr(controller, 'verify_policy_control')
    assert 'native-recovery-policy-v3.json' in controller.run_window.__code__.co_consts
    assert 'scale20k-source-recovery-eligibility-v3' in controller.run_window.__code__.co_consts


def test_driver_forwards_explicit_parent_to_frozen_worker(monkeypatch, tmp_path):
    driver = _load_driver()
    captured = {}

    class FakeController:
        next_concurrency = staticmethod(lambda previous_run, *, first: 4)

        @staticmethod
        def main():
            captured['argv'] = list(sys.argv)

    entry = tmp_path / 'entry.json'
    entry.write_text('{}', encoding='utf-8')
    parent = tmp_path / 'passed-v2.json'
    parent_sha = 'a' * 64
    monkeypatch.setattr(driver, '_verify_recovery_args', lambda args: {'pins': {'process_witnesses': {}}})
    monkeypatch.setattr(driver, '_load_frozen_controller', lambda: FakeController())
    monkeypatch.setattr(sys, 'argv', ['run_scale20k_native_v3.py', '--recovery-entry', str(entry),
                                      '--recovery-entry-sha256', 'b' * 64,
                                      '--prior-control', str(parent), '--prior-control-sha256', parent_sha,
                                      '--inventory', 'inventory.json'])
    driver.main()
    assert '--prior-control' in captured['argv']
    assert str(parent.resolve()) in captured['argv']
    assert parent_sha in captured['argv']


def test_recovery_entry_requires_independent_code_pins(tmp_path):
    from recommend_book import scale20k_recovery_v1 as recovery

    path = tmp_path / 'entry.json'
    path.write_text(json.dumps({'version': recovery.ENTRY_VERSION, 'window_index': 161,
                                'run_name': 'scale20k-content-6801-6900-v2',
                                'failed_gate': False, 'removed': 14}), encoding='utf-8')
    with pytest.raises(ValueError, match='code file'):
        recovery._verify_code_pins(recovery.read(path))


def test_coordinator_uses_v3_module_level_observer(monkeypatch, tmp_path):
    scripts = ROOT / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location('scale20k_coordinator_v2_fixture',
                                                  scripts / 'run_scale20k_content_classification_v2.py')
    coordinator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coordinator)
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'manifest.json').write_text(json.dumps(
        {'chunks': [{'run_name': 'scale20k-content-6801-6900-v2'}]}), encoding='utf-8')
    frozen = tmp_path / 'frozen.py'
    frozen.write_text('pass\n', encoding='utf-8')
    monkeypatch.setattr(coordinator.state, 'code_paths', lambda: [frozen])
    monkeypatch.setattr(coordinator.state, 'BASE', tmp_path / 'base')
    monkeypatch.setattr(coordinator, '_execute_window', lambda *args, **kwargs: 'stubbed-window')
    assert coordinator.execute_window(source, tmp_path / 'runtime', 161) == 'stubbed-window'


def test_preparer_child_program_imports_v2_chunk():
    from recommend_book import scale20k_native_v2 as state

    recipe = state.load_recipe('preparer', ROOT / 'scripts/prepare_scale20k_content_chunk_v2.py')
    child = recipe.prepare.__kwdefaults__['_fn']
    constants = [value for value in child.__code__.co_consts if isinstance(value, str)]
    imports = [value for value in constants if 'prepare_scale20k_content_chunk_' in value]
    assert any('prepare_scale20k_content_chunk_v2' in value for value in imports)
    assert all('prepare_scale20k_content_chunk_v1' not in value for value in imports)
