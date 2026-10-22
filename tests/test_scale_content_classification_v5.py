"""Model-free coordinator-v5 to review-v6 route boundaries."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coordinator5_routes_the_existing_review_entry_to_v6_namespace():
    coordinator = load('coordinator5_fixture', 'scripts/run_scale100k_content_classification_v5.py')
    source = coordinator.adapted_source((ROOT / 'scripts/run_scale100k_content_classification_v4.py').read_bytes())
    assert 'from review_scale_content_v6 import execute;' in source
    assert 'review_scale_content_v6.py' in source
    assert 'review_scale_content_v5.py' in source
    assert 'run_scale100k_native_v5.py' in source
    assert 'separate-review-sharded-v6' in source
    assert 'separate-review-sharded-v4' not in source
    assert coordinator.REVIEW_SHA == __import__('hashlib').sha256(
        (ROOT / 'scripts/review_scale_content_v6.py').read_bytes()).hexdigest()


def test_coordinator5_rejects_recipe_drift_without_starting_a_child():
    coordinator = load('coordinator5_drift_fixture', 'scripts/run_scale100k_content_classification_v5.py')
    with pytest.raises(ValueError, match='Frozen'):
        coordinator.adapted_source(b'changed')


def test_coordinator5_and_review6_share_the_actual_execute_signature():
    coordinator = load('coordinator5_interface_fixture', 'scripts/run_scale100k_content_classification_v5.py')
    review = load('review6_interface_fixture', 'scripts/review_scale_content_v6.py')
    source = coordinator.adapted_source((ROOT / 'scripts/run_scale100k_content_classification_v4.py').read_bytes())
    assert 'selection_path=Path(sys.argv[10]),max_review_concurrency=int(sys.argv[11])' in source
    assert callable(review.execute)
    assert 'def execute(args):' in review.adapted_source(
        (ROOT / 'scripts/review_scale_content_v4.py').read_bytes())


def test_coordinator5_review_entry_reaches_review6_without_launching_a_child(tmp_path, monkeypatch):
    coordinator = load('coordinator5_entry_fixture', 'scripts/run_scale100k_content_classification_v5.py')
    source = coordinator.adapted_source((ROOT / 'scripts/run_scale100k_content_classification_v4.py').read_bytes())
    operation = ModuleType('coordinator5_entry_operation')
    operation.__file__ = str(ROOT / 'scripts/run_scale100k_content_classification_v5.py')
    operation.REVIEW_SHA = coordinator.REVIEW_SHA
    exec(compile(source, operation.__file__, 'exec'), operation.__dict__)
    operation.ROOT = tmp_path
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'review_scale_content_v6.py').write_bytes(
        (ROOT / 'scripts' / 'review_scale_content_v6.py').read_bytes())
    run_dir = tmp_path / 'run'
    run_dir.mkdir()
    (run_dir / 'manifest.json').write_text(
        '{"usage":{"input_tokens":1,"output_tokens":1},"elapsed_seconds":1,"stop_reasons":[],"completed":1,"final_concurrency":4}',
        encoding='utf-8')
    observed = {'run_dir': run_dir}
    observed.update({key: tmp_path / key for key in
                     ('ordered_inputs', 'execution_context_manifest', 'terminal_proof', 'usage_ledger')})
    for path in observed.values():
        if path != run_dir:
            path.write_bytes(b'fixture')
    calls = []
    import review_scale_content_v6 as review
    monkeypatch.setattr(review, 'preview_prompt_chars', lambda args, ids: 10)

    def stop_before_model(*args, **kwargs):
        raise RuntimeError('fixture stopped before model')

    monkeypatch.setattr(review, 'load_bounded_review', stop_before_model)

    def fake_frozen_call(runtime, code, arguments):
        if 'select_review_work_ids' in code:
            return ['selected']
        calls.append(code)
        monkeypatch.setattr(sys, 'argv', ['fixture', *map(str, arguments)])
        exec(compile(code, '<actual-coordinator-review-entry-fixture>', 'exec'), {})

    operation.frozen_call = fake_frozen_call
    with pytest.raises(RuntimeError, match='before model'):
        operation.review_content_window(tmp_path / 'runtime', observed, 'run', full=True)
    assert len(calls) == 1
    assert 'from review_scale_content_v6 import execute;' in calls[0]
    assert 'review_scale_content_v4' not in calls[0]
    plan = tmp_path / 'data/autonomous-next-v3/scale-100000-v1/separate-review-sharded-v6/run/review-plan.json'
    assert plan.exists()
