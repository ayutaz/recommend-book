"""Personal content ranking must retain missingness and protect reference details."""
from copy import deepcopy
import json

import pytest

from recommend_book.recommendation.personal_ranking import rank_personal_features


def test_content_preference_changes_rank_without_exporting_private_observations():
    candidates = [
        {'work_id': 'public:a', 'title': 'A', 'features': {'genre.fantasy': 1}},
        {'work_id': 'public:b', 'title': 'B', 'features': {'genre.mystery': 1}},
    ]
    private = [
        {'work_id': 'reference:secret-a', 'title': 'PRIVATE TITLE A', 'rating': 5,
         'features': {'genre.fantasy': 1}, 'private_token': 'DO NOT EXPORT'},
        {'work_id': 'reference:secret-b', 'title': 'PRIVATE TITLE B', 'rating': 1,
         'features': {'genre.mystery': 1}},
    ]
    before = deepcopy(private)
    result = rank_personal_features(candidates, private, limit=2)
    assert [row['work_id'] for row in result['ranking']] == ['public:a', 'public:b']
    assert result['ranking'][0]['score'] > 0 > result['ranking'][1]['score']
    serialized = json.dumps(result)
    for secret in ('reference:secret', 'PRIVATE TITLE', 'private_token', 'DO NOT EXPORT', 'rating'):
        assert secret not in serialized
    assert private == before
    reverse = deepcopy(private)
    reverse[0]['rating'], reverse[1]['rating'] = 1, 5
    assert rank_personal_features(candidates, reverse)['ranking'][0]['work_id'] == 'public:b'


def test_identity_read_exclusion_and_unknown_status():
    references = [{'work_id': 'read', 'rating': 4, 'features': {'genre.fantasy': 1},
                   'external_ids': [{'source': 'wikidata', 'value': 'Q1'}]}]
    candidates = [
        {'work_id': 'read', 'features': {'genre.fantasy': 1}},
        {'work_id': 'other-edition', 'features': {'genre.fantasy': 1},
         'external_ids': [{'source': 'wikidata', 'value': 'Q1'}]},
        {'work_id': 'candidate', 'features': {'genre.fantasy': 1}, 'read_status': 'unknown'},
    ]
    result = rank_personal_features(candidates, references)
    assert [row['work_id'] for row in result['ranking']] == ['candidate']
    assert result['ranking'][0]['read_status'] == 'unknown'
    assert result['audit']['history_excluded_n'] == 2


def test_missing_content_is_unscored_and_expression_features_rejected():
    refs = [{'work_id': 'reference', 'rating': 5, 'features': {'genre.fantasy': 1}}]
    result = rank_personal_features([{'work_id': 'missing', 'features': {}}], refs)
    assert result['ranking'][0]['score'] is None
    assert result['ranking'][0]['score_status'] == 'unsupported'
    assert result['ranking'][0]['explanations'] == []
    with pytest.raises(ValueError, match='work-level'):
        rank_personal_features([{'work_id': 'style', 'features': {'style.lyrical': 1}}], refs)


def test_all_liked_references_still_rank_by_feature_support():
    refs = [{'work_id': 'reference', 'rating': 5, 'features': {'genre.fantasy': 1}}]
    result = rank_personal_features([
        {'work_id': 'weak', 'features': {'genre.fantasy': 1, 'theme.family': 1}},
        {'work_id': 'strong', 'features': {'genre.fantasy': 1}},
    ], refs)
    assert result['ranking'][0]['work_id'] == 'strong'
    assert result['ranking'][0]['score'] > result['ranking'][1]['score'] > 0


@pytest.mark.parametrize('bad', [True, float('nan'), 0, 6])
def test_invalid_private_values_rejected_without_leaking_details(bad):
    with pytest.raises(ValueError):
        rank_personal_features([{'work_id': 'candidate'}],
                              [{'work_id': 'private', 'rating': bad, 'features': {}}])
