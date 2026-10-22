"""New v19 state over the immutable v18 native, rights and control recipes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'data/autonomous-next-v3/scale-100000-v1'
RUNTIME = ROOT / 'data/autonomous-next-v3/preparation/scale30k-content-runtime-v1'
DATABASE = ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
WRITER_LOCK = ROOT / 'data/autonomous-next-v3/catalogue-writer.lock'
REQUIREMENTS_SHA = 'af2afa4eaaf2129c5b585ca76554a9eaa3f0be51b5a0bf4f0d9b2a09f94db6fc'
BASELINE_SHA = '3886229ec2c404f54b816710e9851bda279897e9f86e255d907f961f20a34bcd'
HISTORICAL_SHA = '41233759f51e6d2c525f005a2265cf74bbea698631ed2d856b6a0a94b58e0309'
PRIOR_CONTROL = ROOT / 'data/autonomous-next-v3/scale-30000-v1/window-controls/scale30k-content-4101-4200.json'
PRIOR_CONTROL_SHA = 'a912fa76c0cc488de4ebed6f9ba87afef5293ea29403446babadb215ed92e849'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')


def verify_requirements():
    path = BASE / 'requirements.json'
    if sha(path) != REQUIREMENTS_SHA or sha(BASE / 'baseline-native-5000.jsonl') != BASELINE_SHA:
        raise ValueError('Frozen v19 requirements or native baseline changed')
    return read(path)


def eligible_count():
    with sqlite3.connect(DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        return db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) '
                          'WHERE w.display_allowed=1 AND w.recommendable=1').fetchone()[0]


def create_exclusion_index():
    """Read old attempted inputs once; future windows use this compact index."""
    verify_requirements()
    path = BASE / 'native-exclusions-v1.json'
    if path.exists():
        raise FileExistsError('Existing index requires its independently fixed expected SHA; no self-approval')
    from .models import WorkInput
    from .usage_guard import model_hash
    previous = ROOT / 'data/autonomous-next-v3/scale-30000-v1'
    historical = previous / 'native-input-hash-exclusions-v1.json'
    if sha(historical) != HISTORICAL_SHA:
        raise ValueError('Original attempted-input index changed')
    baseline = [json.loads(line) for line in (BASE / 'baseline-native-5000.jsonl').read_bytes().splitlines() if line]
    baseline_qids = {row['qid'] for row in baseline}
    if len(baseline) != 5000 or len(baseline_qids) != 5000:
        raise ValueError('Expected exactly 5000 distinct baseline native authorities')
    hashes, attempted_ids, observations = set(read(historical)['input_hashes']), set(), {}
    attempted_count = 0
    for old in sorted((RUNTIME / 'runtime/data/autonomous-next-v3/classification').glob('scale30k-content-*/inputs.jsonl')):
        observations[str(old)] = sha(old)
        for line in old.read_bytes().splitlines():
            if line.strip():
                work = WorkInput.model_validate_json(line)
                hashes.add(model_hash(work)); attempted_ids.add(work.work_id); attempted_count += 1
    qids, mapped = set(baseline_qids), set()
    for mapping in sorted((previous / 'classification-sources').glob('*/work-mapping.json')):
        rows = [row for row in read(mapping) if row['work_id'] in attempted_ids]
        if rows:
            observations[str(mapping)] = sha(mapping)
            qids.update(row['wikidata_qid'] for row in rows)
            mapped.update(row['work_id'] for row in rows)
    if attempted_count != 4075 or mapped != attempted_ids:
        raise ValueError('Completed v18 attempted-input authority census differs')
    value = {'version': 'scale100k-native-exclusions-v1', 'requirements_sha256': REQUIREMENTS_SHA,
        'baseline_native_sha256': BASELINE_SHA, 'baseline_native_works': 5000,
        'v18_attempted_inputs': attempted_count, 'input_hashes': sorted(hashes), 'qids': sorted(qids),
        'creator_code_sha256': sha(__file__), 'source_observations_sha256': observations,
        'old_proof_replays': 0, 'model_calls': 0}
    save(path, value)
    return value


def load_exclusion_index(path, expected_sha):
    if sha(path) != expected_sha:
        raise ValueError('V19 attempted-input exclusion index changed')
    value = read(path)
    if (value['version'] != 'scale100k-native-exclusions-v1'
            or value['requirements_sha256'] != REQUIREMENTS_SHA
            or value['baseline_native_sha256'] != BASELINE_SHA
            or value['creator_code_sha256'] != sha(__file__)):
        raise ValueError('V19 exclusion index authority differs')
    return value


def process_created_at(pid):
    """Observe Windows PID birth; unknown is never permission to delete a lock."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return datetime.fromtimestamp(ticks / 10000000 - 11644473600, timezone.utc).isoformat()
    finally:
        kernel.CloseHandle(handle)


