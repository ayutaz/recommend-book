import pytest
from recommend_book.autonomous_resolution_recovery import pending_subset,write_once,validate_batch,failure_category


def test_archived_recovery_refuses_before_cli_discovery_or_output(tmp_path,monkeypatch):
    import recommend_book.autonomous_resolution_recovery as module
    calls=[]
    monkeypatch.setattr(module,'resolve_executable',lambda: calls.append('discovery'))
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k: calls.append('subprocess'))
    output=tmp_path/'fresh'
    with pytest.raises(RuntimeError,match='Archived runner: no fresh calls; guarded path only'):
        module.recover(tmp_path/'missing-original',output,tmp_path/'missing-role',{})
    assert calls==[] and not output.exists()


def test_pending_subset_preserves_cases_and_excludes_only_finalized():
    cases=[{'case_id':'a','sides':[]},{'case_id':'b','sides':[]}]
    assert pending_subset(cases,{'a'})==[cases[1]]
    with pytest.raises(ValueError):pending_subset(cases,{'absent'})
    with pytest.raises(ValueError):pending_subset(cases+cases,{'a'})


def test_append_only_raw_refuses_overwrite(tmp_path):
    path=tmp_path/'raw';write_once(path,b'first')
    with pytest.raises(FileExistsError):write_once(path,b'changed')
    assert path.read_bytes()==b'first'


def test_invalid_semantic_is_held_without_fabricating_decision():
    case={'case_id':'a','sides':[{'evidence':[{'evidence_id':'a:t','text':'title'}]},{'evidence':[{'evidence_id':'b:t','text':'title'}]}]}
    result={'case_id':'a','decision':'same','reason':'same','checks':dict(record_type='consistent',author='different',edition='consistent'), 'citations':[{'evidence_id':'a:t','quote':'title'},{'evidence_id':'b:t','quote':'title'}],'contradictions':[]}
    statuses=validate_batch({'decisions':[result]},[case])
    assert statuses[0]['status']=='held' and statuses[0]['decision'] is None
    assert statuses[0]['raw_decision']==result


def test_retry_categories_exclude_auth_quota_tool_and_semantic():
    for message in ('authentication 401','quota exceeded','tool_use','invalid_semantic'):
        category,retryable=failure_category(message)
        assert not retryable
    assert failure_category('429 rate limit')[1]
    assert failure_category('timeout')[1]
