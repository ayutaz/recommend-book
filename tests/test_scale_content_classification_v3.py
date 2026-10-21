"""A deterministic publication hold must preserve genuine semantic/native output."""
from copy import deepcopy

import pytest

from recommend_book.models import Evidence, WorkInput
from recommend_book.scale_content_classification_v3 import (
    LICENSE, LICENSE_URL, OBLIGATIONS, public_projection, review_public_rows,
)
from recommend_book.usage_guard import model_hash


def fixture():
    evidence = Evidence(evidence_id='content', kind='synopsis', text='A real\u00a0journey.  A second sentence.',
        source_url='https://en.wikipedia.org/w/index.php?oldid=123', rights_policy_id='wikipedia-screened-prose',
        llm_input_allowed=True, license=LICENSE, license_url=LICENSE_URL, locator='revision:123;section:Plot',
        inherited_restrictions=sorted(OBLIGATIONS), attribution={
            'credit': 'Wikipedia contributors: Article', 'revision_id': 123,
            'permalink': 'https://en.wikipedia.org/w/index.php?oldid=123',
            'history_url': 'https://en.wikipedia.org/w/index.php?title=Article&action=history',
            'changes': 'Screened paragraphs', 'fulfilled_obligations': sorted(OBLIGATIONS)})
    work = WorkInput(work_id='w', title='Book', evidence=[evidence])
    label = {'label_id': 'structure.journey', 'axis': 'structure', 'scope': 'work', 'decision': 'present',
             'evidence': [{'evidence_id': 'content', 'quote': 'A real journey.', 'locator': evidence.locator}]}
    row = {'qid': 'Q1', 'input_work_id': 'w', 'input_hash': model_hash(work),
        'content_evidence_ids': ['content'], 'admitted_labels': [label],
        'native_result': {'work_id': 'w', 'labels': [deepcopy(label)], 'missing_axes': []},
        'label_admission': {'admitted_labels': [deepcopy(label)], 'held_labels': [],
                            'separate_review_cleared_label_ids': ['structure.journey']}}
    return [row], work, {'labels': [{'label_id': 'structure.journey', 'axis': 'structure'}]}


@pytest.mark.parametrize('quote', ['A real journey.', 'A real\u00a0journey. A second sentence.', 'invented quotation'])
def test_inexact_quote_holds_whole_label_without_rewriting_native_or_semantic_review(quote):
    rows, work, taxonomy = fixture()
    rows[0]['admitted_labels'][0]['evidence'][0]['quote'] = quote
    original = deepcopy(rows)
    output, audit = review_public_rows(rows, [work], taxonomy)
    assert rows == original
    assert output[0]['native_result'] == original[0]['native_result']
    assert output[0]['admitted_labels'] == []
    admission = output[0]['label_admission']
    assert admission['admitted_labels'] == []
    assert admission['separate_review_cleared_label_ids'] == ['structure.journey']
    hold = admission['held_labels'][0]
    assert hold['reason'] == 'publication_quote_not_exact_retained_source'
    assert hold['review_kind'] == 'deterministic_literal_quote_review'
    assert len(hold['citation_mismatches'][0]['native_quote_sha256']) == 64
    assert output[0]['source_attributions'][0]['quote_allowed'] is False
    assert audit['publication_quote_hold_count'] == 1
    assert audit['human_reviewed'] is False


def test_exact_quote_and_summary_stay_unchanged_and_licensed():
    rows, work, taxonomy = fixture()
    rows[0]['admitted_labels'][0]['evidence'][0]['quote'] = 'real\u00a0journey'
    output, audit = review_public_rows(rows, [work], taxonomy)
    assert output[0]['admitted_labels'] == rows[0]['admitted_labels']
    assert output[0]['source_attributions'][0]['quote_allowed'] is True
    assert audit['publication_quote_hold_count'] == 0
    rows[0]['admitted_labels'][0]['evidence'][0].update(quote=None, summary='A generated journey summary.')
    output, audit = review_public_rows(rows, [work], taxonomy)
    assert output[0]['admitted_labels'] == rows[0]['admitted_labels']
    assert output[0]['source_attributions'][0]['quote_allowed'] is False
    assert audit['rows'][0]['generated_summary_adaptation_count'] == 1


@pytest.mark.parametrize('change', ['binding', 'locator', 'taxonomy', 'empty_reference'])
def test_quote_hold_cannot_hide_invalid_source_binding_or_closed_taxonomy(change):
    rows, work, taxonomy = fixture()
    citation = rows[0]['admitted_labels'][0]['evidence'][0]
    if change == 'binding':
        rows[0]['content_evidence_ids'] = []
    elif change == 'locator':
        citation['locator'] = 'wrong'
    elif change == 'taxonomy':
        rows[0]['admitted_labels'][0]['label_id'] = 'unknown'
    else:
        citation.update(quote=None, summary=None)
    with pytest.raises(ValueError):
        review_public_rows(rows, [work], taxonomy)


def test_public_axis_and_feature_counts_are_recomputed_after_holds():
    rows, work, taxonomy = fixture()
    filtered, _ = review_public_rows(rows, [work], taxonomy)
    parent = {'verified_projection': {'rows': rows, 'all_native_results': [rows[0]['native_result']],
        'axis_counts': {'structure': {'raw_present_labels': 1, 'raw_absent_labels': 0,
            'unknown_work_count': 0, 'adopted_labels': 1, 'held_present_labels': 0}},
        'raw_classification_saved_count': 1, 'actual_feature_adopted_works': 1,
        'actual_content_non_genre_feature_works': 1}}
    projected = public_projection(parent, filtered)
    assert projected['raw_classification_saved_count'] == 1
    assert projected['all_native_results'] == parent['verified_projection']['all_native_results']
    assert projected['actual_feature_adopted_works'] == 0
    assert projected['actual_content_non_genre_feature_works'] == 0
    assert projected['axis_counts']['structure']['adopted_labels'] == 0
    assert projected['axis_counts']['structure']['held_present_labels'] == 1
