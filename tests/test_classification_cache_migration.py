"""Offline cache migration fixtures; no production result reads or real CLI."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.runner import build_run_config,run_classification
from recommend_book.classification.validation import input_hash,load_taxonomy
from recommend_book.jobs import JobStore,canonical_json,job_key
from recommend_book.models import Evidence,WorkInput
from test_classification import result_for


def script():
    spec=importlib.util.spec_from_file_location('revalidate_classification_cache',Path(__file__).parents[1]/'scripts/revalidate_classification_cache.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(canonical_json(value)+'\n',encoding='utf8')


def fixture(tmp_path,*,held=False,attempts=1):
    source=tmp_path/'source';snapshot=tmp_path/'snapshot';source.mkdir();snapshot.mkdir()
    works=[WorkInput(work_id=f'work-{i}',title='Local snapshot',original_language='en',source_record_ids=[f'src-{i}'],evidence=[Evidence(evidence_id='e1',kind='synopsis',text='A detective solves a murder.',source_url='https://example.test',rights_policy_id='fixture',llm_input_allowed=True,locator='summary')]) for i in range(2)]
    new=build_run_config('gpt-6.1-sol','fake-1',load_taxonomy(),2);old={k:v for k,v in new.items() if k not in ('validation_version','validation_hash')}
    raw=''.join(w.model_dump_json()+'\n' for w in works).encode();(snapshot/'work-inputs.jsonl').write_bytes(raw)
    save(snapshot/'manifest.json',{'work_count':2,'work_inputs_sha256':hashlib.sha256(raw).hexdigest()})
    results=[result_for(w) for w in works]
    if held:
        for r in results:r['labels'][0]['relevance']=None
    states={};keys=[job_key(input_hash(w),old) for w in works]
    histories=[]
    for attempt in range(1,attempts+1):
        final=f'attempts/{keys[0]}.{attempt}.fixture.final.json';events=f'attempts/{keys[0]}.{attempt}.fixture.stdout.jsonl'
        save(source/final,{'results':results})
        stdout=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'results':results})}},{'type':'turn.completed','usage':{'input_tokens':17,'output_tokens':11}}]
        (source/events).write_text(''.join(json.dumps(e)+'\n' for e in stdout),encoding='utf8')
        histories.append({'attempt':attempt,'seconds':1.0,'returncode':0,'usage':{'input_tokens':17,'output_tokens':11},'error_category':'invalid_result' if held else None,'events_path':events,'final_path':final,'shared_batch_size':2})
    for key,w,r in zip(keys,works,results):
        state={'key':key,'status':'held' if held else 'completed','attempts':attempts,'lease_until':None,'lease_token':'old-token','updated_at':1.0,'work_id':w.work_id,'expression_id':None,'input_hash':input_hash(w),'config':old,'history':deepcopy(histories)}
        if held:state.update(error_category='invalid_result',error='Known decisions need evidence, relevance and raw confidence')
        else:
            state['result_path']=f'results/{key}.json';path=source/state['result_path'];path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(r,indent=2)+'\n',encoding='utf8')
        states[key]=state;save(source/f'jobs/{key}.json',state)
    (source/'results.jsonl').write_text('' if held else ''.join(canonical_json(r)+'\n' for r in results),encoding='utf8')
    save(source/'manifest.json',{'run_id':'terminal-fixture','config':old,'selected':2,'completed':0 if held else 2,'failed':2 if held else 0,'pending':0,'jobs':states,'attempts':2*attempts,'cli_invocations':attempts,'usage':{'input_tokens':17*attempts,'output_tokens':11*attempts},'human_quality_verified':False})
    return source,snapshot,works,new


def update_state(source,key,mutate):
    manifest=json.loads((source/'manifest.json').read_text());state=manifest['jobs'][key];mutate(state)
    save(source/f'jobs/{key}.json',state);save(source/'manifest.json',manifest)


def test_completed_byte_copy_import_attempts_zero_and_future_runner_cli_zero(tmp_path,monkeypatch):
    source,snapshot,works,new=fixture(tmp_path);before={p:p.read_bytes() for p in source.rglob('*') if p.is_file()};out=tmp_path/'new'
    report=script().migrate(source,snapshot,out,new_config=new)
    assert report['cache_imports']==2 and report['usage']=={} and report['cli_invocations']==0 and report['attempts']==0
    ledger=[json.loads(l) for l in (out/'migration-provenance.jsonl').read_text().splitlines()]
    for row,w in zip(ledger,works):
        state=JobStore(out).get(job_key(input_hash(w),new))
        assert state['attempts']==0 and state['history']==[] and state['status']=='completed'
        assert (out/state['result_path']).read_bytes()==(source/row['source_durable_result_path']).read_bytes()
        assert row['source_raw_result_value_hash']==row['new_result_value_hash']
    assert all(p.read_bytes()==raw for p,raw in before.items())
    import_manifest=out/'migration-manifest.json'
    original_import_bytes=import_manifest.read_bytes()
    original_import_sha=hashlib.sha256(original_import_bytes).hexdigest()
    assert json.loads(original_import_bytes)==report
    assert 'migration-manifest.json' not in report['output_sha256']
    assert 'manifest.json' not in report['output_sha256']
    import recommend_book.classification.runner as runner
    def prohibited(*a,**k):raise AssertionError('No CLI discovery/version/invocation permitted without a usage ledger')
    monkeypatch.setattr(runner,'resolve_executable',prohibited)
    monkeypatch.setattr(runner,'cli_version',lambda command:'fake-1')
    monkeypatch.setattr(runner.subprocess,'run',prohibited)
    before_current_manifest=(out/'manifest.json').read_bytes()
    with pytest.raises(ValueError,match='usage ledger'):
        run_classification(works,out,batch_size=2)
    assert import_manifest.read_bytes()==original_import_bytes
    assert hashlib.sha256(import_manifest.read_bytes()).hexdigest()==original_import_sha
    preserved=json.loads(import_manifest.read_bytes())
    assert preserved['cache_imports']==2 and preserved['cli_invocations']==0 and preserved['usage']=={}
    assert preserved['legacy_source_totals']['usage']=={'input_tokens':17,'output_tokens':11}
    assert (out/'manifest.json').read_bytes()==before_current_manifest
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_held_whole_batch_known_null_last_valid_attempt_keeps_values(tmp_path):
    source,snapshot,works,new=fixture(tmp_path,held=True,attempts=3);out=tmp_path/'new'
    report=script().migrate(source,snapshot,out,new_config=new)
    assert report['cache_imports']==2
    ledger=[json.loads(l) for l in (out/'migration-provenance.jsonl').read_text().splitlines()]
    assert all(row['source_attempt']['attempt']==3 for row in ledger)
    for row in ledger:
        imported=json.loads((out/row['new_result_path']).read_text())
        assert imported['labels'][0]['relevance'] is None
        assert row['source_raw_result_value_hash']==row['new_result_value_hash']
        assert row['source_validation_policy_recorded'] is False


def test_one_invalid_batch_member_holds_entire_batch_no_partial_salvage(tmp_path):
    source,snapshot,works,new=fixture(tmp_path,held=True)
    manifest=json.loads((source/'manifest.json').read_text());history=next(iter(manifest['jobs'].values()))['history'][0]
    final=json.loads((source/history['final_path']).read_text());final['results'][1]['labels'][0]['evidence'][0]['quote']='fabricated';save(source/history['final_path'],final)
    events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},{'type':'turn.completed','usage':history['usage']}]
    (source/history['events_path']).write_text(''.join(json.dumps(e)+'\n' for e in events))
    report=script().migrate(source,snapshot,tmp_path/'new',new_config=new)
    assert report['cache_imports']==0
    candidates=json.loads((tmp_path/'new/migration-candidates.json').read_text())
    assert candidates[0]['outcome']=='held' and 'Quote' in candidates[0]['reason']
    assert not list((tmp_path/'new/results').glob('*.json'))


@pytest.mark.parametrize('mode',['unfinished','job_mismatch','duplicate_input','unknown_id','config_changed','output_exists','index_mismatch','durable_mismatch','output_in_source'])
def test_source_and_namespace_boundaries_are_rejected(tmp_path,mode):
    source,snapshot,works,new=fixture(tmp_path);out=tmp_path/'new';manifest=json.loads((source/'manifest.json').read_text());key=next(iter(manifest['jobs']))
    if mode=='unfinished':manifest['pending']=1;save(source/'manifest.json',manifest)
    elif mode=='job_mismatch':state=json.loads((source/f'jobs/{key}.json').read_text());state['attempts']=2;save(source/f'jobs/{key}.json',state)
    elif mode=='duplicate_input':
        raw=(snapshot/'work-inputs.jsonl').read_bytes();(snapshot/'work-inputs.jsonl').write_bytes(raw+raw.splitlines(keepends=True)[0]);save(snapshot/'manifest.json',{'work_count':3,'work_inputs_sha256':hashlib.sha256((snapshot/'work-inputs.jsonl').read_bytes()).hexdigest()})
    elif mode=='unknown_id':
        update_state(source,key,lambda s:s.update(work_id='unknown'))
    elif mode=='config_changed':new['model']='different-model'
    elif mode=='output_exists':out.mkdir();(out/'keep').write_text('old')
    elif mode=='index_mismatch':(source/'results.jsonl').write_text('')
    elif mode=='durable_mismatch':
        path=source/manifest['jobs'][key]['result_path'];value=json.loads(path.read_text());value['input_hash']='0'*64;save(path,value)
    elif mode=='output_in_source':out=source/'nested'
    with pytest.raises(ValueError):script().migrate(source,snapshot,out,new_config=new)


@pytest.mark.parametrize('mode',['event_error','tool_use','returncode','agent_mismatch','batch_identity'])
def test_held_transport_events_and_full_identity_guards(tmp_path,mode):
    source,snapshot,works,new=fixture(tmp_path,held=True);manifest=json.loads((source/'manifest.json').read_text());history=next(iter(manifest['jobs'].values()))['history'][0]
    if mode=='returncode':
        for key in manifest['jobs']:update_state(source,key,lambda s:s['history'][0].update(returncode=1))
    else:
        path=source/history['events_path'];events=[json.loads(l) for l in path.read_text().splitlines()]
        if mode=='event_error':events.append({'type':'error','message':'failed'})
        elif mode=='tool_use':events.append({'type':'item.completed','item':{'type':'command_execution'}})
        elif mode=='agent_mismatch':events[0]['item']['text']='{}'
        elif mode=='batch_identity':
            final=json.loads((source/history['final_path']).read_text());final['results'][1]['work_id']='unknown';save(source/history['final_path'],final);events[0]['item']['text']=json.dumps(final)
        path.write_text(''.join(json.dumps(e)+'\n' for e in events))
    report=script().migrate(source,snapshot,tmp_path/'new',new_config=new)
    assert report['cache_imports']==0


@pytest.mark.parametrize('mode',['change','add'])
def test_source_change_while_seeding_never_publishes_new_cache(tmp_path,monkeypatch,mode):
    source,snapshot,works,new=fixture(tmp_path);module=script();original=module.JobStore.complete
    def mutate(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if mode=='change':(source/'results.jsonl').write_text('changed')
        else:(source/'unexpected.txt').write_text('added during seed')
    monkeypatch.setattr(module.JobStore,'complete',mutate)
    with pytest.raises(ValueError,match='protected source artifact changed'):
        module.migrate(source,snapshot,tmp_path/'new',new_config=new)
    assert not (tmp_path/'new').exists()
    assert not list(tmp_path.glob('new.migration-*'))


def test_latest_invalid_attempt_uses_previous_whole_valid_batch_deterministically(tmp_path):
    source,snapshot,works,new=fixture(tmp_path,held=True,attempts=3);manifest=json.loads((source/'manifest.json').read_text());metric=next(iter(manifest['jobs'].values()))['history'][-1]
    final=json.loads((source/metric['final_path']).read_text());final['results'][1]['labels'][0]['evidence'][0]['quote']='invented';save(source/metric['final_path'],final)
    events=[{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},{'type':'turn.completed','usage':metric['usage']}]
    (source/metric['events_path']).write_text(''.join(json.dumps(e)+'\n' for e in events))
    report=script().migrate(source,snapshot,tmp_path/'new',new_config=new)
    ledger=[json.loads(l) for l in (tmp_path/'new/migration-provenance.jsonl').read_text().splitlines()]
    assert report['cache_imports']==2 and all(r['source_attempt']['attempt']==2 for r in ledger)


def test_missing_one_batch_history_member_does_not_salvage_other_member(tmp_path):
    source,snapshot,works,new=fixture(tmp_path,held=True);manifest=json.loads((source/'manifest.json').read_text());key=next(iter(manifest['jobs']))
    update_state(source,key,lambda state:state.update(history=[],attempts=0))
    report=script().migrate(source,snapshot,tmp_path/'new',new_config=new)
    assert report['cache_imports']==0
    assert 'membership is incomplete' in json.loads((tmp_path/'new/migration-candidates.json').read_text())[0]['reason']


def test_exact_stage_input_must_match_ordered_prefix_and_frozen_hashes(tmp_path):
    source,snapshot,works,new=fixture(tmp_path);ordered=tmp_path/'ordered';frozen=tmp_path/'frozen';ordered.mkdir();frozen.mkdir()
    raw=(snapshot/'work-inputs.jsonl').read_bytes();(ordered/'ordered-inputs.jsonl').write_bytes(raw);(frozen/'work-inputs.jsonl').write_bytes(raw)
    save(ordered/'manifest.json',{'ordered_inputs_sha256':hashlib.sha256(raw).hexdigest(),'processing_context':{}})
    save(frozen/'manifest.json',{'work_inputs_sha256':hashlib.sha256(raw).hexdigest()});save(frozen/'processing-context.json',{})
    assert script().migrate(source,snapshot,tmp_path/'good',new_config=new,ordered_dir=ordered,frozen_dir=frozen)['cache_imports']==2
    reordered=b''.join(reversed(raw.splitlines(keepends=True)));(ordered/'ordered-inputs.jsonl').write_bytes(reordered)
    save(ordered/'manifest.json',{'ordered_inputs_sha256':hashlib.sha256(reordered).hexdigest(),'processing_context':{}})
    with pytest.raises(ValueError,match='ordered prefix byte copy'):
        script().migrate(source,snapshot,tmp_path/'bad',new_config=new,ordered_dir=ordered,frozen_dir=frozen)


def test_late_output_collision_preserves_existing_namespace(tmp_path,monkeypatch):
    source,snapshot,works,new=fixture(tmp_path);module=script();original=module.JobStore.complete;out=tmp_path/'new'
    def race(self,*args,**kwargs):
        original(self,*args,**kwargs)
        out.mkdir(exist_ok=True);(out/'keep').write_text('competing namespace')
    monkeypatch.setattr(module.JobStore,'complete',race)
    with pytest.raises(ValueError,match='output collision'):module.migrate(source,snapshot,out,new_config=new)
    assert (out/'keep').read_text()=='competing namespace'
    assert not list(tmp_path.glob('new.migration-*'))


@pytest.mark.parametrize('mode',['index_bool_as_number','event_bool_as_number'])
def test_json_value_identity_preserves_boolean_types(tmp_path,mode):
    source,snapshot,works,new=fixture(tmp_path)
    if mode=='index_bool_as_number':
        rows=[json.loads(line) for line in (source/'results.jsonl').read_text().splitlines()]
        rows[0]['needs_human_review']=1
        (source/'results.jsonl').write_text(''.join(canonical_json(row)+'\n' for row in rows))
        with pytest.raises(ValueError,match='result index differs'):
            script().migrate(source,snapshot,tmp_path/'new',new_config=new)
    else:
        manifest=json.loads((source/'manifest.json').read_text())
        metric=next(iter(manifest['jobs'].values()))['history'][0]
        events_path=source/metric['events_path']
        events=[json.loads(line) for line in events_path.read_text().splitlines()]
        message=json.loads(events[0]['item']['text']);message['results'][0]['needs_human_review']=1
        events[0]['item']['text']=json.dumps(message)
        events_path.write_text(''.join(json.dumps(row)+'\n' for row in events))
        report=script().migrate(source,snapshot,tmp_path/'new',new_config=new)
        assert report['cache_imports']==0
        assert 'immutable raw final' in json.loads((tmp_path/'new/migration-candidates.json').read_text())[0]['reason']


@pytest.mark.parametrize('missing',['--ordered','--frozen'])
def test_cli_requires_both_legacy_input_boundaries(tmp_path,monkeypatch,missing):
    import runpy
    import sys
    script_path=Path(__file__).parents[1]/'scripts/revalidate_classification_cache.py'
    argv=[str(script_path),'--source-run',str(tmp_path/'source'),'--input-snapshot',str(tmp_path/'snapshot'),'--output',str(tmp_path/'new')]
    for option in ('--ordered','--frozen'):
        if option!=missing:argv.extend([option,str(tmp_path/'legacy')])
    monkeypatch.setattr(sys,'argv',argv)
    with pytest.raises(SystemExit) as exc:runpy.run_path(str(script_path),run_name='__main__')
    assert exc.value.code==2
    assert not (tmp_path/'new').exists()
