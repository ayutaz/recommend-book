from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from recommend_book.autonomous_silver import (
    prepare_blind_inputs, compare_silver, run_blind_lanes, adjudicate_silver,
)
from recommend_book.classification.validation import input_hash
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json
from recommend_book.models import Evidence
from test_resolution import record
from test_classification import result_for


def fixture(tmp_path):
    tmp_path=tmp_path/'source';tmp_path.mkdir()
    database=tmp_path/'source.sqlite'
    evidence=Evidence(evidence_id='e1',kind='synopsis',text='A detective solves a murder.',
        language='en',source_url='https://example.org',rights_policy_id='fixture',
        llm_input_allowed=True,locator='summary')
    with DatasetDB(database) as db:
        db.ingest([record('a',original_language='en',evidence=[evidence]),record('b',original_language='en',evidence=[evidence])])
        works=db.work_inputs()
    pack=tmp_path/'pack.json';pack.write_text(json.dumps({'works':[w.model_dump(mode='json') for w in works],
        'predictions':[{'private_prediction':'NEVER_BLIND'}]}),encoding='utf8')
    inputs=tmp_path/'latest.jsonl';inputs.write_text(''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in works),encoding='utf8')
    crosswalk=tmp_path/'crosswalk.json';crosswalk.write_text(json.dumps([{'old_canonical_id':w.work_id,
        'new_canonical_ids':[w.work_id],'old_work_node_ids':[w.work_id]} for w in works]),encoding='utf8')
    return pack,inputs,crosswalk,database,works


def test_blind_preparation_sql_snapshot_and_no_prediction(tmp_path):
    pack,inputs,cross,database,works=fixture(tmp_path);out=tmp_path/'silver'; before={p:p.read_bytes() for p in (pack,inputs,cross,database)}
    manifest=prepare_blind_inputs(pack,inputs,cross,database,out,expected_count=2)
    assert manifest['provenance']=='ai_silver' and manifest['human_gold_created'] is False
    assert manifest['membership_count']==2 and manifest['sql_input_exact_match'] is True
    assert [json.loads(l) for l in (out/'blind-inputs.jsonl').read_text().splitlines()]==[w.model_dump(mode='json') for w in works]
    assert 'NEVER_BLIND' not in (out/'blind-inputs.jsonl').read_text()
    assert all(p.read_bytes()==b for p,b in before.items())
    assert all(not list((out/lane/'workspace').iterdir()) for lane in ['lane-a','lane-b'])


@pytest.mark.parametrize('bad',['duplicate','split','unknown','sql_mismatch','output_exists'])
def test_invalid_membership_identity_and_boundary_rejected(tmp_path,bad):
    pack,inputs,cross,database,works=fixture(tmp_path);out=tmp_path/'silver'
    if bad=='duplicate':
        obj=json.loads(pack.read_text());obj['works'].append(obj['works'][0]);pack.write_text(json.dumps(obj))
    elif bad in ('split','unknown'):
        obj=json.loads(cross.read_text());obj[0]['new_canonical_ids']=['unknown'] if bad=='unknown' else [works[0].work_id,works[1].work_id];cross.write_text(json.dumps(obj))
    elif bad=='sql_mismatch':
        obj=[json.loads(l) for l in inputs.read_text().splitlines()];obj[0]['title']='Changed without DB proof';inputs.write_text(''.join(json.dumps(w)+'\n' for w in obj))
    else:out.mkdir()
    with pytest.raises(ValueError):prepare_blind_inputs(pack,inputs,cross,database,out,expected_count=2)


def test_two_lanes_fixed_budget_pilot_resume_and_prompt_denied_filter(tmp_path):
    pack,inputs,cross,database,works=fixture(tmp_path);out=tmp_path/'silver';prepare_blind_inputs(pack,inputs,cross,database,out,expected_count=2)
    calls=[]
    def fake(works,output_dir,**kwargs):
        calls.append((works,output_dir,kwargs));return {'completed':len(works),'failed':0,'pending':0,'stopped':False}
    report=run_blind_lanes(out,pilot_count=1,runner=fake)
    assert len(calls)==2 and len({c[1] for c in calls})==2
    assert all(len(c[0])==1 and c[2]['concurrency']==2 and c[2]['batch_size']==5 and c[2]['stop_on_rate_limit'] for c in calls)
    assert report['human_gold_created'] is False


def test_agreement_unknown_is_not_absent_and_relevance_not_confidence(tmp_path):
    _,_,_,_,works=fixture(tmp_path);a=[result_for(w) for w in works];b=deepcopy(a)
    b[0]['labels'][0]['relevance']=None;b[0]['labels'][0]['raw_confidence']=.1
    b[1]['labels']=[];b[1]['missing_axes'].append({'axis':'genre','decision':'unknown','reason':'Insufficient'})
    report=compare_silver(works,a,b,a)
    assert report['decision_disagreement_work_ids']==[works[1].work_id]
    assert report['disagreement_work_ids']==[w.work_id for w in works]
    assert report['relevance']['both_known_pairs']==0 and report['relevance']['one_null_pairs']==1
    assert report['known_union']['decision_pairs']==2 and report['known_union']['agreement_pairs']==1
    assert report['human_gold_created'] is False and 'accuracy' not in report
    third=deepcopy(a)
    silver=adjudicate_silver(works,a,b,third)
    assert silver['rows'][1]['adjudication_status']=='third_supported_lane_a'
    assert silver['provenance']=='ai_silver' and silver['human_verified'] is False


