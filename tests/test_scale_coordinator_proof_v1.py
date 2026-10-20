"""Actual terminal-parent-request-coordinator chain; no classification calls."""
import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
from recommend_book import scale_gate_replay_v2


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def chain(tmp_path):
    runtime = tmp_path / 'preparation'
    run = runtime / 'runtime/data/autonomous-next-v3/classification/new100/run/manifest.json'
    save(run, {'run_id': 'actual-run-id'})
    coordinator = tmp_path / 'coordinator.py'
    coordinator.write_text('authentic immutable coordinator')
    terminal = runtime / 'terminal-new100/terminal-proof.json'
    request = terminal.with_name('request.json')
    exit_path = terminal.with_name('process-exit.json')
    save(request, {'version': 'native-snapshot-terminal-request-v1',
        'coordinator_path': str(coordinator), 'coordinator_sha256': sha(coordinator)})
    save(exit_path, {'version': 'native-snapshot-terminal-exit-v1', 'process_pid': 123,
                    'actual_exit_code': 0, 'process_exit_observed': True})
    save(terminal, {'schema_version': 'classification-terminal-proof-v1',
        'run_id': 'actual-run-id', 'manifest_sha256': sha(run), 'exit_code': 0,
        'process_exit_observed': True, 'process_pid': 123,
        'parent_request_sha256': sha(request), 'parent_exit_observation_sha256': sha(exit_path)})
    document = {'runtime_preparation': str(runtime), 'run_manifest': str(run),
        'coordinator_sha256': sha(coordinator), 'artifact_sha256': {str(run): sha(run), str(terminal): sha(terminal)}}
    return document, {'run': run, 'terminal': terminal, 'request': request, 'exit': exit_path, 'coordinator': coordinator}


def test_chain_adds_previously_unflattened_parent_and_actual_coordinator(chain):
    document, files = chain
    pins = {item['path']: item['sha256'] for item in coordinator_chain_pins(document)}
    assert set(map(str, files.values())) <= set(pins)
    assert all(sha(Path(path)) == digest for path, digest in pins.items())


@pytest.mark.parametrize('changed', ['run', 'terminal', 'request', 'exit', 'coordinator'])
def test_mutating_any_actual_parent_chain_file_is_rejected(chain, changed):
    document, files = chain
    files[changed].write_bytes(files[changed].read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA differs'):
        coordinator_chain_pins(document)


def test_export_cannot_name_unchecked_different_coordinator(chain):
    document, _ = chain
    document['coordinator_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='exported coordinator'):
        coordinator_chain_pins(document)


def test_self_consistent_wrong_parent_pid_does_not_pass(chain):
    document, files = chain
    observed = json.loads(files['exit'].read_bytes())
    observed['process_pid'] = 999
    save(files['exit'], observed)
    terminal = json.loads(files['terminal'].read_bytes())
    terminal['parent_exit_observation_sha256'] = sha(files['exit'])
    save(files['terminal'], terminal)
    document['artifact_sha256'][str(files['terminal'])] = sha(files['terminal'])
    with pytest.raises(ValueError, match='Parent observed exit'):
        coordinator_chain_pins(document)


def test_same_named_unpinned_terminal_is_not_accepted(chain):
    document, files = chain
    document['artifact_sha256'].pop(str(files['terminal']))
    with pytest.raises(ValueError, match='lacks pinned actual terminal'):
        coordinator_chain_pins(document)


def test_new_gate_wrapper_reuses_v1_receipt_and_adds_fresh_actual_chain(chain, tmp_path, monkeypatch):
    document, files = chain
    manifest = tmp_path / 'export.json'
    save(manifest, document)
    calls = []
    monkeypatch.setattr(scale_gate_replay_v2, '_verify_v1',
                        lambda *args: calls.append(args) or {'cache_hit': True, 'receipt_path': 'existing'})
    result = scale_gate_replay_v2.verify_prior_export(manifest, sha(manifest), tmp_path / 'cache')
    assert result['cache_hit'] is True and len(calls) == 1
    assert str(files['coordinator']) in {item['path'] for item in result['additional_required_artifacts']}
    files['coordinator'].write_text('changed coordinator')
    with pytest.raises(ValueError, match='SHA differs'):
        scale_gate_replay_v2.verify_prior_export(manifest, sha(manifest), tmp_path / 'cache')
    assert len(calls) == 1


def test_new_versioned_coordinator_only_uses_strengthened_gate_and_current_chain():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    path = root / 'scripts/run_scale_classification_v3.py'
    spec = importlib.util.spec_from_file_location('coordinator_v3', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.check_gate({'selected': 100, 'failed': 0, 'pending': 0}, {'actual_feature_adopted_works': 95})['passed']
    text = path.read_text(encoding='utf-8')
    assert 'from recommend_book.scale_gate_replay_v2 import verify_prior_export' in text
    assert 'current_chain = coordinator_chain_pins(' in text
    assert "replay['additional_required_artifacts']" in text
