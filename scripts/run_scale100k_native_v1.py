"""Serial v19 windows: new inputs only, genuine native/review/rights/import."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import ModuleType

from recommend_book import scale100k_native_v1 as state

ROOT, BASE, RUNTIME = state.ROOT, state.BASE, state.RUNTIME
OPERATION_SHA = '783226f2d3d8e0934ad611261801911f7a1b17b25cf51006d4276e3820f2f1c8'


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def load_operations(raw=None):
    path = ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py'
    raw = path.read_bytes() if raw is None else raw
    if state.hashlib.sha256(raw).hexdigest() != OPERATION_SHA:
        raise ValueError('Frozen native import observer changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    for before, after in (
        ("ROOT = Path(__file__).resolve().parents[3]", "ROOT = Path(__file__).resolve().parents[1]"),
        ("BASE = ROOT / 'data/autonomous-next-v3/scale-30000-v1'", "BASE = ROOT / 'data/autonomous-next-v3/scale-100000-v1'"),
        ('handle = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)',
         'handle = bounded_acquire_writer_lock(LOCK)'),
        ("json.dump({'pid': os.getpid(), 'purpose': 'authentic_native_import'",
         "json.dump({'pid': os.getpid(), 'process_created_at': process_created_at(os.getpid()), 'purpose': 'authentic_native_import'"),
    ):
        if source.count(before) != 1:
            raise ValueError('V19 import observer adapter anchor drift: ' + before)
        source = source.replace(before, after)
    result = ModuleType('v19_preserved_native_publication')
    result.__file__ = str(Path(__file__).resolve())
    result.bounded_acquire_writer_lock = state.bounded_acquire_writer_lock
    result.process_created_at = state.process_created_at
    exec(compile(source, result.__file__, 'exec'), result.__dict__)
    return result


def observed_command(argv, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    state.save(directory / 'request.json', {'argv': list(map(str, argv)), 'shell': False})
    started = time.monotonic()
    with (directory / 'stdout.txt').open('wb') as stdout, (directory / 'stderr.txt').open('wb') as stderr:
        with subprocess.Popen(list(map(str, argv)), cwd=ROOT, stdin=subprocess.DEVNULL,
                              stdout=stdout, stderr=stderr, shell=False) as process:
            state.save(directory / 'process-start.json', {'pid': process.pid, 'started_at': datetime.now(timezone.utc).isoformat()})
            emit({'stage': 'actual_operational_child_started', 'pid': process.pid,
                  'argv': list(map(str, argv)), 'directory': str(directory)})
            process.wait()
            result = {'pid': process.pid, 'actual_exit_code': process.returncode,
                      'process_exit_observed': True, 'elapsed_seconds': time.monotonic() - started}
    state.save(directory / 'process-exit.json', result)
    if result['actual_exit_code']:
        raise ValueError('Actual child failed; preserve output and stop without retry: ' + str(directory))
    return result


def selected_source(global_index, default):
    overrides = BASE / 'window-source-overrides.json'
    if not overrides.exists():
        return default
    path = state.read(overrides).get(str(global_index))
    if path is None:
        return default
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(BASE.resolve()):
        raise ValueError('V19 source override leaves its namespace')
    return path


def run(index_path, expected_index_sha):
    from recommend_book.scale_content_classification_v3 import create_reviewed_export
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    state.verify_requirements()
    state.load_exclusion_index(index_path, expected_index_sha)
    operation = load_operations()
    previous_control, previous_sha = state.PRIOR_CONTROL, state.PRIOR_CONTROL_SHA
    prior = verify_control_receipt(previous_control, previous_sha)
    if prior['window_index'] != 41:
        raise ValueError('V19 must continue the actual v18 final window')
    cohort = 0
    while state.eligible_count() < 10000:
        global_index = 42 + cohort
        expected_name = f'scale100k-content-{cohort*100+1:04d}-{cohort*100+100:04d}'
        default_source = BASE / 'classification-sources' / f'window-{cohort*100+1:04d}-{cohort*100+100:04d}'
        source = selected_source(global_index, default_source)
        while not (source / 'manifest.json').exists():
            emit({'stage': 'waiting_for_usage_admitted_source', 'global_index': global_index, 'source': str(source)})
            time.sleep(30)
            source = selected_source(global_index, default_source)
        fixed = state.read(source / 'manifest.json')
        remaining = 10000 - state.eligible_count()
        if fixed['selected'] > remaining:
            emit({'stage': 'waiting_for_exact_shortfall_source', 'remaining': remaining, 'selected': fixed['selected']})
            time.sleep(30)
            continue
        name = fixed['chunks'][0]['run_name']
        if re.fullmatch(re.escape(expected_name) + r'(?:-v[1-9][0-9]*)?', name) is None:
            raise ValueError('Actual v19 run name differs from the next new window')
        reservation = state.reserve_window(source, global_index, index_path, expected_index_sha)
        emit({'stage': 'new_v19_attempt_reserved', 'path': str(reservation), 'sha256': state.sha(reservation),
              'selected': fixed['selected'], 'global_index': global_index})
        previous_run = state.read(prior['control_artifacts']['run']['path'])
        clean = (not previous_run['failed'] and not previous_run['pending'] and not previous_run.get('stopped')
                 and not previous_run.get('stop_reasons')
                 and previous_run['initial_concurrency'] == previous_run['final_concurrency'])
        concurrency = 8 if cohort and clean and previous_run['final_concurrency'] in (4, 8) else 4
        chunk = fixed['chunks'][0]
        common = ['--prior-control', previous_control, '--prior-control-sha256', previous_sha]
        observed_command([sys.executable, 'scripts/prepare_scale100k_content_chunk_v1.py',
            '--runtime-preparation', RUNTIME, '--inputs', chunk['inputs_path'], '--usage-ledger', chunk['usage_ledger_path'],
            '--run-name', name, '--concurrency', str(concurrency), *common],
            BASE / 'operational-processes' / name / 'preflight')
        observed_command([sys.executable, 'scripts/run_scale100k_content_classification_v1.py',
            '--source-preparation', source, '--runtime-preparation', RUNTIME, '--route-index', str(global_index),
            '--control-receipt', previous_control, '--control-receipt-sha256', previous_sha],
            BASE / 'operational-processes' / name / 'native-and-review')
        parent = BASE / 'classification-exports' / name / 'manifest.json'
        if not parent.exists():
            raise ValueError('Actual coordinator exited without native export; no retry')
        native = state.read(parent)
        terminal = RUNTIME / ('terminal-' + name)
        started, exited = state.read(terminal / 'process-start.json'), state.read(terminal / 'process-exit.json')
        pid_seconds = (datetime.fromisoformat(exited['observed_at']) - datetime.fromisoformat(started['started_at'])).total_seconds()
        model_seconds = pid_seconds + native['separate_review']['elapsed_seconds']
        if model_seconds > 3600:
            raise ValueError('V19 actual PID-inclusive one-hour model budget exceeded')
        public = BASE / 'classification-exports-v3' / name / 'manifest.json'
        emit(create_reviewed_export(parent, state.sha(parent), public.parent))
        prior = operation.finalize(public, state.sha(public))
        previous_control = BASE / 'window-controls' / (name + '.json')
        previous_sha = state.sha(previous_control)
        document = state.read(public)
        actual_run = state.read(document['run_manifest'])
        summary = {'run_name': name, 'cohort_index': cohort, 'global_index': global_index,
            'requirements_sha256': state.REQUIREMENTS_SHA, 'exclusion_index_sha256': expected_index_sha,
            'reservation_path': str(reservation), 'reservation_sha256': state.sha(reservation),
            'native_manifest': str(parent), 'native_manifest_sha256': state.sha(parent),
            'public_manifest': str(public), 'public_manifest_sha256': state.sha(public),
            'gate': prior['gate'], 'native_selected': actual_run['selected'], 'native_completed': actual_run['completed'],
            'native_failed': actual_run['failed'], 'native_usage': actual_run['usage'],
            'native_elapsed_seconds': actual_run['elapsed_seconds'], 'native_terminal_pid_interval_seconds': pid_seconds,
            'pid_inclusive_model_stage_seconds': model_seconds,
            'native_launch_through_public_import_wall_seconds': (datetime.now(timezone.utc) - datetime.fromisoformat(started['started_at'])).total_seconds(),
            'separate_review': document['separate_review'], 'actual_display_labels_by_axis': prior['actual_display_labels_by_axis'],
            'actual_public_eligible_cumulative': state.eligible_count(), 'batch_size': 1,
            'observed_at': datetime.now(timezone.utc).isoformat()}
        state.save(BASE / 'run-summaries' / (name + '.json'), summary)
        emit({'stage': 'actual_window_publicly_imported', 'summary': summary})
        verify_control_receipt(previous_control, previous_sha)
        cohort += 1
    emit({'stage': 'actual_v19_native_target_reached', 'eligible_native_works': state.eligible_count(),
          'source_producer_started_by_native_driver': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create-exclusion-index', action='store_true')
    parser.add_argument('--exclusion-index', type=Path)
    parser.add_argument('--exclusion-index-sha256')
    args = parser.parse_args()
    if args.create_exclusion_index:
        state.create_exclusion_index()
        path = BASE / 'native-exclusions-v1.json'
        emit({'path': str(path), 'sha256': state.sha(path), 'model_calls': 0})
    else:
        if args.exclusion_index is None or args.exclusion_index_sha256 is None:
            parser.error('A separately fixed exclusion index path and expected SHA are required')
        run(args.exclusion_index, args.exclusion_index_sha256)


if __name__ == '__main__':
    main()
