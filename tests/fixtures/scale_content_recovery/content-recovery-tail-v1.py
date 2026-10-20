"""One reserved native recovery, genuine publication, then ordinary continuation."""
from datetime import datetime, timezone
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'data/autonomous-next-v3/scale-30000-v1'
RUN_NAME = 'scale30k-content-2301-2400'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def ordinary_continuation(control_path, document):
    # Start after the genuine recovery receipt; never reinterpret/revisit the
    # failed window as passed. The first normal window uses four slots, and
    # the existing clean-four/eight rule controls later eight-slot windows.
    path = BASE / 'content-continuation-v2.py'
    if (Path(control_path).resolve() != (BASE / 'window-controls' / (RUN_NAME + '.json')).resolve()
            or hashlib.sha256(path.read_bytes()).hexdigest() != document['artifacts']['ordinary_driver']['sha256']
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != document['artifacts']['tail_driver']['sha256']):
        raise ValueError('Actual passed-control or ordinary-entry code binding differs')
    source = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    before = '    index = 0\n    prior = None'
    after = ("    index = 24\n    previous_control = BASE / 'window-controls/scale30k-content-2301-2400.json'\n"
             '    prior = verify_control_receipt(previous_control, sha(previous_control))')
    slots = 'concurrency=8 if index>=2 and clean and previous_run[\'final_concurrency\'] in (4,8) else min(4,max(2,previous_run[\'final_concurrency\']))'
    if source.count(before) != 1 or source.count(slots) != 1:
        raise ValueError('Pinned ordinary continuation entry anchor drift')
    source = source.replace(before, after).replace(slots, 'concurrency=(4 if index==24 else (' + slots.split('=', 1)[1] + '))')
    result = ModuleType('ordinary_after_single_recovery')
    result.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), result.__dict__)
    return result.main()


def execute(checkpoint, expected_sha256):
    from recommend_book.scale_content_recovery_v1 import (
        verify_recovery_checkpoint, read, sha, save, continue_after_recovery_gate)
    from recommend_book.scale_content_classification_v3 import create_reviewed_export
    verify_recovery_checkpoint(checkpoint, expected_sha256)
    document = read(checkpoint)
    reservation = read(Path(checkpoint).with_name('launch-reservation.json'))
    own = document['artifacts']['tail_driver']
    if (str(Path(__file__).resolve()) != own['path'] or sha(__file__) != own['sha256']
            or reservation['checkpoint_sha256'] != expected_sha256
            or reservation['tail_driver_path'] != own['path']
            or reservation['tail_driver_sha256'] != own['sha256']):
        raise ValueError('Reserved recovery tail code/checkpoint differs')
    save(Path(checkpoint).with_name('tail-process-entry.json'),
         {'pid': os.getpid(), 'checkpoint_sha256': expected_sha256, 'tail_driver_sha256': sha(__file__)})
    original = module('preserved_content_operations', document['artifacts']['ordinary_driver']['path'])
    operation = module('preserved_content_publication', document['artifacts']['operation_driver']['path'])
    parent = BASE / 'classification-exports' / RUN_NAME / 'manifest.json'
    public = BASE / 'classification-exports-v3' / RUN_NAME / 'manifest.json'
    control_path = BASE / 'window-controls' / (RUN_NAME + '.json')
    imported = BASE / 'catalogue-imports' / RUN_NAME / 'manifest.json'
    if any(path.exists() for path in (parent, public, control_path, imported)):
        raise ValueError('Recovery publication already exists; no replay or resume')
    original.observed_command([sys.executable, 'scripts/run_scale_content_recovery_v1.py',
        '--checkpoint', str(Path(checkpoint).resolve()), '--checkpoint-sha256', expected_sha256],
        BASE / 'operational-processes' / RUN_NAME / 'single-recovery-native-and-review')
    native = read(parent)
    terminal = Path(document['runtime_preparation']) / ('terminal-' + RUN_NAME)
    started, exited = read(terminal / 'process-start.json'), read(terminal / 'process-exit.json')
    pid_seconds = (datetime.fromisoformat(exited['observed_at']) -
                   datetime.fromisoformat(started['started_at'])).total_seconds()
    model_seconds = pid_seconds + native['separate_review']['elapsed_seconds']
    if model_seconds > 3600:
        raise ValueError('Recovery actual PID-inclusive model budget exceeded; stop without retry')
    original.emit(create_reviewed_export(parent, sha(parent), public.parent))
    lock = ROOT / 'data/autonomous-next-v3/catalogue-writer.lock'
    while lock.exists():
        original.emit({'stage': 'waiting_for_serial_catalogue_writer', 'lock': str(lock), 'run_name': RUN_NAME})
        time.sleep(30)
    receipt = operation.finalize(public, sha(public))
    public_doc = read(public); run = read(public_doc['run_manifest'])
    summary = {'run_name': RUN_NAME, 'public_manifest': str(public), 'public_manifest_sha256': sha(public),
        'native_manifest': str(parent), 'native_manifest_sha256': sha(parent), 'gate': receipt['gate'],
        'native_selected': run['selected'], 'native_completed': run['completed'], 'native_failed': run['failed'],
        'native_elapsed_seconds': run['elapsed_seconds'], 'native_usage': run['usage'],
        'native_terminal_pid_interval_seconds': pid_seconds, 'pid_inclusive_model_stage_seconds': model_seconds,
        'native_launch_through_public_import_wall_seconds': (datetime.now(timezone.utc) -
            datetime.fromisoformat(started['started_at'])).total_seconds(),
        'separate_review': public_doc['separate_review'],
        'actual_display_labels_by_axis': receipt['actual_display_labels_by_axis'],
        'actual_public_eligible_cumulative': original.public_eligible_count(),
        'recovery_checkpoint_sha256': expected_sha256, 'observed_at': datetime.now(timezone.utc).isoformat()}
    save(BASE / 'run-summaries' / (RUN_NAME + '.json'), summary)
    original.emit({'stage': 'actual_recovery_window_publicly_imported', 'summary': summary})
    # This always calls the unchanged ordinary v4 validator. A failed recovery
    # retains its import/failed receipt and cannot enter the callback.
    return continue_after_recovery_gate(control_path, sha(control_path),
        lambda _: ordinary_continuation(control_path, document))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    args = parser.parse_args()
    execute(args.checkpoint, args.checkpoint_sha256)
