"""Purpose-bound lexical reference delivery with an unscored bibliography."""
from __future__ import annotations
from copy import deepcopy
from collections import Counter
from html import escape
from pathlib import Path
from recommend_book.usage_guard import UsageDecision,model_hash,GUARD_VERSION
from recommend_book.recommendation.next_evaluation import (
    rank_owned_titles,admit_private_aggregate,_digest,_spec_sha,_immutable_json)
from recommend_book.recommendation.engine import rank_candidates


def _display_allowed(guard,payload,origins,*,source_manifest=None,derivation_recipe=None):
    if not origins or len(origins)!=len(set(origins)):raise ValueError('Complete bibliographic/classification origins required')
    options={}
    if source_manifest is not None:options['source_manifest']=source_manifest
    if derivation_recipe is not None:options['derivation_recipe']=derivation_recipe
    decision=guard.check_derived(payload,list(origins),'display',**options)
    if not isinstance(decision,UsageDecision) or decision.guard_version!=GUARD_VERSION or decision.usage!='display':
        raise ValueError('Display purpose decision required')
    if decision.payload_sha256!=model_hash(payload) or set(decision.input_hashes)!=set(origins) or not decision.ledger_sha256:
        raise ValueError('Display payload/full-origin binding differs')
    return decision.allowed is True,decision.ledger_sha256


def _work_features(row):
    features=row.get('features',{});provenance=row.get('feature_provenance',{})
    for label in features:
        proofs=provenance.get(label,[])
        if label.startswith('style.') or not proofs or any(p.get('scope')!='work' or p.get('expression_id') for p in proofs):
            return False
    return True


def _projection_payload(packet,expected,origins,kind):
    """Only the exact sealed projection is admitted, never a whole arbitrary row."""
    from recommend_book.models import WorkInput
    if not isinstance(packet,dict) or packet.get('kind')!=kind or packet.get('payload')!=expected:
        raise ValueError('Exact typed projection payload required')
    if packet.get('originating_input_hashes')!=origins or packet.get('value_sha256')!=model_hash(expected):
        raise ValueError('Complete typed projection value/origin binding differs')
    original=WorkInput.model_validate(packet['input_json'])
    if packet.get('input_hash')!=model_hash(original) or packet['input_hash'] not in origins:
        raise ValueError('Exact typed source input differs')
    if original.work_id!=expected['work_id'] or original.title!=expected['title'] or original.evidence:
        raise ValueError('Typed metadata projection subject/title differs')
    return packet['payload']


def _classification_allowed(guard,row,origins):
    """Native output and extracted feature values have separate typed proofs."""
    native=row.get('classification_derivation');feature=row.get('feature_derivation')
    if not isinstance(native,dict) or not isinstance(feature,dict) or not origins:
        return False,None
    required_native=('kind','payload','value_sha256','originating_input_hashes','source_manifest')
    required_feature=('kind','payload','value_sha256','originating_input_hashes','source_manifest','features_recipe','native_result')
    if any(key not in native for key in required_native) or any(key not in feature for key in required_feature):
        return False,None
    manifest=native['source_manifest'];recipe=feature['features_recipe']
    if not isinstance(manifest,dict) or not manifest or not isinstance(recipe,dict) or not all(k in recipe for k in ('path','sha256','extraction','native_result_sha256')):
        return False,None
    expected={'work_id':row['work_id'],'features':row.get('features',{}),'feature_provenance':row.get('feature_provenance',{})}
    result=native['payload']
    if (native['kind']!='classification' or feature['kind']!='classification_features'
        or native['originating_input_hashes']!=origins or feature['originating_input_hashes']!=origins
        or feature['source_manifest']!=manifest or feature['payload']!=expected
        or native['value_sha256']!=model_hash(result) or feature['value_sha256']!=model_hash(expected)
        or feature['native_result']!=result or recipe['native_result_sha256']!=model_hash(result)
        or not isinstance(result,dict) or result.get('work_id')!=row['work_id'] or result.get('input_hash') not in origins):
        raise ValueError('Typed native classification/feature derivation binding differs')
    permitted,ledger=_display_allowed(guard,result,origins,source_manifest=manifest)
    if not permitted:return False,ledger
    approved,feature_ledger=_display_allowed(guard,expected,origins,source_manifest=manifest,derivation_recipe=recipe)
    if feature_ledger!=ledger:raise ValueError('Purpose ledger changed during native/feature checks')
    return approved,ledger


