"""Synthetic fixtures only; no new human ratings or taxonomy gold."""
from copy import deepcopy
import json
import importlib.util
from pathlib import Path
import pytest

from recommend_book.recommendation import autonomous_evaluation as ae


def row(work, rating=None, **updates):
    return {'work_id':work,'title':'synthetic','rating':rating,'features':{'f':1.},
        'author_ids':[],'series_ids':[],'author_group_keys':[], 'external_ids':[],
        'feature_provenance':{'f':[{'source':'synthetic'}]},'read_status':'unknown',
        'popularity':None,**updates}


def test_registration_is_fixed_and_all_four_families():
    settings=ae.default_settings()
    assert len(settings)<=12
    assert {s['family'] for s in settings}=={'personal','popularity','hybrid_rank','personal_tie_robust'}
    assert ae.preregistration()['settings']==settings


def test_tie_robust_includes_entire_cutoff_and_is_rename_invariant():
    refs=[row(f'r{i:02}',5 if i<20 else 1) for i in range(21)]
    setting={'id':'t20','family':'personal_tie_robust','neighbors':20,'prior':.1}
    result=ae.rank_with_setting([row('c')],refs,setting)
    prediction=result['ranking'][0]
    assert len(prediction['explanations'])==21
    assert prediction['score']==pytest.approx(sum(r['rating'] for r in refs)/21)
    renamed=[{**r,'work_id':f'z{20-i:02}'} for i,r in enumerate(refs)]
    assert ae.rank_with_setting([row('c')],renamed,setting)['ranking'][0]['score']==pytest.approx(prediction['score'])
    assert prediction['read_status']=='unknown' and prediction['evidence_count']>0


def test_hybrid_uses_midrank_missing_zero_and_preserves_unsupported():
    setting={'id':'h','family':'hybrid_rank','personal_weight':.5,'missing_component':'zero_fixed_weight'}
    candidates=[row('a',popularity=100),row('b',popularity=10),row('z',features={})]
    result=ae.rank_with_setting(candidates,[row('r',5)],setting)
    by={r['work_id']:r for r in result['ranking']}
    # Whole eligible N=3, personal midrank=1.5; missing stays zero.
    assert by['a']['score']==pytest.approx(11/12)
    assert by['b']['score']==pytest.approx(3/4)
    assert by['z']['score'] is None and by['z']['score_status']=='unsupported'
    assert len(result['audit']['eligible_work_ids'])==3


def test_nested_scrubs_holdout_train_only_same_pool_and_private_report(monkeypatch):
    calls=[]
    original=ae.rank_with_setting
    def spy(candidates,references,setting,**kw):
        assert all(c['rating'] is None and c['read_status']=='unknown' for c in candidates)
        assert not ({c['work_id'] for c in candidates}&{r['work_id'] for r in references})
        calls.append((deepcopy(candidates),deepcopy(references)))
        return original(candidates,references,setting,**kw)
    monkeypatch.setattr(ae,'rank_with_setting',spy)
    rows=[row(f'private-{i}',(i%5)+1,popularity=i,read_status='read') for i in range(15)]
    before=deepcopy(rows)
    report=ae.evaluate_nested(rows,bootstrap_replicates=20)
    assert calls and rows==before
    assert report['counts']['rated_work_n']==15
    assert len(report['outer_folds'])==5
    assert all(f['identity_overlap_n']==0 and f['same_pool'] for f in report['outer_folds'])
    assert all(len(f['inner_folds'])==3 for f in report['outer_folds'])
    assert set(report['candidate_settings'])=={s['id'] for s in ae.default_settings()}
    assert 'private-' not in json.dumps(report) and 'synthetic' not in json.dumps(report)
    assert report['limits']['fresh_holdout'] is False
    assert report['uncertainty']['independent_fold_trials'] is False
    assert report==ae.evaluate_nested(list(reversed(rows)),bootstrap_replicates=20)


def test_transitive_unrated_alias_bridge_kept_whole():
    rows=[row(f'w{i}',5) for i in range(15)]
    rows[0]['author_ids']=['A'];rows[1]['series_ids']=['S']
    rows.append(row('bridge',author_ids=['A'],series_ids=['S']))
    rows[2]['external_ids']=[{'source':'fixture','value':'alias'}]
    rows[3]['external_ids']=[{'source':'fixture','value':'alias'}]
    report=ae.evaluate_nested(rows,bootstrap_replicates=5)
    assert report['counts']['group_n']==13
    assert all(f['identity_overlap_n']==0 for f in report['outer_folds'])


def test_insufficient_groups_fails_instead_of_splitting():
    with pytest.raises(ValueError,match='groups'):
        ae.evaluate_nested([row(f'w{i}',5,author_ids=['same']) for i in range(15)])


