"""Reuse a complete native terminal, without rerunning classification."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_future_coordinator_uses_five_work_review_and_actual_native_parallelism():
    c=module('run_scale100k_content_classification_v3')
    source=c.adapted_source((ROOT/'scripts/run_scale100k_content_classification_v2.py').read_bytes())
    assert 'from review_scale_content_v4 import execute;' in source
    assert 'from review_scale_content_v3 import execute;' not in source
    assert 'max_review_concurrency=int(sys.argv[11])' in source
    assert 'selection_path, manifest["final_concurrency"]]' in source
    assert 'separate-review-sharded-v4' in source
    assert 'scripts/review_scale_content_v4.py' in source and 'scripts/run_scale100k_native_v3.py' in source
    assert source.count('10000000')==3 and 'remaining_seconds = 3600' in source


def test_resume_recipe_cannot_launch_native_and_keeps_original_coordinator_proof():
    tail=module('resume_scale100k_adoption_v1')
    scope=tail.load_tail_recipe()
    names=scope.resume_adoption.__code__.co_names
    assert 'execute_window' not in names and 'terminal' not in names
    assert 'review_content_window' in names and 'coordinator_chain_pins' in names
    assert scope.ORIGINAL_COORDINATOR_SHA==tail.ORIGINAL_COORDINATOR_SHA
    assert scope.REVIEW_SHA=='197191358eb71a2c4cf9e9d52145e59eccb5bcbf11a0040330cb082f1db43410'


def test_complete_tail_executes_review_then_adoption_with_original_native_chain(tmp_path,monkeypatch):
    tail=module('resume_scale100k_adoption_v1');scope=tail.load_tail_recipe()
    monkeypatch.setattr(scope,'ROOT',tmp_path)
    monkeypatch.setattr(scope,'sha',lambda path:'f'*64)
    run=tmp_path/'run';run.mkdir()
    source=tmp_path/'source';source.mkdir()
    (source/'manifest.json').write_text(json.dumps({'chunks':[{'run_name':'new-run'}]}),encoding='utf-8')
    (run/'manifest.json').write_text(json.dumps({'selected':100,'completed':100,'failed':0,'pending':0,
        'usage':{},'elapsed_seconds':100,'cli_invocations':100}),encoding='utf-8')
    review=tmp_path/'review.json';review.write_text(json.dumps({'source_audits':[]}),encoding='utf-8')
    events=[]
    def reviewed(*args,**kwargs):
        assert kwargs['full'] is True;events.append('review')
        return {'review_path':str(review),'elapsed_seconds':10,'observed_usage':{}}
    def frozen(*args):
        events.append('adoption' if len(events)==1 else 'projection')
        return {'imported':100} if len(events)==2 else {'actual_feature_adopted_works':90,'artifact_sha256':{}}
    def chain(doc):
        assert doc['coordinator_sha256']==tail.ORIGINAL_COORDINATOR_SHA
        events.append('native_chain');return []
    monkeypatch.setattr(scope,'review_content_window',reviewed)
    monkeypatch.setattr(scope,'frozen_call',frozen)
    monkeypatch.setattr(scope,'coordinator_chain_pins',chain)
    observed={key:str(tmp_path/key) for key in ('source_database','ordered_inputs','execution_context_manifest',
        'migration_manifest','terminal_proof','cli_profile','usage_ledger')}
    observed['run_dir']=str(run)
    result=scope.resume_adoption(tmp_path/'source',tmp_path/'runtime',42,tmp_path/'prior-control','f'*64,
        observed,{'adapter_path':str(tmp_path/'adapter')},tmp_path/'preflight',tmp_path/'validation',[],{})
    assert result['gate']['passed'] is True
    document=json.loads(Path(result['export_manifest']).read_bytes())
    assert document['coordinator_sha256']==tail.ORIGINAL_COORDINATOR_SHA
    assert str(Path(tail.__file__).resolve()) in document['artifact_sha256']
    assert events==['review','adoption','projection','native_chain']


def native_fixture(tmp_path,monkeypatch):
    tail=module('resume_scale100k_adoption_v1');s=tail.state
    base=tmp_path/'v19';runtime=tmp_path/'runtime';source=base/'source';coor=tmp_path/'coor2.py'
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value),encoding='utf-8')
    source.mkdir(parents=True);write(source/'manifest.json',{'actual':'source'});coor.write_text('fixed')
    monkeypatch.setattr(tail,'BASE',base);monkeypatch.setattr(tail,'RUNTIME',runtime)
    monkeypatch.setattr(tail,'SOURCE',source);monkeypatch.setattr(tail,'SOURCE_SHA',s.sha(source/'manifest.json'))
    monkeypatch.setattr(tail,'ORIGINAL_COORDINATOR',coor);monkeypatch.setattr(tail,'ORIGINAL_COORDINATOR_SHA',s.sha(coor))
    monkeypatch.setattr(s,'verify_requirements',lambda:None)
    monkeypatch.setattr(s,'load_exclusion_index',lambda *args:{})
    inputs=tmp_path/'inputs.jsonl';inputs.write_text('fixed input')
    fixed={'source_database':'actual-db','chunks':[{'inputs_sha256':s.sha(inputs)}]}
    monkeypatch.setattr(s,'_current_source',lambda *args:(fixed,{'hash'},{'Q1'}))
    write(base/'attempt-reservations'/(tail.NAME+'.json'),{'source_sha256':tail.SOURCE_SHA,'source_path':str(source),
        'input_hashes':['hash'],'qids':['Q1'],'global_index':42,'actual_overlap_count':0,'index_path':'index','index_sha256':'fixed'})
    hold=base/'separate-review-sharded-v1'/(tail.NAME+'.selection.json')
    write(hold,{'stage':'operator_hold_not_review_evidence'});monkeypatch.setattr(tail,'HOLD_SHA',s.sha(hold))
    op=base/'operational-processes'/tail.NAME/'native-and-review'
    write(op/'process-exit.json',{'actual_exit_code':1,'process_exit_observed':True})
    (op/'stderr.txt').write_text('FileExistsError: '+str(FileExistsError(17,'File exists',str(hold))),encoding='utf-8')
    terminal=runtime/('terminal-'+tail.NAME);run=runtime/'run'
    write(terminal/'adoption-arguments.json',{'run_dir':str(run),'exit_code':0,'source_database':'actual-db','ordered_inputs':str(inputs)})
    write(terminal/'process-exit.json',{'actual_exit_code':0,'process_exit_observed':True})
    write(terminal/'request.json',{'coordinator_path':str(coor),'coordinator_sha256':tail.ORIGINAL_COORDINATOR_SHA})
    write(terminal/'terminal-proof.json',{'proof':'checked-by-existing-validator'})
    write(run/'manifest.json',{'selected':100,'completed':100,'failed':0,'pending':0,'stopped':False,'stop_reasons':[]})
    monkeypatch.setattr(tail,'coordinator_chain_pins',lambda value:[])
    return tail,write,op,terminal,run


@pytest.mark.parametrize('problem',['partial','stopped','native_exit','bool_exit','wrong_stop','wrong_hold','native_identity'])
def test_resume_rejects_any_other_terminal_before_review(tmp_path,monkeypatch,problem):
    tail,write,op,terminal,run=native_fixture(tmp_path,monkeypatch)
    assert tail.validate_finished_native()[0]['exit_code']==0
    if problem in ('partial','stopped'):
        d=tail.state.read(run/'manifest.json');d.update({'completed':76,'pending':24} if problem=='partial' else {'stopped':True,'stop_reasons':['token_budget']});write(run/'manifest.json',d)
    elif problem in ('native_exit','bool_exit'):
        write(terminal/'process-exit.json',{'actual_exit_code':1 if problem=='native_exit' else False,'process_exit_observed':True})
    elif problem=='wrong_stop':
        write(op/'process-exit.json',{'actual_exit_code':True,'process_exit_observed':True})
    elif problem=='wrong_hold':
        (op/'stderr.txt').write_text('other FileExistsError',encoding='utf-8')
    else:
        write(terminal/'request.json',{'coordinator_path':'different','coordinator_sha256':'different'})
    with pytest.raises(ValueError):tail.validate_finished_native()


def test_future_driver_requires_successful_actual_first_control_and_starts_cohort1():
    driver=module('run_scale100k_native_v3')
    source=driver.adapted_source((ROOT/'scripts/run_scale100k_native_v2.py').read_bytes())
    assert "prior['window_index'] != 42" in source and 'cohort = 1\n' in source
    assert 'previous_control, previous_sha = Path(completed_control), completed_control_sha' in source
    assert 'scripts/run_scale100k_content_classification_v3.py' in source
    assert 'concurrency = 8 if cohort and clean' in source
    assert 'verify_control_receipt(previous_control, previous_sha)' in source


@pytest.mark.parametrize('name',['run_scale100k_content_classification_v3','run_scale100k_native_v3'])
def test_versioned_recipe_sha_change_rejected(name):
    adapter=module(name)
    with pytest.raises(ValueError,match='changed'):adapter.adapted_source(b'changed')
