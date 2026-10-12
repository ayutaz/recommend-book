"""Preregistered, exploratory nested grouped evaluation; no external requests."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math
import random
from statistics import mean

from . import engine
from .evaluation import _digest, _finite_real, _normalize, _split, _tokens, _ranking_metrics, _verify_ranking


def default_settings():
    return [{'id':'personal','family':'personal'}, {'id':'popularity','family':'popularity'}]+[
        {'id':f'hybrid_{int(weight*100)}','family':'hybrid_rank','personal_weight':weight,
         'missing_component':'zero_fixed_weight'} for weight in (.25,.5,.75)]+[
        {'id':f'tie_robust_{neighbors}','family':'personal_tie_robust','neighbors':neighbors,'prior':.1}
        for neighbors in (10,20,40)]


def preregistration():
    return {'version':'autonomous-recommendation-preregistration-v1','settings':default_settings(),
        'outer_folds':5,'inner_folds':3,'outer_seed':20261013,'inner_seed':20261014,'k':10,
        'selection_rule':'Highest arithmetic mean inner nDCG@k; exact ties resolved by preregistered setting order. No secondary-metric tuning.',
        'hybrid_rule':'Supported component score ties use average rank. Utility=(N+1-midrank)/N where N is the entire eligible pool, unsupported contribution zero. Fixed component weights, no per-candidate renormalization. Neither supported => null.',
        'neighbor_rule':'Positive cosine only; include every neighbor with similarity >= nominal nth similarity, exactly equal float ties. Prior .1, global train rating mean, squared cosine weighting.',
        'bootstrap_replicates':200,'bootstrap_seed':20261015,
        'bootstrap_rule':'Resample recorded connected held groups within each fixed outer fold, recompute fixed-order ranking metrics, pair selected vs popularity. Conditional descriptive interval only; no retraining or independent fold-trial assumption.',
        'random_rule':'Independent deterministic shuffle of the same complete pool; seed derived from outer/inner stage, fold and fixed seed.',
        'deployment_selection':'Separate full-source three-fold inner CV, no outer metrics used.',
        'dataset_previously_observed':True,'fresh_holdout_claim':False,
        'feature_policy':'Existing supplied shared features and bridge unchanged; omission stays unknown.',
        'registered_before_execution':True}


def _validate_setting(setting):
    if not isinstance(setting,dict) or not isinstance(setting.get('id'),str) or not setting['id']:
        raise ValueError('Setting requires a nonempty id')
    family=setting.get('family')
    if family not in ('personal','popularity','hybrid_rank','personal_tie_robust'):
        raise ValueError('Unknown setting family')
    expected={'id','family'}
    if family=='hybrid_rank':
        expected|={'personal_weight','missing_component'}
        weight=setting.get('personal_weight')
        if not _finite_real(weight) or not 0<weight<1 or setting.get('missing_component')!='zero_fixed_weight':
            raise ValueError('Hybrid requires finite non-bool weight strictly between 0 and 1 and fixed missing policy')
    if family=='personal_tie_robust':
        expected|={'neighbors','prior'}
        n=setting.get('neighbors');prior=setting.get('prior')
        if isinstance(n,bool) or not isinstance(n,int) or n<1 or not _finite_real(prior) or prior<=0:
            raise ValueError('Tie robust requires positive integer neighbors and finite positive prior')
    if set(setting)!=expected:
        raise ValueError('Unexpected or missing setting parameters')


def _midrank_utilities(ranking):
    n=len(ranking);utilities={};position=0
    while position<n:
        first=ranking[position]
        if first['score'] is None:
            break
        end=position+1
        while end<n and ranking[end]['score']==first['score']:
            end+=1
        midrank=((position+1)+end)/2
        for item in ranking[position:end]:
            utilities[item['work_id']]=(n+1-midrank)/n
        position=end
    return utilities


def rank_with_setting(candidates,references,setting,*,limit=20,read_history=None,require_confirmed_unread=False):
    """Pure engine-compatible ranking. Evidence and unknown read status are retained."""
    _validate_setting(setting)
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<0:
        raise ValueError('Nonnegative integer limit required')
    family=setting['family']
    base=engine.rank_candidates(candidates,references,method='popularity' if family=='popularity' else 'personal',
        limit=len(candidates),read_history=read_history,require_confirmed_unread=require_confirmed_unread)
    ranking=base['ranking'];audit=base['audit']
    if family=='hybrid_rank':
        popular=engine.rank_candidates(candidates,references,method='popularity',limit=len(candidates),
            read_history=read_history,require_confirmed_unread=require_confirmed_unread)
        if set(audit['eligible_work_ids'])!=set(popular['audit']['eligible_work_ids']):
            raise ValueError('Hybrid components disagree about eligible pool')
        personal_u=_midrank_utilities(ranking);popular_u=_midrank_utilities(popular['ranking'])
        popular_by={r['work_id']:r for r in popular['ranking']};weight=setting['personal_weight']
        audit['component_coverage']={'personal_n':len(personal_u),'popularity_n':len(popular_u),'pool_n':len(ranking)}
        for candidate in ranking:
            work=candidate['work_id'];supported=work in personal_u or work in popular_u
            score=weight*personal_u.get(work,0)+(1-weight)*popular_u.get(work,0) if supported else None
            components={'personal':{'score':candidate['score'],'rank_utility':personal_u.get(work), 'status':candidate['score_status']},
                'popularity':{'score':popular_by[work]['score'],'rank_utility':popular_u.get(work),'status':popular_by[work]['score_status']}}
            candidate['score']=score;candidate['score_status']='supported' if supported else 'unsupported'
            candidate['explanations'].append({'reason':'Fixed-weight average-rank hybrid; missing component contributes zero',
                'components':components,'personal_weight':weight,'missing_component':'zero_fixed_weight'})
    elif family=='personal_tie_robust':
        refs=engine._rows(references,'references');rated=[r for r in refs if r['rating'] is not None]
        units={r['work_id']:engine._unit(r['features']) for r in rated}
        global_mean=math.fsum(r['rating'] for r in rated)/len(rated) if rated else None
        for candidate in ranking:
            unit=engine._unit(candidate['features'])
            nearest=sorted([(r,engine._cosine(unit,units[r['work_id']])) for r in rated],
                key=lambda pair:(-pair[1],pair[0]['work_id'])) if unit else []
            nearest=[pair for pair in nearest if pair[1]>0]
            if nearest:
                cutoff=nearest[min(setting['neighbors'],len(nearest))-1][1]
                nearest=[pair for pair in nearest if pair[1]>=cutoff]
                denominator=setting['prior']+math.fsum(sim**2 for _,sim in nearest)
                explanations=[]
                for ref,sim in nearest:
                    weight=sim**2;centered=ref['rating']-global_mean
                    explanations.append({'reference_work_id':ref['work_id'],'similarity':sim,'weight':weight,
                        'rating':ref['rating'],'reference_rating_mean':global_mean,'centered_rating':centered,
                        'contribution':weight*centered/denominator,'features':engine._feature_evidence(candidate,ref),
                        'neighbor_cutoff_similarity':cutoff,'nominal_neighbors':setting['neighbors'],
                        'actual_neighbors':len(nearest),'boundary_ties_included':True})
                candidate['score']=global_mean+math.fsum(e['contribution'] for e in explanations)
                candidate['score_status']='supported';candidate['explanations']=explanations
                candidate['evidence_count']=engine._evidence_count(candidate,explanations)
            # Unsupported base rows keep their reason/null unchanged.
    ranking.sort(key=lambda r:(r['score'] is None,-r['score'] if r['score'] is not None else 0,r['work_id']))
    audit.update(method=family,autonomous_setting=deepcopy(setting),supported_count=sum(r['score'] is not None for r in ranking),
        unsupported_count=sum(r['score'] is None for r in ranking),returned=min(limit,len(ranking)),world_transfer_verified=False)
    return {'ranking':ranking[:limit],'audit':audit}


def _summarize_ranking(ranking,ratings,k):
    metrics=_ranking_metrics([r['work_id'] for r in ranking],ratings,k)
    n=len(ranking);top=ranking[:min(k,n)];scores=[r['score'] for r in ranking if r['score'] is not None]
    boundary=top[-1]['score'] if top else None
    return {'metrics':metrics,'coverage':{'candidate_n':n,
        'supported_candidate_n':len(scores),'supported_candidate_rate':len(scores)/n if n else None,
        'unsupported_candidate_n':n-len(scores),'null_score_n':n-len(scores),
        'supported_top_k_n':sum(r['score'] is not None for r in top),
        'no_evidence_candidate_n':sum(r.get('evidence_count',0)==0 for r in ranking),
        'no_evidence_top_k_n':sum(r.get('evidence_count',0)==0 for r in top)},
        'ties':{'distinct_supported_score_n':len(set(scores)),
            'boundary_tie_n':sum(r['score']==boundary for r in ranking) if top else 0,
            'boundary_null':bool(top and boundary is None),
            'top_k_in_non_singleton_tie_n':sum(sum(other['score']==r['score'] for other in ranking)>1 for r in top),
            'tie_break':'work_id ascending; null unsupported last; null ties counted explicitly'}}


def select_setting(scored,settings):
    """Select solely by mean supplied inner nDCG, exact ties by registered order."""
    def value(setting):
        numbers=[fold['metrics']['ndcg_at_k'] for fold in scored[setting['id']] if fold['metrics']['ndcg_at_k'] is not None]
        return mean(numbers) if numbers else -math.inf
    best=max(settings,key=value)
    if value(best)==-math.inf:
        raise ValueError('No defined inner nDCG for any setting')
    return deepcopy(best)


def _score_partition(index,partition,settings,k,seed,stage):
    train=[index[w] for w in partition['train_work_ids']];held=[index[w] for w in partition['heldout_work_ids']]
    references=deepcopy([index[w] for w in partition['train_rated_work_ids']])
    candidates=deepcopy([index[w] for w in partition['heldout_rated_work_ids']])
    for candidate in candidates:
        candidate['rating']=None;candidate['read_status']='unknown'
    by={r['work_id']:r for r in candidates};responses={};pools={}
    for setting in settings:
        response=rank_with_setting(candidates,references,setting,limit=len(candidates),read_history=references)
        pools[setting['id']]=_verify_ranking(response,by);responses[setting['id']]=response
    eligible=next(iter(pools.values()))
    if any(pool!=eligible for pool in pools.values()):
        raise ValueError('Settings must share one identical common eligible pool')
    if not eligible:
        raise ValueError('Empty common eligible held pool')
    ratings={w:index[w]['rating'] for w in eligible}
    random_ids=list(eligible)
    random.Random(int(_digest({'seed':seed,'stage':stage,'fold':partition['fold'],'purpose':'random-baseline'}),16)).shuffle(random_ids)
    random_rows=[{'work_id':w,'score':float(len(random_ids)-i),'score_status':'supported','evidence_count':0} for i,w in enumerate(random_ids)]
    responses['random']={'ranking':random_rows}
    summaries={name:_summarize_ranking(response['ranking'],ratings,k) for name,response in responses.items()}
    overlap={token for row in train for token in _tokens(row)}&{token for row in held for token in _tokens(row)}
    if overlap:
        raise ValueError('Recorded identity overlap across train and holdout')
    counts={'train_work_n':len(train),'heldout_work_n':len(held),'train_rated_n':len(references),
        'heldout_rated_n':len(candidates),'eligible_n':len(eligible),'excluded_n':len(candidates)-len(eligible)}
    # Private IDs, titles, feedback and evidence never enter the exported observations.
    public={'fold':partition['fold'],'counts':counts,'same_pool':True,'identity_overlap_n':len(overlap),
        'eligible_pool_sha256':_digest(eligible),'methods':summaries}
    compact={name:[{key:row[key] for key in ('work_id','score','score_status','evidence_count')} for row in response['ranking']]
        for name,response in responses.items()}
    return public,{'rankings':compact,'ratings':ratings}


def _aggregate(summaries):
    names=('ndcg_at_k','precision_at_k','recall_at_k','ap_at_k')
    values={name:[s['metrics'][name] for s in summaries if s['metrics'][name] is not None] for name in names}
    n=sum(s['coverage']['candidate_n'] for s in summaries);supported=sum(s['coverage']['supported_candidate_n'] for s in summaries)
    return {'macro':{('map_at_k' if name=='ap_at_k' else name):mean(v) if v else None for name,v in values.items()},
        'defined_fold_counts':{name:len(v) for name,v in values.items()},'candidate_n':n,
        'supported_candidate_n':supported,'supported_candidate_rate':supported/n if n else None,
        'null_score_n':n-supported,'no_evidence_candidate_n':sum(s['coverage']['no_evidence_candidate_n'] for s in summaries),
        'mean_boundary_tie_n':mean(s['ties']['boundary_tie_n'] for s in summaries),
        'mean_distinct_supported_score_n':mean(s['ties']['distinct_supported_score_n'] for s in summaries)}


def _conditional_bootstrap(observations,group_by_work,k,replicates):
    differences=[];rng=random.Random(20261015)
    for _ in range(replicates):
        fold_differences=[]
        for observation in observations:
            groups=sorted({group_by_work[w] for w in observation['ratings']})
            multiplicity=Counter(rng.choices(groups,k=len(groups)))
            metrics={}
            for method in ('selected','popularity'):
                # Copy group observations according to resample multiplicity while
                # retaining the fixed out-of-fold score order and its tie-break.
                ranking=[];ratings={}
                for original in observation['rankings'][method]:
                    w=original['work_id']
                    for copy in range(multiplicity[group_by_work[w]]):
                        alias=f'{w}#bootstrap{copy}';ranking.append(alias);ratings[alias]=observation['ratings'][w]
                metrics[method]=_ranking_metrics(ranking,ratings,k)['ndcg_at_k']
            if all(v is not None for v in metrics.values()):
                fold_differences.append(metrics['selected']-metrics['popularity'])
        if fold_differences:
            differences.append(mean(fold_differences))
    ordered=sorted(differences)
    def quantile(p):
        if not ordered:return None
        position=(len(ordered)-1)*p;lower=int(position);upper=min(lower+1,len(ordered)-1)
        return ordered[lower]+(ordered[upper]-ordered[lower])*(position-lower)
    return {'method':'paired connected-group bootstrap within fixed outer folds','seed':20261015,
        'replicates':replicates,'defined_replicates':len(ordered),'ndcg_difference_selected_minus_popularity_95_interval':[quantile(.025),quantile(.975)],
        'independent_fold_trials':False,'retraining_uncertainty_estimated':False,
        'scope':'Conditional descriptive interval for fixed out-of-fold scores/order, sampled recorded held groups. Does not estimate model-selection/retraining, missing-identity, historical-feature or new-user/world transfer uncertainty.'}


def evaluate_nested(rows,*,settings=None,outer_folds=5,inner_folds=3,outer_seed=20261013,inner_seed=20261014,k=10,bootstrap_replicates=200):
    """Nested grouped CV on already-observed feedback; export aggregates only."""
    for value,minimum in ((outer_folds,2),(inner_folds,2),(k,1),(bootstrap_replicates,1)):
        if isinstance(value,bool) or not isinstance(value,int) or value<minimum:
            raise ValueError('Fold/k/bootstrap counts require valid positive integers')
    if any(isinstance(seed,bool) or not isinstance(seed,int) for seed in (outer_seed,inner_seed)):
        raise ValueError('Integer seeds required')
    settings=deepcopy(default_settings() if settings is None else settings)
    if not settings or len(settings)>12 or len({s.get('id') for s in settings})!=len(settings):
        raise ValueError('One to twelve settings with distinct ids required')
    for setting in settings:_validate_setting(setting)
    if not any(s['id']=='popularity' and s['family']=='popularity' for s in settings):
        raise ValueError('Registered popularity baseline required')
    items=_normalize(rows);index={r['work_id']:r for r in items};outer=_split(items,outer_folds,outer_seed)
    input_sha=_digest(items);folds=[];observations=[]
    for partition in outer['folds']:
        train=[index[w] for w in partition['train_work_ids']]
        inner=_split(train,inner_folds,inner_seed)
        scored={s['id']:[] for s in settings};inner_public=[]
        for ip in inner['folds']:
            public,_=_score_partition(index,ip,settings,k,inner_seed,f'inner-outer-{partition["fold"]}')
            inner_public.append(public)
            for setting in settings:scored[setting['id']].append(public['methods'][setting['id']])
        selected=select_setting(scored,settings)
        public,private=_score_partition(index,partition,settings,k,outer_seed,'outer')
        public['selected_setting']=selected;public['methods']['selected']=deepcopy(public['methods'][selected['id']])
        public['inner_folds']=inner_public;public['inner_split_sha256']=_digest(inner)
        private['rankings']['selected']=private['rankings'][selected['id']]
        folds.append(public);observations.append(private)
    full_inner=_split(items,inner_folds,inner_seed);full_scores={s['id']:[] for s in settings};full_folds=[]
    for partition in full_inner['folds']:
        public,_=_score_partition(index,partition,settings,k,inner_seed,'deployment-inner')
        full_folds.append(public)
        for setting in settings:full_scores[setting['id']].append(public['methods'][setting['id']])
    deployment=select_setting(full_scores,settings)
    aggregate={name:_aggregate([f['methods'][name] for f in folds]) for name in ['selected','popularity','random']}
    candidates={s['id']:_aggregate([f['methods'][s['id']] for f in folds]) for s in settings}
    return {'schema_version':'autonomous-nested-recommendation-v1','status':'exploratory_nested_grouped_observed_feedback',
        'input_sha256':input_sha,'split_sha256':_digest({'input_sha256':input_sha,'split':outer}),
        'settings':{'outer_folds':outer_folds,'inner_folds':inner_folds,'outer_seed':outer_seed,'inner_seed':inner_seed,'k':k},
        'preregistration_value_sha256':_digest(preregistration()),'outer_folds':folds,
        'methods':aggregate,'candidate_settings':candidates,'selection_counts':dict(Counter(f['selected_setting']['id'] for f in folds)),
        'selection':{'recommended_setting':deployment,'selection_basis':'full_source_3fold_inner_cv_only',
            'world_transfer_verified':False,'personal_setting':{'id':'personal','family':'personal'},
            'inner_split_sha256':_digest(full_inner),'inner_folds':full_folds,
            'inner_candidate_metrics':{s['id']:_aggregate(full_scores[s['id']]) for s in settings},
            'outer_metrics_used_for_selection':False},
        'uncertainty':_conditional_bootstrap(observations,outer['group_by_work'],k,bootstrap_replicates),
        'counts':{'work_n':len(items),'rated_work_n':sum(r['rating'] is not None for r in items),
            'unrated_work_n':sum(r['rating'] is None for r in items),'group_n':outer['group_count'],
            'rated_group_n':outer['rated_group_count'],'original_read_status_counts':dict(Counter(r['read_status'] for r in items)),
            'author_authority_recorded_n':sum(bool(r['author_ids']) for r in items),
            'author_name_key_recorded_n':sum(bool(r['author_group_keys']) for r in items),
            'unrecorded_series_n':sum(not r['series_ids'] for r in items),
            'unrecorded_author_and_series_n':sum(not r['author_ids'] and not r['author_group_keys'] and not r['series_ids'] for r in items)},
        'limits':{'dataset_previously_observed':True,'fresh_holdout':False,'prospective':False,'temporal':False,
            'human_taxonomy_gold_n':0,'calibration_measured':False,'world_transfer_verified':False,
            'unrated_feedback_is_negative':False,'missing_feature_is_absent':False,
            'name_blocking':'Supplied normalized display-name keys are conservative blocks, not verified author identity.',
            'identity_scope':'Zero overlap only for recorded author/series/name/external tokens; absent series identities prevent a complete leakage guarantee.',
            'feature_time':'Current metadata/LLM features do not reconstruct historical availability.',
            'all_candidate_outer_metrics':'Descriptive, already observed and unsuitable for selecting settings or claiming independent improvement.',
            'random_support':'Random has a defined order, with zero evidence; supported does not imply a predicted preference.',
            'popularity':'Only observed domain popularity. No Narou-to-world transfer; missing remains unsupported.'}}
