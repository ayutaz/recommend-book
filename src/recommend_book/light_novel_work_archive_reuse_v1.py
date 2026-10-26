"""Read-only proposal for reviewed historical WORK labels, never reclassification.

This is deliberately NOT a feature importer or fresh Plot-only WorkInput
adapter. It preserves mixed historical input scope and emits admission_ready=False.
The public inspector first executes the existing full frozen archive verifier.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .jobs import canonical_json
from .light_novel_work_admission_v3 import verify_metadata
from .light_novel_admission_v3 import items, CONFLICT_TYPES

VERSION = 'light-novel-work-archived-label-bridge-proposal-v1'
EXPORTS = {'scale-content-native-classification-export-v2', 'scale-content-native-classification-export-v3'}


def _require(value, message):
    if not value: raise ValueError(message)


def _read(path, expected):
    raw = Path(path).read_bytes()
    _require(re.fullmatch(r'[0-9a-f]{64}', expected or '') is not None and hashlib.sha256(raw).hexdigest() == expected,
             'Archived reuse source SHA differs')
    return raw


def replay_plot_evidence(evidence, ordered, metadata):
    """Select the exact page inside a pinned batch and replay the OLD recipe."""
    from .autonomous_enrichment import FULFILLED, SECTIONS
    from .ingestion.wikipedia import section_text
    from .wikipedia_prose import screen_section
    from .wikipedia_paragraphs import selection_proof, locator
    _require(evidence.get('kind') == 'synopsis' and evidence.get('expression_ref') is None,
             'Only work Plot synopsis evidence can be reused')
    language = evidence['language']; attribution = evidence['attribution']; identity = attribution['identity_proof']
    match = re.fullmatch(r'wp:([a-z]+):([0-9]+):(.+):screened-paragraphs-v1', evidence['evidence_id'])
    _require(match and match[1] == language and match[3] != 'lead' and match[3] in SECTIONS.get(language, ()),
             'Archived Plot section identity differs')
    revision = int(match[2]); section = match[3]; qid = metadata['id']
    _require(evidence.get('source_url') == f'https://{language}.wikipedia.org/w/index.php?oldid={revision}'
        and evidence.get('locator') == locator(revision, section)
        and evidence.get('license') == 'CC-BY-SA-4.0'
        and evidence.get('license_url') == 'https://creativecommons.org/licenses/by-sa/4.0/'
        and evidence.get('llm_input_allowed') is True
        and evidence.get('rights_policy_id') == 'wikipedia-screened-prose'
        and set(FULFILLED) <= set(attribution.get('fulfilled_obligations', []))
        and set(FULFILLED) <= set(evidence.get('inherited_restrictions', [])), 'Archived Plot rights/locator differs')
    target = json.loads(_read(identity['target_raw_path'], identity['target_raw_sha256']))['entities'][qid]
    kinds = set(items(target, 'P31') + items(target, 'P7937'))
    _require(target.get('id') == qid and 'Q8261' in kinds and not kinds & (CONFLICT_TYPES | {'Q1667921', 'Q104213567'})
        and not items(target, 'P629') and set(items(target, 'P407')) == {'Q5287'}
        and set(items(target, 'P50')) == {a['id'] for a in metadata['authors']}, 'Archived novel unit/author scope differs')
    raw = json.loads(_read(attribution['raw_path'], attribution['raw_sha256']))
    pages = raw.get('query', {}).get('pages', [])
    if isinstance(pages, dict): page_rows = [(str(key), page) for key, page in pages.items()]
    else: page_rows = [(str(index), page) for index, page in enumerate(pages)]
    selected = [(key, page) for key, page in page_rows if page.get('pageid') == attribution['page_id']]
    _require(len(selected) == 1, 'Archived batch has no unique target page')
    key, page = selected[0]
    _require(page.get('ns') == 0 and page.get('pageprops', {}).get('wikibase_item') == qid
        and 'disambiguation' not in page.get('pageprops', {})
        and target.get('sitelinks', {}).get(language + 'wiki', {}).get('title') == page.get('title')
        and identity.get('wikidata_work_id') == identity.get('pageprops_wikibase_item') == qid
        and identity.get('page_id') == page['pageid'] and identity.get('revision_id') == revision
        and identity.get('language') == language and attribution.get('revision_id') == revision,
        'Archived exact article/target revision differs')
    revisions = page.get('revisions', [])
    _require(len(revisions) == 1 and revisions[0].get('revid') == revision, 'Archived single target revision differs')
    slot = revisions[0].get('slots', {}).get('main', {})
    _require(slot.get('contentmodel', 'wikitext') == 'wikitext', 'Archived source model differs')
    text = slot.get('content', slot.get('*', '')); original = section_text(text, section)
    screen = screen_section(original, article_wikitext=text, factual_titles=[ordered['title'], page['title']])
    _require(not screen.article_blocked and screen.text == evidence['text']
        and selection_proof(original, screen) == attribution['paragraph_selection'],
        'Archived paragraph recipe/text cannot be reproduced')
    return {'evidence_id': evidence['evidence_id'], 'source_url': evidence['source_url'],
        'raw_path': attribution['raw_path'], 'raw_sha256': attribution['raw_sha256'],
        'raw_page_locator': '/query/pages/' + key, 'page_id': page['pageid'], 'revision_id': revision,
        'pageprops_wikibase_item': qid, 'target_raw_path': identity['target_raw_path'],
        'target_raw_sha256': identity['target_raw_sha256'], 'section': section, 'locator': evidence['locator'],
        'selected_text_sha256': attribution['paragraph_selection']['selected_text_sha256'],
        'original_recipe': 'screened-paragraphs-v1', 'license': evidence['license'],
        'license_url': evidence['license_url'], 'attribution': deepcopy(attribution)}


def _proposal_from_verified(metadata, verified, mapping, ordered, run, export):
    """Internal pure authority join; caller has replayed the frozen export."""
    qid = metadata['id']; work_id = 'lnw:wd:' + qid; provenance = verified['provenance']
    _require(metadata['kind'] == 'work' and metadata['schema_version'] == 'light-novel-work-metadata-v3'
        and export['version'] in EXPORTS and verified['qid'] == qid
        and verified['work_id'] == mapping['catalogue_work_id'] == 'wd:' + qid
        and mapping['wikidata_qid'] == qid
        and verified['input_work_id'] == mapping['input_work_id'] == mapping['work_id'] == ordered['work_id']
        and mapping['input_sha256'] == provenance['input_hash'] == provenance['native_provenance']['input_hash']
        and provenance['run_id'] == provenance['native_provenance']['run_id'] == run['run_id'],
        'Archived same-unit mapping/input/run binding differs')
    identities = [r for r in run['input_identities'] if r.get('work_id') == ordered['work_id']]
    _require(identities == [{'work_id': ordered['work_id'], 'input_hash': mapping['input_sha256'], 'expression_id': None}]
        and ordered.get('expression_id') is None and ordered['original_language'] == 'ja',
        'Archived work input/expression scope differs')
    admission = provenance['label_admission']
    _require(verified['labels'] == admission['admitted_labels'] and admission.get('native_result_modified') is False,
        'Only final reviewed admitted labels may be considered')
    cleared = set(admission['separate_review_cleared_label_ids'])
    held = {r['label_id'] for r in admission['held_labels']}
    _require(all(l['label_id'] in cleared and l['label_id'] not in held for l in verified['labels']),
        'Held or uncleared native labels cannot be promoted')
    evidence = {e['evidence_id']: e for e in ordered['evidence']}
    _require(len(evidence) == len(ordered['evidence']), 'Archived evidence IDs repeat')
    candidates, label_holds, witnesses = [], [], {}
    for label in verified['labels']:
        _require(label.get('scope') == 'work' and label.get('expression_id') is None,
                 'Archived label is not the exact work scope')
        if label['axis'] == 'genre':
            label_holds.append({'label_id': label['label_id'], 'reason': 'genre_or_structured_metadata_is_not_content_bridge'})
            continue
        used = label.get('evidence', [])
        _require(used, 'Archived content label has no evidence')
        try:
            for ref in used:
                source = evidence[ref['evidence_id']]
                _require(ref['locator'] == source['locator'], 'Archived label locator differs')
                witness = replay_plot_evidence(source, ordered, metadata)
                if ref.get('quote'):
                    _require(ref['quote'] in source['text'], 'Archived literal evidence quote differs')
                witnesses[source['evidence_id']] = witness
            candidates.append(deepcopy(label))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            label_holds.append({'label_id': label['label_id'], 'reason': str(exc)})
    historical_kinds = sorted({e['kind'] for e in ordered['evidence']})
    return {'version': VERSION, 'entry_id': work_id, 'work_id': work_id, 'entity_kind': 'light_novel_work',
        'scope': 'work', 'status': 'bridge_contract_review_required' if candidates else 'held_no_reusable_content_labels',
        'admission_ready': False, 'fresh_plot_only_input': historical_kinds == ['synopsis'],
        'historical_input_evidence_kinds': historical_kinds,
        'historical_entity_kind': 'generic_work_native_v4', 'archived_work_id': verified['work_id'],
        'archived_input_work_id': verified['input_work_id'], 'archived_run_id': provenance['run_id'],
        'archived_input_hash': provenance['input_hash'], 'export_version': export['version'],
        'export_manifest_path': provenance['export_manifest_path'],
        'export_manifest_sha256': provenance['export_manifest_sha256'],
        'candidate_labels': candidates, 'label_holds': label_holds, 'plot_source_witnesses': list(witnesses.values()),
        'historical_held_labels': deepcopy(admission['held_labels']),
        'historical_separate_review_case': deepcopy(admission.get('separate_review_case')),
        'historical_missing_axes': deepcopy(provenance.get('native_result', {}).get('missing_axes', [])),
        'historical_native_result_modified': False,
        'archived_run_manifest_pin': {'path': provenance['native_provenance'].get('run_manifest_path'),
            'sha256': provenance['native_provenance'].get('run_manifest_sha256')},
        'archived_ordered_inputs_pin': {'path': provenance['native_provenance'].get('ordered_inputs_path'),
            'sha256': provenance['native_provenance'].get('ordered_inputs_sha256')},
        'metadata_value_sha256': hashlib.sha256(canonical_json(metadata).encode()).hexdigest(),
        'output_license': provenance['output_license'], 'output_license_url': provenance['output_license_url'],
        'preservation_requirements': ['keep native/admission/review/unknown/calibration facts unchanged',
            'retain historical mixed input scope; never call this a fresh Plot-only classification',
            'separate approved bridge export/import contract and final public/recommendation checks required',
            'no new model call or source resubmission for this already-classified work'],
        'model_calls': 0, 'database_writes': 0}


def inspect_archived_reuse(metadata, manifest_path, manifest_sha256, archive_root):
    """Full read-only inspector; this returns a proposal, never adoptable features."""
    from .classification.native_cli_archive_projection_v1 import verify_catalogue_classification
    verify_metadata(metadata)
    export = json.loads(_read(manifest_path, manifest_sha256))
    _require(export.get('version') in EXPORTS, 'Archived reviewed output version unsupported')
    rows = verify_catalogue_classification(manifest_path, manifest_sha256, archive_root)
    selected = [row for row in rows if row['qid'] == metadata['id']]
    _require(len(selected) == 1, 'Archived requested work is absent or ambiguous')
    verified = selected[0]
    mappings = json.loads(Path(export['work_mapping']).read_bytes())
    mapped = [r for r in mappings if r['wikidata_qid'] == metadata['id']]
    _require(len(mapped) == 1, 'Archived work mapping ambiguous')
    native = verified['provenance']['native_provenance']
    raw = _read(native['ordered_inputs_path'], native['ordered_inputs_sha256'])
    inputs = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ordered = [row for row in inputs if row['work_id'] == verified['input_work_id']]
    _require(len(ordered) == 1, 'Archived work input ambiguous')
    run = json.loads(_read(native['run_manifest_path'], native['run_manifest_sha256']))
    return _proposal_from_verified(metadata, verified, mapped[0], ordered[0], run, export)
