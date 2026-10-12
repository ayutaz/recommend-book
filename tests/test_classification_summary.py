"""Observation fixtures are structural tests, never human gold."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.validation import input_hash, load_taxonomy, target_language
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import Evidence, WorkInput

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('classification_summary',ROOT/'scripts/summarize_classification_run.py')
summary=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(summary)


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
