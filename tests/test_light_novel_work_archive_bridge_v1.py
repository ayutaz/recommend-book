"""Archive bridge orchestration. Only the expensive frozen CLI replay is stubbed.

Typed work admission, exact archived plot/raw replay, title closure and export
pin/projection verification all run normally. Real archive replay is separate.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from test_light_novel_admission_v3 import source
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_archive_reuse_v1 import bundle, dump
from recommend_book import light_novel_work_archive_bridge_v1 as bridge
from recommend_book.light_novel_work_admission_v3 import prepare_metadata
from recommend_book.light_novel_work_archive_reuse_v1 import _proposal_from_verified


@pytest.fixture
def prepared(tmp_path, work_source, bundle, monkeypatch):
    old = work_source()
    raw = json.loads(Path(old['provenance']['raw_path']).read_bytes())
    entity = raw['entities']['Q1']; entity['id'] = bridge.QID
    target = tmp_path / 'current.json'
    pin = dump(target, {'entities': {bridge.QID: entity}})
    provenance = {**old['provenance'], 'raw_path': str(target), 'raw_sha256': pin,
                  'url': 'https://www.wikidata.org/wiki/' + bridge.QID}
    metadata = prepare_metadata(bridge.QID, provenance, old['work_admission_proof']['type_authorities'])
    _, verified, mapping, ordered, run, export = bundle
    ordered['evidence'].append({'evidence_id': 'subject', 'kind': 'subject'})
    verified.update(qid=bridge.QID, work_id='wd:' + bridge.QID)
    mapping.update(wikidata_qid=bridge.QID, catalogue_work_id='wd:' + bridge.QID)
    evidence = ordered['evidence'][0]
    for pathkey, hashkey in [('raw_path', 'raw_sha256')]:
        attr = evidence['attribution']; p = Path(attr[pathkey])
        data = json.loads(p.read_bytes()); data['query']['pages'][1]['pageprops']['wikibase_item'] = bridge.QID
        attr[hashkey] = dump(p, data)
    identity = evidence['attribution']['identity_proof']
    p = Path(identity['target_raw_path']); data = json.loads(p.read_bytes())
    e = data['entities'].pop('Q1'); e['id'] = bridge.QID; data['entities'][bridge.QID] = e
    identity['target_raw_sha256'] = dump(p, data)
    identity.update(wikidata_work_id=bridge.QID, pageprops_wikibase_item=bridge.QID)
    first = verified['labels'][0]
    first.update(decision='present', calibration_status='uncalibrated', calibrated_confidence=None)
    second = deepcopy(first); second.update(label_id='atmosphere.warm', axis='atmosphere')
    verified['labels'].append(second)
    admission = verified['provenance']['label_admission']
    admission.update(admitted_labels=deepcopy(verified['labels']),
                     separate_review_cleared_label_ids=['theme.memory', 'atmosphere.warm'])
    verified['provenance']['native_result'] = {'missing_axes': ['setting'],
        'needs_human_review': True, 'prompt_version': '4.0-provenance'}
    export['source_preparation_sha256'] = 'c' * 64
    context = {'proposal': _proposal_from_verified(metadata, verified, mapping, ordered, run, export),
               'verified': verified, 'ordered': ordered, 'export': export}
    inventory = bridge.duplicate_inventory([{'entry_id': 'lnw:wd:' + bridge.QID,
        'entry_kind': 'work', 'record': metadata, 'record_sha256': bridge.digest(metadata)}])
    paths = {}
    for name, value in [('metadata', metadata), ('duplicate_inventory', inventory), ('manifest', export)]:
        p = tmp_path / (name + '.json'); paths[name + '_path'] = p
        paths[name + '_sha256'] = dump(p, value)
    paths['archive_root'] = tmp_path
    def replay(meta, path, pin, root):
        bridge._read(path, pin)
        return deepcopy(context)
    monkeypatch.setattr(bridge, '_replay_archive', replay)
    return metadata, inventory, context, paths


def make_export(tmp_path, prepared):
    output = tmp_path / 'export'
    document = bridge.build_export(output, **prepared[3])
    path = output / 'manifest.json'
    return document, path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_reviewed_labels_round_trip_preserves_original_context(tmp_path, prepared):
    before = deepcopy(prepared[:3]); document, path, pin = make_export(tmp_path, prepared)
    verified = bridge.verify_feature_export(path, pin); row = verified['rows'][0]
    assert row['entry_id'] == 'lnw:wd:Q110895870'
    assert row['admitted_labels'] == prepared[2]['verified']['labels']
    assert row['archive_context'] == bridge.CONTEXT and row['fresh_classification'] is False
    assert row['bridge_provenance']['fresh_plot_only_input'] is False
    assert row['bridge_provenance']['original_input_evidence_kinds'] == ['description', 'note', 'subject', 'synopsis']
    assert row['bridge_provenance']['new_model_calls'] == 0
    assert row['held_labels'] == [{'label_id': 'protagonist.caregiver'}]
    assert row['unknown_axes'] == ['setting'] and row['native_review_requested'] is True
    assert row['uncalibrated'] is True and row['human_quality_verified'] is False
    assert row['source_attributions'][0]['license'] == 'CC-BY-SA-4.0'
    assert row['content_features_adopted'] and row['non_genre_content_features_adopted']
    assert row['artifacts'] == {str(path.resolve()): pin}
    assert prepared[:3] == before


@pytest.mark.parametrize('change', ['fresh', 'series', 'held', 'raw_label', 'metadata', 'quote', 'source_unit', 'raw_hash'])
def test_bridge_rejects_unsafe_historical_reinterpretation(tmp_path, prepared, change):
    metadata, inventory, context, paths = prepared
    if change == 'fresh': context['proposal']['fresh_plot_only_input'] = True
    elif change == 'series': context['verified']['labels'][0]['scope'] = 'series'
    elif change == 'held': context['verified']['provenance']['label_admission']['held_labels'].append({'label_id': 'theme.memory'})
    elif change == 'raw_label': context['verified']['labels'].append({'label_id': 'protagonist.caregiver'})
    elif change == 'metadata': context['ordered']['evidence'][0]['kind'] = 'description'
    elif change == 'quote':
        for container in [context['proposal']['candidate_labels'], context['verified']['labels'],
                          context['verified']['provenance']['label_admission']['admitted_labels']]:
            container[0]['evidence'][0]['quote'] = 'fabricated quote'
    elif change == 'source_unit': context['ordered']['evidence'][0]['expression_ref'] = 'translation1'
    else: context['ordered']['evidence'][0]['attribution']['raw_sha256'] = '0' * 64
    with pytest.raises((ValueError, KeyError)):
        bridge.build_export(tmp_path / 'export', **paths)


@pytest.mark.parametrize('kind', ['work', 'series', 'publisher_group'])
def test_current_titleless_anchor_and_publisher_group_conflicts_hold(prepared, kind):
    metadata, inventory, context, paths = prepared
    record = {'anchor_edition_record': {'source_record': {'title': metadata['title']}}}
    other = bridge.duplicate_inventory([{'entry_id': 'other', 'entry_kind': kind,
        'record': record, 'record_sha256': bridge.digest(record)}])
    with pytest.raises(ValueError, match='crosswalk hold'):
        bridge._project(metadata, other, context, paths)


@pytest.mark.parametrize('field', ['rows', 'context', 'gate', 'output_license', 'artifact_sha256'])
def test_export_projection_tampering_rejected_even_with_new_manifest_pin(tmp_path, prepared, field):
    document, path, _ = make_export(tmp_path, prepared)
    if field == 'rows': document['rows'][0]['labels'][0]['label'] = 'fabricated'
    elif field == 'context': document[field] = 'fresh_plot_only'
    elif field == 'gate': document[field]['new_model_calls'] = 1
    elif field == 'output_license': document[field] = 'CC0'
    else: document[field].pop(str((bridge.ROOT / 'config/taxonomy.json').resolve()))
    pin = dump(path, document)
    with pytest.raises(ValueError): bridge.verify_feature_export(path, pin)


@pytest.mark.parametrize('source_name', ['metadata', 'duplicate_inventory', 'manifest'])
def test_pinned_sources_are_rechecked(tmp_path, prepared, source_name):
    _, path, pin = make_export(tmp_path, prepared)
    prepared[3][source_name + '_path'].write_text('{}', encoding='utf8')
    with pytest.raises(ValueError): bridge.verify_feature_export(path, pin)


def test_duplicate_inventory_rejects_unchecked_native_row(prepared):
    with pytest.raises(ValueError, match='native record SHA'):
        bridge.duplicate_inventory([{'entry_id': 'bad', 'entry_kind': 'work',
            'record': prepared[0], 'record_sha256': '0' * 64}])


@pytest.mark.parametrize('field', ['authors', 'original_languages', 'kind', 'id'])
def test_current_typed_work_projection_cannot_be_relabelled(prepared, field):
    metadata, inventory, context, paths = prepared
    metadata[field] = {'authors': [], 'original_languages': ['en'], 'kind': 'series',
                       'id': 'Q4729904'}[field]
    with pytest.raises(ValueError): bridge._project(metadata, inventory, context, paths)


def test_same_qid_series_requires_crosswalk_hold(prepared):
    metadata, inventory, context, paths = prepared
    inventory['rows'].append({'entry_id': 'lns:wd:' + bridge.QID,
        'entry_kind': 'series', 'record_sha256': 'f' * 64, 'title': '', 'aliases': []})
    with pytest.raises(ValueError, match='series identity'):
        bridge._project(metadata, inventory, context, paths)


@pytest.mark.parametrize('change', ['none', 'sha', 'target', 'absent', 'unchecked', 'new_duplicate', 'context', 'scope'])
def test_live_catalogue_target_guard_rechecks_current_native_inventory(prepared, change):
    metadata, inventory, context, paths = prepared
    row = bridge._project(metadata, inventory, context, paths)
    record = deepcopy(metadata); pin = bridge.digest(record)
    current = [{'entry_id': row['entry_id'], 'entry_kind': 'work',
                'record': record, 'record_sha256': pin}]
    if change == 'sha': pin = '0' * 64
    elif change == 'target': record['title'] = 'forged'
    elif change == 'absent': current[0]['entry_id'] = 'other'
    elif change == 'unchecked': current[0]['record_sha256'] = '0' * 64
    elif change == 'new_duplicate':
        other = {'anchor_edition_record': {'source_record': {'title': metadata['title']}}}
        current.append({'entry_id': 'other', 'entry_kind': 'publisher_group',
                        'record': other, 'record_sha256': bridge.digest(other)})
    elif change == 'context': row['archive_context'] = 'fresh_plot_only'
    elif change == 'scope': row['scope'] = 'series'
    if change == 'none': assert bridge.check_current_target(row, record, pin, current) is True
    else:
        with pytest.raises(ValueError): bridge.check_current_target(row, record, pin, current)
