"""Synthetic recommendation ratings: structural tests, never private user gold."""
from copy import deepcopy
import math

import pytest

from recommend_book.recommendation import evaluation


def row(work,rating,**updates):
    return {'work_id':work,'title':work,'features':{'theme.fixture':1.},'rating':rating,
        'author_ids':[],'series_ids':[],'author_group_keys':[],'read_status':'unknown',
        'external_ids':[],'feature_provenance':{'theme.fixture':[{'source':'synthetic_fixture'}]},'popularity':None,**updates}


def fake_engine(candidates,references,*,method,limit,read_history=None,require_confirmed_unread=False):
    assert not require_confirmed_unread
    ranking=[]
    for item in sorted(candidates,key=lambda item:item['work_id']):
        supported=method=='personal' and bool(item['features'])
        ranking.append({**item,'score':1. if supported else None,
            'score_status':'supported' if supported else 'unsupported','evidence_count':int(supported),'explanations':[]})
    return {'ranking':ranking[:limit],'audit':{'eligible_work_ids':sorted(item['work_id'] for item in candidates),'excluded':[]}}


def test_rank_metrics_use_graded_gain_and_explicit_positive_denominators():
    metric=evaluation._ranking_metrics(['a','b','c'],{'a':5,'b':1,'c':4},2)
    assert metric['ndcg_at_k']==pytest.approx((31+1/math.log2(3))/(31+15/math.log2(3)))
    assert metric['precision_at_k']==.5 and metric['recall_at_k']==.5
    assert metric['ap_at_k']==.5 and metric['positive_n']==2
    none_positive=evaluation._ranking_metrics(['a','b'],{'a':1,'b':2},10)
    assert none_positive['precision_at_k']==0
    assert none_positive['recall_at_k'] is None and none_positive['ap_at_k'] is None
    assert none_positive['effective_k']==2


def test_train_only_references_scrubbed_holdout_and_same_pool(monkeypatch):
    calls=[]
    def spy(candidates,references,**options):
        calls.append((deepcopy(candidates),deepcopy(references),deepcopy(options)))
        return fake_engine(candidates,references,**options)
    monkeypatch.setattr(evaluation,'rank_candidates',spy)
    items=[row(f'w{i}',[5,1,4,2,5,1][i],read_status='read') for i in range(6)]+[row('unrated',None)]
    before=deepcopy(items)
    report=evaluation.evaluate_recommendations(items,folds=3,seed=12,k=2)
    assert items==before and len(calls)==6
    for fold in report['folds']:
        expected_train=set(report['split']['folds'][fold['fold']]['train_rated_work_ids'])
        pair=calls[2*fold['fold']:2*fold['fold']+2]
        assert pair[0][0]==pair[1][0]
        for candidates,references,options in pair:
            assert {item['work_id'] for item in references}==expected_train
            assert all(item['rating'] is not None for item in references)
            assert all(item['rating'] is None and item['read_status']=='unknown' for item in candidates)
            assert not ({item['work_id'] for item in candidates}&expected_train)
            assert {item['work_id'] for item in (options['read_history'] or [])}<=expected_train
            assert 'unrated' not in {item['work_id'] for item in candidates}
        pool=set(fold['eligible_work_ids'])
        assert all({item['work_id'] for item in result['ranking']}==pool for result in fold['methods'].values())
        assert fold['methods']['popularity']['coverage']['supported_candidate_n']==0
    assert report['counts']['unrated_work_n']==1
    assert report['design']['temporal'] is False and report['design']['prospective'] is False


def test_transitive_groups_cover_unrated_bridges_names_aliases_and_missing_ids(monkeypatch):
    monkeypatch.setattr(evaluation,'rank_candidates',fake_engine)
    items=[row('a',5,author_ids=['author-A']),
        row('bridge',None,author_ids=['author-A'],series_ids=['series-S']),
        row('b',1,series_ids=['series-S'],author_group_keys=['name-key']),
        row('c',4,author_group_keys=['name-key'],external_ids=[{'source':'fixture','value':'alias'}]),
        row('d',2,external_ids=[{'source':'fixture','value':'alias'}]),row('x',5),row('y',1)]
    report=evaluation.evaluate_recommendations(items,folds=3)
    groups=report['split']['group_by_work']
    assert len({groups[work] for work in ('a','bridge','b','c','d')})==1
    assert groups['x']!=groups['y'] and groups['x']!=groups['a']
    assert report['identity_audit']['name_blocking_used'] is True
    assert report['identity_audit']['unrecorded_series_identity_n']==5
    for fold in report['folds']:
        assert fold['author_overlap_n']==fold['series_overlap_n']==fold['author_name_key_overlap_n']==fold['external_identity_overlap_n']==0


