"""Final read-only audit for the fresh v3 handoff; old auditors stay frozen."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3/scale-20000-classifications-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
PARENT = ROOT / 'scripts/audit_scale20k_catalogue_v2.py'
PARENT_SHA = '4cbf764c11e2950c306d93392ae22646fd0a630c35f8e29f53db7114ac4ba29d'
HOLD = BASE / 'recovery-gate161-v1/unstarted-preflight-hold-v1.json'
HOLD_SHA = '1302a61c0446a48dfa55d37b34a5d8555d6c6e59a9b1d4e53aa72b408332224d'
RESERVATION_SHA = 'c85b1e7e2dab114bb0e4e04a640b21fbee21dd65d349ee9f3b96e96d51faf26a'
OLD_GENERATION_CODE = {
    'src/recommend_book/scale20k_native_v2.py': '17c44e6d526bbea98095769741cd727a7b939e82fd3d8abf72bb68e77b265b2c',
    'src/recommend_book/scale20k_recovery_v1.py': 'bcf724351e1fd23787da819b88de198086d3d71aed569eaeb2b2fbd2011787c2',
    'scripts/run_scale20k_native_v3.py': '22c5c6fca32afcc3624a81e905f61ffcdfc5173c3b5c05e8dfe596095bc2931d',
    'scripts/prepare_scale20k_content_chunk_v2.py': '574b78b3895e80c997fd201bbff711b7b39af0e59b05d7177343033685a35d3a',
    'scripts/run_scale20k_content_classification_v2.py': 'df087b0326d81e3ed21061db43454dd7ceb685fef40d18785600c3b582ba3348',
    'scripts/prepare_scale20k_content_sources_v3.py': '550c73527c993546629d96f347a09f8a4501fa826cd560247cca40987b41889e',
}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pinned(pin):
    path = Path(pin['path']).resolve(strict=True)
    if sha(path) != pin['sha256']:
        raise ValueError('Immutable unstarted proof bytes differ: ' + str(path))
    return json.loads(path.read_bytes())


def _unstarted_qids(context):
    return set(context.recovery['pins']['excluded_unstarted_qids'])


def adapted_auditor(raw=None):
    """One SHA-fixed, counted namespace/offset adapter; no old file edits."""
    raw = PARENT.read_bytes() if raw is None else raw
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA:
        raise ValueError('Frozen audit2 bytes differ')
    text = raw.decode('utf-8')
    for before, after in (
        ("set(reservation['excluded_failed_qids']) != set(failed_qids)",
         "set(reservation['excluded_failed_qids']) != (set(failed_qids) | _unstarted_qids(self))"),
        ('set(identity) & set(failed_qids)', 'set(identity) & (set(failed_qids) | _unstarted_qids(self))'),
    ):
        if text.count(before) != 1:
            raise ValueError('Frozen exclusion adapter anchor differs')
        text = text.replace(before, after)
    changes = {
        'src/recommend_book/scale20k_native_v2.py': ('src/recommend_book/scale20k_native_v3.py', 1),
        'src/recommend_book/scale20k_recovery_v1.py': ('src/recommend_book/scale20k_recovery_v2.py', 2),
        'scripts/run_scale20k_native_v3.py': ('scripts/run_scale20k_native_v4.py', 5),
        'scripts/run_scale20k_content_classification_v2.py': ('scripts/run_scale20k_content_classification_v3.py', 1),
        'scripts/prepare_scale20k_content_chunk_v2.py': ('scripts/prepare_scale20k_content_chunk_v3.py', 1),
        'scripts/prepare_scale20k_content_sources_v3.py': ('scripts/prepare_scale20k_content_sources_v4.py', 1),
        'scale20k-native-attempt-reservation-v2': ('scale20k-native-attempt-reservation-v3', 1),
        'scale20k-source-recovery-eligibility-v3': ('scale20k-source-recovery-eligibility-v4', 1),
        'v2': ('v3', 1),
        r'scale20k-content-(\d{4,})-(\d{4,})-v2': (r'scale20k-content-(\d{4,})-(\d{4,})-v3', 1),
        'scale20k-catalogue-audit-v2': ('scale20k-catalogue-audit-v3', 2),
    }
    seen = {key: 0 for key in changes}
    counts = {'offset': 0, 'maximum': 0}

    class Configure(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str) and node.value in changes:
                seen[node.value] += 1
                node.value = changes[node.value][0]
            elif type(node.value) is int and node.value == 214:
                node.value = 213
                counts['maximum'] += 1
            return node

        def visit_BinOp(self, node):
            self.generic_visit(node)
            if (isinstance(node.left, ast.Name) and node.left.id == 'index' and isinstance(node.op, ast.Add)
                    and isinstance(node.right, ast.Constant) and node.right.value == 1):
                node.right.value = 2
                counts['offset'] += 1
            return node

    tree = Configure().visit(ast.parse(text))
    if seen != {key: count for key, (_, count) in changes.items()} or counts != {'offset': 4, 'maximum': 1}:
        raise ValueError('Frozen namespace/offset adapter boundaries differ: ' + str((seen, counts)))
    module = ModuleType('scale20k_final_audit_v3_adapter')
    module.__file__ = str(Path(__file__).resolve())
    module._unstarted_qids = _unstarted_qids
    exec(compile(ast.fix_missing_locations(tree), module.__file__, 'exec'), module.__dict__)
    module.OLD_CODE.update(OLD_GENERATION_CODE)
    module.OLD_CODE['scripts/audit_scale20k_catalogue_v2.py'] = PARENT_SHA
    old_loader = module.load_module

    def load_authenticated_module(path, expected):
        path = Path(path).resolve(strict=True)
        if path.parent != (ROOT / 'src/recommend_book').resolve():
            return old_loader(path, expected)
        if sha(path) != expected:
            raise ValueError('Frozen audit/recovery code differs: ' + str(path))
        # Fresh execution under its real package context supports helper2's
        # relative imports without trusting an already cached helper module.
        spec = importlib.util.spec_from_file_location('recommend_book.' + path.stem, path)
        result = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(result)
        if sha(path) != expected:
            raise ValueError('Frozen audit/recovery code changed during import')
        return result

    module.load_module = load_authenticated_module
    return module


def verify_unstarted_hold(database, recovery_pins):
    """Read existing pre-model failure evidence; never count it as native failure."""
    hold_pin = {'path': str(HOLD), 'sha256': HOLD_SHA}
    hold = pinned(hold_pin)
    expected_reservation = {'path': str(BASE / 'attempt-reservations/scale20k-content-6801-6900-v2.json'),
                            'sha256': RESERVATION_SHA}
    reservation = pinned(expected_reservation)
    qids, hashes = set(reservation['qids']), set(reservation['input_hashes'])
    failed_qids = set(recovery_pins['excluded_failed_attempt_qids'])
    if (hold['version'] != 'scale20k-unstarted-preflight-hold-v1'
            or hold['status'] != 'held_unstarted_runtime_import_failure'
            or hold['window_index'] != 161 or hold['run_name'] != 'scale20k-content-6801-6900-v2'
            or hold['reservation'] != expected_reservation or recovery_pins['unstarted_reservation'] != expected_reservation
            or hold['selected'] != 81 or len(reservation['qids']) != 81 or len(qids) != 81
            or len(reservation['input_hashes']) != 81 or len(hashes) != 81
            or set(hold['qids']) != qids or set(hold['input_hashes']) != hashes
            or set(recovery_pins['excluded_unstarted_qids']) != qids
            or len(failed_qids) != 15 or len(failed_qids | qids) != 96
            or any(type(hold[key]) is not int or hold[key] != 0 for key in
                   ('model_calls', 'same_inputs_retried', 'native_rows_for_selected'))
            or hold['eligible_before'] != 15952 or hold['eligible_after'] != 15952):
        raise ValueError('Actual unstarted81 hold/reservation/zero-model facts differ')
    if set(hold['processes']) != {'preflight', 'controller_worker', 'controller_observer', 'sequential_queue'}:
        raise ValueError('Actual unstarted preflight process chain is incomplete')
    for name, process in hold['processes'].items():
        start, end = pinned(process['start']), pinned(process['exit'])
        pid = start.get('pid')
        birth = start.get('process_created_at')
        if (type(pid) is not int or pid <= 0 or type(end.get('pid')) is not int or end['pid'] != pid
                or type(end.get('actual_exit_code')) is not int or end['actual_exit_code'] != 1
                or end.get('process_exit_observed') is not True
                or not isinstance(birth, str) or not birth or end.get('process_created_at') != birth):
            raise ValueError('Actual preflight stop PID/birth/exit differs: ' + name)
        if datetime.fromisoformat(birth).tzinfo is None:
            raise ValueError('Actual process birth must carry its timezone')
        stderr_path = process['stderr']['path']
        if sha(stderr_path) != process['stderr']['sha256']:
            raise ValueError('Actual preflight stderr changed')
        if name == 'preflight' and 'ImportError' not in Path(stderr_path).read_text(encoding='utf-8'):
            raise ValueError('The unstarted failure was not the saved ImportError')
    pinned(hold['preflight_request'])
    if not hold['confirmed_absent_artifacts'] or any(Path(path).exists() for path in hold['confirmed_absent_artifacts']):
        raise ValueError('Previously unstarted model/public artifacts are present')
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        remaining = db.execute('SELECT count(*) FROM native_classifications WHERE qid IN ('
            + ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]
    if remaining:
        raise ValueError('Unstarted81 authorities acquired native rows')
    return {'hold': hold_pin, 'reservation': expected_reservation, 'processes': hold['processes'],
            'selected': 81, 'model_calls': 0, 'native_failures': 0, 'catalogue_rows_remaining': 0,
            'failure_stage': 'preflight_before_model', 'historical_eligible_unchanged': 15952}


def audit(entry_path, entry_sha, *, database=DATABASE):
    parent = adapted_auditor()

    class RecoveryAudit(parent.RecoveryAudit):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.unstarted = verify_unstarted_hold(self.database, self.recovery['pins'])

    context = RecoveryAudit(entry_path, entry_sha, database=database)
    result = context.audit()
    result['parent_auditor_v2'] = {'path': str(PARENT), 'sha256': PARENT_SHA}
    result['excluded_unstarted_windows'] = [context.unstarted]
    if result['status'] == 'complete':
        try:
            verify_unstarted_hold(context.database, context.recovery['pins'])
        except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
            result['status'] = 'violations'
            result['issues'].append(str(exc))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-entry', type=Path, required=True)
    parser.add_argument('--recovery-entry-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = audit(args.recovery_entry, args.recovery_entry_sha256)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        result = {'version': 'scale20k-catalogue-audit-v3', 'status': 'violations', 'issues': [str(exc)], 'model_calls': 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'output': str(args.output)}))
    raise SystemExit(0 if result['status'] == 'complete' else 1)


if __name__ == '__main__':
    main()
