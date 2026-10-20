"""One explicit, quarantined checkpoint after the measured window-23 failure.

This validates a failed control as failed. It never changes an ordinary control
validator, source admission, model result, or the next window's actual gate.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import sqlite3

from . import scale_content_control_v4 as control

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'data/autonomous-next-v3/scale-30000-v1'
CONTROL_SHA = 'd9d8a6dcf56396f6db9eabfe642c5105b7f3cba2809ef83202ab289ddca2741d'
FAILED_SHA = 'ca64ee57f1ec8750ea58e8a66438a30af497d763c1e4967a5e2cc50f1ec60f91'
DIAGNOSIS_SHA = '4e498a7ff5e595887bd88b20f81f33f6a892cb5e463b0001754aab7d47fe6006'
SOURCE_SHA = '28a705ea17ab460bbd78f7af2bd0fb9553346f0dc776b0a7e48cc8a41810d6b4'
HISTORICAL_SHA = '41233759f51e6d2c525f005a2265cf74bbea698631ed2d856b6a0a94b58e0309'
STOP_SHA = 'ac0b1304d26b1a0c5c20ad1e281f5e63579c0698dd5eee146e0c27a607b79d91'
FAILED_GATE = control.actual_gate(100, 94, 90, 78)
QUARANTINED = {'Q2850430', 'Q2849853', 'Q2878094', 'Q2869411', 'Q2870326', 'Q2898061'}
RUN_NAME = 'scale30k-content-2301-2400'
VERSION = 'scale-content-single-recovery-checkpoint-v1'


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


def _pin(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': sha(path)}


def _check_pins(pins):
    for pin in pins.values():
        if not Path(pin['path']).is_absolute() or sha(pin['path']) != pin['sha256']:
            raise ValueError('Recovery artifact changed: ' + pin['path'])


def verify_exact_failed_control(path, expected_sha256):
    """Reuse all frozen v4 compact checks, requiring its failure branch instead.

    The sole changed predicate accepts a genuinely failed gate for diagnosis;
    the returned document still contains passed=False and its exact six-percent
    reason. The ordinary v4 function and its globals are never modified.
    """
    if sha(control.__file__) != CONTROL_SHA or expected_sha256 != FAILED_SHA:
        raise ValueError('Recovery requires the exact frozen validator and failed receipt')
    source = inspect.getsource(control.verify_control_receipt)
    anchor = "if gate != receipt['gate'] or not gate['passed'] or not public['gate']['passed']:"
    if source.count(anchor) != 1:
        raise ValueError('Frozen failed-control predicate anchor drift')
    source = source.replace('def verify_control_receipt(', 'def _failed_control(', 1)
    source = source.replace(anchor,
        "if gate != receipt['gate'] or gate['passed'] or not public['gate']['passed']:")
    namespace = dict(vars(control))
    exec(compile(source, str(Path(__file__).resolve()), 'exec'), namespace)
    receipt = namespace['_failed_control'](path, expected_sha256)
    if (receipt['window_index'] != 22 or receipt['gate'] != FAILED_GATE
            or receipt['next_review_mode'] != 'full'):
        raise ValueError('Recovery is limited to the exact measured window-23 failure')
    imported = read(receipt['control_artifacts']['import_report']['path'])
    if (imported['actual_process']['actual_exit_code'] != 0
            or imported['result']['raw_classified'] != 94
            or imported['result']['adopted'] != 90 or imported['result']['failed'] != 0):
        raise ValueError('Recovery requires the preserved genuine 94/90 import')
    return receipt


def _assert_unstarted(document):
    runtime = Path(document['runtime_preparation'])
    if (runtime / ('terminal-' + RUN_NAME)).exists():
        raise ValueError('Recovery native terminal already exists; no resume or retry')
    run = runtime / 'runtime/data/autonomous-next-v3/classification' / RUN_NAME
    if run.exists():
        raise ValueError('Recovery run/preflight already exists; no resume')
    if list(runtime.glob('preflight.' + RUN_NAME + '.*')):
        raise ValueError('Recovery preflight already exists; no resume')


def _new_works(document):
    from .models import WorkInput
    from .usage_guard import model_hash
    works = [WorkInput.model_validate_json(line) for line in
             Path(document['inputs_path']).read_bytes().splitlines() if line.strip()]
    return works, {model_hash(work) for work in works}


def _bound_source(document):
    source = Path(document['source_preparation']).resolve(strict=True)
    if sha(source / 'manifest.json') != SOURCE_SHA:
        raise ValueError('Recovery source seal differs')
    fixed = read(source / 'manifest.json')
    if (fixed['window_index'] != 23 or fixed['selected'] != 100
            or len(fixed['chunks']) != 1 or fixed['chunks'][0]['run_name'] != RUN_NAME
            or fixed['required_purpose_admissions_allowed'] is not True):
        raise ValueError('Recovery fixed source window differs')
    chunk = fixed['chunks'][0]
    if (document['inputs_path'] != chunk['inputs_path']
            or Path(document['mapping_path']).resolve() != source / 'work-mapping.json'
            or document['artifacts']['source'] != _pin(source / 'manifest.json')
            or document['artifacts']['inputs'] != _pin(chunk['inputs_path'])
            or document['artifacts']['mapping'] != _pin(source / 'work-mapping.json')
            or document['artifacts']['ledger'] != _pin(chunk['usage_ledger_path'])
            or sha(chunk['inputs_path']) != chunk['inputs_sha256']
            or sha(chunk['usage_ledger_path']) != chunk['usage_ledger_sha256']):
        raise ValueError('Recovery source input/mapping/ledger binding differs')
    if (sha(fixed['source_database']) != fixed['source_database_sha256']
            or sha(fixed['records_path']) != fixed['records_sha256']):
        raise ValueError('Recovery sealed source database/records bytes differ')
    works, _ = _new_works(document)
    _verify_mapping_identity(fixed['source_database'], read(document['mapping_path']), works)


def _verify_mapping_identity(database, mapping, works):
    """The hash-sealed DB, rather than a self-pinned mapping, owns QID identity."""
    expected = {work.work_id: work for work in works}
    if len(mapping) != len(expected) or {row['work_id'] for row in mapping} != set(expected):
        raise ValueError('Recovery mapping work identities differ from fixed inputs')
    from .usage_guard import model_hash
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        for row in mapping:
            work = expected[row['work_id']]
            source = db.execute('SELECT s.source,s.source_id,s.version_id FROM works w '
                'JOIN source_records s ON s.version_id=w.source_version_id WHERE w.work_id=?',
                (work.work_id,)).fetchone()
            if (source is None or source[0] != 'wikidata' or source[1] != row['wikidata_qid']
                    or source[2] not in work.source_record_ids
                    or row['input_work_id'] != work.work_id
                    or row['catalogue_work_id'] != 'wd:' + source[1]
                    or row['input_sha256'] != model_hash(work)):
                raise ValueError('Recovery mapping differs from sealed source authority')


def _fresh_census(document):
    from .models import WorkInput
    from .usage_guard import model_hash
    works, current = _new_works(document)
    mapping = read(document['mapping_path'])
    qids = {row['wikidata_qid'] for row in mapping}
    if (len(works) != 100 or len(current) != 100 or len(qids) != 100
            or {row['work_id'] for row in mapping} != {work.work_id for work in works}
            or qids & QUARANTINED):
        raise ValueError('Recovery requires one hundred unique nonquarantined inputs')
    historical = BASE / 'native-input-hash-exclusions-v1.json'
    if sha(historical) != HISTORICAL_SHA:
        raise ValueError('Historical attempted-input index changed')
    pins = {'historical_inputs': _pin(historical)}
    previous, previous_ids = set(), set()
    native = Path(document['runtime_preparation']) / 'runtime/data/autonomous-next-v3/classification'
    for path in sorted(native.glob('*/inputs.jsonl')):
        if path.parent.name == RUN_NAME:
            continue
        pins['attempted_input:' + path.parent.name] = _pin(path)
        for line in path.read_bytes().splitlines():
            if line.strip():
                work = WorkInput.model_validate_json(line)
                previous.add(model_hash(work)); previous_ids.add(work.work_id)
    prior_qids, mapped = set(), set()
    for path in sorted((BASE / 'classification-sources').glob('*/work-mapping.json')):
        rows = read(path)
        relevant = [row for row in rows if row['work_id'] in previous_ids]
        if relevant:
            pins['attempted_mapping:' + path.parent.name] = _pin(path)
            prior_qids.update(row['wikidata_qid'] for row in relevant)
            mapped.update(row['work_id'] for row in relevant)
    if mapped != previous_ids:
        raise ValueError('Attempted authority census is incomplete')
    if current & (previous | set(read(historical)['input_hashes'])) or qids & prior_qids:
        raise ValueError('Recovery inputs or authorities were already attempted')
    _assert_no_catalogue_overlap(document['catalogue_path'], qids)
    return {'new_input_hashes': sorted(current), 'new_qids': sorted(qids),
            'prior_attempted_inputs': len(previous), 'prior_attempted_authorities': len(prior_qids),
            'actual_input_overlap_count': 0, 'actual_authority_overlap_count': 0}, pins


def _assert_no_catalogue_overlap(database, qids):
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('SELECT count(*) FROM native_classifications WHERE qid IN (' +
                      ','.join('?' for _ in qids) + ')', sorted(qids)).fetchone()[0]:
            raise ValueError('Recovery authority is already present in the native catalogue')


def _verify_stop_observation(path):
    if sha(path) != STOP_SHA:
        raise ValueError('Exact tool-observed exit1 artifact SHA differs')
    stop = read(path)
    if (stop.get('version') != 'scale-content-tool-observed-stop-v1'
            or type(stop.get('actual_exit_code')) is not int or stop['actual_exit_code'] != 1
            or type(stop.get('session_id')) is not int or stop['session_id'] != 94137
            or stop.get('failed_control_sha256') != FAILED_SHA
            or stop.get('process_exit_observed') is not True
            or stop.get('observation_source') != 'exec.write_stdin terminal result'
            or sha(stop['driver_path']) != stop['driver_sha256']):
        raise ValueError('Exact tool-observed continuation exit1 required')
    return stop


def create_recovery_checkpoint(source, runtime, failed, diagnosis, stop_observation, output):
    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)
    if sha(source / 'manifest.json') != SOURCE_SHA or sha(diagnosis) != DIAGNOSIS_SHA:
        raise ValueError('Recovery source or saved diagnosis differs')
    prior = verify_exact_failed_control(failed, FAILED_SHA)
    source_doc = read(source / 'manifest.json')
    if (source_doc['window_index'] != 23 or source_doc['selected'] != 100
            or len(source_doc['chunks']) != 1 or source_doc['chunks'][0]['run_name'] != RUN_NAME
            or source_doc['required_purpose_admissions_allowed'] is not True):
        raise ValueError('Recovery must retain the actual next window index and admitted hundred')
    stop = _verify_stop_observation(stop_observation)
    chunk = source_doc['chunks'][0]
    document = {'version': VERSION, 'window_index': 23, 'failed_window_index': 22,
        'source_preparation': str(source), 'runtime_preparation': str(runtime),
        'inputs_path': chunk['inputs_path'], 'mapping_path': str(source / 'work-mapping.json'),
        'catalogue_path': str((ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite').resolve()),
        'failed_control_path': str(Path(failed).resolve()), 'failed_control_sha256': FAILED_SHA,
        'source_sha256': SOURCE_SHA, 'diagnosis_sha256': DIAGNOSIS_SHA,
        'quarantined_qids': sorted(QUARANTINED), 'failed_gate': prior['gate'],
        'concurrency': 2, 'full_review': True, 'maximum_recovery_processes': 1,
        'same_input_retries': 0, 'existing_actual_gate_unchanged': True}
    _assert_unstarted(document)
    census, census_pins = _fresh_census(document)
    pins = {'source': _pin(source / 'manifest.json'), 'inputs': _pin(chunk['inputs_path']),
        'mapping': _pin(source / 'work-mapping.json'), 'ledger': _pin(chunk['usage_ledger_path']),
        'failed_control': _pin(failed), 'diagnosis': _pin(diagnosis), 'stop': _pin(stop_observation),
        'stopped_driver': _pin(stop['driver_path']), 'helper': _pin(__file__),
        'coordinator': _pin(ROOT / 'scripts/run_scale_content_recovery_v1.py'),
        'tail_driver': _pin(BASE / 'content-recovery-tail-v1.py'),
        'ordinary_driver': _pin(BASE / 'content-continuation-v2.py'),
        'operation_driver': _pin(BASE / 'content-operational-driver-v2.py'),
        'ordinary_control': _pin(control.__file__), **census_pins}
    pins.update({'failed_process:' + name: pin for name, pin in prior['control_artifacts'].items()})
    pins.update({'diagnosis:' + str(index): {'path': str((ROOT / path).resolve()), 'sha256': digest}
                 for index, (path, digest) in enumerate(read(diagnosis)['artifacts_sha256'].items())})
    document.update(freshness=census, artifacts=pins)
    _check_pins(pins)
    _bound_source(document)
    save(output, document)
    return {'checkpoint': str(Path(output).resolve()), 'sha256': sha(output), 'model_calls': 0}


def verify_recovery_checkpoint(path, expected_sha256):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Recovery checkpoint SHA differs')
    document = json.loads(raw)
    if (document['version'] != VERSION or document['window_index'] != 23
            or document['failed_window_index'] != 22 or document['concurrency'] != 2
            or document['full_review'] is not True or document['maximum_recovery_processes'] != 1
            or document['same_input_retries'] != 0 or document['existing_actual_gate_unchanged'] is not True
            or document['quarantined_qids'] != sorted(QUARANTINED)
            or document['source_sha256'] != SOURCE_SHA or document['diagnosis_sha256'] != DIAGNOSIS_SHA):
        raise ValueError('Recovery checkpoint scope or controls differ')
    _check_pins(document['artifacts'])
    _bound_source(document)
    if (document['artifacts']['helper'] != _pin(__file__)
            or document['artifacts']['coordinator'] != _pin(ROOT / 'scripts/run_scale_content_recovery_v1.py')
            or document['artifacts']['tail_driver'] != _pin(BASE / 'content-recovery-tail-v1.py')
            or document['artifacts']['diagnosis']['sha256'] != DIAGNOSIS_SHA
            or document['artifacts']['source']['sha256'] != SOURCE_SHA):
        raise ValueError('Recovery fixed code/source/diagnosis pins differ')
    _verify_stop_observation(document['artifacts']['stop']['path'])
    prior = verify_exact_failed_control(document['failed_control_path'], document['failed_control_sha256'])
    if document['failed_gate'] != prior['gate'] or sha(document['source_preparation'] + '/manifest.json') != SOURCE_SHA:
        raise ValueError('Recovery no longer binds its exact failed/source window')
    works, hashes = _new_works(document)
    qids = {row['wikidata_qid'] for row in read(document['mapping_path'])}
    mapping = read(document['mapping_path'])
    if (len(works) != 100 or len(hashes) != 100 or sorted(hashes) != document['freshness']['new_input_hashes']
            or len(qids) != 100 or sorted(qids) != document['freshness']['new_qids'] or qids & QUARANTINED
            or {row['work_id'] for row in mapping} != {work.work_id for work in works}
            or document['freshness']['actual_input_overlap_count'] != 0
            or document['freshness']['actual_authority_overlap_count'] != 0):
        raise ValueError('Recovery fixed fresh-input census differs')
    _assert_no_catalogue_overlap(document['catalogue_path'], qids)
    if path.read_bytes() != raw:
        raise ValueError('Recovery checkpoint changed during verification')
    return prior


def reserve_recovery_attempt(path, expected_sha256):
    """Call before starting the new coordinator process, never as a resume."""
    verify_recovery_checkpoint(path, expected_sha256)
    document = read(path)
    _assert_unstarted(document)
    reservation = Path(path).with_name('launch-reservation.json')
    save(reservation, {'version': 'scale-content-single-recovery-launch-v1',
        'checkpoint_path': str(Path(path).resolve()), 'checkpoint_sha256': expected_sha256,
        'coordinator_sha256': document['artifacts']['coordinator']['sha256'],
        'tail_driver_path': document['artifacts']['tail_driver']['path'],
        'tail_driver_sha256': document['artifacts']['tail_driver']['sha256'],
        'concurrency': 2, 'full_review': True, 'maximum_processes': 1})
    return _pin(reservation)


def enter_recovery_process(path, expected_sha256):
    verify_recovery_checkpoint(path, expected_sha256)
    document = read(path)
    reservation = read(Path(path).with_name('launch-reservation.json'))
    tail = read(Path(path).with_name('tail-process-entry.json'))
    if (reservation['checkpoint_sha256'] != expected_sha256
            or reservation['checkpoint_path'] != str(Path(path).resolve())
            or reservation['coordinator_sha256'] != document['artifacts']['coordinator']['sha256']
            or reservation['tail_driver_path'] != document['artifacts']['tail_driver']['path']
            or reservation['tail_driver_sha256'] != document['artifacts']['tail_driver']['sha256']
            or tail['checkpoint_sha256'] != expected_sha256
            or tail['tail_driver_sha256'] != document['artifacts']['tail_driver']['sha256']
            or type(tail.get('pid')) is not int or tail['pid'] <= 0
            or reservation['concurrency'] != 2 or reservation['full_review'] is not True
            or reservation['maximum_processes'] != 1):
        raise ValueError('Recovery launch reservation differs')
    _assert_unstarted(document)
    save(Path(path).with_name('process-entry.json'), {'pid': os.getpid(),
        'checkpoint_sha256': expected_sha256, 'coordinator_sha256': reservation['coordinator_sha256']})
    return document


def continue_after_recovery_gate(path, expected_sha256, continuation):
    """Only an ordinary successful v4 receipt can open the next model window."""
    receipt = control.verify_control_receipt(path, expected_sha256)
    if (receipt['window_index'] != 23 or receipt['version'] != 'scale-content-window-control-v4'
            or receipt['gate']['passed'] is not True):
        raise ValueError('Actual recovery gate has not passed; ordinary continuation forbidden')
    return continuation(receipt)


def recovery_artifact_paths(path):
    document = read(path)
    _check_pins(document['artifacts'])
    return [Path(path), Path(path).with_name('launch-reservation.json'),
            Path(path).with_name('process-entry.json'), Path(path).with_name('tail-process-entry.json'),
            *[Path(pin['path']) for pin in document['artifacts'].values()]]