def bounded_acquire_writer_lock(path=WRITER_LOCK, *, timeout=900, poll_seconds=2):
    path = Path(path)
    deadline = time.monotonic() + timeout
    last_owner = None
    while True:
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                owner = read(path)
            except (OSError, ValueError):
                owner = {'state': 'owner_record_in_progress_or_unreadable'}
            if not isinstance(owner, dict):
                owner = {'state': 'owner_record_not_an_object'}
            pid = owner.get('pid')
            observed = process_created_at(pid)
            status = {'pid': pid, 'recorded_created_at': owner.get('process_created_at'),
                      'observed_created_at': observed, 'started_at': owner.get('started_at')}
            if status != last_owner:
                print(json.dumps({'stage': 'waiting_for_catalogue_writer', 'owner': status,
                                  'lock': str(path), 'automatic_unlink': False}), flush=True)
                last_owner = status
            if time.monotonic() >= deadline:
                raise FileExistsError('Bounded catalogue-writer wait expired; owner retained: ' + str(status))
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))


@contextmanager
def writer_lock(*, purpose, timeout=900):
    token = uuid4().hex
    handle = bounded_acquire_writer_lock(WRITER_LOCK, timeout=timeout)
    owner = {'pid': os.getpid(), 'process_created_at': process_created_at(os.getpid()),
             'started_at': datetime.now(timezone.utc).isoformat(), 'purpose': purpose, 'token': token}
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(owner, stream)
        yield
    finally:
        try:
            if read(WRITER_LOCK) == owner:
                WRITER_LOCK.unlink()
        except FileNotFoundError:
            pass


def _current_source(source, global_index):
    from .models import WorkInput
    from .usage_guard import model_hash
    from .scale_content_recovery_v1 import _verify_mapping_identity
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(BASE.resolve()):
        raise ValueError('V19 source leaves its new namespace')
    fixed = read(source / 'manifest.json')
    if (type(global_index) is not int or fixed['version'] != 'scale-content-classification-source-v1'
            or fixed['window_index'] != global_index or global_index < 42
            or fixed['required_purpose_admissions_allowed'] is not True or len(fixed['chunks']) != 1):
        raise ValueError('One exact usage-admitted v19 source required')
    chunk = fixed['chunks'][0]
    for artifact in (chunk['inputs_path'], chunk['usage_ledger_path'], fixed['source_database']):
        if not Path(artifact).resolve(strict=True).is_relative_to(BASE.resolve()):
            raise ValueError('V19 source input/ledger/database leaves its new namespace')
    if (not chunk['run_name'].startswith('scale100k-content-')
            or sha(chunk['inputs_path']) != chunk['inputs_sha256']
            or sha(chunk['usage_ledger_path']) != chunk['usage_ledger_sha256']
            or sha(fixed['source_database']) != fixed['source_database_sha256']):
        raise ValueError('V19 source input/ledger/database seal differs')
    works = [WorkInput.model_validate_json(line) for line in Path(chunk['inputs_path']).read_bytes().splitlines() if line.strip()]
    mapping = read(source / 'work-mapping.json')
    _verify_mapping_identity(fixed['source_database'], mapping, works)
    hashes, qids = {model_hash(work) for work in works}, {row['wikidata_qid'] for row in mapping}
    if (type(fixed['selected']) is not int or not 1 <= fixed['selected'] <= 100
            or len(works) != fixed['selected'] or len(hashes) != len(works) or len(qids) != len(works)):
        raise ValueError('V19 source must contain unique actual selected works')
    return fixed, hashes, qids


