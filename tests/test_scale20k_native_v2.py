"""Source-genre absence never substitutes for novel/content/rights evidence."""
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from recommend_book.models import SourceRecord, Title, Evidence
from recommend_book.database import DatasetDB
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding
from recommend_book.wikipedia_paragraphs import build_evidence

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    path = ROOT / 'scripts' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalogue(tmp_path):
    path = tmp_path / 'catalogue.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE works(qid TEXT PRIMARY KEY,version_id TEXT,display_allowed INTEGER,recommendable INTEGER)')
        db.execute('CREATE TABLE work_genres(qid TEXT,genre_qid TEXT,label TEXT)')
        db.execute('CREATE TABLE native_classifications(qid TEXT PRIMARY KEY,manifest_path TEXT,manifest_sha256 TEXT,labels_json TEXT,provenance_json TEXT)')
        db.execute("INSERT INTO works VALUES('Q1','source1',1,0)")
        db.execute("INSERT INTO works VALUES('Q2','source2',1,1)")
        db.execute("INSERT INTO work_genres VALUES('Q2','Q8253','fiction')")
    return path


def genuine_unknown_genre(tmp_path):
    wd = tmp_path / 'wd.json'
    entity = {'id': 'Q1', 'labels': {'en': {'language': 'en', 'value': 'Book'}},
              'descriptions': {'en': {'language': 'en', 'value': 'novel'}},
              'sitelinks': {'enwiki': {'title': 'Book'}},
              'claims': {'P31': [{'mainsnak': {'datavalue': {'value': {'id': 'Q8261'}}}}]}}
    write(wd, {'entities': {'Q1': entity}})
    record = SourceRecord(source='wikidata', source_id='Q1', provider_id='wikidata', record_kind='work',
        work_kind='novel', source_url='https://www.wikidata.org/wiki/Q1', retrieved_at='2026-10-22',
        titles=[Title(value='Book')], raw_path=str(wd), raw_sha256=hashlib.sha256(wd.read_bytes()).hexdigest(),
        rights_policy_id='wikidata-cc0', evidence=[Evidence(evidence_id='description', kind='description',
            text='novel', source_url='https://www.wikidata.org/wiki/Q1', rights_policy_id='wikidata-cc0',
            llm_input_allowed=True, locator='descriptions.en')])
    page = {'pageid': 7, 'ns': 0, 'title': 'Book', 'pageprops': {'wikibase_item': 'Q1'},
            'revisions': [{'revid': 123, 'slots': {'main': {'contentmodel': 'wikitext',
                'content': '== Plot ==\nAn orphan returns to a distant town and discovers a missing sibling.'}}}]}
    wp = tmp_path / 'wp.json'
    write(wp, {'query': {'pages': [page]}})
    artifact = SimpleNamespace(path=str(wp), sha256=hashlib.sha256(wp.read_bytes()).hexdigest(),
                               source_url='https://en.wikipedia.org/w/api.php')
    evidence, _, _ = build_evidence(record, page, artifact, language='en')
    record.evidence.extend(evidence)
    return record


def test_unknown_source_genre_passes_same_scope_and_real_content_guard(tmp_path):
    builder = load('prepare_scale20k_content_sources_v2')
    record = genuine_unknown_genre(tmp_path)
    records = tmp_path / 'records.jsonl'
    records.write_text(record.model_dump_json() + '\n')
    helper = builder.load_recipe().native_preparer(excluded_qids=set(), run_name='new', window_index=94)
    selected, held = helper.choose_records(records, set(), limit=1)
    assert len(selected) == 1 and not held and selected[0][3] == []
    database = tmp_path / 'source.sqlite'
    sources = DatasetDB(database); sources.ingest([record])
    work = sources.work_inputs()[0]
    bindings = {ref: source_binding(database, ref) for ref in work.source_record_ids}
    registry = RightsRegistry.default()
    official = tmp_path / 'official.txt'; official.write_text('CC0 attribution sharealike')
    ledger = {'version': 'usage-ledger-v3',
        'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
        'official_conditions': {p: [{'raw_path': str(official),
            'raw_sha256': hashlib.sha256(official.read_bytes()).hexdigest(),
            'source_url': registry.policies[p].evidence_urls[0],
            'verified_at': datetime.now(timezone.utc).isoformat()}] for p in ('wikidata-cc0', 'wikipedia-screened-prose')},
        'inputs': {model_hash(work): build_input_entry(work, bindings)},
        'revoked_input_hashes': [], 'private_origins': {}}
    path = tmp_path / 'ledger.json'; write(path, ledger)
    decision = UsageGuard(path).check_input(work)
    assert decision.allowed, decision.reasons
    narrative = next(e for e in record.evidence if e.kind == 'synopsis')
    narrative.inherited_restrictions.remove('share_alike')
    withheld_db = tmp_path / 'withheld.sqlite'
    withheld = DatasetDB(withheld_db); withheld.ingest([record])
    work = withheld.work_inputs()[0]
    bindings = {ref: source_binding(withheld_db, ref) for ref in work.source_record_ids}
    ledger['inputs'] = {model_hash(work): build_input_entry(work, bindings)}
    write(path, ledger)
    refused = UsageGuard(path).check_input(work)
    assert not refused.allowed
    assert 'immutable source absent' not in str(refused.reasons), refused.reasons


