"""Synthetic-only integration contracts for the sealed derived classification DB."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import ROOT, input_hash, load_taxonomy, target_language
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(canonical_json(value) + '\n', encoding='utf-8')


def record(key, **changes):
    fields = dict(source='fixture', source_id=key, source_url='https://example.org/' + key,
                  retrieved_at='2026-10-12T00:00:00Z', provider_id='fixture', record_kind='work',
                  titles=[Title(value='Book ' + key)], contributors=[Contributor(name='Author ' + key)],
                  identifiers=[Identifier(source='fixture', value=key, scope='work')],
                  original_language='en', raw_sha256=hashlib.sha256(key.encode()).hexdigest(),
                  raw_path='fixture/' + key, rights_policy_id='fixture',
                  evidence=[Evidence(evidence_id='e-' + key, kind='synopsis',
                      text='A detective solves a murder.', source_url='https://example.org/' + key,
                      rights_policy_id='fixture', llm_input_allowed=True, locator='summary', language='en')])
    fields.update(changes)
    return SourceRecord(**fields)


def classification(work):
    e = work.evidence[0]
    return dict(work_id=work.work_id, expression_id=work.expression_id, target_language=target_language(work),
        taxonomy_version='1.0', prompt_version='1.0', model='fixture-model', cli_version='supplied-test-cli',
        input_hash=input_hash(work), labels=[dict(label_id='genre.mystery', axis='genre', decision='present',
            scope='expression' if work.expression_id else 'work', expression_id=work.expression_id,
            relevance=None, raw_confidence=0.8123456789, calibrated_confidence=None,
            calibration_status='uncalibrated', calibration_version=None,
            evidence=[dict(evidence_id=e.evidence_id, locator=e.locator, quote=e.text, summary=None,
                           directness='direct')], missing_reason=None, spoiler='premise')],
        missing_axes=[dict(axis=axis, reason='Insufficient evidence', decision='unknown')
                      for axis in ['theme', 'structure', 'protagonist', 'style', 'atmosphere']],
        conflicts=[], needs_human_review=True)


@pytest.fixture
def fixture(tmp_path):
    base = tmp_path / 'source' / 'dataset.sqlite'
    original = record('one')
    translated = record('edition', record_kind='edition', identifiers=[],
        work_refs=original.identifiers, original_language='en', language='ja',
        contributors=[Contributor(name='Author one'), Contributor(name='Translator', role='translator')],
        evidence=[Evidence(evidence_id='ja-excerpt', kind='excerpt', text='日本語の本文。',
            source_url='https://example.org/edition', rights_policy_id='fixture',
            llm_input_allowed=True, locator='page1', language='ja')])
    with DatasetDB(base) as db:
        db.ingest([original, record('two'), translated])
        works = db.work_inputs()
    assert len(works) == 3 and any(w.expression_id for w in works)
    inputs = tmp_path / 'inputs' / 'ordered-inputs.jsonl'
    inputs.parent.mkdir()
    inputs.write_text(''.join(w.model_dump_json() + '\n' for w in works), encoding='utf-8')
    save(inputs.with_name('manifest.json'), {'ordered_inputs_sha256': sha(inputs),
         'processing_context': {'legacy': 'not the execution code'}})
    context = tmp_path / 'execution' / 'manifest.json'
    module('freeze_classification_execution').freeze_execution(inputs, context.parent,
        model='fixture-model', cli_version='supplied-test-cli')
    migration = tmp_path / 'migration' / 'manifest.json'
    save(migration, {'version': 'fixture-migration', 'llm_started': False})
    config = build_run_config('fixture-model', 'supplied-test-cli', load_taxonomy(), 1)
    run = tmp_path / 'run'
    states, results = {}, []
    for n, work in enumerate(works):
        key = job_key(input_hash(work), config)
        state = dict(key=key, work_id=work.work_id, expression_id=work.expression_id,
            input_hash=input_hash(work), config=config, attempts=1, lease_until=None,
            status='held' if n == 2 else 'completed')
        if state['status'] == 'completed':
            state['result_path'] = str(Path('results') / (key + '.json'))
            value = classification(work)
            save(run / state['result_path'], value)
            results.append(value)
        else:
            state.update(error_category='invalid_result', error='Fixture validation failure')
        save(run / 'jobs' / (key + '.json'), state)
        states[key] = state
    (run / 'results.jsonl').write_text(''.join(canonical_json(r) + '\n' for r in results), encoding='utf-8')
    manifest = dict(run_id='fixture-run', config=config, selected=3, completed=2, failed=1,
                    pending=0, stopped=False, jobs=states, human_quality_verified=False)
    save(run / 'manifest.json', manifest)
    proof = tmp_path / 'proof' / 'terminal-proof.json'
    values = dict(schema_version='classification-terminal-proof-v1',
        observed_at='2026-10-12T01:02:03+00:00', session_id='fixture-only', exit_code=0,
        process_exit_observed=True, run_id=manifest['run_id'], manifest_sha256=sha(run / 'manifest.json'),
        results_sha256=sha(run / 'results.jsonl'), ordered_inputs_sha256=sha(inputs),
        execution_context_manifest_sha256=sha(context), migration_manifest_sha256=sha(migration),
        cli_profile_sha256_before=sha(context), cli_profile_sha256_after=sha(context),
        artifact_sha256={'database': sha(base)})
    save(proof, values)
    return dict(source_db=base, ordered_inputs=inputs, run_dir=run,
        execution_context_manifest=context, migration_manifest=migration, terminal_proof=proof,
        output_dir=tmp_path / 'derived', cli_profile=context)


def refresh_proof(f):
    proof = json.loads(f['terminal_proof'].read_text(encoding='utf-8'))
    for key, path in [('manifest_sha256', f['run_dir'] / 'manifest.json'),
        ('results_sha256', f['run_dir'] / 'results.jsonl'), ('ordered_inputs_sha256', f['ordered_inputs'])]:
        proof[key] = sha(path)
    save(f['terminal_proof'], proof)


def tables(path):
    with sqlite3.connect(path) as db:
        return {name: sorted(db.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(), key=repr)
                for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_success_preserves_all_original_rows_and_scores(fixture):
    importer = module('import_classification_database')
    baseline = tables(fixture['source_db']); initial_sha = sha(fixture['source_db'])
    report = importer.import_classifications(**fixture)
    derived = fixture['output_dir'] / 'dataset.sqlite'
    after = tables(derived)
    assert all(after[name] == rows for name, rows in baseline.items())
    assert sha(fixture['source_db']) == initial_sha
    assert report['selected'] == 3 and report['completed'] == 2 and report['held'] == 1
    assert report['human_verified'] is False and report['llm_started'] is False
    with sqlite3.connect(derived) as db:
        labels = db.execute('SELECT relevance,raw_confidence,calibrated_confidence,calibration_status,human_verified FROM classification_labels').fetchall()
        assert labels == [(None, 0.8123456789, None, 'uncalibrated', 0)] * 2
        held = db.execute("SELECT status,result_json,held_reason_json FROM classification_inputs WHERE status='held'").fetchone()
        assert held[1] is None and 'Fixture validation failure' in held[2]
        assert db.execute('SELECT count(*) FROM classification_source_refs').fetchone()[0] >= 3
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert json.loads((fixture['output_dir'] / 'manifest.json').read_text(encoding='utf-8'))['source_files_unchanged'] is True


@pytest.mark.parametrize('change', ['no_exit', 'bool_exit', 'profile', 'pending', 'counts', 'config',
    'durable', 'job', 'quote', 'scope', 'input_ref', 'edition_scope', 'duplicate', 'held_result'])
def test_rejects_nonterminal_or_inconsistent_artifacts(fixture, change):
    importer = module('import_classification_database')
    f = fixture; manifest_path = f['run_dir'] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')); key = next(k for k,s in manifest['jobs'].items() if s['status']=='completed')
    state = manifest['jobs'][key]; result_path = f['run_dir'] / state['result_path']
    if change in {'no_exit', 'bool_exit', 'profile'}:
        proof = json.loads(f['terminal_proof'].read_text(encoding='utf-8'))
        proof[{'no_exit':'process_exit_observed', 'bool_exit':'exit_code', 'profile':'cli_profile_sha256_after'}[change]] = {'no_exit':False, 'bool_exit':True, 'profile':'0'*64}[change]
        save(f['terminal_proof'], proof)
    elif change in {'pending', 'counts', 'config'}:
        if change == 'pending': manifest.update(pending=1)
        if change == 'counts': manifest.update(completed=3)
        if change == 'config': manifest['config']['validation_hash']='0'*64
        save(manifest_path, manifest); refresh_proof(f)
    elif change == 'job':
        state['input_hash']='0'*64; save(f['run_dir'] / 'jobs' / (key+'.json'), state)
    elif change in {'durable', 'quote', 'scope'}:
        value=json.loads(result_path.read_text(encoding='utf-8'))
        if change == 'durable': value['labels'][0]['raw_confidence']=0.1
        if change == 'quote': value['labels'][0]['evidence'][0]['quote']='not in source'
        if change == 'scope': value['labels'][0].update(scope='translation', expression_id='other')
        save(result_path, value)
        if change != 'durable':
            index=[json.loads(line) for line in (f['run_dir']/'results.jsonl').read_text(encoding='utf-8').splitlines()]
            index=[value if r['work_id']==value['work_id'] and r['expression_id']==value['expression_id'] else r for r in index]
            (f['run_dir']/'results.jsonl').write_text(''.join(canonical_json(r)+'\n' for r in index),encoding='utf-8');refresh_proof(f)
    elif change == 'held_result':
        held_key=next(k for k,s in manifest['jobs'].items() if s['status']=='held')
        save(f['run_dir']/'results'/(held_key+'.json'), {'unexpected':'held result'})
    else:
        lines=[json.loads(line) for line in f['ordered_inputs'].read_text(encoding='utf-8').splitlines()]
        if change == 'input_ref': lines[0]['source_record_ids']=['unknown-version']
        if change == 'edition_scope': lines[0]['expression_id']=next(w['expression_id'] for w in lines if w['expression_id'])
        if change == 'duplicate': lines.append(deepcopy(lines[0]))
        f['ordered_inputs'].write_text(''.join(canonical_json(r)+'\n' for r in lines),encoding='utf-8');refresh_proof(f)
    with pytest.raises(ValueError): importer.import_classifications(**f)
    assert not f['output_dir'].exists()


def test_rejects_existing_output_and_source_subdirectory(fixture):
    importer=module('import_classification_database');f=fixture
    f['output_dir'].mkdir();marker=f['output_dir']/'marker';marker.write_text('keep')
    with pytest.raises(ValueError):importer.import_classifications(**f)
    assert marker.read_text()=='keep'
    f['output_dir']=f['run_dir']/'derived'
    with pytest.raises(ValueError):importer.import_classifications(**f)


@pytest.mark.parametrize('target', ['input', 'run_new_file', 'code', 'wal'])
def test_rejects_source_change_before_publish_and_cleans_only_owned_stage(fixture, monkeypatch, target):
    importer=module('import_classification_database');f=fixture
    other=f['output_dir'].parent/'.unrelated-stage';other.mkdir();(other/'keep').write_text('keep')
    fake_code=f['output_dir'].parent/'fake-code';fake_code.mkdir()
    # Avoid changing any project artifact: make the monitoring snapshot include a fixture file.
    code=fake_code/'sentinel';code.write_text('original')
    original=importer._protected
    def protected(*args, **kwargs):
        paths=original(*args, **kwargs);paths.add(code.resolve());return paths
    monkeypatch.setattr(importer,'_protected',protected)
    def mutate():
        path={'input':f['ordered_inputs'],'run_new_file':f['run_dir']/'new-artifact','code':code,
              'wal':Path(str(f['source_db'])+'-wal')}[target]
        path.write_bytes(b'changed')
    monkeypatch.setattr(importer,'_before_publish',mutate)
    with pytest.raises(ValueError, match='changed'):importer.import_classifications(**f)
    assert not f['output_dir'].exists() and (other/'keep').read_text()=='keep'
    assert not list(f['output_dir'].parent.glob('.derived.import-*'))


def test_nonempty_wal_rejected(fixture):
    importer=module('import_classification_database');f=fixture
    Path(str(f['source_db'])+'-wal').write_bytes(b'not checkpointed')
    with pytest.raises(ValueError,match='WAL'):importer.import_classifications(**f)


@pytest.mark.parametrize('raw', ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'])
def test_strict_json_rejects_ambiguous_numbers_and_properties(raw):
    with pytest.raises(ValueError):module('import_classification_database').strict_json(raw)


@pytest.mark.parametrize('field', ['source_record_ids', 'expression_id', 'rights', 'title'])
def test_rejects_input_with_valid_new_hash_but_wrong_immutable_db_binding(fixture, field):
    f=fixture
    lines=[json.loads(line) for line in f['ordered_inputs'].read_text(encoding='utf-8').splitlines()]
    if field=='source_record_ids':lines[0][field]=['invented-version']
    if field=='expression_id':lines[0][field]='invented-expression'
    if field=='rights':lines[0]['evidence'][0]['rights_policy_id']='invented-permission'
    if field=='title':lines[0][field]='Invented title'
    f['ordered_inputs'].write_text(''.join(canonical_json(row)+'\n' for row in lines),encoding='utf-8')
    sibling=json.loads(f['ordered_inputs'].with_name('manifest.json').read_text(encoding='utf-8'))
    sibling['ordered_inputs_sha256']=sha(f['ordered_inputs'])
    save(f['ordered_inputs'].with_name('manifest.json'),sibling)
    replacement=f['execution_context_manifest'].parent.with_name('new-fixture-execution')
    module('freeze_classification_execution').freeze_execution(f['ordered_inputs'],replacement,
        model='fixture-model',cli_version='supplied-test-cli')
    f['execution_context_manifest']=replacement/'manifest.json';f['cli_profile']=f['execution_context_manifest']
    refresh_proof(f)
    proof=json.loads(f['terminal_proof'].read_text(encoding='utf-8'))
    for key in ('execution_context_manifest_sha256','cli_profile_sha256_before','cli_profile_sha256_after'):
        proof[key]=sha(f['execution_context_manifest'])
    save(f['terminal_proof'],proof)
    with pytest.raises(ValueError,match='DB reconstruction'):
        module('import_classification_database').import_classifications(**f)
    assert not f['output_dir'].exists()


def test_nonzero_observed_exit_is_preserved_without_inventing_success(fixture):
    f=fixture;proof=json.loads(f['terminal_proof'].read_text(encoding='utf-8'));proof['exit_code']=7
    save(f['terminal_proof'],proof)
    module('import_classification_database').import_classifications(**f)
    with sqlite3.connect(f['output_dir']/'dataset.sqlite') as db:
        recorded=json.loads(db.execute('SELECT terminal_proof_json FROM classification_runs').fetchone()[0])
        assert recorded['exit_code']==7
        assert db.execute("SELECT count(*) FROM classification_inputs WHERE status='held' AND result_json IS NULL").fetchone()[0]==1


@pytest.mark.parametrize('field', ['selected', 'completed', 'failed', 'pending'])
def test_bool_is_not_a_terminal_count(fixture, field):
    f=fixture;path=f['run_dir']/'manifest.json'
    value=json.loads(path.read_text(encoding='utf-8'));value[field]=False if field=='pending' else True
    save(path,value);refresh_proof(f)
    with pytest.raises(ValueError):module('import_classification_database').import_classifications(**f)
    assert not f['output_dir'].exists()


def test_malformed_edition_expression_link_rejected_even_with_valid_db_sha(fixture):
    f=fixture
    with sqlite3.connect(f['source_db']) as db:
        expression,own_work=db.execute('SELECT expression_id,work_id FROM expressions WHERE expression_id IN (SELECT expression_id FROM edition_contents)').fetchone()
        other_work=db.execute('SELECT work_id FROM works WHERE resolved=1 AND canonical_id=work_id AND work_id!=?',(own_work,)).fetchone()[0]
        db.execute('UPDATE edition_contents SET work_id=? WHERE expression_id=?',(other_work,expression))
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.close()
    proof=json.loads(f['terminal_proof'].read_text(encoding='utf-8'));proof['artifact_sha256']['database']=sha(f['source_db'])
    save(f['terminal_proof'],proof)
    with pytest.raises(ValueError,match='Edition'):
        module('import_classification_database').import_classifications(**f)
    assert not f['output_dir'].exists()


def test_invalid_cached_result_preserves_distinct_status_and_failure_count(fixture):
    f=fixture;path=f['run_dir']/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    key=next(k for k,s in manifest['jobs'].items() if s['status']=='held')
    manifest['jobs'][key]['status']='invalid_cached_result'
    save(f['run_dir']/'jobs'/(key+'.json'),manifest['jobs'][key]);save(path,manifest);refresh_proof(f)
    report=module('import_classification_database').import_classifications(**f)
    assert report['held']==0 and report['invalid_cached_result']==1 and report['failed']==1
    with sqlite3.connect(f['output_dir']/'dataset.sqlite') as db:
        assert db.execute("SELECT count(*) FROM classification_inputs WHERE status='invalid_cached_result' AND result_json IS NULL").fetchone()[0]==1


@pytest.mark.parametrize('representation', ['native', 'slash', 'backslash'])
def test_runner_native_and_backslash_result_paths_are_accepted(fixture, representation):
    f=fixture;path=f['run_dir']/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    for key,state in manifest['jobs'].items():
        if state['status']=='completed':
            state['result_path']={'native':str(Path('results')/(key+'.json')),
                                  'slash':'results/'+key+'.json', 'backslash':'results\\'+key+'.json'}[representation]
            save(f['run_dir']/'jobs'/(key+'.json'),state)
    save(path,manifest);refresh_proof(f)
    report=module('import_classification_database').import_classifications(**f)
    assert report['completed']==2


@pytest.mark.parametrize('representation', ['absolute', 'drive_relative', 'drive_absolute', 'unc',
    'rooted', 'parent_escape', 'parent_inside', 'backslash_parent', 'other_job'])
def test_result_paths_reject_unsafe_or_wrong_job_targets(fixture, representation):
    f=fixture;path=f['run_dir']/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
    keys=[k for k,s in manifest['jobs'].items() if s['status']=='completed'];key=keys[0]
    representations={'absolute':str((f['run_dir']/'results'/(key+'.json')).resolve()),
        'drive_relative':'C:results/'+key+'.json', 'drive_absolute':'C:\\results\\'+key+'.json',
        'unc':'\\\\server\\share\\'+key+'.json', 'rooted':'\\results\\'+key+'.json',
        'parent_escape':'../results/'+key+'.json', 'parent_inside':'results/../results/'+key+'.json',
        'backslash_parent':'results\\..\\results\\'+key+'.json', 'other_job':'results/'+keys[1]+'.json'}
    manifest['jobs'][key]['result_path']=representations[representation]
    save(f['run_dir']/'jobs'/(key+'.json'),manifest['jobs'][key]);save(path,manifest);refresh_proof(f)
    with pytest.raises(ValueError):module('import_classification_database').import_classifications(**f)
    assert not f['output_dir'].exists()
