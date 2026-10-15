import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.evaluation import evaluate_results


def reference_module():
    path = Path(__file__).parents[1] / 'scripts' / 'prepare_curated_reference.py'
    spec = importlib.util.spec_from_file_location('prepare_curated_reference', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native(**labels):
    return {
        'reference_id': 'ne0001',
        'source_sha': '8fccf669ed1bdbad309ff190f5afb0d6066d70a3',
        'source_url': 'https://github.com/cligs/conssa/blob/8fccf669ed1bdbad309ff190f5afb0d6066d70a3/metadata/metadata.tsv',
        'license': 'CC-BY-4.0', 'attribution': 'José Calvo Tello, CoNSSA, 2021',
        'title': 'A title', 'author': 'A writer', 'year': '1900',
        'reference_certainty': 'high', 'source_text_availability': 'restricted',
        'full_text_rights_verified': False, 'provenance': 'curated_reference',
        'native_labels': labels,
    }


def taxonomy():
    return json.loads((Path(__file__).parents[1] / 'config' / 'taxonomy.json').read_text('utf-8'))


def test_direct_positive_reference_remains_separate_from_human_quality():
    module = reference_module()
    record, audit = module.convert_reference(native(**{'protagonist.age': 'child', 'protagonist.profession': 'student'}), taxonomy())
    labels = {r['label_id']: r for r in record['labels']}
    assert set(labels) == {'protagonist.child', 'protagonist.student'}
    assert all(r['decision'] == 'present' and r['relevance'] is None and r['raw_confidence'] is None for r in labels.values())
    assert labels['protagonist.child']['native_field'] == 'protagonist.age'
    assert labels['protagonist.student']['native_value'] == 'student'
    assert record['work_id'] == 'reference:conssa:ne0001'
    assert record['identity_status'] == 'unresolved' and record['evaluation_ready'] is False
    assert record['source_annotation_certainty'] == 'high'
    assert record['attribution'] and record['changes'] and audit['unmapped_fields'] == []
    predictions = [{'work_id':record['work_id'], 'labels':[{'label_id':k,'decision':'present','raw_confidence':.9} for k in labels]}]
    report = evaluate_results(predictions, [record], taxonomy())
    assert report['human'] == {} and report['validation_status'] == 'human_validation_pending'
    assert report['calibration']['status'] == 'uncalibrated'


@pytest.mark.parametrize('narrator', ['homodiegetic', 'autodiegetic', 'heterodiegetic', 'epistolary', 'mixed', 'dialogue'])
def test_ambiguous_native_narrator_and_genre_never_become_gold(narrator):
    module = reference_module()
    record, audit = module.convert_reference(native(**{
        'narrator': narrator, 'protagonist.age': 'young',
        'protagonist.profession': 'military personnel', 'end': 'positive',
        'subgenre.cligs': 'fantastic,historical,social',
        'subgenre.edit.wikidata': 'fantasy',
    }), taxonomy())
    assert record['labels'] == []
    assert not any(r['decision'] == 'absent' for r in record['labels'])
    assert any(r['native_field']=='narrator' and r['conversion_status']=='unsupported' for r in audit['unmapped_fields'])
    assert any(r['native_field']=='subgenre.edit.wikidata' and r['conversion_status']=='excluded_external_genre' for r in audit['unmapped_fields'])


def test_taxonomy_definition_or_source_revision_drift_rejected():
    module = reference_module()
    tax = taxonomy()
    next(r for r in tax['labels'] if r['label_id']=='protagonist.child')['definition'] = 'Any young person'
    with pytest.raises(ValueError, match='definition drift'):
        module.convert_reference(native(**{'protagonist.age':'child'}), tax)
    record = native(**{'protagonist.age':'adult', 'protagonist.profession':'artist'})
    record['source_sha'] = 'unreviewed-new-revision'
    with pytest.raises(ValueError, match='source revision'):
        module.convert_reference(record, taxonomy())


def test_modified_snapshot_rejected_before_output_creation(tmp_path):
    module = reference_module()
    path = tmp_path/'changed.jsonl'
    path.write_text(json.dumps(native(**{'protagonist.age':'child'}))+'\n', encoding='utf-8')
    target = tmp_path/'output'
    with pytest.raises(ValueError, match='snapshot hash mismatch'):
        module.prepare_reference(path, Path(__file__).parents[1]/'config/taxonomy.json', target)
    assert not target.exists()
