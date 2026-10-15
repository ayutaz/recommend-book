from recommend_book.evaluation import evaluate_results, create_split_manifest, create_review_pack, import_annotations
import json
import pytest
from recommend_book.evaluation.metrics import validate_gold

TAX = {'version':'1', 'labels':[{'label_id':'genre.mystery','axis':'genre','primary':True}]}

INVALID_UNIT_VALUES=[pytest.param(value,id=name) for name,value in (
    ('true',True),('false',False),('nan',float('nan')),('positive_infinity',float('inf')),
    ('negative_infinity',float('-inf')),('string','0.5'),('below_zero',-.01),('above_one',1.01),('huge_integer',10**400))]


@pytest.mark.parametrize('value',INVALID_UNIT_VALUES)
def test_gold_relevance_rejects_bool_nonfinite_nonreal_and_out_of_range(value):
    record=human('a','present');record['labels'][0]['relevance']=value
    with pytest.raises(ValueError,match='relevance'):
        validate_gold(record,{'genre.mystery'})


@pytest.mark.parametrize('value',[0,1,None])
def test_gold_relevance_accepts_closed_endpoints_and_null(value):
    record=human('a','present');record['labels'][0]['relevance']=value
    validate_gold(record,{'genre.mystery'})


@pytest.mark.parametrize('value',INVALID_UNIT_VALUES)
@pytest.mark.parametrize('field',['relevance','raw_confidence','confidence_uncalibrated','confidence'])
@pytest.mark.parametrize('decision',['present','absent','unknown'])
@pytest.mark.parametrize('scope',['empty_gold','unannotated_label'])
def test_invalid_prediction_values_rejected_before_gold_join(value,field,decision,scope):
    taxonomy={'version':'1','labels':TAX['labels']+[{'label_id':'genre.romance','axis':'genre'}]}
    prediction=pred('a','present')
    invalid={'label_id':'genre.romance','decision':decision,'relevance':None}
    invalid[field]=value
    prediction['labels'].append(invalid)
    gold=[] if scope=='empty_gold' else [human('a','present')]
    with pytest.raises(ValueError,match='relevance' if field=='relevance' else 'confidence'):
        evaluate_results([prediction],gold,taxonomy)


@pytest.mark.parametrize('value',[0,1,None])
@pytest.mark.parametrize('field',['relevance','raw_confidence','confidence_uncalibrated','confidence'])
@pytest.mark.parametrize('decision',['present','absent','unknown'])
def test_prediction_unit_endpoints_and_null_accepted_without_gold(value,field,decision):
    prediction={'work_id':'a','labels':[{'label_id':'genre.mystery','decision':decision,'relevance':None,field:value}]}
    report=evaluate_results([prediction],[],TAX)
    assert report['gold_work_count']==0 and report['human']=={}
    assert report['validation_status']=='human_validation_pending'
    assert report['calibration']['status']=='uncalibrated'
    assert report['dataset_coverage']['decided_label_slots']==int(decision!='unknown')


@pytest.mark.parametrize('fields,expected',[
    ({'raw_confidence':.1,'confidence_uncalibrated':True,'confidence':'shadowed'},.1),
    ({'raw_confidence':None,'confidence_uncalibrated':True,'confidence':'shadowed'},None),
    ({'confidence_uncalibrated':.2,'confidence':True},.2),
    ({'confidence_uncalibrated':None,'confidence':True},None),
    ({'confidence':.3},.3)])
def test_confidence_priority_and_explicit_null_do_not_fall_through(fields,expected):
    prediction=pred('a','present');label=prediction['labels'][0]
    label.pop('raw_confidence');label.update(fields)
    report=evaluate_results([prediction],[human('a','present')],TAX)
    diagnostics=report['human']['evidence']['holdout']['raw_confidence_diagnostics']
    assert diagnostics['n']==int(expected is not None)
    if expected is not None:
        assert diagnostics['bins'][0]['confidence']==pytest.approx(expected)


def test_known_null_relevance_remains_known_and_unscored_for_relevance():
    prediction=pred('a','present');prediction['labels'][0]['relevance']=None
    report=evaluate_results([prediction],[human('a','present')],TAX)
    metric=report['human']['evidence']['holdout']
    assert metric['micro']['tp']==1 and metric['coverage']['eligible']==1
    assert metric['relevance']['n']==0 and metric['relevance']['mae'] is None

