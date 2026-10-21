"""Reuse an immutable native runtime while the independent public UI evolves."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from recommend_book.classification.native_reference_store_v1 import BINDING_NAME, verify_binding

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_frozen_runtime(preparation):
    preparation = Path(preparation).resolve(strict=True)
    manifest = preparation / 'source-snapshot-manifest.json'
    report = json.loads(manifest.read_bytes())
    runtime = preparation / 'runtime'
    if runtime.resolve() != Path(report['runtime_root']).resolve(strict=True):
        raise ValueError('Frozen content runtime location differs')
    shared = report.get('reference_storage')
    if shared:
        if sha(runtime / BINDING_NAME) != shared['binding_sha256']:
            raise ValueError('Frozen shared binding changed')
        binding = json.loads((runtime / BINDING_NAME).read_bytes())
        if binding['artifacts'] != shared['artifacts']:
            raise ValueError('Frozen shared reference set differs')
        verify_binding(binding)
    checked = {}
    for name, expected in report['copied_original_sha256'].items():
        target = runtime / name
        if sha(target) != expected:
            raise ValueError('Frozen original content runtime changed: ' + name)
        checked[str(target)] = expected
    for name, item in report['transformed'].items():
        target = runtime / name
        if sha(target) != item['sha256']:
            raise ValueError('Frozen transformed native runtime changed: ' + name)
        checked[str(target)] = item['sha256']
    # Shared external corpus is already SHA checked by verify_binding; retain
    # the exact immutable pins without copying that corpus into this window.
    if shared:
        checked.update({item['path']: item['sha256'] for item in shared['artifacts'].values()})
    return {'snapshot': str(manifest), 'snapshot_sha256': sha(manifest),
            'runtime': str(runtime), 'checked_artifact_sha256': checked,
            'current_public_backend_ui_not_used_for_native_execution': True}


def prepare(preparation, inputs, ledger, run_name, concurrency):
    frozen = verify_frozen_runtime(preparation)
    preparation = Path(preparation).resolve(strict=True)
    argv = [sys.executable, str(ROOT / 'scripts/prepare_scale_classification_v1.py'), '_chunk',
        '--runtime-preparation', str(preparation), '--inputs', str(Path(inputs).resolve(strict=True)),
        '--usage-ledger', str(Path(ledger).resolve(strict=True)), '--cli-version', 'codex-cli 0.163.0',
        '--run-name', run_name, '--concurrency', str(concurrency)]
    env = dict(os.environ, PYTHONPATH=str(Path(frozen['runtime']) / 'src'), PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, shell=False, timeout=900)
    prefix = preparation / ('preflight.' + run_name)
    Path(str(prefix) + '.stdout.txt').write_bytes(process.stdout)
    Path(str(prefix) + '.stderr.txt').write_bytes(process.stderr)
    if process.returncode:
        raise ValueError('Actual frozen content preflight failed: ' + process.stderr.decode('utf-8', 'replace')[-2000:])
    # Recheck the frozen code after actual per-use admissions, with no model.
    if verify_frozen_runtime(preparation) != frozen:
        raise ValueError('Frozen content source changed during preflight')
    receipt = {**frozen, 'adapter_path': str(Path(__file__).resolve()), 'adapter_sha256': sha(__file__),
               'unchanged_chunk_preparer_sha256': sha(ROOT / 'scripts/prepare_scale_classification_v1.py'),
               'actual_preflight_exit_code': process.returncode, 'model_calls': 0,
               'actual_preflight_sha256': sha(Path(str(prefix) + '.json'))}
    output = Path(str(prefix) + '.frozen-validation.json')
    with output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
    return json.loads(process.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-preparation', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--usage-ledger', type=Path, required=True)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--concurrency', type=int, choices=(2, 4), default=4)
    args = parser.parse_args()
    print(json.dumps(prepare(args.runtime_preparation, args.inputs, args.usage_ledger, args.run_name, args.concurrency)))


if __name__ == '__main__':
    main()
