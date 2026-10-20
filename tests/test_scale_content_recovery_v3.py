"""The final recovery must preserve both failures and admit exactly five fresh works."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from recommend_book import scale_content_control_v4 as control
from recommend_book import scale_content_recovery_v1 as preserved
from recommend_book import scale_content_recovery_v2 as previous
from recommend_book import scale_content_recovery_v3 as recovery
from recommend_book.models import WorkInput
from recommend_book.usage_guard import model_hash

TAILS = Path(__file__).parent / 'fixtures/scale_content_recovery'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return recovery.sha(path)


def module(path):
    spec = importlib.util.spec_from_file_location('final_recovery_test_' + path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def failed_fixture(tmp_path, monkeypatch, public_passed=False):
    rows = [{'qid': 'Q' + str(i + 1), 'content_evidence_ids': ['plot'],
        'admitted_labels': ([{'axis': 'theme' if i < 69 else 'genre',
            'label_id': 'theme.growth' if i < 69 else 'genre.mystery',
            'evidence': [{'evidence_id': 'plot' if i < 69 else 'metadata',
                          'quote': 'Growing up' if i < 69 else 'mystery novel'}]}]
            if i < 74 else [])} for i in range(79)]
    public = {'version': 'scale-content-native-classification-export-v3',
        'coordinator_sha256': 'coordinator',
        'verified_projection': {'rows': rows, 'actual_feature_adopted_works': 74,
                               'actual_content_non_genre_feature_works': 69},
        'gate': {'passed': public_passed, 'actual_feature_adopted_works': 74,
                 'actual_content_non_genre_feature_works': 69}}
    public_sha = write(tmp_path / 'public.json', public)
    imported = {'stage': 'actual_catalogue_import_complete',
        'actual_process': {'pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True},
        'classification_manifest_sha256': public_sha,
        'result': {'raw_classified': 79, 'adopted': 74, 'failed': 0,
                   'display_policy_sha256': control.display_policy_sha256()}}
    values = {'export': public,
        'run': {'run_id': 'failed41', 'selected': 84, 'failed': 5, 'pending': 0,
                'initial_concurrency': 8, 'final_concurrency': 8,
                'stopped': False, 'stop_reasons': []},
        'import_report': imported, 'import:stdout.json': imported['result'],
        'import:process-start.json': {'pid': 123},
        'import:process-exit.json': imported['actual_process']}
    pins = {}
    for name, value in values.items():
        path = tmp_path / (name.replace(':', '-') + '.json')
        pins[name] = {'path': str(path.resolve()), 'sha256': write(path, value)}
    receipt = {'version': 'scale-content-window-control-v4', 'window_index': 40,
        'run_id': 'failed41', 'coordinator_sha256': 'coordinator',
        'next_review_mode': 'full', 'gate': copy.deepcopy(recovery.FAILED_GATE),
        'display_projection': control.display_counts(rows), 'control_artifacts': pins}
    path = tmp_path / 'failed-control.json'
    digest = write(path, receipt)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    return path, digest, receipt


def test_previous_recoveries_and_both_real_failure_denominators_are_preserved():
    assert preserved.RUN_NAME == 'scale30k-content-2301-2400'
    assert previous.RUN_NAME == 'scale30k-content-3701-3800'
    assert preserved.FAILED_GATE['failure_rate'] == .06
    assert previous.FAILED_GATE['failure_rate'] == .08
    assert recovery.RUN_NAME == 'scale30k-content-4101-4200'
    assert recovery.FAILED_GATE == control.actual_gate(84, 79, 74, 69)
    assert recovery.FAILED_GATE['failure_rate'] == 5 / 84
    assert len(recovery.QUARANTINED) == 5


def test_exact_failed_native_and_public_gates_stay_false(tmp_path, monkeypatch):
    path, digest, receipt = failed_fixture(tmp_path, monkeypatch)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='continuation gate failed'):
        control.verify_control_receipt(path, digest)
    assert recovery.verify_exact_failed_control(path, digest) == receipt
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='continuation gate failed'):
        control.verify_control_receipt(path, digest)


def test_an_earlier_public_pass_cannot_borrow_the_final_recovery(tmp_path, monkeypatch):
    path, digest, _ = failed_fixture(tmp_path, monkeypatch, public_passed=True)
    with pytest.raises(ValueError, match='continuation gate failed'):
        recovery.verify_exact_failed_control(path, digest)


@pytest.mark.parametrize('index', [22, 36, 41])
def test_another_failed_window_is_rejected_even_when_repinned(tmp_path, monkeypatch, index):
    path, _, receipt = failed_fixture(tmp_path, monkeypatch)
    receipt['window_index'] = index
    digest = write(path, receipt)
    monkeypatch.setattr(recovery, 'FAILED_SHA', digest)
    with pytest.raises(ValueError, match='exact measured'):
        recovery.verify_exact_failed_control(path, digest)


@pytest.mark.parametrize('kind', ['helper', 'runner', 'tail'])
def test_reused_code_bytes_must_match_before_any_native_call(kind):
    if kind == 'helper':
        adapt = recovery.adapted_source
    elif kind == 'runner':
        adapt = module(recovery.ROOT / 'scripts/run_scale_content_recovery_v3.py').adapted_runner_source
    else:
        adapt = module(TAILS / 'content-recovery-tail-v3.py').adapted_tail_source
    with pytest.raises(ValueError, match='changed'):
        adapt(b'changed')


def test_changed_anchor_is_not_silently_adapted(monkeypatch):
    raw = Path(preserved.__file__).read_bytes().replace(b'len(works) != 100', b'len(works) != 101')
    monkeypatch.setattr(recovery, 'V1_HELPER_SHA', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='anchor drift'):
        recovery.adapted_source(raw)


def test_frozen_reuse_has_no_ignored_operational_fixture_dependency(tmp_path, monkeypatch):
    tail = tmp_path / 'tail.py'
    tail.write_bytes((TAILS / 'content-recovery-tail-v1.py').read_bytes())
    paths = {'reused_helper_v1': Path(preserved.__file__),
             'reused_runner_v1': recovery.ROOT / 'scripts/run_scale_content_recovery_v1.py',
             'reused_tail_v1': tail}
    monkeypatch.setattr(recovery, 'frozen_reuse_pins',
                        lambda: {name: recovery._pin(path) for name, path in paths.items()})
    recovery.verify_frozen_reuse()
    tail.write_bytes(tail.read_bytes() + b'\n# changed\n')
    with pytest.raises(ValueError, match='changed'):
        recovery.verify_frozen_reuse()


def fresh_fixture(tmp_path, monkeypatch, count=5, duplicate=None):
    base = tmp_path / 'base'; base.mkdir()
    monkeypatch.setattr(recovery, 'BASE', base)
    works = [WorkInput(work_id='new' + str(i), title='Novel ' + str(i)) for i in range(count)]
    inputs = tmp_path / 'inputs.jsonl'
    inputs.write_text('\n'.join(work.model_dump_json() for work in works) + '\n', encoding='utf-8')
    mapping = tmp_path / 'mapping.json'
    write(mapping, [{'work_id': work.work_id, 'wikidata_qid': 'Q' + str(90000000 + i)}
                    for i, work in enumerate(works)])
    historical = base / 'native-input-hash-exclusions-v1.json'
    write(historical, {'input_hashes': [model_hash(works[0])] if duplicate == 'hash' else []})
    monkeypatch.setattr(recovery, 'HISTORICAL_SHA', recovery.sha(historical))
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
        if duplicate == 'qid':
            db.execute("INSERT INTO native_classifications VALUES('Q90000000')")
    return {'inputs_path': str(inputs), 'mapping_path': str(mapping),
            'runtime_preparation': str(tmp_path / 'runtime'), 'catalogue_path': str(database)}


def test_exactly_five_fresh_inputs_without_changing_the_84_work_failure(tmp_path, monkeypatch):
    census, _ = recovery._fresh_census(fresh_fixture(tmp_path, monkeypatch))
    assert len(census['new_input_hashes']) == len(census['new_qids']) == 5
    assert census['actual_input_overlap_count'] == census['actual_authority_overlap_count'] == 0
    assert recovery.FAILED_GATE['selected'] == 84
    assert control.actual_gate(5, 5, 4, 3)['passed'] is True
    assert control.actual_gate(5, 4, 4, 3)['passed'] is False


@pytest.mark.parametrize('count,duplicate', [(4, None), (6, None), (84, None),
                                          (5, 'hash'), (5, 'qid')])
def test_wrong_deficit_or_previous_hash_or_qid_cannot_be_submitted(tmp_path, monkeypatch, count, duplicate):
    with pytest.raises(ValueError, match='unique|already attempted|already present'):
        recovery._fresh_census(fresh_fixture(tmp_path, monkeypatch, count, duplicate))


@pytest.mark.parametrize('count', [4994, 4995, 4996])
def test_prelaunch_requires_exact_actual_4995_without_mutating_database(tmp_path, count):
    path = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE works(qid TEXT PRIMARY KEY,display_allowed INTEGER,recommendable INTEGER)')
        rows = [('Q' + str(i + 1),) for i in range(count)]
        db.executemany('INSERT INTO native_classifications VALUES(?)', rows)
        db.executemany('INSERT INTO works VALUES(?,1,1)', rows)
    before = path.read_bytes()
    if count == 4995:
        recovery._assert_exact_deficit(path)
    else:
        with pytest.raises(ValueError, match='exactly 4995'):
            recovery._assert_exact_deficit(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('session,code', [(12426, 1), (94137, 1), (12426, True)])
def test_exact_observed_stop_session_and_integer_exit_are_required(tmp_path, monkeypatch, session, code):
    driver = tmp_path / 'driver.py'; driver.write_text('# stopped')
    stop = {'version': 'scale-content-tool-observed-stop-v1', 'session_id': session,
            'actual_exit_code': code, 'process_exit_observed': True,
            'observation_source': 'exec.write_stdin terminal result',
            'failed_control_sha256': recovery.FAILED_SHA,
            'driver_path': str(driver.resolve()), 'driver_sha256': recovery.sha(driver)}
    path = tmp_path / 'stop.json'
    monkeypatch.setattr(recovery, 'STOP_SHA', write(path, stop))
    if session == 12426 and type(code) is int:
        assert recovery._verify_stop_observation(path) == stop
    else:
        with pytest.raises(ValueError, match='exit1 required'):
            recovery._verify_stop_observation(path)


@pytest.mark.parametrize('count', [4999, 5000, 5001])
def test_final_tail_cannot_open_another_ordinary_model_window(count):
    tail = module(TAILS / 'content-recovery-tail-v3.py')
    assert not hasattr(tail, 'ordinary_continuation')
    emitted = []
    ordinary = SimpleNamespace(public_eligible_count=lambda: count, emit=emitted.append)
    if count == 5000:
        result = tail.finish_at_target(ordinary)
        assert result['eligible_native_works'] == 5000
        assert result['ordinary_continuation_started'] is False
        assert emitted == [result]
    else:
        with pytest.raises(ValueError, match='exactly 5000'):
            tail.finish_at_target(ordinary)
        assert not emitted