def test_selection_only_inner_metrics_and_zero_positive_defined():
    settings=ae.default_settings()[:2]
    scored={s['id']:[{'metrics':{'ndcg_at_k':.5},'coverage':{'supported_candidate_rate':1}}] for s in settings}
    assert ae.select_setting(scored,settings)['id']==settings[0]['id']
    assert ae._summarize_ranking([{'work_id':'x','score':None,'score_status':'unsupported','evidence_count':0}],{'x':1},10)['metrics']['recall_at_k'] is None


def test_outer_truth_change_cannot_change_that_folds_inner_selection():
    rows=[row(f'w{i}',i%5+1,popularity=i) for i in range(15)]
    split=ae._split(ae._normalize(rows),5,20261013)
    held=set(split['folds'][0]['heldout_work_ids'])
    changed=[{**r,'rating':6-r['rating']} if r['work_id'] in held else r for r in rows]
    first=ae.evaluate_nested(rows,bootstrap_replicates=5)['outer_folds'][0]
    second=ae.evaluate_nested(changed,bootstrap_replicates=5)['outer_folds'][0]
    assert first['selected_setting']==second['selected_setting']
    assert first['inner_folds']==second['inner_folds']


def test_group_bootstrap_is_paired_conditional_not_independent_fold_trial():
    rows=[row(f'w{i}',5,popularity=i) for i in range(15)]
    interval=ae.evaluate_nested(rows,bootstrap_replicates=20)['uncertainty']
    assert interval['ndcg_difference_selected_minus_popularity_95_interval']==[0,0]
    assert interval['defined_replicates']==20
    assert not interval['independent_fold_trials'] and not interval['retraining_uncertainty_estimated']


def test_all_families_preserve_reference_alias_read_unknown_eligibility():
    candidates=[row('read',read_status='read'),row('rated',4),row('alias',external_ids=[{'source':'f','value':'x'}]),row('unknown')]
    refs=[row('reference',5,external_ids=[{'source':'f','value':'x'}])]
    for setting in ae.default_settings():
        result=ae.rank_with_setting(candidates,refs,setting)
        assert result['audit']['eligible_work_ids']==['unknown']
        assert result['ranking'][0]['read_status']=='unknown'
        assert result['ranking'][0]['rating'] is None


def script_module():
    path=Path(__file__).resolve().parents[1]/'scripts/evaluate_autonomous_recommendations.py'
    spec=importlib.util.spec_from_file_location('autonomous_script_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_preregistration_separate_invocation_and_source_tamper_rejected(tmp_path,monkeypatch):
    module=script_module();monkeypatch.setattr(module,'ROOT',tmp_path)
    contract=tmp_path/'contract.json';baseline=tmp_path/'baseline.json'
    sources=[tmp_path/name for name in ('database','annotations','bridge')]
    for path in sources:path.write_text('synthetic',encoding='utf-8')
    contract.write_text(json.dumps({'recommendation':{'outer_folds':5,'inner_folds':3,'outer_seed':20261013,
        'inner_seed':20261014,'max_candidate_settings':12,'method_families':list({s['family'] for s in ae.default_settings()}),
        'dataset_previously_observed':True,'fresh_holdout_claim':False}}),encoding='utf-8')
    baseline.write_text(json.dumps({'artifacts':[{'path':p.name,'sha256':module.sha(p)} for p in [contract,*sources]]}),encoding='utf-8')
    options=['--database',str(sources[0]),'--annotations',str(sources[1]),'--bridge',str(sources[2]),
        '--contract',str(contract),'--baseline',str(baseline),'--output-dir',str(tmp_path/'output')]
    def forbidden(*a,**kw):raise AssertionError('Adapter must not run before completed preregistration')
    monkeypatch.setattr(module,'load_narou_reference',forbidden)
    with pytest.raises(ValueError,match='register-only'):module.main(options)
    assert module.main([*options,'--register-only'])==0
    saved=(tmp_path/'output/preregistration.json').read_bytes()
    assert module.main([*options,'--register-only'])==0
    assert (tmp_path/'output/preregistration.json').read_bytes()==saved
    sources[0].write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='frozen baseline'):module.main(options)


def test_immutable_outputs_cannot_silently_change(tmp_path):
    module=script_module();path=tmp_path/'file.json'
    module.write_immutable(path,{'status':'synthetic'})
    original=path.read_bytes()
    module.write_immutable(path,{'status':'synthetic'})
    with pytest.raises(ValueError,match='differs'):module.write_immutable(path,{'status':'changed'})
    assert path.read_bytes()==original


@pytest.mark.parametrize('setting',[
    {'id':'x','family':'hybrid_rank','personal_weight':True},
    {'id':'x','family':'hybrid_rank','personal_weight':1.1},
    {'id':'x','family':'personal_tie_robust','neighbors':False,'prior':.1},
    {'id':'x','family':'personal_tie_robust','neighbors':20,'prior':float('nan')},
    {'id':'x','family':'unknown'},
])
def test_invalid_settings_rejected(setting):
    with pytest.raises(ValueError):
        ae.rank_with_setting([row('c')],[row('r',5)],setting)