def test_missing_result_is_held_not_unknown_agreement(tmp_path):
    _,_,_,_,works=fixture(tmp_path);a=[result_for(w) for w in works]
    report=compare_silver(works,a,a[:1],a)
    assert report['missing_lane_b']==[works[1].work_id]
    assert report['known_union']['decision_pairs']==1
    with pytest.raises(ValueError):compare_silver(works,a+[a[0]],a,a)
    wrong=deepcopy(a);wrong[0]['input_hash']='unbound'
    with pytest.raises(ValueError):compare_silver(works,a,wrong,a)


@pytest.mark.parametrize('tooluse',[False,True])
def test_existing_runner_blind_stdin_events_and_tool_rejection(tmp_path,monkeypatch,tooluse):
    pack,inputs,cross,database,works=fixture(tmp_path);out=tmp_path/'silver';prepare_blind_inputs(pack,inputs,cross,database,out,expected_count=2)
    calls=[]
    def fake(command,**kwargs):
        if '--version' in command:return subprocess.CompletedProcess(command,0,'fake-1','')
        assert kwargs['shell'] is False and '--ephemeral' in command and '--ignore-user-config' in command
        assert '--sandbox' in command and 'read-only' in command
        prompt=kwargs['input'];assert 'NEVER_BLIND' not in prompt and 'raw_confidence":' not in prompt
        data=json.loads(prompt.split('BEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0])
        values=data.get('works',[data]);results=[]
        for value in values:
            w=next(w for w in works if w.work_id==value['work']['work_id'])
            result=result_for(w);result.update(value['output_identity']);results.append(result)
        target=Path(command[command.index('-o')+1]);target.write_text(json.dumps({'results':results}),encoding='utf8')
        calls.append(command)
        event={'type':'item.completed','item':{'type':'command_execution'}} if tooluse else {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}
        return subprocess.CompletedProcess(command,0,json.dumps(event),'')
    monkeypatch.setattr('recommend_book.classification.runner.subprocess.run',fake)
    report=run_blind_lanes(out,executable='fixture-executable')
    assert len(calls)==2 and report['successful'] is (not tooluse)
    for lane in report['lanes'].values():
        assert lane['cli_invocations']==1
        assert lane['completed']==(0 if tooluse else 2)
        assert lane['failed']==(2 if tooluse else 0)
        if tooluse:assert all(j['error_category']=='tool_use' for j in lane['jobs'].values())
    if not tooluse:
        resume=run_blind_lanes(out,pilot_count=1,executable='fixture-executable')
        assert len(calls)==2 and all(m['reused']==1 and m['usage']=={} and m['cli_invocations']==0 for m in resume['lanes'].values())


def test_adjudication_different_third_judgment_remains_held(tmp_path):
    _,_,_,_,works=fixture(tmp_path);a=[result_for(w) for w in works];b=deepcopy(a);third=deepcopy(a)
    b[0]['labels'][0]['relevance']=.5;third[0]['labels'][0]['relevance']=.25
    report=adjudicate_silver(works,a,b,third)
    assert report['rows'][0]['adjudication_status']=='held_third_differs_both'
    assert report['rows'][0]['selected_result'] is None
    assert report['rows'][0]['third_result']==third[0]


def test_terminal_audit_rejects_index_durable_mismatch(tmp_path,monkeypatch):
    from recommend_book.autonomous_silver import audit_silver_run
    from recommend_book.classification.runner import run_classification
    pack,inputs,cross,database,works=fixture(tmp_path);out=tmp_path/'silver';prepare_blind_inputs(pack,inputs,cross,database,out,expected_count=2)
    def fake(command,**kwargs):
        if '--version' in command:return subprocess.CompletedProcess(command,0,'fake-1','')
        data=json.loads(kwargs['input'].split('BEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0]);values=data['works'];results=[]
        for value in values:
            w=next(w for w in works if w.work_id==value['work']['work_id']);r=result_for(w);r.update(value['output_identity']);results.append(r)
        Path(command[command.index('-o')+1]).write_text(json.dumps({'results':results}),encoding='utf8')
        return subprocess.CompletedProcess(command,0,'{"type":"turn.completed","usage":{"input_tokens":3}}','')
    monkeypatch.setattr('recommend_book.classification.runner.subprocess.run',fake)
    run_classification(works,out/'lane-a',executable='fixture-executable',batch_size=5)
    audit=audit_silver_run(out/'lane-a',works)
    assert audit['completed']==2 and audit['unique_cli_invocations']==1 and audit['tool_calls']==0
    index=out/'lane-a'/'results.jsonl';r=json.loads(index.read_text().splitlines()[0]);r['labels'][0]['raw_confidence']=.7
    lines=index.read_text().splitlines();lines[0]=json.dumps(r);index.write_text('\n'.join(lines)+'\n')
    with pytest.raises(ValueError,match='durable'):audit_silver_run(out/'lane-a',works)