def pred(work, decision, confidence=.9):
    return {'work_id':work,'model':'test-model','prompt_version':'test-prompt','taxonomy_version':'tax',
        'cli_version':'test-cli','target_language':'en',
        'labels':[{'label_id':'genre.mystery','decision':decision,'raw_confidence':confidence,'relevance':.5}]}

def human(work, decision, **kwargs):
    return {'work_id':work, 'provenance':'human','reviewer_id':'r1','truth_level':'evidence','partition':'holdout','labels':[{'label_id':'genre.mystery','decision':decision,'relevance':.75}], **kwargs}

def test_abstention_is_false_negative_unknown_truth_separate():
    report = evaluate_results([pred('a','unknown'),pred('b','present'),pred('c','present')], [human('a','present'),human('b','present'),human('c','unknown')], TAX)
    metrics = report['human']['evidence']['holdout']
    assert metrics['micro']['recall'] == .5
    assert metrics['micro']['precision'] == 1
    assert metrics['coverage']['eligible'] == .5
    assert metrics['unsupported_assertion']['rate'] == 1
    assert metrics['per_label']['genre.mystery']['status'] == 'insufficient_positive_support'

def test_silver_cannot_validate_human_accuracy():
    report = evaluate_results([pred('a','present')], [{'work_id':'a','provenance':'curated_reference','labels':[{'label_id':'genre.mystery','decision':'present'}]}], TAX)
    assert report['validation_status'] == 'human_validation_pending'
    assert report['human'] == {}
    assert report['calibration']['status'] == 'uncalibrated'
    assert report['reference']['micro']['f1'] == 1

def test_work_truth_requires_documented_knowledge():
    with pytest.raises(ValueError, match='work truth'):
        evaluate_results([pred('a','present')],[human('a','present',truth_level='work')],TAX)

def test_grouped_immutable_split(tmp_path):
    inputs = [{'work_id':str(i),'authors':[f'author{i//2}'],'series':None} for i in range(12)]
    manifest = create_split_manifest(inputs,tmp_path,sizes=(4,4,4),seed=3)
    assert {p:len(v) for p,v in manifest['partitions'].items()} == {'dev':4,'calibration':4,'holdout':4}
    lookup = {w:p for p,works in manifest['partitions'].items() for w in works}
    assert all(lookup[str(i)] == lookup[str(i+1)] for i in range(0,12,2))
    assert create_split_manifest(inputs,tmp_path,sizes=(4,4,4),seed=3) == manifest
    with pytest.raises(ValueError, match='immutable'):
        create_split_manifest(inputs,tmp_path,sizes=(4,4,4),seed=4)

def test_review_import_rejects_unknown_and_forged_annotations(tmp_path):
    pack = create_review_pack([{'work_id':'a','title':'</script><script>alert(1)</script>','authors':[],'evidence':[]}],[pred('a','present')],tmp_path)
    html = (tmp_path/'review.html').read_text('utf-8')
    assert '</script><script>alert(1)</script>' not in html
    path=tmp_path/'annotation.json'
    path.write_text(json.dumps([human('missing','present')]),encoding='utf-8')
    with pytest.raises(ValueError,match='unknown work'):
        import_annotations(path,tmp_path)
    path.write_text(json.dumps([human('a','present')]),encoding='utf-8')
    assert import_annotations(path,tmp_path)[0]['provenance'] == 'human'

def test_confidence_brier_ece_and_dataset_coverage():
    report=evaluate_results([pred('a','present',.9),pred('b','present',.9),{'work_id':'unreviewed','labels':[]}],[human('a','present'),human('b','absent')],TAX)
    m=report['human']['evidence']['holdout']
    assert m['raw_confidence_diagnostics']['ece'] == pytest.approx(.4)
    assert m['raw_confidence_diagnostics']['brier'] == pytest.approx(.41)
    assert m['relevance']['mae'] == .25
    assert report['dataset_coverage']['all_registered'] == pytest.approx(2/3)
    assert m['cluster_bootstrap']['micro_f1_ci95'] is not None

