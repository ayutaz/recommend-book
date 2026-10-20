"""Only the second measured failure may open one fresh 86-work checkpoint."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from recommend_book import scale_content_control_v4 as control
from recommend_book import scale_content_recovery_v1 as preserved
from recommend_book import scale_content_recovery_v2 as recovery
from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return recovery.sha(path)


def module(path):
    spec = importlib.util.spec_from_file_location('new_recovery_test_' + path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def failed_fixture(tmp_path, monkeypatch):
    rows = [{'qid': 'Q' + str(i + 1), 'content_evidence_ids': ['plot'],
             'admitted_labels': ([{'axis': 'theme' if i < 81 else 'genre',
                 'label_id': 'theme.growth' if i < 81 else 'genre.mystery',
                 'evidence': [{'evidence_id': 'plot' if i < 81 else 'metadata',
                               'quote': 'Growing up' if i < 81 else 'mystery novel'}]}]
                 if i < 88 else [])} for i in range(92)]
    public = {'version': 'scale-content-native-classification-export-v3',
        'coordinator_sha256': 'coordinator',
        'verified_projection': {'rows': rows, 'actual_feature_adopted_works': 88,
                                'actual_content_non_genre_feature_works': 81},
        'gate': {'passed': True, 'actual_feature_adopted_works': 88,
                 'actual_content_non_genre_feature_works': 81}}
    public_sha = write(tmp_path / 'public.json', public)
    imported = {'stage': 'actual_catalogue_import_complete',
        'actual_process': {'pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True},
        'classification_manifest_sha256': public_sha,
        'result': {'raw_classified': 92, 'adopted': 88, 'failed': 0,
                   'display_policy_sha256': control.display_policy_sha256()}}
    values = {'export': public,
        'run': {'run_id': 'failed37', 'selected': 100, 'failed': 5, 'pending': 0,
                'initial_concurrency': 8, 'final_concurrency': 8,
                'stopped': False, 'stop_reasons': []},
        'import_report': imported, 'import:stdout.json': imported['result'],
        'import:process-start.json': {'pid': 123},
        'import:process-exit.json': imported['actual_process']}
    pins = {}
    for name, value in values.items():
        path = tmp_path / (name.replace(':', '-') + '.json')
        pins[name] = {'path': str(path.resolve()), 'sha256': write(path, value)}
    receipt = {'version': 'scale-content-window-control-v4', 'window_index': 36,
        'run_id': 'failed37', 'coordinator_sha256': 'coordinator',
        'next_review_mode': 'full', 'gate': copy.deepcopy(recovery.FAILED_GATE),
        'display_projection': control.display_counts(rows), 'control_artifacts': pins}
    path = tmp_path / 'failed-control.json'
    digest = write(path, receipt)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    return path, digest, receipt


def test_frozen_v1_reuse_does_not_change_its_globals_or_bytes():
    recovery.verify_frozen_reuse()
    assert preserved.RUN_NAME == 'scale30k-content-2301-2400'
    assert preserved.FAILED_GATE['failure_rate'] == .06
    assert recovery.RUN_NAME == 'scale30k-content-3701-3800'
    assert recovery.FAILED_GATE == control.actual_gate(100, 92, 88, 81)
    assert recovery.FAILED_GATE['failure_rate'] == .08
    assert len(recovery.QUARANTINED) == 8


def test_only_the_new_genuine_failed_gate_is_returned_false(tmp_path, monkeypatch):
    path, digest, receipt = failed_fixture(tmp_path, monkeypatch)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='continuation gate failed'):
        control.verify_control_receipt(path, digest)
    assert recovery.verify_exact_failed_control(path, digest) == receipt
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='continuation gate failed'):
        control.verify_control_receipt(path, digest)


@pytest.mark.parametrize('field,value', [('window_index', 22), ('next_review_mode', 'sample'),
    ('gate', control.actual_gate(100, 94, 90, 78))])
def test_another_failure_cannot_borrow_v2(tmp_path, monkeypatch, field, value):
    path, _, receipt = failed_fixture(tmp_path, monkeypatch)
    receipt[field] = value
    digest = write(path, receipt)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    with pytest.raises(ValueError):
        recovery.verify_exact_failed_control(path, digest)


@pytest.mark.parametrize('kind', ['helper', 'runner', 'tail'])
def test_reused_code_sha_drift_rejected_before_any_native_call(kind):
    if kind == 'helper':
        adapt = recovery.adapted_source
    elif kind == 'runner':
        adapt = module(recovery.ROOT / 'scripts/run_scale_content_recovery_v2.py').adapted_runner_source
    else:
        adapt = module(recovery.BASE / 'content-recovery-tail-v2.py').adapted_tail_source
    with pytest.raises(ValueError, match='changed'):
        adapt(b'changed')


def test_frozen_adapter_requires_exact_anchor_counts(monkeypatch):
    raw = Path(preserved.__file__).read_bytes().replace(b'len(works) != 100', b'len(works) != 101')
    monkeypatch.setattr(recovery, 'V1_HELPER_SHA', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='anchor drift'):
        recovery.adapted_source(raw)


def fresh_fixture(tmp_path, monkeypatch, count=86, duplicate=None):
    base = tmp_path / 'base'; base.mkdir()
    monkeypatch.setattr(recovery, 'BASE', base)
    works = [WorkInput(work_id='w' + str(i), title='Novel ' + str(i)) for i in range(count)]
    inputs = tmp_path / 'new.jsonl'
    inputs.write_text('\n'.join(w.model_dump_json() for w in works) + '\n', encoding='utf-8')
    mapping = tmp_path / 'mapping.json'
    write(mapping, [{'work_id': w.work_id, 'wikidata_qid': 'Q' + str(10000 + i)}
                    for i, w in enumerate(works)])
    historical = base / 'native-input-hash-exclusions-v1.json'
    write(historical, {'input_hashes': [model_hash(works[0])] if duplicate == 'hash' else []})
    monkeypatch.setattr(recovery, 'HISTORICAL_SHA', recovery.sha(historical))
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
        if duplicate == 'qid':
            db.execute("INSERT INTO native_classifications VALUES('Q10000')")
    return {'inputs_path': str(inputs), 'mapping_path': str(mapping),
            'runtime_preparation': str(tmp_path / 'runtime'), 'catalogue_path': str(database)}


def test_exactly_86_fresh_unique_works_and_gate_denominator(tmp_path, monkeypatch):
    census, _ = recovery._fresh_census(fresh_fixture(tmp_path, monkeypatch))
    assert len(census['new_input_hashes']) == len(census['new_qids']) == 86
    assert census['actual_input_overlap_count'] == census['actual_authority_overlap_count'] == 0
    assert control.actual_gate(86, 82, 70, 60)['passed'] is True
    assert control.actual_gate(86, 81, 70, 60)['passed'] is False


@pytest.mark.parametrize('count,duplicate', [(100, None), (86, 'hash'), (86, 'qid')])
def test_wrong_count_or_previous_input_or_qid_rejected(tmp_path, monkeypatch, count, duplicate):
    document = fresh_fixture(tmp_path, monkeypatch, count, duplicate)
    with pytest.raises(ValueError, match='unique|already attempted|already present'):
        recovery._fresh_census(document)


def source_fixture(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.mkdir()
    work = WorkInput(work_id='work1', title='Novel', source_record_ids=['version1'])
    inputs = source / 'inputs'; inputs.write_text(work.model_dump_json() + '\n', encoding='utf-8')
    ledger = source / 'ledger'; ledger.write_text('fixed ledger')
    records = source / 'records'; records.write_text('fixed record')
    database = source / 'source.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE works(work_id TEXT,source_version_id TEXT)')
        db.execute('CREATE TABLE source_records(version_id TEXT,source TEXT,source_id TEXT)')
        db.execute("INSERT INTO works VALUES('work1','version1')")
        db.execute("INSERT INTO source_records VALUES('version1','wikidata','Q10000')")
    mapping = source / 'work-mapping.json'; write(mapping, [{
        'work_id': work.work_id, 'input_work_id': work.work_id, 'wikidata_qid': 'Q10000',
        'catalogue_work_id': 'wd:Q10000', 'input_sha256': model_hash(work)}])
    fixed = {'window_index': 37, 'selected': 86, 'required_purpose_admissions_allowed': True,
        'source_database': str(database), 'source_database_sha256': recovery.sha(database),
        'records_path': str(records), 'records_sha256': recovery.sha(records),
        'chunks': [{'run_name': recovery.RUN_NAME, 'inputs_path': str(inputs),
                    'inputs_sha256': recovery.sha(inputs), 'usage_ledger_path': str(ledger),
                    'usage_ledger_sha256': recovery.sha(ledger)}]}
    seal = source / 'manifest.json'; write(seal, fixed)
    monkeypatch.setattr(recovery, 'SOURCE_SHA', recovery.sha(seal))
    return {'source_preparation': str(source), 'inputs_path': str(inputs), 'mapping_path': str(mapping),
        'artifacts': {key: recovery._pin(path) for key, path in
                      [('source', seal), ('inputs', inputs), ('mapping', mapping), ('ledger', ledger)]}}


@pytest.mark.parametrize('changed', ['inputs', 'ledger', 'mapping', 'selected100'])
def test_sealed_binding_rejects_repinned_substitution(tmp_path, monkeypatch, changed):
    document = source_fixture(tmp_path, monkeypatch)
    recovery._bound_source(document)
    if changed == 'selected100':
        path = Path(document['artifacts']['source']['path'])
        fixed = recovery.read(path); fixed['selected'] = 100
        monkeypatch.setattr(recovery, 'SOURCE_SHA', write(path, fixed))
        document['artifacts']['source'] = recovery._pin(path)
    else:
        path = Path(document['artifacts'][changed]['path'])
        if changed == 'mapping':
            rows = recovery.read(path); rows[0].update(wikidata_qid='Q99999', catalogue_work_id='wd:Q99999')
            write(path, rows)
        else:
            path.write_text('substituted bytes')
        document['artifacts'][changed] = recovery._pin(path)
    with pytest.raises(ValueError, match='differs|authority'):
        recovery._bound_source(document)


@pytest.mark.parametrize('field,value', [('actual_exit_code', True), ('session_id', True), ('session_id', 94137)])
def test_second_stop_requires_strict_exit1_and_exact_session(tmp_path, monkeypatch, field, value):
    driver = tmp_path / 'driver.py'; driver.write_text('old driver')
    stop = {'version': 'scale-content-tool-observed-stop-v1', 'actual_exit_code': 1,
        'session_id': 30949, 'process_exit_observed': True,
        'failed_control_sha256': recovery.FAILED_SHA,
        'observation_source': 'exec.write_stdin terminal result',
        'driver_path': str(driver), 'driver_sha256': recovery.sha(driver)}
    stop[field] = value
    path = tmp_path / 'stop.json'; monkeypatch.setattr(recovery, 'STOP_SHA', write(path, stop))
    with pytest.raises(ValueError, match='exit1'):
        recovery._verify_stop_observation(path)


def test_v2_exclusive_reservation_prevents_second_launch(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    checkpoint = tmp_path / 'checkpoint.json'
    document = {'runtime_preparation': str(runtime),
        'artifacts': {'coordinator': {'sha256': 'new-code'},
                      'tail_driver': {'path': 'new-tail', 'sha256': 'new-tail-code'}}}
    digest = write(checkpoint, document)
    monkeypatch.setattr(recovery, 'verify_recovery_checkpoint', lambda *args: {})
    reservation = recovery.reserve_recovery_attempt(checkpoint, digest)
    assert recovery.read(reservation['path'])['version'] == 'scale-content-single-recovery-launch-v2'
    with pytest.raises(FileExistsError):
        recovery.reserve_recovery_attempt(checkpoint, digest)


def test_failed_recovery_tail_gate_stops_without_continuation(tmp_path, monkeypatch):
    path, digest, _ = failed_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='continuation gate failed'):
        recovery.continue_after_recovery_gate(path, digest,
            lambda _: pytest.fail('next native window must not launch'))


def test_v2_runner_keeps_actual_index_two_slots_full_review_and_proof_pins(tmp_path, monkeypatch):
    runner = module(recovery.ROOT / 'scripts/run_scale_content_recovery_v2.py')
    source = tmp_path / 'source'; source.mkdir()
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    write(source / 'manifest.json', {'chunks': [{'inputs_path': 'inputs', 'usage_ledger_path': 'ledger'}]})
    monkeypatch.setattr(recovery, 'enter_recovery_process', lambda *args: {
        'source_preparation': str(source), 'runtime_preparation': str(runtime)})
    controls = {'batch_size': 5, 'concurrency': 2, 'timeout': 900,
                'max_run_seconds': 3600, 'max_observed_tokens': 2000000}
    def prepare(*args):
        assert args[-2:] == (recovery.RUN_NAME, 2)
        write(runtime / ('preflight.' + recovery.RUN_NAME + '.json'), {'controls': controls})
    monkeypatch.setitem(sys.modules, 'prepare_scale_content_chunk_v4', SimpleNamespace(prepare=prepare))
    calls = []
    monkeypatch.setattr(runner, 'load_recovery_coordinator', lambda: SimpleNamespace(
        execute_window=lambda *args, **kwargs: calls.append((args, kwargs))))
    runner.execute(tmp_path / 'checkpoint', 'digest')
    assert calls[0][0][2] == 37
    transformed = runner.recovery_source((recovery.ROOT / 'scripts/run_scale_content_classification_v6.py').read_bytes())
    tree = ast.parse(transformed)
    review = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name) and n.func.id == 'review_content_window']
    assert next(k.value for k in review[0].keywords if k.arg == 'full').value is True
    assert "prior_control['window_index'] != route_index - 1" in transformed
    assert 'recovery_artifact_paths(control_receipt)' in transformed
    assert 'from recommend_book.scale_content_recovery_v2 import' in transformed


def test_tail_binds_new_code_and_opens_index38_only_after_rights_import_v4_gate():
    tail = module(recovery.BASE / 'content-recovery-tail-v2.py')
    source = tail.adapted_tail_source((recovery.BASE / 'content-recovery-tail-v1.py').read_bytes())
    assert 'index = 38' in source and 'index==38' in source
    assert source.index('create_reviewed_export(parent') < source.index('operation.finalize(public')
    assert source.index('operation.finalize(public') < source.index('return continue_after_recovery_gate(')
    assert 'lambda _: ordinary_continuation(control_path, document)' in source
    assert "sha(__file__) != own['sha256']" in source
    assert 'run_scale_content_recovery_v2.py' in source


@pytest.mark.parametrize('changed', ['path', 'bytes'])
def test_first_ordinary_source_cannot_be_redirected_or_resealed(tmp_path, changed):
    tail = module(recovery.BASE / 'content-recovery-tail-v2.py')
    seal = tmp_path / 'next' / 'manifest.json'
    digest = write(seal, {'window_index': 38})
    selected = seal.parent
    if changed == 'path':
        selected = tmp_path / 'another'
    else:
        write(seal, {'window_index': 39})
    ordinary = SimpleNamespace(selected_source=lambda *_: selected, sha=recovery.sha)
    ordinary.main = lambda: ordinary.selected_source(38, tmp_path)
    with pytest.raises(ValueError, match='sealed index 38'):
        tail._continue_with_fixed_next(ordinary,
            {'artifacts': {'next_source': {'path': str(seal), 'sha256': digest}}})
