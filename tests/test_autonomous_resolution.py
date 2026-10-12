import copy
import pytest
from recommend_book.autonomous_resolution import blind_side, validate_decision, assemble_final, ndl_raw_fields


def case():
    return {'case_id':'p1','sides':[{'version_id':'a','evidence':[{'evidence_id':'a:title','text':'Title A'}]}, {'version_id':'b','evidence':[{'evidence_id':'b:title','text':'Title B'}]}]}


def decision():
    return {'case_id':'p1','decision':'different','citations':[{'evidence_id':'a:title','quote':'Title A'},{'evidence_id':'b:title','quote':'Title B'}], 'contradictions':[], 'checks':{'record_type':'missing','author':'missing','edition':'missing'},'reason':'Distinct titles, identity not established'}


def test_blind_side_removes_prediction_and_denied_text():
    s={'version_id':'a','record':{'titles':[{'value':'A'}],'extra':{'rule':'same'},'evidence':[{'evidence_id':'ok','text':'safe','llm_input_allowed':True},{'evidence_id':'denied','text':'SECRET','llm_input_allowed':False}]},'rule_proposal':{'status':'same'}}
    result=blind_side(s)
    assert 'SECRET' not in str(result) and 'rule' not in str(result)
    assert any(e['text']=='safe' for e in result['evidence'])


def test_quote_and_owner_rejection():
    d=decision();validate_decision(d,case())
    d['citations'][0]['quote']='invented'
    with pytest.raises(ValueError):validate_decision(d,case())
    d=decision();d['citations'][0]['evidence_id']='unknown'
    with pytest.raises(ValueError):validate_decision(d,case())


def test_same_needs_both_sides_and_unary_cannot_assert_match():
    d=decision();d['decision']='same';d['citations']=d['citations'][:1]
    with pytest.raises(ValueError):validate_decision(d,case())
    c=case();c['sides']=c['sides'][:1];d=decision();d['decision']='same';d['citations']=d['citations'][:1]
    with pytest.raises(ValueError):validate_decision(d,c)


def test_disagreement_needs_third_and_never_becomes_human():
    a=decision();b=copy.deepcopy(a);b['decision']='uncertain'
    with pytest.raises(ValueError):assemble_final(case(),a,b,None)
    result=assemble_final(case(),a,b,b)
    assert result['actor']=='AI' and result['human_gold'] is False
    assert result['authority_merge_approved'] is False


def test_raw_ndl_record_boundary_and_roles():
    raw=b'<root xmlns:d="http://purl.org/dc/terms/" xmlns:c="http://purl.org/dc/elements/1.1/" xmlns:n="http://ndl.go.jp/dcndl/terms/"><recordData><d:identifier>123</d:identifier><d:title>Right</d:title><n:alternative><value>Original</value></n:alternative><c:creator>Author</c:creator></recordData><recordData><d:identifier>456</d:identifier><d:title>Wrong</d:title></recordData></root>'
    x=ndl_raw_fields(raw,'R100000002-I123')
    assert 'Right' in str(x) and 'Wrong' not in str(x)
    with pytest.raises(ValueError):ndl_raw_fields(raw,'R100000002-I12')
