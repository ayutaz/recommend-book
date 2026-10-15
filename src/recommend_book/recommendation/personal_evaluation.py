"""Fixed exploratory comparison; this pure evaluator grants no material rights."""
from copy import deepcopy
import random
from statistics import mean

from .evaluation import _digest, _normalize, _ranking_metrics, _split
from .next_evaluation import _title_key, rank_owned_titles
from .personal_ranking import rank_personal_features


def personal_evaluation_spec():
    return {'version': 'personal-content-comparison-v1', 'outer_folds': 5,
            'seed': 20261015, 'k': 10, 'parameter_search_n': 0,
            'methods': ['personal_content', 'owned_title_bigram', 'constant_no_popularity', 'random'],
            'personal_rule': 'cosine squared; fixed midpoint3/prior0.1/nearest20; unsupported stays null',
            'group_rule': 'connected known author/series/external IDs plus normalized identical titles',
            'denominator': 'all supplied original assessments, including unbound and missing-feature works',
            'fresh_holdout': False, 'selection_using_results': False}


def evaluate_personal_features(rows, *, spec):
    """Report aggregate metrics over a common pool with held assessments hidden.

    Known identity groups stay together; unknown authors cannot establish the
    absence of leakage. Historical observations are not a prospective trial.
    The outer source-bound adapter must authorize these exact private inputs.
    """
    if spec != personal_evaluation_spec():
        raise ValueError('Comparison must use the fixed preregistered specification')
    items = _normalize(rows)
    if any(row['rating'] is None for row in items):
        raise ValueError('Every evaluation row must have an original assessment')
    for row in items:
        title = _title_key(row['title'])
        if title:
            row['author_group_keys'] = sorted(set(row['author_group_keys']) | {'same_title:' + _digest(title)})
    split = _split(items, spec['outer_folds'], spec['seed'])
    index = {row['work_id']: row for row in items}
    folds = []
    for partition in split['folds']:
        train = [index[key] for key in partition['train_work_ids']]
        held = [index[key] for key in partition['heldout_work_ids']]
        candidates = deepcopy(held)
        for row in candidates:
            row['rating'] = None
            row['read_status'] = 'unknown'
        truth = {row['work_id']: row['rating'] for row in held}
        ids = sorted(truth)
        shuffled = ids[:]
        random.Random(spec['seed'] + partition['fold']).shuffle(shuffled)
        personal = rank_personal_features(candidates, train, limit=len(candidates))['ranking']
        lexical = rank_owned_titles(candidates, train, limit=len(candidates))['ranking']
        overlap = {}
        for field in ('author_ids', 'series_ids'):
            left = {value for row in train for value in row[field]}
            right = {value for row in held for value in row[field]}
            overlap[field] = len(left & right)
        if any(overlap.values()):
            raise ValueError('Known author/series identity crossed evaluation boundary')
        methods = {}
        for name, order, supported in (
            ('personal_content', [row['work_id'] for row in personal], sum(row['score'] is not None for row in personal)),
            ('owned_title_bigram', [row['work_id'] for row in lexical], sum(row['score'] is not None for row in lexical)),
            ('constant_no_popularity', ids, 0), ('random', shuffled, 0),
        ):
            methods[name] = {'metrics': _ranking_metrics(order, truth, spec['k']),
                             'candidate_n': len(ids), 'supported_candidate_n': supported}
        folds.append({'fold': partition['fold'], 'train_n': len(train), 'held_n': len(held),
                      'eligible_pool_sha256': _digest(ids), 'author_overlap_n': overlap['author_ids'],
                      'series_overlap_n': overlap['series_ids'], 'methods': methods})
    aggregate = {}
    for name in spec['methods']:
        metrics = {}
        for key in ('ndcg_at_k', 'precision_at_k', 'recall_at_k', 'ap_at_k'):
            values = [fold['methods'][name]['metrics'][key] for fold in folds
                      if fold['methods'][name]['metrics'][key] is not None]
            metrics[key] = mean(values) if values else None
        count = sum(fold['methods'][name]['candidate_n'] for fold in folds)
        supported = sum(fold['methods'][name]['supported_candidate_n'] for fold in folds)
        aggregate[name] = {'macro': metrics, 'candidate_n': count, 'supported_candidate_n': supported,
                           'supported_candidate_rate': supported / count if count else None}
    return {'version': 'personal-content-exploratory-comparison-v1', 'spec_sha256': _digest(spec),
            'input_sha256': _digest(items), 'split_sha256': _digest(split),
            'counts': {'assessments_n': len(items), 'connected_groups_n': split['group_count'],
                       'feature_bearing_works_n': sum(bool(row['features']) for row in items),
                       'known_author_works_n': sum(bool(row['author_ids']) for row in items),
                       'known_series_works_n': sum(bool(row['series_ids']) for row in items)},
            'folds': folds, 'methods': aggregate, 'quality_verified': False, 'fresh_holdout': False,
            'private_observations_exported': False,
            'limitations': ['Unbound assessments and unsupported deterministic ties remain in the denominator.',
                           'Missing author/series identities prevent certification of complete group independence.',
                           'Previously observed assessments and dependent folds do not establish personal satisfaction or future quality.',
                           'No API popularity values or retrospective annotation permission were used.']}
