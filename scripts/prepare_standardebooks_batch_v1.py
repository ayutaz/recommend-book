"""Freeze purpose-checked new editorial inputs, at most twenty existing works.

Reads a bounded acquisition report and saved authority records. No network,
private profile, model calls, or changes to historical inputs are performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import editorial_work_record, ingest_editorial_description
from recommend_book.models import SourceRecord
from recommend_book.next_database import build_next_database, get_next_inputs
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding

from prepare_standardebooks_pilot_v1 import BASE, ROOT, SOURCE_DB, save, sha

PREVIOUS = BASE / 'preparation/phase9-standardebooks-pilot-v3'


def prepare(cases_path, output):
    cases_path = Path(cases_path).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Existing preparation is immutable; use a new namespace')
    acquisition = json.loads(cases_path.read_bytes())
    cases = acquisition['eligible_cases']
    if not 1 <= len(cases) <= 20:
        raise ValueError('Only one to twenty new work descriptions may be prepared')
    for field in ('delivery_work_id', 'wikidata_qid', 'source_url'):
        if len({row[field] for row in cases}) != len(cases):
            raise ValueError('Duplicate bounded acquisition identity: ' + field)
    frozen = Path(acquisition['frozen_selection_path'])
    frozen = frozen if frozen.is_absolute() else ROOT / frozen
    if sha(frozen) != acquisition['frozen_selection_sha256']:
        raise ValueError('Original candidate selection changed')
    registry_before = sha(ROOT / 'config/sources.json')
    records, selected = [], []
    with sqlite3.connect(SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        for case in cases:
            qid = case['wikidata_qid']
            if not db.execute('SELECT 1 FROM external_ids WHERE entity_id=? AND scope=? AND source=? AND value=?',
                              (case['delivery_work_id'], 'work', 'wikidata', qid)).fetchone():
                raise ValueError('Current delivery work does not bind the authority: ' + qid)
            rows = list(db.execute('SELECT r.record_json FROM source_heads h JOIN source_records r ON r.version_id=h.version_id '
                                   'WHERE h.source=? AND h.source_id=?', ('wikidata', qid)))
            if len(rows) != 1:
                raise ValueError('Saved authority source head is not unique: ' + qid)
            wd = SourceRecord.model_validate_json(rows[0][0])
            if sha(wd.raw_path) != wd.raw_sha256 or wd.raw_sha256 != case['wikidata_raw_sha256']:
                raise ValueError('Saved authority bytes differ: ' + qid)
            wd = wd.model_copy(update={'evidence': []}, deep=True)
            opf = Path(case['opf_path'])
            opf = opf if opf.is_absolute() else ROOT / opf
            if sha(opf) != case['opf_sha256']:
                raise ValueError('New OPF acquisition bytes differ: ' + qid)
            edition = ingest_editorial_description(opf, source_url=case['source_url'],
                retrieved_at=case['retrieved_at'], wikidata_record=wd)
            editorial = editorial_work_record(edition)
            records.extend((wd, edition, editorial))
            selected.append({**case, 'new_evidence_id': editorial.evidence[0].evidence_id})
    output.mkdir(parents=True)
    source = output / 'sources/source-bindings.sqlite'
    source.parent.mkdir()
    with DatasetDB(source) as db:
        ingested = db.ingest(records)
        works = [w for w in db.work_inputs() if w.expression_id is None]
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    if len(works) != len(cases) or any(len(w.evidence) != 1 for w in works):
        raise ValueError('Acquisitions must resolve to distinct works with one new synopsis each')
    by_evidence = {row['new_evidence_id']: row for row in selected}
    works.sort(key=lambda w: next(i for i, row in enumerate(selected) if row['new_evidence_id'] == w.evidence[0].evidence_id))
    mapping = [{'input_work_id': w.work_id, **by_evidence[w.evidence[0].evidence_id]} for w in works]
    registry_path = output / 'source-registry.json'
    registry_path.write_bytes((PREVIOUS / 'source-registry.json').read_bytes())
    registry = RightsRegistry.from_file(registry_path)
    previous_ledger = json.loads((PREVIOUS / 'usage-ledger-projections.json').read_bytes())
    ordered = output / 'ordered-inputs.jsonl'
    ordered.write_text(''.join(w.model_dump_json() + '\n' for w in works), encoding='utf-8', newline='\n')
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path),
              'registry_file_sha256': sha(registry_path),
              'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': previous_ledger['official_conditions'], 'inputs': {},
              'revoked_input_hashes': [], 'derived_artifacts': {}, 'execution_recipes': {}}

    def bind(values, database, path):
        for number, work in enumerate(values, 1):
            refs = {ref: source_binding(database, ref) for ref in work.source_record_ids}
            ledger['inputs'][model_hash(work)] = build_input_entry(work, refs,
                input_ref={'path': str(path), 'sha256': sha(path), 'line_number': number})

    bind(works, source, ordered)
    initial_ledger = output / 'usage-ledger-inputs.json'
    save(initial_ledger, ledger)
    guard = UsageGuard(initial_ledger, expected_ledger_sha256=sha(initial_ledger))
    receipts = {usage: [guard.check_input(w, usage).model_dump(mode='json') for w in works] for usage in USAGES}
    save(output / 'admission-receipts.json', receipts)
    if any(not row['allowed'] for rows in receipts.values() for row in rows):
        raise ValueError('One or more source-purpose admissions held; inspect receipts')
    next_dir = output / 'next-source'
    build_next_database(source_database=source, output_dir=next_dir)
    groups = get_next_inputs(next_dir / 'dataset.sqlite')
    actual = {model_hash(w): w for w in groups['classification']}
    if actual != {model_hash(w): w for w in works}:
        raise ValueError('Actual next-DB classification inputs differ')
    for kind in ('classification', 'bibliographic', 'aliases'):
        bind(groups[kind], next_dir / 'dataset.sqlite', next_dir / (kind + '-inputs.jsonl'))
    packets = next_dir / 'derivations.jsonl'
    packet_sha = sha(packets)
    for number, line in enumerate(packets.read_text(encoding='utf-8').splitlines(), 1):
        packet = json.loads(line)
        ledger['derived_artifacts'][model_hash(packet['payload'])] = {
            'kind': packet['kind'], 'value_sha256': packet['value_sha256'],
            'originating_input_hashes': packet['originating_input_hashes'], 'raw_path': str(packets),
            'raw_sha256': packet_sha, 'format': 'jsonl', 'line_number': number,
            'locator': '/payload', 'recipe': packet['recipe']}
    save(output / 'usage-ledger-projections.json', ledger)
    for name, values in (('initial', works[:10]), ('remaining', works[10:])):
        (output / (name + '-inputs.jsonl')).write_text(''.join(w.model_dump_json() + '\n' for w in values), encoding='utf-8', newline='\n')
    save(output / 'work-mapping.json', mapping)
    if sha(ROOT / 'config/sources.json') != registry_before:
        raise ValueError('Protected source registry changed')
    report = {'version': 'standardebooks-batch-preparation-v1', 'stage': 'prepared_not_started',
              'acquisition_path': str(cases_path), 'acquisition_sha256': sha(cases_path),
              'source_records_ingested': ingested, 'eligible_works': len(works),
              'initial_works': len(works[:10]), 'remaining_works': len(works[10:]),
              'new_distinct_novels': 0, 'model_calls': 0, 'root_registry_unchanged': True,
              'next_database_sha256': sha(next_dir / 'dataset.sqlite')}
    save(output / 'preparation-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.cases, args.output), ensure_ascii=True))
