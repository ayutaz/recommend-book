"""Local deterministic content recommendations with explicit unread uncertainty."""
from __future__ import annotations

from copy import deepcopy
import json
import math
from numbers import Real


METHODS = frozenset({'personal', 'content', 'popularity'})
NEIGHBORS = 20
PRIOR = 0.1
POSITIVE_RATING = 4


def _number(value, name, *, minimum=None, maximum=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f'{name} must be a finite number, not bool')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} is outside the permitted range')


def _external_ids(row):
    values = row.get('external_ids', [])
    if not isinstance(values, list):
        raise ValueError('external_ids must be a list')
    result = set()
    for item in values:
        if not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k] for k in ('source', 'value')):
            raise ValueError('external IDs need nonempty source/value strings')
        result.add((item['source'], item['value']))
    return result


def _rows(values, scope, *, identity_only=False):
    if not isinstance(values, list):
        raise ValueError(f'{scope} must be a list of rows')
    result = []
    seen = set()
    for original in values:
        if not isinstance(original, dict):
            raise ValueError(f'{scope} needs row mappings')
        work_id = original.get('work_id')
        if work_id is not None and (not isinstance(work_id, str) or not work_id):
            raise ValueError(f'{scope} needs nonempty work_id strings')
        if work_id is None and not identity_only:
            raise ValueError(f'{scope} needs nonempty work_id strings')
        if work_id is not None:
            if work_id in seen:
                raise ValueError(f'duplicate work_id in {scope}: {work_id}')
            seen.add(work_id)
        row = deepcopy(original)
        external = _external_ids(row)
        if work_id is None and not external:
            raise ValueError('read_history needs an explicit work_id or external identity')
        if row.get('rating') is not None:
            _number(row['rating'], 'rating', minimum=1, maximum=5)
        if row.get('read_status', 'unknown') not in ('read', 'unread', 'unknown'):
            raise ValueError('read_status must be read, unread or unknown')
        if identity_only:
            result.append(row)
            continue
        for key, default in {'title': row['work_id'], 'features': {}, 'rating': None,
                             'author_ids': [], 'series_ids': [], 'read_status': 'unknown',
                             'external_ids': [], 'feature_provenance': {}, 'popularity': None}.items():
            row.setdefault(key, deepcopy(default))
        if not isinstance(row['title'], str):
            raise ValueError('title must be a string')
        for key in ('author_ids', 'series_ids'):
            if not isinstance(row[key], list) or any(not isinstance(v, str) or not v for v in row[key]):
                raise ValueError(f'{key} must be a list of nonempty strings')
        if not isinstance(row['features'], dict):
            raise ValueError('features must be a mapping')
        for feature, value in row['features'].items():
            if not isinstance(feature, str) or not feature:
                raise ValueError('feature IDs must be nonempty strings')
            _number(value, 'feature value', minimum=0)
        provenance = row['feature_provenance']
        if not isinstance(provenance, dict) or any(not isinstance(k, str) or not isinstance(v, list) or
                any(not isinstance(record, dict) for record in v) for k, v in provenance.items()):
            raise ValueError('feature_provenance must map feature IDs to lists of record mappings')
        try:
            json.dumps(provenance, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError('feature provenance must contain finite JSON values') from exc
        if row['popularity'] is not None:
            _number(row['popularity'], 'popularity')
        result.append(row)
    return result


def _unit(features):
    """Scale before normalization so finite extreme feature values cannot overflow."""
    positive = {k: float(v) for k, v in features.items() if v > 0}
    if not positive:
        return {}
    scale = max(positive.values())
    scaled = {k: v / scale for k, v in positive.items()}
    length = math.sqrt(math.fsum(v * v for v in scaled.values()))
    return {k: v / length for k, v in scaled.items()}


def _cosine(left, right):
    return min(1.0, max(0.0, math.fsum(left[k] * right[k] for k in sorted(left.keys() & right.keys()))))


def _feature_evidence(candidate, reference):
    shared = sorted(k for k in candidate['features'].keys() & reference['features'].keys()
                    if candidate['features'][k] > 0 and reference['features'][k] > 0)
    return [{'feature_id': feature,
             'candidate_provenance': deepcopy(candidate['feature_provenance'].get(feature, [])),
             'reference_provenance': deepcopy(reference['feature_provenance'].get(feature, []))}
            for feature in shared]


def _evidence_count(candidate, explanations):
    records = set()
    for explanation in explanations:
        for feature in explanation.get('features', []):
            for side, work_id in (('candidate', candidate['work_id']), ('reference', explanation['reference_work_id'])):
                for record in feature[f'{side}_provenance']:
                    records.add((work_id, feature['feature_id'], json.dumps(record, sort_keys=True, allow_nan=False)))
    return len(records)


def _unsupported(reason):
    return None, [{'reason': reason}]


def _score(candidate, references, units, method):
    if method == 'popularity':
        if candidate['popularity'] is None:
            return _unsupported('No observed popularity value')
        return float(candidate['popularity']), [{'reason': 'Observed popularity baseline', 'popularity': candidate['popularity']}]
    candidate_unit = _unit(candidate['features'])
    if not candidate_unit:
        return _unsupported('No positive candidate features')
    rated = [r for r in references if r['rating'] is not None]
    if method == 'personal':
        if not rated:
            return _unsupported('No rated reference works')
        mean = math.fsum(r['rating'] for r in rated) / len(rated)
        nearest = [(r, _cosine(candidate_unit, units[r['work_id']])) for r in rated]
        nearest = sorted((pair for pair in nearest if pair[1] > 0), key=lambda pair: (-pair[1], pair[0]['work_id']))[:NEIGHBORS]
        if not nearest:
            return _unsupported('No positive similarity to rated references')
        denominator = PRIOR + math.fsum(similarity ** 2 for _, similarity in nearest)
        explanations = []
        for reference, similarity in nearest:
            weight = similarity ** 2
            centered = reference['rating'] - mean
            explanations.append({'reference_work_id': reference['work_id'], 'similarity': similarity,
                                 'weight': weight, 'rating': reference['rating'], 'reference_rating_mean': mean,
                                 'centered_rating': centered, 'contribution': weight * centered / denominator,
                                 'features': _feature_evidence(candidate, reference)})
        return mean + math.fsum(e['contribution'] for e in explanations), explanations
    positive = sorted((r for r in rated if r['rating'] >= POSITIVE_RATING and units[r['work_id']]), key=lambda r: r['work_id'])
    if not positive:
        return _unsupported('No positive rated references with usable features')
    explanations = []
    for reference in positive:
        features = _feature_evidence(candidate, reference)
        explanations.append({'reference_work_id': reference['work_id'],
                             'similarity': _cosine(candidate_unit, units[reference['work_id']]),
                             'features': features,
                             'comparison_status': 'shared_positive_features' if features else 'no_shared_positive_features',
                             'reason': 'Cosine of available feature vectors; zero means no observed shared positive feature'})
    return math.fsum(e['similarity'] for e in explanations) / len(explanations), explanations


def rank_candidates(candidates, references, *, method='personal', limit=20,
                    read_history=None, require_confirmed_unread=False):
    """Rank a common eligible pool; unknown read status remains explicitly unknown.

    Features and provenance are supplied by domain adapters. No source inference,
    cross-domain ID mapping, human quality guarantee or calibrated prediction occurs.
    """
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('method must be personal, content or popularity')
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('limit must be a nonnegative integer')
    if not isinstance(require_confirmed_unread, bool):
        raise ValueError('require_confirmed_unread must be bool')
    candidates = _rows(candidates, 'candidates')
    references = _rows(references, 'references')
    history = _rows(read_history, 'read_history', identity_only=True) if read_history is not None else []
    reference_ids = {r['work_id'] for r in references}
    reference_external = set().union(*(_external_ids(r) for r in references)) if references else set()
    history_with_ids = [(r, _external_ids(r)) for r in history]
    eligible = []
    excluded = []
    history_conflicts = []
    for candidate in sorted(candidates, key=lambda r: r['work_id']):
        reasons = []
        external = _external_ids(candidate)
        if candidate['work_id'] in reference_ids:
            reasons.append('reference_work_id')
        if external & reference_external:
            reasons.append('reference_external_id')
        matching = [(r, ids) for r, ids in history_with_ids
                    if r.get('work_id') == candidate['work_id'] or external & ids]
        known_read = [(r, ids) for r, ids in matching if r.get('read_status', 'unknown') == 'read' or r.get('rating') is not None]
        if any(r.get('work_id') == candidate['work_id'] for r, _ in known_read):
            reasons.append('read_history_work_id')
        if any(external & ids for _, ids in known_read):
            reasons.append('read_history_external_id')
        statuses = {r.get('read_status', 'unknown') for r, _ in matching}
        original_status = candidate['read_status']
        if not known_read and original_status != 'read' and 'unread' in statuses:
            candidate['read_status'] = 'unknown' if 'unknown' in statuses else 'unread'
        if len(statuses) > 1:
            history_conflicts.append({'work_id': candidate['work_id'], 'history_statuses': sorted(statuses),
                                      'resolution': 'read_or_known_rating' if known_read else candidate['read_status']})
        if matching:
            candidate['read_status_basis'] = {'candidate_status': original_status,
                                              'matching_history_statuses': sorted(statuses),
                                              'known_history_rating': any(r.get('rating') is not None for r, _ in matching)}
        if candidate['read_status'] == 'read':
            reasons.append('explicit_read_status')
        if candidate['rating'] is not None:
            reasons.append('known_candidate_rating')
        if require_confirmed_unread and candidate['read_status'] != 'unread':
            reasons.append('unread_not_confirmed')
        if reasons:
            excluded.append({'work_id': candidate['work_id'], 'reasons': reasons})
        else:
            eligible.append(candidate)
    units = {r['work_id']: _unit(r['features']) for r in references}
    ranking = []
    for candidate in eligible:
        score, explanations = _score(candidate, references, units, method)
        candidate.update(score=score, score_status='supported' if score is not None else 'unsupported',
                         explanations=explanations, evidence_count=_evidence_count(candidate, explanations))
        ranking.append(candidate)
    ranking.sort(key=lambda r: (r['score'] is None, -r['score'] if r['score'] is not None else 0, r['work_id']))
    audit = {'method': method, 'input_candidates': len(candidates), 'references': len(references),
             'eligible_work_ids': [r['work_id'] for r in eligible], 'eligible_count': len(eligible),
             'excluded': excluded, 'read_history_supplied': read_history is not None,
             'history_conflicts': history_conflicts,
             'confirmed_unread_required': require_confirmed_unread,
             'unknown_read_status_count': sum(r['read_status'] == 'unknown' for r in eligible),
             'supported_count': sum(r['score'] is not None for r in ranking),
             'unsupported_count': sum(r['score'] is None for r in ranking), 'returned': min(limit, len(ranking)),
             'settings': {'neighbors': NEIGHBORS, 'prior': PRIOR, 'positive_rating_minimum': POSITIVE_RATING},
             'human_quality_verified': False, 'confidence_calibrated': False,
             'diversity_limits_applied': False, 'external_requests': 0}
    return {'ranking': ranking[:limit], 'audit': audit}
