"""Explicit v20 controller: reserve, admit, classify, review, publish and count.

Only successful genuine controls advance the next window. Every directory is
create-only; this command has no retry, recovery, source producer or model probe.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from types import ModuleType

from recommend_book import scale20k_native_v1 as state


def emit(value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


def observed_command(argv, directory, *, env=None, timeout=None, stdout_name='stdout.txt', request_fields=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    argv = list(map(str, argv))
    state.save(directory / 'request.json', {'argv': argv, 'shell': False, **(request_fields or {})})
    started = time.monotonic()
    timed_out = False
    with (directory / stdout_name).open('xb') as stdout, (directory / 'stderr.txt').open('xb') as stderr:
        with subprocess.Popen(argv, cwd=state.ROOT, stdin=subprocess.DEVNULL, stdout=stdout,
                              stderr=stderr, shell=False, env=env) as process:
            birth = state.process_created_at(process.pid)
            state.save(directory / 'process-start.json', {'pid': process.pid, 'process_created_at': birth,
                'started_at': datetime.now(timezone.utc).isoformat()})
            emit({'stage': 'actual_operational_child_started', 'pid': process.pid,
                  'process_created_at': birth, 'directory': str(directory)})
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait()
            result = {'pid': process.pid, 'actual_exit_code': process.returncode,
                'process_exit_observed': True, 'process_created_at': birth,
                'elapsed_seconds': time.monotonic() - started, 'timed_out': timed_out,
                'observed_at': datetime.now(timezone.utc).isoformat()}
    state.save(directory / 'process-exit.json', result)
    if result['actual_exit_code'] != 0 or timed_out:
        raise ValueError('Actual child failed; preserve outputs and stop without retry: ' + str(directory))
    return result


def observed_import(path, expected_sha):
    path = Path(path).resolve(strict=True)
    if state.sha(path) != expected_sha:
        raise ValueError('Public export changed before actual import')
    document = state.read(path)
    if document['version'] != 'scale-content-native-classification-export-v3':
        raise ValueError('Actual rights3 public export required')
    report = state.BASE / 'catalogue-imports' / path.parent.name / 'manifest.json'
    if report.parent.exists():
        raise FileExistsError('Import already started; no automatic import retry')
    qids = {row['qid'] for row in document['verified_projection']['rows']}
    if not qids:
        raise ValueError('No genuinely admitted new works to import')
    with state.writer_lock(purpose='v20_authentic_native_import'):
        with sqlite3.connect(state.DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
            marks = ','.join('?' for _ in qids)
            existing = db.execute('SELECT count(*) FROM native_classifications WHERE qid IN (' + marks + ')', sorted(qids)).fetchone()[0]
            eligible = db.execute('SELECT count(*) FROM works WHERE qid IN (' + marks
                + ') AND display_allowed=1 AND recommendable=1', sorted(qids)).fetchone()[0]
        if existing or eligible != len(qids):
            raise ValueError('Require entirely new, currently eligible authorities; no replacement')
        code = ('import json,sys; from recommend_book.scale_content_control_v4 import display_policy_sha256; '
                'before=display_policy_sha256(); from recommend_book.scale_guard_context_v1 import enable_process_cache; '
                '_cache=enable_process_cache(); from recommend_book.scale_catalogue_v1 import ScaleCatalogue; '
                'result=ScaleCatalogue(sys.argv[1]).import_classifications(sys.argv[2],sys.argv[3]); '
                'after=display_policy_sha256();\n'
                'if before!=after: raise ValueError("Display policy changed during actual import")\n'
                'result["display_policy_sha256"]=before; print(json.dumps(result,ensure_ascii=True))')
        actual = observed_command([sys.executable, '-c', code, state.DATABASE.resolve(), path, expected_sha],
            report.parent, stdout_name='stdout.json', request_fields={
                'classification_manifest_sha256': expected_sha, 'full_public_native_verifier_required': True})
        result = state.read(report.parent / 'stdout.json')
        value = {'stage': 'actual_catalogue_import_complete' if result and not result['failed'] else 'actual_import_error',
                 'classification_manifest': str(path), 'classification_manifest_sha256': expected_sha,
                 'actual_process': actual, 'result': result, 'full_public_native_verifier_used': True,
                 'model_outputs_unchanged': True, 'observed_at': datetime.now(timezone.utc).isoformat()}
        state.save(report, value)
    emit(value)
    if value['stage'] != 'actual_catalogue_import_complete':
        raise ValueError('Actual public import failed; keep outputs without retry')
    return report, value


def load_publication():
    """Reuse the exact existing v4 receipt constructor, with explicit parameters."""
    path = state.ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py'
    if state.sha(path) != state.OPERATION_SHA:
        raise ValueError('Fixed genuine control constructor changed')
    module = ModuleType('v20_publication_receipts')
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), 'exec'), module.__dict__)
    module.ROOT, module.BASE, module.DATABASE, module.LOCK = state.ROOT, state.BASE, state.DATABASE, state.WRITER_LOCK
    module.observed_import = observed_import
    return module


def next_concurrency(previous_run, *, first):
    clean = (previous_run['failed'] == 0 and previous_run['pending'] == 0
             and not previous_run.get('stopped') and not previous_run.get('stop_reasons')
             and previous_run['initial_concurrency'] == previous_run['final_concurrency']
             and previous_run['final_concurrency'] in (4, 8))
    return 8 if not first and clean else 4


def source_fits_remaining(fixed, remaining):
    return type(fixed['selected']) is int and 1 <= fixed['selected'] <= min(100, remaining)


def run_window(*, source, source_sha, global_index, fixed, inventory_path, inventory_sha,
               previous_control, previous_sha, concurrency, publication,
               source_index=None, source_index_sha=None):
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    reservation = state.reserve_window(source, global_index, source_sha, inventory_path, inventory_sha,
                                       source_index, source_index_sha)
    chunk = fixed['chunks'][0]
    name = chunk['run_name']
    emit({'stage': 'new_v20_attempt_reserved', 'selected': fixed['selected'], 'global_index': global_index,
          'path': str(reservation), 'sha256': state.sha(reservation)})
    common = ['--prior-control', previous_control, '--prior-control-sha256', previous_sha]
    observed_command([sys.executable, 'scripts/prepare_scale20k_content_chunk_v1.py',
        '--runtime-preparation', state.RUNTIME, '--inputs', chunk['inputs_path'],
        '--usage-ledger', chunk['usage_ledger_path'], '--run-name', name,
        '--concurrency', concurrency, *common], state.BASE / 'operational-processes' / name / 'preflight')
    observed_command([sys.executable, 'scripts/run_scale20k_content_classification_v1.py',
        '--source-preparation', source, '--runtime-preparation', state.RUNTIME,
        '--route-index', global_index, '--control-receipt', previous_control,
        '--control-receipt-sha256', previous_sha],
        state.BASE / 'operational-processes' / name / 'native-and-review')
    parent = state.BASE / 'classification-exports' / name / 'manifest.json'
    native = state.read(parent)
    terminal = state.RUNTIME / ('terminal-' + name)
    started, exited = state.read(terminal / 'process-start.json'), state.read(terminal / 'process-exit.json')
    if (type(exited['actual_exit_code']) is not int or exited['actual_exit_code'] != 0
            or exited['process_exit_observed'] is not True):
        raise ValueError('Actual successful native terminal required')
    pid_seconds = (datetime.fromisoformat(exited['observed_at']) - datetime.fromisoformat(started['started_at'])).total_seconds()
    model_seconds = pid_seconds + native['separate_review']['elapsed_seconds']
    if not 0 <= model_seconds <= 3600:
        raise ValueError('Actual PID-inclusive one-hour model budget exceeded')
    public = state.BASE / 'classification-exports-v3' / name / 'manifest.json'
    code = ('import json,sys; from pathlib import Path; '
            'from recommend_book.scale_content_classification_v3 import create_reviewed_export; '
            'print(json.dumps(create_reviewed_export(Path(sys.argv[1]),sys.argv[2],Path(sys.argv[3]))))')
    observed_command([sys.executable, '-c', code, parent, state.sha(parent), public.parent],
        state.BASE / 'operational-processes' / name / 'output-rights')
    receipt = publication.finalize(public, state.sha(public))
    control = state.BASE / 'window-controls' / (name + '.json')
    document = state.read(public)
    actual_run = state.read(document['run_manifest'])
    summary = {'run_name': name, 'global_index': global_index, 'cohort_index': global_index - 93,
        'requirements_sha256': state.REQUIREMENTS_SHA, 'inventory_sha256': inventory_sha,
        'reservation_path': str(reservation), 'reservation_sha256': state.sha(reservation),
        'public_manifest': str(public), 'public_manifest_sha256': state.sha(public),
        'control_path': str(control), 'control_sha256': state.sha(control), 'gate': receipt['gate'],
        'native_selected': actual_run['selected'], 'native_completed': actual_run['completed'],
        'native_failed': actual_run['failed'], 'native_usage': actual_run['usage'],
        'separate_review': document['separate_review'],
        'native_terminal_pid_interval_seconds': pid_seconds, 'pid_inclusive_model_stage_seconds': model_seconds,
        'native_launch_through_public_import_wall_seconds': (datetime.now(timezone.utc)
            - datetime.fromisoformat(started['started_at'])).total_seconds(),
        'actual_display_labels_by_axis': receipt['actual_display_labels_by_axis'],
        'actual_public_eligible_cumulative': state.eligible_count(), 'batch_size': 1,
        'observed_at': datetime.now(timezone.utc).isoformat()}
    state.save(state.BASE / 'run-summaries' / (name + '.json'), summary)
    emit({'stage': 'actual_v20_window_publicly_imported', 'summary': summary})
    if not receipt['gate']['passed'] or not document['gate']['passed']:
        raise ValueError('Actual window gate failed; preserve imported progress and stop')
    verify_control_receipt(control, state.sha(control))
    return control, state.sha(control), receipt


def run(inventory_path, inventory_sha, source_index, source_index_sha, previous_control, previous_sha):
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    state.verify_requirements()
    state.validate_baseline_membership(state.load_inventory(inventory_path, inventory_sha))
    prior = verify_control_receipt(previous_control, previous_sha)
    global_index = prior['window_index'] + 1
    if global_index < 93 or (global_index == 93 and (Path(previous_control).resolve() != state.PRIOR_CONTROL.resolve()
                                                   or previous_sha != state.PRIOR_CONTROL_SHA)):
        raise ValueError('V20 must begin at the genuine final v19 control92')
    if global_index > 93 and not Path(previous_control).resolve(strict=True).is_relative_to((state.BASE / 'window-controls').resolve()):
        raise ValueError('Further v20 windows require an actual v20 control')
    if not 10000 <= state.eligible_count() <= 20000:
        raise ValueError('Actual preserved baseline/target count differs')
    # Explicit invocations may extend a completed supplied cohort; never resume an attempt.
    entry = state.BASE / 'controller-entries' / f'after-{global_index-1:04d}-{source_index_sha[:16]}.json'
    state.save(entry, {'version': 'scale20k-controller-entry-v1', 'requirements_sha256': state.REQUIREMENTS_SHA,
        'inventory_path': str(Path(inventory_path).resolve()), 'inventory_sha256': inventory_sha,
        'source_index_path': str(Path(source_index).resolve()), 'source_index_sha256': source_index_sha,
        'prior_control': str(Path(previous_control).resolve()), 'prior_control_sha256': previous_sha,
        'driver_sha256': state.sha(__file__), 'pid': os.getpid(),
        'process_created_at': state.process_created_at(os.getpid())})
    publication = load_publication()
    first = global_index == 93
    while state.eligible_count() < 20000:
        try:
            selected = state.source_entry(source_index, source_index_sha, global_index)
        except state.SourceSupplyExhausted:
            emit({'stage': 'sealed_source_supply_exhausted', 'next_global_index': global_index,
                  'eligible_native_works': state.eligible_count(), 'model_started': False,
                  'resume_requires_new_fixed_handoff_and_explicit_invocation': True})
            return
        source = Path(selected['source_path'])
        fixed, _, _ = state.current_source(source, global_index, selected['source_sha256'],
                                           state.load_inventory(inventory_path, inventory_sha))
        remaining = 20000 - state.eligible_count()
        if not source_fits_remaining(fixed, remaining):
            emit({'stage': 'exact_shortfall_source_required', 'remaining': remaining,
                  'next_global_index': global_index, 'model_started': False})
            return
        previous_run = state.read(prior['control_artifacts']['run']['path'])
        slots = next_concurrency(previous_run, first=first)
        previous_control, previous_sha, prior = run_window(source=source, source_sha=selected['source_sha256'],
            global_index=global_index, fixed=fixed, inventory_path=inventory_path, inventory_sha=inventory_sha,
            previous_control=previous_control, previous_sha=previous_sha, concurrency=slots, publication=publication,
            source_index=source_index, source_index_sha=source_index_sha)
        global_index += 1
        first = False
    emit({'stage': 'actual_v20_native_target_reached', 'eligible_native_works': state.eligible_count(),
          'source_producer_started_by_native_driver': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--source-index', type=Path, required=True)
    parser.add_argument('--source-index-sha256', required=True)
    parser.add_argument('--prior-control', type=Path, default=state.PRIOR_CONTROL)
    parser.add_argument('--prior-control-sha256', default=state.PRIOR_CONTROL_SHA)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if any(re.fullmatch('[0-9a-f]{64}', value) is None for value in
           (args.inventory_sha256, args.source_index_sha256, args.prior_control_sha256)):
        parser.error('Independent lower-case SHA256 pins must contain exactly 64 hex digits')
    if args.worker:
        run(args.inventory, args.inventory_sha256, args.source_index, args.source_index_sha256,
            args.prior_control, args.prior_control_sha256)
    else:
        # One witness and one worker; only the worker can launch model stages.
        # Even if the tool session disappears, the actual worker exit survives.
        output = state.BASE / 'controller-processes' / (args.prior_control_sha256[:16] + '-' + args.source_index_sha256[:16])
        observed_command([sys.executable, Path(__file__).resolve(), *sys.argv[1:], '--worker'], output,
            request_fields={'driver_sha256': state.sha(__file__), 'requirements_sha256': state.REQUIREMENTS_SHA,
                            'role': 'one_controller_worker_actual_terminal_witness'})


if __name__ == '__main__':
    main()