def test_unknown_genre_does_not_erase_collection_scope_hold(tmp_path):
    builder = load('prepare_scale20k_content_sources_v2')
    record = genuine_unknown_genre(tmp_path)
    raw = json.loads(Path(record.raw_path).read_bytes())
    raw['entities']['Q1']['claims']['P31'].append({'mainsnak': {'datavalue': {'value': {'id': 'Q277759'}}}})
    record.raw_sha256 = write(Path(record.raw_path), raw)
    path = tmp_path / 'records.jsonl'; path.write_text(record.model_dump_json() + '\n')
    helper = builder.load_recipe().native_preparer(excluded_qids=set(), run_name='new', window_index=94)
    with pytest.raises(ValueError, match='eligible selected count'):
        helper.choose_records(path, set(), limit=1)


def test_count_includes_display_native_without_promoting_source_genre(tmp_path):
    driver = load('run_scale20k_native_v2')
    path = catalogue(tmp_path)
    with sqlite3.connect(path) as db:
        for qid in ('Q1', 'Q2'):
            db.execute('INSERT INTO native_classifications VALUES(?,?,?,?,?)', (qid, 'export', 'sha', '[]', '{}'))
    before = path.read_bytes()
    assert driver.eligible_native_count(path) == 2
    snapshot = driver.catalogue_snapshot(path, ['Q1', 'Q2'])
    assert snapshot['Q1']['recommendable'] == 0 and snapshot['Q1']['genres'] == []
    assert path.read_bytes() == before


def test_import_snapshot_requires_exact_new_labels_and_unchanged_source_flags(tmp_path):
    driver = load('run_scale20k_native_v2')
    path = catalogue(tmp_path)
    before = driver.catalogue_snapshot(path, ['Q1'])
    labels = [{'axis': 'theme', 'label_id': 'theme.growth'}]
    export = tmp_path / 'export.json'
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO native_classifications VALUES(?,?,?,?,?)', ('Q1', str(export.resolve()), 'sha', json.dumps(labels), '{}'))
    after = driver.catalogue_snapshot(path, ['Q1'])
    expected = [{'qid': 'Q1', 'admitted_labels': labels}]
    driver.validate_import_observation(before, after, expected, export, 'sha')
    after['Q1']['recommendable'] = 1
    with pytest.raises(ValueError, match='Source metadata changed'):
        driver.validate_import_observation(before, after, expected, export, 'sha')


def test_new_controller_has_local_state_facade_and_first_unknown_four_slots(tmp_path):
    driver = load('run_scale20k_native_v2')
    module = driver.load_controller()
    from recommend_book import scale20k_native_v1 as fixed_state
    assert module.state is not fixed_state
    assert module.state.eligible_count is not fixed_state.eligible_count
    assert driver.choose_slots(8, unknown_count=1, unknown_passed=False) == 4
    assert driver.choose_slots(8, unknown_count=1, unknown_passed=True) == 8
    assert driver.choose_slots(4, unknown_count=1, unknown_passed=True) == 4


