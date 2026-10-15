"""Local integration of previously verified immutable snapshots, without reclassification.

This adapter consumes specifically pinned prior admissions. It grants no new
source permission and does not accept a replacement self-asserted manifest.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from recommend_book.usage_guard import model_hash
from .next_evaluation import load_owned_ratings
from .personal_evaluation import evaluate_personal_features, personal_evaluation_spec
from .personal_ranking import rank_personal_features

PROFILE_PINS = {
    'public-rows.json': '678f693a9f2bb5ba8b0cf6d71a5c56f9370be8a620ac85d9ac00122b702d0766',
    'purpose-contract.json': '37d221cb5c7d0d34bd26e495eef9c2d55bd7382cd64971452a7456208849f308',
    'private-profile.json': '298d2d6982773d3f9d96afe74427bf46a08cb1fb134c5516cce753ee69066e1d',
    'profile-summary.json': 'c91a104f2b02e2f254654354bce0f0469eb86426ee422f133235efc0f360ab34',
    'purpose-receipts.json': '95a2fb410b469d516312f293b2bbfe030435eed4506ec3356e74620bc63f9165',
}
WORLD_MANIFEST_SHA = '35f8254691958a5517871a68aa2c99ab28aefff2911901a6d68a910768f684f7'
WORLD_ROWS_SHA = '7ddbb48d5dda3a35ea4b511606267e2dc68145b263c60d5002dea35e9219a839'
PURPOSE = 'private_local_serialization_content_profile'


def load_pinned_json(path, expected_sha256):
    raw = Path(path).read_bytes()
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Snapshot hash differs: ' + Path(path).name)
    return json.loads(raw)


def world_candidates(snapshot, label_names):
    """Project only already-admitted public fields; binary features, no old score."""
    rows, seen = [], set()
    for old in snapshot['ranking']:
        identity = old['work_id']
        if identity in seen:
            raise ValueError('Duplicate candidate identity')
        seen.add(identity)
        labels = old['content_labels']
        if len(set(labels)) != len(labels) or any(label not in label_names or label.startswith('style.') for label in labels):
            raise ValueError('Unsupported candidate label')
        rows.append({key: deepcopy(old[key]) for key in
                     ('work_id', 'title', 'authors', 'display_title_aliases', 'read_status')} |
                    {'features': {label: 1.0 for label in labels}, 'feature_provenance': {}})
    return rows


def join_owned_profile(owned_rows, profile_rows):
    """Keep original evaluation IDs/titles; attach features only by verified ncode."""
    rows = deepcopy(owned_rows)
    owners = {row['work_id']: row for row in rows}
    used = set()
    for profile in profile_rows:
        identifiers = [item['value'] for item in profile['external_ids'] if item['source'] == 'narou']
        if len(identifiers) != 1:
            raise ValueError('Exactly one verified owner identity required')
        identity = 'owned:' + identifiers[0].lower()
        if identity not in owners or identity in used:
            raise ValueError('Missing or repeated private identity')
        used.add(identity)
        row = owners[identity]
        if row['rating'] != profile['rating']:
            raise ValueError('Original assessment differs')
        for field in ('features', 'feature_provenance', 'author_ids', 'series_ids', 'external_ids'):
            row[field] = deepcopy(profile[field])
    return rows


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def build_personal_poc(root, output):
    """Reproduce this exact local PoC in a fresh directory using sealed inputs."""
    from .personal_site import render_personal_site

    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Fresh output directory required')
    base = root / 'data/autonomous-next-v3'
    profile_dir = base / 'evaluation/phase3-personal-profile-v1/runtime-v2'
    snapshots = {name: load_pinned_json(profile_dir / name, digest) for name, digest in PROFILE_PINS.items()}
    if json.loads((profile_dir / 'manifest-corrected.json').read_bytes()) != PROFILE_PINS:
        raise ValueError('Corrected profile output roster differs')
    contract, private = snapshots['purpose-contract.json'], snapshots['private-profile.json']
    if (contract['purpose'] != PURPOSE or private['purpose'] != snapshots['purpose-receipts.json']
            or private['purpose']['contract_sha256'] != PROFILE_PINS['purpose-contract.json']
            or private['summary'] != snapshots['profile-summary.json']
            or model_hash(snapshots['public-rows.json']) != contract['public_rows_sha256']):
        raise ValueError('Verified profile snapshot lineage differs')
    source_pins = {str(profile_dir / name): digest for name, digest in PROFILE_PINS.items()}
    for descriptor in contract['artifacts'].values():
        path = Path(descriptor['path'])
        if sha256(path.read_bytes()).hexdigest() != descriptor['sha256']:
            raise ValueError('Profile source/recipe differs: ' + path.name)
        source_pins[str(path)] = descriptor['sha256']

    world_dir = base / 'database/final-delivery-view-v2'
    world_manifest = load_pinned_json(world_dir / 'manifest.json', WORLD_MANIFEST_SHA)
    if (world_manifest['version'] != 'admitted-title-alias-view-v1'
            or world_manifest['output_sha256'][r'site\classified-reference\delivery.json'] != WORLD_ROWS_SHA):
        raise ValueError('Admitted public catalogue differs')
    world = load_pinned_json(world_dir / 'site/classified-reference/delivery.json', WORLD_ROWS_SHA)
    label_path = root / 'config/recommendation-label-display-ja.json'
    labels = load_pinned_json(label_path, world_manifest['source_sha256'][str(label_path)])['labels']
    candidates = world_candidates(world, labels)
    if len(candidates) != 1807 or sum(bool(row['features']) for row in candidates) != 778:
        raise ValueError('Frozen catalogue count differs')
    source_pins.update({str(world_dir / 'manifest.json'): WORLD_MANIFEST_SHA,
                        str(world_dir / 'site/classified-reference/delivery.json'): WORLD_ROWS_SHA,
                        str(label_path): world_manifest['source_sha256'][str(label_path)]})

    prereg_path = base / 'evaluation/phase3-personal-comparison-v1/preregistration.json'
    prereg = json.loads(prereg_path.read_bytes())
    if prereg['spec'] != personal_evaluation_spec():
        raise ValueError('Preregistered comparison specification differs')
    for relative, expected in prereg['source_recipes'].items():
        if sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('Preregistered implementation differs')
        source_pins[str(root / relative)] = expected
    source_pins[str(prereg_path)] = sha256(prereg_path.read_bytes()).hexdigest()
    owned = load_owned_ratings(contract['artifacts']['owned_database']['path'])
    if owned['audit']['source_database_sha256'] != prereg['owner_source']['sha256']:
        raise ValueError('Original assessments differ from preregistration')
    assessments = join_owned_profile(owned['rows'], private['private_rows'])
    history = [{'external_ids': [{'source': 'narou', 'value': row['work_id'].removeprefix('owned:')}]}
               for row in owned['rows']]
    delivery = rank_personal_features(candidates, private['private_rows'], limit=len(candidates), read_history=history)
    delivery['audit'].update(profile_connection_n=len(private['private_rows']), owned_assessment_n=len(assessments),
                             candidate_features='binary admitted-label presence; raw confidence unused',
                             profile_features='provisional source-declared work genre; strict72 quality unverified',
                             source_admission='immutable previously admitted public snapshot and new private purpose snapshot',
                             catalogue_feature_bearing_n=778, new_model_calls=0, database_reimports=0)
    comparison = evaluate_personal_features(assessments, spec=prereg['spec'])
    report = {'version': 'personal-poc-integration-v1', 'ranking': delivery['audit'],
              'comparison': comparison, 'private_profile_reused': True, 'new_owner_work': 0,
              'semantic_quality_verified': False, 'gui_verified': False,
              'novel_scope_public_pilot_rows_added': 0,
              'limitations': ['Only two historical assessments have provisional work genre features.',
                              'Unmapped read history prevents a guarantee that recommendations are unread.',
                              'No strict72 classification or personal satisfaction quality approval.',
                              'Public catalogue fields reuse historical purpose-admitted snapshots; no new source admission.']}
    # Persist only aggregate comparison and allowlisted public candidate fields.
    output.mkdir(parents=True)
    _write(output / 'delivery.json', delivery)
    _write(output / 'comparison.json', comparison)
    _write(output / 'report.json', report)
    (output / 'index.html').write_text(render_personal_site(delivery, labels), encoding='utf-8')
    _write(output / 'manifest.json', {'version': 'personal-poc-sealed-inputs-v1', 'source_sha256': source_pins,
           'output_sha256': {name: sha256((output / name).read_bytes()).hexdigest()
                             for name in ('delivery.json', 'comparison.json', 'report.json', 'index.html')},
           'adapter_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
           'renderer_sha256': sha256((root / 'src/recommend_book/recommendation/personal_site.py').read_bytes()).hexdigest(),
           'new_model_calls': 0, 'private_rows_exported': False, 'self_hash_included': False})
    return report
