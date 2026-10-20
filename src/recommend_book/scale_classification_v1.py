"""Evidence and separate-review admission of authentic scale classifications.

This future-only projection retains the original result, raw confidence, and
null relevance. Explicit source genre words can support a genre label; title,
author, country, prestige and unknown axes never create content features.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import os
import sqlite3
import subprocess
import sys

POLICY = 'scale-native-source-review-admission-v1'
# Deliberately finite entailment policy. These are source genre statements,
# not an ontology/subclass guess. Ambiguous "romance", "literary", "social",
# "speculative fiction", "crime" and publication age are not admitted.
GENRE_TERMS = {
    'genre.mystery': {'mystery fiction', 'mystery novel', 'mystery novels', 'detective fiction', 'detective novel', 'detective novels', '推理小説', '探偵小説'},
    'genre.fantasy': {'fantasy', 'fantasy fiction', 'fantasy novel', 'fantasy novels', 'high fantasy', 'urban fantasy', 'dark fantasy', 'ファンタジー', 'ファンタジー小説'},
    'genre.science_fiction': {'science fiction', 'science fiction novel', 'science fiction novels', 'science-fiction novel', 'science-fiction novels', 'サイエンス・フィクション', 'sf小説', '科学小説'},
    'genre.romance': {'romance novel', 'romance novels', 'romantic novel', 'romantic fiction', '恋愛小説'},
    'genre.historical': {'historical fiction', 'historical novel', 'historical novels', '歴史小説'},
    'genre.horror': {'horror fiction', 'horror novel', 'horror novels', 'ホラー小説', 'ホラー'},
    'genre.thriller': {'thriller', 'thriller novel', 'thriller novels', 'psychological thriller', 'political thriller', 'spy thriller', 'スリラー', 'スリラー小説'},
    'genre.adventure': {'adventure fiction', 'adventure novel', 'adventure novels', '冒険小説'},
    'genre.satire': {'satire', 'satirical novel', 'satirical novels', 'satirical fiction', '風刺小説'},
    'genre.dystopia': {'dystopian fiction', 'dystopian novel', 'dystopian novels', 'ディストピア小説'},
}


def _normalized(text):
    return ' '.join(text.casefold().split())


def _supports(evidence, label_id):
    if evidence.rights_policy_id != 'wikidata-cc0' or not evidence.llm_input_allowed:
        return False
    terms = GENRE_TERMS.get(label_id, set())
    if evidence.kind == 'subject' and evidence.locator == 'claims.P136;claims.P921':
        return bool({_normalized(t) for t in evidence.text.split(';')} & terms)
    if evidence.kind == 'description' and (evidence.locator or '').startswith('descriptions.'):
        text = _normalized(evidence.text)
        # Descriptions must explicitly call the work a genre of novel/fiction;
        # an isolated topic, quoted title or an author's profession is not enough.
        for term in terms:
            if 'novel' not in term and 'fiction' not in term and '小説' not in term:
                continue
            if re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text):
                return True
    return False


def admit_native_labels(work, result, *, source_genre_terms=None, review_cleared_label_ids=()):
    """Select supported native labels; never manufacture a missing model label."""
    if result['work_id'] != work.work_id:
        raise ValueError('Native label admission work identity differs')
    evidence = {e.evidence_id: e for e in work.evidence}
    admitted, held = [], []
    for label in result['labels']:
        reason = None
        if result['conflicts']:
            reason = 'native_source_conflict'
        elif label['decision'] != 'present' or label['scope'] != 'work':
            reason = 'not_present_work_label'
        elif label['label_id'] in set(review_cleared_label_ids):
            if not label['evidence'] or any(ref['evidence_id'] not in evidence or ref['locator'] != evidence[ref['evidence_id']].locator for ref in label['evidence']):
                raise ValueError('Separately reviewed native label lacks exact source citations')
        elif label['axis'] != 'genre' or label['label_id'] not in GENRE_TERMS:
            reason = 'metadata_only_evidence_cannot_establish_this_label'
        else:
            citations = label['evidence']
            supported = []
            for ref in citations:
                source = evidence.get(ref['evidence_id'])
                if source is None or ref['locator'] != source.locator:
                    raise ValueError('Native label citation not bound to exact source evidence')
                genre_confirmed = (source_genre_terms is None or source.kind != 'subject' or
                    bool(set(source_genre_terms) & GENRE_TERMS.get(label['label_id'], set())))
                quoted = ref.get('quote')
                citation_supports = not quoted or _supports(source.model_copy(update={'text': quoted}), label['label_id'])
                if genre_confirmed and citation_supports and _supports(source, label['label_id']):
                    supported.append(source.evidence_id)
            if not supported:
                reason = 'explicit_cited_genre_entailment_absent'
        if reason:
            held.append({'label_id': label['label_id'], 'reason': reason})
        else:
            admitted.append(deepcopy(label))
    return {'policy': POLICY, 'work_id': work.work_id, 'admitted_labels': admitted,
            'held_labels': held, 'native_result_modified': False, 'human_verified': False,
            'confidence_calibrated': False, 'null_relevance_preserved': True,
            'evidence_scope': 'source statements; not independent whole-work truth',
            'separate_review_cleared_label_ids': sorted(set(review_cleared_label_ids)),
            'unknown_axes_preserved': True}


def authenticated_rows(classified_database, ledger_path, mapping_path):
    """Reproduce frozen v4 native lineage and display usage before exporting.

    Called only in the frozen runtime interpreter; the v4 loader reproduces
    source receipts, typed attempts, actual stdout/final bindings, every native
    label and feature derivation before the stricter genre-only projection.
    """
    from recommend_book.next_database_v4 import load_next_world_database
    from recommend_book.usage_guard_v4 import UsageGuard
    from recommend_book.usage_guard import model_hash
    from recommend_book.next_database_v4 import get_next_inputs
    from recommend_book.ingestion.wikidata import claim_values
    from recommend_book.classification.validation import ROOT as runtime_root
    artifacts = {}
    def pin(path, expected=None):
        path = Path(path).resolve(strict=True)
        if str(path) in artifacts:
            if expected is not None and artifacts[str(path)] != expected:
                raise ValueError('Native export dependency has contradictory receipts')
            return  # exact bytes re-read again at the publication boundary
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected is not None and actual != expected:
            raise ValueError('Native export admission dependency changed: ' + str(path))
        artifacts[str(path)] = actual
    pin(classified_database)
    pin(mapping_path)
    pin(ledger_path)
    ledger = json.loads(Path(ledger_path).read_bytes())
    pin(ledger['registry_path'], ledger['registry_file_sha256'])
    for items in ledger['official_conditions'].values():
        for item in items:
            pin(item['raw_path'], item['raw_sha256'])
    for entry in ledger['inputs'].values():
        if 'input_ref' in entry:
            pin(entry['input_ref']['path'], entry['input_ref']['sha256'])
        for source in entry['sources'].values():
            pin(source['database_path'])
            pin(source['raw_path'], source['raw_sha256'])
    for entry in ledger['derived_artifacts'].values():
        pin(entry['raw_path'], entry['raw_sha256'])
        pin(entry['recipe']['path'], entry['recipe']['sha256'])
    for name in ('execution_recipes', 'feature_recipes'):
        for entry in ledger.get(name, {}).values():
            key = 'verifier_path' if 'verifier_path' in entry else 'path'
            hash_key = 'verifier_sha256' if 'verifier_sha256' in entry else 'sha256'
            pin(entry[key], entry[hash_key])
    mapping = json.loads(Path(mapping_path).read_bytes())
    by_id = {row['work_id']: row for row in mapping}
    if len(by_id) != len(mapping):
        raise ValueError('Duplicate classification-to-catalogue mapping')
    guard = UsageGuard(ledger_path)
    loaded = load_next_world_database(classified_database, guard=guard, usage='display', content_classified=True)
    input_by_id = {w.work_id: w for w in get_next_inputs(classified_database)['classification']}
    with sqlite3.connect(Path(classified_database).resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        quality_cases = {json.loads(row[0])['case']['work_id']: json.loads(row[0])['case'] for row in
                         db.execute('SELECT packet_json FROM next_quality_adoption_review')}
        all_native = [json.loads(row[0]) for row in db.execute("SELECT result_json FROM next_classification_inputs WHERE status='completed'")]
        reviews = [json.loads(row[0]).get('quality_adoption_review') for row in db.execute('SELECT provenance_json FROM next_classification_runs')]
    all_native_by_id = {value['work_id']: value for value in all_native}
    review_cleared = {}
    from recommend_book.classification.runner_v4 import bind_final_message
    for review_binding in reviews:
        if not review_binding:
            continue
        pin(review_binding['path'], review_binding['sha256'])
        document = json.loads(Path(review_binding['path']).read_bytes())
        covered = set()
        for audit_binding in document['source_audits']:
            audit_path = Path(audit_binding['path'])
            pin(audit_path, audit_binding['sha256'])
            audit = json.loads(audit_path.read_bytes())
            if audit.get('protocol') != 'complete-input-native-output-internal-review-v2' or audit.get('all_native_labels_examined') is not True:
                raise ValueError('Genuine complete-label separate review protocol required')
            for name, expected in audit['artifact_sha256'].items():
                pin(audit_path.parent / name, expected)
            for path, expected in audit['source_sha256'].items():
                pin(path, expected)
            binding = bind_final_message((audit_path.parent / 'stdout.jsonl').read_bytes(), (audit_path.parent / 'review.final.json').read_bytes())
            observation = json.loads((audit_path.parent / 'process-exit.json').read_bytes())
            start = json.loads((audit_path.parent / 'process-start.json').read_bytes())
            if observation.get('actual_exit_code') != 0 or observation.get('process_exit_observed') is not True or start['pid'] != observation['pid'] or start['pid'] != audit['native_review_process_pid']:
                raise ValueError('Separate review actual PID/observed exit differs')
            if binding['events']['tool_calls'] or binding['events']['event_errors'] or binding['value']['reviews'] != audit['reviews']:
                raise ValueError('Separate review actual final/event binding differs')
            for review in audit['reviews']:
                work_id = review['work_id']
                if work_id in covered or work_id not in all_native_by_id:
                    raise ValueError('Separate review coverage identity differs')
                covered.add(work_id)
                labels = {label['label_id'] for label in all_native_by_id[work_id]['labels']}
                if len(review['reviewed_label_ids']) != len(labels) or set(review['reviewed_label_ids']) != labels:
                    raise ValueError('Separate review omitted actual native labels')
                affected = {label for finding in review['findings'] for label in finding['label_ids']}
                case = quality_cases.get(work_id)
                if not affected <= labels or (bool(affected) != bool(case)) or (case and set(case['label_ids']) != affected):
                    raise ValueError('Separate findings differ from adopted quality cases')
                review_cleared[work_id] = labels - affected
        if covered != set(all_native_by_id):
            raise ValueError('Separate review does not cover every completed native work')
    rows = []
    for row in loaded['rows']:
        packet = row['feature_derivation']
        for item in packet['source_manifest']['artifacts'].values():
            pin(item['path'], item['sha256'])
        request = json.loads(Path(packet['source_manifest']['artifacts']['request']['path']).read_bytes())
        for path, digest in request['executable_sha256'].items():
            pin(path, digest)
        for path, digest in request['config']['runtime_source_sha256'].items():
            pin(runtime_root / path, digest)
        lineage = packet['adoption_provenance']
        for path_key, hash_key in (('ordered_inputs_path', 'ordered_inputs_sha256'),
            ('execution_context_manifest_path', 'execution_context_manifest_sha256'),
            ('run_manifest_path', 'run_manifest_sha256'), ('results_index_path', 'results_index_sha256'),
            ('terminal_proof_path', 'terminal_proof_sha256')):
            pin(lineage[path_key], lineage[hash_key])
        native = packet['native_result']
        work_id = row['work_id']
        if work_id not in by_id:
            raise ValueError('Authentic native work lacks exact catalogue mapping')
        binding = by_id[work_id]
        work = input_by_id[work_id]
        if binding['input_sha256'] != model_hash(work) or binding['source_record_ids'] != work.source_record_ids:
            raise ValueError('Exact catalogue mapping input/source references differ')
        with sqlite3.connect(Path(classified_database).resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            qids = {item[0] for item in db.execute("SELECT value FROM external_ids WHERE source='wikidata' AND scope='work' AND entity_id=?", (work_id,))}
            if qids != {binding['wikidata_qid']} or binding['catalogue_work_id'] != 'wd:' + binding['wikidata_qid']:
                raise ValueError('Native-to-catalogue Wikidata identity differs')
            source_records = [json.loads(db.execute('SELECT record_json FROM source_records WHERE version_id=?', (ref,)).fetchone()[0]) for ref in work.source_record_ids]
        genre_terms = set()
        for record in source_records:
            pin(record['raw_path'], record['raw_sha256'])
            for origin in record['extra'].get('linked_label_provenance', {}).values():
                pin(origin['raw_path'], origin['raw_sha256'])
            raw_entity = json.loads(Path(record['raw_path']).read_bytes())['entities'][record['source_id']]
            for value in claim_values(raw_entity, 'P136'):
                if not isinstance(value, dict) or 'id' not in value:
                    continue
                ref = value['id']
                origin = record['extra'].get('linked_label_provenance', {}).get(ref)
                if origin:
                    genre_terms.add(_normalized(origin['label']))
                else:
                    linked = json.loads(Path(record['raw_path']).read_bytes()).get('entities', {}).get(ref, {})
                    genre_terms.update(_normalized(item['value']) for item in linked.get('labels', {}).values())
        decision = admit_native_labels(work, native, source_genre_terms=genre_terms,
                                       review_cleared_label_ids=review_cleared.get(work_id, ()))
        quality = quality_cases.get(work_id)
        if quality:
            affected = set(quality['label_ids'])
            independently_withheld = [label for label in decision['admitted_labels'] if label['label_id'] in affected]
            decision['admitted_labels'] = [label for label in decision['admitted_labels'] if label['label_id'] not in affected]
            decision['held_labels'].extend({'label_id': label['label_id'], 'reason': 'separate_native_review_semantic_uncertainty'} for label in independently_withheld)
            decision['separate_review_case'] = quality
        rows.append({'qid': binding['wikidata_qid'], 'work_id': binding['catalogue_work_id'],
                     'input_work_id': work_id, 'input_hash': model_hash(work),
                     'run_id': packet['adoption_provenance']['run_id'],
                     'native_result': native, 'admitted_labels': decision['admitted_labels'],
                     'label_admission': decision, 'native_provenance': packet['adoption_provenance'],
                     'source_record_ids': work.source_record_ids})
    if any(hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected for path, expected in artifacts.items()):
        raise ValueError('Native export dependency changed before publication')
    axes = ('genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere')
    axis_counts = {}
    for axis in axes:
        raw = [label for value in all_native for label in value['labels'] if label['axis'] == axis]
        admitted = [label for row in rows for label in row['admitted_labels'] if label['axis'] == axis]
        axis_counts[axis] = {'raw_present_labels': sum(label['decision'] == 'present' for label in raw),
            'raw_absent_labels': sum(label['decision'] == 'absent' for label in raw),
            'unknown_work_count': sum(any(item['axis'] == axis for item in value['missing_axes']) for value in all_native),
            'adopted_labels': len(admitted), 'held_present_labels': sum(label['decision'] == 'present' for label in raw) - len(admitted)}
    return {'version': 'scale-native-admitted-rows-v1', 'rows': rows, 'artifact_sha256': artifacts,
            'all_native_results': all_native, 'axis_counts': axis_counts,
            'raw_classification_saved_count': loaded['audit']['raw_classification_saved_count'],
            'actual_feature_adopted_works': sum(bool(row['admitted_labels']) for row in rows),
            'policy': POLICY, 'human_verified': False}


def verify_catalogue_classification(manifest_path, expected_sha256):
    """Backend boundary: independently replay authentic native display lineage.

    The subprocess imports the frozen local verifier only. It has no classifier
    invocation path and receives no API keys. Caller must pin the export SHA.
    """
    manifest_path = Path(manifest_path).resolve(strict=True)
    raw = manifest_path.read_bytes()
    if not expected_sha256 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Classification export manifest SHA differs')
    document = json.loads(raw)
    if document.get('version') != 'scale-native-classification-export-v1':
        raise ValueError('Unknown classification export version')
    root = Path(__file__).resolve().parents[2]
    runtime_preparation = Path(document['runtime_preparation']).resolve(strict=True)
    if not runtime_preparation.is_relative_to(root / 'data/autonomous-next-v3/preparation'):
        raise ValueError('Classification frozen runtime outside preparation tree')
    runtime = runtime_preparation / 'runtime'
    verifier = runtime / 'src/recommend_book/scale_classification_v1.py'
    if verifier.read_bytes() != Path(__file__).read_bytes():
        raise ValueError('Classification projection verifier differs from frozen code')
    pins = document['artifact_sha256']
    for path, expected in pins.items():
        if not Path(path).is_absolute() or hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError('Classification export artifact changed: ' + path)
    for key in ('classified_database', 'usage_ledger', 'work_mapping'):
        if str(Path(document[key]).resolve(strict=True)) not in pins:
            raise ValueError('Classification export lacks required artifact pin')
    code = ('from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); '
            'import json,sys; from recommend_book.scale_classification_v1 import authenticated_rows; '
            'print(json.dumps(authenticated_rows(*sys.argv[1:]),ensure_ascii=True,sort_keys=True,allow_nan=False))')
    env = dict(os.environ, PYTHONPATH=str(runtime / 'src'), PYTHONDONTWRITEBYTECODE='1')
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
        env.pop(name, None)
    process = subprocess.run([sys.executable, '-c', code, document['classified_database'],
                              document['usage_ledger'], document['work_mapping']],
                             env=env, cwd=root, capture_output=True, shell=False, timeout=900)
    if process.returncode:
        raise ValueError('Frozen native display-lineage verification failed: ' + process.stderr.decode('utf-8', 'replace')[-2000:])
    verified = json.loads(process.stdout)
    if verified != document['verified_projection']:
        raise ValueError('Classification export differs from independently replayed projection')
    if manifest_path.read_bytes() != raw or any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != h for p, h in pins.items()):
        raise ValueError('Classification export changed during verification')
    return [{'qid': row['qid'], 'work_id': row['work_id'], 'input_work_id': row['input_work_id'], 'labels': row['admitted_labels'],
             'provenance': {'export_manifest_path': str(manifest_path), 'export_manifest_sha256': expected_sha256,
                            'run_id': row['run_id'], 'native_result': row['native_result'],
                            'label_admission': row['label_admission'], 'native_provenance': row['native_provenance'],
                            'input_hash': row['input_hash'], 'source_record_ids': row['source_record_ids'],
                            'artifacts': [{'path': str(manifest_path), 'sha256': expected_sha256},
                                          *[{'path': path, 'sha256': digest} for path, digest in pins.items()]]}}
            for row in verified['rows']]
