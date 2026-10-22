"""A timed-out window remains excluded while fresh sources continue at index 76."""
import importlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    return importlib.import_module('run_scale100k_native_v6')


def facts():
    return {'version':'scale100k-review-timeout-diagnostic-v1',
            'failed_global_index':76,'prior_global_index':75,'selected_works':100,
            'native_completed':99,'native_failed':1,'mechanically_unreviewed_works':5,
            'review_whole_holds':0,'conservative_failure_count':6,
            'conservative_failure_rate':.06,'unchanged_max_failure_rate':.05,
            'failed_window_accepted':False,'failed_window_sql_rows':0,
            'eligible_cumulative':8369,'model_calls_added':0,'original_artifacts_modified':False,
            'driver_actual_exit_code':None,
            'groups':[{'group':i,'actual_exit_code':1 if i==14 else 0,
                       'timeout':i==14,'quality_adoption_review_saved':i!=14,
                       'expected_work_ids':[f'w{i}-{j}' for j in range(4 if i==20 else 5)]}
                      for i in range(1,21)]}


def test_exact_timeout_facts_accept_no_invented_parent_exit(monkeypatch):
    module(monkeypatch).validate_failure_facts(facts())


@pytest.mark.parametrize('field,value',[
    ('native_failed',True),('native_failed',0),('failed_global_index',77),
    ('conservative_failure_rate',.05),('failed_window_accepted',True),
    ('failed_window_sql_rows',1),('driver_actual_exit_code',1),
])
def test_other_failure_or_fabricated_exit_is_rejected(monkeypatch,field,value):
    d=facts();d[field]=value
    with pytest.raises(ValueError):module(monkeypatch).validate_failure_facts(d)


def test_fresh_entry_executes_real_driver_preflight_with_four_slots(tmp_path,monkeypatch):
    m=module(monkeypatch);monkeypatch.setattr(m,'BASE',tmp_path)
    source=tmp_path/'classification-sources-v9'/'window';source.mkdir(parents=True)
    name='scale100k-content-3401-3500-v9'
    (source/'manifest.json').write_text(json.dumps({'selected':100,'chunks':[
        {'run_name':name,'inputs_path':'fresh-input','usage_ledger_path':'fresh-ledger'}]}))
    override=tmp_path/'map.json';override.write_text(json.dumps({'76':str(source)}))
    scope=m.load_operations(override,m.state.sha(override))
    scope.BASE=tmp_path;scope.RUNTIME=tmp_path/'runtime'
    prior_run=tmp_path/'prior.json';prior_run.write_text(json.dumps(
        {'failed':0,'pending':0,'initial_concurrency':8,'final_concurrency':8}))
    prior={'window_index':75,'control_artifacts':{'run':{'path':str(prior_run)}}}
    import recommend_book.scale_content_control_v4 as controls
    monkeypatch.setattr(controls,'verify_control_receipt',lambda *a:prior)
    monkeypatch.setattr(m.state,'verify_requirements',lambda:None)
    monkeypatch.setattr(m.state,'load_exclusion_index',lambda *a:None)
    monkeypatch.setattr(m.state,'eligible_count',lambda:8369)
    reservations=[]
    def reserve(p,i,*args):
        reservations.append((p,i));f=tmp_path/'reservation';f.write_text('{}');return f
    monkeypatch.setattr(m.state,'reserve_window',reserve)
    scope.load_operations=lambda:SimpleNamespace()
    scope.verify_coordinator=lambda:None
    scope.emit=lambda x:None
    calls=[]
    class Boundary(Exception):pass
    def observed(argv,directory):calls.append(list(map(str,argv)));raise Boundary
    scope.observed_command=observed
    with pytest.raises(Boundary):scope.run(tmp_path/'index','a'*64,tmp_path/'control','b'*64,4)
    assert reservations==[(source.resolve(),76)]
    assert calls[0][1]=='scripts/prepare_scale100k_content_chunk_v2.py'
    assert calls[0][calls[0].index('--concurrency')+1]=='4'
    assert name in calls[0] and 'fresh-input' in calls[0]


def test_changed_map_is_rejected_before_reservation(tmp_path,monkeypatch):
    m=module(monkeypatch);p=tmp_path/'map';p.write_text('{}');digest=m.state.sha(p)
    p.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='map'):m.select_source(76,tmp_path,p,digest)


def test_coordinator_pins_new_paths_without_changing_review_recipe(monkeypatch):
    module(monkeypatch)
    c=importlib.import_module('run_scale100k_content_classification_v6')
    source=c.adapted_source((ROOT/'scripts/run_scale100k_content_classification_v5.py').read_bytes())
    assert 'from review_scale_content_v6 import execute;' in source
    assert 'run_scale100k_native_v6.py' in source
    assert 'prepare_scale100k_timeout_recovery_sources_v1.py' in source
    assert 'max_review_concurrency=int(sys.argv[11])' in source
    assert 'selection_path, manifest["final_concurrency"]]' in source
    with pytest.raises(ValueError):c.adapted_source(b'changed')


@pytest.mark.parametrize('slots',[4,8])
def test_actual_coordinator6_reaches_review6_model_boundary(tmp_path,monkeypatch,slots):
    module(monkeypatch)
    coordinator=importlib.import_module('run_scale100k_content_classification_v6')
    source=coordinator.adapted_source((ROOT/'scripts/run_scale100k_content_classification_v5.py').read_bytes())
    operation=ModuleType('coordinator6_entry_operation')
    operation.__file__=str(ROOT/'scripts/run_scale100k_content_classification_v6.py')
    operation.REVIEW_SHA=coordinator.REVIEW_SHA
    exec(compile(source,operation.__file__,'exec'),operation.__dict__)
    operation.ROOT=tmp_path
    (tmp_path/'scripts').mkdir()
    (tmp_path/'scripts/review_scale_content_v6.py').write_bytes((ROOT/'scripts/review_scale_content_v6.py').read_bytes())
    run=tmp_path/'run';run.mkdir()
    (run/'manifest.json').write_text(json.dumps({'usage':{'input_tokens':1,'output_tokens':1},
        'elapsed_seconds':1,'stop_reasons':[],'completed':1,'final_concurrency':slots}))
    observed={'run_dir':run}
    for key in ['ordered_inputs','execution_context_manifest','terminal_proof','usage_ledger']:
        observed[key]=tmp_path/key;observed[key].write_bytes(b'fixture')
    review=importlib.import_module('review_scale_content_v6')
    monkeypatch.setattr(review,'preview_prompt_chars',lambda *args:10)
    class ModelBoundary(Exception):pass
    def stop(*args,**kwargs):raise ModelBoundary
    monkeypatch.setattr(review,'load_bounded_review',stop)
    calls=[]
    def frozen_call(runtime,code,args):
        if 'select_review_work_ids' in code:return ['selected']
        calls.append(code);monkeypatch.setattr(sys,'argv',['fixture',*map(str,args)])
        exec(compile(code,'<actual-coordinator6-review-boundary>','exec'),{})
    operation.frozen_call=frozen_call
    with pytest.raises(ModelBoundary):operation.review_content_window(tmp_path/'runtime',observed,'run',full=True)
    assert len(calls)==1
    plan=json.loads((tmp_path/'data/autonomous-next-v3/scale-100000-v1/separate-review-sharded-v6/run/review-plan.json').read_bytes())
    assert plan['max_group_works']==5 and plan['max_concurrent_review_cli']==slots
