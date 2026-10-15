"""Bounded v3 comparison contracts and source-separated private input origins."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
import math
import random
from statistics import mean

from .evaluation import _normalize,_digest,_split,_connected_groups
from .evaluation import _ranking_metrics


def _title_key(value):
    if not isinstance(value,str):raise ValueError('Owned/title metadata must be strings')
    return ''.join(unicodedata.normalize('NFKC',value).casefold().split())


def _grams(title):
    text=_title_key(title)
    return {text[i:i+2] for i in range(len(text)-1)}


def owned_title_spec(source_sha256,*,title_alias_mode=False):
    """Fixed before observations; no API input, feature inference or tuning."""
    spec={'version':'owned-title-fixed-v3.2','source_database_sha256':source_sha256,
        'methods':['owned_title_bigram','constant_no_popularity','random'],
        'rule':'NFKC/casefold/remove whitespace; character bigram set Jaccard; similarity weighted mean of rating minus fixed midpoint3; zero similarity remains unsupported',
        'title_grouping':'normalized identical titles block folds; not authority identity',
        'outer_folds':5,'inner_folds':0,'seed':20261013,'k':10,'parameter_search_n':0,
        'api_fields_used':[],'annotations_reused':0,'new_ratings':0,'dataset_previously_observed':True,
        'fresh_holdout':False,'semantic_preference_accuracy_verified':False,
        'random_rule':'sorted held work IDs shuffled with independent Random(seed+fold)',
        'permission_required':True,'reference_output':'aggregate only; no source titles, IDs, individual ratings or quotes',
        'prior_preregistration':'preregistration-v1.json archived; its API/nested variants held, not executed under this spec'}
    if title_alias_mode:
        spec.update(version='owned-title-fixed-v3.3',world_title_rule='max primary and individually display-guarded source-bound aliases',
            alias_duplicate_policy='Each reference contributes at most once; title authority/translation identity never inferred',
            alias_source_policy='Exact stored SourceRecord work titles only; separate complete WorkInput origin for each alias')
    return spec


def load_owned_ratings(database):
    """Read exactly the owner's observations; never join API metadata tables."""
    path=Path(database).resolve(strict=True);before=_sha(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    db.execute('pragma query_only=on')
    try:
        records=list(db.execute('''WITH ranked AS (SELECT o.ncode,o.title,o.my_rating,o.snapshot_id,
            ROW_NUMBER() OVER(PARTITION BY o.ncode ORDER BY i.observed_at DESC,i.imported_at DESC,i.snapshot_id DESC) AS rank
            FROM rating_observations o JOIN imports i USING(snapshot_id))
            SELECT ncode,title,my_rating,snapshot_id FROM ranked WHERE rank=1 ORDER BY ncode'''))
    finally:db.close()
    if len(records)!=335:raise ValueError('Owned baseline requires exactly335 ratings')
    rows=[]
    for record in records:
        value=record['my_rating']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 1<=value<=5:
            raise ValueError('Owned rating must be finite nonbool1..5')
        _title_key(record['title'])
        rows.append({'work_id':'owned:'+record['ncode'].lower(),'title':record['title'],'rating':float(value),
            'snapshot_id':record['snapshot_id'],'features':{},'popularity':None,'author_ids':[],
            'series_ids':[],'external_ids':[],'read_status':'unknown','feature_provenance':{}})
    if before!=_sha(path):raise ValueError('Owned source changed')
    return {'rows':rows,'audit':{'source_database_sha256':before,'source_tables_read':['rating_observations','imports'],
        'latest_rows_n':335,'api_tables_read':[],'api_annotations_reused':0,'input_sha256':_digest(rows),
        'author_series_identity_unavailable':True,'titles_not_authority_keys':True}}


def rank_owned_titles(candidates,references,*,limit=100):
    """Pure fixed lexical arithmetic. Production admission is the caller's guard."""
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<0:raise ValueError('limit must be nonnegative integer')
    prepared=[]
    for row in references:
        rating=row.get('rating')
        if isinstance(rating,bool) or not isinstance(rating,(int,float)) or not math.isfinite(rating) or not 1<=rating<=5:
            raise ValueError('reference rating must be finite nonbool1..5')
        prepared.append((_grams(row['title']),rating-3))
    ranking=[];seen=set()
    for row in candidates:
        work=row['work_id']
        if not isinstance(work,str) or not work or work in seen:raise ValueError('Distinct candidate IDs required')
        seen.add(work);gramsets=[_grams(t) for t in [row['title'],*row.get('title_aliases',[])]];weight=total=0.;support=0
        for refgrams,centered in prepared:
            similarity=max((len(grams&refgrams)/len(grams|refgrams) if grams|refgrams else 0. for grams in gramsets),default=0.)
            if similarity>0:weight+=similarity;total+=similarity*centered;support+=1
        score=total/weight if weight else None
        # Deliberately omit all input extra fields and all reference identities.
        ranking.append({'work_id':work,'title':row['title'],'score':score,
            'score_status':'supported' if weight else 'unsupported','read_status':row.get('read_status','unknown'),
            'evidence_count':support,'explanations':[{'kind':'title_lexical_similarity','reference_count':support,
                'semantic_content_inferred':False,'authority_match_inferred':False}] if weight else []})
    ranking.sort(key=lambda r:(r['score'] is None,-(r['score'] or 0),r['work_id']))
    return {'ranking':ranking[:limit],'audit':{'eligible_n':len(ranking),
        'supported_n':sum(r['score'] is not None for r in ranking),'authority_matches_inferred':0,
        'api_fields_used':[],'reference_titles_exported':0,'reference_ids_exported':0,
        'confirmed_unread_n':sum(r['read_status']=='unread' for r in ranking)}}


def admit_private_aggregate(guard,payload,origin_ids):
    """Reject booleans and detached receipts; the genuine guard rechecks sources."""
    from recommend_book.usage_guard import UsageDecision,model_hash,GUARD_VERSION
    if not origin_ids or len(origin_ids)!=len(set(origin_ids)):raise ValueError('Complete distinct purpose origins required')
    decision=guard.check_private_reference_aggregate(payload,list(origin_ids),context='private_local_owned_title_ranking')
    if not isinstance(decision,UsageDecision) or decision.allowed is not True or decision.guard_version!=GUARD_VERSION or decision.usage!='local_storage':
        raise ValueError('Valid allowed purpose decision required')
    private_roster=any(p.get('material')=='owned manual-import observations' and p.get('allowed') is True
        and p.get('context')=='private_local_owned_title_ranking' and p.get('input_sha256')==payload['input_sha256']
        and p.get('origin_ids_sha256')==_digest(sorted(origin_ids)) and p.get('origin_n')==len(origin_ids)
        for p in decision.material_decisions)
    if decision.payload_sha256!=model_hash(payload) or (set(decision.input_hashes)!=set(origin_ids) and not private_roster) or not decision.ledger_sha256:
        raise ValueError('Complete purpose payload/origin binding differs')
    return {'guard_version':decision.guard_version,'ledger_sha256':decision.ledger_sha256,
        'origin_n':len(origin_ids),'origin_ids_sha256':_digest(sorted(origin_ids)),
        'payload_sha256':decision.payload_sha256,'proof_hashes':decision.proof_hashes}


def _spec_sha(spec):
    return hashlib.sha256((json.dumps(spec,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()).hexdigest()


def evaluate_owned_titles(rows,*,guard,origin_ids,spec):
    """Fixed exploratory lexical CV. No private roster is exported."""
    if spec!=owned_title_spec(spec['source_database_sha256'],title_alias_mode=spec['version']=='owned-title-fixed-v3.3'):
        raise ValueError('Frozen fixed lexical spec differs')
    if any(row.get('features') or row.get('popularity') is not None for row in rows):
        raise ValueError('Owned-only evaluation cannot reuse API features/popularity')
    request={'mode':'owned_title_fixed_ranking','input_sha256':_digest(rows),
        'preregistration_sha256':_spec_sha(spec),'aggregate_only':True}
    purpose=admit_private_aggregate(guard,request,origin_ids)
    items=_normalize(rows)
    for row in items:
        # This is a leakage block, not an assertion that titles prove identity.
        title=_title_key(row['title'])
        row['author_group_keys']=['normalized_title:'+_digest(title)] if title else []
    split=_split(items,spec['outer_folds'],spec['seed']);index={r['work_id']:r for r in items}
    observations=[]
    for partition in split['folds']:
        train=[index[w] for w in partition['train_work_ids']]
        held=[index[w] for w in partition['heldout_work_ids']]
        overlap={_title_key(r['title']) for r in train if _title_key(r['title'])}&{_title_key(r['title']) for r in held if _title_key(r['title'])}
        if overlap:raise ValueError('Normalized title crosses heldout boundary')
        candidates=[{'work_id':r['work_id'],'title':r['title'],'rating':None,'read_status':'unknown'} for r in held]
        ratings={r['work_id']:r['rating'] for r in held};ids=sorted(ratings)
        randomids=ids[:];random.Random(spec['seed']+partition['fold']).shuffle(randomids)
        ranking=rank_owned_titles(candidates,train,limit=len(candidates))['ranking']
        methods={}
        for method,order,support in [('owned_title_bigram',[r['work_id'] for r in ranking],sum(r['score'] is not None for r in ranking)),
            ('constant_no_popularity',ids,0),('random',randomids,0)]:
            methods[method]={'metrics':_ranking_metrics(order,ratings,spec['k']),
                'supported_candidate_n':support,'candidate_n':len(ids),
                'supported_rate':support/len(ids) if ids else None,
                'tie_policy':'unsupported and equal scores ordered by work ID' if method!='random' else 'fixed seeded permutation'}
        observations.append({'fold':partition['fold'],'train_n':len(train),'held_n':len(held),
            'title_overlap_n':0,'eligible_pool_sha256':_digest(ids),'methods':methods})
    aggregate={}
    for method in spec['methods']:
        vals={key:[fold['methods'][method]['metrics'][key] for fold in observations if fold['methods'][method]['metrics'][key] is not None]
            for key in ['ndcg_at_k','precision_at_k','recall_at_k','ap_at_k']}
        aggregate[method]={'macro':{key:mean(value) if value else None for key,value in vals.items()},
            'defined_fold_n':{key:len(value) for key,value in vals.items()},
            'supported_candidate_n':sum(f['methods'][method]['supported_candidate_n'] for f in observations)}
    aggregate['popularity']={'status':'held','metrics':None,'reason':'No API popularity read or comparable owned popularity in this mode'}
    aggregate['hybrid_50']={'status':'held','metrics':None,'reason':'API-dependent prior family outside owned-title preregistration; no automatic selection'}
    # Recheck the original source/permission after computation as well.
    if purpose!=admit_private_aggregate(guard,request,origin_ids):raise ValueError('Purpose/source changed during computation')
    return {'version':'owned-title-exploratory-v3.2','input_sha256':request['input_sha256'],
        'preregistration_sha256':request['preregistration_sha256'],'split_sha256':_digest(split),
        'counts':{'rated_n':len(items),'normalized_title_group_n':split['group_count']},
        'folds':observations,'methods':aggregate,'purpose':purpose,'quality_verified':False,
        'fresh_holdout':False,'author_series_leakage_certified':False,
        'limitations':['Missing author/series authority; identical title blocking only.',
            'Book-title lexical resemblance does not establish content/genre preference or work identity.',
            'Previously observed ratings; folds are not independent replications or prospective validation.'],
        'private_titles_exported':0,'private_ids_exported':0,'new_ratings':0}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _immutable_json(path,value):
    encoded=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=encoded:raise ValueError('Immutable output differs; use a new namespace')
    else:
        with path.open('xb') as stream:stream.write(encoded)


def verify_archived_baseline(reference,report):
    """Verify a previously observed 335 snapshot; does not reuse held features."""
    rows=_normalize(reference['rows']);audit=reference['audit'];groups=_connected_groups(rows)
    if len(rows)!=335 or audit['latest_rows_n']!=335 or sum(r['rating'] is not None for r in rows)!=335:
        raise ValueError('Saved baseline must contain exactly 335 known ratings')
    if audit['annotation_n']!=331 or audit['missing_annotation_n']!=4 or len(set(groups.values()))!=283:
        raise ValueError('Saved annotation331/missing4/connected group283 differs')
    digest=_digest(rows)
    if report['input_sha256']!=digest:raise ValueError('Archived input digest differs')
    split_sha=_digest({'input_sha256':digest,'split':_split(rows,5,20261013)})
    if report['split_sha256']!=split_sha:raise ValueError('Archived split digest differs')
    if report.get('provenance',{}).get('source_sha256',audit['source_sha256'])!=audit['source_sha256']:
        raise ValueError('Archived source snapshot differs')
    return {'rated_work_n':335,'annotation_n':331,'missing_annotation_n':4,'connected_group_n':283,
        'input_sha256':digest,'split_sha256':split_sha,'source_sha256':deepcopy(audit['source_sha256']),
        'dataset_previously_observed':True,'fresh_holdout':False,'extra_personal_ratings':0,
        'archived_outer_selected_ndcg_at_10':report['methods']['selected']['macro']['ndcg_at_k'],
        'archived_outer_popularity_ndcg_at_10':report['methods']['popularity']['macro']['ndcg_at_k'],
        'archive_permission_status':'History verification only; current feature reuse requires a separate purpose receipt.'}


def evaluation_spec(baseline,*,archived_report_sha256):
    return {'version':'next-recommendation-evaluation-v3','baseline':deepcopy(baseline),
        'archived_report_sha256':archived_report_sha256,'outer_folds':5,'inner_folds':3,
        'outer_seed':20261013,'inner_seed':20261014,'k':10,'max_candidate_settings':9,
        'max_feature_connection_candidates':1,'methods':['personal','popularity','hybrid_50'],
        'hybrid_personal_weight':.5,'hybrid_missing_component':'zero_fixed_weight',
        'source_mode_selection':'purpose_receipt_only_never_quality_metric',
        'source_mode_order':['approved_cached_shared','approved_factual_metadata_fallback'],
        'variants':['shared_base','central_parenting_family_hint','factual_metadata_fallback'],
        'feature_connection':{'source_native_id':'parenting','source_prominence':'central','target_id':'theme.family',
            'strength':1.,'relation':'experimental_narrow_parenting_hint_not_taxonomy_truth',
            'secondary_is_mapped':False,'human_gold':False,'world_transfer_verified':False},
        'factual_fallback':{'features':['declared_genre'],'mapping':'existing_fixed_narou_genre_codes',
            'popularity':'observed_global_point_only','grouping':'recorded_writer_normalized_name_and_exact_external_alias',
            'no_title_story_keyword_inference':True},
        'selection':'Preregistered inner mean nDCG only, exact ties by declared order; outer metrics never select.',
        'held_policy':'Any held originating input blocks the whole derived classification, even with zero cited references. All blocked variants reported with null metrics.',
        'dataset_previously_observed':True,'fresh_holdout':False,'new_user_ratings':0,'llm_requests':0,
        'source_superiority_established':False,'world_preference_quality_verified':False,
        'guard_receipt_required_for_new_computation':True}


def connect_parenting_hint(original):
    """One preregistered experimental connection; caller must authorize origins."""
    row=deepcopy(original)
    native='narou.theme.parenting'
    proofs=[p for p in row.get('native_feature_provenance',{}).get(native,[])
        if p.get('native_theme_id')=='parenting' and p.get('native_prominence')=='central']
    if proofs and row.get('native_features',{}).get(native)==1:
        row['features']['theme.family']=max(row['features'].get('theme.family',0),1.)
        row['feature_provenance'].setdefault('theme.family',[]).extend({**p,
            'connection_version':'central-parenting-family-hint-v3','human_taxonomy_truth':False,
            'world_transfer_verified':False,'relation':'experimental_narrow_parenting_hint_not_taxonomy_truth'} for p in proofs)
    return row


def prepare_reference_origins(database,annotations,output_dir):
    """Local private source inventory, exact cached bytes; never print row values."""
    database=Path(database).resolve(strict=True);annotations=Path(annotations).resolve(strict=True)
    dbsha=_sha(database);annsha=_sha(annotations);output=Path(output_dir)
    annotation=json.loads(annotations.read_text(encoding='utf-8'));positions={r['ncode'].lower():i for i,r in enumerate(annotation['novels'])}
    db=sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row;db.execute('pragma query_only=on')
    try:
        records=list(db.execute('''WITH ranked AS (SELECT o.*,i.observed_at,i.imported_at,
            ROW_NUMBER() OVER(PARTITION BY o.ncode ORDER BY i.observed_at DESC,i.imported_at DESC,i.snapshot_id DESC) as rank
            FROM rating_observations o JOIN imports i USING(snapshot_id))
            SELECT r.ncode,r.snapshot_id,r.my_rating,r.first_rated_at,r.last_rated_at,
                m.writer,m.genre,m.global_point,m.keyword,m.story,m.raw_json,m.fetch_status
            FROM ranked r LEFT JOIN novel_metadata m USING(ncode) WHERE rank=1 ORDER BY r.ncode'''))
    finally:db.close()
    if len(records)!=335:raise ValueError('Private source inventory requires frozen335 ratings')
    entries=[];raw_n=0
    for record in records:
        code=record['ncode'].lower();rawpath=None;rawsha=None;metadata=None
        if record['fetch_status']=='success' and record['raw_json']:
            raw=record['raw_json'].encode();rawsha=hashlib.sha256(raw).hexdigest();rawpath=output/'raw'/f'{rawsha}.json'
            metadata=json.loads(raw)
            if metadata.get('ncode','').lower()!=code or any(metadata.get(k)!=record[k] for k in ('genre','writer','global_point','story','keyword')):
                raise ValueError('Cached factual/story fields differ from exact API record')
            rawpath.parent.mkdir(parents=True,exist_ok=True)
            if rawpath.exists():
                if rawpath.read_bytes()!=raw:raise ValueError('Cached raw inventory collision')
            else:
                with rawpath.open('xb') as stream:stream.write(raw)
            raw_n+=1
        annotation_index=positions.get(code)
        entries.append({'work_id':'narou:'+code,'external_identity':{'source':'narou','value':code},
            'personal_rating_origin':{'database_sha256':dbsha,'snapshot_id':record['snapshot_id'],
                'value_sha256':_digest(record['my_rating']),'rating_value_not_exported':True},
            'factual_metadata_origin':{'raw_path':str(rawpath.resolve()) if rawpath else None,'raw_sha256':rawsha,
                'database_table':'novel_metadata.raw_json','values':{k:record[k] for k in ('genre','global_point','writer')} if metadata else {},
                'field_pointers':{k:k for k in ('genre','global_point','writer')},'fetch_status':record['fetch_status']},
            'third_party_story_keywords_origin':{'raw_path':str(rawpath.resolve()) if rawpath else None,'raw_sha256':rawsha,
                'field_pointers':['title','story','keyword'],'cached_third_party_text':True},
            'cached_native_annotation_origin':{'path':str(annotations),'artifact_sha256':annsha,
                'pointer':f'novels/{annotation_index}' if annotation_index is not None else None,
                'value_sha256':_digest(annotation['novels'][annotation_index]) if annotation_index is not None else None,
                'annotation_global_source_sha256':annotation['source_sha256'],'prompt_version':annotation['prompt_version'],
                'original_classifier_input_hash':None,'original_classifier_input_hash_recorded':False,
                'past_prelaunch_permission_established':False},
            'current_purpose_receipt':'not_evaluated_by_this_inventory'})
    if _sha(database)!=dbsha or _sha(annotations)!=annsha:raise ValueError('Frozen335 source changed during inventory')
    document={'version':'next-private-reference-origins-v3','private_local_only':True,'rows_n':335,
        'cached_API_records_n':raw_n,'cached_annotations_n':len(positions),'source_sha256':{'database':dbsha,'annotations':annsha},
        'new_llm_requests':0,'new_personal_ratings':0,'permission_granted_by_inventory':False,'rows':entries}
    _immutable_json(output/'origins.json',document)
    return {'rows_n':335,'cached_API_records_n':raw_n,'cached_annotations_n':len(positions),
        'origins_path':str(output/'origins.json'),'origins_sha256':_sha(output/'origins.json'),'source_sha256':document['source_sha256']}
