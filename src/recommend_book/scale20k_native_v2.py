"""Recovery-generation v2 authority for scale20k.

This module is a small façade over the frozen v1 state.  It changes the
namespace and run-name boundary only; the v1 validators and fixed recipe
bytes remain dependencies and are never rewritten.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import types

from . import scale20k_native_v1 as _v1

ROOT, BASE, RUNTIME, DATABASE, WRITER_LOCK = _v1.ROOT, _v1.BASE, _v1.RUNTIME, _v1.DATABASE, _v1.WRITER_LOCK
REQUIREMENTS_SHA, BASELINE_SHA, WORKS_BASELINE_SHA, PLAN_SHA = (
    _v1.REQUIREMENTS_SHA, _v1.BASELINE_SHA, _v1.WORKS_BASELINE_SHA, _v1.PLAN_SHA)
PREPARER_SHA, COORDINATOR_SHA = _v1.PREPARER_SHA, _v1.COORDINATOR_SHA
REVIEW_SHA, TERMINAL_SHA, OPERATION_SHA = _v1.REVIEW_SHA, _v1.TERMINAL_SHA, _v1.OPERATION_SHA
PRIOR_CONTROL = BASE / 'window-controls/scale20k-content-6701-6800-v1.json'
PRIOR_CONTROL_SHA = 'd314c5084acab3b394c41a2e28a5527e4b2649561a4e03852a7cba1702971691'
FAILED_CONTROL = BASE / 'window-controls/scale20k-content-6801-6900-v1.json'
FAILED_CONTROL_SHA = '073dfb95d9c0fdb70c7f58c7638779ad940696b9ccfcab3a45ef1e542c4cec6a'
RECOVERY_GENERATION = 'scale20k-recovery-gate161-v1'
BUILDER_NAME = 'prepare_scale20k_content_sources_v3.py'
ACTIVE_RECOVERY_ENTRY = ((Path(os.environ['SCALE20K_RECOVERY_ENTRY']).resolve(),
                          os.environ['SCALE20K_RECOVERY_ENTRY_SHA256'])
                         if os.environ.get('SCALE20K_RECOVERY_ENTRY')
                         and os.environ.get('SCALE20K_RECOVERY_ENTRY_SHA256') else None)
ORIGINAL_STATE_V1_SHA = 'dde58428553eb9ed1f16e8ed0fb4c7932c8aafb19debafc9aa5cbbca887030e1'

if _v1.sha(_v1.__file__) != ORIGINAL_STATE_V1_SHA:
    raise ValueError('Frozen scale20k v1 state bytes changed')

SourceSupplyExhausted = _v1.SourceSupplyExhausted
sha, read, save = _v1.sha, _v1.read, _v1.save
writer_lock, process_created_at = _v1.writer_lock, _v1.process_created_at
verify_requirements, load_inventory = _v1.verify_requirements, _v1.load_inventory
validate_baseline_membership, eligible_count = _v1.validate_baseline_membership, _v1.eligible_count


def catalogue_snapshot(database, qids):
    """Read the bounded source metadata snapshot used by v2 policy checks."""
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


def canonical_run_name(global_index):
    if type(global_index) is not int or global_index < 161:
        raise ValueError('Recovery global window must be an integer at least 161')
    start = 6801 + (global_index - 161) * 100
    return f'scale20k-content-{start:04d}-{start + 99:04d}-v2'


def source_entry(path, expected_sha, global_index):
    if sha(path) != expected_sha:
        raise ValueError('Independent recovery source index bytes differ')
    value = read(path)
    if (value.get('version') not in ('scale20k-source-index-v2', 'scale20k-recovery-source-index-v2')
            or value.get('requirements_sha256') != REQUIREMENTS_SHA):
        raise ValueError('Recovery source index authority differs')
    rows = value.get('windows', [])
    indices = [row.get('window_index') for row in rows]
    if (any(type(index) is not int or index < 161 for index in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError('Recovery source indices must be unique integers from 161')
    for row in rows:
        if row.get('window_index') == global_index:
            if row.get('run_name') not in (None, canonical_run_name(global_index)):
                raise ValueError('Recovery source run name differs from v2 canonical name')
            if row.get('recovery_generation') not in (None, RECOVERY_GENERATION):
                raise ValueError('Recovery source index generation differs')
            if row.get('upstream_window_index') not in (None, global_index + 1):
                raise ValueError('Recovery source index upstream lineage differs')
            if row.get('source_path') is None or row.get('source_sha256') is None:
                raise ValueError('Recovery source row must pin source bytes')
            return row
    raise SourceSupplyExhausted('Next recovery window is not in the fixed source handoff')


def current_source(source, global_index, expected_sha, inventory):
    # Reuse the complete v1 input/mapping/database identity proof while
    # substituting only the immutable canonical-name boundary.
    original = _v1.canonical_run_name
    _v1.canonical_run_name = canonical_run_name
    try:
        fixed, hashes, qids = _v1.current_source(source, global_index, expected_sha, inventory)
    finally:
        _v1.canonical_run_name = original
    if fixed.get('recovery_generation') not in (None, RECOVERY_GENERATION):
        raise ValueError('Source belongs to a different recovery generation')
    if fixed.get('recovery_generation') is None:
        raise ValueError('Recovery source manifest must carry its generation seal')
    if fixed.get('upstream_window_index') != global_index + 1:
        raise ValueError('Recovery source must identify its consumed v1 source window')
    policy_path = source / 'native-recovery-policy-v3.json'
    if not policy_path.is_file():
        raise ValueError('Recovery source must carry the independently observed v3 lineage policy')
    policy = read(policy_path)
    if (policy.get('version') != 'scale20k-source-recovery-eligibility-v3'
            or policy.get('recovery_generation') != RECOVERY_GENERATION
            or policy.get('new_window_index') != global_index
            or policy.get('new_run_name') != canonical_run_name(global_index)
            or policy.get('upstream_window_index') != global_index + 1
            or policy.get('source_manifest_sha256') != sha(source / 'manifest.json')
            or policy.get('model_calls') != 0 or policy.get('network_requests') != 0
            or policy.get('metadata_mutations') != 0
            or policy.get('old_source_and_index_modified') is not False):
        raise ValueError('Recovery source lineage/observation policy differs')
    if (policy.get('selected') != len(qids)
            or set(policy.get('qids', [])) != set(qids)
            or policy.get('catalogue_before') != catalogue_snapshot(DATABASE, qids)
            or policy.get('catalogue_after') != policy.get('catalogue_before')):
        raise ValueError('Recovery source catalogue metadata observation differs')
    builder_path = ROOT / 'scripts' / BUILDER_NAME
    observer_path = Path(policy.get('observer_path', '')).resolve(strict=True)
    if (policy.get('source_builder_path') != str(builder_path.resolve())
            or policy.get('source_builder_sha256') != sha(builder_path)
            or policy.get('observer_sha256') != sha(observer_path)):
        raise ValueError('Recovery source builder/observer code pin differs')
    origin = policy.get('origin')
    if (not isinstance(origin, dict) or origin.get('window_index') != global_index + 1
            or origin.get('run_name') != _v1.canonical_run_name(global_index + 1)
            or not isinstance(origin.get('source_path'), str)
            or not isinstance(origin.get('source_sha256'), str)
            or not isinstance(origin.get('index_path'), str)
            or not isinstance(origin.get('index_sha256'), str)):
        raise ValueError('Recovery source must carry a complete upstream origin pin')
    origin_manifest = Path(origin['source_path']).resolve(strict=True) / 'manifest.json'
    origin_index = Path(origin['index_path']).resolve(strict=True)
    if sha(origin_manifest) != origin['source_sha256'] or sha(origin_index) != origin['index_sha256']:
        raise ValueError('Recovery upstream origin bytes differ from its policy pins')
    return fixed, hashes, qids


def _recovery_pins():
    from .scale20k_recovery_v1 import verify_recovery_entry
    if ACTIVE_RECOVERY_ENTRY is None:
        raise ValueError('Recovery entry must be sealed before reserving a v2 window')
    path, expected = ACTIVE_RECOVERY_ENTRY
    return verify_recovery_entry(path, expected)


def _excluded_qids(result):
    return set(result['pins']['failed_qids'])


def _excluded_hashes(result):
    # Source manifests also carry model input hashes.  A receipt may pin them
    # explicitly; qid exclusion remains mandatory even when hashes are absent.
    hashes = result['entry'].get('failed_input_hashes', [])
    if not isinstance(hashes, list) or len(set(hashes)) != len(hashes):
        raise ValueError('Failed input hashes must be a distinct list')
    return set(hashes)


def reserve_window(source, global_index, source_sha, inventory_path, inventory_sha,
                   source_index_path, source_index_sha):
    inventory = load_inventory(inventory_path, inventory_sha)
    recovery = _recovery_pins()
    entry = source_entry(source_index_path, source_index_sha, global_index)
    if (Path(entry['source_path']).resolve(strict=True) != Path(source).resolve(strict=True)
            or entry['source_sha256'] != source_sha):
        raise ValueError('Selected recovery window differs from its fixed source index')
    fixed, hashes, qids = current_source(source, global_index, source_sha, inventory)
    excluded_qids = _excluded_qids(recovery)
    excluded_hashes = _excluded_hashes(recovery)
    if qids & excluded_qids or hashes & excluded_hashes:
        raise ValueError('A failed global-161 input is present in the recovery source')
    failed_reservation = BASE / 'attempt-reservations/scale20k-content-6801-6900-v1.json'
    failed_attempt_qids = set(read(failed_reservation).get('qids', [])) if failed_reservation.is_file() else set()
    name = fixed['chunks'][0]['run_name']
    for candidate in (RUNTIME / ('terminal-' + name), RUNTIME / ('preflight.' + name + '.json'),
                      RUNTIME / 'runtime/data/autonomous-next-v3/classification' / name):
        if candidate.exists():
            raise ValueError('Recovery window was already prepared or attempted')
    path = BASE / 'attempt-reservations' / (name + '.json')
    value = {'version': 'scale20k-native-attempt-reservation-v2', 'generation': RECOVERY_GENERATION,
             'global_index': global_index, 'run_name': name,
             'source_path': str(Path(source).resolve()), 'source_sha256': source_sha,
             'inventory_path': str(Path(inventory_path).resolve()), 'inventory_sha256': inventory_sha,
             'source_index_path': str(Path(source_index_path).resolve()), 'source_index_sha256': source_index_sha,
             'input_hashes': sorted(hashes), 'qids': sorted(qids), 'actual_overlap_count': 0,
             'recovery_entry': recovery['pins']['entry'],
             'excluded_failed_qids': sorted(excluded_qids | failed_attempt_qids),
             'excluded_failed_input_hashes': sorted(excluded_hashes), 'helper_sha256': sha(__file__),
             'requirements_sha256': REQUIREMENTS_SHA, 'batch_size': 1}
    with writer_lock(purpose='v20_recovery_native_attempt_reservation'):
        old_hashes, old_qids = set(inventory['input_hashes']), set(inventory['qids'])
        for old in (BASE / 'attempt-reservations').glob('*.json'):
            receipt = read(old)
            old_hashes.update(receipt.get('input_hashes', [])); old_qids.update(receipt.get('qids', []))
        if hashes & old_hashes or qids & old_qids:
            raise ValueError('Already attempted input or authority; preserve old failure/hold')
        with sqlite3.connect(DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
            if qids and db.execute('SELECT count(*) FROM native_classifications WHERE qid IN ('
                    + ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]:
                raise ValueError('Existing native authority cannot be replaced')
        save(path, value)
    return path


def verify_reservation(source, global_index):
    if sha(BASE / 'requirements.json') != REQUIREMENTS_SHA:
        raise ValueError('Frozen v20 requirements changed before recovery launch')
    fixed = read(Path(source) / 'manifest.json')
    path = BASE / 'attempt-reservations' / (fixed['chunks'][0]['run_name'] + '.json')
    value = read(path)
    recovery_pin = value.get('recovery_entry')
    if (not isinstance(recovery_pin, dict) or not isinstance(recovery_pin.get('path'), str)
            or not isinstance(recovery_pin.get('sha256'), str)):
        raise ValueError('Reservation must carry an independently pinned recovery entry')
    from .scale20k_recovery_v1 import verify_recovery_entry
    verified_recovery = verify_recovery_entry(recovery_pin['path'], recovery_pin['sha256'])
    if ACTIVE_RECOVERY_ENTRY is not None:
        active_path, active_sha = ACTIVE_RECOVERY_ENTRY
        if Path(recovery_pin['path']).resolve() != active_path.resolve() or recovery_pin['sha256'] != active_sha:
            raise ValueError('Reservation recovery entry differs from the controller sealed entry')
    failed_reservation = BASE / 'attempt-reservations/scale20k-content-6801-6900-v1.json'
    failed_qids = set(read(failed_reservation).get('qids', [])) if failed_reservation.is_file() else set()
    inventory = load_inventory(value['inventory_path'], value['inventory_sha256'])
    entry = source_entry(value['source_index_path'], value['source_index_sha256'], global_index)
    fixed, hashes, qids = current_source(source, global_index, value['source_sha256'], inventory)
    if (value.get('version') != 'scale20k-native-attempt-reservation-v2'
            or value.get('generation') != RECOVERY_GENERATION or value.get('global_index') != global_index
            or value.get('source_path') != str(Path(source).resolve())
            or value.get('run_name') != fixed['chunks'][0]['run_name']
            or value.get('input_hashes') != sorted(hashes) or value.get('qids') != sorted(qids)
            or not set(verified_recovery['pins']['failed_qids']).issubset(set(value.get('excluded_failed_qids', [])))
            or set(qids) & failed_qids
            or value.get('actual_overlap_count') != 0 or value.get('batch_size') != 1
            or value.get('helper_sha256') != sha(__file__) or value.get('requirements_sha256') != REQUIREMENTS_SHA
            or Path(entry['source_path']).resolve(strict=True) != Path(source).resolve(strict=True)
            or entry['source_sha256'] != value['source_sha256']
            or (RUNTIME / ('terminal-' + value['run_name'])).exists()):
        raise ValueError('Exact unstarted recovery reservation binding differs')
    return [path, Path(value['inventory_path']), Path(value['source_index_path']),
            Path(recovery_pin['path']), BASE / 'requirements.json', BASE / 'baseline-native-10000.jsonl', *code_paths()]


def code_paths():
    names = ('run_scale20k_native_v3.py', 'prepare_scale20k_content_chunk_v2.py',
             'run_scale20k_content_classification_v2.py', BUILDER_NAME,
             'prepare_scale100k_content_chunk_v1.py', 'prepare_scale100k_content_chunk_v2.py',
             'prepare_scale_content_chunk_v1.py', 'prepare_scale_content_chunk_v4.py',
             'prepare_scale_classification_v1.py', 'native_snapshot_terminal_v1.py',
             'run_scale_content_classification_v6.py', 'run_scale100k_content_classification_v1.py',
             'run_scale100k_content_classification_v2.py', 'run_scale100k_content_classification_v3.py',
             'run_scale100k_content_classification_v4.py', 'run_scale100k_content_classification_v5.py',
             'review_scale_content_v6.py')
    paths = [Path(__file__).resolve(), ROOT / 'src/recommend_book/scale20k_recovery_v1.py',
             Path(_v1.__file__).resolve()]
    for name in names:
        candidate = ROOT / 'scripts' / name
        if not candidate.is_file():
            raise FileNotFoundError('Recovery code pin is missing: ' + str(candidate))
        paths.append(candidate)
    paths.append(ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py')
    return paths


def code_snapshot():
    return {str(path): sha(path) for path in code_paths()}


def verify_code_snapshot(snapshot):
    for path, expected in snapshot.items():
        if sha(path) != expected:
            raise ValueError('Recovery classification recipe changed during window: ' + path)


def instrument_terminal_source(text):
    original = _v1.canonical_run_name
    _v1.canonical_run_name = canonical_run_name
    try:
        return _v1.instrument_terminal_source(text)
    finally:
        _v1.canonical_run_name = original


@contextmanager
def _recipe_aliases():
    old_state = sys.modules.get('recommend_book.scale20k_native_v1')
    sys.modules['recommend_book.scale20k_native_v1'] = sys.modules[__name__]
    try:
        yield
    finally:
        if old_state is None:
            sys.modules.pop('recommend_book.scale20k_native_v1', None)
        else:
            sys.modules['recommend_book.scale20k_native_v1'] = old_state


def load_recipe(kind, owner_path):
    if sha(_v1.__file__) != ORIGINAL_STATE_V1_SHA:
        raise ValueError('Frozen scale20k v1 state bytes changed')
    with _recipe_aliases():
        recipe = _v1.load_recipe(kind, owner_path)
    for name in ('execute_window', 'main', 'prepare', 'frozen_prepare', 'trial_control'):
        fn = getattr(recipe, name, None)
        if fn is None:
            continue
        def wrapped(*args, _fn=fn, **kwargs):
            with _recipe_aliases():
                return _fn(*args, **kwargs)
        setattr(recipe, name, wrapped)
    if kind == 'preparer':
        wrapped_prepare = getattr(recipe, 'prepare', None)
        original_prepare = (wrapped_prepare.__kwdefaults__ or {}).get('_fn') if wrapped_prepare else None
        if original_prepare is not None:
            def rewrite(code):
                constants = []
                for value in code.co_consts:
                    if isinstance(value, types.CodeType):
                        value = rewrite(value)
                    elif isinstance(value, str):
                        value = value.replace('prepare_scale20k_content_chunk_v1',
                                              'prepare_scale20k_content_chunk_v2')
                    constants.append(value)
                return code.replace(co_consts=tuple(constants))
            rebound = types.FunctionType(rewrite(original_prepare.__code__), original_prepare.__globals__,
                                         original_prepare.__name__, original_prepare.__defaults__,
                                         original_prepare.__closure__)
            rebound.__kwdefaults__ = original_prepare.__kwdefaults__
            wrapped_prepare.__kwdefaults__['_fn'] = rebound
    return recipe
