"""Prepare a separate positive-only CoNSSA reference; no acquisition or LLM calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

SOURCE_COMMIT = '8fccf669ed1bdbad309ff190f5afb0d6066d70a3'
SOURCE_URL = f'https://github.com/cligs/conssa/blob/{SOURCE_COMMIT}/metadata/metadata.tsv'
INPUT_SHA256 = '4acc51caff87ee9f12815dda0644301b912742e2e5c67c960b7645744ba7c66a'
METADATA_SHA256 = '2c127dbb6f82b33548151e09a5a185a384c36981eb14e7fd1c7cfab54f035400'
MAPPING_VERSION = 'conssa-direct-positive-v1'

# Each rule retains the exact target definition reviewed for this mapping.
# Other native values do not establish the absence of these characteristics.
RULES = (
    ('protagonist.age', 'child', 'protagonist.child',
     'A central character is explicitly a child.',
     'The native protagonist age explicitly identifies a child.'),
    ('protagonist.age', 'adult', 'protagonist.adult',
     'A central character is explicitly an adult.',
     'The native protagonist age explicitly identifies an adult.'),
    ('protagonist.profession', 'student', 'protagonist.student',
     'A central character has an explicit student role.',
     'The native protagonist profession explicitly identifies a student role.'),
    ('protagonist.profession', 'artist', 'protagonist.artist',
     'A central character practices an artistic vocation.',
     'The native protagonist profession explicitly identifies an artistic vocation.'),
)


def checked_taxonomy(taxonomy):
    labels = {r['label_id']: r for r in taxonomy['labels']}
    for _, _, label_id, definition, _ in RULES:
        if label_id not in labels or labels[label_id].get('definition') != definition:
            raise ValueError(f'taxonomy definition drift for {label_id}; review the mapping')
    return labels


def convert_reference(native, taxonomy):
    """Return positive reference annotations plus an audit of unsupported fields."""
    checked_taxonomy(taxonomy)
    if native.get('source_sha') != SOURCE_COMMIT or native.get('source_url') != SOURCE_URL:
        raise ValueError('unreviewed source revision or source URL')
    if native.get('license') != 'CC-BY-4.0' or not native.get('attribution'):
        raise ValueError('source license and attribution are required')
    if native.get('provenance') != 'curated_reference' or native.get('reference_certainty') not in ('high', 'medium'):
        raise ValueError('expected high/medium curated reference annotation')
    reference_id = native['reference_id']
    if not isinstance(reference_id, str) or not reference_id.startswith('ne'):
        raise ValueError('invalid CoNSSA reference ID')
    native_labels = native['native_labels']
    mapped = []
    used_fields = set()
    for field, value, label_id, definition, reason in RULES:
        if native_labels.get(field) == value:
            mapped.append({
                'label_id': label_id, 'decision': 'present', 'relevance': None,
                'raw_confidence': None, 'confidence': None,
                'native_field': field, 'native_value': value,
                'mapping_rule': f'{MAPPING_VERSION}:{field}={value}',
                'mapping_reason': reason, 'target_definition': definition,
            })
            used_fields.add(field)
    unmapped = []
    for field, value in native_labels.items():
        if field in used_fields:
            continue
        status = 'unsupported'
        reason = 'No reviewed direct rule. Missing or other categories do not establish absence.'
        if field == 'narrator':
            reason = ('Participation in the story, grammatical person, and predominant narrative form '
                      'are different concepts. The saved native category alone does not establish '
                      'structure.first_person/third_person/epistolary as defined in this taxonomy.')
        elif field.startswith('subgenre.edit.') or field.startswith('subgenre.lithist.'):
            status = 'excluded_external_genre'
            reason = 'Externally sourced genre classifications are excluded from this direct annotation mapping.'
        elif field.startswith('subgenre') or field == 'end':
            reason = 'Genre terms or positive/negative ending do not establish this taxonomy genre/theme/atmosphere labels.'
        unmapped.append({'native_field':field, 'native_value':value,
                         'conversion_status':status, 'mapping_reason':reason})
    record = {
        'work_id': f'reference:conssa:{reference_id}', 'reference_id':reference_id,
        'provenance':'curated_reference', 'reference_scope':'positive_only',
        'identity_status':'unresolved', 'evaluation_ready':False,
        'input_status':'independent_input_not_constructed',
        'title':native.get('title'), 'authors':[native.get('author')],
        'source_publication_year':native.get('year'),
        'source_annotation_certainty':native['reference_certainty'],
        'source_commit':SOURCE_COMMIT, 'source_url':SOURCE_URL,
        'source_metadata_sha256':METADATA_SHA256,
        'license':'CC-BY-4.0', 'license_url':'https://creativecommons.org/licenses/by/4.0/',
        'attribution':native['attribution'],
        'changes':('Selected direct positive age/profession annotations and mapped four reviewed '
                   'target labels; omitted unsupported categories; assigned unresolved reference IDs.'),
        'source_text_availability':native.get('source_text_availability'),
        'full_text_rights_verified':False,
        'mapping_version':MAPPING_VERSION, 'taxonomy_version':taxonomy.get('version'),
        'labels':mapped,
    }
    return record, {'reference_id':reference_id, 'mapped_labels':[r['label_id'] for r in mapped],
                    'unmapped_fields':unmapped}


def prepare_reference(input_path, taxonomy_path, output_dir):
    input_path, taxonomy_path, output_dir = map(Path, (input_path, taxonomy_path, output_dir))
    body = input_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != INPUT_SHA256:
        raise ValueError('snapshot hash mismatch; use the reviewed fixed native fixture')
    original = input_path.parent/'conssa-metadata.tsv'
    if hashlib.sha256(original.read_bytes()).hexdigest() != METADATA_SHA256:
        raise ValueError('original source metadata snapshot hash mismatch')
    tax_body = taxonomy_path.read_bytes()
    taxonomy = json.loads(tax_body.decode('utf-8-sig'))
    checked_taxonomy(taxonomy)
    items = [json.loads(line) for line in body.decode('utf-8-sig').splitlines() if line.strip()]
    if len(items) != 269 or len({r['reference_id'] for r in items}) != 269:
        raise ValueError('expected 269 unique fixed CoNSSA source references')
    gold, audit = [], []
    for item in items:
        record, row_audit = convert_reference(item, taxonomy)
        audit.append(row_audit)
        if record['labels']:
            gold.append(record)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError('output directory must be new or empty; preserve prior snapshots')
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        'gold.jsonl': ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in gold).encode('utf-8'),
        'mapping-audit.jsonl': ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in audit).encode('utf-8'),
    }
    for name, data in outputs.items():
        (output_dir/name).write_bytes(data)
    summary = {
        'status':'independent_reference_prepared_not_evaluated',
        'mapping_version':MAPPING_VERSION, 'source_commit':SOURCE_COMMIT,
        'input_path':input_path.as_posix(), 'input_sha256':INPUT_SHA256,
        'source_metadata_sha256':METADATA_SHA256,
        'taxonomy_version':taxonomy.get('version'),
        'taxonomy_sha256':hashlib.sha256(tax_body).hexdigest(),
        'source_candidates':len(items), 'mapped_reference_works':len(gold),
        'unmapped_reference_works':len(items)-len(gold),
        'positive_support':dict(Counter(label['label_id'] for r in gold for label in r['labels'])),
        'human_gold_works_added':0, 'current_poc_identity_matches_confirmed':0,
        'current_poc_evaluation_performed':False,
        'closed_world_negatives':False, 'confidence_calibration_eligible':False,
        'reference_metrics_scope':'positive-label detection recall only; no general precision/accuracy claim',
        'files':{name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)} for name,data in outputs.items()},
    }
    (output_dir/'manifest.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=Path('data/reference/conssa-eligible-native-reference.jsonl'))
    parser.add_argument('--taxonomy',type=Path,default=Path('config/taxonomy.json'))
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        summary = prepare_reference(args.input,args.taxonomy,args.output)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,KeyError) as error:
        print(f'Curated reference error: {error}',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
