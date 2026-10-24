"""Tiny offline tests for the offset-two adapter and genuine pre-model hold."""
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location('_audit3_fixture_' + path.stem, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')
    import hashlib
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def source_fixture(tmp_path, monkeypatch, index):
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    adapted = wrapper.adapted_auditor()
    helpers = load(ROOT / 'tests/test_audit_scale20k_catalogue_v2.py')
    old, context, old_public, receipt, calls = helpers.setup(tmp_path, monkeypatch, index=index)
    adapted.ROOT, adapted.BASE = old.ROOT, old.BASE
    adapted.UPSTREAM_INDEX_SHA, adapted.FAILED_RESERVATION_SHA = old.UPSTREAM_INDEX_SHA, old.FAILED_RESERVATION_SHA
    context.__class__ = adapted.RecoveryAudit
    context.code = {str((tmp_path / relative).resolve()): 'code-sha' for relative in adapted.NEW_CODE}
    context.recovery['pins']['excluded_unstarted_qids'] = ['Qheld' + str(i) for i in range(81)]
    context.recovery['pins']['excluded_failed_attempt_qids'] = ['Qfailed' + str(i) for i in range(15)]
    name = adapted.canonical_name(index)
    source = adapted.BASE / 'new-source'
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['chunks'][0]['run_name'] = name
    manifest['upstream_window_index'] = index + 2
    source_pin = save(source / 'manifest.json', manifest)
    policy_path = source / 'native-recovery-policy-v3.json'
    policy = json.loads(policy_path.read_bytes())
    policy.update(version='scale20k-source-recovery-eligibility-v4', new_run_name=name,
                  upstream_window_index=index + 2, source_manifest_sha256=source_pin['sha256'])
    policy['origin']['window_index'] = index + 2
    policy['origin']['run_name'] = adapted.canonical_name(index + 2, 'v1')
    origin = policy['origin']
    observed = []
    def validate_origin(**kwargs):
        observed.append(kwargs['origin_window_index'])
        assert kwargs['origin_window_index'] == index + 2
        return origin
    context.builder = SimpleNamespace(validate_origin=validate_origin)
    policy_pin = save(policy_path, policy)
    index_path = adapted.BASE / 'source-index.json'
    source_index = json.loads(index_path.read_bytes())
    source_index['windows'][0].update(source_sha256=source_pin['sha256'], eligibility_policy_sha256=policy_pin['sha256'])
    index_pin = save(index_path, source_index)
    old_reservation_path = adapted.BASE / 'attempt-reservations' / (old.canonical_name(index) + '.json')
    reservation = json.loads(old_reservation_path.read_bytes())
    reservation.update(version='scale20k-native-attempt-reservation-v3', run_name=name,
        source_sha256=source_pin['sha256'], source_index_sha256=index_pin['sha256'],
        excluded_failed_qids=context.recovery['pins']['excluded_failed_attempt_qids'] + context.recovery['pins']['excluded_unstarted_qids'])
    reservation_pin = save(adapted.BASE / 'attempt-reservations' / (name + '.json'), reservation)
    prior_name = adapted.canonical_name(index - 1, 'v1' if index == 161 else 'v3')
    prior_pin = save(adapted.BASE / 'window-controls' / (prior_name + '.json'),
                     {'window_index': index - 1, 'gate': {'passed': True}})
    if index == 161:
        context.recovery['pins']['prior_control'] = prior_pin
    document = json.loads(Path(old_public['path']).read_bytes())
    document.update(source_preparation_sha256=source_pin['sha256'], prior_control_receipt=prior_pin['path'],
        artifact_sha256={reservation_pin['path']: reservation_pin['sha256'], prior_pin['path']: prior_pin['sha256']})
    public_pin = save(adapted.BASE / 'classification-exports-v3' / name / 'manifest.json', document)
    pre = save(adapted.BASE / 'pre.json', {'source_manifest_sha256': source_pin['sha256']})
    receipt['control_artifacts'].update(export=public_pin, native_eligibility_source_observation=policy_pin,
        native_eligibility_pre_classification=pre,
        native_eligibility_controller={'path': str(tmp_path / 'scripts/run_scale20k_native_v4.py'), 'sha256': 'code-sha'},
        native_eligibility_source_builder={'path': str(tmp_path / adapted.NEW_CODE[-1]), 'sha256': 'code-sha'})
    save(adapted.BASE / 'window-controls' / (name + '.json'), receipt)
    return wrapper, adapted, context, public_pin, observed


@pytest.mark.parametrize('index', [161, 162])
def test_actual_adapted_source_dispatch_uses_offset2_and96_exclusions(tmp_path, monkeypatch, index):
    wrapper, adapted, context, public, observed = source_fixture(tmp_path, monkeypatch, index)
    assert adapted.canonical_name(161) == 'scale20k-content-6801-6900-v3'
    assert context.dispatch(public['path'], public['sha256'])['gate']['passed'] is True
    assert observed == [index + 2]
    assert context.verified == {index}


def test_adapter_rejects_changed_parent_before_exec():
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    with pytest.raises(ValueError, match='Frozen audit2'):
        wrapper.adapted_auditor(b'changed')


def test_frozen_old_generation_digests_have_exact64_hex_characters():
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    assert all(len(digest) == 64 and set(digest) <= set('0123456789abcdef')
               for digest in wrapper.OLD_GENERATION_CODE.values())


def test_authenticated_package_helper_load_supports_its_real_relative_import():
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    parent = wrapper.adapted_auditor()
    path = ROOT / 'src/recommend_book/scale20k_recovery_v2.py'
    helper = parent.load_module(path, wrapper.sha(path))
    assert helper.__package__ == 'recommend_book'
    assert helper._v1.ENTRY_VERSION == 'scale20k-recovery-entry-v1'
    assert callable(helper.verify_recovery_entry)


def test_package_helper_digest_failure_precedes_import_execution():
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    parent = wrapper.adapted_auditor()
    path = ROOT / 'src/recommend_book/scale20k_recovery_v2.py'
    with pytest.raises(ValueError, match='Frozen audit/recovery'):
        parent.load_module(path, '0' * 64)


def test_new_reservation_cannot_drop_unstarted81_from_its_exclusions(tmp_path, monkeypatch):
    wrapper, adapted, context, public, _ = source_fixture(tmp_path, monkeypatch, 161)
    document = json.loads(Path(public['path']).read_bytes())
    reservation_path = adapted.BASE / 'attempt-reservations' / (adapted.canonical_name(161) + '.json')
    reservation = json.loads(reservation_path.read_bytes())
    reservation['excluded_failed_qids'] = context.recovery['pins']['excluded_failed_attempt_qids']
    changed = save(reservation_path, reservation)
    document['artifact_sha256'][changed['path']] = changed['sha256']
    public = save(Path(public['path']), document)
    with pytest.raises(ValueError, match='reservation/recovery'):
        context._source({'control_artifacts': {}}, document, 161, adapted.canonical_name(161))


def test_new_namespace_cannot_extend_past213(tmp_path, monkeypatch):
    wrapper, adapted, context, _, _ = source_fixture(tmp_path, monkeypatch, 161)
    path = adapted.BASE / 'classification-exports-v3' / adapted.canonical_name(214) / 'manifest.json'
    pin = save(path, {})
    with pytest.raises(ValueError, match='window range'):
        context.dispatch(pin['path'], pin['sha256'])


def hold_fixture(tmp_path, monkeypatch):
    wrapper = load(ROOT / 'scripts/audit_scale20k_catalogue_v3.py')
    base = tmp_path / 'v20'
    monkeypatch.setattr(wrapper, 'BASE', base)
    qids = ['Qheld' + str(i) for i in range(81)]
    hashes = [f'{i:064x}' for i in range(81)]
    reservation = save(base / 'attempt-reservations/scale20k-content-6801-6900-v2.json', {
        'version': 'scale20k-native-attempt-reservation-v2', 'global_index': 161,
        'run_name': 'scale20k-content-6801-6900-v2', 'qids': qids, 'input_hashes': hashes})
    monkeypatch.setattr(wrapper, 'RESERVATION_SHA', reservation['sha256'])
    processes = {}
    for i, name in enumerate(('preflight', 'controller_worker', 'controller_observer', 'sequential_queue')):
        birth = '2026-10-23T08:11:19+00:00'
        start = save(tmp_path / name / 'start.json', {'pid': 100 + i, 'process_created_at': birth})
        end = save(tmp_path / name / 'exit.json', {'pid': 100 + i, 'process_created_at': birth,
            'actual_exit_code': 1, 'process_exit_observed': True})
        stderr = tmp_path / name / 'stderr.txt'
        stderr.write_text('ImportError: cannot import scale20k_native_v2', encoding='utf-8')
        processes[name] = {'start': start, 'exit': end, 'stderr': {'path': str(stderr), 'sha256': wrapper.sha(stderr)}}
    request = save(tmp_path / 'preflight-request.json', {'shell': False, 'argv': ['python', 'prepare.py']})
    absent = tmp_path / 'unstarted-native-manifest.json'
    hold = {'version': 'scale20k-unstarted-preflight-hold-v1', 'status': 'held_unstarted_runtime_import_failure',
        'window_index': 161, 'run_name': 'scale20k-content-6801-6900-v2', 'reservation': reservation,
        'selected': 81, 'qids': qids, 'input_hashes': hashes, 'model_calls': 0, 'same_inputs_retried': 0,
        'native_rows_for_selected': 0, 'eligible_before': 15952, 'eligible_after': 15952,
        'processes': processes, 'preflight_request': request, 'confirmed_absent_artifacts': [str(absent)]}
    hold_path = tmp_path / 'hold.json'
    pin = save(hold_path, hold)
    monkeypatch.setattr(wrapper, 'HOLD', hold_path)
    monkeypatch.setattr(wrapper, 'HOLD_SHA', pin['sha256'])
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
    recovery = {'unstarted_reservation': reservation, 'excluded_unstarted_qids': qids,
                'excluded_failed_attempt_qids': ['Qfailed' + str(i) for i in range(15)]}
    return wrapper, database, recovery, hold


def test_unstarted81_remains_separate_from_native_failure_count(tmp_path, monkeypatch):
    wrapper, database, recovery, hold = hold_fixture(tmp_path, monkeypatch)
    result = wrapper.verify_unstarted_hold(database, recovery)
    assert result['selected'] == 81
    assert result['model_calls'] == result['native_failures'] == result['catalogue_rows_remaining'] == 0
    assert result['failure_stage'] == 'preflight_before_model'


@pytest.mark.parametrize('changed', ['exit_bool', 'birth', 'process_missing', 'stderr_missing', 'model_artifact',
                                     'native_row', 'excluded81_changed', 'failed_overlap'])
def test_unstarted_hold_requires_actual_process_absence_and_no96_overlap(tmp_path, monkeypatch, changed):
    wrapper, database, recovery, hold = hold_fixture(tmp_path, monkeypatch)
    if changed in ('exit_bool', 'birth'):
        old = hold['processes']['preflight']['exit']
        end = json.loads(Path(old['path']).read_bytes())
        end['actual_exit_code' if changed == 'exit_bool' else 'process_created_at'] = True if changed == 'exit_bool' else 'different'
        hold['processes']['preflight']['exit'] = save(Path(old['path']), end)
        pin = save(wrapper.HOLD, hold)
        monkeypatch.setattr(wrapper, 'HOLD_SHA', pin['sha256'])
    elif changed == 'process_missing':
        hold['processes'].pop('controller_observer')
        pin = save(wrapper.HOLD, hold)
        monkeypatch.setattr(wrapper, 'HOLD_SHA', pin['sha256'])
    elif changed == 'stderr_missing':
        Path(hold['processes']['preflight']['stderr']['path']).unlink()
    elif changed == 'model_artifact':
        Path(hold['confirmed_absent_artifacts'][0]).write_bytes(b'native output must not exist')
    elif changed == 'native_row':
        with sqlite3.connect(database) as db:
            db.execute('INSERT INTO native_classifications VALUES(?)', (recovery['excluded_unstarted_qids'][0],))
    elif changed == 'excluded81_changed':
        recovery['excluded_unstarted_qids'] = recovery['excluded_unstarted_qids'][:-1]
    else:
        recovery['excluded_failed_attempt_qids'][0] = recovery['excluded_unstarted_qids'][0]
    with pytest.raises((ValueError, OSError)):
        wrapper.verify_unstarted_hold(database, recovery)
