from copy import deepcopy
import math

import pytest

from recommend_book.recommendation.engine import rank_candidates


def row(work_id,features=None,*,rating=None,read_status='unknown',external_ids=None,popularity=None):
    return {'work_id':work_id,'title':work_id,'features':features or {},'rating':rating,
        'author_ids':[],'series_ids':[],'read_status':read_status,'external_ids':external_ids or [],
        'feature_provenance':{},'popularity':popularity}


def test_personal_equation_negative_preferences_and_does_not_mutate():
    references=[row('liked',{'mystery':1},rating=5),row('disliked',{'romance':1},rating=1)]
    candidates=[row('mystery',{'mystery':1}),row('romance',{'romance':1}),row('none')]
    original=deepcopy((candidates,references))
    result=rank_candidates(candidates,references)
    scores={r['work_id']:r['score'] for r in result['ranking']}
    assert scores['mystery']==pytest.approx(3+2/1.1)
    assert scores['romance']==pytest.approx(3-2/1.1)
    assert scores['none'] is None
    assert scores['romance']>0  # a lower preference adjusts the score, not eligibility
    assert result['ranking'][-1]['score_status']=='unsupported'
    assert (candidates,references)==original


def test_squared_cosine_prior_and_reference_mean():
    references=[row('like',{'x':1},rating=5),row('low',{'y':1},rating=1)]
    candidate=row('mixed',{'x':1,'y':2})
    ranked=rank_candidates([candidate],references)['ranking'][0]
    expected=3+(.2*2+.8*-2)/(1+.1)
    assert ranked['score']==pytest.approx(expected)
    assert sum(e['weight'] for e in ranked['explanations'])==pytest.approx(1)


def test_common_eligible_pool_and_identity_unread_scope():
    references=[row('reference',{'x':1},rating=5,external_ids=[{'source':'wikidata','value':'Q1'}])]
    history=[{'work_id':'historic','external_ids':[{'source':'narou','value':'n1234ab'}],'read_status':'read'}]
    candidates=[row('reference'),row('other-edition',external_ids=[{'source':'wikidata','value':'Q1'}]),
        row('historic'),row('history-alias',external_ids=[{'source':'narou','value':'n1234ab'}]),
        row('rated',rating=1),row('explicit-read',read_status='read'),
        row('unread',{'x':1},read_status='unread',popularity=3),row('unknown',{'x':1},popularity=2),
        row('same-value-different-source',external_ids=[{'source':'other','value':'Q1'}])]
    for method in ('personal','content','popularity'):
        result=rank_candidates(candidates,references,method=method,limit=99,read_history=history)
        assert set(result['audit']['eligible_work_ids'])=={'unread','unknown','same-value-different-source'}
        assert next(r for r in result['ranking'] if r['work_id']=='unknown')['read_status']=='unknown'
        assert len(result['audit']['excluded'])==6
    confirmed=rank_candidates(candidates,references,require_confirmed_unread=True,read_history=history)
    assert confirmed['audit']['eligible_work_ids']==['unread']
    assert confirmed['audit']['confirmed_unread_required'] is True


def test_worldwide_unknown_remains_unknown_without_history():
    result=rank_candidates([row('world',{'x':1})],[row('liked',{'x':1},rating=5)])
    assert result['ranking'][0]['read_status']=='unknown'
    assert result['audit']['read_history_supplied'] is False


def test_different_expression_same_work_id_is_excluded():
    reference=row('work',{'x':1},rating=5);reference['expression_id']='original'
    candidate=row('work',{'x':1});candidate['expression_id']='translation'
    assert rank_candidates([candidate],[reference])['ranking']==[]


def test_content_is_mean_positive_reference_cosine_and_popularity_observed_only():
    references=[row('positive-x',{'x':1},rating=5),row('positive-y',{'y':1},rating=4),row('low',{'x':1},rating=1)]
    candidates=[row('x',{'x':1},popularity=0),row('y',{'y':1}),row('empty')]
    content=rank_candidates(candidates,references,method='content')['ranking']
    assert [r['work_id'] for r in content]==['x','y','empty']
    assert content[0]['score']==content[1]['score']==pytest.approx(.5)
    popularity=rank_candidates(candidates,references,method='popularity')['ranking']
    assert popularity[0]['score']==0 and popularity[0]['score_status']=='supported'
    assert popularity[1]['score'] is None


def test_nearest_twenty_and_order_ties_deterministic():
    refs=[row(f'r{i:02}',{'x':1},rating=1 if i<20 else 5) for i in range(21)]
    candidates=[row('z',{'x':1}),row('a',{'x':1})]
    result=rank_candidates(candidates,refs)['ranking']
    assert [r['work_id'] for r in result]==['a','z']
    assert len(result[0]['explanations'])==20
    assert {e['reference_work_id'] for e in result[0]['explanations']}=={f'r{i:02}' for i in range(20)}
    assert result==rank_candidates(list(reversed(candidates)),list(reversed(refs)))['ranking']


def test_heldout_rating_is_not_available_to_score_and_known_rating_excludes():
    candidate=row('held',{'x':1});candidate['heldout_rating']=1
    references=[row('train',{'x':1},rating=5)]
    score=rank_candidates([candidate],references)['ranking'][0]['score']
    candidate['heldout_rating']=5
    assert rank_candidates([candidate],references)['ranking'][0]['score']==score
    candidate['rating']=5
    assert rank_candidates([candidate],references)['ranking']==[]


