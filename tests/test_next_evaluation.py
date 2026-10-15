"""Synthetic fixtures: no new personal ratings or human taxonomy gold."""
from copy import deepcopy
import pytest
from recommend_book.recommendation import next_evaluation as ne
from recommend_book.recommendation.evaluation import _normalize,_digest,_split
import sqlite3


def reference_fixture():
    rows=[{'work_id':f'private-{i}','title':'synthetic','rating':i%5+1,'features':{'f':1.},
        'author_ids':[f'author-{i//2}'] if i<104 else [],'series_ids':[],
        'author_group_keys':[],'external_ids':[],'feature_provenance':{},'read_status':'unknown','popularity':None} for i in range(335)]
    reference={'rows':rows,'audit':{'latest_rows_n':335,'annotation_n':331,'missing_annotation_n':4,
        'source_sha256':{'database':'d'*64,'annotations':'a'*64,'bridge':'b'*64}}}
    normalized=_normalize(rows);inputsha=_digest(normalized)
    report={'counts':{'work_n':335,'rated_work_n':335,'group_n':283},'input_sha256':inputsha,
        'split_sha256':_digest({'input_sha256':inputsha,'split':_split(normalized,5,20261013)}),
        'methods':{'selected':{'macro':{'ndcg_at_k':.5739950901424992}},'popularity':{'macro':{'ndcg_at_k':.6339778306197021}}}}
    return reference,report


def test_335_archived_baseline_exact_and_no_private_rows_in_report():
    reference,report=reference_fixture();before=deepcopy(reference)
    verified=ne.verify_archived_baseline(reference,report)
    assert verified['rated_work_n']==335 and verified['annotation_n']==331 and verified['missing_annotation_n']==4
    assert verified['connected_group_n']==283 and not verified['fresh_holdout']
    assert 'private-' not in str(verified) and reference==before


def test_336_or_changed_input_is_rejected_not_called_fresh():
    reference,report=reference_fixture();reference['rows'][0]['rating']=1.5
    with pytest.raises(ValueError,match='digest'):ne.verify_archived_baseline(reference,report)
    reference,report=reference_fixture();reference['audit']['latest_rows_n']=336
    with pytest.raises(ValueError,match='335'):ne.verify_archived_baseline(reference,report)


def test_spec_fixed_bounds_and_factual_fallback_before_results():
    reference,report=reference_fixture();verified=ne.verify_archived_baseline(reference,report)
    spec=ne.evaluation_spec(verified,archived_report_sha256='c'*64)
    assert spec['max_candidate_settings']==9
    assert spec['outer_folds']==5 and spec['inner_folds']==3
    assert spec['max_feature_connection_candidates']==1 and spec['new_user_ratings']==0
    assert spec['feature_connection']['source_native_id']=='parenting'
    assert spec['source_mode_selection']=='purpose_receipt_only_never_quality_metric'
    assert set(spec['methods'])=={'personal','popularity','hybrid_50'}
    assert spec['factual_fallback']['features']==['declared_genre']
    assert spec['factual_fallback']['popularity']=='observed_global_point_only'


def test_parenting_hint_requires_central_exact_provenance_and_leaves_unknown():
    row={'features':{},'feature_provenance':{},'native_features':{'narou.theme.parenting':1},
        'native_feature_provenance':{'narou.theme.parenting':[{'native_theme_id':'parenting','native_prominence':'central','source':'synthetic','human_taxonomy_truth':False}]}}
    before=deepcopy(row);connected=ne.connect_parenting_hint(row)
    assert connected['features']=={'theme.family':1.} and row==before
    assert connected['feature_provenance']['theme.family'][0]['human_taxonomy_truth'] is False
    row['native_feature_provenance']['narou.theme.parenting'][0]['native_prominence']='secondary'
    assert ne.connect_parenting_hint(row)['features']=={}


def test_owned_adapter_reads_only_observation_and_import_tables(tmp_path):
    path=tmp_path/'owned.sqlite';db=sqlite3.connect(path)
    db.executescript('CREATE TABLE imports(snapshot_id TEXT,observed_at TEXT,imported_at TEXT); CREATE TABLE rating_observations(snapshot_id TEXT,ncode TEXT,title TEXT,my_rating REAL);')
    db.execute('INSERT INTO imports VALUES (?,?,?)',('s','2026','2026'))
    db.executemany('INSERT INTO rating_observations VALUES (?,?,?,?)',[('s',f'n{i}','private synthetic title',4) for i in range(335)])
    db.commit();db.close();before=path.read_bytes()
    got=ne.load_owned_ratings(path)
    assert len(got['rows'])==335 and got['audit']['api_tables_read']==[]
    assert got['audit']['api_annotations_reused']==0 and path.read_bytes()==before
    assert 'private synthetic title' not in str(got['audit'])


def test_lexical_rule_fixed_and_null_when_no_title_overlap():
    refs=[{'work_id':'private','title':'ABCD','rating':5}]
    candidates=[{'work_id':'w1','title':'ABXY'},{'work_id':'w2','title':'QRST'}]
    before=deepcopy(refs)
    result=ne.rank_owned_titles(candidates,refs,limit=10)
    assert result['ranking'][0]['work_id']=='w1'
    assert result['ranking'][0]['score']==2
    assert result['ranking'][1]['score'] is None
    assert refs==before and 'private' not in str(result)
    assert result['audit']['authority_matches_inferred']==0