def test_calibration_fits_only_calibration_and_supported_scope():
    predictions=[]; gold=[]
    for i in range(40):
        w=f'cal{i}';predictions.append(pred(w,'present',.9));gold.append(human(w,'present' if i<30 else 'absent',partition='calibration',original_language='en',evidence_kind='synopsis'))
    predictions.append(pred('held','present',.9))
    gold.append(human('held','absent',original_language='en',evidence_kind='synopsis'))
    report=evaluate_results(predictions,gold,TAX)
    model=report['calibration']['models']['evidence']
    assert model['scopes']['genre.mystery|en|synopsis'][0]['probability'] == .75
    assert report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']['brier'] == pytest.approx(.75**2)
    no_training=evaluate_results(predictions,[human(w['work_id'],'present',original_language='en',evidence_kind='synopsis') for w in predictions],TAX)
    assert no_training['calibration']['status'] == 'uncalibrated'


@pytest.mark.parametrize('confidence,expected',[(.5,0),(.49999,1),(1.,0)])
def test_calibration_half_boundary_matches_fit_bins(confidence,expected):
    predictions=[];gold=[]
    for index in range(40):
        work=f'boundary-cal-{index}';low=index<20
        predictions.append(pred(work,'present',.25 if low else .75))
        gold.append(human(work,'present' if low else 'absent',partition='calibration',
            original_language='en',evidence_kind='synopsis',authors=[f'cal-author-{index}']))
    predictions.append(pred('boundary-held','present',confidence))
    gold.append(human('boundary-held','present',original_language='en',evidence_kind='synopsis',authors=['held-author']))
    report=evaluate_results(predictions,gold,TAX)
    bands=report['calibration']['models']['evidence']['scopes']['genre.mystery|en|synopsis']
    assert [(band['lower'],band['upper'],band['n'],band['probability']) for band in bands]==[(0,.5,20,1),(.5,1,20,0)]
    diagnostic=report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']
    assert diagnostic['n']==1 and diagnostic['bins'][0]['confidence']==expected
    assert diagnostic['brier']==(expected-1)**2


@pytest.mark.parametrize('high_count,expected_status',[(20,'uncalibrated'),(21,'calibrated_scopes_available')])
def test_boundary_fix_keeps_insufficient_scope_or_band_uncalibrated(high_count,expected_status):
    predictions=[];gold=[]
    for index in range(19+high_count):
        work=f'insufficient-cal-{index}';low=index<19
        predictions.append(pred(work,'present',.25 if low else .75))
        gold.append(human(work,'present' if low else 'absent',partition='calibration',original_language='en',evidence_kind='synopsis'))
    predictions.append(pred('insufficient-held','present',.49999))
    gold.append(human('insufficient-held','present',original_language='en',evidence_kind='synopsis'))
    report=evaluate_results(predictions,gold,TAX)
    assert report['calibration']['status']==expected_status
    diagnostic=report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']
    assert diagnostic['n']==0 and diagnostic['brier'] is None and diagnostic['ece'] is None


@pytest.mark.parametrize('field',['model','prompt_version','taxonomy_version','cli_version','target_language'])
@pytest.mark.parametrize('invalid',['missing','empty','none','whitespace','nonstring','list','mapping'])
def test_incomplete_calibration_context_never_fits_even_with_human_support(field,invalid):
    predictions=[];gold=[]
    for index in range(40):
        work=f'missing-context-{index}';prediction=pred(work,'present')
        if invalid=='missing':
            prediction.pop(field)
        else:
            prediction[field]={'empty':'','none':None,'whitespace':'   ','nonstring':0,'list':[],'mapping':{}}[invalid]
        predictions.append(prediction)
        gold.append(human(work,'present',partition='calibration',original_language='en',evidence_kind='synopsis'))
    predictions.append(pred('complete-context-held','present'))
    gold.append(human('complete-context-held','present',original_language='en',evidence_kind='synopsis'))
    report=evaluate_results(predictions,gold,TAX)
    assert report['calibration']['status']=='uncalibrated'
    assert report['calibration']['models']['evidence']['scopes']=={}
    assert report['human']['evidence']['calibration']['micro']['precision']==1
    assert report['human']['evidence']['holdout']['micro']['precision']==1
    assert report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']['n']==0


@pytest.mark.parametrize('field',['model','prompt_version','taxonomy_version','cli_version','target_language'])
def test_context_mismatch_prevents_holdout_calibration_for_each_execution_field(field):
    predictions=[pred(f'context-cal-{index}','present') for index in range(40)]
    gold=[human(item['work_id'],'present',partition='calibration',original_language='en',evidence_kind='synopsis') for item in predictions]
    held=pred('context-held','present');held[field]='different-complete-context'
    predictions.append(held);gold.append(human('context-held','present',original_language='en',evidence_kind='synopsis'))
    report=evaluate_results(predictions,gold,TAX)
    assert report['calibration']['status']=='calibrated_scopes_available'
    assert report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']['n']==0

