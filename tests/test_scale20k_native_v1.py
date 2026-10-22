"""Isolated v20 contract, atomic attempts and real subprocess boundaries."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import ModuleType
from types import SimpleNamespace

import pytest

from recommend_book import scale20k_native_v1 as state
from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash


ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return state.sha(path)


def driver():
    spec = importlib.util.spec_from_file_location('test_scale20k_driver', ROOT / 'scripts/run_scale20k_native_v1.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_inventory_cannot_self_approve_changed_bytes(tmp_path):
    p = tmp_path / 'inventory.json'
    value = {'version': 'scale20k-native-exclusions-v1',
             'requirements_sha256': state.REQUIREMENTS_SHA,
             'baseline_native_sha256': state.BASELINE_SHA,
             'qids': ['Q1'], 'input_hashes': ['a' * 64], 'source_database_refs': []}
    expected = write(p, value)
    assert state.load_inventory(p, expected)['qids'] == ['Q1']
    value['qids'] = []
    write(p, value)
    with pytest.raises(ValueError, match='inventory bytes'):
        state.load_inventory(p, expected)


def source_fixture(tmp_path, monkeypatch, *, external=False):
    base = tmp_path / 'v20'
    monkeypatch.setattr(state, 'BASE', base)
    monkeypatch.setattr(state, 'RUNTIME', tmp_path / 'runtime')
    source = base / 'classification-sources/window-0001-0100'
    source.mkdir(parents=True)
    inputs, ledger = source / 'inputs.jsonl', source / 'ledger.json'
    work = WorkInput(work_id='w1', title='A novel', source_record_ids=['s1'])
    inputs.write_text(work.model_dump_json() + '\n', encoding='utf-8')
    write(ledger, {'arbitrary': 'never grants actual model admission'})
    database = (tmp_path if external else source) / 'source.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE works(work_id TEXT,source_version_id TEXT)')
        db.execute('CREATE TABLE source_records(version_id TEXT,source TEXT,source_id TEXT)')
        db.execute("INSERT INTO works VALUES('w1','s1')")
        db.execute("INSERT INTO source_records VALUES('s1','wikidata','Q123')")
    write(source / 'work-mapping.json', [{'work_id': 'w1', 'input_work_id': 'w1',
        'wikidata_qid': 'Q123', 'catalogue_work_id': 'wd:Q123', 'input_sha256': model_hash(work)}])
    write(source / 'manifest.json', {'version': 'scale-content-classification-source-v1',
        'window_index': 93, 'selected': 1, 'required_purpose_admissions_allowed': True,
        'source_database': str(database), 'source_database_sha256': state.sha(database),
        'chunks': [{'run_name': 'scale20k-content-0001-0100-v1',
            'inputs_path': str(inputs), 'inputs_sha256': state.sha(inputs),
            'usage_ledger_path': str(ledger), 'usage_ledger_sha256': state.sha(ledger)}]})
    inventory = {'qids': [], 'input_hashes': [], 'source_database_refs':
                 [{'path': str(database.resolve()), 'sha256': state.sha(database)}] if external else []}
    return source, inventory


@pytest.mark.parametrize('changed', ['inputs', 'ledger', 'mapping', 'database', 'scope', 'selected_bool'])
def test_source_hash_identity_scope_and_strict_counts_fail_closed(tmp_path, monkeypatch, changed):
    source, inventory = source_fixture(tmp_path, monkeypatch)
    assert state.current_source(source, 93, state.sha(source / 'manifest.json'), inventory)[2] == {'Q123'}
    manifest = state.read(source / 'manifest.json')
    if changed in ('inputs', 'ledger'):
        key = 'inputs_path' if changed == 'inputs' else 'usage_ledger_path'
        Path(manifest['chunks'][0][key]).write_text('tampered')
    elif changed == 'mapping':
        rows = state.read(source / 'work-mapping.json')
        rows[0].update(wikidata_qid='Q999', catalogue_work_id='wd:Q999')
        write(source / 'work-mapping.json', rows)
    elif changed == 'database':
        with sqlite3.connect(manifest['source_database']) as db:
            db.execute("UPDATE source_records SET source_id='Q999'")
    else:
        manifest['required_purpose_admissions_allowed' if changed == 'scope' else 'selected'] = False if changed == 'scope' else True
        write(source / 'manifest.json', manifest)
    with pytest.raises(ValueError):
        state.current_source(source, 93, state.sha(source / 'manifest.json'), inventory)


def test_thin_external_db_needs_independently_pinned_allowlist(tmp_path, monkeypatch):
    source, inventory = source_fixture(tmp_path, monkeypatch, external=True)
    digest = state.sha(source / 'manifest.json')
    assert state.current_source(source, 93, digest, inventory)[2] == {'Q123'}
    with pytest.raises(ValueError, match='external source database'):
        state.current_source(source, 93, digest, {'source_database_refs': []})


def test_source_index_is_fixed_and_must_match_next_global_window(tmp_path):
    p = tmp_path / 'sources.json'
    digest = write(p, {'version': 'scale20k-source-index-v1', 'requirements_sha256': state.REQUIREMENTS_SHA,
        'windows': [{'window_index': 93, 'source_path': 'a', 'source_sha256': 'a' * 64}]})
    assert state.source_entry(p, digest, 93)['source_path'] == 'a'
    with pytest.raises(ValueError, match='not in fixed source index'):
        state.source_entry(p, digest, 94)


def test_preflight_failure_never_reaches_native_or_publication(tmp_path, monkeypatch):
    d = driver()
    events = []
    write(tmp_path / 'reservation', {'real_fixture_reservation': True})
    monkeypatch.setattr(state, 'reserve_window', lambda *a, **k: events.append('reserve') or tmp_path / 'reservation')
    def fail(*a, **k):
        events.append('preflight')
        raise ValueError('Actual child exit1')
    monkeypatch.setattr(d, 'observed_command', fail)
    fixed = {'selected': 1, 'chunks': [{'run_name': 'scale20k-content-0001-0100-v1',
              'inputs_path': 'input', 'usage_ledger_path': 'ledger'}]}
    with pytest.raises(ValueError, match='exit1'):
        d.run_window(source=tmp_path, source_sha='s', global_index=93, fixed=fixed,
            inventory_path=tmp_path / 'inventory', inventory_sha='i',
            previous_control=tmp_path / 'control', previous_sha='c', concurrency=4,
            publication=SimpleNamespace(finalize=lambda *a: events.append('import')))
    assert events == ['reserve', 'preflight']


def test_real_child_exit_and_birth_are_saved_without_retry(tmp_path, monkeypatch):
    d = driver()
    monkeypatch.setattr(state, 'process_created_at', lambda pid: 'observed-birth')
    out = tmp_path / 'process'
    with pytest.raises(ValueError, match='without retry'):
        d.observed_command([sys.executable, '-c', 'raise SystemExit(7)'], out)
    assert state.read(out / 'process-exit.json')['actual_exit_code'] == 7
    assert state.read(out / 'process-start.json')['process_created_at'] == 'observed-birth'
    assert state.read(out / 'request.json')['shell'] is False
    with pytest.raises(FileExistsError):
        d.observed_command([sys.executable, '-c', 'raise SystemExit(0)'], out)


def test_adaptive_slots_and_exact_remaining_never_expand_last_batch():
    d = driver()
    clean = {'failed': 0, 'pending': 0, 'initial_concurrency': 4, 'final_concurrency': 4}
    assert d.next_concurrency(clean, first=True) == 4
    assert d.next_concurrency(clean, first=False) == 8
    assert d.next_concurrency(dict(clean, failed=1), first=False) == 4
    assert d.source_fits_remaining({'selected': 7}, 7)
    assert not d.source_fits_remaining({'selected': 100}, 7)


def test_competing_controllers_reserve_only_once_under_shared_lock(tmp_path, monkeypatch):
    base = tmp_path / 'v20'
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
    monkeypatch.setattr(state, 'BASE', base)
    monkeypatch.setattr(state, 'RUNTIME', tmp_path / 'runtime')
    monkeypatch.setattr(state, 'DATABASE', database)
    monkeypatch.setattr(state, 'WRITER_LOCK', tmp_path / 'catalogue-writer.lock')
    monkeypatch.setattr(state, 'load_inventory', lambda *a: {'qids': [], 'input_hashes': []})
    monkeypatch.setattr(state, 'source_entry', lambda *a: {'source_path': str(tmp_path), 'source_sha256': 'source'})
    barrier = Barrier(2)
    def current(*args):
        barrier.wait(timeout=5)
        return {'chunks': [{'run_name': 'scale20k-content-0001-0100-v1'}]}, {'h'}, {'Q1'}
    monkeypatch.setattr(state, 'current_source', current)
    def reserve(_):
        try:
            return state.reserve_window(tmp_path, 93, 'source', tmp_path / 'inventory', 'inventory', tmp_path / 'index', 'index')
        except (ValueError, FileExistsError) as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sum(isinstance(item, Path) for item in outcomes) == 1
    assert len(list((base / 'attempt-reservations').glob('*.json'))) == 1
    assert not state.WRITER_LOCK.exists()


@pytest.mark.parametrize('slots', [4, 8])
def test_actual_generated_review_entry_reaches_enum_group_plan_without_model(tmp_path, monkeypatch, slots):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    wrapper = script('run_scale20k_content_classification_v1')
    operation = wrapper.recipe
    monkeypatch.setattr(operation, 'ROOT', tmp_path)
    path = tmp_path / 'scripts/review_scale_content_v6.py'
    path.parent.mkdir()
    path.write_bytes((ROOT / 'scripts/review_scale_content_v6.py').read_bytes())
    run = tmp_path / 'run'
    write(run / 'manifest.json', {'usage': {'input_tokens': 1, 'output_tokens': 1},
        'elapsed_seconds': 1, 'stop_reasons': [], 'completed': 11, 'final_concurrency': slots})
    observed = {'run_dir': run}
    for key in ('ordered_inputs', 'execution_context_manifest', 'terminal_proof', 'usage_ledger'):
        observed[key] = tmp_path / key
        observed[key].write_bytes(b'fixture')
    import review_scale_content_v6 as review
    monkeypatch.setattr(review, 'preview_prompt_chars', lambda args, ids: 10)
    def before_model(*args, **kwargs):
        raise RuntimeError('fixture model boundary reached')
    monkeypatch.setattr(review, 'load_bounded_review', before_model)
    calls = []
    def frozen(runtime, code, arguments):
        if 'select_review_work_ids' in code:
            return [f'w{i}' for i in range(11)]
        calls.append(code)
        monkeypatch.setattr(sys, 'argv', ['fixture', *map(str, arguments)])
        exec(compile(code, '<actual-v20-coordinator-review-entry>', 'exec'), {})
    operation.frozen_call = frozen
    with pytest.raises(RuntimeError, match='model boundary reached'):
        operation.review_content_window(tmp_path / 'runtime', observed, 'window', full=True)
    plan = tmp_path / 'data/autonomous-next-v3/scale-20000-classifications-v1/separate-review-sharded-v6/window/review-plan.json'
    assert plan.exists()
    selections = sorted(plan.parent.glob('group-*.selection.json'))
    assert [len(state.read(path)['selected_work_ids']) for path in selections] == [5, 5, 1]
    assert len(calls) == 1


def test_actual_preparer_child_program_imports_new_guarded_entry(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    wrapper = script('prepare_scale20k_content_chunk_v1')
    monkeypatch.setitem(sys.modules, 'prepare_scale20k_content_chunk_v1', wrapper)
    guard_recipe = wrapper.recipe
    monkeypatch.setattr(guard_recipe, 'ROOT', tmp_path)
    original = ModuleType('prepare_scale_content_chunk_v1')
    original.verify_frozen_runtime = lambda path: {'runtime': str(tmp_path / 'runtime')}
    original.sha = lambda path: 'fixed'
    monkeypatch.setitem(sys.modules, 'prepare_scale_content_chunk_v1', original)
    calls = []
    def guarded_boundary(*args):
        calls.append(args)
        raise RuntimeError('actual mandatory frozen guard boundary')
    monkeypatch.setattr(wrapper, 'frozen_prepare', guarded_boundary)
    def execute_child(argv, **kwargs):
        monkeypatch.setattr(sys, 'argv', ['-c', *argv[3:]])
        exec(compile(argv[2], '<actual-v20-preparer-child>', 'exec'), {})
    monkeypatch.setattr(guard_recipe.subprocess, 'run', execute_child)
    inputs, ledger = tmp_path / 'inputs', tmp_path / 'ledger'
    inputs.write_bytes(b'fixture'); ledger.write_bytes(b'fixture')
    with pytest.raises(RuntimeError, match='mandatory frozen guard boundary'):
        wrapper.prepare(tmp_path, inputs, ledger, 'scale20k-content-0001-0100-v1', 4)
    assert len(calls) == 1
    assert calls[0][-2:] == ('scale20k-content-0001-0100-v1', 4)


def test_recipe_drift_rejected_before_any_process(monkeypatch):
    monkeypatch.setattr(state, 'COORDINATOR_SHA', '0' * 64)
    with pytest.raises(ValueError, match='immutable recipe bytes'):
        state.load_recipe('coordinator', ROOT / 'scripts/run_scale20k_content_classification_v1.py')


def test_failed_actual_gate_is_saved_and_never_becomes_continuation(tmp_path, monkeypatch):
    d = driver()
    monkeypatch.setattr(state, 'BASE', tmp_path)
    monkeypatch.setattr(state, 'RUNTIME', tmp_path / 'runtime')
    monkeypatch.setattr(state, 'eligible_count', lambda: 10094)
    name = 'scale20k-content-0001-0100-v1'
    reservation = tmp_path / 'reservation.json'
    write(reservation, {'selected': 100})
    monkeypatch.setattr(state, 'reserve_window', lambda *a: reservation)
    run = tmp_path / 'run.json'
    write(run, {'selected': 100, 'completed': 95, 'failed': 5, 'usage': {}})
    native = {'separate_review': {'elapsed_seconds': 10}}
    write(tmp_path / 'classification-exports' / name / 'manifest.json', native)
    terminal = state.RUNTIME / ('terminal-' + name)
    write(terminal / 'process-start.json', {'started_at': '2026-10-22T00:00:00+00:00'})
    write(terminal / 'process-exit.json', {'observed_at': '2026-10-22T00:00:10+00:00',
        'actual_exit_code': 0, 'process_exit_observed': True})
    write(tmp_path / 'classification-exports-v3' / name / 'manifest.json',
          {'run_manifest': str(run), 'separate_review': {}, 'gate': {'passed': True}})
    receipt = {'gate': {'passed': False}, 'actual_display_labels_by_axis': {}}
    def finalize(*args):
        write(tmp_path / 'window-controls' / (name + '.json'), receipt)
        return receipt
    commands = []
    monkeypatch.setattr(d, 'observed_command', lambda argv, *a, **k: commands.append(argv))
    with pytest.raises(ValueError, match='Actual window gate failed'):
        d.run_window(source=tmp_path, source_sha='s', global_index=93,
            fixed={'chunks': [{'run_name': name, 'inputs_path': 'i', 'usage_ledger_path': 'l'}], 'selected': 100},
            inventory_path=tmp_path / 'inventory', inventory_sha='i', previous_control='prior',
            previous_sha='p', concurrency=4, publication=SimpleNamespace(finalize=finalize))
    assert len(commands) == 3  # prepare, native/review, rights; never another native window
    assert state.read(tmp_path / 'run-summaries' / (name + '.json'))['gate']['passed'] is False


def test_default_entry_starts_one_witnessed_worker_with_original_pins(tmp_path, monkeypatch):
    d = driver()
    monkeypatch.setattr(state, 'BASE', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['run_scale20k_native_v1.py', '--inventory', 'inventory',
        '--inventory-sha256', 'a' * 64, '--source-index', 'index', '--source-index-sha256', 'b' * 64])
    calls = []
    monkeypatch.setattr(d, 'observed_command', lambda argv, path, **kwargs: calls.append((argv, path, kwargs)))
    monkeypatch.setattr(d, 'run', lambda *args: pytest.fail('Parent witness must not run model controller'))
    d.main()
    assert len(calls) == 1
    argv, output, fields = calls[0]
    assert argv[-1] == '--worker'
    assert argv.count('--worker') == 1
    assert output.is_relative_to(tmp_path / 'controller-processes')
    assert fields['request_fields']['driver_sha256'] == state.sha(d.__file__)


def test_inventory_startup_requires_every_baseline_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'BASE', tmp_path)
    path = tmp_path / 'baseline-native-10000.jsonl'
    path.write_text('\n'.join(json.dumps({'qid': f'Q{i}'}) for i in range(10000)))
    inventory = {'qids': [f'Q{i}' for i in range(10000)]}
    state.validate_baseline_membership(inventory)
    inventory['qids'].pop()
    with pytest.raises(ValueError, match='10000 distinct baseline'):
        state.validate_baseline_membership(inventory)


def test_model_time_code_changes_are_rejected_without_pinning_ui(tmp_path, monkeypatch):
    path = tmp_path / 'fixed.py'
    path.write_text('fixed')
    monkeypatch.setattr(state, 'code_paths', lambda: [path])
    snapshot = state.code_snapshot()
    state.verify_code_snapshot(snapshot)
    path.write_text('changed')
    with pytest.raises(ValueError, match='changed during window'):
        state.verify_code_snapshot(snapshot)


def test_code_dependency_set_does_not_freeze_live_producers_ui_or_docs():
    paths = [str(path).replace('\\', '/') for path in state.code_paths()]
    assert not any('scale_catalogue_v1.py' in p or 'scale_site_v1.py' in p
                   or '/docs/' in p or 'README' in p or 'collect_' in p or 'materialize_' in p for p in paths)


@pytest.mark.parametrize('duplicate', ['qid', 'input_hash', 'catalogue'])
def test_old_held_failed_or_classified_works_are_never_reserved(tmp_path, monkeypatch, duplicate):
    source, inventory = source_fixture(tmp_path, monkeypatch)
    digest = state.sha(source / 'manifest.json')
    _, hashes, _ = state.current_source(source, 93, digest, inventory)
    inventory.update(qids=['Q123'] if duplicate == 'qid' else [],
                     input_hashes=list(hashes) if duplicate == 'input_hash' else [])
    catalogue = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(catalogue) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
        if duplicate == 'catalogue':
            db.execute("INSERT INTO native_classifications VALUES('Q123')")
    monkeypatch.setattr(state, 'DATABASE', catalogue)
    monkeypatch.setattr(state, 'WRITER_LOCK', tmp_path / 'writer.lock')
    monkeypatch.setattr(state, 'load_inventory', lambda *args: inventory)
    monkeypatch.setattr(state, 'source_entry', lambda *args: {'source_path': str(source), 'source_sha256': digest})
    with pytest.raises(ValueError, match='Already attempted|Existing native authority'):
        state.reserve_window(source, 93, digest, tmp_path / 'inventory', 'i', tmp_path / 'index', 's')
    assert not list((state.BASE / 'attempt-reservations').glob('*.json'))


@pytest.mark.parametrize('field', ['inputs_path', 'usage_ledger_path'])
def test_thin_database_permission_never_allows_old_input_or_ledger_paths(tmp_path, monkeypatch, field):
    source, inventory = source_fixture(tmp_path, monkeypatch)
    fixed = state.read(source / 'manifest.json')
    old = tmp_path / ('outside-' + field)
    old.write_bytes(Path(fixed['chunks'][0][field]).read_bytes())
    fixed['chunks'][0][field] = str(old)
    write(source / 'manifest.json', fixed)
    with pytest.raises(ValueError, match='inputs and ledger must stay in v20'):
        state.current_source(source, 93, state.sha(source / 'manifest.json'), inventory)


def test_unavailable_birth_is_preserved_as_unknown_not_fabricated(tmp_path, monkeypatch):
    d = driver()
    monkeypatch.setattr(state, 'process_created_at', lambda pid: None)
    result = d.observed_command([sys.executable, '-c', 'pass'], tmp_path / 'actual-child')
    assert result['actual_exit_code'] == 0
    assert result['process_created_at'] is None
    assert state.read(tmp_path / 'actual-child/process-start.json')['process_created_at'] is None
