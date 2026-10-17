"""Fixture-only scope checks; these do not claim real model classification."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from recommend_book.classification.native_validation_scope_v1 import NativeValidationScopes

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('snapshot_builder', ROOT / 'scripts/prepare_native_snapshot_runtime_v1.py')
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def executable(tmp_path):
    path = tmp_path / 'fixture-tool'
    path.write_bytes(b'original')
    return [str(path)], {str(path.resolve()):hashlib.sha256(b'original').hexdigest()}


def test_projection_scope_reads_bytes_twice_before_publication(tmp_path, monkeypatch):
    command, receipt = executable(tmp_path)
    reads, published = [], []
    read_bytes = Path.read_bytes
    def read(path):
        reads.append(path)
        return read_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    owner = NativeValidationScopes()
    with owner.validation():
        for _ in range(100):
            owner.current.check_receipt(command, receipt)
    published.append('after-close')
    assert len(reads) == 2
    assert published == ['after-close']
    assert owner.reports[0]['receipt_count'] == 100
    with pytest.raises(ValueError, match='active'):
        owner.current


def test_same_size_mtime_change_blocks_publication(tmp_path):
    command, receipt = executable(tmp_path)
    path = Path(command[0])
    before = path.stat()
    published = []
    owner = NativeValidationScopes()
    with pytest.raises(ValueError, match='changed'):
        with owner.validation():
            owner.current.check_receipt(command, receipt)
            path.write_bytes(b'modified')
            os.utime(path, ns=(before.st_atime_ns,before.st_mtime_ns))
        published.append('must-not-publish')
    assert published == []
    assert owner.reports == []


def test_snapshot_scope_cannot_be_resumed_or_reused(tmp_path):
    command, receipt = executable(tmp_path)
    owner = NativeValidationScopes()
    with owner.validation() as scope:
        scope.check_receipt(command,receipt)
    with pytest.raises(ValueError,match='closed'):
        scope.check_receipt(command,receipt)
    Path(command[0]).write_bytes(b'modified')
    with pytest.raises(ValueError,match='receipt differs'):
        with owner.validation():
            owner.current.check_receipt(command,receipt)


def test_two_concurrent_workers_have_distinct_invocation_snapshots(tmp_path):
    command, receipt = executable(tmp_path)
    owner, barrier = NativeValidationScopes(), threading.Barrier(2)
    def worker(_):
        with owner.validation() as scope:
            scope.check_receipt(command,receipt)
            barrier.wait(timeout=5)
            return id(scope)
    with ThreadPoolExecutor(max_workers=2) as pool:
        scopes = list(pool.map(worker,range(2)))
    assert len(set(scopes)) == 2
    assert len(owner.reports) == 2


@pytest.mark.parametrize('change', ['same_size_source', 'added_source'])
def test_source_context_drift_blocks_publication(tmp_path, change):
    command, receipt = executable(tmp_path)
    root = tmp_path / 'runtime'
    source = root / 'src/recommend_book/module.py'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'original')
    expected = {'src/recommend_book/module.py':hashlib.sha256(b'original').hexdigest()}
    owner, published = NativeValidationScopes(), []
    with pytest.raises(ValueError, match='source.*changed'):
        with owner.validation() as scope:
            scope.check_receipt(command, receipt)
            scope.check_source_receipts(root, expected)
            if change == 'same_size_source':
                source.write_bytes(b'modified')
            else:
                (source.parent / 'extra.py').write_bytes(b'added')
        published.append(True)
    assert published == []


@pytest.mark.parametrize('relative', ['classification/runner.py','classification/native_attempt.py','usage_guard.py','next_database.py'])
def test_transform_is_deterministic_compiles_and_preserves_original(relative):
    path = ROOT / 'src/recommend_book' / relative
    original = path.read_bytes()
    first = builder.transform(path.relative_to(ROOT).as_posix(),original)
    assert first == builder.transform(path.relative_to(ROOT).as_posix(),original)
    assert path.read_bytes() == original
    assert first != original
    compile(first,str(path),'exec')


def test_new_verifier_refuses_unscoped_or_legacy_result():
    original = (ROOT / 'src/recommend_book/classification/native_attempt.py').read_bytes()
    source = builder.transform('src/recommend_book/classification/native_attempt.py',original)
    # The verifier imports runner_v4 lazily; inspect the generated guard before
    # such import instead of disguising a synthetic subprocess as native work.
    assert b"if artifact_session is None:" in source
    assert b"descriptor.get('kind') != 'classification-native-attempt-v4'" in source
    assert b'command_artifact_hashes(command) !=' not in source
    from recommend_book.classification.native_attempt import verify_native_attempt
    with pytest.raises(ValueError, match='Unrecognized native result lineage'):
        verify_native_attempt({'kind':'classification-native-attempt-v4'}, {}, [], expected_config_sha256='unapproved',works=[])


def test_publication_is_explicitly_before_rename_and_full_context_is_hashed():
    source = builder.transform('src/recommend_book/next_database.py',(ROOT / 'src/recommend_book/next_database.py').read_bytes())
    assert b'_snapshot_boundary()\n        stage.rename(output)' in source
    runner = builder.transform('src/recommend_book/classification/runner.py',(ROOT / 'src/recommend_book/classification/runner.py').read_bytes())
    assert b"config['runtime_source_sha256']" in runner
    assert b'subprocess.Popen(argv' in runner
    assert b'"process_exit_observed":returncode is not None' in runner


@pytest.mark.parametrize('limit', [None, 200000])
def test_preflight_uses_same_token_limit_for_preparation_and_freeze(tmp_path, monkeypatch, limit):
    output = tmp_path / 'snapshot'
    runtime = output / 'runtime'
    runtime.mkdir(parents=True)
    verifier = runtime / 'verifier.py'
    verifier.write_bytes(b'fixture verifier')
    implementation = runtime / 'database.py'
    implementation.write_bytes(b'fixture database')
    inputs, ledger = tmp_path / 'inputs.jsonl', tmp_path / 'ledger.json'
    inputs.write_text('{}\n', encoding='utf-8')
    ledger.write_text('{}', encoding='utf-8')
    modules = {
        'recommend_book.classification.runner_v4': dict(build_run_config=lambda *a, **k: {},
            resolve_executable=lambda: ['fixture-tool'], command_artifact_hashes=lambda c: {}),
        'recommend_book.classification.native_attempt_v4': {'__file__': str(verifier)},
        'recommend_book.next_database_v4': {'__file__': str(implementation)},
        'recommend_book.usage_guard_v4': {'UsageGuard': object},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
        parent, attribute = name.rsplit('.', 1)
        monkeypatch.setattr(sys.modules[parent], attribute, module, raising=False)
    calls = {}
    def prepare(inputs, ledger, namespace, **kwargs):
        calls['prepare'] = kwargs
        namespace.mkdir(parents=True)
        (namespace / 'preparation-manifest.json').write_text('{}', encoding='utf-8')
        return {'selected': 1}
    def freeze_execution(*args, **kwargs):
        calls['freeze'] = kwargs
    helpers = {'new_guarded_preparation': SimpleNamespace(prepare=prepare),
               'new_execution_freeze': SimpleNamespace(freeze_execution=freeze_execution)}
    monkeypatch.setattr(importlib.util, 'spec_from_file_location', lambda name, path:
        SimpleNamespace(module=helpers[name], loader=SimpleNamespace(exec_module=lambda module: None)))
    monkeypatch.setattr(importlib.util, 'module_from_spec', lambda spec: spec.module)
    kwargs = {} if limit is None else {'max_observed_tokens': limit}
    result = builder.preflight(output, inputs, ledger, 'fixture-version', 'guarded-v3-baseline', 'fixture', **kwargs)
    assert result['stage'] == 'prepared_not_started'
    assert calls['prepare']['max_observed_tokens'] == calls['freeze']['max_observed_tokens'] == (limit or 500000)


@pytest.mark.parametrize('command', ['preflight', '_preflight'])
@pytest.mark.parametrize('limit', [None, '200000'])
def test_cli_forwards_token_limit_to_child_or_preflight(tmp_path, monkeypatch, command, limit):
    argv = ['prepare-runtime', command, '--output', str(tmp_path), '--inputs', str(tmp_path / 'inputs'),
            '--usage-ledger', str(tmp_path / 'ledger'), '--cli-version', 'fixture']
    if limit is not None:
        argv += ['--max-observed-tokens', limit]
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(builder, 'verify_source', lambda path: {})
    seen = []
    def run(command, **kwargs):
        seen.append(command[command.index('--max-observed-tokens') + 1])
        return SimpleNamespace(stdout=b'{}', stderr=b'', returncode=0)
    monkeypatch.setattr(builder.subprocess, 'run', run)
    monkeypatch.setattr(builder, 'preflight', lambda *args: seen.append(str(args[-1])) or {})
    builder.main()
    assert seen == [limit or '500000']


@pytest.mark.parametrize('limit', ['0', '-1', '1.5', 'bad'])
def test_cli_rejects_nonpositive_or_noninteger_token_limit(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(sys, 'argv', ['prepare-runtime', '_preflight', '--output', str(tmp_path),
                                    '--max-observed-tokens', limit])
    with pytest.raises(SystemExit) as error:
        builder.main()
    assert error.value.code == 2


@pytest.mark.parametrize('limit', [0, -1, True, 1.5])
def test_preflight_rejects_invalid_token_limit_before_runtime_access(tmp_path, limit):
    with pytest.raises(builder.argparse.ArgumentTypeError, match='positive integer'):
        builder.preflight(tmp_path, None, None, None, None, 'fixture', limit)
