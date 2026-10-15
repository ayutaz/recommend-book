"""Synthetic structural fixtures for stability; never pseudo gold."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.validation import input_hash, load_taxonomy, target_language
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import Evidence, WorkInput

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('stability',ROOT/'scripts/compare_classification_stability.py')
stability=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(stability)


@pytest.fixture
def runs(tmp_path):
    taxonomy=load_taxonomy()
    config={'model':'test-fixture','cli_version':'test-fixture','taxonomy_version':taxonomy['version'],'prompt_version':'1.0',
        'taxonomy_hash':hashlib.sha256(canonical_json(taxonomy).encode()).hexdigest(),
        'schema_hash':hashlib.sha256((ROOT/'schemas/classification.schema.json').read_bytes()).hexdigest(),
        'prompt_hash':hashlib.sha256((ROOT/'src/recommend_book/classification/prompt.py').read_bytes()).hexdigest(),
        'sandbox':'read-only','reasoning_effort':'medium','web_search':'disabled','batch_size':1}
    works=[WorkInput(work_id=name,title='<script>fixture</script>',original_language=language,
        evidence=[Evidence(evidence_id='source',kind='synopsis' if name=='a' else 'bibliographic',text='Fixture source text.',locator='source',
            source_url='https://example.org',rights_policy_id='fixture',llm_input_allowed=True)]) for name,language in [('a','en'),('b','ja'),('c','fr')]]
    path=tmp_path/'inputs.jsonl';path.write_text(''.join(work.model_dump_json()+'\n' for work in works),encoding='utf-8')
    def label(label_id,decision,confidence=None):
        return {'label_id':label_id,'axis':'genre','decision':decision,'scope':'work','expression_id':None,
            'relevance':None if decision=='unknown' else .5,'raw_confidence':confidence,
            'calibrated_confidence':None,'calibration_status':'uncalibrated','calibration_version':None,
            'evidence':[] if decision=='unknown' else [{'evidence_id':'source','locator':'source','quote':'Fixture source','summary':None,'directness':'direct'}],
            'missing_reason':'Not known' if decision=='unknown' else None,'spoiler':'none'}
    profiles={'2':[[label('genre.mystery','present',.9),label('genre.romance','absent',.8),label('genre.horror','unknown')],
                    [label('genre.fantasy','present',.6)],[]],
              '4':[[label('genre.mystery','present',.8),label('genre.romance','present',.7),label('genre.fantasy','unknown')],[],[]]}
    directories={}
    for name,profile in profiles.items():
        directory=tmp_path/f'run-{name}';directory.mkdir();(directory/'results').mkdir();directories[name]=directory
        rows=[];jobs={}
        for work,labels in zip(works,profile):
            known_axes={item['axis'] for item in labels if item['decision']!='unknown'}
            result={'work_id':work.work_id,'expression_id':None,'target_language':target_language(work),
                'taxonomy_version':taxonomy['version'],'prompt_version':'1.0','model':'test-fixture','cli_version':'test-fixture',
                'input_hash':input_hash(work),'labels':labels,'missing_axes':[{'axis':axis['axis_id'],'decision':'unknown','reason':'Fixture missing'} for axis in taxonomy['axes'] if axis['axis_id'] not in known_axes],
                'conflicts':[],'needs_human_review':True}
            rows.append(result);key=job_key(input_hash(work),config);relative=f'results/{key}.json'
            (directory/relative).write_text(json.dumps(result),encoding='utf-8')
            jobs[key]={'key':key,'work_id':work.work_id,'expression_id':None,'input_hash':input_hash(work),'status':'completed','config':config,'result_path':relative}
        (directory/'results.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
        (directory/'manifest.json').write_text(json.dumps({'run_id':f'fixture-{name}','config':config,'selected':3,'completed':3,'pending':0,'failed':0,'stopped':False,'jobs':jobs,'human_quality_verified':False}),encoding='utf-8')
    return path,directories


def test_known_union_excludes_unreported_inflation_and_retains_four_states(runs,tmp_path):
    path,directories=runs
    report=stability.compare_runs(path,directories)
    pair=report['pairs']['2__4']['overall']
    assert pair['registered_label_slots']==216
    assert pair['known_union_slots']==3 and pair['both_known_slots']==2
    assert pair['same_known_state_slots']==1
    assert pair['known_union_agreement_rate']==pytest.approx(1/3)
    assert pair['both_known_agreement_rate']==.5
    assert pair['present_absent_flip_count']==1
    assert pair['known_to_unreported_count']==1
    assert pair['state_transitions']['explicit_unknown->unreported']==1
    assert pair['state_transitions']['unreported->explicit_unknown']==1
    assert pair['eligible_known_set_inputs']==2 and pair['both_empty_known_set_inputs']==1
    assert pair['all_both_known_confidence']['mean_absolute_delta']==pytest.approx(.1)
    assert 'all_slot_accuracy' not in pair and 'accuracy' not in pair
    assert report['quality']['human_gold_count']==0
    assert report['quality']['precision'] is None and report['quality']['calibration'] is None
    assert report['pairs']['2__4']['by_original_language']['ja']['known_union_slots']==1
    assert report['pairs']['2__4']['by_evidence_depth']['facts']['known_union_slots']==1
    artifacts=stability.write_comparison(report,tmp_path/'report')
    assert '<script>fixture</script>' not in Path(artifacts['html']).read_text('utf-8')


def test_completed_run_is_required(runs):
    path,directories=runs
    manifest=directories['4']/'manifest.json';row=json.loads(manifest.read_text('utf-8'));row['pending']=1;manifest.write_text(json.dumps(row),encoding='utf-8')
    with pytest.raises(ValueError,match='completed'):
        stability.compare_runs(path,directories)


def test_individually_valid_runs_with_different_config_cannot_be_compared(runs):
    path,directories=runs
    manifest=directories['4']/'manifest.json'
    row=json.loads(manifest.read_text('utf-8'))
    row['config']['batch_size']=2
    jobs={}
    for job in row['jobs'].values():
        job['config']=row['config']
        job['key']=job_key(job['input_hash'],row['config'])
        jobs[job['key']]=job
    row['jobs']=jobs
    manifest.write_text(json.dumps(row),encoding='utf-8')
    with pytest.raises(ValueError,match='identical fixed input and run configuration hashes'):
        stability.compare_runs(path,directories)


def test_all_neither_known_has_no_agreement_denominator():
    taxonomy=load_taxonomy()
    missing=[{'axis':axis['axis_id']} for axis in taxonomy['axes']]
    key=('fixture',None)
    left={key:{'labels':[{'label_id':'genre.mystery','decision':'unknown','raw_confidence':None}],'missing_axes':missing}}
    right={key:{'labels':[],'missing_axes':missing}}
    report=stability._pair_stats([key],left,right,taxonomy)
    assert report['known_union_slots']==0 and report['both_known_slots']==0
    assert report['known_union_agreement_rate'] is None
    assert report['both_known_agreement_rate'] is None
    assert report['known_set_input_agreement_rate'] is None
    assert report['eligible_known_set_inputs']==0 and report['both_empty_known_set_inputs']==1
    assert report['neither_known_slots']==72
    assert report['state_transitions']['explicit_unknown->unreported']==1
    assert report['state_transitions']['unreported->unreported']==71
    assert report['all_both_known_confidence']['mean_absolute_delta'] is None


def test_comparison_does_not_read_unrequested_run_or_write_inputs(runs,tmp_path):
    path,directories=runs
    (tmp_path/'unrequested-run').mkdir()
    (tmp_path/'unrequested-run'/'manifest.json').write_text('not JSON',encoding='utf-8')
    original={file:file.read_bytes() for directory in directories.values() for file in directory.rglob('*') if file.is_file()}
    original[path]=path.read_bytes()
    report=stability.compare_runs(path,directories)
    assert all(file.read_bytes()==content for file,content in original.items())
    assert set(report['run_provenance'])=={'2','4'}
