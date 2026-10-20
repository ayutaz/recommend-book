"""Read-only saved-rating audit against newly acquired catalogue metadata.

No history is sent to a model or network service. Reports contain aggregates;
private link records contain opaque owner digests and public catalogue IDs only.
The audit never changes canonical identities, existing ratings or read status.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data' / 'autonomous-next-v3'
DEFAULT_HISTORY = BASE / 'database/rating-world-binding-v1/owned-rating-origin.jsonl'
DEFAULT_PROFILE = BASE / 'evaluation/phase3-personal-profile-v1/runtime-v2/private-profile.json'
_VOLUME = re.compile(r'(?:第\s*[0-9一二三四五六七八九十百]+\s*[巻話部章]|\b(?:vol(?:ume)?\.?|book|part|episode)\s*\d+|\bvolume\b)', re.I)


def exact_text(value):
    """No subtitle removal, fuzzy matching, transliteration or volume collapsing."""
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _owner_key(row):
    code = str(row.get('ncode') or '').lower()
    if not re.fullmatch(r'n[0-9]{4}[a-z]{1,2}', code):
        raise ValueError('Saved history row has no exact serialization ID')
    return code


def _profile_codes(rows):
    result = set()
    for row in rows:
        if not row.get('features'):
            continue
        for item in row.get('external_ids', []):
            if item.get('source') == 'narou' and re.fullmatch(r'n[0-9]{4}[a-z]{1,2}', str(item.get('value', '')).lower()):
                result.add(str(item['value']).lower())
    return result


def _exact_work_ids(row):
    return {item['value'] for item in row.get('provider_work_ids', [])
            if item.get('source') == 'wikidata' and item.get('scope') == 'work'
            and re.fullmatch(r'Q[1-9][0-9]*', str(item.get('value', '')))}


def audit_history(history, catalogue_rows, existing_profile, *, detail_resolver=None):
    """Audit exact same-work joins; source serialization relations cannot admit books.

    Optional owner author names/provider IDs must come from saved structured
    evidence with explicit identity scope. The ordinary saved roster has neither;
    its exact title hits are candidate-only and remain ambiguous.
    """
    by_qid = {row['qid']: row for row in catalogue_rows}
    by_title = defaultdict(set)
    for qid, row in by_qid.items():
        for name in [row.get('title'), *row.get('aliases', [])]:
            if exact_text(name):
                by_title[exact_text(name)].add(qid)
    existing = _profile_codes(existing_profile)
    links, read_plan, genre_ratings = [], [], defaultdict(dict)
    counts = Counter()
    seen = set()
    matched_works = set()
    for owner in history:
        code = _owner_key(owner)
        if code in seen:
            raise ValueError('Saved owner roster contains duplicate serialization IDs')
        seen.add(code)
        candidate_qids = set(by_title.get(exact_text(owner.get('title')), set()))
        explicit_ids = _exact_work_ids(owner)
        candidate_qids.update(explicit_ids & by_qid.keys())
        eligible_scope = owner.get('record_kind') == 'work' and owner.get('identity_scope_verified') is True
        strong, basis = set(), None
        if eligible_scope and len(explicit_ids) <= 1:
            strong = explicit_ids & by_qid.keys()
            basis = 'exact_provider_work_id' if strong else None
            if not strong and owner.get('author_identity_verified') is True:
                authors = {exact_text(v) for v in owner.get('author_names', []) if exact_text(v)}
                if authors and not _VOLUME.search(exact_text(owner.get('title'))):
                    strong = {qid for qid in candidate_qids
                              if authors <= {exact_text(a.get('name')) for a in by_qid[qid].get('authors', [])
                                            if a.get('name')}
                              and not _VOLUME.search(exact_text(by_qid[qid].get('title')))}
                    basis = 'exact_title_and_verified_author' if strong else None
        if len(strong) == 1:
            qid = next(iter(strong))
            # Only candidates that still reproduce current raw proof may link.
            target = detail_resolver(qid) if detail_resolver else by_qid[qid]
            if target is None:
                strong = set()
            elif target.get('record_kind', 'work') != 'work':
                strong = set()
        qid = next(iter(strong)) if len(strong) == 1 else None
        existing_content = code in existing
        if existing_content:
            kind = 'existing_content'
        elif qid:
            kind = 'strong_metadata'
        elif candidate_qids:
            kind = 'ambiguous'
        else:
            kind = 'unmatched'
        counts[kind] += 1
        link = {'history_digest': _digest(code), 'kind': kind,
                'catalogue_qid': qid, 'candidate_count': len(candidate_qids),
                'scope': 'same_work' if qid else 'unresolved',
                'basis': basis if qid else None, 'existing_content_connection': existing_content,
                'content_transfer_adopted': False}
        links.append(link)
        if qid:
            matched_works.add(qid)
            rating = owner.get('my_rating', owner.get('rating'))
            if isinstance(rating, (int, float)) and not isinstance(rating, bool) and 1 <= rating <= 5:
                for genre in by_qid[qid].get('genres', []):
                    genre_ratings[genre['qid']][qid] = (rating - 3) / 4
            if owner.get('read_status') == 'read':
                read_plan.append({'qid': qid, 'before': by_qid[qid].get('read_status', 'unknown'),
                                  'after': 'read', 'basis': basis})
    preferences = [{'genre_qid': qid, 'support': len(ratings),
                    'mean_centered_rating': round(sum(ratings.values()) / len(ratings), 6),
                    'basis': 'source_declared_metadata', 'calibrated': False}
                   for qid, ratings in sorted(genre_ratings.items()) if len(ratings) >= 3]
    report = {'version': 'scale-history-link-audit-v1', 'history_rows': len(history),
              'catalogue_works': len(by_qid), 'existing_content_history_links': counts['existing_content'],
              'strong_metadata_history_links': counts['strong_metadata'],
              'ambiguous_history_rows': counts['ambiguous'], 'unmatched_history_rows': counts['unmatched'],
              'matched_catalogue_works': len(matched_works), 'scale_content_history_links': 0,
              'coarse_genre_preference_features': len(preferences), 'read_state_proposals': len(read_plan),
              'read_state_changes': 0, 'canonical_merges': 0, 'network_calls': 0, 'model_calls': 0,
              'original_history_changed': False, 'individual_history_exported': False,
              'content_connection_scope': 'Existing saved content connections remain separate from new catalogue metadata matches.',
              'matching_policy': 'Exact work-scoped provider ID or exact title/alias plus verified author; serialization, edition, series and volume scope never inferred.'}
    return {'report': report, 'private_links': links, 'genre_preferences': preferences,
            'read_status_plan': read_plan}


def load_history(path):
    raw = Path(path).read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.strip()]
    for row in rows:
        # Saved NCODE ratings apply to their serial work, never the related book series.
        row.setdefault('record_kind', 'serialization')
    return rows, hashlib.sha256(raw).hexdigest()


def load_catalogue(database):
    from recommend_book.scale_catalogue_v1 import ScaleCatalogue
    catalogue = ScaleCatalogue(database)
    # Conditions are revalidated by the public backend before reading the small projection.
    stats = catalogue.catalogue_stats()
    with sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        rows = [json.loads(value) for (value,) in connection.execute(
            'SELECT row_json FROM works WHERE display_allowed=1 ORDER BY qid')]
    if len(rows) != stats['searchable_works']:
        raise ValueError('Catalogue changed during metadata projection read')
    return rows, catalogue


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--history', type=Path, default=DEFAULT_HISTORY)
    parser.add_argument('--existing-profile', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--expected-history-count', type=int, default=335)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    data_root = (ROOT / 'data').resolve()
    if not output.is_relative_to(data_root):
        parser.error('Private audit output must stay under the ignored data directory')
    if output.exists() and any(output.iterdir()):
        parser.error('Use a new empty output directory to preserve the previous audit')
    history, history_sha = load_history(args.history)
    if len(history) != args.expected_history_count:
        parser.error('Saved history roster count differs from the expected count')
    profile_raw = args.existing_profile.read_bytes()
    profile = json.loads(profile_raw)['private_rows']
    rows, catalogue = load_catalogue(args.database)
    result = audit_history(history, rows, profile, detail_resolver=catalogue.work_detail)
    if hashlib.sha256(args.history.read_bytes()).hexdigest() != history_sha:
        raise ValueError('Original history changed during the audit')
    if args.existing_profile.read_bytes() != profile_raw:
        raise ValueError('Existing profile changed during the audit')
    output.mkdir(parents=True, exist_ok=True)
    for key, filename in [('report', 'report.json'), ('genre_preferences', 'genre-preferences.json'),
                           ('read_status_plan', 'read-status-plan.json')]:
        (output / filename).write_text(json.dumps(result[key], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'private-links.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n'
                                                    for row in result['private_links']), encoding='utf-8')
    print(json.dumps(result['report'], ensure_ascii=False))


if __name__ == '__main__':
    main()
