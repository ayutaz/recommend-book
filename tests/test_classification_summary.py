"""Observation fixtures are structural tests, never human gold."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.validation import input_hash, load_taxonomy, target_language
from recommend_book.classification import validation as current_validation
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import Evidence, WorkInput

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('classification_summary',ROOT/'scripts/summarize_classification_run.py')
summary=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


def test_imports_only_scratch_validator_and_observer():
    assert Path(current_validation.__file__).resolve().is_relative_to(ROOT)
    assert current_validation.ROOT.resolve()==ROOT
    assert Path(summary.__file__).resolve().is_relative_to(ROOT)


def test_relevance_numeric_and_null_are_separate_from_presence_and_all_slots():
    key=('fixture',None);other=('empty',None)
    inputs={key:{'evidence':[]},other:{'evidence':[]}}
    labels=[{'label_id':'genre.mystery','decision':'present','relevance':None,'raw_confidence':.9,'evidence':[]},
        {'label_id':'genre.romance','decision':'absent','relevance':.25,'raw_confidence':.8,'evidence':[]},
        {'label_id':'genre.horror','decision':'unknown','relevance':None,'raw_confidence':None,'evidence':[]}]
    results={key:{'labels':labels,'missing_axes':[],'needs_human_review':True},other:{'labels':[],'missing_axes':[],'needs_human_review':True}}
    stats=summary._stats([key,other],inputs,results,load_taxonomy())
    assert stats['decided_label_slots']==2 and stats['taxonomy_label_slots']==144
    assert stats['relevance']['n_known']==2
    assert stats['relevance']['n_numeric']==1 and stats['relevance']['n_null']==1
    assert stats['relevance']['numeric_rate_among_known']==.5
    assert stats['relevance']['null_rate_among_known']==.5
    assert stats['relevance']['numeric_rate_all_taxonomy_slots']==pytest.approx(1/144)
    assert stats['relevance']['null_rate_all_taxonomy_slots']==pytest.approx(1/144)
    assert stats['relevance']['mean']==.25
    assert stats['relevance']['mean']!=.125


def _retag_config(run, updates, remove=()):
    path=run/'manifest.json';manifest=json.loads(path.read_text('utf-8'))
    config=manifest['config'];config.update(updates)
    for field in remove:
        config.pop(field,None)
    jobs={}
    for job in manifest['jobs'].values():
        job['config']=config;job['key']=job_key(job['input_hash'],config);jobs[job['key']]=job
    manifest['jobs']=jobs
    path.write_text(json.dumps(manifest),encoding='utf-8')


@pytest.mark.parametrize('relevance',[None,0])
def test_known_null_and_numeric_zero_relevance_preserve_presence_coverage(completed_run,tmp_path,relevance):
    path,run=completed_run
    rows=[json.loads(line) for line in (run/'results.jsonl').read_text('utf-8').splitlines()]
    rows[0]['labels'][0]['relevance']=relevance
    (run/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
    manifest=json.loads((run/'manifest.json').read_text('utf-8'))
    job=next(job for job in manifest['jobs'].values() if job['work_id']=='a')
    (run/job['result_path']).write_text(json.dumps(rows[0]),encoding='utf-8')
    before={file:file.read_bytes() for file in (path,run/'results.jsonl',run/'manifest.json',run/job['result_path'])}
    report=summary.summarize_run(path,run)
    assert report['overall']['decided_work_coverage']==.5
    assert report['overall']['decided_label_slots']==1
    observed=report['overall']['relevance']
    assert observed['n_known']==1 and observed['n_numeric']==int(relevance is not None)
    assert observed['n_null']==int(relevance is None)
    assert observed['mean']==relevance
    axis=report['overall']['axes']['genre']['relevance']
    assert (axis['n_known'],axis['n_numeric'],axis['n_null'],axis['mean'])==(observed['n_known'],observed['n_numeric'],observed['n_null'],observed['mean'])
    assert axis['numeric_rate_all_taxonomy_slots']==pytest.approx(int(relevance is not None)/24)
    assert report['by_original_language']['ja']['relevance']['n_known']==0
    assert report['by_original_language']['ja']['relevance']['numeric_rate_among_known'] is None
    assert report['quality']['relevance_accuracy'] is None and report['quality']['human_gold_count']==0
    assert all(file.read_bytes()==data for file,data in before.items())
    artifact=summary.write_summary(report,tmp_path/'relevance-observation')
    page=Path(artifact['html']).read_text('utf-8')
    assert '関連度数値あり / known' in page and '関連度null / known' in page


def test_legacy_config_is_distinct_from_current_read_only_revalidation(completed_run):
    path,run=completed_run
    report=summary.summarize_run(path,run)
    original=report['provenance']['original_run_validation']
    actual=report['provenance']['observation_revalidation']
    assert original['status']=='unrecorded_legacy_policy'
    assert original['validation_hash'] is None and original['validation_version'] is None
    assert original['original_execution_policy_independently_verified'] is False
    assert actual['validation_version']==current_validation.VALIDATION_VERSION
    assert actual['validation_hash']==hashlib.sha256(Path(current_validation.__file__).read_bytes()).hexdigest()
    assert actual['validated_result_count']==2 and actual['status']=='validated_now_read_only'
    assert 'validation_hash' not in report['provenance']['config']


@pytest.mark.parametrize('freeze_field',['work_inputs_sha256','pilot_inputs_sha256','ordered_inputs_sha256'])
def test_freeze_input_fields_accept_exact_bytes_and_reject_changed_bytes(completed_run,freeze_field):
    path,run=completed_run
    frozen={'work_count':2,freeze_field:hashlib.sha256(path.read_bytes()).hexdigest()}
    (path.parent/'manifest.json').write_text(json.dumps(frozen),encoding='utf-8')
    report=summary.summarize_run(path,run)
    assert report['provenance']['frozen_manifest']['matched_hash_fields']==[freeze_field]
    assert report['provenance']['original_run_validation']['status']=='unrecorded_legacy_policy'
    assert report['provenance']['observation_revalidation']['validation_version']==current_validation.VALIDATION_VERSION
    # A blank JSONL line preserves WorkInput values/job keys, but changes the
    # exact frozen file bytes; rejection must therefore come from SHA matching.
    path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='Input file SHA does not match frozen manifest'):
        summary.summarize_run(path,run)


@pytest.mark.parametrize('policy',['matching','bad_hash','bad_version','partial'])
def test_recorded_validation_policy_must_match_actual_scratch_artifact(completed_run,policy):
    path,run=completed_run
    fields={'validation_version':current_validation.VALIDATION_VERSION,
        'validation_hash':hashlib.sha256(Path(current_validation.__file__).read_bytes()).hexdigest()}
    if policy=='bad_hash':
        fields['validation_hash']='0'*64
    elif policy=='bad_version':
        fields['validation_version']='1.0'
    elif policy=='partial':
        fields.pop('validation_hash')
    _retag_config(run,fields)
    if policy=='matching':
        report=summary.summarize_run(path,run)
        assert report['provenance']['original_run_validation']['status']=='recorded_artifacts_match_current'
        assert report['provenance']['observation_revalidation']['validation_hash']==fields['validation_hash']
    else:
        with pytest.raises(ValueError,match='validation version/hash'):
            summary.summarize_run(path,run)


@pytest.fixture
def completed_run(tmp_path):
    inputs=[WorkInput(work_id='a',title='<script>unsafe</script>',original_language='en',
        evidence=[Evidence(evidence_id='e1',kind='synopsis',text='A detective solves a crime.',locator='summary',
            source_url='https://example.org/a',rights_policy_id='fixture',llm_input_allowed=True),
            Evidence(evidence_id='secret',kind='note',text='Denied note',source_url='https://example.org',rights_policy_id='fixture')]),
        WorkInput(work_id='b',title='Unknown metadata',original_language='ja',
            evidence=[Evidence(evidence_id='e2',kind='bibliographic',text='Title only',source_url='https://example.org/b',rights_policy_id='fixture',llm_input_allowed=True)])]
    taxonomy=load_taxonomy()
    config={'model':'test-fixture','cli_version':'test-fixture','taxonomy_version':taxonomy['version'],'prompt_version':'1.0',
        'taxonomy_hash':hashlib.sha256(canonical_json(taxonomy).encode()).hexdigest(),
        'schema_hash':hashlib.sha256((ROOT/'schemas/classification.schema.json').read_bytes()).hexdigest(),
        'prompt_hash':hashlib.sha256((ROOT/'src/recommend_book/classification/prompt.py').read_bytes()).hexdigest(),
        'sandbox':'read-only','reasoning_effort':'medium','web_search':'disabled','batch_size':1}
    label={'label_id':'genre.mystery','axis':'genre','decision':'present','scope':'work','expression_id':None,
        'relevance':1,'raw_confidence':.9,'calibrated_confidence':None,'calibration_status':'uncalibrated','calibration_version':None,
        'evidence':[{'evidence_id':'e1','locator':'summary','quote':'detective solves a crime','summary':None,'directness':'direct'}],
        'missing_reason':None,'spoiler':'premise'}
    unknown={**label,'label_id':'genre.horror','decision':'unknown','relevance':None,'raw_confidence':None,'evidence':[],'missing_reason':'Insufficient input'}
    results=[];jobs={}
    run=tmp_path/'run';run.mkdir();(run/'results').mkdir()
    for index,work in enumerate(inputs):
        result={'work_id':work.work_id,'expression_id':None,'target_language':target_language(work),'taxonomy_version':taxonomy['version'],
            'prompt_version':'1.0','model':'test-fixture','cli_version':'test-fixture','input_hash':input_hash(work),
            'labels':[label if index==0 else unknown],
            'missing_axes':[{'axis':axis['axis_id'],'decision':'unknown','reason':'No supported evidence'} for axis in taxonomy['axes'] if index or axis['axis_id']!='genre'],
            'conflicts':[],'needs_human_review':True}
        results.append(result)
        key=job_key(input_hash(work),config)
        relative=f'results/{key}.json'
        (run/relative).write_text(json.dumps(result),encoding='utf-8')
        jobs[key]={'key':key,'work_id':work.work_id,'expression_id':None,'status':'completed','input_hash':input_hash(work),'config':config,'result_path':relative}
    path=tmp_path/'pilot.jsonl';path.write_text(''.join(work.model_dump_json()+'\n' for work in inputs),encoding='utf-8')
    (run/'results.jsonl').write_text(''.join(json.dumps(result)+'\n' for result in results),encoding='utf-8')
    manifest={'run_id':'test-fixture','config':config,'selected':2,'completed':2,'failed':0,'pending':0,'stopped':False,'jobs':jobs,'human_quality_verified':False}
    (run/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return path,run


def test_unknown_and_unreported_slots_are_separate_and_no_accuracy_is_invented(completed_run,tmp_path):
    path,run=completed_run
    report=summary.summarize_run(path,run)
    assert report['overall']['input_count']==2
    assert report['overall']['decided_work_coverage']==.5
    assert report['overall']['taxonomy_label_slots']==144
    assert report['overall']['explicit_unknown_labels']==1
    assert report['overall']['unreported_label_slots']==142
    assert report['overall']['axes']['genre']['decided_work_coverage']==.5
    assert report['overall']['axes']['style']['unknown_axis_count']==2
    assert set(report['by_evidence_kind'])=={'synopsis','bibliographic'}
    assert report['by_evidence_depth']['synopsis']['input_count']==1
    assert report['overall']['raw_confidence']['mean']==.9
    assert report['overall']['evidence_references']['quote_matched_count']==1
    assert report['quality']['semantic_correctness'] is None
    assert report['quality']['precision'] is None
    assert report['quality']['calibration'] is None
    assert report['quality']['human_gold_count']==0
    artifact=summary.write_summary(report,tmp_path/'observation')
    html=Path(artifact['html']).read_text('utf-8')
    assert '<script>unsafe</script>' not in html
    assert 'human_validation_pending' in html


@pytest.mark.parametrize('corruption',['input_hash','missing_result','incomplete','durable_mismatch','job_config','swapped_job_work'])
def test_inconsistent_or_unfinished_run_is_rejected(completed_run,corruption):
    path,run=completed_run
    manifest=json.loads((run/'manifest.json').read_text('utf-8'))
    if corruption=='input_hash':
        rows=path.read_text('utf-8').splitlines();row=json.loads(rows[0]);row['title']='Changed fixed snapshot';rows[0]=json.dumps(row);path.write_text('\n'.join(rows)+'\n',encoding='utf-8')
    elif corruption=='missing_result':
        (run/'results.jsonl').write_text((run/'results.jsonl').read_text('utf-8').splitlines()[0]+'\n',encoding='utf-8')
    elif corruption=='incomplete':
        manifest['pending']=1
    elif corruption=='durable_mismatch':
        job=next(iter(manifest['jobs'].values()));result=json.loads((run/job['result_path']).read_text('utf-8'));result['needs_human_review']=False;(run/job['result_path']).write_text(json.dumps(result),encoding='utf-8')
    elif corruption=='job_config':
        next(iter(manifest['jobs'].values()))['config']['model']='Different job config'
    else:
        jobs=list(manifest['jobs'].values())
        jobs[0]['work_id'],jobs[1]['work_id']=jobs[1]['work_id'],jobs[0]['work_id']
    (run/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError):
        summary.summarize_run(path,run)


def test_summary_only_citation_is_counted_but_never_semantically_validated(completed_run):
    path,run=completed_run
    result_rows=[json.loads(line) for line in (run/'results.jsonl').read_text('utf-8').splitlines()]
    ref=result_rows[0]['labels'][0]['evidence'][0];ref['quote']=None;ref['summary']='This is a summary whose faithfulness remains for human review.'
    (run/'results.jsonl').write_text(''.join(json.dumps(result)+'\n' for result in result_rows),encoding='utf-8')
    manifest=json.loads((run/'manifest.json').read_text('utf-8'))
    job=next(job for job in manifest['jobs'].values() if job['work_id']=='a')
    (run/job['result_path']).write_text(json.dumps(result_rows[0]),encoding='utf-8')
    report=summary.summarize_run(path,run)
    refs=report['overall']['evidence_references']
    assert refs['summary_count']==1 and refs['summary_only_count']==1
    assert refs['quote_matched_count']==0
    assert report['quality']['summary_faithfulness'] is None
