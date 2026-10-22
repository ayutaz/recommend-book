"""V19 isolation, fixed exclusions and atomic single-writer reservations."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest

from recommend_book import scale100k_native_v1 as state
from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash


ROOT = Path(__file__).resolve().parents[1]


def module(name):
    path = ROOT / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location('v19_test_' + name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return state.sha(path)


def test_existing_exclusion_index_cannot_approve_its_own_sha(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'BASE', tmp_path)
    monkeypatch.setattr(state, 'verify_requirements', lambda: {})
    write(tmp_path / 'native-exclusions-v1.json', {'tampered': True})
    with pytest.raises(FileExistsError, match='independently fixed'):
        state.create_exclusion_index()


def test_exclusion_index_rejects_changed_bytes_and_wrong_authority(tmp_path):
    path = tmp_path / 'index.json'
    value = {'version': 'scale100k-native-exclusions-v1', 'requirements_sha256': state.REQUIREMENTS_SHA,
             'baseline_native_sha256': state.BASELINE_SHA, 'creator_code_sha256': state.sha(state.__file__)}
    digest = write(path, value)
    assert state.load_exclusion_index(path, digest) == value
    value['baseline_native_sha256'] = 'different'
    changed = write(path, value)
    with pytest.raises(ValueError, match='index changed'):
        state.load_exclusion_index(path, digest)
    with pytest.raises(ValueError, match='authority differs'):
        state.load_exclusion_index(path, changed)


@pytest.mark.parametrize('changed', ['requirements', 'baseline'])
def test_changed_frozen_requirements_or_baseline_rejected(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(state, 'BASE', tmp_path)
    requirement = tmp_path / 'requirements.json'; baseline = tmp_path / 'baseline-native-5000.jsonl'
    monkeypatch.setattr(state, 'REQUIREMENTS_SHA', write(requirement, {'requirements': 'fixed'}))
    monkeypatch.setattr(state, 'BASELINE_SHA', write(baseline, {'baseline': 'fixed'}))
    (requirement if changed == 'requirements' else baseline).write_text('changed')
    with pytest.raises(ValueError, match='requirements or native baseline'):
        state.verify_requirements()


def reservation_fixture(tmp_path, monkeypatch):
    base = tmp_path / 'v19'; base.mkdir()
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
    monkeypatch.setattr(state, 'BASE', base)
    monkeypatch.setattr(state, 'RUNTIME', tmp_path / 'runtime')
    monkeypatch.setattr(state, 'DATABASE', database)
    monkeypatch.setattr(state, 'WRITER_LOCK', tmp_path / 'catalogue-writer.lock')
    monkeypatch.setattr(state, 'verify_requirements', lambda: {})
    monkeypatch.setattr(state, 'load_exclusion_index', lambda *a: {'input_hashes': [], 'qids': []})
    original_acquire = state.bounded_acquire_writer_lock
    monkeypatch.setattr(state, 'bounded_acquire_writer_lock', lambda path, **kwargs:
                        original_acquire(path, timeout=2, poll_seconds=.002))
    def source(name):
        path = base / name; write(path / 'manifest.json', {'selected': 1})
        return path
    return source


def test_competing_drivers_cannot_reserve_the_same_work(tmp_path, monkeypatch):
    source = reservation_fixture(tmp_path, monkeypatch)
    barrier = Barrier(2)
    def current(path, index):
        barrier.wait(timeout=5)
        return {'chunks': [{'run_name': 'scale100k-content-' + path.name}]}, {'hash'}, {'Q1'}
    monkeypatch.setattr(state, '_current_source', current)
    def reserve(name):
        try:
            return state.reserve_window(source(name), 42, tmp_path / 'index', 'fixed')
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ['one', 'two']))
    assert sum(isinstance(value, Path) for value in results) == 1
    assert any('already attempted' in str(value) for value in results)
    assert len(list((state.BASE / 'attempt-reservations').glob('*.json'))) == 1
    assert not state.WRITER_LOCK.exists()


@pytest.mark.parametrize('duplicate', ['hash', 'qid', 'catalogue'])
def test_old_or_failed_attempts_never_get_new_reservations(tmp_path, monkeypatch, duplicate):
    source = reservation_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(state, '_current_source', lambda *a:
                        ({'chunks': [{'run_name': 'scale100k-content-new'}]}, {'h'}, {'Q9'}))
    if duplicate == 'catalogue':
        with sqlite3.connect(state.DATABASE) as db:
            db.execute("INSERT INTO native_classifications VALUES('Q9')")
    else:
        monkeypatch.setattr(state, 'load_exclusion_index', lambda *a:
            {'input_hashes': ['h'] if duplicate == 'hash' else [], 'qids': ['Q9'] if duplicate == 'qid' else []})
    with pytest.raises(ValueError, match='already attempted|existing native'):
        state.reserve_window(source('source'), 42, tmp_path / 'index', 'fixed')
    assert not list((state.BASE / 'attempt-reservations').glob('*.json'))


def test_normal_lock_owner_is_waited_for_without_deletion(tmp_path, monkeypatch):
    lock = tmp_path / 'catalogue-writer.lock'
    original = {'pid': 123, 'process_created_at': 'birth', 'started_at': 'lock-start'}
    write(lock, original)
    observed = []
    monkeypatch.setattr(state, 'process_created_at', lambda pid: observed.append(pid) or 'birth')
    with pytest.raises(FileExistsError, match='owner retained'):
        state.bounded_acquire_writer_lock(lock, timeout=.01, poll_seconds=.002)
    assert observed and set(observed) == {123}
    assert state.read(lock) == original


def test_writer_context_never_unlinks_a_different_owner(tmp_path, monkeypatch):
    lock = tmp_path / 'catalogue-writer.lock'
    monkeypatch.setattr(state, 'WRITER_LOCK', lock)
    other = {'pid': 456, 'token': 'different'}
    with state.writer_lock(purpose='test', timeout=1):
        write(lock, other)
    assert state.read(lock) == other


def source_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'BASE', tmp_path)
    source = tmp_path / 'classification-sources/window-0001-0100'; source.mkdir(parents=True)
    work = WorkInput(work_id='w1', title='A novel', source_record_ids=['s1'])
    inputs = source / 'inputs.jsonl'; inputs.write_text(work.model_dump_json() + '\n', encoding='utf-8')
    ledger = source / 'ledger.json'; write(ledger, {'fixed': 'ledger'})
    database = source / 'source.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE works(work_id TEXT,source_version_id TEXT)')
        db.execute('CREATE TABLE source_records(version_id TEXT,source TEXT,source_id TEXT)')
        db.execute("INSERT INTO works VALUES('w1','s1')")
        db.execute("INSERT INTO source_records VALUES('s1','wikidata','Q123')")
    write(source / 'work-mapping.json', [{'work_id': 'w1', 'input_work_id': 'w1', 'wikidata_qid': 'Q123',
        'catalogue_work_id': 'wd:Q123', 'input_sha256': model_hash(work)}])
    write(source / 'manifest.json', {'version': 'scale-content-classification-source-v1',
        'window_index': 42, 'selected': 1, 'required_purpose_admissions_allowed': True,
        'source_database': str(database), 'source_database_sha256': state.sha(database),
        'chunks': [{'run_name': 'scale100k-content-0001-0100', 'inputs_path': str(inputs),
                    'inputs_sha256': state.sha(inputs), 'usage_ledger_path': str(ledger),
                    'usage_ledger_sha256': state.sha(ledger)}]})
    return source


@pytest.mark.parametrize('changed', ['inputs', 'ledger', 'database', 'mapping', 'not_admitted'])
def test_new_source_bytes_identity_and_purpose_seal_are_required(tmp_path, monkeypatch, changed):
    source = source_fixture(tmp_path, monkeypatch)
    assert state._current_source(source, 42)[2] == {'Q123'}
    fixed = state.read(source / 'manifest.json')
    if changed in ('inputs', 'ledger'):
        Path(fixed['chunks'][0]['inputs_path' if changed == 'inputs' else 'usage_ledger_path']).write_text('changed')
    elif changed == 'database':
        with sqlite3.connect(fixed['source_database']) as db:
            db.execute("UPDATE source_records SET source_id='Q999'")
    elif changed == 'mapping':
        path = source / 'work-mapping.json'
        rows = state.read(path); rows[0].update(wikidata_qid='Q999', catalogue_work_id='wd:Q999')
        write(path, rows)
    else:
        fixed['required_purpose_admissions_allowed'] = False
        write(source / 'manifest.json', fixed)
    with pytest.raises(ValueError, match='seal differs|source authority|usage-admitted'):
        state._current_source(source, 42)


@pytest.mark.parametrize('field', ['inputs_path', 'usage_ledger_path', 'source_database'])
def test_new_source_artifacts_cannot_point_to_an_old_namespace(tmp_path, monkeypatch, field):
    source = source_fixture(tmp_path, monkeypatch)
    fixed = state.read(source / 'manifest.json')
    container = fixed if field == 'source_database' else fixed['chunks'][0]
    original = Path(container[field])
    outside = tmp_path.parent / (tmp_path.name + '-outside-' + field)
    outside.write_bytes(original.read_bytes())
    container[field] = str(outside)
    write(source / 'manifest.json', fixed)
    with pytest.raises(ValueError, match='leaves its new namespace'):
        state._current_source(source, 42)


def test_single_work_preparer_changes_both_config_and_execution_controls():
    preparer = module('prepare_scale100k_content_chunk_v1')
    old = (ROOT / 'scripts/prepare_scale_content_chunk_v4.py').read_bytes()
    transformed = preparer.adapted_source(old)
    assert "load_taxonomy(), 1, execution_spec='provenance-v4')" in transformed
    assert "controls = {'batch_size': 1, 'concurrency': concurrency" in transformed
    assert 'from prepare_scale100k_content_chunk_v1 import frozen_prepare' in transformed
    assert preparer.BUILDER_SHA == state.sha(ROOT / 'scripts/prepare_scale_classification_v1.py')


def test_coordinator_keeps_actual_gate_order_budget_and_full_review():
    coordinator = module('run_scale100k_content_classification_v1')
    old = (ROOT / 'scripts/run_scale_content_classification_v6.py').read_bytes()
    source = coordinator.adapted_source(old)
    assert "'batch_size': 1" in source and "'batch_size': 5" not in source
    assert "prior_control['window_index'] != route_index - 1" in source
    assert 'full=True' in source and 'gate = check_gate(run_manifest, projection)' in source
    assert 'pins = [*v19_pins' in source and 'scale-30000-v1' not in source
    assert 'runtime_preparation != V19_RUNTIME.resolve(strict=True)' in source
    assert coordinator.check_gate({'selected': 100, 'failed': 6, 'pending': 0},
                                  {'actual_feature_adopted_works': 90})['passed'] is False
    assert coordinator.check_gate({'selected': 100, 'failed': 0, 'pending': 0},
                                  {'actual_feature_adopted_works': 69})['passed'] is False


@pytest.mark.parametrize('name,previous', [
    ('prepare_scale100k_content_chunk_v1', 'prepare_scale_content_chunk_v4'),
    ('run_scale100k_content_classification_v1', 'run_scale_content_classification_v6')])
def test_changed_or_anchor_drifted_frozen_code_rejected(name, previous, monkeypatch):
    adapter = module(name)
    with pytest.raises(ValueError, match='changed'):
        adapter.adapted_source(b'different')
    raw = (ROOT / 'scripts' / (previous + '.py')).read_bytes().replace(b'scale-30000-v1', b'scale-30001-v1')
    if name.startswith('prepare_'):
        raw = raw.replace(b'from prepare_scale_content_chunk_v4 import frozen_prepare', b'from other import frozen_prepare')
    monkeypatch.setattr(adapter, 'V18_SHA', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='anchor drift'):
        adapter.adapted_source(raw)


def test_driver_never_loads_an_index_without_an_independent_expected_sha(monkeypatch):
    driver = module('run_scale100k_native_v1')
    monkeypatch.setattr(driver.sys, 'argv', ['driver', '--exclusion-index', 'changed.json'])
    monkeypatch.setattr(driver, 'run', lambda *a: pytest.fail('model route forbidden'))
    with pytest.raises(SystemExit):
        driver.main()


def driver_fixture(tmp_path, monkeypatch):
    driver = module('run_scale100k_native_v1')
    monkeypatch.setattr(driver, 'BASE', tmp_path)
    monkeypatch.setattr(driver.state, 'verify_requirements', lambda: {})
    monkeypatch.setattr(driver.state, 'load_exclusion_index', lambda *a: {})
    monkeypatch.setattr(driver, 'load_operations', lambda: SimpleNamespace())
    from recommend_book import scale_content_control_v4 as control
    monkeypatch.setattr(control, 'verify_control_receipt', lambda *a: {'window_index': 41})
    monkeypatch.setattr(driver, 'observed_command', lambda *a: pytest.fail('unexpected model process'))
    return driver


def test_driver_stops_at_actual_ten_thousand_without_another_window(tmp_path, monkeypatch, capsys):
    driver = driver_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(driver.state, 'eligible_count', lambda: 10000)
    driver.run(tmp_path / 'index', 'fixed')
    assert 'actual_v19_native_target_reached' in capsys.readouterr().out


def test_driver_waits_for_exact_shortfall_instead_of_sending_extra_works(tmp_path, monkeypatch):
    driver = driver_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(driver.state, 'eligible_count', lambda: 9990)
    write(tmp_path / 'classification-sources/window-0001-0100/manifest.json', {'selected': 100})
    monkeypatch.setattr(driver.time, 'sleep', lambda *_: (_ for _ in ()).throw(TimeoutError('bounded test wait')))
    monkeypatch.setattr(driver.state, 'reserve_window', lambda *a: pytest.fail('oversized source must not be reserved'))
    with pytest.raises(TimeoutError):
        driver.run(tmp_path / 'index', 'fixed')


def test_versioned_source_override_is_namespace_scoped(tmp_path, monkeypatch):
    driver = module('run_scale100k_native_v1')
    monkeypatch.setattr(driver, 'BASE', tmp_path / 'v19')
    proper = driver.BASE / 'first-sealed-v5'; proper.mkdir(parents=True)
    write(driver.BASE / 'window-source-overrides.json', {'42': str(proper)})
    assert driver.selected_source(42, driver.BASE / 'failed-first') == proper.resolve()
    write(driver.BASE / 'window-source-overrides.json', {'42': str(tmp_path)})
    with pytest.raises(ValueError, match='leaves its namespace'):
        driver.selected_source(42, driver.BASE / 'failed-first')