def reserve_window(source, global_index, index_path, index_sha):
    """Exclusive before preflight; never resume or resend an attempted input."""
    verify_requirements()
    excluded = load_exclusion_index(index_path, index_sha)
    fixed, hashes, qids = _current_source(source, global_index)
    name = fixed['chunks'][0]['run_name']
    for existing in (RUNTIME / ('terminal-' + name),
                     RUNTIME / 'runtime/data/autonomous-next-v3/classification' / name):
        if existing.exists():
            raise ValueError('V19 native/preflight already exists; no automatic resume')
    path = BASE / 'attempt-reservations' / (name + '.json')
    value = {'version': 'scale100k-native-attempt-reservation-v1', 'global_index': global_index,
        'run_name': name, 'source_path': str(Path(source).resolve()), 'source_sha256': sha(Path(source) / 'manifest.json'),
        'index_path': str(Path(index_path).resolve()), 'index_sha256': index_sha,
        'input_hashes': sorted(hashes), 'qids': sorted(qids), 'actual_overlap_count': 0,
        'helper_sha256': sha(__file__), 'requirements_sha256': REQUIREMENTS_SHA, 'batch_size': 1}
    with writer_lock(purpose='v19_native_attempt_reservation'):
        prior_hashes, prior_qids = set(excluded['input_hashes']), set(excluded['qids'])
        for receipt in (BASE / 'attempt-reservations').glob('*.json'):
            old = read(receipt)
            prior_hashes.update(old['input_hashes']); prior_qids.update(old['qids'])
        if hashes & prior_hashes or qids & prior_qids:
            raise ValueError('V19 source overlaps an already attempted input or authority')
        with sqlite3.connect(DATABASE.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
            if db.execute('SELECT count(*) FROM native_classifications WHERE qid IN (' + ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]:
                raise ValueError('V19 source would replace an existing native authority')
        save(path, value)
    return path


def verify_reservation(source, global_index):
    verify_requirements()
    fixed, hashes, qids = _current_source(source, global_index)
    name = fixed['chunks'][0]['run_name']
    path = BASE / 'attempt-reservations' / (name + '.json')
    value = read(path)
    if (value['version'] != 'scale100k-native-attempt-reservation-v1' or value['run_name'] != name
            or value['source_path'] != str(Path(source).resolve())
            or value['source_sha256'] != sha(Path(source) / 'manifest.json')
            or value['global_index'] != global_index or value['input_hashes'] != sorted(hashes)
            or value['qids'] != sorted(qids) or value['actual_overlap_count'] != 0
            or value['helper_sha256'] != sha(__file__) or value['requirements_sha256'] != REQUIREMENTS_SHA
            or value['batch_size'] != 1 or (RUNTIME / ('terminal-' + name)).exists()):
        raise ValueError('V19 reserved source or unstarted process binding differs')
    load_exclusion_index(value['index_path'], value['index_sha256'])
    return [path, Path(value['index_path']), BASE / 'requirements.json', BASE / 'baseline-native-5000.jsonl',
            Path(__file__), ROOT / 'scripts/prepare_scale100k_content_chunk_v1.py',
            ROOT / 'scripts/run_scale100k_content_classification_v1.py', ROOT / 'scripts/run_scale100k_native_v1.py']
