"""Second fresh-handoff state: old global 163..215 mapped to v3 161..213."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys

from . import scale20k_native_v1 as _v1
from . import scale20k_native_v2 as _v2
from . import scale20k_recovery_v2 as recovery

ROOT, BASE, RUNTIME, DATABASE, WRITER_LOCK = _v1.ROOT, _v1.BASE, _v1.RUNTIME, _v1.DATABASE, _v1.WRITER_LOCK
REQUIREMENTS_SHA, BASELINE_SHA, WORKS_BASELINE_SHA, PLAN_SHA = _v1.REQUIREMENTS_SHA, _v1.BASELINE_SHA, _v1.WORKS_BASELINE_SHA, _v1.PLAN_SHA
PREPARER_SHA, COORDINATOR_SHA = _v1.PREPARER_SHA, _v1.COORDINATOR_SHA
REVIEW_SHA, TERMINAL_SHA, OPERATION_SHA = _v1.REVIEW_SHA, _v1.TERMINAL_SHA, _v1.OPERATION_SHA
PRIOR_CONTROL, PRIOR_CONTROL_SHA = _v2.PRIOR_CONTROL, _v2.PRIOR_CONTROL_SHA
FAILED_CONTROL_SHA = recovery._v1.FAILED_CONTROL_SHA
RECOVERY_GENERATION = 'scale20k-recovery-gate161-v2'
BUILDER_NAME = 'prepare_scale20k_content_sources_v4.py'
ACTIVE_RECOVERY_ENTRY = ((Path(os.environ['SCALE20K_RECOVERY_ENTRY']).resolve(), os.environ['SCALE20K_RECOVERY_ENTRY_SHA256'])
                         if os.environ.get('SCALE20K_RECOVERY_ENTRY') and os.environ.get('SCALE20K_RECOVERY_ENTRY_SHA256') else None)
SourceSupplyExhausted = _v1.SourceSupplyExhausted
sha, read, save = _v1.sha, _v1.read, _v1.save
writer_lock, process_created_at = _v1.writer_lock, _v1.process_created_at
verify_requirements, load_inventory = _v1.verify_requirements, _v1.load_inventory
validate_baseline_membership, eligible_count = _v1.validate_baseline_membership, _v1.eligible_count
catalogue_snapshot = _v2.catalogue_snapshot


def canonical_run_name(global_index):
    if type(global_index) is not int or not 161 <= global_index <= 213:
        raise ValueError('Recovery v3 global window must be 161..213')
    start = 6801 + (global_index - 161) * 100
    return f'scale20k-content-{start:04d}-{start + 99:04d}-v3'


def source_entry(path, expected_sha, global_index):
    if sha(path) != expected_sha:
        raise ValueError('Recovery v3 source index SHA differs')
    document = read(path)
    if document.get('requirements_sha256') != REQUIREMENTS_SHA or document.get('version') not in ('scale20k-source-index-v3', 'scale20k-recovery-source-index-v3'):
        raise ValueError('Recovery v3 source index authority differs')
    rows = document.get('windows', [])
    if len({row.get('window_index') for row in rows}) != len(rows):
        raise ValueError('Recovery v3 source indices must be unique')
    for row in rows:
        if row.get('window_index') == global_index:
            if row.get('run_name') != canonical_run_name(global_index) or row.get('upstream_window_index') != global_index + 2:
                raise ValueError('Recovery v3 source/upstream lineage differs')
            return row
    raise SourceSupplyExhausted('Recovery v3 source handoff exhausted')


def current_source(source, global_index, expected_sha, inventory):
    original = _v1.canonical_run_name
    _v1.canonical_run_name = canonical_run_name
    try:
        fixed, hashes, qids = _v1.current_source(source, global_index, expected_sha, inventory)
    finally:
        _v1.canonical_run_name = original
    if fixed.get('recovery_generation') != RECOVERY_GENERATION or fixed.get('upstream_window_index') != global_index + 2:
        raise ValueError('Recovery v3 source manifest lineage differs')
    policies = [source / 'native-recovery-policy-v4.json']
    policy_path = next((path for path in policies if path.is_file()), None)
    if policy_path is None:
        raise ValueError('Recovery v3 source policy is missing')
    policy = read(policy_path)
    if (policy.get('recovery_generation') != RECOVERY_GENERATION or policy.get('new_window_index') != global_index
            or policy.get('new_run_name') != canonical_run_name(global_index)
            or policy.get('upstream_window_index') != global_index + 2
            or policy.get('source_manifest_sha256') != sha(source / 'manifest.json')
            or policy.get('model_calls') != 0 or policy.get('network_requests', policy.get('web_requests')) != 0
            or policy.get('metadata_mutations') != 0):
        raise ValueError('Recovery v3 source policy differs')
    return fixed, hashes, qids


def _entry():
    if ACTIVE_RECOVERY_ENTRY is None:
        raise ValueError('Recovery v2 entry must be sealed before reservation')
    return recovery.verify_recovery_entry(*ACTIVE_RECOVERY_ENTRY)


def reserve_window(source, global_index, source_sha, inventory_path, inventory_sha, source_index_path, source_index_sha):
    inventory = load_inventory(inventory_path, inventory_sha)
    receipt = _entry()
    indexed = source_entry(source_index_path, source_index_sha, global_index)
    if Path(indexed['source_path']).resolve(strict=True) != Path(source).resolve(strict=True) or indexed['source_sha256'] != source_sha:
        raise ValueError('Recovery v3 source differs from index')
    fixed, hashes, qids = current_source(source, global_index, source_sha, inventory)
    with sqlite3.connect(DATABASE.resolve().as_uri() + '?mode=ro', uri=True) as db:
        if qids and db.execute('SELECT count(*) FROM native_classifications WHERE qid IN (' + ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]:
            raise ValueError('Existing native authority cannot be replaced')
    failed_reservation = recovery.FAILED_RESERVATION
    if recovery.sha(failed_reservation) != recovery.FAILED_RESERVATION_SHA:
        raise ValueError('Failed reservation bytes differ before recovery launch')
    failed_document = read(failed_reservation)
    failed_hashes = set(failed_document.get('input_hashes', []))
    excluded = set(receipt['pins']['excluded_failed_attempt_qids']) | set(receipt['pins']['excluded_unstarted_qids'])
    if qids & excluded or hashes & failed_hashes or hashes & set(receipt['pins'].get('excluded_failed_input_hashes', [])):
        raise ValueError('Recovery v3 source overlaps failed or held input')
    name = fixed['chunks'][0]['run_name']
    for candidate in (RUNTIME / ('terminal-' + name), RUNTIME / ('preflight.' + name + '.json'),
                      RUNTIME / 'runtime/data/autonomous-next-v3/classification' / name):
        if candidate.exists():
            raise ValueError('Recovery window was already prepared or attempted')
    path = BASE / 'attempt-reservations' / (name + '.json')
    if path.exists():
        raise ValueError('Recovery v3 reservation already exists')
    value = {'version': 'scale20k-native-attempt-reservation-v3', 'generation': RECOVERY_GENERATION,
             'global_index': global_index, 'run_name': name, 'source_path': str(Path(source).resolve()),
             'source_sha256': source_sha, 'inventory_path': str(Path(inventory_path).resolve()), 'inventory_sha256': inventory_sha,
             'source_index_path': str(Path(source_index_path).resolve()), 'source_index_sha256': source_index_sha,
             'input_hashes': sorted(hashes), 'qids': sorted(qids), 'actual_overlap_count': 0,
             'recovery_entry': receipt['pins']['entry'], 'excluded_failed_qids': sorted(excluded),
             'excluded_failed_input_hashes': sorted(receipt['pins'].get('excluded_failed_input_hashes', [])),
             'helper_sha256': sha(__file__), 'requirements_sha256': REQUIREMENTS_SHA, 'batch_size': 1}
    with writer_lock(purpose='v20_recovery_v3_native_attempt_reservation'):
        old_hashes, old_qids = set(inventory['input_hashes']), set(inventory['qids'])
        for old in (BASE / 'attempt-reservations').glob('*.json'):
            old_value = read(old); old_hashes.update(old_value.get('input_hashes', [])); old_qids.update(old_value.get('qids', []))
        if hashes & old_hashes or qids & old_qids:
            raise ValueError('Recovery v3 input was already attempted')
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
    receipt = recovery.verify_recovery_entry(recovery_pin['path'], recovery_pin['sha256'])
    if ACTIVE_RECOVERY_ENTRY is not None and (Path(recovery_pin['path']).resolve() != ACTIVE_RECOVERY_ENTRY[0].resolve()
                                               or recovery_pin['sha256'] != ACTIVE_RECOVERY_ENTRY[1]):
        raise ValueError('Reservation recovery entry differs from the active sealed entry')
    inventory = load_inventory(value['inventory_path'], value['inventory_sha256'])
    indexed = source_entry(value['source_index_path'], value['source_index_sha256'], global_index)
    fixed, hashes, qids = current_source(source, global_index, value['source_sha256'], inventory)
    failed_reservation = recovery.FAILED_RESERVATION
    if recovery.sha(failed_reservation) != recovery.FAILED_RESERVATION_SHA:
        raise ValueError('Failed reservation bytes differ before recovery launch')
    failed_document = read(failed_reservation)
    failed_qids = set(failed_document.get('qids', []))
    excluded = set(receipt['pins']['excluded_failed_attempt_qids']) | set(receipt['pins']['excluded_unstarted_qids'])
    if (value.get('version') != 'scale20k-native-attempt-reservation-v3' or value.get('generation') != RECOVERY_GENERATION
            or value.get('global_index') != global_index or value.get('source_path') != str(Path(source).resolve())
            or value.get('run_name') != fixed['chunks'][0]['run_name']
            or value.get('input_hashes') != sorted(hashes) or value.get('qids') != sorted(qids)
            or not excluded.issubset(set(value.get('excluded_failed_qids', [])))
            or set(receipt['pins'].get('excluded_failed_input_hashes', [])) & hashes
            or set(value.get('excluded_failed_input_hashes', [])) != set(receipt['pins'].get('excluded_failed_input_hashes', []))
            or qids & failed_qids or value.get('actual_overlap_count') != 0 or value.get('batch_size') != 1
            or value.get('helper_sha256') != sha(__file__) or value.get('requirements_sha256') != REQUIREMENTS_SHA
            or Path(indexed['source_path']).resolve(strict=True) != Path(source).resolve()
            or indexed['source_sha256'] != value['source_sha256']
            or (RUNTIME / ('terminal-' + value['run_name'])).exists()):
        raise ValueError('Exact unstarted recovery reservation binding differs')
    return [path, Path(recovery_pin['path']), Path(value['inventory_path']), Path(value['source_index_path']),
            BASE / 'requirements.json', BASE / 'baseline-native-10000.jsonl', *code_paths()]


def code_paths():
    names = ('run_scale20k_native_v4.py', 'prepare_scale20k_content_chunk_v3.py', 'run_scale20k_content_classification_v3.py', BUILDER_NAME,
             'run_scale20k_native_v2.py', 'run_scale20k_native_v3.py', 'run_scale20k_content_classification_v2.py',
             'prepare_scale20k_content_chunk_v1.py', 'prepare_scale20k_content_chunk_v2.py',
             'prepare_scale20k_content_sources_v3.py',
             'prepare_scale100k_content_chunk_v1.py', 'prepare_scale100k_content_chunk_v2.py', 'prepare_scale_content_chunk_v1.py',
             'prepare_scale_content_chunk_v4.py', 'prepare_scale_classification_v1.py', 'native_snapshot_terminal_v1.py',
             'run_scale_content_classification_v6.py', 'run_scale100k_content_classification_v1.py', 'run_scale100k_content_classification_v2.py',
             'run_scale100k_content_classification_v3.py', 'run_scale100k_content_classification_v4.py', 'run_scale100k_content_classification_v5.py', 'review_scale_content_v6.py')
    paths = [Path(__file__).resolve(), ROOT / 'src/recommend_book/scale20k_recovery_v2.py',
             ROOT / 'src/recommend_book/scale20k_native_v2.py', ROOT / 'src/recommend_book/scale20k_recovery_v1.py',
             Path(_v1.__file__).resolve()]
    for name in names:
        path = ROOT / 'scripts' / name
        if not path.is_file():
            raise FileNotFoundError('Recovery v3 code pin is missing: ' + str(path))
        paths.append(path)
    paths.append(ROOT / 'data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py')
    return paths


def code_snapshot():
    return {str(path): sha(path) for path in code_paths()}


def verify_code_snapshot(snapshot):
    for path, expected in snapshot.items():
        if sha(path) != expected:
            raise ValueError('Recovery v3 code changed during window: ' + path)


def load_recipe(kind, owner_path):
    # Frozen recipe expansion uses v1 state globals; process-local aliasing
    # keeps the runtime child on old chunk1 while parent guards use state3.
    old = sys.modules.get('recommend_book.scale20k_native_v1')
    sys.modules['recommend_book.scale20k_native_v1'] = sys.modules[__name__]
    try:
        recipe = _v1.load_recipe(kind, owner_path)
    finally:
        if old is None: sys.modules.pop('recommend_book.scale20k_native_v1', None)
        else: sys.modules['recommend_book.scale20k_native_v1'] = old
    return recipe
