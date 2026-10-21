"""Focused schema and recipe boundaries for the foreign-work review fix."""
import importlib.util
from pathlib import Path
import sys

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_review5_schema_accepts_only_the_selected_five_work_ids():
    review = load('review_scale_content_v5_fixture', 'scripts/review_scale_content_v5.py')
    schema = review.review_schema(['w-2', 'w-1'])
    valid = {'reviews': [{'work_id': 'w-1', 'reviewed_label_ids': [],
                          'summary': 'ok', 'findings': []}]}
    Draft202012Validator(schema).validate(valid)
    foreign = {'reviews': [{'work_id': 'foreign', 'reviewed_label_ids': [],
                            'summary': 'wrong', 'findings': []}]}
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(foreign)


@pytest.mark.parametrize('ids', [[], ['w-1', 'w-1'], ['w-' + str(i) for i in range(6)]])
def test_review5_rejects_empty_duplicate_or_oversized_groups(ids):
    review = load('review_scale_content_v5_group_fixture', 'scripts/review_scale_content_v5.py')
    with pytest.raises(ValueError):
        review.review_schema(ids)


def test_review5_keeps_protocol_fields_and_pins_schema_and_both_review_code_bytes():
    review = load('review_scale_content_v5_protocol_fixture', 'scripts/review_scale_content_v5.py')
    import review_world_pilot_v1 as original
    base = original.review_schema()
    selected = review.review_schema(['w'])
    selected['properties']['reviews']['items']['properties']['work_id'].pop('enum')
    assert selected == base
    source = review.adapted_source((ROOT / 'scripts/review_scale_content_v4.py').read_bytes())
    assert "pin(args.output / 'schema.json')" in source
    assert 'review_scale_content_v4.py' in source
    assert 'review_scale_content_v3.py' in source


def test_review5_bounded_module_exposes_the_enum_schema_without_changing_semantic_protocol():
    review = load('review_scale_content_v5_module_fixture', 'scripts/review_scale_content_v5.py')
    bounded = review.load_bounded_review(['selected'], 123)
    schema = bounded.review_schema()
    assert schema['properties']['reviews']['items']['properties']['work_id']['enum'] == ['selected']
    assert schema['properties']['reviews']['items']['properties']['summary'] == {'type': 'string'}
    assert bounded.INSTRUCTIONS == load('review_world_protocol_fixture', 'scripts/review_world_pilot_v1.py').INSTRUCTIONS


def test_coordinator4_binds_review5_and_rejects_coordinator_drift():
    coordinator = load('coordinator4_fixture', 'scripts/run_scale100k_content_classification_v4.py')
    source = coordinator.adapted_source((ROOT / 'scripts/run_scale100k_content_classification_v3.py').read_bytes())
    assert 'from review_scale_content_v5 import execute;' in source
    assert 'review_scale_content_v5.py' in source
    assert 'run_scale100k_native_v4.py' in source
    assert 'prepare_scale100k_review_recovery_sources_v1.py' in source
    assert 'generation-v7-entry.json' in source
    assert coordinator.REVIEW_SHA == __import__('hashlib').sha256(
        (ROOT / 'scripts/review_scale_content_v5.py').read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='Frozen'):
        coordinator.adapted_source(b'changed')
