"""Proposal-level guards; no fresh classifier, writer or unchecked label promotion."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.light_novel_work_archive_reuse_v1 import _proposal_from_verified, replay_plot_evidence
from recommend_book.wikipedia_paragraphs import selection_proof, locator
from recommend_book.wikipedia_prose import screen_section
from recommend_book.ingestion.wikipedia import section_text
from recommend_book.autonomous_enrichment import FULFILLED


def claim(value):
    return {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': value}}}}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    title = '合成作品'; plot = '主人公は眠るたびにその日の記憶を失う。毎朝日記を読み、友人の助けで生活を続けている。友人は毎日彼女を支え、二人は新しい思い出を作ろうとする。'
    text = '導入の書誌情報。\n== あらすじ ==\n' + plot
    page = {'pageid': 10, 'title': title, 'ns': 0, 'pageprops': {'wikibase_item': 'Q1'},
        'revisions': [{'revid': 11, 'slots': {'main': {'contentmodel': 'wikitext', 'content': text}}}]}
    raw = tmp_path / 'batch.json'; pin = dump(raw, {'query': {'pages': [{'pageid': 9, 'title': '無関係'}, page]}})
    entity = {'id': 'Q1', 'sitelinks': {'jawiki': {'title': title}}, 'claims': {
        'P31': [claim('Q7725634')], 'P7937': [claim('Q8261')], 'P407': [claim('Q5287')], 'P50': [claim('Q2')]}}
    target = tmp_path / 'target.json'; target_pin = dump(target, {'entities': {'Q1': entity}})
    original = section_text(text, 'あらすじ'); screen = screen_section(original, article_wikitext=text, factual_titles=[title, title])
    attribution = {'raw_path': str(raw), 'raw_sha256': pin, 'page_id': 10, 'revision_id': 11,
        'fulfilled_obligations': FULFILLED, 'paragraph_selection': selection_proof(original, screen),
        'identity_proof': {'target_raw_path': str(target), 'target_raw_sha256': target_pin,
            'wikidata_work_id': 'Q1', 'pageprops_wikibase_item': 'Q1', 'page_id': 10, 'revision_id': 11, 'language': 'ja'}}
    evidence = {'evidence_id': 'wp:ja:11:あらすじ:screened-paragraphs-v1', 'kind': 'synopsis', 'expression_ref': None,
        'source_url': 'https://ja.wikipedia.org/w/index.php?oldid=11', 'rights_policy_id': 'wikipedia-screened-prose',
        'language': 'ja', 'locator': locator(11, 'あらすじ'), 'llm_input_allowed': True, 'text': screen.text,
        'license': 'CC-BY-SA-4.0', 'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
        'inherited_restrictions': FULFILLED, 'attribution': attribution}
    metadata = {'id': 'Q1', 'schema_version': 'light-novel-work-metadata-v3', 'kind': 'work', 'authors': [{'id': 'Q2'}]}
    ordered = {'work_id': 'old1', 'title': title, 'original_language': 'ja', 'expression_id': None,
        'evidence': [evidence, {'evidence_id': 'lead', 'kind': 'note'}, {'evidence_id': 'description', 'kind': 'description'}]}
    mapping = {'wikidata_qid': 'Q1', 'catalogue_work_id': 'wd:Q1', 'input_work_id': 'old1', 'work_id': 'old1', 'input_sha256': 'a'*64}
    label = {'label_id': 'theme.memory', 'axis': 'theme', 'scope': 'work', 'expression_id': None,
        'evidence': [{'evidence_id': evidence['evidence_id'], 'locator': evidence['locator'], 'quote': None, 'summary': '既存の審査済み要約'}]}
    admission = {'admitted_labels': [deepcopy(label)], 'native_result_modified': False,
        'separate_review_cleared_label_ids': ['theme.memory'], 'held_labels': [{'label_id': 'protagonist.caregiver'}]}
    verified = {'qid': 'Q1', 'work_id': 'wd:Q1', 'input_work_id': 'old1', 'labels': [label], 'provenance': {
        'input_hash': 'a'*64, 'run_id': 'run1', 'native_provenance': {'input_hash': 'a'*64, 'run_id': 'run1'},
        'label_admission': admission, 'export_manifest_path': 'archived.json', 'export_manifest_sha256': 'b'*64,
        'output_license': 'CC-BY-SA-4.0', 'output_license_url': 'https://creativecommons.org/licenses/by-sa/4.0/'}}
    run = {'run_id': 'run1', 'input_identities': [{'work_id': 'old1', 'input_hash': 'a'*64, 'expression_id': None}]}
    return metadata, verified, mapping, ordered, run, {'version': 'scale-content-native-classification-export-v3'}


def test_reviewed_plot_labels_are_only_a_historical_scope_proposal(bundle):
    before = deepcopy(bundle); result = _proposal_from_verified(*bundle)
    assert result['status'] == 'bridge_contract_review_required' and result['admission_ready'] is False
    assert result['fresh_plot_only_input'] is False
    assert result['historical_input_evidence_kinds'] == ['description', 'note', 'synopsis']
    assert result['candidate_labels'] == bundle[1]['labels']
    assert result['historical_held_labels'] == [{'label_id': 'protagonist.caregiver'}]
    assert result['historical_native_result_modified'] is False
    assert result['plot_source_witnesses'][0]['raw_page_locator'] == '/query/pages/1'
    assert result['model_calls'] == result['database_writes'] == 0 and bundle == before


def test_genre_only_archive_does_not_become_content_or_reinstate_held_theme(bundle):
    metadata, verified, mapping, ordered, run, export = bundle
    genre = {'label_id': 'genre.science_fiction', 'axis': 'genre', 'scope': 'work', 'expression_id': None, 'evidence': []}
    verified['labels'] = [genre]; admission = verified['provenance']['label_admission']
    admission.update(admitted_labels=[deepcopy(genre)], separate_review_cleared_label_ids=['genre.science_fiction'],
        held_labels=[{'label_id': 'theme.war'}])
    result = _proposal_from_verified(*bundle)
    assert result['status'] == 'held_no_reusable_content_labels' and result['candidate_labels'] == []
    assert result['label_holds'][0]['reason'] == 'genre_or_structured_metadata_is_not_content_bridge'


@pytest.mark.parametrize('change', ['qid', 'input_hash', 'run', 'expression', 'label_scope', 'held_label', 'unknown_export', 'series_metadata'])
def test_authority_scope_and_final_admission_mismatch_rejected(bundle, change):
    metadata, verified, mapping, ordered, run, export = bundle
    if change == 'qid': mapping['wikidata_qid'] = 'Q2'
    elif change == 'input_hash': mapping['input_sha256'] = 'c'*64
    elif change == 'run': run['run_id'] = 'other'
    elif change == 'expression': ordered['expression_id'] = 'translation1'
    elif change == 'label_scope':
        verified['labels'][0]['scope'] = 'series'; verified['provenance']['label_admission']['admitted_labels'] = deepcopy(verified['labels'])
    elif change == 'held_label': verified['provenance']['label_admission']['held_labels'].append({'label_id': 'theme.memory'})
    elif change == 'unknown_export': export['version'] = 'generic-unreviewed-export'
    else: metadata['kind'] = 'series'
    with pytest.raises(ValueError): _proposal_from_verified(*bundle)


@pytest.mark.parametrize('change', ['raw_hash', 'wrong_page', 'wrong_revision', 'generic_without_novel_form', 'wrong_author', 'wrong_plot_text', 'quote', 'lead_basis'])
def test_plot_source_replay_holds_unproved_content_without_promoting_raw_labels(bundle, change):
    metadata, verified, mapping, ordered, run, export = bundle; evidence = ordered['evidence'][0]; attribution = evidence['attribution']
    if change == 'raw_hash': attribution['raw_sha256'] = '0'*64
    elif change == 'wrong_page': attribution['page_id'] = 9
    elif change == 'wrong_revision': attribution['revision_id'] = 12
    elif change in {'generic_without_novel_form', 'wrong_author'}:
        target = Path(attribution['identity_proof']['target_raw_path']); data = json.loads(target.read_bytes())
        if change == 'generic_without_novel_form': data['entities']['Q1']['claims'].pop('P7937')
        else: data['entities']['Q1']['claims']['P50'] = [claim('Q3')]
        attribution['identity_proof']['target_raw_sha256'] = dump(target, data)
    elif change == 'wrong_plot_text': evidence['text'] += '改変'
    elif change == 'quote': verified['labels'][0]['evidence'][0]['quote'] = '存在しない引用'
    else: verified['labels'][0]['evidence'][0]['evidence_id'] = 'lead'
    if change in {'quote', 'lead_basis'}: verified['provenance']['label_admission']['admitted_labels'] = deepcopy(verified['labels'])
    result = _proposal_from_verified(*bundle)
    assert result['candidate_labels'] == [] and result['status'] == 'held_no_reusable_content_labels'
    assert result['admission_ready'] is False
