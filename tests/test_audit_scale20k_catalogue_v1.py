"""Tiny, offline final-audit boundaries; never use the live catalogue."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_scale20k_catalogue_v1.py'


def module():
    spec = importlib.util.spec_from_file_location('audit20k_test', SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fixture(tmp_path, *, labels=None, content_ids=None):
    rows = []
    for qid in ('Q1', 'Q2'):
        rows.append({'qid': qid, 'work_id': 'wd:' + qid, 'labels': (labels or {}).get(qid, []),
                     'provenance': {'native_result': {'missing_axes': [{'axis': 'style'}]},
                                    'label_admission': {'held_labels': []}, 'artifacts': [],
                                    'content_evidence_ids': (content_ids or {}).get(qid, [])}})
    manifest = tmp_path / 'manifest.json'
    manifest.write_bytes(encoded({'version': 'scale-content-native-classification-export-v3',
        'verified_projection': {'rows': [dict(qid=r['qid'], work_id=r['work_id'],
            admitted_labels=r['labels'], **r['provenance']) for r in rows]}, 'artifact_sha256': {}}))
    sha = digest(manifest.read_bytes())
    canonical = []
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE works(qid TEXT PRIMARY KEY,version_id TEXT,display_allowed INTEGER,
            recommendable INTEGER,language TEXT,year INTEGER,row_json TEXT);
            CREATE TABLE source_versions(qid TEXT,version_id TEXT);
            CREATE TABLE native_classifications(qid TEXT PRIMARY KEY,manifest_path TEXT,manifest_sha256 TEXT,
            labels_json TEXT,provenance_json TEXT);
            CREATE TABLE work_genres(qid TEXT,genre_qid TEXT,label TEXT);''')
        for r in rows:
            qid = r['qid']
            db.execute('INSERT INTO works VALUES(?,?,1,0,?,?,?)',
                (qid, 'v' + qid, None, None, json.dumps({'region': []})))
            db.execute('INSERT INTO source_versions VALUES(?,?)', (qid, 'v' + qid))
            item = {'qid': qid, 'manifest_path': str(manifest), 'manifest_sha256': sha,
                    'labels_json': encoded(r['labels']).decode(),
                    'provenance_json': encoded(r['provenance']).decode()}
            canonical.append(item)
            db.execute('INSERT INTO native_classifications VALUES(?,?,?,?,?)', tuple(item.values()))
    raw_inputs = {
        'plan': b'fixed plan',
        'requirements': encoded({'version': 'scale20k-v20-requirements-v1', 'baseline': {'native': 1, 'stored': 2},
            'requirements': {'2': {'eligible_unique_native_sol_works_minimum': 2, 'new_accepted_works_minimum': 1,
                                  'preserve_existing_native_rows': 1}}}),
        'baseline_native': encoded({'qid': 'Q1', 'row_sha256': digest(encoded(canonical[0]))}) + b'\n',
        'baseline_works': b''.join(encoded({'qid': q, 'version_id': 'v' + q}) + b'\n' for q in ('Q1', 'Q2'))}
    pins = {}
    for name, raw in raw_inputs.items():
        path = tmp_path / (name + '.json')
        path.write_bytes(raw)
        pins[name] = {'path': str(path), 'sha256': digest(raw)}
    calls = []
    def verify(path, expected):
        calls.append((str(path), expected))
        return rows
    def control(path, expected):
        return {'control_artifacts': {'export': {'path': str(manifest), 'sha256': sha}},
                'gate': {'passed': True}}
    return module(), database, pins, rows, calls, verify, control


def run(f, **kwargs):
    m, database, pins, _, _, verify, control = f
    return m.audit(database=database, frozen=pins, manifest_verifier=verify,
                   control_verifier=control, **kwargs)


def test_unknown_source_genre_is_eligible_without_feature_or_negative_inference(tmp_path):
    f = fixture(tmp_path)
    report = run(f)
    assert report['status'] == 'complete'
    assert report['counts']['eligible_native'] == 2
    assert report['counts']['eligible_native_source_genre_unknown'] == 2
    assert report['counts']['display_feature_works'] == 0
    assert report['axes']['style']['explicit_unknown_works'] == 2
    assert report['axes']['theme']['explicit_unknown_works'] == 0
    assert report['axes']['theme']['unknown_unavailable_works'] == 2
    assert report['axes']['theme']['no_display_feature_works'] == 2
    assert len(f[4]) == 1  # One real verifier boundary per distinct manifest.


@pytest.mark.parametrize('axis,evidence_id,expected_new', [
    ('genre', 'content-Q2', 0),
    ('theme', 'metadata-Q2', 0),
    ('theme', 'content-Q2', 1),
])
def test_content_nongenre_requires_displayed_nongenre_label_and_exact_content_id(
        tmp_path, axis, evidence_id, expected_new):
    def label(axis, label_id, evidence_id):
        return {'axis': axis, 'label_id': label_id, 'decision': 'present', 'scope': 'work',
                'evidence': [{'evidence_id': evidence_id}]}
    f = fixture(tmp_path, labels={
        'Q1': [label('theme', 'theme.family', 'content-Q1')],
        'Q2': [label(axis, axis + '.test', evidence_id), label(axis, axis + '.other', evidence_id)]},
        content_ids={'Q1': ['content-Q1'], 'Q2': ['content-Q2']})
    report = run(f)
    assert report['status'] == 'complete'
    assert report['counts']['display_content_non_genre_feature_works'] == 1 + expected_new
    assert report['counts']['new_display_content_non_genre_feature_works'] == expected_new


@pytest.mark.parametrize('changed', ['baseline_file', 'native_row', 'source_ref'])
def test_baseline_tamper_or_lost_original_version_fails(tmp_path, changed):
    f = fixture(tmp_path)
    if changed == 'baseline_file':
        Path(f[2]['baseline_native']['path']).write_bytes(b'changed')
    else:
        with sqlite3.connect(f[1]) as db:
            if changed == 'native_row':
                db.execute("UPDATE native_classifications SET labels_json='[{}]' WHERE qid='Q1'")
            else:
                db.execute("DELETE FROM source_versions WHERE qid='Q1'")
    assert run(f)['status'] == 'violations'
    assert not f[4]  # Cheap baseline rejection precedes expensive replay.


def test_target_not_reached_does_not_start_full_proof_audit(tmp_path):
    f = fixture(tmp_path)
    with sqlite3.connect(f[1]) as db:
        db.execute("UPDATE works SET display_allowed=0 WHERE qid='Q2'")
    report = run(f)
    assert report['status'] == 'incomplete'
    assert report['proofs']['status'] == 'not_run_target_or_baseline'
    assert not f[4]


@pytest.mark.parametrize('changed', ['missing_proof', 'labels', 'provenance', 'identity', 'missing_row', 'missing_public_row'])
def test_proof_failure_or_sql_public_mismatch_fails_closed(tmp_path, changed):
    f = fixture(tmp_path)
    if changed == 'missing_proof':
        def broken(*args):
            raise FileNotFoundError('missing actual review/rights proof')
        f = (*f[:5], broken, f[6])
    elif changed == 'missing_public_row':
        def missing(*args):
            return f[3][:-1]
        f = (*f[:5], missing, f[6])
    elif changed == 'missing_row':
        with sqlite3.connect(f[1]) as db:
            db.execute("DELETE FROM native_classifications WHERE qid='Q2'")
        # Make target still fail rather than silently accept incomplete SQL coverage.
    else:
        if changed == 'labels':
            f[3][1]['labels'] = [{'axis': 'theme', 'label_id': 'theme.test'}]
        elif changed == 'provenance':
            f[3][1]['provenance']['extra'] = 'not persisted'
        else:
            f[3][1]['work_id'] = 'wd:FOREIGN'
    report = run(f)
    assert report['status'] in ('violations', 'incomplete')
    assert report['status'] != 'complete'


def test_production_dispatch_rejects_internal_content_v1(tmp_path):
    m = module()
    path = tmp_path / 'internal.json'
    path.write_bytes(encoded({'version': 'scale-content-native-classification-export-v1'}))
    with pytest.raises(ValueError, match='Unsupported public'):
        m.verify_public_manifest(path, digest(path.read_bytes()))


def test_controller_policy_failure_rejects_otherwise_valid_rows(tmp_path):
    f = fixture(tmp_path)
    def broken(*args):
        raise ValueError('policy flag mutation or controller pin changed')
    f = (*f[:6], broken)
    assert run(f)['status'] == 'violations'


def test_adopted_and_held_can_coexist_without_unknown_fabrication(tmp_path):
    f = fixture(tmp_path)
    r = f[3][1]
    label = {'axis': 'theme', 'label_id': 'theme.family', 'decision': 'present', 'scope': 'work'}
    r['labels'] = [label]
    r['provenance']['label_admission']['held_labels'] = [{'axis': 'theme', 'label_id': 'theme.loss'}]
    with sqlite3.connect(f[1]) as db:
        db.execute('UPDATE native_classifications SET labels_json=?,provenance_json=? WHERE qid=?',
                   (encoded(r['labels']).decode(), encoded(r['provenance']).decode(), r['qid']))
    # Match the authenticated public projection too.
    manifest = Path(f[4][0][0]) if f[4] else tmp_path / 'manifest.json'
    document = json.loads(manifest.read_bytes())
    document['verified_projection']['rows'][1].update(admitted_labels=r['labels'], **r['provenance'])
    manifest.write_bytes(encoded(document))
    sha = digest(manifest.read_bytes())
    with sqlite3.connect(f[1]) as db:
        db.execute('UPDATE native_classifications SET manifest_sha256=?', (sha,))
        baseline = dict(zip(('qid','manifest_path','manifest_sha256','labels_json','provenance_json'),
                            db.execute("SELECT * FROM native_classifications WHERE qid='Q1'").fetchone()))
    raw = encoded({'qid': 'Q1', 'row_sha256': digest(encoded(baseline))}) + b'\n'
    pin = f[2]['baseline_native']
    Path(pin['path']).write_bytes(raw)
    pin['sha256'] = digest(raw)
    def control(*args):
        return {'control_artifacts': {'export': {'path': str(manifest), 'sha256': sha}}, 'gate': {'passed': True}}
    report = run((*f[:6], control))
    assert report['status'] == 'complete'
    axis = report['axes']['theme']
    assert axis['adopted_labels'] == axis['adopted_works'] == 1
    assert axis['held_labels'] == axis['held_works'] == 1


def chain_fixture(tmp_path):
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded(value))
        return digest(path.read_bytes())
    runtime = tmp_path / 'runtime-preparation'
    run = runtime / 'runtime/data/autonomous-next-v3/classification/fixture/run/manifest.json'
    run_sha = save(run, {'run_id': 'native-run'})
    terminal = runtime / 'terminal-fixture/terminal-proof.json'
    coordinator = tmp_path / 'coordinator.py'
    coordinator.write_bytes(b'fixed actual coordinator')
    coordinator_sha = digest(coordinator.read_bytes())
    request_sha = save(terminal.with_name('request.json'), {
        'version': 'native-snapshot-terminal-request-v1', 'coordinator_path': str(coordinator),
        'coordinator_sha256': coordinator_sha})
    exit_sha = save(terminal.with_name('process-exit.json'), {'version': 'native-snapshot-terminal-exit-v1',
        'process_pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True})
    terminal_sha = save(terminal, {'schema_version': 'classification-terminal-proof-v1', 'run_id': 'native-run',
        'manifest_sha256': run_sha, 'exit_code': 0, 'process_exit_observed': True, 'process_pid': 123,
        'parent_request_sha256': request_sha, 'parent_exit_observation_sha256': exit_sha})
    document = {'version': 'scale-content-native-classification-export-v3', 'runtime_preparation': str(runtime),
        'run_manifest': str(run), 'coordinator_sha256': coordinator_sha,
        'artifact_sha256': {str(run): run_sha, str(terminal): terminal_sha}}
    path = tmp_path / 'public.json'
    save(path, document)
    return path, terminal, coordinator


@pytest.mark.parametrize('changed', ['missing_terminal', 'request_mismatch', 'coordinator_changed', 'missing_rights'])
def test_real_dispatch_rejects_missing_or_changed_actual_process_rights_proof(tmp_path, changed):
    m = module()
    path, terminal, coordinator = chain_fixture(tmp_path)
    if changed == 'missing_terminal':
        terminal.unlink()
    elif changed == 'request_mismatch':
        terminal.with_name('request.json').write_bytes(b'{}')
    elif changed == 'coordinator_changed':
        coordinator.write_bytes(b'changed actual coordinator')
    # For missing_rights, the actual chain passes, but the real existing public
    # rights verifier rejects missing rights process/code; no model is invoked.
    with pytest.raises((OSError, ValueError, KeyError)):
        m.verify_public_manifest(path, digest(path.read_bytes()))


@pytest.mark.parametrize('changed', ['none', 'source_flag', 'builder_pin', 'policy_downgrade', 'index_downgrade'])
def test_real_policy_dispatch_once_and_current_metadata_check(tmp_path, monkeypatch, changed):
    f = fixture(tmp_path)
    m, database, _, _, _, _, _ = f
    base = tmp_path / 'v20'
    run_name = 'scale20k-content-0401-0500-v1'
    public = base / 'classification-exports-v3' / run_name / 'manifest.json'
    public.parent.mkdir(parents=True)
    public.write_bytes(b'{}')
    public_sha = digest(public.read_bytes())
    controller = m._load_script('run_scale20k_native_v2.py')
    policy = tmp_path / 'policy.json'
    policy.write_bytes(encoded({'after_import': controller.catalogue_snapshot(database, ['Q1', 'Q2'])}))
    code = {}
    for name in m.POLICY_CODE_PINS:
        path = tmp_path / (name + '.py')
        path.write_bytes(b'fixed synthetic code')
        code[name] = {'path': str(path), 'sha256': digest(path.read_bytes())}
    receipt = {'window_index': 97, 'control_artifacts': {**code,
        'native_eligibility_policy': {'path': str(policy), 'sha256': digest(policy.read_bytes())},
        'export': {'path': str(public), 'sha256': public_sha}}, 'gate': {'passed': True}}
    if changed == 'policy_downgrade':
        receipt['control_artifacts'].pop('native_eligibility_policy')
    if changed == 'index_downgrade':
        receipt['window_index'] = 96
    control_path = base / 'window-controls' / (run_name + '.json')
    control_path.parent.mkdir(parents=True)
    control_path.write_bytes(encoded(receipt))
    calls = []
    def verify(path, sha):
        calls.append(path)
        return receipt
    monkeypatch.setattr(m, 'BASE', base)
    monkeypatch.setattr(m, 'POLICY_CODE_PINS', code)
    monkeypatch.setattr(m, '_load_script', lambda name: SimpleNamespace(
        verify_policy_control=verify, catalogue_snapshot=controller.catalogue_snapshot))
    if changed == 'source_flag':
        with sqlite3.connect(database) as db:
            db.execute("UPDATE works SET recommendable=1 WHERE qid='Q2'")
    if changed == 'builder_pin':
        Path(code['native_eligibility_source_builder']['path']).write_bytes(b'tampered')
    if changed == 'none':
        result = m.verify_window_control(public, public_sha, database=database)
        assert result['gate']['passed'] is True
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError):
            m.verify_window_control(public, public_sha, database=database)
        assert len(calls) == (1 if changed == 'source_flag' else 0)