def test_provenance_is_preserved_and_explanations_count_actual_records():
    reference=row('train',{'x':1},rating=5);reference['feature_provenance']={'x':[{'source_record_id':'src1','evidence_id':'e1'}]}
    candidate=row('candidate',{'x':1});candidate['feature_provenance']={'x':[{'source_record_id':'src2','evidence_id':'e2'}]}
    ranked=rank_candidates([candidate],[reference])['ranking'][0]
    assert ranked['evidence_count']==2
    assert ranked['feature_provenance']==candidate['feature_provenance']
    assert ranked['explanations'][0]['features'][0]['candidate_provenance']==candidate['feature_provenance']['x']


@pytest.mark.parametrize('field,value',[('rating',True),('rating',6),('rating',math.nan),('popularity',math.inf),('features',{'x':-1}),('features',{'x':True}),('features',{'x':math.nan}),('read_status','maybe')])
def test_invalid_input_is_rejected(field,value):
    candidate=row('candidate');candidate[field]=value
    with pytest.raises(ValueError):rank_candidates([candidate],[])


@pytest.mark.parametrize('where',['candidates','references','history'])
def test_duplicate_work_ids_rejected(where):
    kwargs={'candidates':[row('candidate')],'references':[],'read_history':None}
    kwargs[{'history':'read_history'}.get(where,where)]=[row('duplicate'),row('duplicate')]
    with pytest.raises(ValueError,match='duplicate'):rank_candidates(**kwargs)


@pytest.mark.parametrize('kwargs',[{'method':'invented'},{'method':[]},{'limit':True},{'limit':-1},{'require_confirmed_unread':1}])
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(ValueError):rank_candidates([],[],**kwargs)


def test_extreme_finite_features_do_not_overflow_or_fabricate_missing_score():
    ranked=rank_candidates([row('c',{'x':1e308,'y':1e308})],[row('r',{'x':1e308,'y':1e308},rating=4)])['ranking'][0]
    assert math.isfinite(ranked['score']) and ranked['score']==pytest.approx(4)
    assert rank_candidates([row('empty')],[row('r',{'x':1},rating=5)])['ranking'][0]['score'] is None


def test_huge_integer_feature_is_rejected_without_overflow():
    with pytest.raises(ValueError):rank_candidates([row('c',{'x':10**400})],[])


def test_zero_similarity_and_absent_positive_reference_are_distinct_from_missing_features():
    candidate=row('c',{'x':1});reference=row('r',{'y':1},rating=4)
    assert rank_candidates([candidate],[reference])['ranking'][0]['score'] is None
    assert rank_candidates([candidate],[reference],method='content')['ranking'][0]['score']==0
    reference['rating']=1
    assert rank_candidates([candidate],[reference],method='content')['ranking'][0]['score'] is None


def test_limit_zero_retains_full_eligible_pool_audit_without_ranked_rows():
    result=rank_candidates([row('c')],[],limit=0)
    assert result['ranking']==[]
    assert result['audit']['eligible_work_ids']==['c'] and result['audit']['unsupported_count']==1


def test_history_unknown_does_not_exclude_and_unread_confirms_strict_pool():
    candidates=[row('unknown'),row('unread')]
    history=[{'work_id':'unknown','read_status':'unknown'},{'work_id':'unread','read_status':'unread'}]
    ranked=rank_candidates(candidates,[],read_history=history)['ranking']
    assert {r['work_id']:r['read_status'] for r in ranked}=={'unknown':'unknown','unread':'unread'}
    assert rank_candidates(candidates,[],read_history=history,require_confirmed_unread=True)['audit']['eligible_work_ids']==['unread']


@pytest.mark.parametrize('status',['read','unread','unknown'])
def test_external_only_history_identity_respects_status(status):
    external=[{'source':'narou','value':'n1111aa'}]
    candidate=row('wd:Q1',external_ids=external)
    history=[{'external_ids':external,'read_status':status}]
    result=rank_candidates([candidate],[],read_history=history)
    assert bool(result['ranking'])==(status!='read')
    strict=rank_candidates([candidate],[],read_history=history,require_confirmed_unread=True)
    assert bool(strict['ranking'])==(status=='unread')


def test_matching_history_conflicts_use_read_or_known_rating_first_and_unknown_conservatively():
    external=[{'source':'narou','value':'n1111aa'}]
    candidate=row('candidate',external_ids=external)
    unread={'work_id':'alias-unread','external_ids':external,'read_status':'unread'}
    unknown={'work_id':'alias-unknown','external_ids':external,'read_status':'unknown'}
    result=rank_candidates([candidate],[],read_history=[unread,unknown])
    assert result['ranking'][0]['read_status']=='unknown'
    assert not rank_candidates([candidate],[],read_history=[unread,unknown],require_confirmed_unread=True)['ranking']
    read={'external_ids':external,'read_status':'read'}
    assert not rank_candidates([candidate],[],read_history=[unread,unknown,read])['ranking']
    rated={'external_ids':external,'read_status':'unknown','rating':1}
    assert not rank_candidates([candidate],[],read_history=[unread,rated])['ranking']


def test_history_without_any_explicit_identity_is_rejected():
    with pytest.raises(ValueError,match='identity'):rank_candidates([],[],read_history=[{'read_status':'read'}])
