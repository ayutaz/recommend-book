"""The one recovery cannot forgive a failed window or resend an existing run."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from recommend_book import scale_content_recovery_v1 as recovery
from recommend_book import scale_content_control_v4 as ordinary
from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return recovery.sha(path)


def failed_fixture(tmp_path, monkeypatch):
    rows = []
    for index in range(94):
        content = index < 78
        labels = ([{'axis': 'theme' if content else 'genre',
                    'label_id': 'theme.growth' if content else 'genre.mystery',
                    'evidence': [{'evidence_id': 'plot' if content else 'metadata',
                                  'quote': 'Growing up' if content else 'mystery novel'}]}]
                  if index < 90 else [])
        rows.append({'qid': 'Q' + str(index + 1), 'content_evidence_ids': ['plot'],
                     'admitted_labels': labels})
    public = {'version': 'scale-content-native-classification-export-v3',
        'coordinator_sha256': 'coordinator',
        'verified_projection': {'rows': rows, 'actual_feature_adopted_works': 90,
                                'actual_content_non_genre_feature_works': 78},
        'gate': {'passed': True, 'actual_feature_adopted_works': 90,
                 'actual_content_non_genre_feature_works': 78}}
    run = {'run_id': 'failed-run', 'selected': 100, 'failed': 5, 'pending': 0,
           'initial_concurrency': 4, 'final_concurrency': 4,
           'stopped': False, 'stop_reasons': []}
    public_sha = write(tmp_path / 'public.json', public)
    imported = {'stage': 'actual_catalogue_import_complete',
        'actual_process': {'pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True},
        'classification_manifest_sha256': public_sha,
        'result': {'raw_classified': 94, 'adopted': 90, 'failed': 0,
                   'display_policy_sha256': ordinary.display_policy_sha256()}}
    values = {'export': public, 'run': run, 'import_report': imported,
        'import:stdout.json': imported['result'], 'import:process-start.json': {'pid': 123},
        'import:process-exit.json': imported['actual_process']}
    pins = {}
    for name, value in values.items():
        path = tmp_path / (name.replace(':', '-') + '.json')
        pins[name] = {'path': str(path.resolve()), 'sha256': write(path, value)}
    document = {'version': 'scale-content-window-control-v4', 'window_index': 22,
        'run_id': 'failed-run', 'coordinator_sha256': 'coordinator',
        'next_review_mode': 'full', 'gate': copy.deepcopy(recovery.FAILED_GATE),
        'display_projection': ordinary.display_counts(rows), 'control_artifacts': pins}
    path = tmp_path / 'failed-control.json'
    digest = write(path, document)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    return path, digest, document


def test_genuine_failure_is_returned_false_and_ordinary_validator_still_rejects(tmp_path, monkeypatch):
    path, digest, expected = failed_fixture(tmp_path, monkeypatch)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='continuation gate failed'):
        ordinary.verify_control_receipt(path, digest)
    assert recovery.verify_exact_failed_control(path, digest) == expected
    assert expected['gate']['passed'] is False
    assert expected['gate']['failure_rate'] == .06
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='continuation gate failed'):
        ordinary.verify_control_receipt(path, digest)


@pytest.mark.parametrize('change', ['reason', 'counts', 'index', 'sample_review'])
def test_other_failure_cannot_borrow_the_exact_recovery(tmp_path, monkeypatch, change):
    path, _, document = failed_fixture(tmp_path, monkeypatch)
    if change == 'reason':
        document['gate']['reasons'] = ['something_else']
    elif change == 'counts':
        document['gate']['accepted'] = 93
    elif change == 'index':
        document['window_index'] = 21
    else:
        document['next_review_mode'] = 'sample'
    digest = write(path, document)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    with pytest.raises(ValueError):
        recovery.verify_exact_failed_control(path, digest)


def test_failed_validator_rejects_broken_real_process_binding(tmp_path, monkeypatch):
    path, _, document = failed_fixture(tmp_path, monkeypatch)
    start = Path(document['control_artifacts']['import:process-start.json']['path'])
    document['control_artifacts']['import:process-start.json']['sha256'] = write(start, {'pid': 999})
    digest = write(path, document)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    with pytest.raises(ValueError, match='import/control'):
        recovery.verify_exact_failed_control(path, digest)


def test_failed_validator_requires_frozen_sha_and_unique_anchor(tmp_path, monkeypatch):
    path, digest, _ = failed_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(recovery, 'CONTROL_SHA', '0' * 64)
    with pytest.raises(ValueError, match='frozen validator'):
        recovery.verify_exact_failed_control(path, digest)
    monkeypatch.setattr(recovery, 'CONTROL_SHA', recovery.sha(ordinary.__file__))
    monkeypatch.setattr(recovery.inspect, 'getsource', lambda _: 'def other(): pass')
    with pytest.raises(ValueError, match='anchor drift'):
        recovery.verify_exact_failed_control(path, digest)


@pytest.mark.parametrize('field,value', [('actual_exit_code', 0), ('actual_exit_code', True), ('session_id', 1),
    ('process_exit_observed', False), ('failed_control_sha256', 'different')])
def test_tool_observed_stop_requires_the_exact_exit1(tmp_path, monkeypatch, field, value):
    driver = tmp_path / 'old-driver.py'
    driver.write_text('preserved driver', encoding='utf-8')
    stop = {'version': 'scale-content-tool-observed-stop-v1', 'actual_exit_code': 1,
        'session_id': 94137, 'process_exit_observed': True,
        'failed_control_sha256': recovery.FAILED_SHA,
        'observation_source': 'exec.write_stdin terminal result',
        'driver_path': str(driver), 'driver_sha256': recovery.sha(driver)}
    stop[field] = value
    path = tmp_path / 'stop.json'; write(path, stop)
    monkeypatch.setattr(recovery, 'STOP_SHA', recovery.sha(path))
    with pytest.raises(ValueError, match='exit1'):
        recovery._verify_stop_observation(path)


@pytest.mark.parametrize('existing', ['preflight', 'run', 'terminal'])
def test_recovery_never_resumes_an_existing_preflight_or_native_directory(tmp_path, existing):
    document = {'runtime_preparation': str(tmp_path)}
    if existing == 'preflight':
        (tmp_path / ('preflight.' + recovery.RUN_NAME + '.json')).write_text('{}')
    elif existing == 'terminal':
        (tmp_path / ('terminal-' + recovery.RUN_NAME)).mkdir()
    else:
        (tmp_path / 'runtime/data/autonomous-next-v3/classification' / recovery.RUN_NAME).mkdir(parents=True)
    with pytest.raises(ValueError, match='already exists'):
        recovery._assert_unstarted(document)


def test_exclusive_reservation_and_process_entry_allow_only_one_launch(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    checkpoint = tmp_path / 'checkpoint.json'
    document = {'runtime_preparation': str(runtime),
        'artifacts': {'coordinator': {'sha256': 'code'},
                      'tail_driver': {'path': 'tail', 'sha256': 'tail-code'}}}
    digest = write(checkpoint, document)
    monkeypatch.setattr(recovery, 'verify_recovery_checkpoint', lambda *args: {'gate': {'passed': False}})
    recovery.reserve_recovery_attempt(checkpoint, digest)
    with pytest.raises(FileExistsError):
        recovery.reserve_recovery_attempt(checkpoint, digest)
    write(checkpoint.with_name('tail-process-entry.json'),
          {'pid': 123, 'checkpoint_sha256': digest, 'tail_driver_sha256': 'tail-code'})
    assert recovery.enter_recovery_process(checkpoint, digest) == document
    with pytest.raises(FileExistsError):
        recovery.enter_recovery_process(checkpoint, digest)


def fresh_fixture(tmp_path, monkeypatch, duplicate_kind=None):
    base = tmp_path / 'base'; base.mkdir()
    monkeypatch.setattr(recovery, 'BASE', base)
    works = [WorkInput(work_id='w' + str(i), title='Novel ' + str(i)) for i in range(100)]
    inputs = tmp_path / 'new.jsonl'
    inputs.write_text('\n'.join(w.model_dump_json() for w in works) + '\n', encoding='utf-8')
    mapping = tmp_path / 'mapping.json'
    write(mapping, [{'work_id': w.work_id, 'wikidata_qid': 'Q' + str(10000 + i)}
                    for i, w in enumerate(works)])
    historical = base / 'native-input-hash-exclusions-v1.json'
    write(historical, {'input_hashes': [model_hash(works[0])] if duplicate_kind == 'hash' else []})
    monkeypatch.setattr(recovery, 'HISTORICAL_SHA', recovery.sha(historical))
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
        if duplicate_kind == 'qid':
            db.execute('INSERT INTO native_classifications VALUES (?)', ('Q10000',))
    return {'inputs_path': str(inputs), 'mapping_path': str(mapping),
            'runtime_preparation': str(tmp_path / 'runtime'), 'catalogue_path': str(database)}


@pytest.mark.parametrize('duplicate_kind', ['hash', 'qid'])
def test_recovery_rejects_reused_input_or_authority_without_model_calls(tmp_path, monkeypatch, duplicate_kind):
    document = fresh_fixture(tmp_path, monkeypatch, duplicate_kind)
    with pytest.raises(ValueError, match='already attempted|already present'):
        recovery._fresh_census(document)


def test_one_hundred_new_inputs_get_a_zero_overlap_census(tmp_path, monkeypatch):
    document = fresh_fixture(tmp_path, monkeypatch)
    census, pins = recovery._fresh_census(document)
    assert len(census['new_input_hashes']) == len(census['new_qids']) == 100
    assert census['actual_input_overlap_count'] == census['actual_authority_overlap_count'] == 0
    assert pins['historical_inputs']['sha256'] == recovery.HISTORICAL_SHA


def coordinator_module():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_scale_content_recovery_v1.py'
    spec = importlib.util.spec_from_file_location('recovery_coordinator_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_coordinator_retains_window_order_and_actual_gate_and_forces_all_review():
    module = coordinator_module()
    path = module.ROOT / 'scripts/run_scale_content_classification_v6.py'
    source = module.recovery_source(path.read_bytes())
    tree = ast.parse(source)
    review = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name) and node.func.id == 'review_content_window']
    assert len(review) == 1
    assert next(item.value for item in review[0].keywords if item.arg == 'full').value is True
    assert "prior_control['window_index'] != route_index - 1" in source
    assert "gate = check_gate(run_manifest, projection)" in source
    assert 'recovery_artifact_paths(control_receipt)' in source
    assert module.load_recovery_coordinator().__file__ == str(Path(module.__file__).resolve())


def test_coordinator_drift_fails_before_any_native_call():
    module = coordinator_module()
    with pytest.raises(ValueError, match='coordinator changed'):
        module.recovery_source(b'changed')


def test_recovery_preflight_must_be_two_slots_before_native_entry(tmp_path, monkeypatch):
    module = coordinator_module()
    source = tmp_path / 'source'; source.mkdir()
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    write(source / 'manifest.json', {'chunks': [{'inputs_path': 'inputs', 'usage_ledger_path': 'ledger'}]})
    monkeypatch.setattr(recovery, 'enter_recovery_process', lambda *args: {
        'source_preparation': str(source), 'runtime_preparation': str(runtime)})
    calls = []
    def prepare(*args):
        calls.append(args[-1])
        write(runtime / ('preflight.' + recovery.RUN_NAME + '.json'), {'controls': {
            'batch_size': 5, 'concurrency': 8, 'timeout': 900,
            'max_run_seconds': 3600, 'max_observed_tokens': 2000000}})
    monkeypatch.setitem(sys.modules, 'prepare_scale_content_chunk_v4', SimpleNamespace(prepare=prepare))
    monkeypatch.setattr(module, 'load_recovery_coordinator', lambda: pytest.fail('native entry forbidden'))
    with pytest.raises(ValueError, match='fixed bounded controls'):
        module.execute(tmp_path / 'checkpoint.json', 'hash')
    assert calls == [2]


def test_source_binding_rejects_an_unpinned_alternate_input(tmp_path, monkeypatch):
    document, inputs, ledger, mapping = bound_source_fixture(tmp_path, monkeypatch)
    recovery._bound_source(document)
    document['inputs_path'] = str(tmp_path / 'another-input')
    with pytest.raises(ValueError, match='binding differs'):
        recovery._bound_source(document)


def bound_source_fixture(tmp_path, monkeypatch):
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
    fixed = {'window_index': 23, 'selected': 100, 'required_purpose_admissions_allowed': True,
        'source_database': str(database), 'source_database_sha256': recovery.sha(database),
        'records_path': str(records), 'records_sha256': recovery.sha(records),
        'chunks': [{'run_name': recovery.RUN_NAME, 'inputs_path': str(inputs),
                    'inputs_sha256': recovery.sha(inputs), 'usage_ledger_path': str(ledger),
                    'usage_ledger_sha256': recovery.sha(ledger)}]}
    seal = source / 'manifest.json'; write(seal, fixed)
    monkeypatch.setattr(recovery, 'SOURCE_SHA', recovery.sha(seal))
    document = {'source_preparation': str(source), 'inputs_path': str(inputs), 'mapping_path': str(mapping),
        'artifacts': {key: recovery._pin(path) for key, path in
                      [('source', seal), ('inputs', inputs), ('mapping', mapping), ('ledger', ledger)]}}
    return document, inputs, ledger, mapping


@pytest.mark.parametrize('changed', ['inputs', 'ledger', 'mapping'])
def test_checkpoint_checks_sealed_bytes_and_authority_even_after_mapping_is_repinned(tmp_path, monkeypatch, changed):
    document, inputs, ledger, mapping = bound_source_fixture(tmp_path, monkeypatch)
    if changed == 'mapping':
        rows = recovery.read(mapping)
        rows[0]['wikidata_qid'] = 'Q99999'
        rows[0]['catalogue_work_id'] = 'wd:Q99999'
        write(mapping, rows)
        document['artifacts']['mapping'] = recovery._pin(mapping)
    else:
        (inputs if changed == 'inputs' else ledger).write_text('substituted bytes')
        document['artifacts'][changed] = recovery._pin(inputs if changed == 'inputs' else ledger)
    with pytest.raises(ValueError, match='binding differs|sealed source authority'):
        recovery._bound_source(document)


def test_failed_actual_tail_gate_never_enters_ordinary_continuation(tmp_path, monkeypatch):
    path, digest, _ = failed_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='continuation gate failed'):
        recovery.continue_after_recovery_gate(path, digest,
            lambda _: pytest.fail('ordinary model launch must remain stopped'))
