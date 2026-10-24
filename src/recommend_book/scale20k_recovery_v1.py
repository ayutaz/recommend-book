"""Read-only proof boundary for the failed global-161 v1 window.

The recovery receipt is deliberately separate from the v1 controller.  It
proves that the failed rows were quarantined before a v2 source is admitted;
it never edits the catalogue and it never authorises a retry of those inputs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'data/autonomous-next-v3/scale-20000-classifications-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
ENTRY_VERSION = 'scale20k-recovery-entry-v1'
FAILED_CONTROL = BASE / 'window-controls/scale20k-content-6801-6900-v1.json'
FAILED_CONTROL_SHA = '073dfb95d9c0fdb70c7f58c7638779ad940696b9ccfcab3a45ef1e542c4cec6a'
FAILED_SUMMARY = BASE / 'run-summaries/scale20k-content-6801-6900-v1.json'
FAILED_SUMMARY_SHA = '405bf4fcd670677a11dcb6542af4e769d1a81f4032a062776f09b8c3ad0fc1e3'
FAILED_EXPORT = BASE / 'classification-exports-v3/scale20k-content-6801-6900-v1/manifest.json'
FAILED_EXPORT_SHA = '4b4b67bd9e40bde564f9592be3c8b00048459765c0ca739952b20588aa22c90c'
PRIOR_CONTROL = BASE / 'window-controls/scale20k-content-6701-6800-v1.json'
PRIOR_CONTROL_SHA = 'd314c5084acab3b394c41a2e28a5527e4b2649561a4e03852a7cba1702971691'
QUARANTINE = BASE / 'recovery-gate161-v1/quarantine/manifest.json'
QUARANTINE_SHA = 'dc7fdeb75eb83f8e133f2dbdabd486e995cb68ef223494f7ea519657c498339d'
QUARANTINE_ROWS = BASE / 'recovery-gate161-v1/quarantine/rows-before.json'
QUARANTINE_ROWS_SHA = '569a0ac528818e2277e667440dbee8be01a36d159ee676b34c84a1268d8018ae'
QUARANTINE_SCRIPT_SHA = '25a0f2560b2c4eb4564667d9b79a02b7cca8e572abf7c110b883fc6626bd7d93'


def sha(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path: str | Path):
    return json.loads(Path(path).read_bytes())


def _pin(value, *, label: str):
    if not isinstance(value, dict) or not isinstance(value.get('path'), str):
        raise ValueError(f'{label} must contain a path and sha256')
    expected = value.get('sha256')
    path = Path(value['path']).resolve(strict=True)
    if not isinstance(expected, str) or sha(path) != expected:
        raise ValueError(f'{label} bytes differ from its sealed SHA256')
    return path, expected


def _required_pin(entry, key: str, fallback: Path, expected: str):
    value = entry.get(key)
    if value is None:
        value = {'path': str(fallback), 'sha256': expected}
    path, actual = _pin(value, label=key)
    if path != fallback.resolve() or actual != expected:
        raise ValueError(f'{key} must point at the immutable recovery proof')
    return {'path': str(path), 'sha256': actual}


def _failed_qids(rows_document):
    rows = rows_document.get('rows')
    if not isinstance(rows, list) or len(rows) != 14:
        raise ValueError('The failed window must have exactly fourteen quarantined rows')
    qids = [row.get('qid') for row in rows]
    if any(not isinstance(qid, str) or not qid for qid in qids) or len(set(qids)) != 14:
        raise ValueError('Quarantined QIDs must be distinct non-empty strings')
    return tuple(sorted(qids))


def _verify_code_pins(entry):
    expected_names = {
        'scale20k_native_v2': ROOT / 'src/recommend_book/scale20k_native_v2.py',
        'scale20k_recovery_v1': ROOT / 'src/recommend_book/scale20k_recovery_v1.py',
        'run_scale20k_native_v3': ROOT / 'scripts/run_scale20k_native_v3.py',
        'run_scale20k_content_classification_v2': ROOT / 'scripts/run_scale20k_content_classification_v2.py',
        'prepare_scale20k_content_chunk_v2': ROOT / 'scripts/prepare_scale20k_content_chunk_v2.py',
        'prepare_scale20k_content_sources_v3': ROOT / 'scripts/prepare_scale20k_content_sources_v3.py',
    }
    pins = entry.get('code_pins')
    if not isinstance(pins, dict) or set(pins) != set(expected_names):
        raise ValueError('Recovery entry must independently pin every v2 recovery code file')
    result = {}
    for name, expected_path in expected_names.items():
        pin_path, pin_sha = _pin(pins[name], label='code_pins.' + name)
        if pin_path != expected_path.resolve() or pin_sha != sha(expected_path):
            raise ValueError('Recovery code pin differs from the actual sealed file: ' + name)
        result[name] = {'path': str(pin_path), 'sha256': pin_sha}
    return result


def verify_recovery_entry(path: str | Path, expected_sha: str, *, database=DATABASE):
    """Validate a sealed receipt and return ``{'entry', 'pins'}``.

    The returned pins are the values a later v2 controller must carry into its
    reservation and final audit.  This function is read-only by construction.
    """
    entry_path = Path(path).resolve(strict=True)
    if not isinstance(expected_sha, str) or sha(entry_path) != expected_sha:
        raise ValueError('Recovery entry bytes differ from the supplied SHA256')
    entry = read(entry_path)
    if (entry.get('version') != ENTRY_VERSION or entry.get('window_index') != 161
            or entry.get('failed_gate') is not False or entry.get('removed') != 14):
        raise ValueError('Recovery entry version, index, gate or removal count differs')
    if entry.get('run_name') not in (None, 'scale20k-content-6801-6900-v2'):
        raise ValueError('Recovery entry must name the v2 global-161 run')

    prior = _required_pin(entry, 'prior_control', PRIOR_CONTROL, PRIOR_CONTROL_SHA)
    failed = _required_pin(entry, 'failed_control', FAILED_CONTROL, FAILED_CONTROL_SHA)
    failed_summary = _required_pin(entry, 'failed_summary', FAILED_SUMMARY, FAILED_SUMMARY_SHA)
    failed_export = _required_pin(entry, 'failed_export', FAILED_EXPORT, FAILED_EXPORT_SHA)
    quarantine = _required_pin(entry, 'quarantine', QUARANTINE, QUARANTINE_SHA)
    rows_pin = _required_pin(entry, 'quarantine_rows', QUARANTINE_ROWS, QUARANTINE_ROWS_SHA)
    qmanifest, rows_document = read(quarantine['path']), read(rows_pin['path'])
    gate = qmanifest.get('failed_gate')
    if (qmanifest.get('status') != 'quarantined' or qmanifest.get('removed') != 14
            or not isinstance(gate, dict) or gate.get('passed') is not False
            or gate.get('selected') != 15 or gate.get('accepted') != 14
            or gate.get('failure_rate') != 1 / 15
            or qmanifest.get('failed_inputs_retried') != 0
            or qmanifest.get('model_calls') != 0
            or qmanifest.get('original_proofs_modified') is not False):
        raise ValueError('Actual quarantine proof does not describe a clean hold')
    qids = _failed_qids(rows_document)
    if entry.get('failed_qids') is not None and sorted(entry['failed_qids']) != list(qids):
        raise ValueError('Recovery entry failed QIDs differ from quarantine rows')
    if entry.get('catalogue_rows_remaining', 0) != 0:
        raise ValueError('Failed window rows remain in the catalogue')
    code_pins = _verify_code_pins(entry)

    dbpath = Path(database).resolve(strict=True)
    marks = ','.join('?' for _ in qids)
    with sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True) as db:
        remaining = db.execute(
            'SELECT count(*) FROM native_classifications WHERE qid IN (' + marks + ')', qids
        ).fetchone()[0]
    if remaining != 0:
        raise ValueError('Quarantined QIDs are still present in native_classifications')

    # Verify the known successful parent with the public verifier when present.
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    prior_receipt = verify_control_receipt(prior['path'], prior['sha256'])
    if prior_receipt.get('window_index') != 160:
        raise ValueError('Recovery parent must be the genuine passed global-160 control')
    failed_document = read(failed['path'])
    if (failed_document.get('window_index') != 161
            or failed_document.get('gate', {}).get('passed') is not False
            or failed_document.get('gate', {}).get('selected') != 15
            or failed_document.get('gate', {}).get('accepted') != 14
            or failed_document.get('gate', {}).get('failure_rate') != 1 / 15):
        raise ValueError('The excluded control is not the exact failed global-161 receipt')

    # A recovery receipt may carry the actual controller/quarantine process
    # chain.  When present, authenticate every witness and its exit semantics;
    # omitting it is rejected by the launch wrapper, while this lower-level
    # verifier remains useful for read-only receipt inspection and fixtures.
    witnesses = entry.get('process_witnesses')
    if witnesses is not None:
        required = ('failed_controller_start', 'failed_controller_exit',
                    'failed_controller_observer_start', 'failed_controller_observer_exit',
                    'quarantine_request', 'quarantine_start', 'quarantine_exit')
        if any(name not in witnesses for name in required):
            raise ValueError('Recovery process witness chain is incomplete')
        checked = {}
        for name in required:
            witness_path, witness_sha = _pin(witnesses[name], label='process_witnesses.' + name)
            checked[name] = {'path': str(witness_path), 'sha256': witness_sha}
        request = read(checked['quarantine_request']['path'])
        if (request.get('quarantine_script_sha256') != entry.get('quarantine_script_sha256')
                or entry.get('quarantine_script_sha256') != QUARANTINE_SCRIPT_SHA
                or request.get('failed_control_sha256') != FAILED_CONTROL_SHA
                or request.get('model_calls') != 0 or request.get('failed_inputs_retried') != 0):
            raise ValueError('Actual quarantine request is not bound to the failed hold')
        if read(checked['quarantine_exit']['path']).get('actual_exit_code') != 0:
            raise ValueError('Actual quarantine process did not exit successfully')
        def assert_process_pair(start_name, exit_name, expected_code):
            start, exit = read(checked[start_name]['path']), read(checked[exit_name]['path'])
            if (not isinstance(start.get('pid'), int) or start.get('pid') <= 0
                    or not isinstance(start.get('process_created_at'), str) or not start.get('process_created_at')
                    or exit.get('pid') != start.get('pid')
                    or exit.get('process_exit_observed') is not True
                    or exit.get('actual_exit_code') != expected_code):
                raise ValueError('Actual process witness pair has the wrong PID/exit semantics')
        assert_process_pair('failed_controller_start', 'failed_controller_exit', 1)
        assert_process_pair('failed_controller_observer_start', 'failed_controller_observer_exit', 1)
        assert_process_pair('quarantine_start', 'quarantine_exit', 0)
        pins_witnesses = checked
    else:
        pins_witnesses = None

    pins = {
        'entry': {'path': str(entry_path), 'sha256': expected_sha},
        'prior_control': prior,
        'failed_control': failed,
        'failed_summary': failed_summary,
        'failed_export': failed_export,
        'quarantine': quarantine,
        'quarantine_rows': rows_pin,
        'failed_qids': list(qids),
        'removed': 14,
        'catalogue_rows_remaining': 0,
        'failed_gate': False,
        'code_pins': code_pins,
    }
    if pins_witnesses is not None:
        pins['process_witnesses'] = pins_witnesses
    return {'entry': entry, 'pins': pins}
