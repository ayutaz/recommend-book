"""Resume only post-native work after the exact pre-model review-code failure."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def module(name='resume_scale100k_adoption_v2'):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def facts():
    return {'native':{'selected':100,'completed':100,'failed':0,'pending':0,'stopped':False,'stop_reasons':[]},
            'exited':{'process_pid':168352,'actual_exit_code':0,'process_exit_observed':True},
            'coordinator':{'pid':46384,'actual_exit_code':1,'process_exit_observed':True},
            'stop':{'session_id':88143,'actual_exit_code':1,'process_exit_observed':True,'tool_result':{'exit_code':1}},
            'selection':{'full':True,'selected_work_ids':[f'w{i}' for i in range(100)],'completed_native_count':100},
            'error':"review_scale_content_v5.py NameError: name 'review_concurrency' is not defined",
            'review_output_exists':False}


def test_exact_pre_model_failure_retains_real_selection_and_accepts_no_native_retry():
    assert module().validate_failure_facts(**facts()) is None


@pytest.mark.parametrize('problem',['partial','native_bool_exit','coordinator_bool_exit','coordinator_success',
                                  'stop_bool_exit','wrong_native_pid','old_review_output','missing_selection',
                                  'different_error'])
def test_other_failures_or_any_started_review_are_rejected(problem):
    d=facts()
    if problem=='partial':d['native']['completed']=99
    elif problem=='native_bool_exit':d['exited']['actual_exit_code']=False
    elif problem=='coordinator_bool_exit':d['coordinator']['actual_exit_code']=True
    elif problem=='coordinator_success':d['coordinator']['actual_exit_code']=0
    elif problem=='stop_bool_exit':d['stop']['tool_result']['exit_code']=True
    elif problem=='wrong_native_pid':d['exited']['process_pid']=1
    elif problem=='old_review_output':d['review_output_exists']=True
    elif problem=='missing_selection':d['selection']['selected_work_ids'].pop()
    elif problem=='different_error':d['error']='Another NameError'
    with pytest.raises(ValueError):module().validate_failure_facts(**d)


def test_tail_recipe_has_no_native_runner_and_keeps_original_coordinator_binding():
    m=module();scope=m.load_tail_recipe()
    assert 'execute_window' not in scope.resume_adoption.__code__.co_names
    assert 'review_content_window' in scope.resume_adoption.__code__.co_names
    assert scope.ORIGINAL_COORDINATOR_SHA==m.ORIGINAL_COORDINATOR_SHA
    assert scope.REVIEW_SHA==m.REVIEW_SHA
    constants=str(scope.review_content_window.__code__.co_consts)
    assert 'review_scale_content_v6' in constants
    assert 'separate-review-sharded-v6' in constants


def test_review_call_uses_new_namespace_and_sixth_review_module():
    m=module();source=m.tail_recipe_source()
    assert 'run_scale100k_content_classification_v5.py' in source
    assert 'ORIGINAL_COORDINATOR_SHA' in source


def test_pinned_old_tail_recipe_rejects_changed_bytes():
    with pytest.raises(ValueError,match='Frozen'):module().tail_recipe_source(b'changed')


def test_saved_tail_reviews_and_adopts_without_invoking_native(tmp_path,monkeypatch):
    m=module();scope=m.load_tail_recipe();events=[]
    monkeypatch.setattr(scope,'ROOT',tmp_path)
    monkeypatch.setattr(scope,'sha',lambda path:'f'*64)
    run=tmp_path/'run';run.mkdir()
    (run/'manifest.json').write_text(json.dumps({'selected':100,'completed':100,'failed':0,'pending':0,
        'usage':{},'elapsed_seconds':1451,'cli_invocations':100}))
    source=tmp_path/'source';source.mkdir()
    (source/'manifest.json').write_text(json.dumps({'chunks':[{'run_name':'saved-native'}]}))
    review=tmp_path/'review.json';review.write_text(json.dumps({'source_audits':[]}))
    def reviewed(*args,**kwargs):
        assert kwargs['full'] is True;events.append('review')
        return {'review_path':str(review),'elapsed_seconds':10,'observed_usage':{}}
    def frozen(*args):
        if len(events)==1:events.append('adoption');return {'imported':100}
        events.append('projection');return {'actual_feature_adopted_works':90,'artifact_sha256':{}}
    def chain(document):
        assert document['coordinator_sha256']==m.ORIGINAL_COORDINATOR_SHA
        events.append('original_native_chain');return []
    monkeypatch.setattr(scope,'review_content_window',reviewed)
    monkeypatch.setattr(scope,'frozen_call',frozen)
    monkeypatch.setattr(scope,'coordinator_chain_pins',chain)
    observed={k:str(tmp_path/k) for k in ('source_database','ordered_inputs','execution_context_manifest',
        'migration_manifest','terminal_proof','cli_profile','usage_ledger')}
    observed['run_dir']=str(run)
    result=scope.resume_adoption(source,tmp_path/'runtime',49,tmp_path/'control48','f'*64,observed,
        {'adapter_path':str(tmp_path/'adapter')},tmp_path/'preflight',tmp_path/'validation',[],{})
    assert result['gate']['passed'] is True
    exported=json.loads(Path(result['export_manifest']).read_bytes())
    assert exported['coordinator_sha256']==m.ORIGINAL_COORDINATOR_SHA
    assert str(Path(m.__file__).resolve()) in exported['artifact_sha256']
    assert events==['review','adoption','projection','original_native_chain']


def test_future_driver_starts_at_real_success49_without_reusing_old_entry_start():
    m=module('run_scale100k_native_v5')
    source=m.adapted_source((ROOT/'scripts/run_scale100k_native_v4.py').read_bytes())
    assert "prior['window_index'] != 49" in source
    assert "cohort = prior['window_index'] - 41" in source
    assert 'window-source-overrides-v3.json' in source
    assert 'scripts/run_scale100k_content_classification_v5.py' in source
    assert 'assert_first_source(source)' not in source
    assert 'first_concurrency == 8' in source
    assert 'generation-v7-continuation-entry.json' in source
