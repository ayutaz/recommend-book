"""Known, narrowly bounded archived-label reuse export; no fresh classifier.

Only Q110895870's two final reviewed Plot labels qualify. Historical input
context remains mixed metadata/lead/Plot and every old frozen recipe is replayed.
No model call, WorkInput construction, source acquisition or database writer.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .jobs import canonical_json
from .light_novel_work_admission_v3 import verify_metadata, check_duplicate_titles
from .light_novel_admission_v3 import stored_title_closure
from .light_novel_work_archive_reuse_v1 import _read, _require, _proposal_from_verified, replay_plot_evidence, EXPORTS

VERSION = 'light-novel-work-archived-label-feature-export-v1'
CONTEXT = 'historical_mixed_metadata_lead_and_plot'
QID = 'Q110895870'
LABEL_IDS = {'theme.memory', 'atmosphere.warm'}
ROOT = Path(__file__).resolve().parents[2]
RUNTIME_FILES = ('src/recommend_book/light_novel_work_archive_bridge_v1.py',
    'src/recommend_book/light_novel_work_archive_reuse_v1.py',
    'src/recommend_book/light_novel_work_admission_v3.py', 'src/recommend_book/light_novel_admission_v3.py',
    'src/recommend_book/classification/native_cli_archive_projection_v1.py',
    'src/recommend_book/classification/native_cli_archive_v1.py',
    'src/recommend_book/wikipedia_paragraphs.py', 'src/recommend_book/wikipedia_prose.py',
    'src/recommend_book/ingestion/wikipedia.py', 'src/recommend_book/ingestion/ndl.py',
    'src/recommend_book/autonomous_enrichment.py', 'src/recommend_book/models.py', 'src/recommend_book/jobs.py',
    'config/taxonomy.json')


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _save(path, value):
    with Path(path).open('x', encoding='utf8') as handle:
        handle.write(canonical_json(value) + '\n')


def duplicate_inventory(records):
    """Freeze exact saved title closures; caller supplies SHA-authenticated rows."""
    rows = []
    for row in records:
        record = row['record']
        _require(row['record_sha256'] == digest(record), 'Duplicate inventory native record SHA differs')
        if row['entry_kind'] not in {'series', 'work', 'publisher_group'}:
            continue
        titles = stored_title_closure(record)
        rows.append({'entry_id': row['entry_id'], 'entry_kind': row['entry_kind'],
            'record_sha256': row['record_sha256'], 'title': titles[0] if titles else '', 'aliases': titles[1:]})
    _require(1 <= len(rows) <= 100000 and len({r['entry_id'] for r in rows}) == len(rows),
        'Duplicate inventory finite identity scope differs')
    return {'version': 'light-novel-work-archive-duplicate-inventory-v1',
            'scope': 'saved_LN_work_series_publisher_group_title_closure', 'rows': rows}


def _check_duplicates(metadata, inventory):
    _require(inventory.get('version') == 'light-novel-work-archive-duplicate-inventory-v1'
        and inventory.get('scope') == 'saved_LN_work_series_publisher_group_title_closure'
        and isinstance(inventory.get('rows'), list) and 1 <= len(inventory['rows']) <= 100000,
        'Archived bridge duplicate proof scope differs')
    ids = [row['entry_id'] for row in inventory['rows']]
    _require(len(ids) == len(set(ids)), 'Archived bridge duplicate proof identity repeated')
    check_duplicate_titles(metadata, inventory['rows'])


def check_current_target(row, record, record_sha256, existing):
    """Live catalogue boundary; caller repeats under its writer transaction.

    ``existing`` is the complete current native series/work/publisher-group
    inventory, with entry_id/entry_kind/record_sha256/record fields. This does
    not trust the frozen export inventory as a substitute for the current DB.
    """
    _require(row.get('entry_id') == row.get('work_id') == 'lnw:wd:' + QID
        and row.get('entity_kind') == 'light_novel_work' and row.get('scope') == 'work'
        and 'series_id' not in row and row.get('archive_context') == CONTEXT
        and row.get('fresh_classification') is False,
        'Current archived bridge target identity/context differs')
    _require(record_sha256 == digest(record) == row.get('target_record_sha256'),
        'Current archived bridge target metadata SHA differs')
    verify_metadata(record)
    _require(record['id'] == QID, 'Current archived bridge typed work differs')
    inventory = duplicate_inventory(existing)
    _require(any(item['entry_id'] == row['entry_id'] and item['entry_kind'] == 'work'
                 and item['record_sha256'] == record_sha256 for item in inventory['rows']),
        'Current archived bridge native target absent')
    _check_duplicates(record, inventory)
    return True


def _replay_archive(metadata, manifest_path, manifest_sha256, archive_root):
    from .classification.native_cli_archive_projection_v1 import verify_catalogue_classification
    export = json.loads(_read(manifest_path, manifest_sha256))
    _require(export.get('version') in EXPORTS, 'Reviewed native archive version differs')
    rows = verify_catalogue_classification(manifest_path, manifest_sha256, archive_root)
    selected = [row for row in rows if row['qid'] == metadata['id']]
    _require(len(selected) == 1, 'Archived bridge exact requested work absent or ambiguous')
    verified = selected[0]; native = verified['provenance']['native_provenance']
    mapping_rows = json.loads(Path(export['work_mapping']).read_bytes())
    mapping = [row for row in mapping_rows if row['wikidata_qid'] == metadata['id']]
    _require(len(mapping) == 1, 'Archived bridge work mapping ambiguous')
    ordered_rows = [json.loads(line) for line in _read(native['ordered_inputs_path'], native['ordered_inputs_sha256']).splitlines() if line.strip()]
    ordered = [row for row in ordered_rows if row['work_id'] == verified['input_work_id']]
    _require(len(ordered) == 1, 'Archived bridge original mixed work input ambiguous')
    run = json.loads(_read(native['run_manifest_path'], native['run_manifest_sha256']))
    proposal = _proposal_from_verified(metadata, verified, mapping[0], ordered[0], run, export)
    return {'proposal': proposal, 'verified': verified, 'ordered': ordered[0], 'export': export}


def _project(metadata, inventory, context, source):
    """Project unchanged final labels; no raw or held labels may be added."""
    verify_metadata(metadata)
    _require(metadata['id'] == QID, 'Archived bridge bounded work identity differs; genre-only work remains held')
    _check_duplicates(metadata, inventory)
    proposal = context['proposal']; verified = context['verified']; ordered = context['ordered']
    _require(proposal['work_id'] == 'lnw:wd:' + QID and proposal['status'] == 'bridge_contract_review_required'
        and proposal['admission_ready'] is False and proposal['fresh_plot_only_input'] is False
        and proposal['historical_input_evidence_kinds'] == ['description', 'note', 'subject', 'synopsis'],
        'Historical mixed input context must remain explicit')
    labels = proposal['candidate_labels']
    _require(labels == verified['labels'] and {row['label_id'] for row in labels} == LABEL_IDS and len(labels) == 2
        and all(row['axis'] != 'genre' and row['scope'] == 'work' and row.get('expression_id') is None
                and row.get('decision') == 'present' and row.get('calibration_status') == 'uncalibrated'
                and row.get('calibrated_confidence') is None for row in labels),
        'Archived bridge accepts only the two unchanged reviewed Plot labels')
    admission = verified['provenance']['label_admission']
    _require(labels == admission['admitted_labels'] and admission.get('native_result_modified') is False
        and LABEL_IDS <= set(admission['separate_review_cleared_label_ids'])
        and not LABEL_IDS & {l['label_id'] for l in admission['held_labels']},
        'Held, unreviewed or modified labels cannot enter archived bridge')
    by_id = {e['evidence_id']: e for e in ordered['evidence']}
    citations = {c['evidence_id'] for label in labels for c in label['evidence']}
    _require(citations and len(by_id) == len(ordered['evidence']), 'Archived evidence identity differs')
    witnesses = [replay_plot_evidence(by_id[key], ordered, metadata) for key in sorted(citations)]
    _require(witnesses == sorted(proposal['plot_source_witnesses'], key=lambda row: row['evidence_id']),
        'Archived bridge Plot source witness differs')
    for label in labels:
        for ref in label['evidence']:
            evidence = by_id[ref['evidence_id']]
            _require(ref['locator'] == evidence['locator'] and (not ref.get('quote') or ref['quote'] in evidence['text']),
                'Archived bridge exact citation differs')
    taxonomy = json.loads((ROOT / 'config/taxonomy.json').read_bytes())
    names = {row['label_id']: row for row in taxonomy['labels']}
    _require(all(row['label_id'] in names and row['axis'] == names[row['label_id']]['axis'] for row in labels),
        'Archived bridge taxonomy identity differs')
    provenance = verified['provenance']; native = provenance['native_provenance']
    return {'entry_id': 'lnw:wd:' + QID, 'work_id': 'lnw:wd:' + QID,
        'entity_kind': 'light_novel_work', 'scope': 'work', 'archive_context': CONTEXT,
        'target_record_sha256': digest(metadata), 'input_hash': provenance['input_hash'],
        'source_manifest_sha256': context['export']['source_preparation_sha256'],
        'native_result_sha256': digest(provenance['native_result']),
        'review_sha256': digest(admission), 'admitted_labels': deepcopy(labels),
        'labels': [{**deepcopy(row), 'label': names[row['label_id']]['label']} for row in labels],
        'held_labels': deepcopy(admission['held_labels']),
        'unknown_axes': deepcopy(provenance['native_result']['missing_axes']),
        'source_attributions': [deepcopy(by_id[key]) for key in sorted(citations)],
        'content_features_adopted': True, 'non_genre_content_features_adopted': True,
        'uncalibrated': True, 'human_quality_verified': False,
        'separate_internal_review_completed': True, 'fresh_classification': False,
        'native_review_requested': provenance['native_result']['needs_human_review'],
        'bridge_provenance': {'version': VERSION, 'context': CONTEXT,
            'original_work_id': verified['work_id'], 'original_input_work_id': verified['input_work_id'],
            'original_run_id': provenance['run_id'], 'original_input_hash': provenance['input_hash'],
            'original_prompt_version': provenance['native_result']['prompt_version'],
            'original_input_evidence_kinds': proposal['historical_input_evidence_kinds'],
            'original_native_provenance': deepcopy(native),
            'archive_manifest_path': source['manifest_path'], 'archive_manifest_sha256': source['manifest_sha256'],
            'metadata_path': source['metadata_path'], 'metadata_sha256': source['metadata_sha256'],
            'duplicate_inventory_path': source['duplicate_inventory_path'],
            'duplicate_inventory_sha256': source['duplicate_inventory_sha256'],
            'plot_source_witnesses': witnesses, 'fresh_plot_only_input': False,
            'native_result_modified': False, 'new_model_calls': 0},
        'output_license': provenance['output_license'], 'output_license_url': provenance['output_license_url']}


def _load_source(source):
    _require(set(source) == {'metadata_path', 'metadata_sha256', 'duplicate_inventory_path', 'duplicate_inventory_sha256',
        'manifest_path', 'manifest_sha256', 'archive_root'}, 'Archived bridge source pin fields differ')
    metadata = json.loads(_read(source['metadata_path'], source['metadata_sha256']))
    inventory = json.loads(_read(source['duplicate_inventory_path'], source['duplicate_inventory_sha256']))
    verify_metadata(metadata)
    _require(metadata['id'] == QID, 'Archived bridge bounded work identity differs')
    return metadata, inventory


def build_export(output, *, metadata_path, metadata_sha256, duplicate_inventory_path,
                 duplicate_inventory_sha256, manifest_path, manifest_sha256, archive_root):
    """Prepare one create-only reviewed archive-label export, never DB writes."""
    source = {key: str(Path(value).resolve()) for key, value in {
        'metadata_path': metadata_path, 'duplicate_inventory_path': duplicate_inventory_path,
        'manifest_path': manifest_path, 'archive_root': archive_root}.items()}
    source.update(metadata_sha256=metadata_sha256, duplicate_inventory_sha256=duplicate_inventory_sha256,
                  manifest_sha256=manifest_sha256)
    metadata, inventory = _load_source(source)
    context = _replay_archive(metadata, source['manifest_path'], manifest_sha256, source['archive_root'])
    row = _project(metadata, inventory, context, source)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    pins = {str((ROOT / relative).resolve()): hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() for relative in RUNTIME_FILES}
    for name in ('metadata', 'duplicate_inventory'):
        pins[source[name + '_path']] = source[name + '_sha256']
    pins[source['manifest_path']] = manifest_sha256
    frozen = output / 'frozen-verifier.py'; frozen.write_bytes(Path(__file__).read_bytes())
    pins[str(frozen)] = hashlib.sha256(frozen.read_bytes()).hexdigest()
    export = {'version': VERSION, 'context': CONTEXT, 'source': source, 'rows': [row],
        'gate': {'passed': True, 'selected': 1, 'adopted': 1, 'failed': 0, 'pending': 0,
                 'new_model_calls': 0, 'historical_classifications_modified': False},
        'artifact_sha256': pins, 'frozen_verifier': str(frozen),
        'output_license': 'CC-BY-SA-4.0', 'output_license_url': 'https://creativecommons.org/licenses/by-sa/4.0/'}
    _save(output / 'manifest.json', export)
    return export


def verify_feature_export(path, expected_sha256):
    path = Path(path).resolve(); raw = _read(path, expected_sha256); export = json.loads(raw)
    _require(export.get('version') == VERSION and export.get('context') == CONTEXT,
             'Unknown archived bridge version or historical context')
    pins = export.get('artifact_sha256', {})
    required = {str((ROOT / name).resolve()) for name in RUNTIME_FILES}
    required.update(export['source'][key] for key in ('metadata_path', 'duplicate_inventory_path', 'manifest_path'))
    required.add(export['frozen_verifier'])
    _require(set(pins) == required, 'Archived bridge verifier/source dependency closure differs')
    for name, pin in pins.items(): _read(name, pin)
    _require(Path(export['frozen_verifier']).read_bytes() == Path(__file__).read_bytes(),
        'Archived bridge immutable verifier differs')
    metadata, inventory = _load_source(export['source'])
    context = _replay_archive(metadata, export['source']['manifest_path'], export['source']['manifest_sha256'], export['source']['archive_root'])
    row = _project(metadata, inventory, context, export['source'])
    _require(export.get('rows') == [row] and export.get('gate') == {'passed': True, 'selected': 1, 'adopted': 1,
        'failed': 0, 'pending': 0, 'new_model_calls': 0, 'historical_classifications_modified': False}
        and export.get('output_license') == 'CC-BY-SA-4.0'
        and export.get('output_license_url') == 'https://creativecommons.org/licenses/by-sa/4.0/',
        'Archived bridge export projection/gate/license differs')
    _require(path.read_bytes() == raw, 'Archived bridge changed during verification')
    for name, pin in pins.items(): _read(name, pin)
    return {**export, 'rows': [{**row, 'artifacts': {str(path): expected_sha256}}]}