def test_lexical_rejects_rating_bool_and_does_not_reuse_supplied_features():
    with pytest.raises(ValueError,match='rating'):
        ne.rank_owned_titles([{'work_id':'w','title':'AB'}],[{'work_id':'p','title':'AB','rating':True}])
    got=ne.rank_owned_titles([{'work_id':'w','title':'QQ','features':{'unsafe':1}}],
        [{'work_id':'p','title':'AB','rating':5,'features':{'unsafe':1}}])
    assert got['ranking'][0]['score'] is None
    assert 'unsafe' not in str(got)


def test_spec2_no_model_selection_or_api_reuse():
    spec=ne.owned_title_spec('a'*64)
    assert spec['parameter_search_n']==0 and spec['inner_folds']==0
    assert spec['api_fields_used']==[] and spec['fresh_holdout'] is False
    assert spec['methods']==['owned_title_bigram','constant_no_popularity','random']


def test_evaluate_requires_exact_purpose_decision_before_ranking():
    refs=[{'work_id':str(i),'title':f'AB{i}','rating':4 if i%2 else 2} for i in range(12)]
    class BadGuard:
        def check_private_reference_aggregate(self,*a,**kw):return True
    with pytest.raises(ValueError,match='purpose'):
        ne.evaluate_owned_titles(refs,guard=BadGuard(),origin_ids=['test'],spec=ne.owned_title_spec('a'*64))


def test_evaluate_same_pool_train_only_no_private_output_or_title_leak():
    from recommend_book.usage_guard import UsageDecision,model_hash
    class Guard:
        def check_private_reference_aggregate(self,payload,origins,**kw):
            return UsageDecision(allowed=True,usage='local_storage',ledger_path='synthetic',ledger_sha256='a'*64,
                input_hashes=origins,payload_sha256=model_hash(payload))
    refs=[{'work_id':f'private-{i}','title':f'AB{i//2}','rating':4 if i%2 else 2} for i in range(14)]
    before=deepcopy(refs)
    got=ne.evaluate_owned_titles(refs,guard=Guard(),origin_ids=['synthetic-origin'],spec=ne.owned_title_spec('a'*64))
    assert got['counts']['rated_n']==14 and got['counts']['normalized_title_group_n']==7
    assert all(f['title_overlap_n']==0 for f in got['folds'])
    assert all(len({m['metrics']['eligible_n'] for m in f['methods'].values()})==1 for f in got['folds'])
    assert 'private-' not in str(got) and 'AB' not in str(got) and refs==before
    assert got['methods']['popularity']['status']=='held' and not got['quality_verified']


def test_approved_alias_max_similarity_counts_reference_once():
    got=ne.rank_owned_titles([{'work_id':'w','title':'English','title_aliases':['AB','AB']}],
        [{'work_id':'p','title':'AB','rating':5}])
    assert got['ranking'][0]['score']==2 and got['ranking'][0]['evidence_count']==1
    spec=ne.owned_title_spec('a'*64,title_alias_mode=True)
    assert spec['world_title_rule']=='max primary and individually display-guarded source-bound aliases'
    assert spec['parameter_search_n']==0


def test_detached_allowed_decision_cannot_start_private_computation():
    from recommend_book.usage_guard import UsageDecision
    class Guard:
        def check_private_reference_aggregate(self,*args,**kwargs):
            return UsageDecision(allowed=True,usage='local_storage',ledger_path='synthetic',ledger_sha256='a'*64,
                input_hashes=['other'],payload_sha256='wrong')
    with pytest.raises(ValueError,match='binding'):
        ne.evaluate_owned_titles([{'work_id':'p','title':'AB','rating':5}],guard=Guard(),origin_ids=['owner'],spec=ne.owned_title_spec('a'*64))


def test_fold_ranking_receives_only_train_ratings_and_unrated_candidates(monkeypatch):
    from recommend_book.usage_guard import UsageDecision,model_hash
    class Guard:
        def check_private_reference_aggregate(self,payload,origins,**kw):
            return UsageDecision(allowed=True,usage='local_storage',ledger_path='synthetic',ledger_sha256='a'*64,
                input_hashes=origins,payload_sha256=model_hash(payload))
    original=ne.rank_owned_titles;calls=[]
    def rank(candidates,references,**kw):
        assert all(r['rating'] is None for r in candidates)
        assert not {r['work_id'] for r in candidates}&{r['work_id'] for r in references}
        calls.append(len(candidates));return original(candidates,references,**kw)
    monkeypatch.setattr(ne,'rank_owned_titles',rank)
    rows=[{'work_id':str(i),'title':f'AB{i}','rating':2} for i in range(10)]
    got=ne.evaluate_owned_titles(rows,guard=Guard(),origin_ids=['test'],spec=ne.owned_title_spec('a'*64))
    assert len(calls)==5 and sum(calls)==10
    assert got['methods']['owned_title_bigram']['macro']['recall_at_k'] is None


def test_private_receipt_for_display_is_not_local_arithmetic_permission():
    from recommend_book.usage_guard import UsageDecision,model_hash
    class Guard:
        def check_private_reference_aggregate(self,payload,origins,**kw):
            return UsageDecision(allowed=True,usage='display',ledger_path='synthetic',ledger_sha256='a'*64,
                input_hashes=origins,payload_sha256=model_hash(payload))
    with pytest.raises(ValueError,match='purpose'):
        ne.evaluate_owned_titles([{'work_id':'p','title':'AB','rating':5}],guard=Guard(),origin_ids=['own'],spec=ne.owned_title_spec('a'*64))
