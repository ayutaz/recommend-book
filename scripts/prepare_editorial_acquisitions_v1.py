"""Prepare at most twenty saved acquisitions using complete authority inputs.

No fetches, model calls, identity overrides or modifications of old datasets.
Each case must resolve normally before the existing purpose-checked batch flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import editorial_work_record, ingest_editorial_description
from recommend_book.ingestion.wikidata import LANGUAGE_CODES, claim_values
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord
from recommend_book.next_database import build_next_database

from prepare_standardebooks_batch_v1 import prepare
from prepare_standardebooks_pilot_v1 import BASE, save, sha


def normalize_supported_metadata(record):
    """Project display names; retain raw claims and unrecognized language IDs.

    The adapter keeps country authority IDs even when linked labels are supplied.
    Unknown language codes stay unknown rather than becoming a guessed locale.
    The existing UsageGuard independently reproduces all projected values.
    """
    projected = record.model_copy(deep=True)
    proofs = projected.extra.get('linked_label_provenance', {})
    regions = []
    for value in projected.region:
        if value not in proofs:
            regions.append(value)
            continue
        proof = proofs[value]
        if sha(proof['raw_path']) != proof['raw_sha256']:
            raise ValueError('Linked country raw changed')
        entity = json.loads(Path(proof['raw_path']).read_bytes())['entities'][value]
        if entity.get('id') != value or proof['label'] not in {v['value'] for v in entity.get('labels', {}).values()}:
            raise ValueError('Linked country label not reproduced')
        regions.append(proof['label'])
    projected.region = regions
    refs = [v['id'] for v in claim_values({'claims': record.extra['claims']}, 'P407')
            if isinstance(v, dict) and 'id' in v]
    if len(refs) == 1 and record.original_language == refs[0] and refs[0] not in LANGUAGE_CODES:
        projected.original_language = None
    if (projected.region, projected.original_language) != (record.region, record.original_language):
        projected.extra['metadata_normalization_projection'] = {
            'original_record_sha256': hashlib.sha256(canonical_json(record.model_dump(mode='json')).encode()).hexdigest(),
            'original_region': record.region, 'original_language_value': record.original_language,
            'raw_language_qids_retained': refs, 'raw_claims_preserved': True}
    return projected


def prepare_acquisitions(cases_path, records_path, output):
    cases_path, records_path = (Path(p).resolve(strict=True) for p in (cases_path, records_path))
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(BASE / 'preparation'):
        raise ValueError('Fresh preparation namespace required')
    acquisition = json.loads(cases_path.read_bytes())
    cases = acquisition['eligible_cases']
    if not 1 <= len(cases) <= 20:
        raise ValueError('One through twenty frozen acquisitions required')
    if len({c['wikidata_qid'] for c in cases}) != len(cases):
        raise ValueError('Repeated candidate work identity')
    frozen = Path(acquisition['frozen_selection_path']).resolve(strict=True)
    if sha(frozen) != acquisition['frozen_selection_sha256']:
        raise ValueError('Frozen selection changed')
    frozen_rows = json.loads(frozen.read_bytes())['rows']
    selected_urls = {row['source_url'] for row in frozen_rows}
    if (not 1 <= len(frozen_rows) <= 20 or len(selected_urls) != len(frozen_rows)
            or len({case['source_url'] for case in cases}) != len(cases)
            or not {case['source_url'] for case in cases} <= selected_urls):
        raise ValueError('Acquisitions differ from bounded pre-fetch selection')
    records = [SourceRecord.model_validate_json(line) for line in
               records_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    by_qid = {record.source_id: record for record in records}
    if len(by_qid) != len(records):
        raise ValueError('Repeated normalized authority record')
    output.mkdir(parents=True)
    accepted, selected, outcomes = [], [], []
    for case in cases:
        qid = case['wikidata_qid']
        original = by_qid[qid]
        record = normalize_supported_metadata(original)
        if ((record.source, record.provider_id, record.record_kind) != ('wikidata', 'wikidata', 'work')
                or record.evidence or sha(record.raw_path) != record.raw_sha256
                or record.raw_sha256 != case['wikidata_raw_sha256']
                or sha(case['opf_path']) != case['opf_sha256']):
            raise ValueError('Original metadata-only acquisition binding differs: ' + qid)
        try:
            edition = ingest_editorial_description(case['opf_path'], source_url=case['source_url'],
                retrieved_at=case['retrieved_at'], wikidata_record=record)
            editorial = editorial_work_record(edition)
            with DatasetDB(':memory:') as db:
                db.ingest([record, edition, editorial])
                works = [w for w in db.work_inputs() if w.expression_id is None]
                decisions = [dict(row) for row in db.connection.execute('SELECT status,reason FROM resolution_decisions')]
            if len(works) != 1 or len(works[0].source_record_ids) != 3 or len(works[0].evidence) != 1:
                outcomes.append({'qid': qid, 'title': case['title'], 'status': 'held',
                    'reason': 'Complete authority/edition/editorial input does not resolve normally',
                    'work_count': len(works), 'decisions': decisions})
                continue
            accepted.append(record)
            selected.append(case)
            outcomes.append({'qid': qid, 'title': case['title'], 'status': 'eligible',
                             'complete_input_sources': len(works[0].source_record_ids)})
        except ValueError as exc:
            outcomes.append({'qid': qid, 'title': case['title'], 'status': 'held', 'reason': str(exc)})
    save(output / 'complete-input-outcomes.json', outcomes)
    save(output / 'normalized-authorities.json', [r.model_dump(mode='json') for r in accepted])
    if not selected:
        report = {'stage': 'no_complete_inputs', 'eligible': 0, 'held': len(outcomes), 'model_calls': 0}
        save(output / 'preparation-report.json', report)
        return report
    metadata = output / 'metadata/authority.sqlite'
    with DatasetDB(metadata) as db:
        db.ingest(accepted)
        rows = list(db.connection.execute("SELECT value,entity_id FROM external_ids WHERE source='wikidata' AND scope='work'"))
        identities = {row['value']: row['entity_id'] for row in rows}
    with sqlite3.connect(metadata) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    authority_next = output / 'authority-next'
    build_next_database(source_database=metadata, output_dir=authority_next)
    admitted_cases = {'eligible_cases': [{**case, 'delivery_work_id': identities[case['wikidata_qid']]} for case in selected],
        'frozen_selection_path': str(frozen), 'frozen_selection_sha256': sha(frozen)}
    admitted_path = output / 'eligible-cases.json'
    save(admitted_path, admitted_cases)
    batch = prepare(admitted_path, output / 'batch', authority_database=authority_next / 'dataset.sqlite')
    mapping = json.loads((output / 'batch/work-mapping.json').read_bytes())
    save(output / 'delivery-mapping.json', {'cases': mapping})
    report = {'stage': 'prepared_not_started', 'candidate_report_path': str(cases_path),
        'candidate_report_sha256': sha(cases_path), 'authority_records_path': str(records_path),
        'authority_records_sha256': sha(records_path), 'eligible': len(selected),
        'complete_input_held': len(outcomes)-len(selected), 'model_calls': 0,
        'authority_database': str(authority_next / 'dataset.sqlite'),
        'authority_database_sha256': sha(authority_next / 'dataset.sqlite'), 'batch': batch}
    save(output / 'preparation-report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_acquisitions(args.cases, args.records, args.output), ensure_ascii=False))