def test_permutation_invariance_and_seeded_random(monkeypatch):
    monkeypatch.setattr(evaluation,'rank_candidates',fake_engine)
    items=[row(f'w{i}',i%5+1) for i in range(15)]
    a=evaluation.evaluate_recommendations(items,folds=3,seed=7,k=2)
    b=evaluation.evaluate_recommendations(list(reversed(items)),folds=3,seed=7,k=2)
    assert a==b
    changed=evaluation.evaluate_recommendations(items,folds=3,seed=8,k=2)
    assert a['split_sha256']!=changed['split_sha256']


def test_cannot_split_one_connected_group_or_silently_reduce_folds(monkeypatch):
    monkeypatch.setattr(evaluation,'rank_candidates',fake_engine)
    with pytest.raises(ValueError,match='groups'):
        evaluation.evaluate_recommendations([row(str(i),5,author_ids=['one']) for i in range(10)],folds=3)
    with pytest.raises(ValueError,match='groups'):
        evaluation.evaluate_recommendations([row('a',5),row('b',1)],folds=5)


def test_engine_pool_mismatch_is_rejected(monkeypatch):
    def inconsistent(candidates,references,**options):
        result=fake_engine(candidates,references,**options)
        if options['method']=='popularity':
            result['ranking']=result['ranking'][:-1]
            result['audit']['eligible_work_ids']=result['audit']['eligible_work_ids'][:-1]
        return result
    monkeypatch.setattr(evaluation,'rank_candidates',inconsistent)
    with pytest.raises(ValueError,match='same eligible'):
        evaluation.evaluate_recommendations([row(str(i),i%5+1) for i in range(6)],folds=3)


def test_actual_engine_unknown_support_same_pool_and_no_popularity_inference():
    items=[row(f'w{i}',i%5+1,features={} if i==4 else {'theme.fixture':1.}) for i in range(6)]
    before=deepcopy(items)
    report=evaluation.evaluate_recommendations(items,folds=3,k=10)
    assert items==before
    assert report['methods']['personal']['candidate_n']==6
    assert report['methods']['personal']['no_feature_candidate_n']==1
    assert report['methods']['personal']['supported_candidate_n']==5
    assert report['methods']['personal']['no_evidence_candidate_n']==1
    assert report['methods']['popularity']['supported_candidate_n']==0
    assert report['methods']['random']['supported_candidate_n']==6
    assert all(fold['counts']['excluded_held_n']==0 for fold in report['folds'])
    assert report['counts']['observed_popularity_n']==0
    for fold in report['folds']:
        assert len(fold['methods']['popularity']['ranking'])==len(fold['eligible_work_ids'])
        assert all(item['score'] is None for item in fold['methods']['popularity']['ranking'])


def test_actual_engine_held_rating_changes_cannot_change_held_ranking():
    items=[row(f'w{i}',i%5+1) for i in range(9)]
    before=evaluation.evaluate_recommendations(items,folds=3,seed=15,k=2)
    held=set(before['split']['folds'][0]['heldout_rated_work_ids'])
    changed=deepcopy(items)
    for item in changed:
        if item['work_id'] in held:
            item['rating']=6-item['rating']
    after=evaluation.evaluate_recommendations(changed,folds=3,seed=15,k=2)
    assert before['input_sha256']!=after['input_sha256']
    assert before['split']==after['split']
    for method in ('personal','popularity','random'):
        assert before['folds'][0]['methods'][method]['ranking']==after['folds'][0]['methods'][method]['ranking']


@pytest.mark.parametrize('updates',[{'rating':True},{'rating':0},{'features':{'x':-1}},
    {'features':{'x':float('nan')}},{'popularity':float('inf')}])
def test_invalid_numeric_input_is_not_hashed_or_scored(updates):
    invalid=row('bad',5);invalid.update(updates)
    with pytest.raises(ValueError):
        evaluation.evaluate_recommendations([invalid,row('other',1)],folds=2)
