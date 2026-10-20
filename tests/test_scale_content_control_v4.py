"""Publication adoption and conservative catalogue display have distinct counts."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book import scale_content_control_v4 as control


def save(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def label(label_id, quote, evidence_id='metadata'):
    return {'label_id': label_id, 'axis': label_id.split('.')[0],
            'evidence': [{'evidence_id': evidence_id, 'quote': quote}]}


def rows():
    return [{'qid': 'Q1', 'content_evidence_ids': [],
             'admitted_labels': [label('genre.romance', 'romantic fiction')]},
            {'qid': 'Q2', 'content_evidence_ids': ['plot'],
             'admitted_labels': [label('theme.growth', 'Growing up', 'plot')]},
            {'qid': 'Q3', 'content_evidence_ids': ['plot'],
             'admitted_labels': [label('genre.romance', 'romantic fiction'),
                                 label('theme.journey', 'A journey', 'plot')]},
            {'qid': 'Q4', 'content_evidence_ids': [],
             'admitted_labels': [label('genre.romance', 'A romance novel')]}]


def receipt(tmp_path):
    projection = {'rows': rows(), 'actual_feature_adopted_works': 4,
                  'actual_content_non_genre_feature_works': 2}
    public = {'version': 'scale-content-native-classification-export-v3',
              'coordinator_sha256': 'coordinator', 'gate': {
                  'passed': True, 'actual_feature_adopted_works': 4,
                  'actual_content_non_genre_feature_works': 2},
              'verified_projection': projection}
    run = {'run_id': 'run', 'selected': 4, 'failed': 0, 'pending': 0,
           'initial_concurrency': 8, 'final_concurrency': 8,
           'stopped': False, 'stop_reasons': []}
    public_sha = save(tmp_path / 'export.json', public)
    run_sha = save(tmp_path / 'run.json', run)
    imported = {'stage': 'actual_catalogue_import_complete',
                'actual_process': {'actual_exit_code': 0, 'process_exit_observed': True, 'pid': 123},
                'classification_manifest_sha256': public_sha,
                'result': {'raw_classified': 4, 'adopted': 3, 'failed': 0,
                           'display_policy_sha256': control.display_policy_sha256()}}
    import_sha = save(tmp_path / 'import.json', imported)
    stdout_sha = save(tmp_path / 'import-stdout.json', imported['result'])
    start_sha = save(tmp_path / 'import-start.json', {'pid': 123})
    exit_sha = save(tmp_path / 'import-exit.json', imported['actual_process'])
    document = {'version': 'scale-content-window-control-v4', 'window_index': 3,
                'run_id': 'run', 'coordinator_sha256': 'coordinator',
                'gate': control.actual_gate(4, 4, 3, 2),
                'display_projection': control.display_counts(rows()),
                'control_artifacts': {
                    name: {'path': str((tmp_path / filename).resolve()), 'sha256': digest}
                    for name, filename, digest in [
                        ('export', 'export.json', public_sha), ('run', 'run.json', run_sha),
                        ('import_report', 'import.json', import_sha),
                        ('import:stdout.json', 'import-stdout.json', stdout_sha),
                        ('import:process-start.json', 'import-start.json', start_sha),
                        ('import:process-exit.json', 'import-exit.json', exit_sha)]}}
    path = tmp_path / 'control.json'
    return path, save(path, document), document


def test_existing_conservative_display_hold_can_continue_without_reimport(tmp_path):
    original = rows()
    before = copy.deepcopy(original)
    counts = control.display_counts(original)
    assert original == before
    assert counts['published_feature_works'] == 4
    assert counts['actual_display_feature_works'] == 3
    assert counts['published_content_non_genre_feature_works'] == 2
    assert counts['actual_content_non_genre_feature_works'] == 2
    assert counts['policy_sha256'] == control.display_policy_sha256()
    assert counts['held_labels'] == [
        {'qid': qid, 'label_id': 'genre.romance',
         'reason': 'ambiguous_romantic_fiction_citation'} for qid in ('Q1', 'Q3')]
    path, digest, expected = receipt(tmp_path)
    assert control.verify_control_receipt(path, digest) == expected


@pytest.mark.parametrize('field,value', [
    ('policy_sha256', '0' * 64), ('actual_display_feature_works', 4),
    ('published_feature_works', 3), ('actual_content_non_genre_feature_works', 1),
    ('held_labels', [])])
def test_display_receipt_must_equal_current_policy_applied_to_pinned_rows(tmp_path, field, value):
    path, _, document = receipt(tmp_path)
    document['display_projection'][field] = value
    with pytest.raises(ValueError, match='display projection'):
        control.verify_control_receipt(path, save(path, document))


@pytest.mark.parametrize('field,value', [('actual_feature_adopted_works', 3),
                                      ('actual_content_non_genre_feature_works', 1)])
def test_declared_public_counts_are_recomputed_even_if_gate_matches(tmp_path, field, value):
    path, _, document = receipt(tmp_path)
    public_path = tmp_path / 'export.json'
    public = json.loads(public_path.read_bytes())
    public['verified_projection'][field] = public['gate'][field] = value
    document['control_artifacts']['export']['sha256'] = save(public_path, public)
    with pytest.raises(ValueError, match='publication counts'):
        control.verify_control_receipt(path, save(path, document))


def test_import_counts_cannot_be_forged_to_match_unfiltered_public_count(tmp_path):
    path, _, document = receipt(tmp_path)
    imported_path = tmp_path / 'import.json'
    imported = json.loads(imported_path.read_bytes())
    imported['result']['adopted'] = 4
    document['control_artifacts']['import_report']['sha256'] = save(imported_path, imported)
    with pytest.raises(ValueError, match='import/control'):
        control.verify_control_receipt(path, save(path, document))


def test_duplicate_work_rows_are_not_counted_twice():
    original = rows()
    with pytest.raises(ValueError, match='Duplicate'):
        control.display_counts([*original, original[0]])


def observe_fixture(tmp_path, document):
    public_path = tmp_path / 'export.json'
    digest = document['control_artifacts']['export']['sha256']
    database = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE works(qid TEXT,display_allowed INTEGER,recommendable INTEGER)')
        db.execute('CREATE TABLE native_classifications(qid TEXT,labels_json TEXT,manifest_sha256 TEXT)')
        for row in rows():
            db.execute('INSERT INTO works VALUES(?,1,1)', (row['qid'],))
            db.execute('INSERT INTO native_classifications VALUES(?,?,?)',
                       (row['qid'], json.dumps(row['admitted_labels']), digest))
    details = control.observe_catalogue_display(database, public_path, digest)
    imported = json.loads((tmp_path / 'import.json').read_bytes())
    del imported['result']['display_policy_sha256']
    pins = document['control_artifacts']
    pins['import_report']['sha256'] = save(tmp_path / 'import.json', imported)
    pins['import:stdout.json']['sha256'] = save(tmp_path / 'import-stdout.json', imported['result'])
    actual = {'pid': 789, 'actual_exit_code': 0, 'process_exit_observed': True}
    observed = {'stage': 'actual_catalogue_display_observed',
                'binding_phase': 'post_import_current_catalogue', 'actual_process': actual,
                'classification_manifest_sha256': digest,
                'display_projection': details['display_projection'], 'observation': details}
    artifacts = {
        'display_observation': observed,
        'display:stdout.json': details,
        'display:process-start.json': {'pid': 789},
        'display:process-exit.json': actual,
        'import:request.json': {'argv': ['python', '-c', 'import', str(database.resolve()), str(public_path.resolve()), digest]},
        'display:request.json': {'argv': ['python', '-c', 'observe', str(database.resolve()), str(public_path.resolve()), digest],
                                 'shell': False, 'read_only': True}}
    for name, value in artifacts.items():
        path = tmp_path / (name.replace(':', '-') + '.json')
        pins[name] = {'path': str(path.resolve()), 'sha256': save(path, value)}
    pins['display:observer_code'] = {'path': details['observer_code_path'],
                                     'sha256': details['observer_code_sha256']}
    return observed, database


def test_historic_import_uses_read_only_current_observation_without_forging_old_report(tmp_path):
    path, _, document = receipt(tmp_path)
    observed, database = observe_fixture(tmp_path, document)
    before = database.read_bytes()
    assert control.verify_control_receipt(path, save(path, document)) == document
    assert database.read_bytes() == before
    assert observed['binding_phase'] == 'post_import_current_catalogue'
    assert observed['observation']['matched_eligible_works'] == 4


@pytest.mark.parametrize('change', ['database', 'manifest', 'missing_row', 'labels', 'policy_before', 'policy_after'])
def test_historic_observation_must_match_actual_import_database_and_exact_rows(tmp_path, change):
    path, _, document = receipt(tmp_path)
    observed, _ = observe_fixture(tmp_path, document)
    details = observed['observation']
    if change == 'database':
        details['database_path'] = str(tmp_path / 'copy.sqlite')
    elif change == 'manifest':
        details['classification_manifest_sha256'] = '0' * 64
    elif change == 'missing_row':
        details['persisted_rows'].pop()
    elif change == 'labels':
        details['persisted_rows'][0]['admitted_labels'] = []
    else:
        details['policy_sha256_' + change.split('_')[1]] = '0' * 64
    for name, value in [('display_observation', observed), ('display:stdout.json', details)]:
        pin = document['control_artifacts'][name]
        pin['sha256'] = save(Path(pin['path']), value)
    with pytest.raises(ValueError, match='display observation'):
        control.verify_control_receipt(path, save(path, document))


def test_read_only_observer_rejects_changed_persisted_labels(tmp_path):
    _, _, document = receipt(tmp_path)
    _, database = observe_fixture(tmp_path, document)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE native_classifications SET labels_json='[]' WHERE qid='Q1'")
    with pytest.raises(ValueError, match='Current catalogue labels'):
        control.observe_catalogue_display(database, tmp_path / 'export.json',
            document['control_artifacts']['export']['sha256'])


def test_import_time_policy_fingerprint_must_match_current_policy(tmp_path):
    path, _, document = receipt(tmp_path)
    imported = json.loads((tmp_path / 'import.json').read_bytes())
    imported['result']['display_policy_sha256'] = '0' * 64
    for name, filename, value in [('import_report', 'import.json', imported),
                                  ('import:stdout.json', 'import-stdout.json', imported['result'])]:
        document['control_artifacts'][name]['sha256'] = save(tmp_path / filename, value)
    with pytest.raises(ValueError, match='import display policy'):
        control.verify_control_receipt(path, save(path, document))


@pytest.mark.parametrize('field,value', [('raw_classified', 3), ('failed', 1)])
def test_real_import_counts_still_constrain_display_continuation(tmp_path, field, value):
    path, _, document = receipt(tmp_path)
    imported = json.loads((tmp_path / 'import.json').read_bytes())
    imported['result'][field] = value
    for name, filename, payload in [('import_report', 'import.json', imported),
                                    ('import:stdout.json', 'import-stdout.json', imported['result'])]:
        document['control_artifacts'][name]['sha256'] = save(tmp_path / filename, payload)
    with pytest.raises(ValueError, match='import/control'):
        control.verify_control_receipt(path, save(path, document))


@pytest.mark.parametrize('artifact', ['process-start.json', 'process-exit.json', 'stdout.json'])
def test_pinned_import_process_observations_must_match_report(tmp_path, artifact):
    path, _, document = receipt(tmp_path)
    pin = document['control_artifacts']['import:' + artifact]
    data = json.loads(Path(pin['path']).read_bytes())
    data['pid' if artifact != 'stdout.json' else 'adopted'] = 999
    pin['sha256'] = save(Path(pin['path']), data)
    with pytest.raises(ValueError, match='import/control'):
        control.verify_control_receipt(path, save(path, document))


@pytest.mark.parametrize('version', [2, 3])
def test_legacy_receipts_keep_their_original_contract(tmp_path, version):
    from test_scale_content_control_v3 import receipt as legacy_receipt
    path, digest, expected = legacy_receipt(tmp_path, export_version=version, receipt_version=version)
    assert control.verify_control_receipt(path, digest) == expected


def test_measured_preparer_accepts_actual_conservative_display_receipt(tmp_path):
    path, digest, expected = receipt(tmp_path)
    script = Path(__file__).parents[1] / 'scripts/prepare_scale_content_chunk_v4.py'
    spec = importlib.util.spec_from_file_location('display_count_content_prepare', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.trial_control(path, digest) == expected