def test_undecidable_absent_gold_no_f1_can_render(tmp_path):
    from recommend_book.evaluation import write_report
    report=evaluate_results([pred('a','absent'),pred('b','absent')],[human('a','absent'),human('b','absent')],TAX)
    assert report['human']['evidence']['holdout']['micro']['f1'] is None
    write_report(report,tmp_path)
    assert (tmp_path/'report.json').exists()

def test_resolution_pair_unknown_prediction_and_candidate_recall():
    from recommend_book.evaluation import evaluate_resolution_pairs
    gold=[{'left_id':'a','right_id':'b','decision':'same','provenance':'human','reviewer_id':'r'},{'left_id':'a','right_id':'c','decision':'different','provenance':'human','reviewer_id':'r'}]
    report=evaluate_resolution_pairs([{'left_id':'a','right_id':'b','decision':'uncertain'},{'left_id':'a','right_id':'c','decision':'same'}],gold,candidates=[('a','c')])
    assert report['merge']['fn']==1
    assert report['merge']['fp']==1
    assert report['candidate_recall']==0
    assert report['planned_size_met'] is False

def test_author_group_cross_partition_is_rejected():
    with pytest.raises(ValueError,match='leakage'):
        evaluate_results([pred('a','present'),pred('b','present')],[human('a','present',authors=['Same author'],partition='calibration'),human('b','present',authors=['Same author'],partition='holdout')],TAX)

def test_jsonl_line_error_is_actionable(tmp_path):
    create_review_pack([{'work_id':'a','title':'A'}],[pred('a','present')],tmp_path)
    path=tmp_path/'bad.jsonl';path.write_text('\n{notjson}\n',encoding='utf-8')
    with pytest.raises(ValueError,match='line 2'):
        import_annotations(path,tmp_path)

def test_import_uses_exact_allowed_evidence_scope(tmp_path):
    evidence=[{'evidence_id':'allowed','kind':'synopsis','text':'A','llm_input_allowed':True},{'evidence_id':'hidden','kind':'excerpt','text':'B','llm_input_allowed':False}]
    create_review_pack([{'work_id':'a','title':'A','evidence':evidence}],[pred('a','present')],tmp_path)
    record=human('a','present');record['labels'][0]['evidence_ids']=['hidden']
    path=tmp_path/'annotations.json';path.write_text(json.dumps([record]),encoding='utf-8')
    with pytest.raises(ValueError,match='unknown evidence'):
        import_annotations(path,tmp_path)
    record['labels'][0]['evidence_ids']=['allowed'];path.write_text(json.dumps([record]),encoding='utf-8')
    assert import_annotations(path,tmp_path)[0]['evidence_kind']=='synopsis'

def test_model_change_does_not_reuse_calibration():
    predictions=[dict(pred(str(i),'present'),model='old') for i in range(40)]+[dict(pred('new','present'),model='new')]
    gold=[human(str(i),'present',partition='calibration',original_language='en',evidence_kind='synopsis') for i in range(40)]+[human('new','present',original_language='en',evidence_kind='synopsis')]
    report=evaluate_results(predictions,gold,TAX)
    assert report['calibration']['status']=='calibrated_scopes_available'
    assert report['human']['evidence']['holdout']['calibrated_confidence_diagnostics']['n']==0

def test_synthetic_ui_pack_never_imports_as_human_gold(tmp_path):
    from recommend_book.evaluation import evaluate_results
    create_review_pack([{'work_id':'a','title':'UI test'}],[pred('a','present')],tmp_path,annotation_mode='synthetic_ui_test')
    path=tmp_path/'synthetic.json';record=human('a','present');record['provenance']='synthetic_ui_test'
    path.write_text(json.dumps([record]),encoding='utf-8')
    with pytest.raises(ValueError,match='synthetic'):
        import_annotations(path,tmp_path)
    imported=import_annotations(path,tmp_path,allow_synthetic=True)
    assert imported[0]['provenance']=='synthetic_ui_test'
    with pytest.raises(ValueError,match='provenance'):
        evaluate_results([pred('a','present')],imported,TAX)
    record['provenance']='human';path.write_text(json.dumps([record]),encoding='utf-8')
    with pytest.raises(ValueError,match='synthetic'):
        import_annotations(path,tmp_path,allow_synthetic=True)
