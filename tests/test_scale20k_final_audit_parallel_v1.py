from pathlib import Path
import importlib.util
import threading

import pytest

_source = Path(__file__).resolve().parents[1] / 'scripts/run_scale20k_final_audit_v1.py'
_spec = importlib.util.spec_from_file_location('test_final_audit_parallel', _source)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
parallel_manifest_verifier = _module.parallel_manifest_verifier


def test_proofs_run_in_parallel_once_and_return_in_auditor_order(tmp_path):
    paths = [tmp_path / name for name in ('first.json', 'second.json')]
    for path in paths:
        path.write_text('{}')
    pairs = [(str(path.resolve()), str(i)) for i, path in enumerate(paths)]
    barrier = threading.Barrier(2)
    calls = []
    def verify(path, digest):
        calls.append((path, digest))
        barrier.wait(timeout=5)
        return [{'digest': digest}]
    with parallel_manifest_verifier(pairs, verify, workers=2) as consume:
        assert consume(*pairs[1]) == [{'digest': '1'}]
        assert consume(*pairs[0]) == [{'digest': '0'}]
        with pytest.raises(ValueError, match='repeated'):
            consume(*pairs[0])
    assert sorted(calls) == sorted(pairs)


def test_failed_proof_never_returns_an_authorized_result(tmp_path):
    path = tmp_path / 'proof.json'
    path.write_text('{}')
    pair = (str(path.resolve()), 'digest')
    def reject(*args):
        raise ValueError('changed proof')
    with parallel_manifest_verifier([pair], reject) as consume:
        with pytest.raises(ValueError, match='changed proof'):
            consume(*pair)
