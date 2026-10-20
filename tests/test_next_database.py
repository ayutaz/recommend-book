"""Purpose-safe next DB projection, scope and immutable history fixtures."""
import importlib
import json
from pathlib import Path
import sqlite3

import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Identifier, Title
from test_import_classification_database import record, save, sha, fixture, module, classification


def api():
    return importlib.import_module('recommend_book.next_database')


@pytest.fixture
def source_database(tmp_path):
    (tmp_path / 'history').mkdir()
    path = tmp_path / 'history' / 'source.sqlite'
    records = []
    for key, qid, kind, claims in [
        ('story', 'Q1', 'short_story', {'P31': [{'rank': 'normal', 'mainsnak': {'property': 'P31', 'snaktype': 'value', 'datavalue': {'value': {'id': 'Q49084'}}}}],
            'P361': [{'rank': 'normal', 'mainsnak': {'property': 'P361', 'snaktype': 'value', 'datavalue': {'value': {'id': 'Q2'}}}}]}),
        ('collection', 'Q2', 'collection', {'P527': [{'rank': 'normal', 'mainsnak': {'property': 'P527', 'snaktype': 'value', 'datavalue': {'value': {'id': 'Q1'}}}}],
            'P1476': [{'rank': 'normal', 'mainsnak': {'property': 'P1476', 'snaktype': 'value', 'datavalue': {'value': {'text': 'Collected Stories', 'language': 'en'}}}},
                {'rank': 'normal', 'mainsnak': {'property': 'P1476', 'snaktype': 'value', 'datavalue': {'value': {'text': 'Translated name', 'language': 'pt'}}}}]}),
    ]:
        raw = tmp_path / (qid + '.json')
        save(raw, {'entities': {qid: {'id': qid, 'labels': {'en': {'language': 'en', 'value': 'Same title'}, 'ja': {'language': 'ja', 'value': '表示名'}}, 'aliases': {}, 'claims': claims}}})
        records.append(record(key, source='wikidata', source_id=qid, work_kind=kind,
            titles=[Title(value='Same title', language='en', kind='preferred')], raw_path=str(raw),
            raw_sha256=sha(raw), identifiers=[Identifier(source='wikidata', value=qid, scope='work')]))
    with DatasetDB(path) as db:
        db.ingest(records)
    return path


def test_inspection_preserves_story_collection_and_published_title_scope(source_database):
    before = sha(source_database)
    result = api().inspect_next_database(source_database)
    assert result['audit']['historical_work_count'] == 2
    assert result['audit']['kind_counts'] == {'short_story': 1, 'collection': 1}
    assert result['audit']['raw_relationship_counts'] == {'part_of': 1, 'has_part': 1}
    assert len(result['works']) == 2
    assert len({w['work_id'] for w in result['works']}) == 2
    published = [t for w in result['works'] for t in w['title_assertions'] if t['assertion_type'] == 'published_title_candidate']
    assert {t['value'] for t in published} == {'Collected Stories', 'Translated name'}
    assert all(t['original_role_established'] is False for t in published)
    assert all(w['japanese_display_candidates'] for w in result['works'])
    assert sha(source_database) == before


def test_metadata_candidates_bind_exact_source_values_and_keep_unknown(source_database):
    result = api().inspect_next_database(source_database)
    assert len(result['metadata_lineage']) == 2
    for row in result['metadata_lineage']:
        assert set(row['fields']) == {'title', 'authors', 'original_language', 'first_publication_year', 'region', 'series'}
        assert row['fields']['title']['candidates']
        assert row['fields']['series']['value'] is None
        assert row['fields']['series']['unknown'] is True
        assert row['fields']['title']['candidates'][0]['source_version_id'] in row['source_record_ids']
        assert row['fields']['title']['candidates'][0]['material_field'] == 'bibliographic'


