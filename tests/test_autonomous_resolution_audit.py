import copy
import pytest
from recommend_book.autonomous_resolution_audit import decision_issues, verify_cached_attempt


def sample():
    return {'case_id':'p','sides':[{'version_id':'a','evidence':[{'evidence_id':'a:id','field':'identifiers','text':'[{"scope":"work","source":"x","value":"1"}]'}]},{'version_id':'b','evidence':[{'evidence_id':'b:id','field':'identifiers','text':'[{"scope":"work","source":"x","value":"1"}]'}]}]}


def result():
    return {'case_id':'p','decision':'same','reason':'explicit work relation','citations':[{'evidence_id':'a:id','quote':'"value":"1"'},{'evidence_id':'b:id','quote':'"value":"1"'}],'contradictions':[],'checks':{'record_type':'consistent','author':'consistent','edition':'different'}}


def test_author_difference_cannot_assert_same():
    d=result();d['checks']['author']='different'
    assert 'same_with_different_author_check' in decision_issues(d,sample())


def test_different_editions_need_explicit_original_work_binding():
    assert decision_issues(result(),sample())==[]
    c=sample();c['sides'][1]['evidence'][0]['text']='[{"scope":"edition","source":"x","value":"1"}]'
    assert 'edition_difference_without_explicit_shared_work_identity' in decision_issues(result(),c)


def test_cached_result_requires_raw_binding(tmp_path):
    import json,hashlib
    from recommend_book.autonomous_resolution import prompt,canonical,digest
    c=sample();batch=[c];model='gpt-6.1-sol';version='codex-cli 0.162.1';key=digest(canonical({'lane':'A','cases':batch,'prior':None,'cli_version':version,'model':model}).encode());stem=tmp_path/('A-'+key)
    (tmp_path/'attempts').mkdir();(tmp_path/'lanes').mkdir();stem=tmp_path/'attempts'/stem.name
    def put(suffix,obj):(stem.with_name(stem.name+suffix)).write_text(json.dumps(obj),encoding='utf8')
    text=prompt(batch,'A');stem.with_name(stem.name+'.prompt.txt').write_text(text,encoding='utf8')
    put('.command.json',{'argv':['codex','exec','--model',model,'--sandbox','read-only','--ephemeral','--ignore-user-config','--skip-git-repo-check','--json','--color','never','-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.multi_agent=false','--cd',str(tmp_path/'workspace'),'--output-schema',str(tmp_path/'schema.json'),'-o',str(stem.with_name(stem.name+'.final.json')),'-'],'stdin_sha256':digest(text.encode()),'actor':'AI'})
    event={'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'decisions':[result()]})}}
    stdout=json.dumps(event)+'\n'+json.dumps({'type':'turn.completed','usage':{}})+'\n'
    stem.with_name(stem.name+'.stdout.jsonl').write_text(stdout,encoding='utf8');stem.with_name(stem.name+'.stderr.txt').write_text('',encoding='utf8')
    put('.observation.json',{'exit_code':0,'actor':'AI','process_exit_observed':True,'events':{'usage':{},'tool_calls':[],'event_errors':[]}});put('.final.json',{'decisions':[result()]})
    cache=tmp_path/'lanes'/('A-'+key+'.json');cache.write_text(json.dumps([result()]),encoding='utf8')
    verify_cached_attempt(tmp_path,'A',batch,None,model,version)
    cache.write_text(json.dumps([dict(result(),decision='different')]),encoding='utf8')
    with pytest.raises(ValueError):verify_cached_attempt(tmp_path,'A',batch,None,model,version)
    put('.final.json',{'decisions':[dict(result(),decision='different')]})
    with pytest.raises(ValueError,match='Last completed agent payload'):
        verify_cached_attempt(tmp_path,'A',batch,None,model,version)


def test_bounded_retry_preserves_every_failure_and_stops():
    from recommend_book.autonomous_resolution_audit import bounded_retry, RetryableAttemptError
    events=[];waits=[]
    def action(n):
        raise RetryableAttemptError('rate_limit','429')
    with pytest.raises(RetryableAttemptError):bounded_retry(action,events.append,sleep=waits.append)
    assert [x['attempt'] for x in events]==[1,2,3]
    assert waits==[1,2] and all(x['actor']=='AI' for x in events)


def test_validation_and_usage_limit_are_not_retried():
    from recommend_book.autonomous_resolution_audit import bounded_retry
    events=[]
    def action(n):raise ValueError('citation fabricated')
    with pytest.raises(ValueError):bounded_retry(action,events.append,sleep=lambda _:pytest.fail('Unexpected retry'))
    assert len(events)==1 and events[0]['retryable'] is False


def test_output_cannot_enter_source_or_run_tree(tmp_path):
    from recommend_book.autonomous_resolution_audit import validate_output_boundary
    run=tmp_path/'run';role=tmp_path/'roles'/'audit.json';source=tmp_path/'source'/'original.json'
    for target in (run/'new',role.parent/'new',source.parent/'new'):
        with pytest.raises(ValueError):validate_output_boundary(target,run,role,[source])
    assert validate_output_boundary(tmp_path/'evaluation'/'new',run,role,[source])==(tmp_path/'evaluation'/'new').resolve()


def test_stdout_last_agent_payload_and_unique_turn_must_bind_raw_final():
    from recommend_book.autonomous_resolution_audit import verify_stdout_payload
    import json
    payload={'decisions':[result()]}
    agent=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(payload)}})
    terminal=json.dumps({'type':'turn.completed','usage':{}})
    verify_stdout_payload(agent+'\n'+terminal,payload)
    with pytest.raises(ValueError):verify_stdout_payload(agent+'\n'+terminal,{'decisions':[]})
    with pytest.raises(ValueError):verify_stdout_payload(agent+'\n'+terminal+'\n'+terminal,payload)
    with pytest.raises(ValueError):verify_stdout_payload('{"type":"turn.completed","type":"x"}',payload)
    with pytest.raises(ValueError):verify_stdout_payload('{"type":"turn.completed","usage":NaN}',payload)


def test_terminal_summary_cannot_hide_calls_usage_or_adjudications():
    from recommend_book.autonomous_resolution_audit import verify_terminal_summary
    m={'decision_counts':{'different':1},'adjudicated':1,'cli_calls_this_execution':3,'usage_this_execution':{'input_tokens':8}}
    finals=[{'original_decision':'different'}]
    attempts=[{'lane':'A','decisions':[{}],'usage':{'input_tokens':3}}, {'lane':'B','decisions':[{}],'usage':{'input_tokens':3}}, {'lane':'C','decisions':[{}],'usage':{'input_tokens':2}}]
    verify_terminal_summary(m,finals,attempts)
    for field in ('adjudicated','cli_calls_this_execution','usage_this_execution'):
        broken=copy.deepcopy(m);broken[field]={} if field=='usage_this_execution' else 0
        with pytest.raises(ValueError):verify_terminal_summary(broken,finals,attempts)
