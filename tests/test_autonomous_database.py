"""Offline fixtures for authentic multi-run DB composition and evidence versions."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import tracemalloc

import pytest

from recommend_book.classification.validation import input_hash
from recommend_book.database import DatasetDB
from recommend_book.models import Evidence, Identifier, SourceRecord, WorkInput
from recommend_book.recommendation.world import load_world_database
import test_recommendation_overlay as overlay_tests
from test_import_classification_database import save, sha, module, classification
from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import load_taxonomy
from recommend_book.jobs import canonical_json, job_key
from test_recommendation_overlay import prepared
from test_recommendation_overlay import refresh


def api():
    return importlib.import_module('recommend_book.autonomous_database')


def test_reconstructs_authentic_baseline_without_rebinding(prepared, tmp_path):
    arguments = {k: v for k, v in prepared.items() if k != 'base_rows'}
    output = tmp_path.with_name(tmp_path.name + '-autonomous')
    report = api().build_autonomous_database(baseline_overlay=arguments, output_dir=output)
    assert report['state'] == 'bibliographic_ready'
    assert report['work_count'] == 2 and report['changed_count'] == 0
    assert len(load_world_database(output / 'baseline-classified.sqlite')['rows']) == 2
    with sqlite3.connect(output / 'baseline-classified.sqlite') as db:
        rows = db.execute('SELECT work_id,input_hash,result_json,run_id FROM classification_inputs').fetchall()
        assert {r[3] for r in rows} == {'base-fixture', 'delta-fixture'}
        for work_id, digest, raw, _ in rows:
            result = json.loads(raw)
            assert result['work_id'] == work_id and result['input_hash'] == digest
    with sqlite3.connect(output / 'dataset.sqlite') as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'classification_%'").fetchone()
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert json.loads((output / 'unresolved-holds.json').read_text(encoding='utf-8'))['ai_agreement_is_authority'] is False


def test_global_position_gaps_preserve_many_results_from_each_run(tmp_path):
    from test_import_classification_database import record
    source = tmp_path / 'source.sqlite'
    with DatasetDB(source) as db:
        db.ingest([record(str(n)) for n in range(6)])
        works = [w for w in db.work_inputs() if w.expression_id is None]
    config = build_run_config('fixture-model', 'supplied-test-cli', load_taxonomy(), 1)
    entries = []
    for position, work in enumerate(works):
        key = job_key(input_hash(work), config)
        entries.append(dict(work=work, result=classification(work), state={'status': 'completed'}, key=key,
            run_id='fixture-run-' + str(position % 2), config=config, context={}, migration={}, proof={},
            provenance={'fixture_only': True}, original_run=None, position=position))
    target = tmp_path / 'classified.sqlite'
    api()._classify_database(source, target, entries)
    with sqlite3.connect(target) as db:
        saved = db.execute('SELECT work_id,selected_position FROM classification_inputs ORDER BY selected_position').fetchall()
    assert saved == [(work.work_id, i) for i, work in enumerate(works)]
    assert len(load_world_database(target)['rows']) == 6


def test_large_origin_metadata_memory_is_bounded_by_runs_not_works(tmp_path):
    from test_import_classification_database import record
    source = tmp_path / 'source.sqlite'
    with DatasetDB(source) as db:
        db.ingest([record(str(n)) for n in range(30)])
        works = db.work_inputs()
    config = build_run_config('fixture-model', 'supplied-test-cli', load_taxonomy(), 1)
    # Real production origin records contain roughly 3.4 MB of immutable
    # migration/provenance JSON. Reusing it for 1752 rows must not duplicate it.
    large = {'raw_artifact_history': 'x' * 1_000_000}
    entries = [dict(work=w, result=classification(w), state={'status': 'completed'},
        key=job_key(input_hash(w), config), run_id='large-origin-fixture', config=config, context={},
        migration=large, proof={}, provenance=large, original_run=None, position=i) for i, w in enumerate(works)]
    classified = tmp_path / 'classified.sqlite'
    api()._classify_database(source, classified, entries)
    tracemalloc.start()
    try:
        saved = api()._saved_entries(classified, works)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(saved) == 30
    assert peak < 16_000_000, f'Per-work origin metadata duplication: peak={peak}'
    assert all(e['migration'] == large and e['provenance'] == large for e in saved)


def test_refuses_existing_namespace(prepared, tmp_path):
    output = tmp_path.with_name(tmp_path.name + '-occupied')
    output.mkdir()
    args = {k: v for k, v in prepared.items() if k != 'base_rows'}
    with pytest.raises(ValueError, match='new'):
        api().build_autonomous_database(baseline_overlay=args, output_dir=output)


@pytest.fixture
def enrichment(tmp_path, monkeypatch):
    original = overlay_tests.record
    def with_raw(key, **changes):
        qid = 'Q2' if key == 'two' else 'Q1'
        raw = tmp_path / 'raw' / (key + '.json')
        save(raw, {'entities': {qid: {'id': qid, 'claims': {}}}})
        record = original(key, **changes)
        return record.model_copy(update={'raw_path': str(raw), 'raw_sha256': sha(raw),
            'identifiers': [*record.identifiers, Identifier(source='wikidata', value=qid, scope='work')]})
    monkeypatch.setattr(overlay_tests, 'record', with_raw)
    p = overlay_tests.prepared.__wrapped__(tmp_path)
    with sqlite3.connect(p['enriched_database']) as db:
        db.row_factory = sqlite3.Row
        old = dict(db.execute("SELECT * FROM source_records WHERE source_id='two'").fetchone())
        target = SourceRecord.model_validate_json(old['record_json'])
    whole = [WorkInput.model_validate_json(line) for line in (p['delta_manifest'].parent / 'all-inputs.jsonl').read_text().splitlines()]
    work = next(w for w in whole if w.title == 'Book two')
    raw = tmp_path / 'enrichment' / 'raw.json'
    save(raw, {'query': {'pages': [{'pageid': 2, 'pageprops': {'wikibase_item': 'Q2'},
        'revisions': [{'revid': 22, 'slots': {'main': {'content': '== Plot ==\nA detective solves a second murder.'}}}]}]}})
    proof = {'wikidata_work_id': 'Q2', 'pageprops_wikibase_item': 'Q2', 'page_id': 2, 'revision_id': 22,
        'language': 'en', 'target_raw_path': target.raw_path, 'target_raw_sha256': target.raw_sha256,
        'target_raw_locator': 'entities.Q2'}
    obligations = ['attribution', 'share_alike', 'mark_changes', 'third_party_text_screened']
    e = Evidence(evidence_id='wp:en:22:Plot', kind='synopsis', text='A detective solves a second murder.',
        source_url='https://en.wikipedia.org/w/index.php?oldid=22', rights_policy_id='wikipedia-screened-prose',
        llm_input_allowed=True, language='en', locator='revision:22;section:Plot', license='CC-BY-SA-4.0',
        license_url='https://creativecommons.org/licenses/by-sa/4.0/', inherited_restrictions=obligations,
        attribution={'raw_path': str(raw), 'raw_sha256': sha(raw), 'page_id': 2, 'revision_id': 22,
            'identity_proof': proof, 'fulfilled_obligations': obligations, 'credit': 'Wikipedia contributors',
            'permalink': 'https://en.wikipedia.org/w/index.php?oldid=22', 'history_url': 'https://en.wikipedia.org/w/index.php?action=history',
            'changes': 'Screened section extracted'})
    ledger = {'work_id': work.work_id, 'evidence_id': e.evidence_id, 'source_url': e.source_url,
        'raw_path': str(raw), 'raw_sha256': sha(raw), 'locator': e.locator, 'target_source': target.source,
        'target_source_id': target.source_id, 'target_old_version_id': old['version_id'],
        'rights_policy_id': e.rights_policy_id, 'usages': {'acquire': 'allowed', 'local_storage': 'allowed',
        'external_llm': 'conditional', 'commercial': 'conditional', 'display': 'conditional', 'redistribution': 'conditional'},
        'fulfilled_obligations': obligations, 'usage_permitted': {k: True for k in ['acquire', 'local_storage', 'external_llm', 'commercial', 'display', 'redistribution']},
        'identity_proof': proof, 'license': e.license, 'license_url': e.license_url,
        'evidence_text_sha256': hashlib.sha256(e.text.encode()).hexdigest(),
        'screen_version': 'fixture-v1', 'screening': {'accepted': True}}
    records = tmp_path / 'enrichment' / 'records.jsonl'
    save(records, target.model_copy(update={'evidence': [*target.evidence, e]}).model_dump(mode='json'))
    proposals = tmp_path / 'enrichment' / 'accepted-inputs.jsonl'
    save(proposals, work.model_copy(update={'evidence': [*work.evidence, e]}).model_dump(mode='json'))
    ledger_path = tmp_path / 'enrichment' / 'ledger.jsonl'
    save(ledger_path, ledger)
    return {'baseline_overlay': {k: v for k, v in p.items() if k != 'base_rows'}, 'accepted_source_records': records,
        'accepted_inputs': proposals, 'accepted_ledger': ledger_path,
        'output_dir': tmp_path.with_name(tmp_path.name + '-autonomous')}


def test_enrichment_preserves_canonicals_and_has_reversible_source_versions(enrichment):
    report = api().build_autonomous_database(**enrichment)
    assert report['changed_count'] == 1 and report['unchanged_count'] == 1
    output = enrichment['output_dir']
    baseline = [WorkInput.model_validate_json(s) for s in (output / 'baseline-inputs.jsonl').read_text().splitlines()]
    final = [WorkInput.model_validate_json(s) for s in (output / 'new-all-inputs.jsonl').read_text().splitlines()]
    assert [w.work_id for w in final] == [w.work_id for w in baseline]
    changed = next(w for w in final if w.title == 'Book two')
    original = next(w for w in baseline if w.work_id == changed.work_id)
    assert input_hash(changed) != input_hash(original)
    assert next(e for e in changed.evidence if e.evidence_id == 'wp:en:22:Plot').rights_policy_id == 'wikipedia-screened-prose'
    with DatasetDB(output / 'dataset.sqlite') as db:
        updated = db.connection.execute("SELECT version_id,parent_version_id,raw_sha256,payload_sha256 FROM source_records WHERE parent_version_id IS NOT NULL").fetchone()
        old = db.connection.execute('SELECT raw_sha256,payload_sha256 FROM source_records WHERE version_id=?', (updated['parent_version_id'],)).fetchone()
        assert updated['raw_sha256'] == old['raw_sha256'] and updated['payload_sha256'] != old['payload_sha256']
        db.undo_ingest(updated['version_id'])
        restored = {w.work_id: w for w in db.work_inputs() if w.expression_id is None}
        assert all(input_hash(restored[w.work_id]) == input_hash(w) for w in baseline)


@pytest.mark.parametrize('mutation', ['bibliography', 'original_raw', 'permission', 'cc0', 'obligation',
    'raw_bytes', 'pageprops', 'revision', 'old_version', 'proposal_refs', 'extra_ledger'])
def test_rejects_unbound_or_unlicensed_enrichment(enrichment, mutation):
    records = [json.loads(s) for s in enrichment['accepted_source_records'].read_text().splitlines()]
    ledger = json.loads(enrichment['accepted_ledger'].read_text())
    proposal = json.loads(enrichment['accepted_inputs'].read_text())
    if mutation == 'bibliography':
        records[0]['original_language'] = 'ja'
    elif mutation == 'original_raw':
        records[0]['raw_sha256'] = '0' * 64
    elif mutation == 'permission':
        ledger['usage_permitted']['commercial'] = False
    elif mutation == 'cc0':
        records[0]['evidence'][-1]['rights_policy_id'] = 'wikidata-cc0'
        ledger['rights_policy_id'] = 'wikidata-cc0'
    elif mutation == 'obligation':
        ledger['fulfilled_obligations'].remove('share_alike')
    elif mutation == 'raw_bytes':
        from pathlib import Path
        Path(ledger['raw_path']).write_bytes(b'changed raw')
    elif mutation in ('pageprops', 'revision'):
        from pathlib import Path
        raw = Path(ledger['raw_path'])
        payload = json.loads(raw.read_text())
        if mutation == 'pageprops': payload['query']['pages'][0]['pageprops']['wikibase_item'] = 'Q999'
        else: payload['query']['pages'][0]['revisions'][0]['revid'] = 33
        save(raw, payload)
        ledger['raw_sha256'] = sha(raw)
        records[0]['evidence'][-1]['attribution']['raw_sha256'] = sha(raw)
    elif mutation == 'old_version':
        ledger['target_old_version_id'] = 'missing'
    elif mutation == 'proposal_refs':
        proposal['source_record_ids'] = ['missing']
    else:
        duplicate = deepcopy(ledger)
        duplicate['evidence_id'] = 'unused'
        enrichment['accepted_ledger'].write_text(json.dumps(ledger) + '\n' + json.dumps(duplicate) + '\n')
    if mutation != 'extra_ledger': save(enrichment['accepted_ledger'], ledger)
    save(enrichment['accepted_source_records'], records[0])
    save(enrichment['accepted_inputs'], proposal)
    with pytest.raises(ValueError): api().build_autonomous_database(**enrichment)
    assert not enrichment['output_dir'].exists()


def test_refuses_source_mutation_before_publish(prepared, tmp_path, monkeypatch):
    p = {k: v for k, v in prepared.items() if k != 'base_rows'}
    target = tmp_path.with_name(tmp_path.name + '-mutated')
    def change():
        with open(p['enriched_database'], 'ab') as file: file.write(b'changed')
    monkeypatch.setattr(api(), '_before_publish', change)
    with pytest.raises(ValueError, match='source changed'):
        api().build_autonomous_database(baseline_overlay=p, output_dir=target)
    assert not target.exists()


def test_finalize_with_no_changes_preserves_multiple_authentic_runs(prepared, tmp_path):
    p = {k: v for k, v in prepared.items() if k != 'base_rows'}
    namespace = tmp_path.with_name(tmp_path.name + '-build')
    api().build_autonomous_database(baseline_overlay=p, output_dir=namespace)
    target = tmp_path.with_name(tmp_path.name + '-final')
    report = api().finalize_autonomous_database(namespace=namespace, output_dir=target)
    assert report['state'] == 'finalized' and report['completed'] == report['work_count'] == 2
    assert report['model_values_changed'] is False and report['single_run_claimed'] is False
    with sqlite3.connect(target / 'dataset.sqlite') as db:
        assert db.execute('SELECT count(*) FROM classification_runs').fetchone()[0] == 2
    assert len(load_world_database(target / 'dataset.sqlite')['rows']) == 2


@pytest.fixture
def completed_enrichment(enrichment, tmp_path):
    api().build_autonomous_database(**enrichment)
    namespace = enrichment['output_dir']
    inputs = namespace / 'classification-inputs/ordered-inputs.jsonl'
    work = WorkInput.model_validate_json(inputs.read_text())
    context = tmp_path / 'new-context' / 'manifest.json'
    module('freeze_classification_execution').freeze_execution(inputs, context.parent,
        model='fixture-model', cli_version='supplied-test-cli')
    config = build_run_config('fixture-model', 'supplied-test-cli', load_taxonomy(), 1)
    key = job_key(input_hash(work), config)
    run = tmp_path / 'new-run'
    state = dict(key=key, work_id=work.work_id, expression_id=None, input_hash=input_hash(work),
        config=config, attempts=1, lease_until=None, status='completed',
        result_path=str(Path('results') / (key + '.json')))
    result = classification(work)
    result['labels'][0]['label_id'] = 'genre.horror'
    save(run / 'results' / (key + '.json'), result)
    save(run / 'jobs' / (key + '.json'), state)
    run.mkdir(exist_ok=True)
    (run / 'results.jsonl').write_text(canonical_json(result) + '\n')
    save(run / 'manifest.json', dict(run_id='new-fixture-run', config=config, selected=1, completed=1,
        failed=0, pending=0, stopped=False, jobs={key: state}, human_quality_verified=False))
    migration = tmp_path / 'new-migration/manifest.json'
    save(migration, {'version': 'fixture', 'llm_started': False})
    proof = tmp_path / 'new-proof/proof.json'
    save(proof, dict(schema_version='classification-terminal-proof-v1', observed_at='2026-10-13T00:00:00Z',
        session_id='fixture', exit_code=0, process_exit_observed=True, run_id='new-fixture-run',
        manifest_sha256=sha(run / 'manifest.json'), results_sha256=sha(run / 'results.jsonl'),
        ordered_inputs_sha256=sha(inputs), execution_context_manifest_sha256=sha(context),
        migration_manifest_sha256=sha(migration), cli_profile_sha256_before=sha(context), cli_profile_sha256_after=sha(context),
        artifact_sha256={'database': sha(namespace / 'dataset.sqlite')}))
    return dict(namespace=namespace, output_dir=tmp_path.with_name(tmp_path.name + '-final'), run_dir=run,
        execution_context_manifest=context, migration_manifest=migration, terminal_proof=proof, cli_profile=context)


def test_finalize_replaces_only_changed_results_and_keeps_source_lineage(completed_enrichment):
    report = api().finalize_autonomous_database(**completed_enrichment)
    assert report['changed_count'] == 1 and report['completed'] == report['work_count'] == 2
    final = completed_enrichment['output_dir'] / 'dataset.sqlite'
    rows = load_world_database(final)['rows']
    changed = next(r for r in rows if r['title'] == 'Book two')
    assert changed['features'] == {'genre.horror': 1.0}
    assert next(r for r in rows if r['title'] == 'Book one')['features'] == {'genre.fantasy': 1.0}
    with sqlite3.connect(final) as db:
        assert {r[0] for r in db.execute('SELECT run_id FROM classification_runs')} == {'delta-fixture', 'new-fixture-run'}
        assert db.execute('SELECT count(*) FROM classification_source_refs').fetchone()[0] > 0
        assert db.execute('SELECT count(*) FROM classification_label_evidence').fetchone()[0] == 2
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('mutation', ['nonzero_exit', 'held', 'wrong_job', 'durable', 'profile', 'new_raw'])
def test_finalize_rejects_unfinished_or_changed_artifacts(completed_enrichment, mutation):
    p = completed_enrichment
    proof = json.loads(p['terminal_proof'].read_text())
    run = json.loads((p['run_dir'] / 'manifest.json').read_text())
    key = next(iter(run['jobs']))
    if mutation == 'nonzero_exit': proof['exit_code'] = 17
    elif mutation == 'profile': proof['cli_profile_sha256_after'] = '0' * 64
    elif mutation == 'held':
        run.update(completed=0, failed=1)
        run['jobs'][key].update(status='held', error='held fixture', error_category='insufficient')
        run['jobs'][key].pop('result_path')
        save(p['run_dir'] / 'jobs' / (key + '.json'), run['jobs'][key])
    elif mutation == 'wrong_job':
        run['jobs'][key]['input_hash'] = '0' * 64
        save(p['run_dir'] / 'jobs' / (key + '.json'), run['jobs'][key])
    elif mutation == 'durable':
        result_path = p['run_dir'] / 'results' / (key + '.json')
        result = json.loads(result_path.read_text())
        result['labels'][0]['raw_confidence'] = 0.2
        save(result_path, result)
    else:
        with open(p['namespace'] / 'dataset.sqlite', 'ab') as stream: stream.write(b'changed')
    save(p['run_dir'] / 'manifest.json', run)
    proof['manifest_sha256'] = sha(p['run_dir'] / 'manifest.json')
    save(p['terminal_proof'], proof)
    with pytest.raises(ValueError): api().finalize_autonomous_database(**p)
    assert not p['output_dir'].exists()


def test_unresolved_author_role_ambiguities_remain_sidecar_holds(prepared, tmp_path):
    from test_import_classification_database import record
    raw_role = tmp_path / 'role-raw.json'
    save(raw_role, {'dc:creator': 'Translator 訳', 'dcterms:creator': 'generic creator'})
    target = record('unresolved', record_kind='edition', work_refs=[]).model_copy(update={
        'source': 'ndl', 'raw_path': str(raw_role), 'raw_sha256': sha(raw_role)})
    with DatasetDB(prepared['enriched_database']) as db:
        db.ingest([target])
        version = db.connection.execute("SELECT version_id FROM source_heads WHERE source='ndl'").fetchone()[0]
        nodes = [dict(r) for r in db.connection.execute('SELECT work_id,canonical_id,resolved FROM works WHERE source_version_id=?', (version,))]
    manifest = json.loads(prepared['delta_manifest'].read_text())
    manifest['new_database_sha256'] = sha(prepared['enriched_database'])
    save(prepared['delta_manifest'], manifest)
    refresh(prepared)
    audit = tmp_path / 'roles' / 'audit.json'
    save(audit, {'version': 'ndl-source-role-ambiguity-audit-v1',
        'source_database_sha256': sha(prepared['enriched_database']), 'records': [{
        'source': 'ndl', 'source_id': target.source_id, 'source_version_id': version,
        'raw_path': target.raw_path, 'raw_sha256': target.raw_sha256,
        'derived_contributors': [c.model_dump(mode='json') for c in target.contributors],
        'work_nodes': [{'work_node': r['work_id'], 'canonical_id': r['canonical_id']} for r in nodes],
        'role_ambiguity': True, 'raw_dc_creator': [{'field_locator': 'dc:creator[2]', 'text': 'Translator 訳', 'explicit_role': 'translator'}],
        'raw_generic_creator_authorities': [{'field_locator': 'dcterms:creator', 'role_scope': 'generic creator; not explicit author role'}]}],
        'unique_records': 1, 'role_ambiguity_record_count': 1, 'actor': 'mechanical_audit',
        'human_gold': False, 'database_written': False})
    arguments = {k: v for k, v in prepared.items() if k != 'base_rows'}
    output = tmp_path.with_name(tmp_path.name + '-role-holds')
    report = api().build_autonomous_database(baseline_overlay=arguments, output_dir=output, author_role_audit=audit)
    assert report['author_role_ambiguity_count'] == 1
    sidecar = json.loads((output / 'author-role-ambiguities.json').read_text())
    assert sidecar['resolved_ambiguous_count'] == 0 and sidecar['authority_merge_allowed'] is False
    assert report['work_count'] == 2 and report['changed_count'] == 0


@pytest.fixture
def scoped_database(tmp_path):
    from test_import_classification_database import record
    source = tmp_path / 'scope.sqlite'
    claim = {'rank': 'normal', 'mainsnak': {'snaktype': 'value', 'property': 'P31',
        'datavalue': {'value': {'id': 'Q3331189'}}}}
    raw = tmp_path / 'scope-raw.json'
    save(raw, {'entities': {'Q1': {'id': 'Q1', 'claims': {'P31': [claim]}}}})
    target = record('edition-scope', source='wikidata', source_id='Q1', raw_path=str(raw),
        raw_sha256=sha(raw), identifiers=[Identifier(source='wikidata', value='Q1', scope='work')])
    with DatasetDB(source) as db:
        db.ingest([target, record('real-work')])
        works = db.work_inputs()
        version = db.connection.execute("SELECT version_id FROM source_heads WHERE source='wikidata'").fetchone()[0]
        canonical = db.connection.execute('SELECT canonical_id FROM works WHERE source_version_id=?', (version,)).fetchone()[0]
    config = build_run_config('fixture-model', 'supplied-test-cli', load_taxonomy(), 1)
    entries = [dict(work=w, result=classification(w), state={'status': 'completed'},
        key=job_key(input_hash(w), config), run_id='scope-fixture', config=config, context={}, migration={},
        proof={}, provenance={}, original_run=None, position=i) for i, w in enumerate(works)]
    classified = tmp_path / 'scope-classified.sqlite'
    api()._classify_database(source, classified, entries)
    document = {'version': 'work-scope-quarantine-v1', 'status': 'mechanical_audit',
        'baseline_world_works': 2, 'scanned_wikidata_canonical_works': 1,
        'non_wikidata_canonical_works': [{'work_id': w.work_id} for w in works if w.work_id != canonical],
        'scope_class': 'Q3331189', 'quarantine_count': 1, 'class_raw_proofs': [],
        'baseline_modified': False, 'quarantines': [{'work_id': canonical, 'wikidata_id': 'Q1',
        'source': 'wikidata', 'source_id': 'Q1', 'target_source_version_id': version,
        'all_active_source_versions': [{'source': 'wikidata', 'source_id': 'Q1', 'version_id': version}],
        'raw_path': str(raw), 'raw_sha256': sha(raw), 'raw_claims': {'P31': [claim]},
        'locators': {'P31': 'entities.Q1.claims.P31'}, 'class_match_indexes': [0],
        'legacy_normalized_values': {'record_kind': 'work', 'work_kind': target.work_kind,
            'titles': [t.model_dump(mode='json') for t in target.titles],
            'identifiers': [i.model_dump(mode='json') for i in target.identifiers]},
        'human_reviewed': False, 'originals_modified': False}]}
    return classified, works, document, raw, canonical


def test_scope_loader_fail_closed_without_eligibility_ledger(scoped_database):
    with pytest.raises(ValueError, match='eligibility'):
        api().load_autonomous_world_database(scoped_database[0])


def test_verified_scope_conflict_excludes_pool_preserves_history(scoped_database):
    path, works, document, _, canonical = scoped_database
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        before = db.execute('SELECT * FROM classification_inputs').fetchall()
        api()._install_scope_eligibility(db, works, document, path.parent)
        assert [tuple(r) for r in db.execute('SELECT * FROM classification_inputs')] == [tuple(r) for r in before]
    result = api().load_autonomous_world_database(path)
    assert len(result['rows']) == 1 and result['rows'][0]['work_id'] != canonical
    assert result['audit']['historical_work_count'] == 2
    assert result['audit']['eligible_work_count'] == 1
    assert result['audit']['scope_quarantined_count'] == 1
    assert result['audit']['scope_quarantine_verified'] is True
    assert len(load_world_database(path)['rows']) == 2


@pytest.mark.parametrize('mutation', ['raw_sha', 'version', 'canonical', 'locator', 'missing', 'bool_count', 'deprecated'])
def test_scope_quarantine_rejects_unbound_or_incomplete_proof(scoped_database, mutation):
    path, works, document, _, _ = scoped_database
    row = document['quarantines'][0]
    if mutation == 'raw_sha': row['raw_sha256'] = '0' * 64
    elif mutation == 'version': row['target_source_version_id'] = 'not-a-version'
    elif mutation == 'canonical': row['work_id'] = works[1].work_id if works[1].work_id != row['work_id'] else works[0].work_id
    elif mutation == 'locator': row['locators']['P31'] = 'entities.Q2.claims.P31'
    elif mutation == 'missing': document.update(quarantines=[], quarantine_count=0)
    elif mutation == 'bool_count': document['quarantine_count'] = True
    else: row['raw_claims']['P31'][0]['rank'] = 'deprecated'
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(ValueError): api()._install_scope_eligibility(db, works, document, path.parent)


@pytest.mark.parametrize('mutation', ['raw', 'missing_row', 'flip_eligible', 'audit_hash'])
def test_scope_loader_rechecks_raw_and_rejects_missing_eligible_row(scoped_database, mutation):
    path, works, document, raw, _ = scoped_database
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        api()._install_scope_eligibility(db, works, document, path.parent)
    if mutation == 'raw': raw.write_bytes(b'changed')
    else:
        with sqlite3.connect(path) as db:
            if mutation == 'missing_row': db.execute('DELETE FROM autonomous_work_eligibility WHERE eligible=1')
            elif mutation == 'flip_eligible': db.execute('UPDATE autonomous_work_eligibility SET eligible=1')
            else: db.execute("UPDATE autonomous_scope_audit SET audit_sha256='wrong'")
    with pytest.raises(ValueError): api().load_autonomous_world_database(path)


def test_finalize_binds_empty_non_wikidata_scope_audit_to_base_database(prepared, tmp_path):
    arguments = {k: v for k, v in prepared.items() if k != 'base_rows'}
    namespace = tmp_path.with_name(tmp_path.name + '-build')
    api().build_autonomous_database(baseline_overlay=arguments, output_dir=namespace)
    works = [WorkInput.model_validate_json(l) for l in (namespace / 'new-all-inputs.jsonl').read_text().splitlines()]
    path = tmp_path / 'scope' / 'audit.json'
    document = {'version': 'work-scope-quarantine-v1', 'status': 'mechanical_audit',
        'baseline_world_works': len(works), 'scanned_wikidata_canonical_works': 0,
        'non_wikidata_canonical_works': [{'work_id': w.work_id} for w in works],
        'scope_class': 'Q3331189', 'quarantine_count': 0, 'quarantines': [],
        'class_raw_proofs': [], 'baseline_modified': False,
        'input_database_path': str(prepared['enriched_database']), 'input_database_sha256': '0' * 64}
    save(path, document)
    output = tmp_path.with_name(tmp_path.name + '-final')
    with pytest.raises(ValueError, match='originating database SHA'):
        api().finalize_autonomous_database(namespace=namespace, output_dir=output, scope_quarantine=path)
    assert not output.exists()
    document['input_database_sha256'] = sha(prepared['enriched_database'])
    save(path, document)
    result = api().finalize_autonomous_database(namespace=namespace, output_dir=output, scope_quarantine=path)
    assert result['historical_work_count'] == result['eligible_work_count'] == 2
    assert result['scope_quarantined_count'] == 0
    assert len(api().load_autonomous_world_database(output / 'dataset.sqlite')['rows']) == 2
