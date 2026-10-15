"""Exploratory grouped cross-validation of observed recommendation ratings."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
from numbers import Real
import random
from statistics import mean


def rank_candidates(*args, **kwargs):
    # Late import keeps this evaluator independent of engine initialization.
    from .engine import rank_candidates as rank
    return rank(*args, **kwargs)


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _finite_real(value):
    if isinstance(value,bool) or not isinstance(value,Real):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _identities(values,field):
    if values is None:
        return []
    if not isinstance(values,list) or any(not isinstance(value,str) for value in values):
        raise ValueError(f'{field} requires a list of string identity tokens')
    return sorted({value.strip() for value in values if value.strip()})


def _normalize(rows):
    normalized=[];seen=set()
    for original in rows:
        row=deepcopy(dict(original))
        work=row.get('work_id')
        if not isinstance(work,str) or not work.strip() or work in seen:
            raise ValueError('Distinct nonempty work_id required for every input row')
        seen.add(work)
        rating=row.get('rating')
        if rating is not None and (not _finite_real(rating) or not 1<=rating<=5):
            raise ValueError('rating must be null or finite non-bool real 1..5')
        row['rating']=None if rating is None else float(rating)
        features=row.get('features',{})
        if not isinstance(features,dict) or any(not isinstance(key,str) or not key.strip() or not _finite_real(value) or value<0 for key,value in features.items()):
            raise ValueError('features must map nonempty strings to finite nonnegative non-bool real values')
        row['features']={key:float(value) for key,value in sorted(features.items())}
        for field in ('author_ids','series_ids','author_group_keys'):
            row[field]=_identities(row.get(field,[]),field)
        row.setdefault('read_status','unknown')
        if row['read_status'] not in ('read','unread','unknown'):
            raise ValueError('read_status must be read, unread or unknown')
        external=row.get('external_ids',[])
        if not isinstance(external,list):
            raise ValueError('external_ids must be a list of source/value identities')
        pairs=set()
        for item in external:
            if not isinstance(item,dict):
                raise ValueError('external identity requires source/value')
            source=item.get('source',item.get('namespace'));value=item.get('value')
            if not isinstance(source,str) or not source.strip() or not isinstance(value,str) or not value.strip():
                raise ValueError('external identity requires nonempty string source/value')
            pairs.add((source.strip(),value.strip()))
        row['external_ids']=[{'source':source,'value':value} for source,value in sorted(pairs)]
        row.setdefault('feature_provenance',{})
        if not isinstance(row['feature_provenance'],dict):
            raise ValueError('feature_provenance must be a dictionary')
        popularity=row.get('popularity')
        if popularity is not None and not _finite_real(popularity):
            raise ValueError('popularity must be null or finite non-bool real')
        row['popularity']=None if popularity is None else float(popularity)
        normalized.append(row)
    if not normalized:
        raise ValueError('Nonempty observed rating input required')
    return sorted(normalized,key=lambda row:row['work_id'])


def _tokens(row):
    return ([('author',value) for value in row['author_ids']]+
        [('series',value) for value in row['series_ids']]+
        [('author_name_block',value) for value in row['author_group_keys']]+
        [('external',item['source'],item['value']) for item in row['external_ids']])


def _connected_groups(rows):
    parent={row['work_id']:row['work_id'] for row in rows}
    def find(work):
        while parent[work]!=work:
            parent[work]=parent[parent[work]];work=parent[work]
        return work
    owners={}
    for row in rows:
        for token in _tokens(row):
            if token in owners:
                a,b=find(row['work_id']),find(owners[token])
                if a!=b:
                    parent[max(a,b)]=min(a,b)
            else:
                owners[token]=row['work_id']
    return {work:find(work) for work in sorted(parent)}


def _split(rows,folds,seed):
    groups=_connected_groups(rows);members=defaultdict(list)
    for row in rows:
        members[groups[row['work_id']]].append(row)
    rated=[group for group in sorted(members) if any(row['rating'] is not None for row in members[group])]
    if len(rated)<folds:
        raise ValueError(f'Requires at least {folds} independent groups with ratings; only {len(rated)} groups available. Cannot split connected groups or silently reduce folds.')
    rng=random.Random(seed);rng.shuffle(rated)
    rated.sort(key=lambda group:sum(row['rating'] is not None for row in members[group]),reverse=True)
    priority=list(range(folds));rng.shuffle(priority);tie={fold:index for index,fold in enumerate(priority)}
    assignments={};rated_load=[0]*folds;all_load=[0]*folds
    for group in rated+[group for group in sorted(members) if group not in set(rated)]:
        fold=min(range(folds),key=lambda index:(rated_load[index],all_load[index],tie[index]))
        assignments[group]=fold
        rated_load[fold]+=sum(row['rating'] is not None for row in members[group]);all_load[fold]+=len(members[group])
    partitions=[]
    for fold in range(folds):
        held=[row for row in rows if assignments[groups[row['work_id']]]==fold]
        train=[row for row in rows if assignments[groups[row['work_id']]]!=fold]
        partitions.append({'fold':fold,'train_work_ids':[row['work_id'] for row in train],
            'heldout_work_ids':[row['work_id'] for row in held],
            'train_rated_work_ids':[row['work_id'] for row in train if row['rating'] is not None],
            'heldout_rated_work_ids':[row['work_id'] for row in held if row['rating'] is not None],
            'heldout_group_ids':sorted({groups[row['work_id']] for row in held})})
    return {'version':'grouped-observed-ratings-cv-v1','seed':seed,'fold_count':folds,
        'group_by_work':groups,'group_count':len(members),'rated_group_count':len(rated),
        'folds':partitions,'policy':'Whole connected components of recorded author/series IDs, conservative author-name keys and exact external identities; all rows including unrated bridges. Missing identities add no shared token.'}


def _ranking_metrics(ranking,ratings,k):
    effective=min(k,len(ratings));top=ranking[:effective]
    if len(ranking)!=len(ratings) or set(ranking)!=set(ratings):
        raise ValueError('Metrics require exactly the common eligible rated pool')
    positive_n=sum(rating>=4 for rating in ratings.values())
    hits=0;ap_sum=0.
    for index,work in enumerate(top,1):
        if ratings[work]>=4:
            hits+=1;ap_sum+=hits/index
    dcg=sum((2**ratings[work]-1)/math.log2(index+2) for index,work in enumerate(top))
    ideal=sum((2**rating-1)/math.log2(index+2) for index,rating in enumerate(sorted(ratings.values(),reverse=True)[:effective]))
    return {'eligible_n':len(ratings),'effective_k':effective,'positive_n':positive_n,'positive_hits_at_k':hits,
        'ndcg_at_k':dcg/ideal if ideal else None,'precision_at_k':hits/effective if effective else None,
        'recall_at_k':hits/positive_n if positive_n else None,'ap_at_k':ap_sum/positive_n if positive_n else None}


def _verify_ranking(response,candidates):
    ranking=response['ranking'];eligible=response['audit']['eligible_work_ids']
    ids=[row['work_id'] for row in ranking]
    if len(eligible)!=len(set(eligible)) or len(ids)!=len(set(ids)) or set(ids)!=set(eligible) or not set(eligible)<=set(candidates):
        raise ValueError('Engine ranking and eligible pool must agree completely without truncation')
    for row in ranking:
        status=row.get('score_status');score=row.get('score')
        if status not in ('supported','unsupported') or (status=='supported' and not _finite_real(score)) or (status=='unsupported' and score is not None):
            raise ValueError('Engine must expose supported finite scores or unsupported null scores')
        evidence=row.get('evidence_count',0)
        if isinstance(evidence,bool) or not isinstance(evidence,int) or evidence<0:
            raise ValueError('Engine evidence_count must be a nonnegative integer')
    return sorted(eligible)


def _method_report(ranking,ratings,candidates,k):
    metrics=_ranking_metrics([row['work_id'] for row in ranking],ratings,k)
    supported=sum(row['score_status']=='supported' for row in ranking)
    top=ranking[:metrics['effective_k']];supported_top=sum(row['score_status']=='supported' for row in top)
    return {'metrics':metrics,'ranking':[{'work_id':row['work_id'],'score':row['score'],
        'score_status':row['score_status'],'evidence_count':row.get('evidence_count',0)} for row in ranking],
        'coverage':{'candidate_n':len(ranking),'supported_candidate_n':supported,
            'supported_candidate_rate':supported/len(ranking) if ranking else None,
            'unsupported_candidate_n':len(ranking)-supported,
            'no_evidence_candidate_n':sum(row.get('evidence_count',0)==0 for row in ranking),
            'supported_without_evidence_candidate_n':sum(row['score_status']=='supported' and row.get('evidence_count',0)==0 for row in ranking),
            'no_feature_candidate_n':sum(not any(value>0 for value in candidates[row['work_id']]['features'].values()) for row in ranking),
            'top_k_n':len(top),'supported_top_k_n':supported_top,'supported_top_k_rate':supported_top/len(top) if top else None,
            'no_evidence_top_k_n':sum(row.get('evidence_count',0)==0 for row in top),
            'interpretation':'Quality metrics include unsupported deterministic ties from the same eligible pool; coverage must be read alongside quality.'}}


def evaluate_recommendations(rows,*,folds=5,seed=20261013,k=10):
    """Group-held observed ratings; this is neither temporal nor prospective."""
    if isinstance(folds,bool) or not isinstance(folds,int) or folds<2:
        raise ValueError('At least two integer folds required')
    if isinstance(seed,bool) or not isinstance(seed,int):
        raise ValueError('Integer seed required')
    if isinstance(k,bool) or not isinstance(k,int) or k<1:
        raise ValueError('Positive integer k required')
    items=_normalize(rows);index={row['work_id']:row for row in items};split=_split(items,folds,seed)
    input_hash=_digest(items);split_hash=_digest({'input_sha256':input_hash,**split})
    observations=[]
    for partition in split['folds']:
        train=[index[work] for work in partition['train_work_ids']]
        held=[index[work] for work in partition['heldout_work_ids']]
        references=deepcopy([index[work] for work in partition['train_rated_work_ids']])
        candidates=deepcopy([index[work] for work in partition['heldout_rated_work_ids']])
        for candidate in candidates:
            candidate['rating']=None;candidate['read_status']='unknown'
        by_candidate={row['work_id']:row for row in candidates}
        responses={method:rank_candidates(deepcopy(candidates),deepcopy(references),method=method,
            limit=len(candidates),read_history=deepcopy(references),require_confirmed_unread=False)
            for method in ('personal','popularity')}
        pools={method:_verify_ranking(response,by_candidate) for method,response in responses.items()}
        if pools['personal']!=pools['popularity']:
            raise ValueError('Personal and popularity must use the same eligible held pool')
        eligible=pools['personal'];ratings={work:index[work]['rating'] for work in eligible}
        random_ids=list(eligible)
        random_seed=int(_digest({'seed':seed,'fold':partition['fold'],'purpose':'independent-random-baseline-v1'}),16)
        random.Random(random_seed).shuffle(random_ids)
        random_rows=[{'work_id':work,'score':float(len(random_ids)-position),'score_status':'supported','evidence_count':0} for position,work in enumerate(random_ids)]
        methods={method:_method_report(response['ranking'],ratings,by_candidate,k) for method,response in responses.items()}
        methods['random']=_method_report(random_rows,ratings,by_candidate,k)
        methods['random']['coverage']['interpretation']='Seeded random order is defined for all eligible candidates; supported here means an order is available, not evidence support.'
        train_tokens={token for row in train for token in _tokens(row)}
        held_tokens={token for row in held for token in _tokens(row)}
        overlap=train_tokens&held_tokens
        if overlap:
            raise ValueError('Recorded author/series/name/external identity crosses train and holdout')
        observations.append({'fold':partition['fold'],'counts':{'train_work_n':len(train),'heldout_work_n':len(held),
            'train_rated_n':len(references),'heldout_rated_n':len(candidates),'eligible_held_n':len(eligible),
            'excluded_held_n':len(candidates)-len(eligible),'heldout_unrated_n':sum(row['rating'] is None for row in held)},
            'author_overlap_n':sum(token[0]=='author' for token in overlap),'series_overlap_n':sum(token[0]=='series' for token in overlap),
            'author_name_key_overlap_n':sum(token[0]=='author_name_block' for token in overlap),
            'external_identity_overlap_n':sum(token[0]=='external' for token in overlap),
            'eligible_work_ids':eligible,'excluded_held_work_ids':sorted(set(by_candidate)-set(eligible)),
            'eligible_pool_sha256':_digest(eligible),'methods':methods})
    aggregate={}
    for method in ('personal','popularity','random'):
        names=('ndcg_at_k','precision_at_k','recall_at_k','ap_at_k')
        values={name:[fold['methods'][method]['metrics'][name] for fold in observations if fold['methods'][method]['metrics'][name] is not None] for name in names}
        coverage=[fold['methods'][method]['coverage'] for fold in observations]
        total=sum(item['candidate_n'] for item in coverage);supported=sum(item['supported_candidate_n'] for item in coverage)
        aggregate[method]={'macro':{('map_at_k' if name=='ap_at_k' else name):mean(numbers) if numbers else None for name,numbers in values.items()},
            'defined_fold_counts':{('map_at_k' if name=='ap_at_k' else name):len(numbers) for name,numbers in values.items()},
            'fold_count':folds,'candidate_n':total,'supported_candidate_n':supported,
            'supported_candidate_rate':supported/total if total else None,
            'no_evidence_candidate_n':sum(item['no_evidence_candidate_n'] for item in coverage),
            'supported_without_evidence_candidate_n':sum(item['supported_without_evidence_candidate_n'] for item in coverage),
            'no_feature_candidate_n':sum(item['no_feature_candidate_n'] for item in coverage)}
    return {'schema_version':'1','status':'exploratory_grouped_observed_rating_cv','input_sha256':input_hash,
        'split_sha256':split_hash,'split':split,'folds':observations,'methods':aggregate,
        'settings':{'folds':folds,'seed':seed,'k':k,'positive_threshold':4,'graded_gain':'2**rating - 1',
            'precision_denominator':'min(k, eligible pool size)','ap_denominator':'all eligible positives; truncated AP@k',
            'settings_status':'fixed exploratory settings; not an independent tuning validation'},
        'design':{'temporal':False,'prospective':False,'candidate_scope':'Held observed-rated works only; null ratings are not negative feedback.',
            'simulation':'Held ratings are withheld and read_status becomes unknown only for ranking; original rated/read-status provenance remains in the input digest.',
            'popularity_scope':'Only supplied popularity observations are ranked. Missing popularity is unsupported; no worldwide popularity inference.',
            'feature_time':'Current supplied features do not reconstruct what was available before an historical rating.'},
        'counts':{'work_n':len(items),'rated_work_n':sum(row['rating'] is not None for row in items),
            'unrated_work_n':sum(row['rating'] is None for row in items),
            'original_read_status_counts':dict(sorted(Counter(row['read_status'] for row in items).items())),
            'observed_popularity_n':sum(row['popularity'] is not None for row in items)},
        'identity_audit':{'author_id_recorded_n':sum(bool(row['author_ids']) for row in items),
            'author_name_key_recorded_n':sum(bool(row['author_group_keys']) for row in items),
            'name_blocking_used':any(row['author_group_keys'] for row in items),
            'name_blocking_policy':'Conservative exact supplied normalized author-name keys; not adjudicated authority identity.',
            'unrecorded_author_and_series_identity_n':sum(not row['author_ids'] and not row['author_group_keys'] and not row['series_ids'] for row in items),
            'missing_author_authority_and_series_ids_n':sum(not row['author_ids'] and not row['series_ids'] for row in items),
            'unrecorded_series_identity_n':sum(not row['series_ids'] for row in items),
            'overlap_scope':'Zero overlap concerns recorded IDs/name keys/external aliases only. Missing series or author identities cannot be certified leak-free.'},
        'quality_limits':{'independent_proof':False,'classification_human_gold_created':False,
            'interpretation':'Observed personal ratings are preference feedback, not classification gold. Grouped CV does not establish prospective recommendation quality.'}}