def test_new_control_policy_artifact_is_checked_by_unchanged_v4(tmp_path):
    # Reuse only a synthetic fixture constructor, never acquired/native artifacts.
    path = ROOT / 'tests/test_scale_content_control_v4.py'
    spec = importlib.util.spec_from_file_location('v4_synthetic_fixture', path)
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    control_path, _, receipt = fixture.receipt(tmp_path)
    driver = load('run_scale20k_native_v2')
    policy = tmp_path / 'policy.json'; digest = write(policy, {'observed': 'actual-test-fixture'})
    receipt['control_artifacts']['native_eligibility_policy'] = {'path': str(policy.resolve()), 'sha256': digest}
    receipt['control_artifacts']['native_eligibility_controller'] = {'path': str(Path(driver.__file__).resolve()),
        'sha256': hashlib.sha256(Path(driver.__file__).read_bytes()).hexdigest()}
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    expected = write(control_path, receipt)
    assert verify_control_receipt(control_path, expected) == receipt
    policy.write_text('tampered')
    with pytest.raises(ValueError, match='artifact changed'):
        verify_control_receipt(control_path, expected)


def test_unknown_genre_import_and_existing_control_constructor_connect_end_to_end(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    driver = load('run_scale20k_native_v2')
    controller = driver.load_controller()
    state = controller.state
    state.BASE = tmp_path / 'v20'
    state.RUNTIME = tmp_path / 'runtime'
    state.DATABASE = catalogue(tmp_path)
    state.writer_lock = lambda **kwargs: nullcontext()
    name = 'scale20k-content-0101-0200-v1'
    source = state.BASE / 'source'
    mapping = source / 'work-mapping.json'
    write(mapping, [{'work_id': 'w1', 'wikidata_qid': 'Q1'}])
    source_sha = write(source / 'manifest.json', {'selected': 1})
    source_policy = source / 'native-eligibility-policy.json'
    policy_sha = write(source_policy, {'version': 'scale20k-source-display-eligibility-v2',
        'policy': driver.POLICY, 'source_manifest_sha256': source_sha,
        'source_builder_sha256': state.sha(ROOT / 'scripts/prepare_scale20k_content_sources_v2.py'),
        'observer_sha256': state.sha(ROOT / 'scripts/run_scale20k_native_v2.py'),
        'catalogue_before': driver.catalogue_snapshot(state.DATABASE, ['Q1']),
        'catalogue_after': driver.catalogue_snapshot(state.DATABASE, ['Q1'])})
    state.source_entry = lambda *args: {'eligibility_policy_sha256': policy_sha}
    reservation = tmp_path / 'reservation.json'; write(reservation, {'fixture': 'attempt'})
    state.reserve_window = lambda *args, **kwargs: reservation
    terminal = state.RUNTIME / ('terminal-' + name)
    write(terminal / 'process-start.json', {'started_at': '2026-10-22T00:00:00+00:00'})
    write(terminal / 'process-exit.json', {'observed_at': '2026-10-22T00:00:10+00:00',
        'actual_exit_code': 0, 'process_exit_observed': True})
    run_path = tmp_path / 'run.json'
    write(run_path, {'run_id': 'fixture-run', 'selected': 1, 'completed': 1, 'failed': 0, 'pending': 0,
        'stopped': False, 'stop_reasons': [], 'initial_concurrency': 4, 'final_concurrency': 4, 'usage': {}})
    audit = tmp_path / 'audit.json'; audit_sha = write(audit, {'reviews': [{'work_id': 'w1', 'findings': []}]})
    review = tmp_path / 'review.json'; write(review, {'source_audits': [{'path': str(audit), 'sha256': audit_sha}]})
    separate = {'review_path': str(review), 'elapsed_seconds': 1}
    labels = [{'label_id': 'theme.growth', 'axis': 'theme', 'evidence': [{'evidence_id': 'plot', 'quote': 'An orphan'}]}]
    public_rows = [{'qid': 'Q1', 'content_evidence_ids': ['plot'], 'admitted_labels': labels}]
    public_path = state.BASE / 'classification-exports-v3' / name / 'manifest.json'
    write(state.BASE / 'classification-exports' / name / 'manifest.json', {'separate_review': separate})
    from recommend_book.scale_content_control_v4 import display_policy_sha256
    commands = []
    def child(argv, directory, **kwargs):
        commands.append(list(map(str, argv)))
        directory = Path(directory)
        if 'ScaleCatalogue' not in str(argv):
            if directory.name == 'output-rights':
                write(public_path, {'version': 'scale-content-native-classification-export-v3', 'window_index': 94,
                    'work_mapping': str(mapping.resolve()), 'artifact_sha256': {str(mapping.resolve()): state.sha(mapping)},
                    'coordinator_sha256': 'fixture-coordinator', 'run_manifest': str(run_path),
                    'verified_projection': {'rows': public_rows, 'actual_feature_adopted_works': 1,
                                           'actual_content_non_genre_feature_works': 1},
                    'gate': {'passed': True, 'actual_feature_adopted_works': 1, 'actual_content_non_genre_feature_works': 1},
                    'separate_review': separate})
            return {'actual_exit_code': 0}
        directory.mkdir(parents=True, exist_ok=False)
        digest = state.sha(public_path)
        with sqlite3.connect(state.DATABASE) as db:
            db.execute('INSERT INTO native_classifications VALUES(?,?,?,?,?)',
                       ('Q1', str(public_path.resolve()), digest, json.dumps(labels), '{}'))
        result = {'raw_classified': 1, 'adopted': 1, 'failed': 0, 'display_policy_sha256': display_policy_sha256()}
        actual = {'pid': 123, 'actual_exit_code': 0, 'process_exit_observed': True}
        write(directory / 'request.json', {'argv': list(map(str, argv))})
        write(directory / 'process-start.json', {'pid': 123})
        write(directory / 'process-exit.json', actual)
        write(directory / 'stdout.json', result)
        (directory / 'stderr.txt').write_text('')
        return actual
    controller.observed_command = child
    import recommend_book.scale_coordinator_proof_v1 as chain
    monkeypatch.setattr(chain, 'coordinator_chain_pins', lambda *args: [])
    before_known = driver.catalogue_snapshot(state.DATABASE, ['Q2'])
    control, digest, receipt = controller.run_window(source=source, source_sha=source_sha, global_index=94,
        fixed={'selected': 1, 'chunks': [{'run_name': name, 'inputs_path': 'fixture', 'usage_ledger_path': 'fixture'}]},
        inventory_path='fixture', inventory_sha='fixture', previous_control='fixture', previous_sha='fixture',
        concurrency=8, publication=controller.load_publication(), source_index='fixture', source_index_sha='fixture')
    assert receipt['gate']['passed'] is True
    assert receipt['gate']['accepted'] == 1
    policy = state.read(receipt['control_artifacts']['native_eligibility_policy']['path'])
    assert policy['requested_concurrency'] == 4 and policy['first_unknown_probe'] is True
    assert policy['source_genre_counts'] == {'source_genre_known': 0, 'source_genre_unknown': 1}
    assert commands[0][commands[0].index('--concurrency') + 1] == '4'
    assert commands[1][1] == 'scripts/run_scale20k_content_classification_v1.py'
    assert driver.catalogue_snapshot(state.DATABASE, ['Q1'])['Q1']['recommendable'] == 0
    assert driver.catalogue_snapshot(state.DATABASE, ['Q2']) == before_known
    driver.verify_policy_control(control, digest)
    Path(receipt['control_artifacts']['native_eligibility_policy']['path']).write_text('changed')
    with pytest.raises(ValueError, match='artifact changed'):
        driver.verify_policy_control(control, digest)


@pytest.mark.parametrize('promote', [False, True])
def test_source_entry_observes_real_guard_boundary_and_never_promotes_metadata(tmp_path, monkeypatch, promote):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    builder = load('prepare_scale20k_content_sources_v2')
    database = catalogue(tmp_path)
    record = genuine_unknown_genre(tmp_path)
    records = tmp_path / 'records.jsonl'; records.write_text(record.model_dump_json() + '\n')
    output = tmp_path / 'new-source'
    calls = []
    def canonical(records_path, catalogue_path, directory, conditions, **kwargs):
        calls.append('unchanged canonical admission boundary')
        write(output / 'manifest.json', {'required_purpose_admissions_allowed': True})
        if promote:
            with sqlite3.connect(database) as db:
                db.execute("UPDATE works SET recommendable=1 WHERE qid='Q1'")
        return {'required_purpose_admissions_allowed': True, 'model_calls': 0}
    monkeypatch.setattr(builder, 'load_recipe', lambda: SimpleNamespace(prepare_native_window=canonical))
    if promote:
        with pytest.raises(ValueError, match='metadata or source entry changed'):
            builder.prepare_native_window(records, database, output, tmp_path / 'conditions')
        assert not (output / 'native-eligibility-policy.json').exists()
    else:
        before = database.read_bytes()
        result = builder.prepare_native_window(records, database, output, tmp_path / 'conditions')
        assert result['model_calls'] == 0
        assert database.read_bytes() == before
        policy = json.loads(Path(result['eligibility_policy_path']).read_bytes())
        assert policy['catalogue_before'] == policy['catalogue_after']
        assert policy['source_genre_counts']['source_genre_unknown'] == 1
    assert len(calls) == 1