def test_purpose_proposals_keep_inventory_and_aliases_are_complete_inputs(source_database):
    from recommend_book.models import SourceRecord, WorkInput, Evidence
    with sqlite3.connect(source_database) as db:
        source = SourceRecord.model_validate_json(db.execute('SELECT record_json FROM source_records LIMIT 1').fetchone()[0])
    db.close()
    source.titles.append(Title(value='日本語表示', language='ja', kind='original'))
    source.evidence.append(Evidence(evidence_id='legacy:wikipedia-prose-v2', kind='synopsis', text='held text',
        source_url='https://example.org/old', rights_policy_id='wikipedia-screened-prose', llm_input_allowed=True))
    with DatasetDB(source_database) as db:
        db.ingest([source])
    proposals = api().build_next_input_proposals(source_database)
    assert len(proposals['bibliographic']) == 2
    assert all(not work.evidence for work in proposals['bibliographic'])
    assert len(proposals['aliases']) == 1
    alias = proposals['aliases'][0]
    assert isinstance(alias, WorkInput) and alias.title == '日本語表示'
    assert not alias.evidence
    assert all('wikipedia-prose-v2' not in ev.evidence_id for work in proposals['classification'] for ev in work.evidence)
    assert proposals['alias_metadata'][api().input_hash(alias)]['role'] == 'unknown'
    assert set(alias.source_record_ids) == set(next(w for w in proposals['bibliographic'] if w.work_id == alias.work_id).source_record_ids)


def test_alias_proposals_preserve_foreign_language_and_hold_ambiguous_language(source_database):
    from recommend_book.models import SourceRecord
    with sqlite3.connect(source_database) as db:
        source = SourceRecord.model_validate_json(db.execute('SELECT record_json FROM source_records LIMIT 1').fetchone()[0])
    db.close()
    source.titles.extend([Title(value='Titre publié', language='fr', kind='alternative'),
                         Title(value='Ambiguous', language='en', kind='alternative'),
                         Title(value='Ambiguous', language='de', kind='alternative'),
                         Title(value='Unknown language', kind='alternative')])
    with DatasetDB(source_database) as db:
        db.ingest([source])
    proposals = api().build_next_input_proposals(source_database)
    assert [w.title for w in proposals['aliases']] == ['Titre publié']
    alias = proposals['aliases'][0]
    assert not alias.evidence
    assert proposals['alias_metadata'][api().input_hash(alias)]['language'] == 'fr'
    assert proposals['alias_metadata'][api().input_hash(alias)]['role'] == 'unknown'


def test_new_database_keeps_original_tables_but_never_adopts_legacy_features(source_database, tmp_path):
    output = tmp_path / 'next-v3'
    before = sha(source_database)
    report = api().build_next_database(source_database=source_database, output_dir=output)
    assert report['bibliographic_work_count'] == 2
    assert report['classification_adopted_count'] == 0
    inputs = api().get_next_inputs(output / 'dataset.sqlite')
    assert len(inputs['bibliographic']) == 2
    with sqlite3.connect(source_database) as old, sqlite3.connect(output / 'dataset.sqlite') as new:
        for name, in old.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            assert old.execute('SELECT * FROM "' + name + '"').fetchall() == new.execute('SELECT * FROM "' + name + '"').fetchall()
        assert new.execute('PRAGMA foreign_key_check').fetchall() == []
    assert sha(source_database) == before
    with pytest.raises(ValueError, match='existing'):
        api().build_next_database(source_database=source_database, output_dir=output)


def test_loader_requires_whole_input_and_exact_derived_permission(source_database, tmp_path):
    from types import SimpleNamespace
    output = tmp_path / 'baseline'
    api().build_next_database(source_database=source_database, output_dir=output)
    class DenyingGuard:
        def check_input(self, work, usage):
            return SimpleNamespace(allowed=True)
        def check_derived(self, payload, origins, usage):
            return SimpleNamespace(allowed=False)
    loaded = api().load_next_world_database(output / 'dataset.sqlite', guard=DenyingGuard())
    assert loaded['rows'] == []
    assert loaded['audit']['bibliographic_work_count'] == 2
    assert loaded['audit']['purpose_held_count'] == 2
    class AllowedGuard(DenyingGuard):
        def check_derived(self, payload, origins, usage):
            assert origins
            assert set(payload) == {'work_id','title','authors','original_language','first_publication_year','region','series'}
            return SimpleNamespace(allowed=True)
    loaded = api().load_next_world_database(output / 'dataset.sqlite', guard=AllowedGuard())
    assert len(loaded['rows']) == 2
    assert all(row['features'] == {} and row['classification_originating_input_hashes'] == [] for row in loaded['rows'])
    with sqlite3.connect(output / 'dataset.sqlite') as db:
        db.execute("UPDATE next_projection_inputs SET packet_json='{}' WHERE kind='bibliographic'")
    db.close()
    with pytest.raises(ValueError, match='derivation'):
        api().load_next_world_database(output / 'dataset.sqlite', guard=AllowedGuard())


