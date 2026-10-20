"""Prepare a fresh source cohort after measured ambiguous-romance holds.

No old native outputs or source pool are changed. Six-purpose admission and
exact source projections reuse the original preparation implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from types import ModuleType

from recommend_book.models import SourceRecord
from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
from recommend_book.scale_guard_context_v1 import enable_process_cache

ROOT = Path(__file__).resolve().parents[1]
SAFE_TERMS = {'fantasy', 'science fiction', 'detective fiction', 'mystery fiction',
              'horror fiction', 'thriller', 'adventure fiction', 'high fantasy',
              'urban fantasy', 'epic fantasy', 'space opera', 'cyberpunk'}


def original_preparer():
    path = ROOT / 'scripts/prepare_scale_classification_v1.py'
    text = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    anchor = "'run_name': 'scale-' + chunk_dir.name"
    if text.count(anchor) != 1:
        raise ValueError('Original new-window namespace anchor drift')
    text = text.replace(anchor, "'run_name': 'scale-recovery-' + chunk_dir.name")
    module = ModuleType('new_recovery_source_preparation')
    module.__file__ = str(path)
    exec(compile(text, str(path), 'exec'), module.__dict__)
    return module


def source_genres(record):
    ids = {s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
           for s in record.extra.get('claims', {}).get('P136', []) if s.get('rank') != 'deprecated'}
    labels = {(record.extra.get('linked_label_provenance', {}).get(qid, {}).get('label') or '').strip().casefold()
              for qid in ids}
    if 'Q19765983' in ids:
        return set()
    return labels & SAFE_TERMS


def select_candidate_records(records_path, catalogue, excluded, *, limit, preparer):
    with sqlite3.connect(Path(catalogue).resolve().as_uri() + '?mode=ro', uri=True) as db:
        visible = {row[0] for row in db.execute('SELECT qid FROM works WHERE display_allowed=1 AND recommendable=1')}
    selected, counters, seen = [], Counter(), set()
    with Path(records_path).open('rb') as stream:
        for line in stream:
            if not line.strip():
                continue
            record = SourceRecord.model_validate_json(line)
            qid = record.source_id
            if qid in seen:
                raise ValueError('Duplicate recovery candidate source ID')
            seen.add(qid)
            if qid in excluded or qid not in visible:
                counters['already_processed_legacy_or_not_catalogue_admitted'] += 1
                continue
            genres = source_genres(record)
            if not genres:
                counters['ambiguous_romance_or_no_selected_explicit_genre'] += 1
                continue
            # Preliminary scope uses the preserved raw claims. The unchanged
            # preparer subsequently rereads/SHA-validates the actual raw entity
            # and all six UsageGuard decisions before any model can run.
            if preparer.scope_issues(record, {'claims': record.extra.get('claims', {})}):
                counters['preliminary_scope_issue'] += 1
                continue
            selected.append(record)
            counters['selected'] += 1
            if len(selected) == limit:
                break
    if len(selected) != limit:
        raise ValueError('Insufficient fresh nonambiguous catalogue-admitted source records')
    return selected, dict(counters)


def prepare(args):
    preparer = original_preparer()
    snapshot = args.snapshot.resolve()
    snapshot.mkdir(parents=True, exist_ok=False)
    legacy, legacy_proofs = preparer.historical_qids(args.historical_database)
    processed, export_pins = set(), {}
    for path in args.processed_export:
        path = path.resolve(strict=True)
        raw = path.read_bytes()
        document = json.loads(raw)
        coordinator_chain_pins(document)
        export_pins[str(path)] = hashlib.sha256(raw).hexdigest()
        # Exclusion is conservative authority filtering, not reuse/admission
        # of classifications or authorization from a failed feature gate.
        processed.update(row['qid'] for row in document['verified_projection']['rows'])
    with sqlite3.connect(args.catalogue.resolve().as_uri() + '?mode=ro', uri=True) as db:
        legacy.update(row[0] for row in db.execute('SELECT DISTINCT qid FROM crosswalk'))
    selected, counts = select_candidate_records(args.records, args.catalogue, legacy | processed,
        limit=args.limit, preparer=preparer)
    records = snapshot / 'works-enriched.jsonl'
    with records.open('x', encoding='utf-8', newline='\n') as stream:
        for record in selected:
            stream.write(record.model_dump_json() + '\n')
    mapping = snapshot / 'processed-and-crosswalk-exclusions.json'
    preparer.save(mapping, [{'wikidata_qid': qid} for qid in sorted(legacy | processed)])
    recipe = {'version': 'scale-recovery-source-selection-v1', 'target_new_valid_classifications': 600,
              'prepared_source_count': args.limit, 'reserve_count': args.limit - 600,
              'processed_unique_excluded': len(processed), 'legacy_crosswalk_excluded': len(legacy),
              'processed_export_sha256': export_pins, 'legacy_query_proofs': legacy_proofs,
              'candidate_filter': 'catalogue-admitted, explicit selected P136 labels; Q19765983 excluded entirely',
              'safe_source_genre_terms': sorted(SAFE_TERMS), 'selection_counts': counts,
              'original_preparer_sha256': preparer.sha(ROOT / 'scripts/prepare_scale_classification_v1.py'),
              'wrapper_sha256': preparer.sha(__file__), 'original_pool_modified': False,
              'new_model_calls': 0, 'new_web_requests': 0}
    preparer.save(snapshot / 'selection-manifest.json', recipe)
    _cache = enable_process_cache()
    result = preparer.prepare_sources(records, args.output,
        historical_databases=args.historical_database, historical_mappings=[mapping],
        official_conditions=args.official_conditions, limit=args.limit)
    return {'source_preparation': str(args.output.resolve()), 'selected': result['selected'],
            'all_six_purpose_admissions_allowed': result['all_six_purpose_admissions_allowed'],
            'stage': 'fresh_recovery_source_prepared_no_models'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('records', 'catalogue', 'snapshot', 'output', 'official-conditions'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--historical-database', type=Path, action='append', required=True)
    parser.add_argument('--processed-export', type=Path, action='append', required=True)
    parser.add_argument('--limit', type=int, default=700)
    args = parser.parse_args()
    if not 600 <= args.limit <= 1000:
        parser.error('Fresh target requires 600 sources plus bounded reserve <=1000')
    print(json.dumps(prepare(args), ensure_ascii=True))


if __name__ == '__main__':
    main()
