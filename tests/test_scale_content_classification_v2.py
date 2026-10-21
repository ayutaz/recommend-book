"""Actual publication-rights projection differs from semantic label review."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from recommend_book.models import Evidence, WorkInput
from recommend_book.scale_content_classification_v2 import (
    LICENSE, LICENSE_URL, OBLIGATIONS, review_public_rows,
    verify_catalogue_classification,
)
from recommend_book.usage_guard import model_hash


def fixture():
    evidence = Evidence(evidence_id='content', kind='synopsis', text='A real plot about a journey.',
        source_url='https://en.wikipedia.org/w/index.php?oldid=123', rights_policy_id='wikipedia-screened-prose',
        llm_input_allowed=True, license=LICENSE, license_url=LICENSE_URL, locator='revision:123;section:Plot',
        inherited_restrictions=sorted(OBLIGATIONS), attribution={
            'credit': 'Wikipedia contributors: Article', 'revision_id': 123,
            'permalink': 'https://en.wikipedia.org/w/index.php?oldid=123',
            'history_url': 'https://en.wikipedia.org/w/index.php?title=Article&action=history',
            'changes': 'Screened selected paragraphs; markup removed', 'fulfilled_obligations': sorted(OBLIGATIONS)})
    extra = evidence.model_copy(update={'evidence_id': 'not-actual-content'})
    work = WorkInput(work_id='w', title='Book', evidence=[evidence, extra])
    label = {'label_id': 'theme.growth', 'axis': 'theme', 'scope': 'work', 'decision': 'present',
             'evidence': [{'evidence_id': 'content', 'quote': 'a journey', 'locator': evidence.locator}]}
    rows = [{'qid': 'Q1', 'input_work_id': 'w', 'input_hash': model_hash(work),
             'content_evidence_ids': ['content'], 'admitted_labels': [label],
             'source_attributions': [{'evidence_ids': ['not-actual-content'], 'display_allowed': True}]}]
    return rows, work, {'labels': [{'label_id': 'theme.growth', 'axis': 'theme'}]}


def test_actual_rights_projection_filters_exact_content_and_explicit_license():
    rows, work, taxonomy = fixture()
    output, audit = review_public_rows(rows, [work], taxonomy)
    assert output[0]['admitted_labels'] == rows[0]['admitted_labels']
    assert [a['evidence_ids'] for a in output[0]['source_attributions']] == [['content']]
    attr = output[0]['source_attributions'][0]
    assert attr['attribution']['history_url'].endswith('action=history')
    assert attr['output_license'] == LICENSE and attr['output_license_url'] == LICENSE_URL
    assert audit['rows'][0]['exact_citation_count'] == 1
    assert audit['human_reviewed'] is False and audit['legal_clearance_claimed'] is False


@pytest.mark.parametrize('field', ['credit', 'history_url', 'changes', 'fulfilled_obligations'])
def test_v1_display_allowed_flag_cannot_replace_actual_output_obligation(field):
    rows, work, taxonomy = fixture()
    work.evidence[0].attribution.pop(field)
    rows[0]['input_hash'] = model_hash(work)
    with pytest.raises(ValueError):
        review_public_rows(rows, [work], taxonomy)


def test_exact_quote_and_closed_taxonomy_and_input_binding():
    rows, work, taxonomy = fixture()
    for change in ('quote', 'taxonomy', 'input'):
        changed = deepcopy(rows)
        if change == 'quote':
            changed[0]['admitted_labels'][0]['evidence'][0]['quote'] = 'invented quotation'
        elif change == 'taxonomy':
            changed[0]['admitted_labels'][0]['label_id'] = 'invented.label'
        else:
            changed[0]['input_hash'] = '0' * 64
        with pytest.raises(ValueError):
            review_public_rows(changed, [work], taxonomy)


def test_unreviewed_content_citation_rejected_even_with_old_flags():
    rows, work, taxonomy = fixture()
    rows[0]['admitted_labels'][0]['evidence'][0]['evidence_id'] = 'not-actual-content'
    with pytest.raises(ValueError, match='outside exact reviewed'):
        review_public_rows(rows, [work], taxonomy)


@pytest.mark.parametrize('quote',[None,''])
def test_generated_summary_is_licensed_adaptation_without_quote_permission(quote):
    rows, work, taxonomy = fixture()
    rows[0]['admitted_labels'][0]['evidence'][0].update(quote=quote,summary='The characters undertake a journey.')
    output,audit=review_public_rows(rows,[work],taxonomy)
    assert output[0]['source_attributions'][0]['quote_allowed'] is False
    assert output[0]['source_attributions'][0]['output_license']==LICENSE
    assert audit['rows'][0]['generated_summary_adaptation_count']==1
    assert audit['rows'][0]['exact_quote_count']==0


@pytest.mark.parametrize('quote,summary',[(None,None),(None,''),('',None),('invented','a plausible summary')])
def test_empty_or_invented_quote_cannot_be_hidden_by_summary(tmp_path,quote,summary):
    rows,work,taxonomy=fixture()
    rows[0]['admitted_labels'][0]['evidence'][0].update(quote=quote,summary=summary)
    with pytest.raises(ValueError):
        review_public_rows(rows,[work],taxonomy)


def test_summary_requires_actual_retained_source_binding():
    rows,work,taxonomy=fixture()
    rows[0]['admitted_labels'][0]['evidence'][0].update(evidence_id='not-actual-content',quote=None,summary='Generated summary')
    with pytest.raises(ValueError,match='outside exact reviewed'):
        review_public_rows(rows,[work],taxonomy)


def test_public_verifier_rejects_internal_content_v1(tmp_path):
    import hashlib
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'version': 'scale-content-native-classification-export-v1'}))
    with pytest.raises(ValueError, match='v2 required'):
        verify_catalogue_classification(path, hashlib.sha256(path.read_bytes()).hexdigest())


def test_frozen_runtime_validation_ignores_unused_live_ui_but_rejects_native_tamper(tmp_path):
    import hashlib
    import importlib.util
    script = Path(__file__).resolve().parents[1] / 'scripts/prepare_scale_content_chunk_v1.py'
    spec = importlib.util.spec_from_file_location('content_chunk', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    runtime = tmp_path / 'runtime'; runtime.mkdir()
    code = runtime / 'native.py'; code.write_text('fixed native code')
    transformed = runtime / 'guard_v4.py'; transformed.write_text('fixed transformed guard')
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / 'source-snapshot-manifest.json').write_text(json.dumps({'runtime_root': str(runtime),
        'copied_original_sha256': {'native.py': digest(code)},
        'transformed': {'guard_v4.py': {'sha256': digest(transformed)}}}))
    first = module.verify_frozen_runtime(tmp_path)
    unused = tmp_path / 'unrelated-current-ui.py'; unused.write_text('updated UI')
    assert module.verify_frozen_runtime(tmp_path) == first
    transformed.write_text('changed guard')
    with pytest.raises(ValueError, match='transformed native runtime changed'):
        module.verify_frozen_runtime(tmp_path)


def test_v2_control_rejects_old_internal_export(tmp_path):
    from recommend_book.scale_content_control_v2 import verify_control_receipt
    import hashlib
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'version': 'scale-content-window-control-v1'}))
    with pytest.raises(ValueError, match='Unknown content control'):
        verify_control_receipt(path, hashlib.sha256(path.read_bytes()).hexdigest())
