"""Durable prior-window replay proofs with fresh exact-byte integrity per use.

Only an actually completed, isolated full native verifier creates a receipt.
Reusing it skips that loader, never a current source/ledger/code byte check.
This module performs no classification, external request, or usage admission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

VERSION = 'scale-prior-export-replay-v1'
ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _save(path, value):
    with Path(path).open('xb') as stream:
        stream.write(canonical(value) + b'\n')


def _expected_result(document):
    return [{'qid': row['qid'], 'work_id': row['work_id'], 'input_work_id': row['input_work_id'],
             'labels': row['admitted_labels']} for row in document['verified_projection']['rows']]


def _bindings(manifest, expected_sha256):
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Prior export manifest SHA differs')
    document = json.loads(raw)
    if document.get('version') != 'scale-native-classification-export-v1':
        raise ValueError('Unknown prior export version')
    runtime = Path(document['runtime_preparation']).resolve(strict=True) / 'runtime'
    verifier = ROOT / 'src/recommend_book/scale_classification_v1.py'
    frozen = runtime / 'src/recommend_book/scale_classification_v1.py'
    if verifier.read_bytes() != frozen.read_bytes():
        raise ValueError('Prior export frozen verifier differs')
    pins = dict(document['artifact_sha256'])
    for key in ('classified_database', 'usage_ledger', 'work_mapping'):
        if str(Path(document[key]).resolve(strict=True)) not in pins:
            raise ValueError('Prior export lacks required dependency pin')
    # Complete artifact bytes, including live registries/ledgers and executable
    # and frozen code dependencies, are reread on EVERY receipt use. A changed
    # revocation/condition/decision input invalidates the receipt immediately.
    pins.update({str(manifest): expected_sha256, str(Path(__file__).resolve()): sha(__file__),
                 str(verifier): sha(verifier), str(Path(sys.executable).resolve()): sha(sys.executable)})
    _check_pins(pins)
    return document, pins


def _check_pins(pins):
    for path, digest in pins.items():
        if not Path(path).is_absolute() or sha(path) != digest:
            raise ValueError('Prior replay dependency changed: ' + path)


def _request_value(manifest, expected_sha256):
    return {'argv': [sys.executable, '-m', 'recommend_book.scale_gate_replay_v1',
                    '--full-replay', str(manifest), '--expected-sha256', expected_sha256],
            'shell': False, 'stdin': 'DEVNULL', 'cwd': str(ROOT),
            'purpose': 'read_only_full_native_export_replay',
            'helper_sha256': sha(__file__), 'manifest_sha256': expected_sha256}


def _full_replay(manifest, expected_sha256, output):
    """Actual read-only subprocess; its own verifier invokes no model tools."""
    request = _request_value(manifest, expected_sha256)
    argv = request['argv']
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
        env.pop(key, None)
    _save(output / 'request.json', request)
    started = time.monotonic()
    with subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False) as process:
        _save(output / 'process-start.json', {'pid': process.pid,
              'started_at': datetime.now(timezone.utc).isoformat()})
        try:
            stdout, stderr = process.communicate(timeout=1800)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            (output / 'stdout.json').write_bytes(stdout)
            (output / 'stderr.txt').write_bytes(stderr)
            raise ValueError('Prior export full replay exceeded bounded timeout')
        observed = {'pid': process.pid, 'actual_exit_code': process.returncode,
                    'process_exit_observed': True, 'elapsed_seconds': time.monotonic() - started}
    (output / 'stdout.json').write_bytes(stdout)
    (output / 'stderr.txt').write_bytes(stderr)
    _save(output / 'process-exit.json', observed)
    if observed['actual_exit_code'] != 0:
        raise ValueError('Prior native full replay failed: ' + stderr.decode('utf-8', 'replace')[-1500:])
    return json.loads(stdout), observed


def verify_prior_export(manifest_path, expected_sha256, cache_directory):
    manifest = Path(manifest_path).resolve(strict=True)
    document, pins = _bindings(manifest, expected_sha256)
    expected = _expected_result(document)
    cache = Path(cache_directory).resolve() / expected_sha256 / sha(__file__)
    for receipt_path in sorted(cache.glob('attempt-*/receipt.json')):
        try:
            raw = receipt_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != receipt_path.with_suffix('.sha256').read_text().strip():
                continue
            receipt = json.loads(raw)
            if (receipt['version'] != VERSION or receipt['manifest_path'] != str(manifest)
                    or receipt['manifest_sha256'] != expected_sha256 or receipt['protected_artifacts'] != pins):
                continue
            output = receipt_path.parent
            required_artifacts = {str((output / name).resolve()) for name in
                ('request.json', 'process-start.json', 'process-exit.json', 'stdout.json', 'stderr.txt')}
            if set(receipt['replay_artifacts']) != required_artifacts:
                continue
            _check_pins(receipt['replay_artifacts'])
            request = json.loads((output / 'request.json').read_bytes())
            start = json.loads((output / 'process-start.json').read_bytes())
            observed = json.loads((output / 'process-exit.json').read_bytes())
            if (request != _request_value(manifest, expected_sha256)
                    or observed != receipt['observed_process'] or observed['pid'] != start['pid']
                    or observed['actual_exit_code'] != 0 or observed['process_exit_observed'] is not True
                    or json.loads((output / 'stdout.json').read_bytes()) != expected
                    or receipt['result_sha256'] != hashlib.sha256(canonical(expected)).hexdigest()):
                continue
            # Close the byte-change race after receipt/result validation too.
            _check_pins(pins)
            return {'cache_hit': True, 'receipt_path': str(receipt_path), 'receipt_sha256': sha(receipt_path),
                    'replay_artifacts': receipt['replay_artifacts'], 'protected_artifact_count': len(pins),
                    'full_replay_seconds': observed['elapsed_seconds']}
        except (OSError, ValueError, KeyError, TypeError):
            # A missing/altered replay proof is never an authorization flag.
            # Recreate proof only by running the full verifier again.
            continue
    output = cache / ('attempt-' + uuid.uuid4().hex)
    output.mkdir(parents=True, exist_ok=False)
    result, observed = _full_replay(manifest, expected_sha256, output)
    if result != expected or observed['actual_exit_code'] != 0 or observed['process_exit_observed'] is not True:
        raise ValueError('Actual prior export replay differs from bound native projection')
    _check_pins(pins)
    artifacts = {str((output / name).resolve()): sha(output / name) for name in
                 ('request.json', 'process-start.json', 'process-exit.json', 'stdout.json', 'stderr.txt')}
    receipt_path = output / 'receipt.json'
    _save(receipt_path, {'version': VERSION, 'manifest_path': str(manifest),
          'manifest_sha256': expected_sha256, 'protected_artifacts': pins,
          'replay_artifacts': artifacts, 'observed_process': observed,
          'result_sha256': hashlib.sha256(canonical(result)).hexdigest(),
          'usage_decisions_cached': False})
    receipt_path.with_suffix('.sha256').write_text(sha(receipt_path) + '\n', encoding='ascii')
    return {'cache_hit': False, 'receipt_path': str(receipt_path), 'receipt_sha256': sha(receipt_path),
            'replay_artifacts': artifacts, 'protected_artifact_count': len(pins),
            'full_replay_seconds': observed['elapsed_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-replay', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args()
    from recommend_book.scale_classification_v1 import verify_catalogue_classification
    rows = verify_catalogue_classification(args.full_replay, args.expected_sha256)
    print(json.dumps([{key: row[key] for key in ('qid', 'work_id', 'input_work_id', 'labels')}
                      for row in rows], ensure_ascii=True, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
