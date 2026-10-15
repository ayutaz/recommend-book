import json

import pytest

from recommend_book.recommendation.personal_evaluation import evaluate_personal_features, personal_evaluation_spec


def samples(features=True):
    return [{'work_id': f'PRIVATE-ID-{i}', 'title': f'PRIVATE-TITLE-{i}',
             'rating': 1 + i % 5, 'author_ids': [f'author-{i // 2}'],
             'features': {'genre.fantasy': 1} if features else {}}
            for i in range(12)]


def test_common_pool_and_known_author_grouping_without_private_export():
    report = evaluate_personal_features(samples(), spec=personal_evaluation_spec())
    assert report['counts']['assessments_n'] == 12
    assert report['counts']['known_author_works_n'] == 12
    assert report['counts']['connected_groups_n'] == 6
    for fold in report['folds']:
        assert fold['author_overlap_n'] == fold['series_overlap_n'] == 0
        assert len({method['candidate_n'] for method in fold['methods'].values()}) == 1
    assert 'PRIVATE-ID' not in json.dumps(report)
    assert 'PRIVATE-TITLE' not in json.dumps(report)
    assert report['quality_verified'] is False and report['fresh_holdout'] is False


def test_missing_features_stay_in_full_denominator_and_have_no_support():
    report = evaluate_personal_features(samples(False), spec=personal_evaluation_spec())
    method = report['methods']['personal_content']
    assert report['counts']['feature_bearing_works_n'] == 0
    assert method['supported_candidate_n'] == 0
    assert method['candidate_n'] == 12
    assert method['supported_candidate_rate'] == 0
    assert method['macro']['ndcg_at_k'] == report['methods']['constant_no_popularity']['macro']['ndcg_at_k']


def test_comparison_is_deterministic_and_contract_cannot_be_retuned():
    rows = samples()
    spec = personal_evaluation_spec()
    assert evaluate_personal_features(rows, spec=spec) == evaluate_personal_features(rows, spec=spec)
    spec['k'] = 1
    with pytest.raises(ValueError, match='fixed'):
        evaluate_personal_features(rows, spec=spec)
