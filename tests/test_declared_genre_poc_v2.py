"""A supplement must not replace adopted labels or unrelated public metadata."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('declared_genre_poc_v2', Path(__file__).resolve().parents[1] / 'scripts/build_declared_genre_poc_v2.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def packet(work_id='current', qid='Q1', value=1.0):
    return {'work_id': work_id, 'source_qid': qid, 'projection': {
        'metadata': {'source_qid': qid, 'title': 'New source title',
                     'titles': [{'value': 'New source title'}, {'value': 'Source alias'}]},
        'provisional_recommendation_features': {'genre.fantasy': value},
        'feature_provenance': {'genre.fantasy': [{'basis': 'source assertion'}]},
        'llm_generated': False, 'quality_verified': False}}


def row():
    return {'work_id': 'current', 'title': 'Existing admitted title', 'authors': ['Admitted author'],
            'display_title_aliases': ['Admitted alias'], 'read_status': 'unknown',
            'features': {}, 'feature_provenance': {}}


def test_explicit_featureless_update_preserves_admitted_public_metadata():
    original = row()
    candidates, cohort = [deepcopy(original)], {}
    builder.apply_packets(candidates, cohort, [packet()], {'genre.fantasy': 'Fantasy'}, existing_featureless=True)
    for key in ['work_id', 'title', 'authors', 'display_title_aliases', 'read_status']:
        assert candidates[0][key] == original[key]
    assert candidates[0]['features'] == {'genre.fantasy': 1.0}
    assert len(candidates) == 1


@pytest.mark.parametrize('features,explicit,identity', [({}, False, 'current'),
    ({'genre.mystery': 1.0}, True, 'current'), ({}, True, 'missing')])
def test_unapproved_or_nonempty_current_row_cannot_be_replaced(features, explicit, identity):
    current = row()
    current['features'] = features
    with pytest.raises(ValueError):
        builder.apply_packets([current], {}, [packet(identity)], {'genre.fantasy': 'Fantasy'}, existing_featureless=explicit)


@pytest.mark.parametrize('cohort,packets', [({'old': 'Q1'}, [packet('new')]),
    ({}, [packet('new'), packet('other')]), ({}, [packet('new'), packet('new', 'Q2')])])
def test_duplicate_source_or_work_identity_is_rejected(cohort, packets):
    with pytest.raises(ValueError):
        builder.apply_packets([], cohort, packets, {'genre.fantasy': 'Fantasy'})


@pytest.mark.parametrize('value', [True, 0.7, float('nan')])
def test_confidence_values_and_boolean_are_not_presence_features(value):
    with pytest.raises(ValueError):
        builder.apply_packets([], {}, [packet('new', value=value)], {'genre.fantasy': 'Fantasy'})
