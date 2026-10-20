"""Prior-gate replay memoization: no models, no network, live byte rejection."""
import json
from pathlib import Path

import pytest

from recommend_book import scale_gate_replay_v1 as replay


@pytest.fixture
def prior(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    helper = root / 'src/recommend_book/scale_classification_v1.py'
    helper.parent.mkdir(parents=True)
    helper.write_text('frozen authentic verifier')
    runtime = root / 'runtime-preparation/runtime/src/recommend_book'
    runtime.mkdir(parents=True)
    (runtime / helper.name).write_bytes(helper.read_bytes())
    names = ('classified_database', 'usage_ledger', 'work_mapping', 'registry', 'source', 'frozen_code')
    paths = {}
    for name in names:
        paths[name] = root / (name + '.json')
        paths[name].write_text('original ' + name)
    row = {'qid': 'Q101', 'work_id': 'wd:Q101', 'input_work_id': 'work_101',
           'admitted_labels': [{'label_id': 'genre.fantasy', 'raw_confidence': .8, 'relevance': None}]}
    doc = {'version': 'scale-native-classification-export-v1',
           'runtime_preparation': str(root / 'runtime-preparation'),
           **{name: str(paths[name]) for name in names[:3]},
           'artifact_sha256': {str(path): replay.sha(path) for path in paths.values()},
           'verified_projection': {'rows': [row]}}
    manifest = root / 'manifest.json'
    manifest.write_text(json.dumps(doc))
    monkeypatch.setattr(replay, 'ROOT', root)
    calls = []

    def full(manifest, expected_sha, output):
        calls.append(output)
        request = replay._request_value(manifest, expected_sha)
        result = replay._expected_result(doc)
        observed = {'pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True, 'elapsed_seconds': 74.0}
        for name, value in (('request.json', request), ('process-start.json', {'pid': 123}),
                            ('process-exit.json', observed), ('stdout.json', result)):
            replay._save(output / name, value)
        (output / 'stderr.txt').write_bytes(b'')
        return result, observed

    monkeypatch.setattr(replay, '_full_replay', full)
    return manifest, replay.sha(manifest), tmp_path / 'cache', paths, calls


def test_durable_receipt_reuses_actual_replay_but_reads_all_bound_bytes(prior, monkeypatch):
    manifest, digest, cache, paths, calls = prior
    first = replay.verify_prior_export(manifest, digest, cache)
    assert first['cache_hit'] is False and len(calls) == 1
    checked = []
    original = replay.sha
    monkeypatch.setattr(replay, 'sha', lambda p: (checked.append(str(p)), original(p))[1])
    second = replay.verify_prior_export(manifest, digest, cache)
    assert second['cache_hit'] is True and len(calls) == 1
    assert set(map(str, paths.values())) <= set(checked)
    assert first['receipt_sha256'] == second['receipt_sha256']


@pytest.mark.parametrize('changed', ['usage_ledger', 'registry', 'source', 'frozen_code', 'classified_database'])
def test_any_changed_dependency_rejected_without_reusing_or_replaying(prior, changed):
    manifest, digest, cache, paths, calls = prior
    replay.verify_prior_export(manifest, digest, cache)
    original_stat = paths[changed].stat()
    raw = paths[changed].read_bytes()
    paths[changed].write_bytes(b'X' + raw[1:])
    import os
    os.utime(paths[changed], ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    with pytest.raises(ValueError, match='dependency changed'):
        replay.verify_prior_export(manifest, digest, cache)
    assert len(calls) == 1


def test_changed_manifest_rejected(prior):
    manifest, digest, cache, _, calls = prior
    replay.verify_prior_export(manifest, digest, cache)
    manifest.write_bytes(manifest.read_bytes() + b' ')
    with pytest.raises(ValueError, match='manifest SHA differs'):
        replay.verify_prior_export(manifest, digest, cache)
    assert len(calls) == 1


@pytest.mark.parametrize('tamper', ['receipt', 'stdout', 'observed_exit', 'missing_start'])
def test_flags_or_changed_replay_artifacts_require_actual_full_replay(prior, tamper):
    manifest, digest, cache, _, calls = prior
    first = replay.verify_prior_export(manifest, digest, cache)
    receipt = Path(first['receipt_path'])
    if tamper == 'receipt':
        receipt.write_text('{"verified":true}')
    elif tamper == 'stdout':
        (receipt.parent / 'stdout.json').write_text('[]')
    elif tamper == 'observed_exit':
        (receipt.parent / 'process-exit.json').write_text('{"actual_exit_code":0,"process_exit_observed":true}')
    else:
        (receipt.parent / 'process-start.json').unlink()
    second = replay.verify_prior_export(manifest, digest, cache)
    assert second['cache_hit'] is False and len(calls) == 2
    assert second['receipt_path'] != first['receipt_path']


def test_incorrect_full_replay_result_never_creates_receipt(prior, monkeypatch):
    manifest, digest, cache, _, _ = prior
    monkeypatch.setattr(replay, '_full_replay', lambda *args: ([], {
        'actual_exit_code': 0, 'process_exit_observed': True}))
    with pytest.raises(ValueError, match='differs from bound'):
        replay.verify_prior_export(manifest, digest, cache)
    assert not list(cache.glob('**/receipt.json'))


@pytest.mark.parametrize('passed', [True, False])
def test_versioned_coordinator_uses_verified_receipt_and_preserves_actual_gate(tmp_path, monkeypatch, passed):
    import importlib.util
    import hashlib
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('scale_coordinator_v2', root / 'scripts/run_scale_classification_v2.py')
    coordinator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(coordinator)
    source, runtime = tmp_path / 'source', tmp_path / 'runtime'
    source.mkdir()
    runtime.mkdir()
    inputs = source / 'inputs.jsonl'
    inputs.write_bytes(b'unchanged inputs')
    document = {'version': 'scale-classification-source-v1', 'all_six_purpose_admissions_allowed': True,
                'source_database': str(inputs), 'source_database_sha256': replay.sha(inputs),
                'chunks': [{}, {'inputs_path': str(inputs), 'inputs_sha256': replay.sha(inputs), 'run_name': 'next100'}]}
    (source / 'manifest.json').write_text(json.dumps(document))
    run_manifest = tmp_path / 'run.json'
    run_manifest.write_text(json.dumps({'selected': 100, 'failed': 0, 'pending': 0}))
    previous = tmp_path / 'previous.json'
    previous.write_text(json.dumps({'chunk_index': 0,
        'source_preparation_sha256': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest(),
        'run_manifest': str(run_manifest), 'gate': {'passed': passed},
        'verified_projection': {'actual_feature_adopted_works': 95}}))
    calls = []
    monkeypatch.setattr(replay, 'verify_prior_export', lambda *args: calls.append(args) or {'cache_hit': True})
    if passed:
        # The verified previous stage reaches untouched next-window preflight;
        # this fixture intentionally has none and can launch no model.
        with pytest.raises(FileNotFoundError, match='preflight.next100.json'):
            coordinator.execute_window(source, runtime, 1, previous_exports=[previous])
    else:
        with pytest.raises(ValueError, match='Previous actual native window failed'):
            coordinator.execute_window(source, runtime, 1, previous_exports=[previous])
    assert len(calls) == 1 and calls[0][0] == previous.resolve()
