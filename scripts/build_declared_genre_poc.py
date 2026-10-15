"""Append purpose-admitted, explicitly provisional source genres to the local PoC."""
from pathlib import Path
from hashlib import sha256
import argparse
import json

from recommend_book.recommendation.personal_poc import load_pinned_json, world_candidates
from recommend_book.recommendation.personal_ranking import rank_personal_features
from recommend_book.recommendation.personal_site import render_personal_site

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3'
BASE_MANIFEST_SHA = '615d7e9864987d6d7e9d9f09c1ca3cb5c7ebc1e3b1c21d45f8bceb328d0f30b6'
BASE_DELIVERY_SHA = 'ae66574340aeba8056793f30b12b8055ca5410bc8f895dbfc0d796be665c5f0a'
PROFILE_SHA = '298d2d6982773d3f9d96afe74427bf46a08cb1fb134c5516cce753ee69066e1d'


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def build(output, supplements):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Fresh output directory required')
    base = BASE / 'database/phase3-personal-poc-with-expansion-v1'
    manifest = load_pinned_json(base / 'manifest.json', BASE_MANIFEST_SHA)
    if manifest['output_sha256']['delivery.json'] != BASE_DELIVERY_SHA:
        raise ValueError('Pinned original catalogue differs')
    world = load_pinned_json(base / 'delivery.json', BASE_DELIVERY_SHA)
    names_path = ROOT / 'config/recommendation-label-display-ja.json'
    names = json.loads(names_path.read_bytes())['labels']
    candidates = world_candidates(world, names)
    if len(candidates) != 1807 or sum(bool(r['features']) for r in candidates) != 820:
        raise ValueError('Pinned baseline count differs')
    seen = {r['work_id'] for r in candidates}
    sources, cohort = {}, {}
    for directory, expected in supplements:
        directory = Path(directory).resolve(strict=True)
        sm = load_pinned_json(directory / 'manifest.json', expected)
        if (sm['version'] != 'purpose-admitted-source-declared-genre-supplement-v1' or
            sm['main_database_accessed'] or sm['llm_generated'] or sm['strict72_quality_verified']):
            raise ValueError('Unexpected supplement purpose/type')
        for name, checksum in sm['output_sha256'].items():
            if digest(directory / name) != checksum:
                raise ValueError('Admitted source supplement differs')
        packets = [json.loads(line) for line in (directory / 'feature-packets.jsonl').read_bytes().splitlines() if line]
        if len(packets) != sm['packet_n'] or sm['receipt_n'] != 3 * len(packets):
            raise ValueError('Complete source-purpose coverage differs')
        for packet in packets:
            work_id, qid, projection = packet['work_id'], packet['source_qid'], packet['projection']
            if work_id in seen or qid in cohort.values() or projection['metadata']['source_qid'] != qid:
                raise ValueError('Source work identity overlaps or differs')
            features = projection['provisional_recommendation_features']
            if (projection['llm_generated'] or projection['quality_verified'] or
                any(label not in names or not label.startswith('genre.') or value != 1.0 for label, value in features.items())):
                raise ValueError('Unsupported source declaration projection')
            seen.add(work_id)
            cohort[work_id] = qid
            title = projection['metadata']['title']
            aliases = list(dict.fromkeys(t['value'] for t in projection['metadata']['titles'] if t['value'] != title))
            candidates.append({'work_id': work_id, 'title': title, 'authors': [],
                'display_title_aliases': aliases, 'read_status': 'unknown',
                'external_ids': [{'source': 'wikidata', 'value': qid, 'scope': 'work'}],
                'features': features, 'feature_provenance': projection['feature_provenance']})
        sources[str(directory / 'manifest.json')] = expected
    profile_path = BASE / 'evaluation/phase3-personal-profile-v1/runtime-v2/private-profile.json'
    profile = load_pinned_json(profile_path, PROFILE_SHA)
    delivery = rank_personal_features(candidates, profile['private_rows'], limit=len(candidates))
    for row in delivery['ranking']:
        if row['work_id'] in cohort:
            row.update(classification_basis='source_declared_genre', source_qid=cohort[row['work_id']])
    delivery['audit'].update(catalogue_candidate_n=len(candidates),
        catalogue_feature_bearing_n=sum(bool(r['features']) for r in candidates),
        prior_llm_feature_bearing_n=820, source_declared_candidate_n=len(cohort),
        source_declared_feature_bearing_n=sum(bool(r['features']) for r in candidates if r['work_id'] in cohort),
        source_declared_genres_are_not_llm_outputs=True, strict72_quality_verified=False,
        raw_confidence_used_as_weight=False, new_model_calls=0, private_profile_connection_n=2,
        original_comparison_reused_without_retuning=True, main_database_updated=False)
    sources.update({str(base / 'manifest.json'): BASE_MANIFEST_SHA, str(base / 'delivery.json'): BASE_DELIVERY_SHA,
        str(profile_path): PROFILE_SHA, str(Path(__file__)): digest(__file__), str(names_path): digest(names_path),
        str(ROOT / 'src/recommend_book/recommendation/personal_site.py'): digest(ROOT / 'src/recommend_book/recommendation/personal_site.py'),
        str(ROOT / 'src/recommend_book/recommendation/personal_ranking.py'): digest(ROOT / 'src/recommend_book/recommendation/personal_ranking.py')})
    # One boundary check per small source artifact; no main DB or native replay.
    if any(digest(path) != expected for path, expected in sources.items()):
        raise ValueError('Pinned source changed while composing the display')
    output.mkdir(parents=True)
    (output / 'delivery.json').write_text(json.dumps(delivery, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'index.html').write_text(render_personal_site(delivery, names), encoding='utf-8')
    report = {'version': 'personal-poc-with-declared-genres-v1', 'audit': delivery['audit'],
        'source_sha256': sources, 'output_sha256': {name: digest(output / name) for name in ['delivery.json', 'index.html']},
        'gui_verified': False, 'private_observations_exported': False, 'source_scope_independently_expert_verified': False,
        'limitations': ['Source declaration projections are provisional and separate from LLM classifications.',
            'New author names were not purpose-admitted and are not displayed; explicit P50 IDs remain in the supplement.',
            'Read-state unknown remains unknown; preference accuracy and satisfaction remain unverified.']}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return delivery['audit']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--supplement', nargs=2, action='append', required=True, metavar=('DIRECTORY', 'MANIFEST_SHA256'))
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.supplement)))