def build_next_delivery(world,references=None,*,guard,origin_ids=None,spec=None,limit=100,read_history=None,private_guard=None,mode='lexical-reference'):
    """Titles imply lexical resemblance only. Complete source inputs stay bound."""
    if mode not in ('bibliographic-only','lexical-reference','classified-reference'):raise ValueError('Explicit supported delivery mode required')
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<0:raise ValueError('limit must be nonnegative integer')
    metadata_only=mode=='bibliographic-only';content_mode=mode=='classified-reference'
    private=None
    if not metadata_only:
        if references is None or spec is None or not origin_ids:raise ValueError('Owned reference/spec/origins required for reference mode')
        request={'mode':'owned_title_fixed_ranking','input_sha256':_digest(references),
            'preregistration_sha256':_spec_sha(spec),'aggregate_only':True}
        private_guard=private_guard if private_guard is not None else guard
        private=admit_private_aggregate(private_guard,request,origin_ids)
    admitted=[];held_meta=held_content=held_scope=content_n=alias_held=alias_used=0;ledgers=set();seen=set();labels={}
    for original in world:
        row=original;work=row['work_id']
        if work in seen:raise ValueError('Duplicate world work ID')
        seen.add(work)
        origins=row.get('bibliographic_originating_input_hashes',[])
        if not origins:raise ValueError('Complete bibliographic origins required')
        expected={'work_id':work,**{f:row.get(f) for f in ('title','authors','original_language','first_publication_year','region','series')}}
        payload=_projection_payload(row.get('bibliographic_derivation'),expected,origins,'bibliographic_metadata')
        allowed,ledger=_display_allowed(guard,payload,origins);ledgers.add(ledger)
        if not allowed:held_meta+=1;continue
        safe={'work_id':work,'title':row['title'],'authors':row.get('authors',[]),
            'series_ids':row.get('series_ids',[]),'author_ids':row.get('author_ids',[]),
            'external_ids':row.get('external_ids',[]),'read_status':row.get('read_status','unknown'),
            'rating':None,'features':{},'feature_provenance':{},'popularity':None}
        safe['title_aliases']=[]
        if metadata_only or spec.get('version')=='owned-title-fixed-v3.3':
            for alias in row.get('display_title_aliases',[]):
                alias_origins=alias.get('originating_input_hashes',[])
                value={f:alias.get(f) for f in ('work_id','title','language','role')}
                alias_payload=_projection_payload(alias.get('derivation_packet'),value,alias_origins,'title_alias')
                permitted,al=_display_allowed(guard,alias_payload,alias_origins);ledgers.add(al)
                if permitted:safe['title_aliases'].append(alias['title']);alias_used+=1
                else:alias_held+=1
        labels[work]=[]
        if content_mode:
            allorigins=row.get('classification_originating_input_hashes',[])
            if not _work_features(row):held_scope+=1
            else:
                permitted,cl=_classification_allowed(guard,row,allorigins)
                if cl is not None:ledgers.add(cl)
                if not permitted:held_content+=1
                elif not _work_features(row):held_scope+=1
                else:
                    content_n+=1;labels[work]=sorted(row['features'])
        admitted.append(safe)
    if len(ledgers)>1:raise ValueError('Purpose ledger changed during delivery')
    if admitted:
        # Shared engine's actual ID/history eligibility; no title identity inference.
        eligible=rank_candidates(admitted,[],method='content',limit=len(admitted),read_history=read_history)['ranking']
        ids={r['work_id'] for r in eligible};candidates=[r for r in admitted if r['work_id'] in ids]
    else:candidates=[]
    if metadata_only:
        unscored=[{'work_id':r['work_id'],'title':r['title'],'read_status':r['read_status'],
            'score':None,'score_status':'unsupported','explanations':[],'evidence_count':0}
            for r in sorted(candidates,key=lambda r:r['work_id'])[:limit]]
        result={'ranking':unscored,'audit':{'eligible_n':len(candidates),'supported_n':0,
            'authority_matches_inferred':0,'api_fields_used':[],'reference_titles_exported':0,
            'reference_ids_exported':0,'confirmed_unread_n':sum(r['read_status']=='unread' for r in candidates)}}
    else:result=rank_owned_titles(candidates,references,limit=limit)
    byid={r['work_id']:r for r in candidates}
    for row in result['ranking']:
        row['authors']=byid[row['work_id']]['authors']
        row['display_title_aliases']=byid[row['work_id']]['title_aliases']
        row['content_labels']=labels[row['work_id']]
        row['content_labels_are_preference_evidence']=False
    baseline=[{'work_id':r['work_id'],'title':r['title'],'authors':r['authors'],
        'read_status':r['read_status'],'score':None,'score_status':'unsupported','explanations':[]}
        for r in sorted(candidates,key=lambda r:r['work_id'])[:limit]]
    groups=Counter(s for row in result['ranking'] for s in byid[row['work_id']]['series_ids'])
    authorcounts=Counter(a for row in result['ranking'] for a in byid[row['work_id']]['author_ids'])
    if not metadata_only and private!=admit_private_aggregate(private_guard,request,origin_ids):raise ValueError('Private purpose changed during delivery')
    return {'version':'next-lexical-delivery-v3.2','ranking':result['ranking'],
        'bibliographic_baseline':baseline,'method':'bibliographic_unscored' if metadata_only else 'owned_title_bigram',
        'mode':mode,'explanation':'用途審査を通った書誌のみの未採点一覧です。作品の好みや内容を評価した順位ではありません。' if metadata_only else
            '本人の保存評点と書名の文字類似による探索的な参考表示。品質改善方式として採用した順位ではありません。',
        'purpose':private,'preregistration_sha256':None if metadata_only else _spec_sha(spec),
        'audit':{**result['audit'],'world_inventory_n':len(world),'bibliographic_held_n':held_meta,
            'content_feature_held_n':held_content,'scope_feature_held_n':held_scope,'content_feature_eligible_n':content_n,
            'title_alias_used_n':alias_used,'title_alias_held_n':alias_held,
            'display_ledger_sha256':next(iter(ledgers),None),'history_excluded_n':len(admitted)-len(candidates),
            'classification_features_read':content_mode,'owned_ratings_read':not metadata_only,'quiet_fallback':False,
            'user_tasks':0,'new_ratings':0,'world_content_profile_bindings_n':0,'quality_verified':False,
            'api_fields_used':[],'world_popularity_comparable':False,
            'series_max_occurrences_top_n':max(groups.values(),default=0),
            'author_max_occurrences_top_n':max(authorcounts.values(),default=0),
            'recorded_series_top_n':sum(bool(byid[r['work_id']]['series_ids']) for r in result['ranking']),
            'series_diversity_verified_for_missing_ids':False,'translation_style_features_used':0,
            'private_reference_titles_displayed':0,'private_reference_ids_displayed':0,
            'scores_are_registered_user_ratings':False,'scope':'Purpose-checked unscored bibliography' if metadata_only else 'Fixed lexical arithmetic, no semantic content-preference inference'}}


