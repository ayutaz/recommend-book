from copy import deepcopy

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_sources_v1 import work_bundle
from recommend_book.light_novel_work_classification_contract_v1 import (
    result_schema,validate_result,build_prompt,input_hash,load_taxonomy,VERSION,
    project_reviewed_result,validate_reviews,check_gate,
)


def native(work):
    return {'work_id':work.work_id,'entity_kind':'light_novel_work','scope':'work',
        'taxonomy_version':load_taxonomy()['version'],'prompt_version':VERSION,
        'model':'gpt-6.1-sol','cli_version':'fixture','input_hash':input_hash(work),
        'labels':[{'label_id':'genre.fantasy','axis':'genre','decision':'present','scope':'work',
            'relevance':.9,'raw_confidence':.8,'calibrated_confidence':None,
            'calibration_status':'uncalibrated','calibration_version':None,
            'evidence':[{'evidence_id':work.evidence[0].evidence_id,'locator':work.evidence[0].locator,
                'quote':None,'summary':'A supplied synopsis supports this provisional label.','directness':'direct'}],
            'missing_reason':None,'spoiler':'premise'}],
        'missing_axes':[{'axis':axis,'decision':'unknown','reason':'Insufficient evidence'} for axis in
            ('theme','structure','protagonist','style','atmosphere')], 'conflicts':[],'needs_human_review':False}


def test_work_native_review_and_projection_keep_work_identity(work_bundle):
    work=work_bundle[3];value=native(work)
    validate_result(value,work,cli_version='fixture')
    assert result_schema(work)['properties']['work_id']=={'const':'lnw:wd:Q1'}
    prompt=build_prompt(work,cli_version='fixture')
    assert '"scope": "work"' in prompt and '"series_id"' not in prompt
    review={'work_id':work.work_id,'reviewed_label_ids':['genre.fantasy'],'summary':'Checked all labels','findings':[]}
    row=project_reviewed_result(work,value,review)
    assert row['entry_id']==work.work_id and row['scope']=='work' and row['entity_kind']=='light_novel_work'
    assert 'series_id' not in row and row['content_features_adopted'] is True
    review['findings']=[{'label_ids':['genre.fantasy'],'reason_code':'semantic_entailment_uncertain','reason':'Not entailed'}]
    assert project_reviewed_result(work,value,review)['content_features_adopted'] is False


@pytest.mark.parametrize('tamper',['series_scope','series_identity','wrong_hash','bad_locator','style','metadata_axis'])
def test_same_quality_gates_hold_wrong_scope_or_unsupported_content(work_bundle,tamper):
    work=work_bundle[3];value=native(work)
    if tamper=='series_scope':value['labels'][0]['scope']='series'
    elif tamper=='series_identity':value['work_id']='lns:wd:Q1'
    elif tamper=='wrong_hash':value['input_hash']='wrong'
    elif tamper=='bad_locator':value['labels'][0]['evidence'][0]['locator']='foreign'
    elif tamper=='style':
        label=next(l for l in load_taxonomy()['labels'] if l['axis']=='style');value['labels'][0].update(label_id=label['label_id'],axis='style')
    else:
        label=next(l for l in load_taxonomy()['labels'] if l['axis']=='protagonist');value['labels'][0].update(label_id=label['label_id'],axis='protagonist')
        work=work.model_copy(update={'evidence':[work.evidence[0].model_copy(update={'kind':'bibliographic'})]});value['input_hash']=input_hash(work)
    with pytest.raises(ValueError):validate_result(value,work)


def test_gate_requires_actual_work_success_and_adoption():
    assert check_gate(selected=19,failed=0,pending=0,adopted=19)['passed']
    assert not check_gate(selected=19,failed=1,pending=0,adopted=18)['passed']
    assert not check_gate(selected=19,failed=0,pending=1,adopted=18)['passed']
    assert not check_gate(selected=19,failed=0,pending=0,adopted=0)['passed']
