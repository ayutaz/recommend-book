from copy import deepcopy
import json

import pytest

from recommend_book.classification import native_genre_v1 as native
from recommend_book.classification.validation import input_hash
from recommend_book.models import Evidence, WorkInput


def work():
    return WorkInput(work_id='opaque:one', expression_id='opaque:edition', title='', original_language='fr',
        evidence=[Evidence(evidence_id='body:1', kind='excerpt', text='Il cherche le meurtrier dans la maison.',
            source_url='https://example.org/opaque', rights_policy_id='fixture', llm_input_allowed=True,
            language='fr', locator='body[0:42]', expression_ref='opaque:edition')])


def result(w):
    return {'work_id':w.work_id,'expression_id':w.expression_id,'input_hash':input_hash(w),
        'model':'model','cli_version':'cli','prompt_version':native.PROMPT_VERSION,'task_version':native.TASK_VERSION,
        'genres':[{'label':label,'state':'unknown','evidence':[],'unknown_reason':'Excerpt insufficient.'}
                  for label in native.LABELS]}


def present(r):
    r['genres'][1].update(state='present', unknown_reason=None,
        evidence=[{'evidence_id':'body:1','locator':'body[0:42]','quote':'Il cherche le meurtrier'}])
    return r


def test_blind_full_guard_compatible_prompt_never_contains_taxonomy_or_denied_text():
    w = work()
    w.evidence.append(Evidence(evidence_id='held', kind='note', text='HIDDEN-ANSWER',
        source_url='https://example.org', rights_policy_id='held', llm_input_allowed=False))
    before = deepcopy(w)
    prompt = native.build_prompt(w, profile=native.SPEC_NAME, model='model', cli_version='cli', taxonomy={'secret':'GOLD'})
    data = json.loads(prompt.split('\nBEGIN_LOCAL_DATA_JSON\n')[1].split('\nEND_LOCAL_DATA_JSON')[0])
    assert data['work'] == w.model_dump(mode='json') | {'evidence':[w.evidence[0].model_dump(mode='json')]}
    assert data['output_identity']['input_hash'] == input_hash(w)
    assert 'HIDDEN-ANSWER' not in prompt and 'GOLD' not in prompt and 'genre.fantasy' not in prompt
    assert 'model conventions' in prompt and 'not an annotation manual' in prompt
    assert w == before
    batch = native.build_batch_prompt([w], profile=native.SPEC_NAME, model='model', cli_version='cli')
    assert json.loads(batch.split('\nBEGIN_LOCAL_DATA_JSON\n')[1].split('\nEND_LOCAL_DATA_JSON')[0])['works'] == [data]


def test_reference_metadata_cannot_silently_enter_blind_work():
    for field, value in [('title','Book answer'),('authors',['Author answer']),('series','Known series'),('first_publication_year',1880)]:
        w = work()
        setattr(w, field, value)
        with pytest.raises(ValueError, match='blind'):
            native.build_prompt(w, profile=native.SPEC_NAME, model='m', cli_version='c')
    with pytest.raises(ValueError, match='spec'):
        native.build_prompt(work(), profile='native-context-v3', model='m', cli_version='c')


def test_independent_four_decisions_and_exact_primary_grounding():
    w = work()
    r = present(result(w))
    before = deepcopy(r)
    assert native.validate_native(r,w) == r
    assert r == before
    assert native.validate_native(result(w),w)['genres'][1]['state'] == 'unknown'


@pytest.mark.parametrize('change', ['missing','duplicate','reordered','extra','confidence','world72','absent',
    'identity','hash','task','prompt','quote','locator','evidence','unknown_evidence','unknown_reason','present_reason','present_empty'])
def test_invalid_results_fail_closed(change):
    w = work()
    r = present(result(w))
    if change == 'missing': r['genres'].pop()
    elif change == 'duplicate': r['genres'][0]['label'] = r['genres'][1]['label']
    elif change == 'reordered': r['genres'].reverse()
    elif change == 'extra': r['gold'] = True
    elif change == 'confidence': r['genres'][1]['confidence'] = .9
    elif change == 'world72': r['genres'][1]['label'] = 'genre.mystery'
    elif change == 'absent': r['genres'][1]['state'] = 'absent'
    elif change == 'identity': r['expression_id'] = 'foreign'
    elif change == 'hash': r['input_hash'] = '0' * 64
    elif change == 'task': r['task_version'] = 'native-protagonist-v3'
    elif change == 'prompt': r['prompt_version'] = '1.0'
    elif change in ('quote','locator','evidence'):
        r['genres'][1]['evidence'][0][{'quote':'quote','locator':'locator','evidence':'evidence_id'}[change]] = 'invented'
    elif change == 'unknown_evidence': r['genres'][0]['evidence'] = r['genres'][1]['evidence']
    elif change == 'unknown_reason': r['genres'][0]['unknown_reason'] = ' '
    elif change == 'present_reason': r['genres'][1]['unknown_reason'] = 'unsupported'
    else: r['genres'][1]['evidence'] = []
    with pytest.raises(ValueError): native.validate_native(r,w)


@pytest.mark.parametrize('change', ['expression','language','kind','denied'])
def test_other_expression_nonprimary_language_or_denied_evidence_cannot_ground_present(change):
    w = work()
    if change == 'expression': w.evidence[0].expression_ref = 'foreign'
    elif change == 'language': w.evidence[0].language = 'en'
    elif change == 'kind': w.evidence[0].kind = 'synopsis'
    else: w.evidence[0].llm_input_allowed = False
    with pytest.raises(ValueError): native.validate_native(present(result(w)),w)