def test_atomic_publish_rejects_source_changes_and_cleans_only_stage(source_database, tmp_path, monkeypatch):
    output = tmp_path / 'failed-next'
    def change():
        with sqlite3.connect(source_database) as db:
            db.execute('CREATE TABLE mutation(value TEXT)')
        db.close()
    monkeypatch.setattr(api(), '_before_next_publish', change)
    with pytest.raises(ValueError, match='changed'):
        api().build_next_database(source_database=source_database, output_dir=output)
    assert not output.exists()
    assert not list(tmp_path.glob('.failed-next.next-*'))
    assert source_database.exists()


def test_recipe_code_change_before_publication_refuses_baseline(source_database,tmp_path,monkeypatch):
    module=api()
    original_sha=module._sha
    changed=[False]
    monkeypatch.setattr(module,'_before_next_publish',lambda:changed.__setitem__(0,True))
    monkeypatch.setattr(module,'_sha',lambda path:'0'*64 if changed[0] and Path(path).resolve()==Path(module.__file__).resolve() else original_sha(path))
    output=tmp_path/'code-changed'
    with pytest.raises(ValueError,match='changed'):
        module.build_next_database(source_database=source_database,output_dir=output)
    assert not output.exists()


@pytest.fixture
def next_run(tmp_path, monkeypatch):
    from recommend_book.classification.runner import build_run_config
    from recommend_book.classification.validation import load_taxonomy
    from recommend_book.jobs import job_key, canonical_json
    from test_classification_guarded import allowed_many_fixture, event_bytes
    from test_classification import native_many
    from recommend_book.classification.runner import run_classification
    import subprocess
    history = tmp_path/'native-history'
    history.mkdir()
    _,ledger,binary,_ = allowed_many_fixture(history,2)
    namespace = tmp_path / 'new-baseline'
    api().build_next_database(source_database=history/'source-many.sqlite', output_dir=namespace)
    works = api().get_next_inputs(namespace / 'dataset.sqlite')['classification']
    inputs = tmp_path / 'next-inputs' / 'ordered-inputs.jsonl'
    inputs.parent.mkdir()
    inputs.write_text(''.join(canonical_json(w.model_dump(mode='json')) + '\n' for w in works), encoding='utf-8')
    save(inputs.with_name('manifest.json'), {'ordered_inputs_sha256': sha(inputs)})
    context = tmp_path / 'next-context' / 'manifest.json'
    module('freeze_classification_execution').freeze_execution(inputs, context.parent, model='gpt-6.1-sol', cli_version='fake-1')
    context_value=json.loads(context.read_bytes())
    context_value['execution_context']['run_config']=build_run_config('gpt-6.1-sol','fake-1',load_taxonomy(),1,execution_spec='guarded-v3-baseline')
    save(context,context_value)
    run = tmp_path / 'next-run'
    def response(argv,kwargs,n):
        entry=json.loads(kwargs['input'].decode().split('BEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0])
        work=next(w for w in works if w.work_id==entry['work']['work_id'])
        result=classification(work)
        result.update(model='gpt-6.1-sol',cli_version='fake-1')
        Path(argv[argv.index('-o')+1]).write_bytes(json.dumps(result).encode())
        return subprocess.CompletedProcess(argv,0,event_bytes(result),b'')
    native_many(monkeypatch,works,responder=response)
    report=run_classification(works,run,executable=str(binary),usage_ledger=ledger,batch_size=1)
    assert report['completed']==2
    migration = tmp_path/'next-migration'/'manifest.json'
    save(migration, {'version':'fixture-no-rebind','llm_started':False})
    proof = tmp_path/'next-proof'/'proof.json'
    save(proof, {'schema_version':'classification-terminal-proof-v1','observed_at':'2026-10-13T00:00:00Z',
        'session_id':'fixture-only','exit_code':0,'process_exit_observed':True,'run_id':report['run_id'],
        'manifest_sha256':sha(run/'manifest.json'),'results_sha256':sha(run/'results.jsonl'),
        'ordered_inputs_sha256':sha(inputs),'execution_context_manifest_sha256':sha(context),
        'migration_manifest_sha256':sha(migration),'cli_profile_sha256_before':sha(context),
        'cli_profile_sha256_after':sha(context),'artifact_sha256':{'database':sha(namespace/'dataset.sqlite')}})
    from types import SimpleNamespace
    class FixtureGuard:
        def check_input(self, work, usage): return SimpleNamespace(allowed=True)
        def check_derived(self, payload, origins, usage, **kwargs): return SimpleNamespace(allowed=True)
    return {'source_database':namespace/'dataset.sqlite','ordered_inputs':inputs,'run_dir':run,
        'execution_context_manifest':context,'migration_manifest':migration,'terminal_proof':proof,
        'cli_profile':context,'output_dir':tmp_path/'next-classified','guard':FixtureGuard()}


def test_next_classification_import_preserves_tables_and_adopts_exact_native_results(next_run):
    before = sha(next_run['source_database'])
    report = api().import_next_classifications(**next_run)
    assert report['completed'] == 2
    assert report['legacy_features_adopted'] == 0
    assert sha(next_run['source_database']) == before
    loaded = api().load_next_world_database(next_run['output_dir']/'dataset.sqlite', guard=next_run['guard'], content_classified=True)
    assert len(loaded['rows']) == 2
    assert all(row['features'] == {'genre.mystery':1.0} for row in loaded['rows'])
    assert all(row['classification_derivation']['source_manifest']['artifacts']['final'] for row in loaded['rows'])
    with sqlite3.connect(next_run['output_dir']/'dataset.sqlite') as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('case',['conflict','abstained','review_only'])
def test_native_conflict_is_saved_but_not_promoted_to_recommendation_features(tmp_path,monkeypatch,case):
    import sys
    original = classification
    first = []
    def response(work):
        result = original(work)
        if not first:
            first.append(work.work_id)
        if work.work_id==first[0]:
            if case=='conflict':
                result['conflicts']=['The supplied synopsis concerns an unresolved version; work scope is uncertain.']
            elif case=='abstained':
                result['labels']=[]
                result['missing_axes']=[{'axis':axis,'reason':'Insufficient supplied material','decision':'unknown'}
                    for axis in ['genre','theme','structure','protagonist','style','atmosphere']]
            result['needs_human_review']=True
        return result
    monkeypatch.setattr(sys.modules[__name__],'classification',response)
    prepared=next_run.__wrapped__(tmp_path,monkeypatch)
    report=api().import_next_classifications(**prepared)
    loaded=api().load_next_world_database(prepared['output_dir']/'dataset.sqlite',guard=prepared['guard'],content_classified=True)
    assert len(loaded['rows'])==2  # Bibliography is available for held or abstaining results.
    row=next(row for row in loaded['rows'] if row['work_id']==first[0])
    if case in ('conflict','abstained'):
        assert row['features']=={}
        assert row['feature_provenance']=={}
        assert report['feature_adopted_work_count']==1
    else:
        assert row['features']=={'genre.mystery':1.0}
        assert report['feature_adopted_work_count']==2
    assert report['raw_classification_saved_count']==2
    status={'conflict':'held_conflict','abstained':'abstained','review_only':'adopted'}[case]
    assert row['feature_derivation']['feature_adoption']['status']==status
    assert loaded['audit']['conflict_held_count']==int(case=='conflict')
    assert loaded['audit']['abstained_work_count']==int(case=='abstained')
    assert loaded['audit']['feature_adopted_work_count']==report['feature_adopted_work_count']
    raw=row['classification_derivation']['payload']
    assert raw['needs_human_review'] is True
    if case=='conflict':
        assert len(raw['labels'])==1
        source_work=next(work for work in api().get_next_inputs(prepared['source_database'])['classification'] if work.work_id==first[0])
        assert raw['conflicts']==response(source_work)['conflicts']
        with sqlite3.connect(prepared['output_dir']/'dataset.sqlite') as db:
            assert db.execute('SELECT count(*) FROM next_classification_labels').fetchone()[0]==2


def quality_review_fixture(prepared, status='held'):
    manifest=json.loads((prepared['run_dir']/'manifest.json').read_bytes())
    key=next(iter(manifest['jobs']))
    result=json.loads(api()._importer().contained(prepared['run_dir'],manifest['jobs'][key]['result_path']).read_bytes())
    review_dir=prepared['output_dir'].parent/'quality-review'
    review_dir.mkdir()
    audit=review_dir/'ai-evidence-audit.json'
    save(audit,{'actor':'ai_internal_review','human_gold':False,'diagnosis':'Subject applicability uncertain'})
    false_flags={'human_gold':False,'authority_merge_approved':False,'confidence_calibrated':False}
    value={'schema_version':'next-quality-adoption-review-v1','actor':'ai_internal_review',**false_flags,
        'run_id':manifest['run_id'],'ordered_inputs_sha256':sha(prepared['ordered_inputs']),
        'run_manifest_sha256':sha(prepared['run_dir']/'manifest.json'),'results_sha256':sha(prepared['run_dir']/'results.jsonl'),
        'execution_context_manifest_sha256':sha(prepared['execution_context_manifest']),
        'source_audits':[{'path':str(audit.resolve()),'sha256':sha(audit)}],
        'cases':[{'work_id':result['work_id'],'input_hash':result['input_hash'],'job_key':key,
            'native_result_value_sha256':api()._value_sha(result),'status':status,
            'reason_codes':['evidence_subject_scope_mismatch' if status=='held' else 'semantic_entailment_uncertain'],
            'reasons':['AI diagnostic only; not an adjudication of human truth'],
            'label_ids':[result['labels'][0]['label_id']],'source_audit_sha256':[sha(audit)],**false_flags}]}
    path=review_dir/'adoption-review.json'
    save(path,value)
    prepared['quality_adoption_review']=path
    return path,value,result


@pytest.mark.parametrize('status',['held','uncertain'])
def test_quality_review_holds_whole_content_row_and_preserves_bibliography_and_native_labels(next_run,status):
    path,review,result=quality_review_fixture(next_run,status)
    report=api().import_next_classifications(**next_run)
    db_path=next_run['output_dir']/'dataset.sqlite'
    metadata=api().load_next_world_database(db_path,guard=next_run['guard'])
    content=api().load_next_world_database(db_path,guard=next_run['guard'],content_classified=True)
    assert len(metadata['rows'])==2
    assert len(content['rows'])==(1 if status=='held' else 2)
    assert report['raw_classification_saved_count']==2
    assert report['quality_held_count']==int(status=='held')
    assert content['audit']['quality_uncertain_count']==int(status=='uncertain')
    assert content['audit']['feature_adopted_work_count']==(1 if status=='held' else 2)
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT count(*) FROM next_classification_labels').fetchone()[0]==2
        raw=db.execute('SELECT result_json FROM next_classification_inputs WHERE job_key=?',(review['cases'][0]['job_key'],)).fetchone()[0]
        assert json.loads(raw)==result
        saved=db.execute('SELECT packet_json FROM next_quality_adoption_review').fetchone()[0]
        assert json.loads(saved)['case']==review['cases'][0]
    # Removal of a held row must not silently reactivate its features.
    with sqlite3.connect(db_path) as db:
        db.execute('DELETE FROM next_quality_adoption_review')
    with pytest.raises(ValueError,match='Quality'):
        api().load_next_world_database(db_path,guard=next_run['guard'],content_classified=True)


@pytest.mark.parametrize('bad',['input','result','run','gold','bool_flag','label','source_audit','status'])
def test_quality_review_requires_exact_native_binding_and_no_authority(next_run,bad):
    path,review,_=quality_review_fixture(next_run)
    case=review['cases'][0]
    if bad=='input':case['input_hash']='0'*64
    elif bad=='result':case['native_result_value_sha256']='0'*64
    elif bad=='run':review['run_id']='foreign-run'
    elif bad=='gold':case['human_gold']=True
    elif bad=='bool_flag':case['confidence_calibrated']=0
    elif bad=='label':case['label_ids']=['invented.label']
    elif bad=='source_audit':Path(review['source_audits'][0]['path']).write_bytes(b'{}')
    else:case['status']='uncertain'
    save(path,review)
    with pytest.raises(ValueError):api().import_next_classifications(**next_run)
    assert not next_run['output_dir'].exists()


@pytest.mark.parametrize('failure',['exit','result','input','denied','no_native','outside_native','foreign_native_run'])
def test_next_classification_refuses_unbound_derivations(next_run, failure):
    if failure == 'exit':
        proof = json.loads(next_run['terminal_proof'].read_text(encoding='utf-8'))
        proof['exit_code'] = 17
        save(next_run['terminal_proof'], proof)
    elif failure == 'result':
        path = next(next_run['run_dir'].glob('results/*.json'))
        result = json.loads(path.read_text(encoding='utf-8'))
        result['labels'][0]['raw_confidence'] = .7
        save(path,result)
    elif failure == 'input':
        next_run['ordered_inputs'].write_text('{}\n',encoding='utf-8')
    elif failure in ('outside_native','foreign_native_run'):
        manifest=json.loads((next_run['run_dir']/'manifest.json').read_bytes())
        state=next(iter(manifest['jobs'].values()))
        history=next(h for h in reversed(state['history']) if h.get('source_manifest_path'))
        descriptor_path=next_run['run_dir']/history['source_manifest_path']
        descriptor=json.loads(descriptor_path.read_bytes())
        if failure=='outside_native':
            outside=next_run['output_dir'].parent/'copied-native-prompt.bin'
            outside.write_bytes(Path(descriptor['artifacts']['prompt']['path']).read_bytes())
            descriptor['artifacts']['prompt']['path']=str(outside)
        else:
            path=Path(descriptor['artifacts']['request']['path'])
            request=json.loads(path.read_bytes())
            request['run_id']='foreign-run'
            save(path,request)
            descriptor['artifacts']['request']['sha256']=sha(path)
            attempt_path=Path(descriptor['artifacts']['attempt']['path'])
            attempt=json.loads(attempt_path.read_bytes())
            attempt['run_id']='foreign-run'
            save(attempt_path,attempt)
            descriptor['artifacts']['attempt']['sha256']=sha(attempt_path)
        save(descriptor_path,descriptor)
        history['source_manifest_sha256']=sha(descriptor_path)
        for key,state in manifest['jobs'].items():save(next_run['run_dir']/'jobs'/(key+'.json'),state)
        save(next_run['run_dir']/'manifest.json',manifest)
        save(next_run['run_dir']/('manifest.'+manifest['run_id']+'.json'),manifest)
        proof=json.loads(next_run['terminal_proof'].read_bytes())
        proof['manifest_sha256']=sha(next_run['run_dir']/'manifest.json')
        save(next_run['terminal_proof'],proof)
    elif failure == 'no_native':
        manifest=json.loads((next_run['run_dir']/'manifest.json').read_bytes())
        for key,state in manifest['jobs'].items():
            state['history']=[]
            save(next_run['run_dir']/'jobs'/(key+'.json'),state)
        save(next_run['run_dir']/'manifest.json',manifest)
        save(next_run['run_dir']/('manifest.'+manifest['run_id']+'.json'),manifest)
        proof=json.loads(next_run['terminal_proof'].read_bytes())
        proof['manifest_sha256']=sha(next_run['run_dir']/'manifest.json')
        save(next_run['terminal_proof'],proof)
    else:
        from types import SimpleNamespace
        next_run['guard'].check_input = lambda work,usage: SimpleNamespace(allowed=False)
    with pytest.raises(ValueError):
        api().import_next_classifications(**next_run)
    assert not next_run['output_dir'].exists()


@pytest.mark.parametrize('mutation',['feature','numeric'])
def test_arbitrary_feature_packet_or_changed_model_value_is_rejected(next_run, mutation):
    from recommend_book.jobs import canonical_json
    api().import_next_classifications(**next_run)
    path=next_run['output_dir']/'dataset.sqlite'
    with sqlite3.connect(path) as db:
        if mutation=='numeric':
            db.execute('UPDATE next_classification_labels SET raw_confidence=.999')
        else:
            key,raw=db.execute('SELECT job_key,packet_json FROM next_classification_derivations LIMIT 1').fetchone()
            packet=json.loads(raw)
            packet['payload']['features']['invented.feature']=100
            packet['value_sha256']=api()._value_sha(packet['payload'])
            db.execute('UPDATE next_classification_derivations SET packet_json=? WHERE job_key=?',(canonical_json(packet),key))
    db.close()
    with pytest.raises(ValueError,match='Classification'):
        api().load_next_world_database(path,guard=next_run['guard'],content_classified=True)


@pytest.mark.parametrize('invalid_reference',['quote','locator'])
def test_reader_revalidates_quote_even_when_all_saved_value_hashes_match(next_run, invalid_reference):
    """A matching hash does not establish that a quote occurs in its evidence."""
    from recommend_book.jobs import canonical_json
    from recommend_book.models import WorkInput
    api().import_next_classifications(**next_run)
    path = next_run['output_dir'] / 'dataset.sqlite'
    with sqlite3.connect(path) as db:
        key, run_id, input_json, raw = db.execute(
            'SELECT job_key,run_id,input_json,result_json FROM next_classification_inputs LIMIT 1').fetchone()
        result = json.loads(raw)
        result['labels'][0]['evidence'][0][invalid_reference] = 'This quotation is absent from the actual input.'
        result_sha = api()._value_sha(result)
        db.execute('UPDATE next_classification_inputs SET result_json=?,result_sha256=? WHERE job_key=?',
                   (canonical_json(result), result_sha, key))
        label = result['labels'][0]
        db.execute('UPDATE next_classification_labels SET label_json=? WHERE job_key=? AND label_id=?',
                   (canonical_json(label), key, label['label_id']))
        packet = json.loads(db.execute('SELECT packet_json FROM next_classification_derivations WHERE job_key=?', (key,)).fetchone()[0])
        packet['native_result'] = result
        packet['features_recipe']['native_result_sha256'] = result_sha
        packet['payload'] = api().extract_classification_features(WorkInput.model_validate_json(input_json), result, key, run_id)
        packet['value_sha256'] = api()._value_sha(packet['payload'])
        artifact = packet['adoption_provenance']['result_artifacts'][0]
        save(Path(artifact['path']), result)
        artifact['sha256'] = sha(Path(artifact['path']))
        db.execute('UPDATE next_classification_derivations SET packet_json=? WHERE job_key=?', (canonical_json(packet), key))
    with pytest.raises(ValueError, match='quote|Quote|locator'):
        api().load_next_world_database(path, guard=next_run['guard'], content_classified=True)


def test_relationship_annotation_is_bound_to_raw_and_does_not_merge(source_database,tmp_path):
    with sqlite3.connect(source_database) as db:
        version,work_id,raw_path,raw_sha=db.execute("SELECT s.version_id,w.canonical_id,s.raw_path,s.raw_sha256 FROM source_records s JOIN works w ON s.version_id=w.source_version_id WHERE s.source_id='Q1'").fetchone()
    db.close()
    statement=json.loads(Path(raw_path).read_bytes())['entities']['Q1']['claims']['P361'][0]
    annotation={'subject_work_id':work_id,'subject_source_version_id':version,'target_work_id':None,
        'target_source_version_id':None,'source':'wikidata','source_id':'Q1','raw_path':raw_path,'raw_sha256':raw_sha,
        'relation_type':'part_of','status':'held','assertion_locator':'entities.Q1.claims.P361[0]',
        'assertion_json':statement,'authority_merge_approved':False,'human_verified':False,'original_work_identity_verified':False}
    path=tmp_path/'relation.json'
    save(path,[annotation])
    report=api().build_next_database(source_database=source_database,output_dir=tmp_path/'annotated',authority_metadata_relations=path)
    assert report['relationship_assertion_count']==1
    assert report['bibliographic_work_count']==2
    assert report['authority_merges']==0
    del annotation['original_work_identity_verified']
    save(path,[annotation])
    unknown=api().build_next_database(source_database=source_database,output_dir=tmp_path/'unknown-original-role',authority_metadata_relations=path)
    assert unknown['authority_merges']==0
    supplement=tmp_path/'supplement.json'
    save(supplement,{'human_gold':0,'new_merges':0,'cases':[{'subject_work_id':work_id,
        'authority_merge_approved':False,'human_verified':False,'original_work_identity_verified':False,
        'active_source_evidence':[{'source_version_id':version,'raw_path':raw_path,'raw_sha256':raw_sha,
                                  'raw_claims':{'P361':[{'locator':'entities.Q1.claims.P361[0]','assertion_json':statement}]}}]}]})
    supplemented=api().build_next_database(source_database=source_database,output_dir=tmp_path/'supplemented',supplementary_type_audit=supplement)
    assert supplemented['supplementary_scope_annotation_count']==1
    annotation['assertion_json']={'invented':True}
    save(path,[annotation])
    with pytest.raises(ValueError,match='raw assertion'):
        api().build_next_database(source_database=source_database,output_dir=tmp_path/'bad-annotation',authority_metadata_relations=path)


def test_inspection_raw_sha_failure_is_not_silently_ignored(source_database):
    with sqlite3.connect(source_database) as db:
        raw = Path(db.execute('SELECT raw_path FROM source_records LIMIT 1').fetchone()[0])
    raw.write_bytes(b'altered')
    with pytest.raises(ValueError, match='raw'):
        api().inspect_next_database(source_database)


def test_owned_ratings_bind_only_explicit_original_work_ncode(source_database, tmp_path):
    owned = tmp_path / 'owned.sqlite'
    with sqlite3.connect(owned) as db:
        db.execute('CREATE TABLE imports(snapshot_id TEXT PRIMARY KEY, observed_at TEXT, imported_at TEXT)')
        db.execute('CREATE TABLE rating_observations(snapshot_id TEXT, ncode TEXT, title TEXT, my_rating INTEGER)')
        db.execute("INSERT INTO imports VALUES('snapshot','2026-10-13','2026-10-13')")
        db.execute("INSERT INTO rating_observations VALUES('snapshot','n1234aa','Same title',5)")
    # Immutable source SHA/version must be regenerated rather than patched.
    with DatasetDB(source_database) as db:
        head = db.connection.execute("SELECT record_json FROM source_records WHERE source_id='Q1'").fetchone()[0]
        original = __import__('recommend_book.models', fromlist=['SourceRecord']).SourceRecord.model_validate_json(head)
        data = json.loads(Path(original.raw_path).read_bytes())
        raw = tmp_path / 'Q1-new.json'
        data['entities']['Q1']['claims']['P856'] = [{'rank': 'normal', 'mainsnak': {'property': 'P856', 'snaktype': 'value', 'datavalue': {'value': 'https://ncode.syosetu.com/n1234aa/'}}}]
        data['entities']['Q1']['claims']['P407'] = [{'rank': 'normal', 'mainsnak': {'property': 'P407', 'snaktype': 'value', 'datavalue': {'value': {'id': 'Q5287'}}}}]
        save(raw, data)
        db.ingest([original.model_copy(update={'raw_path': str(raw), 'raw_sha256': sha(raw), 'original_language': 'ja'})])
    output = tmp_path.with_name(tmp_path.name + '-binding')
    result = api().inspect_rating_world_binding(source_database, owned, output)
    assert result['owned_ratings_count'] == 1
    assert result['verified_binding_count'] == 1
    assert result['title_only_binding_count'] == 0
    assert result['narou_api_metadata_read'] is False
    assert (output / 'bindings.jsonl').is_file()


def test_title_equality_does_not_create_owned_rating_world_binding(source_database, tmp_path):
    owned = tmp_path / 'owned.sqlite'
    with sqlite3.connect(owned) as db:
        db.execute('CREATE TABLE imports(snapshot_id TEXT PRIMARY KEY, observed_at TEXT, imported_at TEXT)')
        db.execute('CREATE TABLE rating_observations(snapshot_id TEXT, ncode TEXT, title TEXT, my_rating INTEGER)')
        db.execute("INSERT INTO imports VALUES('snapshot','2026-10-13','2026-10-13')")
        db.execute("INSERT INTO rating_observations VALUES('snapshot','n1234aa','Same title',5)")
    result = api().inspect_rating_world_binding(source_database, owned, tmp_path.with_name(tmp_path.name + '-binding'))
    assert result['verified_binding_count'] == 0 and result['explicit_ncode_candidate_count'] == 0
