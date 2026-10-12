"""Synthetic changed-only overlay; new classification never rebinds old labels."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import input_hash,load_taxonomy
from recommend_book.database import DatasetDB,_id
from recommend_book.jobs import canonical_json,job_key
from recommend_book.models import Contributor,Identifier,Title,WorkInput
from recommend_book.recommendation.world import load_world_database
from test_import_classification_database import module,record,classification,save,sha


def overlay():return importlib.import_module('recommend_book.recommendation.overlay')
def write_inputs(path,works):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(work.model_dump_json()+'\n' for work in works),encoding='utf-8')


@pytest.fixture
def prepared(tmp_path):
    source=tmp_path/'source.sqlite';base=tmp_path/'base.sqlite';new=tmp_path/'new.sqlite'
    with DatasetDB(source) as db:
        db.ingest([record('one'),record('two')]);old=db.work_inputs()
    importer=module('import_classification_database')
    config=build_run_config('fixture-model','supplied-test-cli',load_taxonomy(),1)
    source_db=sqlite3.connect(source);source_db.row_factory=sqlite3.Row
    destination=sqlite3.connect(base)
    try:
        source_db.backup(destination)
        for ddl in importer.DDL:destination.execute(ddl)
        jobs={job_key(input_hash(w),config):{'status':'completed'} for w in old}
        expected={job_key(input_hash(w),config):w for w in old}
        importer.insert_rows(destination,source_db,{'run_id':'base-fixture','config':config,'jobs':jobs},
            {},{},{},{},expected,{key:classification(work) for key,work in expected.items()})
        destination.commit()
    finally:destination.close()
    destination=sqlite3.connect(new)
    try:source_db.backup(destination)
    finally:destination.close();source_db.close()
    old_one=next(w for w in old if w.title=='Book one')
    addition_id=next('addition'+str(i) for i in range(1000) if _id('work','fixture','addition'+str(i))<old_one.work_id)
    with DatasetDB(new) as db:
        db.ingest([record(addition_id,titles=[Title(value='Book one')],
            contributors=[Contributor(name='Author one',identifiers=[Identifier(source='wikidata',value='Q999',scope='agent')])],
            identifiers=[Identifier(source='fixture',value='one',scope='work')],series='New authority series',
            region=['GB'],first_publication_year=1930)])
        current=db.work_inputs()
    assert len(current)==len(old)==2
    new_one=next(w for w in current if w.title=='Book one')
    assert new_one.work_id!=old_one.work_id
    namespace=tmp_path/'delta';namespace.mkdir()
    old_inputs=namespace/'old.jsonl';all_inputs=namespace/'new-all.jsonl';delta_inputs=namespace/'work-inputs.jsonl'
    write_inputs(old_inputs,old);write_inputs(all_inputs,current);write_inputs(delta_inputs,[new_one])
    old_lines=old_inputs.read_bytes().splitlines(keepends=True)
    new_lines={json.loads(line)['work_id']:line for line in all_inputs.read_bytes().splitlines(keepends=True)}
    (namespace/'all-inputs.jsonl').write_bytes(b''.join(new_lines[new_one.work_id] if w.work_id==old_one.work_id else line
        for w,line in zip(old,old_lines)))
    for name in importer.ARTIFACTS:
        target=namespace/'artifacts'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(importer.ROOT/name,target)
    with sqlite3.connect(base) as db:
        crosswalk=[{'old_canonical_id':w.work_id,'new_canonical_ids':[new_one.work_id if w.work_id==old_one.work_id else w.work_id],
            'old_work_node_ids':[r[0] for r in db.execute('SELECT work_id FROM works WHERE resolved=1 AND canonical_id=? ORDER BY work_id',(w.work_id,))]} for w in old]
    crosswalk_path=namespace/'canonical-crosswalk.source.json';save(crosswalk_path,crosswalk)
    current_index={w.work_id:w for w in current}
    comparison=[]
    for before,cross in zip(old,crosswalk):
        after=current_index[cross['new_canonical_ids'][0]]
        comparison.append({'old_work_id':before.work_id,'new_work_id':after.work_id,'expression_id':None,
            'old_input_hash':input_hash(before),'new_input_hash':input_hash(after),
            'changed':input_hash(before)!=input_hash(after),'identity_changed':before.work_id!=after.work_id,
            'source_refs_changed':before.source_record_ids!=after.source_record_ids,
            'evidence_changed':before.evidence!=after.evidence})
    comparison_path=namespace/'input-comparison.json';save(comparison_path,comparison)
    manifest=namespace/'manifest.json'
    save(manifest,{'version':'enrichment-classification-delta-v1','old_inputs_path':str(old_inputs),'old_inputs_sha256':sha(old_inputs),
        'new_database_path':str(new),'new_database_sha256':sha(new),'new_all_inputs_path':str(all_inputs),
        'all_inputs_sha256':sha(namespace/'all-inputs.jsonl'),
        'new_all_inputs_sha256':sha(all_inputs),'canonical_crosswalk_path':crosswalk_path.name,
        'canonical_crosswalk_sha256':sha(crosswalk_path),'input_comparison_path':comparison_path.name,
        'input_comparison_sha256':sha(comparison_path),'work_count':1,'ordered_inputs_sha256':sha(delta_inputs),
        'work_inputs_sha256':sha(delta_inputs),'total_work_count':2,'changed_work_ids':[new_one.work_id],
        'unchanged_work_ids':[w.work_id for w in current if w.work_id!=new_one.work_id],
        'identity_changed_count':1,'changed_count':1,'unchanged_count':1,
        'execution_context':{'run_config':config,'controls':{'batch_size':1},'artifact_sha256':{
            name:sha(importer.ROOT/name) for name in importer.ARTIFACTS}},'source_files_unchanged_before_publish':True})
    run=tmp_path/'run';run.mkdir();key=job_key(input_hash(new_one),config)
    result=classification(new_one);result['labels'][0]['label_id']='genre.fantasy'
    save(run/'results'/(key+'.json'),result)
    state={'key':key,'status':'completed','lease_until':None,'work_id':new_one.work_id,'expression_id':None,
        'input_hash':input_hash(new_one),'config':config,'result_path':str(Path('results')/(key+'.json'))}
    save(run/'jobs'/(key+'.json'),state)
    results=run/'results.jsonl';results.write_text(canonical_json(result)+'\n',encoding='utf-8')
    run_manifest=run/'manifest.json'
    save(run_manifest,{'run_id':'delta-fixture','config':config,'selected':1,'completed':1,'failed':0,'pending':0,
        'stopped':False,'human_quality_verified':False,'jobs':{key:state}})
    proof=tmp_path/'proof.json'
    save(proof,{'schema_version':'classification-terminal-proof-v1','observed_at':'2026-10-13T00:00:00Z',
        'session_id':'fixture','exit_code':0,'process_exit_observed':True,'run_id':'delta-fixture',
        'manifest_sha256':sha(run_manifest),'results_sha256':sha(results),'ordered_inputs_sha256':sha(delta_inputs),
        'execution_context_manifest_sha256':sha(manifest),'cli_profile_sha256_before':sha(manifest),
        'cli_profile_sha256_after':sha(manifest),'artifact_sha256':{'base_database':sha(base),'enriched_database':sha(new),
            'canonical_crosswalk':sha(crosswalk_path),'input_comparison':sha(comparison_path)}})
    return {'base_rows':load_world_database(base)['rows'],'base_database':base,'enriched_database':new,
        'delta_inputs':delta_inputs,'delta_manifest':manifest,'results':results,'run_manifest':run_manifest,'terminal_proof':proof}


def refresh(p):
    proof=json.loads(p['terminal_proof'].read_text(encoding='utf-8'));manifest=json.loads(p['delta_manifest'].read_text(encoding='utf-8'))
    for field,path in [('manifest_sha256',p['run_manifest']),('results_sha256',p['results']),('ordered_inputs_sha256',p['delta_inputs']),
        ('execution_context_manifest_sha256',p['delta_manifest']),('cli_profile_sha256_before',p['delta_manifest']),('cli_profile_sha256_after',p['delta_manifest'])]:proof[field]=sha(path)
    for field,path in [('base_database',p['base_database']),('enriched_database',p['enriched_database']),
        ('canonical_crosswalk',p['delta_manifest'].parent/manifest['canonical_crosswalk_path']),
        ('input_comparison',p['delta_manifest'].parent/manifest['input_comparison_path'])]:proof['artifact_sha256'][field]=sha(path)
    save(p['terminal_proof'],proof)


def test_replaces_changed_rows_with_new_id_and_preserves_unchanged_values(prepared):
    before=deepcopy(prepared['base_rows']);old_id=next(r['work_id'] for r in before if r['title']=='Book one')
    for row in prepared['base_rows']:row.update(rating=5,read_status='read',popularity=0.4)
    loaded=overlay().apply_classification_overlay(**prepared)
    changed=next(r for r in loaded['rows'] if r['title']=='Book one')
    unchanged=next(r for r in loaded['rows'] if r['title']=='Book two')
    assert changed['work_id']!=old_id and changed['features']=={'genre.fantasy':1.0}
    assert 'genre.mystery' not in changed['feature_provenance']
    assert changed['author_ids'] and changed['series_ids'] and changed['region']==['GB']
    assert changed['first_publication_year']==1930 and changed['series']=='New authority series'
    assert changed['rating']==5 and changed['read_status']=='read' and changed['popularity']==0.4
    original_unchanged=next(r for r in prepared['base_rows'] if r['work_id']==unchanged['work_id'])
    assert unchanged is original_unchanged and canonical_json(unchanged)==canonical_json(original_unchanged)
    assert loaded['audit']['old_to_new_canonical'][old_id]==changed['work_id']
    assert loaded['audit']['identity_changed_count']==1 and loaded['audit']['unchanged_work_count']==1
    assert loaded['audit']['old_labels_rebound'] is False and loaded['audit']['human_verified'] is False
    with sqlite3.connect(prepared['enriched_database']) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'classification_%'").fetchone()[0]==0


@pytest.mark.parametrize('mutation',['pending','bool_pending','no_exit','bool_exit','config','results_order','quote','missing_delta','unknown_version','crosswalk'])
def test_rejects_unproven_or_inexact_overlay(prepared,mutation):
    p=prepared
    if mutation in ('pending','bool_pending','config'):
        value=json.loads(p['run_manifest'].read_text(encoding='utf-8'))
        if mutation=='pending':value['pending']=1
        if mutation=='bool_pending':value['pending']=False
        if mutation=='config':value['config']['validation_version']='legacy'
        save(p['run_manifest'],value);refresh(p)
    elif mutation in ('no_exit','bool_exit'):
        proof=json.loads(p['terminal_proof'].read_text(encoding='utf-8'));proof['process_exit_observed']=False if mutation=='no_exit' else True
        if mutation=='bool_exit':proof['exit_code']=True
        save(p['terminal_proof'],proof)
    elif mutation in ('results_order','quote'):
        result=json.loads(p['results'].read_text(encoding='utf-8'))
        if mutation=='results_order':p['results'].write_text('',encoding='utf-8')
        else:
            result['labels'][0]['evidence'][0]['quote']='Not in evidence'
            save(p['results'],result)
            run=json.loads(p['run_manifest'].read_text(encoding='utf-8'));key=next(iter(run['jobs']))
            save(p['run_manifest'].parent/'results'/(key+'.json'),result)
        refresh(p)
    elif mutation in ('missing_delta','unknown_version'):
        work=json.loads(p['delta_inputs'].read_text(encoding='utf-8'))
        if mutation=='missing_delta':p['delta_inputs'].write_text('',encoding='utf-8')
        else:work['source_record_ids']=['unknown'];save(p['delta_inputs'],work)
        manifest=json.loads(p['delta_manifest'].read_text(encoding='utf-8'))
        manifest['ordered_inputs_sha256']=manifest['work_inputs_sha256']=sha(p['delta_inputs'])
        save(p['delta_manifest'],manifest);refresh(p)
    else:
        manifest=json.loads(p['delta_manifest'].read_text(encoding='utf-8'));path=p['delta_manifest'].parent/manifest['canonical_crosswalk_path']
        cross=json.loads(path.read_text(encoding='utf-8'));cross[0]['old_work_node_ids']=['invented-node'];save(path,cross)
        manifest['canonical_crosswalk_sha256']=sha(path);save(p['delta_manifest'],manifest);refresh(p)
    initial=deepcopy(p['base_rows'])
    with pytest.raises(ValueError):overlay().apply_classification_overlay(**p)
    assert p['base_rows']==initial


@pytest.mark.parametrize('exit_code', [1, 17, -1])
def test_rejects_unsuccessful_terminal_process(prepared, exit_code):
    proof = json.loads(prepared['terminal_proof'].read_text(encoding='utf-8'))
    proof['exit_code'] = exit_code
    save(prepared['terminal_proof'], proof)
    before = deepcopy(prepared['base_rows'])
    with pytest.raises(ValueError, match='successful terminal'):
        overlay().apply_classification_overlay(**prepared)
    assert prepared['base_rows'] == before


def test_rejects_nonempty_wal(prepared):
    Path(str(prepared['enriched_database'])+'-wal').write_bytes(b'fixture WAL')
    with pytest.raises(ValueError,match='WAL'):overlay().apply_classification_overlay(**prepared)


def test_rejects_changed_copied_code_without_changing_current_classifier(prepared):
    path=prepared['delta_manifest'].parent/'artifacts/src/recommend_book/classification/validation.py'
    path.write_bytes(b'fixture wrong copied code')
    with pytest.raises(ValueError,match='artifact'):overlay().apply_classification_overlay(**prepared)


def test_rejects_changed_full_overlay_input_snapshot(prepared):
    p=prepared;path=p['delta_manifest'].parent/'all-inputs.jsonl'
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError):overlay().apply_classification_overlay(**p)


@pytest.mark.parametrize('field',['work_count','changed_count','unchanged_count','identity_changed_count','total_work_count'])
def test_bool_is_not_a_delta_manifest_count(prepared,field):
    p=prepared;value=json.loads(p['delta_manifest'].read_text(encoding='utf-8'));value[field]=True
    save(p['delta_manifest'],value);refresh(p)
    with pytest.raises(ValueError):overlay().apply_classification_overlay(**p)


@pytest.mark.parametrize('target',['database','new_run_file','copied_code'])
def test_source_change_before_return_rejects_overlay_without_mutating_rows(prepared,monkeypatch,target):
    p=prepared;initial=deepcopy(p['base_rows'])
    def mutate():
        path={'database':p['enriched_database'],'new_run_file':p['run_manifest'].parent/'new-file',
              'copied_code':p['delta_manifest'].parent/'artifacts/config/sources.json'}[target]
        with path.open('ab') as stream:stream.write(b'fixture change')
    monkeypatch.setattr(overlay(),'_before_return',mutate)
    with pytest.raises(ValueError,match='changed'):overlay().apply_classification_overlay(**p)
    assert p['base_rows']==initial


def make_all_changed(p):
    with DatasetDB(p['enriched_database']) as db:
        db.ingest([record('two',first_publication_year=1900)]);current=db.work_inputs()
    manifest=json.loads(p['delta_manifest'].read_text(encoding='utf-8'))
    old_path=Path(manifest['old_inputs_path']);old=[json.loads(line) for line in old_path.read_bytes().splitlines()]
    cross=json.loads((p['delta_manifest'].parent/manifest['canonical_crosswalk_path']).read_text(encoding='utf-8'))
    mapping={row['old_canonical_id']:row['new_canonical_ids'][0] for row in cross}
    index={w.work_id:w for w in current};ordered=[index[mapping[w['work_id']]] for w in old]
    write_inputs(Path(manifest['new_all_inputs_path']),current);write_inputs(p['delta_inputs'],ordered)
    new_raw={json.loads(line)['work_id']:line for line in Path(manifest['new_all_inputs_path']).read_bytes().splitlines(keepends=True)}
    (p['delta_manifest'].parent/'all-inputs.jsonl').write_bytes(b''.join(new_raw[w.work_id] for w in ordered))
    comparisons=[]
    for before,after in zip(old,ordered):
        comparisons.append({'old_work_id':before['work_id'],'new_work_id':after.work_id,'expression_id':None,
            'old_input_hash':input_hash(WorkInput.model_validate(before)),
            'new_input_hash':input_hash(after),'changed':True,'identity_changed':before['work_id']!=after.work_id,
            'source_refs_changed':before['source_record_ids']!=after.source_record_ids,
            'evidence_changed':before['evidence']!=after.model_dump(mode='json')['evidence']})
    comparison_path=p['delta_manifest'].parent/manifest['input_comparison_path'];save(comparison_path,comparisons)
    manifest.update(new_database_sha256=sha(p['enriched_database']),new_all_inputs_sha256=sha(Path(manifest['new_all_inputs_path'])),
        all_inputs_sha256=sha(p['delta_manifest'].parent/'all-inputs.jsonl'),ordered_inputs_sha256=sha(p['delta_inputs']),
        work_inputs_sha256=sha(p['delta_inputs']),input_comparison_sha256=sha(comparison_path),
        changed_work_ids=[w.work_id for w in ordered],unchanged_work_ids=[],work_count=2,changed_count=2,unchanged_count=0)
    save(p['delta_manifest'],manifest)
    config=manifest['execution_context']['run_config'];values=[];states={}
    for work in ordered:
        key=job_key(input_hash(work),config);value=classification(work);value['labels'][0]['label_id']='genre.fantasy'
        values.append(value);save(p['run_manifest'].parent/'results'/(key+'.json'),value)
        state={'key':key,'status':'completed','lease_until':None,'work_id':work.work_id,'expression_id':None,
            'input_hash':input_hash(work),'config':config,'result_path':str(Path('results')/(key+'.json'))}
        save(p['run_manifest'].parent/'jobs'/(key+'.json'),state);states[key]=state
    p['results'].write_text(''.join(canonical_json(value)+'\n' for value in values),encoding='utf-8')
    run=json.loads(p['run_manifest'].read_text(encoding='utf-8'));run.update(selected=2,completed=2,jobs=states)
    save(p['run_manifest'],run);refresh(p)


def test_multiple_changed_results_require_exact_input_order(prepared):
    p=prepared;make_all_changed(p)
    assert overlay().apply_classification_overlay(**p)['audit']['changed_work_count']==2
    lines=p['results'].read_bytes().splitlines(keepends=True);p['results'].write_bytes(b''.join(reversed(lines)));refresh(p)
    with pytest.raises(ValueError):overlay().apply_classification_overlay(**p)


def test_held_changed_input_cannot_retain_old_features(prepared):
    p=prepared;run=json.loads(p['run_manifest'].read_text(encoding='utf-8'));key=next(iter(run['jobs']))
    run['jobs'][key].update(status='held',error='Fixture failure');run.update(completed=0,failed=1)
    save(p['run_manifest'],run);refresh(p)
    initial=deepcopy(p['base_rows'])
    with pytest.raises(ValueError):overlay().apply_classification_overlay(**p)
    assert p['base_rows']==initial


def test_projection_matches_world_adapter_without_writing_enriched_database(prepared,tmp_path):
    p=prepared;make_all_changed(p);overlaid=overlay().apply_classification_overlay(**p)
    projected=tmp_path/'fixture-classified-projection.sqlite'
    original=sqlite3.connect(p['enriched_database']);original.row_factory=sqlite3.Row
    db=sqlite3.connect(projected)
    try:
        original.backup(db);importer=module('import_classification_database')
        for statement in importer.DDL:db.execute(statement)
        run=json.loads(p['run_manifest'].read_text(encoding='utf-8'))
        works=[WorkInput.model_validate_json(line) for line in p['delta_inputs'].read_bytes().splitlines()]
        expected={job_key(input_hash(w),run['config']):w for w in works}
        values=[json.loads(line) for line in p['results'].read_bytes().splitlines()]
        importer.insert_rows(db,original,run,{},{},{},{},expected,dict(zip(expected,values)))
        db.commit()
    finally:db.close();original.close()
    loaded=load_world_database(projected)['rows']
    assert {r['work_id']:r for r in loaded}=={r['work_id']:r for r in overlaid['rows']}
    with sqlite3.connect(p['enriched_database']) as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'classification_%'").fetchone()[0]==0


def test_new_unknown_decision_removes_old_positive_feature(prepared):
    p=prepared;value=json.loads(p['results'].read_text(encoding='utf-8'))
    value['labels'][0].update(decision='unknown',relevance=None,raw_confidence=None,missing_reason='Not established')
    value['missing_axes'].append({'axis':'genre','decision':'unknown','reason':'Not established'})
    save(p['results'],value)
    run=json.loads(p['run_manifest'].read_text(encoding='utf-8'));key=next(iter(run['jobs']))
    save(p['run_manifest'].parent/'results'/(key+'.json'),value);refresh(p)
    output=overlay().apply_classification_overlay(**p)
    row=next(r for r in output['rows'] if r['title']=='Book one')
    assert row['features']=={} and row['feature_provenance']=={}
    assert next(r for r in p['base_rows'] if r['title']=='Book one')['features']=={'genre.mystery':1.0}
