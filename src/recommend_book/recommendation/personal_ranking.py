"""Pure content arithmetic; callers must admit sources and private purpose first."""
from __future__ import annotations

import math

from .engine import _cosine, _rows, _unit, rank_candidates

MIDPOINT = 3.0
PRIOR = 0.1
NEIGHBORS = 20


def _work_features(rows):
    for row in rows:
        for label in row['features']:
            if label.startswith('style.') or any(
                proof.get('scope', 'work') != 'work' or proof.get('expression_id')
                for proof in row['feature_provenance'].get(label, [])
            ):
                raise ValueError('Only admitted work-level content features can be ranked')


def rank_personal_features(candidates, references, *, limit=20, read_history=None):
    """Score signed preferences without exporting source observations or identities.

    The midpoint, prior and neighbor count are fixed before measured evaluation.
    Missing candidate/shared features remain unsupported. No confidence values are
    used as weights. A numeric score is neither an observed assessment nor a
    calibrated probability. Source/purpose admission belongs to the outer adapter.
    """
    candidates = _rows(candidates, 'candidates')
    references = _rows(references, 'references')
    _work_features(candidates)
    _work_features(references)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError('limit must be a nonnegative integer')
    # Reuse the shared engine's identity/history rules without scoring private data twice.
    identities = [{'work_id': row['work_id'], 'external_ids': row['external_ids']}
                  for row in references]
    eligibility = rank_candidates(candidates, identities, method='personal', limit=len(candidates),
                                  read_history=read_history)
    prepared = [(row, _unit(row['features'])) for row in references if row['rating'] is not None]
    ranked = []
    for candidate in eligibility['ranking']:
        unit = _unit(candidate['features'])
        neighbors = [(row, other, _cosine(unit, other)) for row, other in prepared] if unit else []
        neighbors = sorted((item for item in neighbors if item[2] > 0),
                           key=lambda item: (-item[2], item[0]['work_id']))[:NEIGHBORS]
        denominator = PRIOR + math.fsum(similarity ** 2 for _, _, similarity in neighbors)
        score = math.fsum(similarity ** 2 * (row['rating'] - MIDPOINT)
                          for row, _, similarity in neighbors) / denominator if neighbors else None
        contributions = {}
        for row, other, similarity in neighbors:
            centered = row['rating'] - MIDPOINT
            for label in unit.keys() & other.keys():
                value = similarity * centered * unit[label] * other[label] / denominator
                contributions.setdefault(label, []).append(value)
        explanations = [{'feature_id': label, 'contribution': math.fsum(values)}
                        for label, values in sorted(contributions.items())]
        # Explicit allowlist: never copy reference IDs, titles, values, proofs or caller extras.
        ranked.append({'work_id': candidate['work_id'], 'title': candidate['title'],
                       'authors': candidate.get('authors', []),
                       'display_title_aliases': candidate.get('display_title_aliases', []),
                       'read_status': candidate['read_status'], 'content_labels': sorted(candidate['features']),
                       'score': score, 'score_status': 'supported' if neighbors else 'unsupported',
                       'preference_support_count': len(neighbors), 'explanations': explanations})
    ranked.sort(key=lambda row: (row['score'] is None, -(row['score'] or 0), row['work_id']))
    return {'version': 'personal-feature-ranking-v1', 'ranking': ranked[:limit],
            'audit': {'algorithm': 'signed_feature_cosine_v1', 'midpoint': MIDPOINT,
                      'prior': PRIOR, 'neighbors': NEIGHBORS, 'input_candidates': len(candidates),
                      'eligible_n': len(ranked), 'supported_n': sum(row['score'] is not None for row in ranked),
                      'history_excluded_n': len(candidates) - len(ranked),
                      'profile_reference_count': len(prepared), 'private_observations_exported': False,
                      'unknown_read_status_n': sum(row['read_status'] == 'unknown' for row in ranked),
                      'confidence_calibrated': False, 'quality_verified': False}}
