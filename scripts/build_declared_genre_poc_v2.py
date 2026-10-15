"""Compose admitted source supplements without changing the frozen prior view."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import argparse
import json
import re

from recommend_book.recommendation.personal_poc import load_pinned_json, world_candidates
from recommend_book.recommendation.personal_ranking import rank_personal_features
from recommend_book.recommendation.personal_site import render_personal_site

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3'
BASE_MANIFEST_SHA = 'c17db791ec4da8d307fa6f9d36c75229005be3800817b94577e27049abd51bba'
BASE_DELIVERY_SHA = '29b4f4888db09ef4469a7976c773138365fff151095616b63a15f68e9c8c8df7'
PROFILE_SHA = '298d2d6982773d3f9d96afe74427bf46a08cb1fb134c5516cce753ee69066e1d'


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def apply_packets(candidates, source_cohort, packets, label_names, *, existing_featureless=False):
    """Apply one already admitted supplement; never override existing features.

    Existing-row additions require an explicit caller choice as well as the
    pinned supplement's current-work identity proof. Source admission belongs to
    the producer, not this projection. The source caller must reject any failure
    and discard this in-memory transaction before publishing its output.
    """
    index = {r['work_id']: r for r in candidates}
    seen = set()
    for packet in packets:
        identity, qid = packet['work_id'], packet['source_qid']
        projection = packet['projection']
        features = projection['provisional_recommendation_features']
        if (not isinstance(qid, str) or not re.fullmatch(r'Q[1-9][0-9]*', qid)
            or identity in seen or identity in source_cohort or qid in source_cohort.values()
            or projection['metadata']['source_qid'] != qid
            or projection['llm_generated'] is not False or projection['quality_verified'] is not False
            or not isinstance(features, dict) or not features
            or any(label not in label_names or not label.startswith('genre.')
                   or isinstance(value, bool) or value != 1.0 for label, value in features.items())):
            raise ValueError('Unsupported, repeated or mismatched source projection')
        if identity in index:
            if not existing_featureless or index[identity]['features']:
                raise ValueError('Existing source features cannot be overwritten')
            row = index[identity]
            row['features'] = deepcopy(features)
            row['feature_provenance'] = deepcopy(projection['feature_provenance'])
        else:
            if existing_featureless:
                raise ValueError('Expected a current featureless work')
            title = projection['metadata']['title']
            row = {'work_id': identity, 'title': title, 'authors': [],
                'display_title_aliases': list(dict.fromkeys(t['value'] for t in projection['metadata']['titles']
                                                           if t['value'] != title)),
                'read_status': 'unknown', 'external_ids': [{'source': 'wikidata', 'value': qid, 'scope': 'work'}],
                'features': deepcopy(features), 'feature_provenance': deepcopy(projection['feature_provenance'])}
            candidates.append(row)
            index[identity] = row
        seen.add(identity)
        source_cohort[identity] = qid
    return seen


def build(output, supplements):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Fresh output directory required')
    base = BASE / 'database/phase3-personal-poc-with-declared-genres-v1'
    manifest = load_pinned_json(base / 'manifest.json', BASE_MANIFEST_SHA)
    if manifest['output_sha256']['delivery.json'] != BASE_DELIVERY_SHA:
        raise ValueError('Prior catalogue pin differs')
    prior = load_pinned_json(base / 'delivery.json', BASE_DELIVERY_SHA)
    names_path = ROOT / 'config/recommendation-label-display-ja.json'
    names = json.loads(names_path.read_bytes())['labels']
    candidates = world_candidates(prior, names)
    if len(candidates) != 1961 or sum(bool(r['features']) for r in candidates) != 974:
        raise ValueError('Prior catalogue counts differ')
    cohort = {r['work_id']: r['source_qid'] for r in prior['ranking']
              if r.get('classification_basis') == 'source_declared_genre'}
    if len(cohort) != 154:
        raise ValueError('Prior source-declared cohort differs')
    pins = {str(base / 'manifest.json'): BASE_MANIFEST_SHA, str(base / 'delivery.json'): BASE_DELIVERY_SHA}
    changed = set()
    for directory, expected, existing in supplements:
        directory = Path(directory).resolve(strict=True)
        supplement = load_pinned_json(directory / 'manifest.json', expected)
        expected_version = ('purpose-admitted-current-featureless-source-declared-genre-supplement-v1'
                            if existing else 'purpose-admitted-source-declared-genre-supplement-v1')
        if (supplement['version'] != expected_version
            or supplement['main_database_accessed'] or supplement['llm_generated']
            or supplement['strict72_quality_verified']):
            raise ValueError('Unrecognized supplement purpose/type')
        for name, checksum in supplement['output_sha256'].items():
            path = (directory / name).resolve()
            if not path.is_relative_to(directory) or digest(path) != checksum:
                raise ValueError('Supplement path or SHA differs')
            pins[str(path)] = checksum
        packets = [json.loads(x) for x in (directory / 'feature-packets.jsonl').read_bytes().splitlines() if x.strip()]
        if (len(packets) != supplement['packet_n'] or supplement['receipt_n'] != 3 * len(packets)
            or supplement['feature_bearing_n'] != len(packets)):
            raise ValueError('Incomplete purpose or feature coverage')
        applied = apply_packets(candidates, cohort, packets, names, existing_featureless=existing)
        if existing:
            changed.update(applied)
        pins[str(directory / 'manifest.json')] = expected
    profile_path = BASE / 'evaluation/phase3-personal-profile-v1/runtime-v2/private-profile.json'
    profile = load_pinned_json(profile_path, PROFILE_SHA)
    delivery = rank_personal_features(candidates, profile['private_rows'], limit=len(candidates))
    for row in delivery['ranking']:
        if row['work_id'] in cohort:
            row.update(classification_basis='source_declared_genre', source_qid=cohort[row['work_id']])
    old_index = {r['work_id']: r for r in prior['ranking']}
    new_index = {r['work_id']: r for r in delivery['ranking']}
    if any(new_index[identity] != row for identity, row in old_index.items() if identity not in changed):
        raise ValueError('Unrelated prior public row changed')
    audit = delivery['audit']
    audit.update(catalogue_candidate_n=len(candidates), catalogue_feature_bearing_n=sum(bool(r['features']) for r in candidates),
        prior_llm_feature_bearing_n=820, source_declared_candidate_n=len(cohort),
        source_declared_feature_bearing_n=len(cohort), current_featureless_rows_updated_n=len(changed),
        unchanged_prior_public_rows_n=len(old_index) - len(changed), new_model_calls=0,
        raw_confidence_used_as_weight=False, strict72_quality_verified=False,
        source_declared_genres_are_not_llm_outputs=True, private_profile_connection_n=2,
        original_comparison_reused_without_retuning=True, main_database_updated=False)
    pins.update({str(profile_path): PROFILE_SHA, str(Path(__file__)): digest(__file__), str(names_path): digest(names_path)})
    for module in ['personal_site.py', 'personal_ranking.py', 'personal_poc.py']:
        path = ROOT / 'src/recommend_book/recommendation' / module
        pins[str(path)] = digest(path)
    if any(digest(path) != expected for path, expected in pins.items()):
        raise ValueError('Source changed during composition')
    output.mkdir(parents=True)
    (output / 'delivery.json').write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    (output / 'index.html').write_text(render_personal_site(delivery, names), encoding='utf8')
    report = {'version': 'personal-poc-with-declared-genres-v2', 'audit': audit,
        'source_sha256': pins, 'output_sha256': {name: digest(output / name) for name in ['delivery.json', 'index.html']},
        'gui_verified': False, 'private_observations_exported': False, 'strict72_quality_verified': False}
    (output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--supplement', nargs=2, action='append', default=[], metavar=('DIRECTORY', 'MANIFEST_SHA256'))
    parser.add_argument('--existing-featureless-supplement', nargs=2, action='append', default=[], metavar=('DIRECTORY', 'MANIFEST_SHA256'))
    args = parser.parse_args()
    supplements = [(p, h, False) for p, h in args.supplement] + [(p, h, True) for p, h in args.existing_featureless_supplement]
    if not supplements:
        parser.error('At least one purpose-admitted supplement is required')
    print(json.dumps(build(args.output, supplements)))