def render_next_delivery(delivery):
    """Escape metadata and offer browsing; no review or feedback task forms."""
    cards=[]
    metadata_only=delivery.get('mode')=='bibliographic-only'
    for rank,row in enumerate(delivery['ranking'],1):
        score=f"{row['score']:.3f}" if row['score'] is not None else '未採点'
        detail=f'<p>未採点 / 読書状態: {escape(row["read_status"])}</p>' if metadata_only else (
            f'<p>書名類似の参考値: {score} / 読書状態: {escape(row["read_status"])}</p>'
            f'<p>書名が重なる保存評点の件数: {row["evidence_count"]}</p>')
        aliases=' / '.join(escape(t) for t in row.get('display_title_aliases',[]))
        cards.append(f'<article><h2>{rank}. {escape(row["title"])}</h2><p>{escape(" / ".join(row.get("authors",[])))}</p>'
            +detail+(f'<p>資料に記録された別名: {aliases}</p>' if aliases else '')+'</article>')
    baseline=''.join(f'<li>{escape(r["title"])} — 未採点</li>' for r in delivery['bibliographic_baseline'])
    return ('<!doctype html><html lang="ja"><meta charset="utf-8"><title>参考作品一覧</title>'
        '<style>body{font-family:system-ui;max-width:1000px;margin:auto;padding:24px}article{padding:16px;border:1px solid #ddd;margin:12px 0}</style>'+
        ('<h1>書誌のみの未採点一覧</h1><p>' if metadata_only else '<h1>書名の類似による探索的参考一覧</h1><p>')+escape(delivery['explanation'])+
        '</p><p>追加入力の必要はありません。未読は確認できていません。参考値は作品への本人評価ではありません。</p>'+''.join(cards)+
        '<details><summary>書誌のみの一覧（未採点）</summary><ul>'+baseline+'</ul></details></html>')


def write_next_delivery(delivery,output_dir):
    output=Path(output_dir);_immutable_json(output/'delivery.json',delivery)
    html=render_next_delivery(delivery).encode()
    path=output/'index.html'
    if path.exists() and path.read_bytes()!=html:raise ValueError('Immutable HTML differs')
    if not path.exists():
        with path.open('xb') as stream:stream.write(html)
