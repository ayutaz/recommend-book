"""Deterministic preference ranking for the scale catalogue.

This module deliberately keeps request time free of model calls.  Native
features are only used after the catalogue's authenticated export binding has
been checked.  Raw labels may be used as an inexpensive upper bound while
choosing a small verification window; they are never emitted as reasons or
used in the final score.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
import json
from pathlib import Path
import re

from .scale_catalogue_v1 import CLASSIFICATION_AXES, _display_native_labels, _qid, _refs

DEFAULT_AXIS_WEIGHTS = {
    'genre': 0.25,
    'theme': 0.20,
    'structure': 0.15,
    'protagonist': 0.15,
    'style': 0.10,
    'atmosphere': 0.15,
}
MAX_LIMIT = 100
_QID_RE = re.compile(r'^Q[1-9][0-9]*$')


def _taxonomy():
    path = Path(__file__).resolve().parents[2] / 'config' / 'taxonomy.json'
    payload = json.loads(path.read_bytes())
    labels = {}
    for item in payload.get('labels', []):
        label_id = item.get('label_id')
        axis = item.get('axis')
        if (isinstance(label_id, str) and isinstance(axis, str)
                and axis in CLASSIFICATION_AXES and label_id.startswith(axis + '.')
                and item.get('primary', True)):
            labels[label_id] = {'label_id': label_id, 'axis': axis,
                                'label': item.get('label') or label_id}
    display_path = Path(__file__).resolve().parents[2] / 'config' / 'recommendation-label-display-ja.json'
    if display_path.is_file():
        display = json.loads(display_path.read_bytes()).get('labels', {})
        for label_id, item in labels.items():
            item['label'] = display.get(label_id, item['label'])
    return labels


def _normalise_list(values, name):
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f'{name} must be a list')
    result = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{name} contains an invalid value')
        result.append(value.strip())
    return list(dict.fromkeys(result))


def _feature_ids(values, name, taxonomy):
    values = _normalise_list(values, name)
    unknown = [value for value in values if value not in taxonomy]
    if unknown:
        raise ValueError(f'{name} contains an unknown feature')
    return set(values)


def _weights(value):
    if value is None:
        return dict(DEFAULT_AXIS_WEIGHTS)
    if not isinstance(value, dict):
        raise ValueError('axis_weights must be an object')
    result = dict(DEFAULT_AXIS_WEIGHTS)
    for axis, weight in value.items():
        if axis not in CLASSIFICATION_AXES:
            raise ValueError('axis_weights contains an unknown axis')
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not isfinite(weight) or weight < 0:
            raise ValueError('axis weight must be a finite non-negative number')
        result[axis] = float(weight)
    total = sum(result.values())
    if total <= 0:
        raise ValueError('axis weights must contain a positive value')
    return {axis: value / total for axis, value in result.items()}


def _year(value, name):
    if value in (None, ''):
        return None
    if isinstance(value, bool):
        raise ValueError(f'{name} must be an integer')
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name} must be an integer') from None
    if not -5000 <= value <= 3000:
        raise ValueError(f'{name} is outside the supported range')
    return value


def _native_shape(labels, taxonomy):
    """Return taxonomy-bound, present labels without trusting provenance."""
    if not isinstance(labels, list):
        return []
    labels = [label for label in labels if isinstance(label, dict)]
    accepted, _ = _display_native_labels(labels)
    result = []
    for label in accepted:
        label_id = label.get('label_id')
        if (label.get('decision') != 'present' or label.get('scope') != 'work'
                or label_id not in taxonomy or label.get('axis') != taxonomy[label_id]['axis']):
            continue
        confidence = label.get('raw_confidence')
        if confidence is not None and (isinstance(confidence, bool) or
                                       not isinstance(confidence, (int, float)) or
                                       not isfinite(confidence)):
            continue
        relevance = label.get('relevance')
        if relevance is not None and (isinstance(relevance, bool) or
                                      not isinstance(relevance, (int, float)) or
                                      not isfinite(relevance)):
            continue
        evidence = [item for item in label.get('evidence', [])
                    if isinstance(item, dict) and isinstance(item.get('locator'), str)]
        result.append({**label, 'label_id': label_id, 'axis': taxonomy[label_id]['axis'],
                       'label': taxonomy[label_id]['label'], 'evidence': evidence})
    return result


def _raw_labels(row, taxonomy):
    try:
        return _native_shape(json.loads(row['labels_json']), taxonomy)
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []


def _native_features(catalogue, qid, verified, taxonomy, cache, info_cache=None):
    """Authenticate one row lazily; invalid rows become source-only rows."""
    if qid in cache:
        return cache[qid]
    if info_cache is None:
        info_cache = {}
    try:
        with catalogue._connect() as db:
            native = db.execute('SELECT * FROM native_classifications WHERE qid=?', (qid,)).fetchone()
        if native is None:
            result = []
            info_cache[qid] = {'status': 'no_native_record', 'missing_axes': list(CLASSIFICATION_AXES),
                               'source_attributions': []}
        else:
            bound = catalogue._native_proofs(native, verified=verified)
            result = _native_shape(bound['admitted_labels'], taxonomy)
            sources = [item for item in bound.get('source_attributions', [])
                       if isinstance(item, dict) and item.get('display_allowed', True)]
            result = [{**label, '_source_attributions': sources} for label in result]
            missing = {item.get('axis') for item in bound.get('native_result', {}).get('missing_axes', [])
                       if isinstance(item, dict) and item.get('axis') in CLASSIFICATION_AXES}
            info_cache[qid] = {'status': 'verified', 'missing_axes': sorted(missing),
                               'source_attributions': sources}
    except (AttributeError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        # A revoked, changed, or otherwise unbound native row has no native
        # recommendation authority.  Source metadata can still be used.
        result = []
        info_cache[qid] = {'status': 'proof_unavailable', 'missing_axes': list(CLASSIFICATION_AXES),
                           'source_attributions': []}
    cache[qid] = result
    return result


def _read_native_rows(catalogue, qids):
    if not qids:
        return {}
    result = {}
    values = list(qids)
    with catalogue._connect() as db:
        for start in range(0, len(values), 500):
            params = values[start:start + 500]
            placeholders = ','.join('?' for _ in params)
            for row in db.execute(f'SELECT qid,labels_json FROM native_classifications WHERE qid IN ({placeholders})', params):
                result[row['qid']] = row
    return result


def _decode_candidate_rows(stored_rows):
    """Decode each candidate row once and retain its SQLite storage row."""
    return [(json.loads(stored['row_json']), stored) for stored in stored_rows]


def _merge_candidate_rows(*batches):
    """Merge candidate batches with native rows overriding fallback rows."""
    by_qid = {}
    for batch in batches:
        for row, stored in batch:
            by_qid[row['qid']] = (row, stored)
    return list(by_qid.values())


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _source_genres(row):
    return {item.get('qid') for item in row.get('genres', []) if item.get('qid')}


def _source_authors(row):
    return {item.get('qid') for item in row.get('authors', []) if item.get('qid')}


def _source_series(catalogue, row, cache):
    """Read only explicit P179 series authorities from a bound source raw."""
    qid = row.get('qid')
    if qid in cache:
        return cache[qid]
    series = set()
    for artifact in row.get('provenance', {}).get('artifacts', []):
        if not isinstance(artifact, dict):
            continue
        try:
            payload = catalogue._payload(artifact.get('raw_path') or artifact['path'],
                                        artifact.get('raw_sha256') or artifact['sha256'])
            entity = payload.get('entities', {}).get(qid)
            if isinstance(entity, dict):
                series.update(_refs(entity, 'P179'))
        except (AttributeError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            # A source row with no readable explicit series authority remains
            # unknown.  Titles and aliases are never used to infer one.
            continue
    cache[qid] = series
    return series


def _score(row, *, native, liked_rows, liked_native, preferred, avoided, weights):
    by_axis = {axis: set() for axis in CLASSIFICATION_AXES}
    for label in native:
        by_axis[label['axis']].add(label['label_id'])
    reasons = []
    score = 0.0
    candidate_genres = _source_genres(row)
    liked_genres = [_source_genres(item) for item in liked_rows]
    liked_authors = [_source_authors(item) for item in liked_rows]
    for axis in CLASSIFICATION_AXES:
        targets = set(preferred.get(axis, set()))
        liked_sets = [set(item.get(axis, set())) for item in liked_native if item.get(axis)]
        explicit = by_axis[axis] & targets
        explicit_ratio = len(explicit) / len(targets) if targets else 0.0
        liked_ratio = max((_jaccard(by_axis[axis], target) for target in liked_sets), default=0.0)
        source_ratio = 0.0
        if axis == 'genre':
            source_ratio = max((_jaccard(candidate_genres, target) for target in liked_genres), default=0.0)
        match = max(explicit_ratio, liked_ratio, source_ratio)
        if match:
            score += weights[axis] * match
        matched = explicit | {label for target in liked_sets for label in by_axis[axis] & target}
        if matched:
            reasons.append({'kind': 'native_feature_match', 'axis': axis,
                            'shared': sorted(matched), 'weight': round(weights[axis] * max(explicit_ratio, liked_ratio), 6),
                            'relation': 'match', 'source_locator': sorted({ref.get('locator') for label in native
                                                                             if label['label_id'] in matched
                                                                             for ref in label.get('evidence', [])}) or None,
                            'model_confidence': max((label.get('raw_confidence') for label in native
                                                    if label['label_id'] in matched and isinstance(label.get('raw_confidence'), (int, float))), default=None),
                            'relevance': max((label.get('relevance') for label in native
                                              if label['label_id'] in matched and isinstance(label.get('relevance'), (int, float))), default=None),
                            'provenance': {'status': 'verified_native', 'source_attributions': [source
                                for label in native if label['label_id'] in matched
                                for source in label.get('_source_attributions', [])]}})
        if source_ratio and not matched:
            shared = sorted(candidate_genres & set().union(*liked_genres))
            reasons.append({'kind': 'source_genre', 'shared': shared,
                            'weight': round(weights[axis] * source_ratio, 6),
                            'relation': 'match', 'source_locator': 'claims.P136',
                            'model_confidence': None, 'relevance': None})
    avoided_matches = []
    for axis, labels in avoided.items():
        shared = by_axis[axis] & labels
        if shared:
            penalty = 0.75 * weights[axis] * len(shared) / len(labels)
            score -= penalty
            avoided_matches.append({'kind': 'avoided_feature', 'axis': axis, 'shared': sorted(shared),
                                    'weight': round(-penalty, 6), 'relation': 'penalty',
                                    'source_locator': sorted({ref.get('locator') for label in native
                                                               if label['label_id'] in shared for ref in label.get('evidence', [])}) or None,
                                    'model_confidence': max((label.get('raw_confidence') for label in native
                                                             if label['label_id'] in shared and isinstance(label.get('raw_confidence'), (int, float))), default=None),
                                    'relevance': max((label.get('relevance') for label in native
                                                      if label['label_id'] in shared and isinstance(label.get('relevance'), (int, float))), default=None)})
    reasons.extend(avoided_matches)
    if liked_rows:
        languages = {item.get('original_language') for item in liked_rows if item.get('original_language')}
        if row.get('original_language') in languages:
            score += 0.025
            reasons.append({'kind': 'same_language', 'shared': [row['original_language']], 'weight': 0.025,
                            'relation': 'context', 'source_locator': 'claims.P407', 'model_confidence': None, 'relevance': None})
        years = [item.get('year') for item in liked_rows if item.get('year') is not None]
        if row.get('year') is not None and years and min(abs(row['year'] - year) for year in years) <= 20:
            score += 0.025
            reasons.append({'kind': 'publication_period', 'shared': [row['year']], 'weight': 0.025,
                            'relation': 'context', 'source_locator': 'claims.P577', 'model_confidence': None, 'relevance': None})
    if not reasons:
        reasons.append({'kind': 'feature_unavailable', 'shared': [], 'weight': 0.0,
                        'relation': 'unknown', 'source_locator': None, 'model_confidence': None, 'relevance': None})
    elif not any(reason['kind'] == 'native_feature_match' for reason in reasons) and liked_rows:
        reasons.insert(0, {'kind': 'fallback_source', 'shared': sorted(candidate_genres), 'weight': 0.0,
                           'relation': 'source_only', 'source_locator': 'claims.P136',
                           'model_confidence': None, 'relevance': None})
    return score, reasons


def _diversify(ranked, limit, *, series_getter=None):
    selected = []
    deferred = []
    author_counts = defaultdict(int)
    series_counts = defaultdict(int)
    for item in ranked:
        row = item[0]
        authors = sorted(_source_authors(row))
        if series_getter is not None:
            series = sorted(str(value) for value in series_getter(row))
        else:
            series = [str(value) for value in row.get('series', [])] if isinstance(row.get('series'), list) else []
        # A single explicit series should contribute at most one item to a
        # short recommendation page.  The author cap remains two, while this
        # stricter series cap prevents adjacent volumes (possibly by different
        # authors) from crowding out distinct works.
        if any(author_counts[author] >= 2 for author in authors) or any(series_counts[value] >= 1 for value in series):
            deferred.append(item)
            continue
        selected.append(item)
        for author in authors:
            author_counts[author] += 1
        for value in series:
            series_counts[value] += 1
        if len(selected) == limit:
            return selected
    for item in deferred:
        if len(selected) == limit:
            break
        selected.append(item)
    return selected


def recommend_preferences(catalogue, *, liked_qids=None, preferred_features=None,
                          avoided_features=None, axis_weights=None, genre=None,
                          language=None, year_from=None, year_to=None,
                          exclude_read=True, limit=12):
    """Return deterministic, explainable preference candidates."""
    if isinstance(exclude_read, bool) is False:
        raise ValueError('exclude_read must be boolean')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError('limit must be 1..100')
    taxonomy = _taxonomy()
    liked_values = _normalise_list(liked_qids, 'liked_qids')
    for value in liked_values:
        if not _QID_RE.fullmatch(value.removeprefix('wd:')):
            raise ValueError('liked_qids contains an invalid QID')
    liked_values = list(dict.fromkeys(value.removeprefix('wd:') for value in liked_values))
    preferred_values = _feature_ids(preferred_features, 'preferred_features', taxonomy)
    avoided_values = _feature_ids(avoided_features, 'avoided_features', taxonomy)
    weights = _weights(axis_weights)
    year_from, year_to = _year(year_from, 'year_from'), _year(year_to, 'year_to')
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError('year_from must not exceed year_to')
    if genre:
        genre = _qid(genre)
    if language is not None and (not isinstance(language, str) or not language.strip()):
        raise ValueError('language must be a non-empty string')
    _, rights = catalogue._conditions()
    if rights['display'] != 'allowed':
        return {'mode': 'insufficient_evidence', 'personalized': bool(liked_values or preferred_values or avoided_values),
                'items': [], 'reasons': [], 'explanation': '表示利用条件が成立していないため候補を表示できません。'}
    clauses = ['w.display_allowed=1']
    params = []
    if genre:
        clauses.append('EXISTS(SELECT 1 FROM work_genres g WHERE g.qid=w.qid AND g.genre_qid=?)')
        params.append(genre)
    if language:
        clauses.append('w.language=?')
        params.append(language.strip())
    if year_from is not None:
        clauses.append('w.year>=?'); params.append(year_from)
    if year_to is not None:
        clauses.append('w.year<=?'); params.append(year_to)
    if liked_values:
        clauses.append('w.qid NOT IN (' + ','.join('?' for _ in liked_values) + ')')
        params.extend(liked_values)
    if exclude_read:
        clauses.append("coalesce(r.status,'unknown')!='read'")
    with catalogue._connect() as db:
        liked_stored = db.execute('SELECT w.row_json,r.status AS read_status FROM works w LEFT JOIN read_status r USING(qid) '
                                  'WHERE w.qid IN (' + ','.join('?' for _ in liked_values) + ') AND w.display_allowed=1', liked_values).fetchall() if liked_values else []
    liked_by_qid = {json.loads(row['row_json'])['qid']: json.loads(row['row_json']) for row in liked_stored}
    missing_likes = sorted(set(liked_values) - set(liked_by_qid))
    if missing_likes:
        raise ValueError('liked_qids contains a work unavailable for display')
    liked_rows = list(liked_by_qid.values())
    verified = catalogue._native_operation() if hasattr(catalogue, '_native_operation') else None
    if verified is None:
        from .scale_catalogue_v1 import _NativeOperation
        verified = _NativeOperation()
    native_cache = {}
    native_info_cache = {}
    liked_native = []
    for qid in liked_values:
        labels = _native_features(catalogue, qid, verified, taxonomy, native_cache, native_info_cache)
        by_axis = {axis: set() for axis in CLASSIFICATION_AXES}
        for label in labels:
            by_axis[label['axis']].add(label['label_id'])
        liked_native.append(by_axis)
    preferred = defaultdict(set)
    avoided = defaultdict(set)
    for label_id in preferred_values:
        preferred[taxonomy[label_id]['axis']].add(label_id)
    for label_id in avoided_values:
        avoided[taxonomy[label_id]['axis']].add(label_id)
    # The raw label shape is only an upper bound for the lazy verification
    # window. It is never passed to _score or returned to the caller.
    native_interest = bool(preferred_values or avoided_values or
                           any(any(values for values in item.values()) for item in liked_native))
    window = max(limit * 8, 48)

    def select_candidates(db, extra=(), extra_params=(), limit_rows=None):
        sql = ('SELECT w.row_json,r.status AS read_status FROM works w LEFT JOIN read_status r USING(qid) WHERE '
               + ' AND '.join([*clauses, *extra]))
        query_params = [*params, *extra_params]
        if limit_rows is not None:
            sql += ' ORDER BY w.recommendable DESC,w.title,w.qid LIMIT ?'
            query_params.append(limit_rows)
        return db.execute(sql, query_params).fetchall()

    with catalogue._connect() as db:
        if native_interest:
            # Native labels are the only source of feature matches. Read those
            # rows plus a small source fallback window; do not deserialize all
            # 32k works merely to discover that they have no native row.
            native_clause = 'EXISTS(SELECT 1 FROM native_classifications n WHERE n.qid=w.qid)'
            native_rows = select_candidates(db, extra=(native_clause,))
            fallback_rows = select_candidates(db, limit_rows=window)
            candidate_rows = _merge_candidate_rows(
                _decode_candidate_rows(fallback_rows),
                _decode_candidate_rows(native_rows),
            )
        elif liked_rows:
            liked_genres = sorted(set().union(*(_source_genres(item) for item in liked_rows)))
            if liked_genres:
                placeholders = ','.join('?' for _ in liked_genres)
                source_rows = select_candidates(
                    db,
                    extra=(f'EXISTS(SELECT 1 FROM work_genres sg WHERE sg.qid=w.qid AND sg.genre_qid IN ({placeholders}))',),
                    extra_params=liked_genres, limit_rows=window)
                fallback_rows = select_candidates(db, limit_rows=window)
                candidate_rows = _merge_candidate_rows(
                    _decode_candidate_rows(fallback_rows),
                    _decode_candidate_rows(source_rows),
                )
            else:
                candidate_rows = _decode_candidate_rows(select_candidates(db, limit_rows=window))
        else:
            candidate_rows = _decode_candidate_rows(select_candidates(db, limit_rows=window))
    qids = [row['qid'] for row, _ in candidate_rows]
    raw_rows = _read_native_rows(catalogue, qids) if native_interest else {}
    provisional = []
    for row, stored in candidate_rows:
        raw = _raw_labels(raw_rows.get(row['qid']), taxonomy) if row['qid'] in raw_rows else []
        raw_ids = {label['label_id'] for label in raw}
        relevant = bool(raw_ids & preferred_values or raw_ids & avoided_values or
                        any(raw_ids & set().union(*(item.get(axis, set()) for item in liked_native))
                            for axis in CLASSIFICATION_AXES))
        # A small source-only score is safe to calculate before native proof;
        # native labels affect the upper bound only when they are relevant.
        # Avoided native labels are omitted from this provisional score. If a
        # proof later fails, that negative contribution disappears and the
        # final score can increase; treating it as zero keeps this value a
        # genuine upper bound for the lazy verification stopping rule.
        score, reasons = _score(row, native=raw if relevant else [], liked_rows=liked_rows,
                                liked_native=liked_native, preferred=preferred, avoided={}, weights=weights)
        provisional.append((score, row['title'], row['qid'], row, stored, relevant))
    provisional.sort(key=lambda item: (-item[0], item[1], item[2]))
    scored = []
    for index, (upper, _, qid, row, stored, relevant) in enumerate(provisional):
        native = (_native_features(catalogue, qid, verified, taxonomy, native_cache, native_info_cache)
                  if relevant else [])
        score, reasons = _score(row, native=native, liked_rows=liked_rows, liked_native=liked_native,
                                preferred=preferred, avoided=avoided, weights=weights)
        scored.append((row, stored, score, reasons, bool(native), upper))
        # Keep authentication bounded while retaining a truthful upper-bound
        # rule. A later row can only score at most its provisional score, so it
        # cannot displace the current kth row once that upper bound is lower.
        # Equal scores are consumed too, because qid is the final tie-breaker.
        if len(scored) >= max(limit * 2, 8) and index + 1 < len(provisional):
            current = sorted(scored, key=lambda item: (-item[2], item[0]['title'], item[0]['qid']))
            boundary = current[limit - 1][2]
            next_upper = provisional[index + 1][0]
            exact_top = all(abs(item[2] - item[5]) <= 1e-12 for item in current[:limit])
            if next_upper < boundary or (next_upper == boundary and exact_top):
                break
    scored.sort(key=lambda item: (-item[2], item[0]['title'], item[0]['qid']))
    series_cache = {}
    selected = _diversify(scored, limit,
                          series_getter=lambda row: _source_series(catalogue, row, series_cache))
    native_verified = verified
    items = []
    for row, stored, score, reasons, has_native, _ in selected:
        public = catalogue._row(stored, native_verified=native_verified)
        info = native_info_cache.get(row['qid'], {'status': 'not_checked',
                                                   'missing_axes': list(CLASSIFICATION_AXES),
                                                   'source_attributions': []})
        native_labels = native_cache.get(row['qid'], [])
        known_axes = {label['axis'] for label in native_labels}
        insufficiency = [{'axis': axis, 'status': 'unknown',
                          'reason': 'axis_not_adopted_or_proof_unavailable'}
                         for axis in CLASSIFICATION_AXES
                         if axis not in known_axes or axis in info.get('missing_axes', [])]
        matched_avoided = sorted({label_id for reason in reasons if reason['kind'] == 'avoided_feature'
                                  for label_id in reason.get('shared', [])})
        unknown_avoided = sorted(avoided_values - set(matched_avoided))
        provenance = [{**source, 'status': info.get('status', 'unknown')}
                      for source in info.get('source_attributions', [])]
        if not provenance:
            provenance = [{'status': info.get('status', 'unknown')}]
        public.update({'score': round(score, 6), 'reasons': reasons,
                       'score_kind': 'uncalibrated_preference_similarity',
                       'evidence_status': 'verified_native' if has_native else ('source_metadata' if row.get('genres') else 'unknown'),
                       'feature_insufficiency': insufficiency,
                       'avoided_feature_status': {'matched': matched_avoided,
                                                 'unknown': unknown_avoided},
                       'recommendation_provenance': provenance})
        items.append(public)
    return {'mode': 'preference_similarity',
            'personalized': bool(liked_values or preferred_values or avoided_values),
            'items': items,
            'filters': {'liked_qids': liked_values, 'preferred_features': sorted(preferred_values),
                        'avoided_features': sorted(avoided_values), 'axis_weights': weights,
                        'genre': genre, 'language': language, 'year_from': year_from, 'year_to': year_to,
                        'exclude_read': exclude_read},
            'explanation': '指定した作品・特徴・書誌条件を再現可能な計算式で順位付けしています。スコアは未校正で、読書履歴から好みを推測していません。保留・証跡不備の詳細分類は根拠に使わず、必要に応じて資料記載のジャンルへフォールバックします。'}


def preference_facets(catalogue):
    """Return safe choices without making the initial page wait on 5k proofs.

    Taxonomy choices are intentionally marked as on-demand.  The database is
    not treated as an authenticated count source; a label is admitted for a
    recommendation only after its own manifest binding is checked.
    """
    _, rights = catalogue._conditions()
    if rights['display'] != 'allowed':
        return {'native_features': [], 'languages': [], 'genres': []}
    taxonomy = _taxonomy()
    features = [{'label_id': item['label_id'], 'label': item['label'], 'axis': item['axis'],
                 'adopted_count': None, 'availability': 'verified_on_demand',
                 'source': 'authenticated_native_export'}
                for item in sorted(taxonomy.values(), key=lambda value: (value['axis'], value['label_id']))]
    source = catalogue.catalogue_facets()
    return {'native_features': features, 'languages': source.get('languages', []), 'genres': source.get('genres', [])}
