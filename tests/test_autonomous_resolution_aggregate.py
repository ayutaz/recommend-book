import pytest
from recommend_book.autonomous_resolution_aggregate import merge_proposals,verify_recovery_summary


def test_full_coverage_preserves_invalid_hold_without_a_synthetic_decision():
    cases=[{'case_id':'a'},{'case_id':'b'}]
    old=[{'case_id':'a','audited_decision':'different','status':'completed'}]
    new=[{'case_id':'b','audited_decision':None,'status':'held','issues':['invalid_semantic']}]
    assert merge_proposals(cases,old,new)==old+new
    with pytest.raises(ValueError):merge_proposals(cases,old,[])
    with pytest.raises(ValueError):merge_proposals(cases,old,old+new)


def test_terminal_counts_and_usage_cannot_claim_missing_attempts_as_zero():
    attempts=[{'observation':{'attempt':1,'events':{'usage':{'input_tokens':3}}}}]
    finals=[{'status':'held','lane_c':None}]
    terminal={'processed':1,'cli_attempts':1,'retry_attempts':0,'held':1,'adjudicated':0,'usage':{'input_tokens':3}}
    verify_recovery_summary(terminal,finals,attempts)
    for field in ('processed','cli_attempts','held','usage'):
        broken=dict(terminal);broken[field]={} if field=='usage' else 0
        with pytest.raises(ValueError):verify_recovery_summary(broken,finals,attempts)
