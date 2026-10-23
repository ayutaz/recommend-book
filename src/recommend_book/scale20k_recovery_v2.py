"""Recovery receipt v2 for the second fresh handoff (old global 163 onward)."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from . import scale20k_recovery_v1 as _v1

ROOT, BASE, DATABASE = _v1.ROOT, _v1.BASE, _v1.DATABASE
ENTRY_VERSION = 'scale20k-recovery-entry-v2'
UNSTARTED_RESERVATION = BASE / 'attempt-reservations/scale20k-content-6801-6900-v2.json'
UNSTARTED_RESERVATION_SHA = 'c85b1e7e2dab114bb0e4e04a640b21fbee21dd65d349ee9f3b96e96d51faf26a'
FAILED_RESERVATION = BASE / 'attempt-reservations/scale20k-content-6801-6900-v1.json'
FAILED_RESERVATION_SHA = 'e78cfe78e0b2dd6236fa4e44b0d176f910f176c618ce4bce0dc17b8a28996864'
UNSTARTED_HOLD = BASE / 'recovery-gate161-v1/unstarted-preflight-hold-v1.json'
UNSTARTED_HOLD_SHA = '1302a61c0446a48dfa55d37b34a5d8555d6c6e59a9b1d4e53aa72b408332224d'
CODE_NAMES = {
    'scale20k_native_v3': ROOT / 'src/recommend_book/scale20k_native_v3.py',
    'scale20k_recovery_v2': ROOT / 'src/recommend_book/scale20k_recovery_v2.py',
    'run_scale20k_native_v4': ROOT / 'scripts/run_scale20k_native_v4.py',
    'run_scale20k_content_classification_v3': ROOT / 'scripts/run_scale20k_content_classification_v3.py',
    'prepare_scale20k_content_chunk_v3': ROOT / 'scripts/prepare_scale20k_content_chunk_v3.py',
    'prepare_scale20k_content_sources_v4': ROOT / 'scripts/prepare_scale20k_content_sources_v4.py',
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _code_pins(entry):
    pins = entry.get('code_pins')
    if not isinstance(pins, dict) or set(pins) != set(CODE_NAMES):
        raise ValueError('Recovery v2 entry must contain exactly six named code pins')
    result = {}
    for name, expected in CODE_NAMES.items():
        pin = pins[name]
        path = Path(pin.get('path', '')).resolve(strict=True) if isinstance(pin, dict) else None
        if path != expected.resolve() or pin.get('sha256') != sha(expected):
            raise ValueError('Recovery v2 code pin differs: ' + name)
        result[name] = {'path': str(path), 'sha256': pin['sha256']}
    return result


def _verify_hold_processes(hold):
    processes = hold.get('processes')
    if not isinstance(processes, dict) or set(processes) != {'preflight', 'controller_worker', 'controller_observer', 'sequential_queue'}:
        raise ValueError('Actual unstarted hold must pin all four failed process witnesses')
    checked = {}
    for name, pair in processes.items():
        if not isinstance(pair, dict):
            raise ValueError('Unstarted hold process witness is malformed')
        start = pair.get('start'); exit = pair.get('exit')
        if not isinstance(start, dict) or not isinstance(exit, dict):
            raise ValueError('Unstarted hold process start/exit pins are required')
        start_path, start_sha = _v1._pin(start, label='unstarted_hold.processes.' + name + '.start')
        exit_path, exit_sha = _v1._pin(exit, label='unstarted_hold.processes.' + name + '.exit')
        start_doc, exit_doc = _v1.read(start_path), _v1.read(exit_path)
        if (not isinstance(start_doc.get('pid'), int) or start_doc.get('pid') <= 0
                or not isinstance(start_doc.get('process_created_at'), str) or not start_doc.get('process_created_at')
                or exit_doc.get('pid') != start_doc.get('pid')
                or exit_doc.get('process_created_at') != start_doc.get('process_created_at')
                or exit_doc.get('process_exit_observed') is not True
                or exit_doc.get('actual_exit_code') != 1):
            raise ValueError('Unstarted hold process witness has unexpected exit semantics')
        checked[name] = {'start': {'path': str(start_path), 'sha256': start_sha},
                         'exit': {'path': str(exit_path), 'sha256': exit_sha}}
    request = hold.get('preflight_request')
    if not isinstance(request, dict):
        raise ValueError('Unstarted hold must pin its preflight request')
    request_path, request_sha = _v1._pin(request, label='unstarted_hold.preflight_request')
    checked['preflight_request'] = {'path': str(request_path), 'sha256': request_sha}
    return checked


def verify_recovery_entry(path, expected_sha, *, database=DATABASE):
    entry_path = Path(path).resolve(strict=True)
    if sha(entry_path) != expected_sha:
        raise ValueError('Recovery v2 entry SHA differs')
    original = _v1.read(entry_path)
    if original.get('version') != ENTRY_VERSION or original.get('window_index') != 161:
        raise ValueError('Recovery v2 entry version/index differs')
    if original.get('run_name') != 'scale20k-content-6801-6900-v3':
        raise ValueError('Recovery v2 entry must name the fresh v3 global-161 run')
    pins = _code_pins(original)
    reservation = original.get('unstarted_reservation')
    if not isinstance(reservation, dict) or Path(reservation.get('path', '')).resolve(strict=True) != UNSTARTED_RESERVATION.resolve() \
            or reservation.get('sha256') != UNSTARTED_RESERVATION_SHA:
        raise ValueError('Recovery v2 entry must pin the held 81-work reservation')
    if sha(UNSTARTED_RESERVATION) != UNSTARTED_RESERVATION_SHA:
        raise ValueError('Held v2 reservation bytes differ from its independent SHA')
    held = _v1.read(UNSTARTED_RESERVATION)
    if (held.get('global_index') != 161 or held.get('run_name') != 'scale20k-content-6801-6900-v2'
            or held.get('version') != 'scale20k-native-attempt-reservation-v2'
            or len(held.get('qids', [])) != 81 or len(set(held.get('qids', []))) != 81):
        raise ValueError('Held v2 reservation is not the exact unstarted 81-work receipt')
    hold_pin = original.get('unstarted_preflight_hold')
    if not isinstance(hold_pin, dict) or Path(hold_pin.get('path', '')).resolve(strict=True) != UNSTARTED_HOLD.resolve() or hold_pin.get('sha256') != UNSTARTED_HOLD_SHA:
        raise ValueError('Recovery v2 entry must pin the actual unstarted preflight hold')
    if sha(UNSTARTED_HOLD) != UNSTARTED_HOLD_SHA:
        raise ValueError('Actual unstarted hold bytes differ from its independent SHA')
    hold = _v1.read(UNSTARTED_HOLD)
    if (hold.get('version') != 'scale20k-unstarted-preflight-hold-v1'
            or hold.get('status') != 'held_unstarted_runtime_import_failure'
            or hold.get('window_index') != 161
            or hold.get('selected') != 81 or hold.get('model_calls') != 0
            or hold.get('same_inputs_retried') != 0 or hold.get('native_rows_for_selected') != 0
            or hold.get('eligible_before') != 15952 or hold.get('eligible_after') != 15952
            or sorted(hold.get('qids', [])) != sorted(held.get('qids', []))):
        raise ValueError('Actual unstarted preflight hold is not the exact 81-work receipt')
    hold_witnesses = _verify_hold_processes(hold)

    # Reuse the immutable failed-window/quarantine proof, adapting only the
    # entry version and fresh run name in memory.
    old_version, old_verify = _v1.ENTRY_VERSION, _v1._verify_code_pins
    old_read = _v1.read
    _v1.ENTRY_VERSION = _v1.ENTRY_VERSION
    def read_adapter(value):
        document = old_read(value)
        if Path(value).resolve() == entry_path:
            document = copy.deepcopy(document)
            document['version'] = _v1.ENTRY_VERSION
            document['run_name'] = 'scale20k-content-6801-6900-v2'
            document['code_pins'] = {
                'scale20k_native_v2': {'path': str(ROOT / 'src/recommend_book/scale20k_native_v2.py'),
                                       'sha256': sha(ROOT / 'src/recommend_book/scale20k_native_v2.py')},
                'scale20k_recovery_v1': {'path': str(ROOT / 'src/recommend_book/scale20k_recovery_v1.py'),
                                         'sha256': sha(ROOT / 'src/recommend_book/scale20k_recovery_v1.py')},
                'run_scale20k_native_v3': {'path': str(ROOT / 'scripts/run_scale20k_native_v3.py'),
                                           'sha256': sha(ROOT / 'scripts/run_scale20k_native_v3.py')},
                'run_scale20k_content_classification_v2': {'path': str(ROOT / 'scripts/run_scale20k_content_classification_v2.py'),
                                                           'sha256': sha(ROOT / 'scripts/run_scale20k_content_classification_v2.py')},
                'prepare_scale20k_content_chunk_v2': {'path': str(ROOT / 'scripts/prepare_scale20k_content_chunk_v2.py'),
                                                      'sha256': sha(ROOT / 'scripts/prepare_scale20k_content_chunk_v2.py')},
                'prepare_scale20k_content_sources_v3': {'path': str(ROOT / 'scripts/prepare_scale20k_content_sources_v3.py'),
                                                        'sha256': sha(ROOT / 'scripts/prepare_scale20k_content_sources_v3.py')},
            }
        return document
    def old_code_pins(document):
        return old_verify(document)
    try:
        _v1.read = read_adapter
        _v1._verify_code_pins = old_code_pins
        result = _v1.verify_recovery_entry(entry_path, expected_sha, database=database)
    finally:
        _v1.read, _v1._verify_code_pins = old_read, old_verify
    result['entry'] = original
    result['pins']['code_pins'] = pins
    result['pins']['unstarted_reservation'] = reservation
    result['pins']['unstarted_preflight_hold'] = hold_pin
    result['pins']['unstarted_preflight_processes'] = hold_witnesses
    result['pins']['excluded_unstarted_qids'] = held['qids']
    if sha(FAILED_RESERVATION) != FAILED_RESERVATION_SHA:
        raise ValueError('Failed v1 reservation bytes differ from its independent SHA')
    result['pins']['failed_reservation'] = {'path': str(FAILED_RESERVATION.resolve()), 'sha256': FAILED_RESERVATION_SHA}
    failed_attempt = _v1.read(FAILED_RESERVATION)
    result['pins']['excluded_failed_attempt_qids'] = sorted(set(result['pins']['failed_qids']) | set(failed_attempt['qids']))
    result['pins']['excluded_failed_input_hashes'] = sorted(set(failed_attempt.get('input_hashes', [])))
    witnesses = result['pins'].get('process_witnesses', {})
    for name in ('failed_controller_start', 'failed_controller_exit',
                 'failed_controller_observer_start', 'failed_controller_observer_exit',
                 'quarantine_start', 'quarantine_exit'):
        if name not in witnesses:
            raise ValueError('Recovery entry process witness chain is incomplete')
    for start_name, exit_name in (('failed_controller_start', 'failed_controller_exit'),
                                  ('failed_controller_observer_start', 'failed_controller_observer_exit'),
                                  ('quarantine_start', 'quarantine_exit')):
        start_doc = _v1.read(witnesses[start_name]['path'])
        exit_doc = _v1.read(witnesses[exit_name]['path'])
        if exit_doc.get('process_created_at') != start_doc.get('process_created_at'):
            raise ValueError('Recovery process birth witness differs between start and exit')
    return result
