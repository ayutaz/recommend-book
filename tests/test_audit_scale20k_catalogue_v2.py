"""Offline recovery-audit fixtures; no catalogue/model/network mutation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_scale20k_catalogue_v2.py'


def module():
    spec = importlib.util.spec_from_file_location('audit20k_v2_fixture', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def save(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True), encoding='utf-8')
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def setup(tmp_path, monkeypatch, *, index=161):
    m = module()
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    base = tmp_path / 'v20'
    monkeypatch.setattr(m, 'BASE', base)
    context = m.RecoveryAudit.__new__(m.RecoveryAudit)
    context.database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(context.database) as db:
        db.execute('CREATE TABLE native_classifications(qid TEXT)')
    context.code = {str((tmp_path / relative).resolve()): 'code-sha' for relative in m.NEW_CODE}
    context.entry_pin = {'path': str(tmp_path / 'entry.json'), 'sha256': 'entry-sha'}
    context.entry = {}
    context.verified = set()
    context.control_counts = {}
    context.shortfalls = {}
    name = m.canonical_name(index)
    prior_name = m.canonical_name(index - 1, 'v1' if index == 161 else 'v2')
    prior = save(base / 'window-controls' / (prior_name + '.json'),
                 {'window_index': index - 1, 'gate': {'passed': True}})
    failed_qids = ['Qfailed' + str(i) for i in range(15)]
    context.recovery = {'pins': {'prior_control': prior, 'failed_qids': failed_qids[:14]}}
    failed_attempt = save(base / 'attempt-reservations/scale20k-content-6801-6900-v1.json', {
        'version': 'scale20k-native-attempt-reservation-v1', 'global_index': 161,
        'run_name': 'scale20k-content-6801-6900-v1', 'qids': failed_qids})
    monkeypatch.setattr(m, 'FAILED_RESERVATION_SHA', failed_attempt['sha256'], raising=False)
    mapping = [{'wikidata_qid': 'Qfresh', 'input_sha256': 'a' * 64}]
    source = base / 'new-source'
    mapping_pin = save(source / 'work-mapping.json', mapping)
    upstream_index = save(tmp_path / 'upstream-index.json', {'windows': []})
    monkeypatch.setattr(m, 'UPSTREAM_INDEX_SHA', upstream_index['sha256'])
    upstream_manifest = save(tmp_path / 'old-unattempted-source/manifest.json', {'untouched': True})
    source_manifest = save(source / 'manifest.json', {'window_index': index, 'selected': 1,
        'chunks': [{'run_name': name}], 'upstream_window_index': index + 1,
        'upstream_source_index_sha256': upstream_index['sha256'],
        'upstream_source_manifest_sha256': upstream_manifest['sha256']})
    snapshot = {'Qfresh': {'recommendable': 0, 'display_allowed': 1}}
    origin = {'window_index': index + 1, 'index_sha256': upstream_index['sha256'],
        'run_name': m.canonical_name(index + 1, 'v1'),
        'index_path': upstream_index['path'], 'source_path': str(Path(upstream_manifest['path']).parent),
        'source_sha256': upstream_manifest['sha256'], 'input_identity': {'Qfresh': 'a' * 64}}
    context.builder = SimpleNamespace(validate_origin=lambda **kwargs: origin)
    source_policy = save(source / 'native-recovery-policy-v3.json', {
        'version': 'scale20k-source-recovery-eligibility-v3', 'new_window_index': index,
        'new_run_name': name, 'source_manifest_sha256': source_manifest['sha256'],
        'source_manifest_path': source_manifest['path'], 'selected': 1, 'qids': ['Qfresh'],
        'catalogue_before': snapshot, 'catalogue_after': snapshot, 'upstream_window_index': index + 1,
        'origin': origin, 'bindings': {'work_mapping': mapping_pin},
        'source_builder_sha256': 'code-sha', 'observer_sha256': 'code-sha',
        'required_purpose_admissions_allowed': True, 'metadata_mutations': 0})
    index_pin = save(base / 'source-index.json', {'windows': [{'window_index': index,
        'source_path': str(source), 'source_sha256': source_manifest['sha256'],
        'eligibility_policy_sha256': source_policy['sha256']}]})
    policy = save(base / 'policy.json', {'before_import': snapshot, 'after_import': snapshot})
    pre = save(base / 'pre.json', {'source_manifest_sha256': source_manifest['sha256']})
    reservation = save(base / 'attempt-reservations' / (name + '.json'), {
        'version': 'scale20k-native-attempt-reservation-v2', 'global_index': index, 'run_name': name,
        'recovery_entry': context.entry_pin, 'actual_overlap_count': 0, 'helper_sha256': 'code-sha',
        'excluded_failed_qids': failed_qids, 'source_path': str(source), 'source_sha256': source_manifest['sha256'],
        'source_index_path': index_pin['path'], 'source_index_sha256': index_pin['sha256'],
        'qids': ['Qfresh'], 'input_hashes': ['a' * 64]})
    public = save(base / 'classification-exports-v3' / name / 'manifest.json', {
        'prior_control_receipt': prior['path'],
        'source_preparation': str(source), 'source_preparation_sha256': source_manifest['sha256'],
        'artifact_sha256': {reservation['path']: reservation['sha256'], prior['path']: prior['sha256']}})
    receipt = {'window_index': index, 'gate': {'passed': True, 'accepted': 1}, 'control_artifacts': {
        'export': public, 'native_eligibility_policy': policy,
        'native_eligibility_pre_classification': pre, 'native_eligibility_source_observation': source_policy,
        'native_eligibility_controller': {'path': str(tmp_path / 'scripts/run_scale20k_native_v3.py'), 'sha256': 'code-sha'},
        'native_eligibility_source_builder': {'path': str(tmp_path / m.NEW_CODE[-1]), 'sha256': 'code-sha'}}}
    save(base / 'window-controls' / (name + '.json'), receipt)
    calls = []
    def verify(path, expected):
        calls.append(('recovery', str(path)))
        return receipt
    context.driver = SimpleNamespace(verify_policy_control=verify,
        catalogue_snapshot=lambda database, qids: snapshot)
    def ordinary(path, expected, database):
        calls.append(('ordinary', str(path)))
        raise ValueError('ordinary failed control remains rejected')
    context.audit1 = SimpleNamespace(verify_window_control=ordinary)
    return m, context, public, receipt, calls


@pytest.mark.parametrize('index', [161, 162])
def test_real_recovery_dispatch_keeps_unknown_genre_and_strict_prior(tmp_path, monkeypatch, index):
    m, context, public, receipt, calls = setup(tmp_path, monkeypatch, index=index)
    assert context.dispatch(public['path'], public['sha256'])['gate']['passed'] is True
    assert context.verified == {index}
    assert len(calls) == 1


@pytest.mark.parametrize('changed', ['failed_parent', 'entry', 'source_index', 'upstream', 'metadata',
                                     'code_pin', 'gate', 'canonical_origin', 'failed_input_hash'])
def test_recovery_cannot_hide_wrong_parent_source_entry_metadata_or_failed_gate(tmp_path, monkeypatch, changed):
    m, context, public, receipt, calls = setup(tmp_path, monkeypatch)
    document = json.loads(Path(public['path']).read_bytes())
    if changed == 'failed_parent':
        document['prior_control_receipt'] = str(m.BASE / 'window-controls' / (m.canonical_name(161, 'v1') + '.json'))
        public = save(Path(public['path']), document)
        receipt['control_artifacts']['export'] = public
    elif changed == 'entry':
        context.entry_pin = {**context.entry_pin, 'sha256': 'different sealed entry'}
    elif changed == 'source_index':
        (m.BASE / 'source-index.json').write_bytes(b'changed')
    elif changed == 'upstream':
        (tmp_path / 'upstream-index.json').write_bytes(b'changed')
    elif changed == 'metadata':
        context.driver.catalogue_snapshot = lambda *args: {'Qfresh': {'recommendable': 1, 'display_allowed': 1}}
    elif changed == 'code_pin':
        receipt['control_artifacts']['native_eligibility_controller']['sha256'] = 'other-code'
    elif changed == 'canonical_origin':
        context.builder.validate_origin = lambda **kwargs: {'input_identity': 'wrong upstream identity'}
    elif changed == 'failed_input_hash':
        context.entry['failed_input_hashes'] = ['a' * 64]
    else:
        receipt['gate']['passed'] = False
    with pytest.raises(ValueError):
        context.dispatch(public['path'], public['sha256'])
    assert not context.verified


def test_old_failed_window_still_uses_unmodified_ordinary_verifier(tmp_path, monkeypatch):
    m, context, _, _, calls = setup(tmp_path, monkeypatch)
    old = save(m.BASE / 'classification-exports-v3' / m.canonical_name(161, 'v1') / 'manifest.json', {})
    with pytest.raises(ValueError, match='ordinary failed'):
        context.dispatch(old['path'], old['sha256'])
    assert calls == [('ordinary', old['path'])]


def test_after_bootstrap_cannot_use_a_v1_parent(tmp_path, monkeypatch):
    m, context, public, receipt, _ = setup(tmp_path, monkeypatch, index=162)
    document = json.loads(Path(public['path']).read_bytes())
    wrong = save(m.BASE / 'window-controls' / (m.canonical_name(161, 'v1') + '.json'),
                 {'window_index': 161, 'gate': {'passed': True}})
    document['prior_control_receipt'] = wrong['path']
    document['artifact_sha256'][wrong['path']] = wrong['sha256']
    public = save(Path(public['path']), document)
    receipt['control_artifacts']['export'] = public
    with pytest.raises(ValueError, match='preceding passed v2'):
        context.dispatch(public['path'], public['sha256'])


@pytest.mark.parametrize('changed', ['only_quarantine14', 'failed_reservation_changed', 'quarantine_outside_attempt'])
def test_failed_reservation_requires_authenticated15_and_quarantine14_subset(tmp_path, monkeypatch, changed):
    m, context, public, receipt, _ = setup(tmp_path, monkeypatch)
    if changed == 'only_quarantine14':
        document = json.loads(Path(public['path']).read_bytes())
        reservation_path = m.BASE / 'attempt-reservations' / (m.canonical_name(161) + '.json')
        reservation = json.loads(reservation_path.read_bytes())
        reservation['excluded_failed_qids'] = context.recovery['pins']['failed_qids']
        new_pin = save(reservation_path, reservation)
        document['artifact_sha256'][new_pin['path']] = new_pin['sha256']
        public = save(Path(public['path']), document)
        receipt['control_artifacts']['export'] = public
    elif changed == 'failed_reservation_changed':
        (m.BASE / 'attempt-reservations/scale20k-content-6801-6900-v1.json').write_bytes(b'changed')
    else:
        context.recovery['pins']['failed_qids'][-1] = 'Qoutside_failed_attempt'
    with pytest.raises(ValueError):
        context.dispatch(public['path'], public['sha256'])


def test_fixed_parent_auditor_rejects_code_drift_before_execution(tmp_path):
    m = module()
    path = tmp_path / 'auditor.py'
    path.write_bytes(b'raise AssertionError("must never execute")')
    with pytest.raises(ValueError, match='Frozen audit'):
        m.load_module(path, m.AUDIT1_SHA)


def test_sealed_code_pins_fail_closed_when_changed(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    monkeypatch.setattr(m, 'NEW_CODE', ('new.py',))
    monkeypatch.setattr(m, 'OLD_CODE', {})
    path = tmp_path / 'new.py'
    path.write_bytes(b'fixed')
    entry = {'code_pins': {str(path): m.sha(path)}}
    assert m.code_pins(entry) == entry['code_pins']
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='code pin'):
        m.code_pins(entry)


@pytest.mark.parametrize('present', [False, True])
def test_excluded_failed_window_keeps_false_gate_and_requires_all14_absent(tmp_path, monkeypatch, present):
    m, context, _, _, _ = setup(tmp_path, monkeypatch)
    qids = ['Qfailed' + str(i) for i in range(14)]
    gate = {'passed': False, 'selected': 15, 'accepted': 14, 'failure_rate': 1 / 15}
    summary = save(tmp_path / 'failed-summary.json', {'gate': gate})
    control = save(tmp_path / 'failed-control.json', {'gate': gate})
    export = save(tmp_path / 'failed-export.json', {'gate': {'passed': False},
        'verified_projection': {'rows': [{'qid': qid} for qid in qids]}})
    quarantine = save(tmp_path / 'quarantine.json', {'failed_gate': gate, 'remaining': 15952})
    rows = save(tmp_path / 'rows.json', {'rows': [{'qid': qid} for qid in qids]})
    diagnostic = save(tmp_path / 'diagnostic.json', {'summary': summary, 'control': control, 'export': export,
        'quarantine': quarantine, 'actual_gate': gate, 'native_completed': 14, 'native_failed': 1,
        'failure_category': 'provider_capacity', 'attempts': 1, 'same_failed_inputs_retried': 0})
    monkeypatch.setattr(m, 'DIAGNOSTIC', Path(diagnostic['path']))
    monkeypatch.setattr(m, 'DIAGNOSTIC_SHA', diagnostic['sha256'])
    context.recovery['pins'].update(failed_control=control, quarantine=quarantine, quarantine_rows=rows, failed_qids=qids)
    if present:
        with sqlite3.connect(context.database) as db:
            db.execute('INSERT INTO native_classifications VALUES(?)', (qids[0],))
        with pytest.raises(ValueError, match='failed native rows'):
            context.excluded_failed_window()
    else:
        output = context.excluded_failed_window()
        assert output['removed'] == 14
        assert output['catalogue_rows_remaining'] == 0
        assert output['failed_gate'] == gate  # Never transform the failure into pass.
        assert output['export'] == export


def test_wrapper_uses_fixed_baseline_target_and_public_logic_once(tmp_path, monkeypatch):
    m = module()
    old_tests = SCRIPT.parents[1] / 'tests/test_audit_scale20k_catalogue_v1.py'
    spec = importlib.util.spec_from_file_location('_old_audit_tiny_fixtures', old_tests)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    parent, database, frozen, rows, calls, verify, control = helpers.fixture(tmp_path)
    assert m.sha(m.AUDIT1) == m.AUDIT1_SHA
    monkeypatch.setattr(parent, 'FROZEN', frozen)
    monkeypatch.setattr(parent, 'verify_public_manifest', verify)
    context = m.RecoveryAudit.__new__(m.RecoveryAudit)
    context.audit1, context.database, context.verified = parent, database, set()
    context.entry_pin = save(tmp_path / 'entry.json', {})
    context.entry = {}
    context.excluded = {'failed_gate': {'passed': False}, 'catalogue_rows_remaining': 0}
    context.recovery = {'pins': {'failed_qids': ['Qnotpresent']}}
    monkeypatch.setattr(m, 'code_pins', lambda entry: {})
    def dispatch(path, expected):
        context.verified.add(161)
        return control(path, expected)
    context.dispatch = dispatch
    output = context.audit()
    assert output['status'] == 'complete'
    assert output['counts']['eligible_native'] == 2
    assert output['counts']['new_eligible_native'] == 1
    assert len(calls) == 1
    with sqlite3.connect(database) as db:
        db.execute("UPDATE native_classifications SET labels_json='changed' WHERE qid='Q1'")
    output = context.audit()
    assert output['status'] == 'violations'
    assert len(calls) == 1  # Changed baseline stops before any additional replay.


def shortfall_fixture(tmp_path):
    m = module()
    context = m.RecoveryAudit.__new__(m.RecoveryAudit)
    index = 202
    name = m.canonical_name(index)
    context.verified = set(range(161, index + 1))
    context.control_counts = {i: 99 for i in range(161, index)}
    context.control_counts[index - 1] = 87  # 15952 + 40*99 +87 =19999.
    context.shortfalls = {}
    context.builder = SimpleNamespace(_shortfall_selection=lambda document:
        {row['qid']: row['input_sha256'] for row in document['selected']})
    prior = save(tmp_path / 'prior-control.json', {'window_index': index - 1, 'gate': {'passed': True}})
    identity = {'Qchosen': 'a' * 64}
    origin = {'selected': 2, 'input_identity': {**identity, 'Qunused': 'b' * 64},
              'index_sha256': 'index-sha', 'source_sha256': 'source-sha'}
    receipt = {'version': 'scale20k-exact-shortfall-v1', 'eligible_native_count': 19999,
        'target_eligible_native': 20000, 'remaining': 1, 'run_index': index, 'run_name': name,
        'selected': [{'qid': 'Qchosen', 'input_sha256': 'a' * 64}],
        'origin_index_sha256': origin['index_sha256'], 'origin_source_sha256': origin['source_sha256'],
        'prior_control': prior, 'existing_native_overlap_count': 0, 'existing_attempt_overlap_count': 0}
    pin = save(tmp_path / 'exact-shortfall.json', receipt)
    policy = {'exact_shortfall_receipt': {**receipt, **pin}, 'upstream_subset_status': 'unused_final_shortfall',
        'upstream_subset_holds': None, 'unused_final_shortfall': [
            {'qid': 'Qunused', 'input_sha256': 'b' * 64, 'status': 'unused_final_shortfall'}]}
    public = {'prior_control_receipt': prior['path'], 'artifact_sha256': {prior['path']: prior['sha256']}}
    return m, context, policy, origin, identity, index, name, public


def test_exact_shortfall_authenticates_historical_count_after_import_without_current_db(tmp_path):
    m, context, policy, origin, identity, index, name, public = shortfall_fixture(tmp_path)
    context._shortfall(policy, origin, identity, index, name, public)
    output = context._shortfall_history()
    assert output[0]['authenticated_eligible_before'] == 19999
    assert output[0]['selected'] == 1
    # No database or current-count API exists on this context: the check uses
    # the immutable quarantine anchor and the genuinely verified prior counts.


@pytest.mark.parametrize('changed', ['history_count', 'missing_parent', 'wrong_prior', 'guard_hold_omission',
                                     'omission_identity', 'changed_receipt', 'chosen_outside_origin'])
def test_exact_shortfall_rejects_wrong_history_parent_or_unused_identity(tmp_path, changed):
    m, context, policy, origin, identity, index, name, public = shortfall_fixture(tmp_path)
    if changed == 'history_count':
        context.control_counts[index - 1] -= 1
    elif changed == 'missing_parent':
        context.verified.remove(index - 1)
    elif changed == 'wrong_prior':
        public['prior_control_receipt'] = str(tmp_path / 'foreign-control.json')
    elif changed == 'guard_hold_omission':
        policy['unused_final_shortfall'][0]['status'] = 'guard_held'
    elif changed == 'omission_identity':
        policy['unused_final_shortfall'][0]['input_sha256'] = 'c' * 64
    elif changed == 'changed_receipt':
        Path(policy['exact_shortfall_receipt']['path']).write_bytes(b'changed')
    else:
        identity['Qoutside'] = identity.pop('Qchosen')
    with pytest.raises(ValueError):
        context._shortfall(policy, origin, identity, index, name, public)
        context._shortfall_history()
