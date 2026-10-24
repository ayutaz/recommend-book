"""Bounded recovery controller for global 161 and the remaining v2 windows.

The frozen v1 controller is expanded in memory with two substitutions: the
v2 state façade and the v2 preparer/coordinator entry names.  The original
source bytes are hash checked before expansion and remain untouched.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import ModuleType

from recommend_book import scale20k_native_v3 as state

ROOT = Path(__file__).resolve().parents[1]
DRIVER_V1_SHA = '59ceada510c757c9bba223af8a4affdb4d6f6c767410261042940774ed9e59f7'
BUILDER_NAME = state.BUILDER_NAME
POLICY = 'novel_content_with_display_permission_source_genre_optional'


def observed_command(argv, directory, *, env=None, timeout=None, stdout_name='stdout.txt', request_fields=None):
    """Run one bounded child and retain actual start/exit witnesses."""
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


def eligible_native_count(database):
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) '
                          'WHERE w.display_allowed=1').fetchone()[0]


def catalogue_snapshot(database, qids):
    qids = sorted(set(qids))
    if not qids or len(qids) > 100:
        raise ValueError('One bounded actual selected authority set required')
    marks = ','.join('?' for _ in qids)
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        result = {qid: {'version_id': version, 'display_allowed': display, 'recommendable': recommendable,
                        'genres': [], 'native': None} for qid, version, display, recommendable in db.execute(
            'SELECT qid,version_id,display_allowed,recommendable FROM works WHERE qid IN (' + marks + ')', qids)}
        if set(result) != set(qids):
            raise ValueError('Selected authority absent from catalogue')
        for qid, genre, label in db.execute('SELECT qid,genre_qid,label FROM work_genres WHERE qid IN ('
                + marks + ') ORDER BY qid,genre_qid,label', qids):
            result[qid]['genres'].append([genre, label])
        for qid, manifest, digest, labels in db.execute('SELECT qid,manifest_path,manifest_sha256,labels_json '
                'FROM native_classifications WHERE qid IN (' + marks + ')', qids):
            result[qid]['native'] = {'manifest_path': manifest, 'manifest_sha256': digest, 'labels': json.loads(labels)}
    return result


def require_new_display_works(snapshot):
    for row in snapshot.values():
        if row['display_allowed'] != 1 or row['native'] is not None:
            raise ValueError('Only new display-authorized authorities may be classified')
        if not row['genres'] and row['recommendable'] != 0:
            raise ValueError('Unknown source genre must remain source recommendable=0')


def genre_counts(snapshot):
    known = sum(bool(row['genres']) for row in snapshot.values())
    return {'source_genre_known': known, 'source_genre_unknown': len(snapshot) - known}


def validate_import_observation(before, after, expected_rows, export, expected_sha):
    if set(before) != set(after):
        raise ValueError('Actual selected authority set changed during import')
    expected = {row['qid']: row['admitted_labels'] for row in expected_rows}
    if len(expected) != len(expected_rows) or not set(expected) <= set(before):
        raise ValueError('Admitted authority set differs from selected works')
    require_new_display_works(before)
    for qid, original in before.items():
        current = after[qid]
        if {k: v for k, v in original.items() if k != 'native'} != {k: v for k, v in current.items() if k != 'native'}:
            raise ValueError('Source metadata changed during native import')
        wanted = ({'manifest_path': str(Path(export).resolve()), 'manifest_sha256': expected_sha,
                   'labels': expected[qid]} if qid in expected else None)
        if current['native'] != wanted:
            raise ValueError('Actual imported manifest or labels differ from authentic projection')


def _load_frozen_controller():
    # Expand the existing v2 eligibility controller.  Its load_controller()
    # still authenticates and parses the frozen v1 driver, while retaining the
    # display-policy observation and import/publication guards.
    path = ROOT / 'scripts/run_scale20k_native_v2.py'
    expected = 'ba52758a2026b8e46b3ffd5f9332672a5c893974167bfafda626ab84d8cf4f1b'
    if state.sha(path) != expected:
        raise ValueError('Frozen v2 eligibility controller bytes changed')
    tree = ast.parse(path.read_bytes())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == 'recommend_book':
            for alias in node.names:
                if alias.name == 'scale20k_native_v1':
                    alias.name = 'scale20k_native_v3'
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = (node.value.replace('prepare_scale20k_content_sources_v2.py', BUILDER_NAME)
                          .replace('native-eligibility-policy.json', 'native-recovery-policy-v4.json')
                          .replace('scale20k-source-display-eligibility-v2',
                                   'scale20k-source-recovery-eligibility-v4'))
    module = ModuleType('scale20k_native_v3_expanded_for_recovery')
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    controller = module.load_controller()
    original_observed = controller.observed_command

    def observed_command(argv, *args, **kwargs):
        rewritten = [str(item).replace('run_scale20k_content_classification_v1.py',
                              'run_scale20k_content_classification_v3.py') for item in argv]
        return original_observed(rewritten, *args, **kwargs)

    controller.observed_command = observed_command
    controller.verify_policy_control = module.verify_policy_control
    return controller


def verify_policy_control(path, expected_sha):
    """Verify the v2 display-policy receipt using the same public interface as v2."""
    # Reuse the complete v2 policy verifier (controller, builder, preflight,
    # source observation and import metadata), with its fixed state rebound to
    # the v2 façade by _load_frozen_controller().
    return _load_frozen_controller().verify_policy_control(path, expected_sha)


def _verify_recovery_args(args):
    from recommend_book.scale20k_recovery_v2 import verify_recovery_entry
    if not args.recovery_entry or not args.recovery_entry_sha256:
        raise ValueError('A sealed recovery entry and SHA256 are required before any v2 launch')
    result = verify_recovery_entry(args.recovery_entry, args.recovery_entry_sha256)
    if 'process_witnesses' not in result['pins']:
        raise ValueError('Recovery launch requires the actual failed/quarantine process witness chain')
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    prior_path = args.prior_control.resolve()
    if prior_path == state.PRIOR_CONTROL.resolve():
        if args.prior_control_sha256 != state.PRIOR_CONTROL_SHA:
            raise ValueError('Recovery bootstrap must use the genuine passed global-160 control')
    else:
        if (not prior_path.is_relative_to(state.BASE.resolve())
                or prior_path.parent != (state.BASE / 'window-controls').resolve()
                or args.prior_control_sha256 == state.FAILED_CONTROL_SHA):
            raise ValueError('Continuation parent must be a passed v2 control in the frozen namespace')
        receipt = verify_control_receipt(prior_path, args.prior_control_sha256)
        if receipt.get('window_index', 0) < 161 or receipt.get('gate', {}).get('passed') is not True:
            raise ValueError('Continuation parent must be an actually passed v2 control')
        if not (str(receipt.get('run_id', '')).endswith(('-v2', '-v3'))
                or any(suffix in prior_path.name for suffix in ('-v2', '-v3'))):
            raise ValueError('Continuation parent must use a recovery canonical run name')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-entry', type=Path,
                        default=Path(__import__('os').environ['SCALE20K_RECOVERY_ENTRY'])
                        if __import__('os').environ.get('SCALE20K_RECOVERY_ENTRY') else None)
    parser.add_argument('--recovery-entry-sha256',
                        default=__import__('os').environ.get('SCALE20K_RECOVERY_ENTRY_SHA256'))
    parser.add_argument('--prior-control', type=Path, default=state.PRIOR_CONTROL)
    parser.add_argument('--prior-control-sha256', default=state.PRIOR_CONTROL_SHA)
    args, remaining = parser.parse_known_args()
    if not args.recovery_entry or not args.recovery_entry_sha256:
        parser.error('Recovery entry and SHA256 are required (or must be inherited by the worker)')
    if len(args.recovery_entry_sha256) != 64 or any(c not in '0123456789abcdef' for c in args.recovery_entry_sha256):
        parser.error('Recovery SHA256 must be lower-case hexadecimal')
    _verify_recovery_args(args)
    state.ACTIVE_RECOVERY_ENTRY = (args.recovery_entry.resolve(), args.recovery_entry_sha256)
    import os
    os.environ['SCALE20K_RECOVERY_ENTRY'] = str(args.recovery_entry.resolve())
    os.environ['SCALE20K_RECOVERY_ENTRY_SHA256'] = args.recovery_entry_sha256
    module = _load_frozen_controller()
    original_next = module.next_concurrency
    first_call = True

    def recovery_slots(previous_run, *, first):
        nonlocal first_call
        force_first = first_call
        first_call = False
        return original_next(previous_run, first=(first or force_first))

    module.next_concurrency = recovery_slots
    original_argv = sys.argv
    try:
        # parse_known_args consumed these two flags.  Reinsert them so the
        # frozen controller and its worker receive the exact selected parent.
        forwarded = [*remaining, '--prior-control', str(args.prior_control.resolve()),
                     '--prior-control-sha256', args.prior_control_sha256]
        sys.argv = [original_argv[0], *forwarded]
        module.main()
    finally:
        sys.argv = original_argv


if __name__ == '__main__':
    main()

