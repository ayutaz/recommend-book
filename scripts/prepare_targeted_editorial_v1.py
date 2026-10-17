"""Reassess the ten saved cases with audited corrections; never fetch or infer IDs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.authority_identity_correction import project_identity_correction
from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import editorial_work_record, ingest_editorial_description
from recommend_book.ingestion.wikidata import claim_values
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord
from recommend_book.next_database import build_next_database

from prepare_standardebooks_pilot_v1 import BASE, ROOT, save, sha
from prepare_standardebooks_batch_v1 import prepare


def bind_saved_labels(record, saved, label_receipt):
    """Reproduce linked names from pinned official entities without dropping fields."""
    authors = saved / 'raw/wikidata-enwiki-batch.json'
    if sha(authors) != 'a15aa93b40d0a8d5cb1ccdb0f0551301b15823bf1a7af3c8b619900f5d9ceff7':
        raise ValueError('Saved author authority changed')
    labels = Path(label_receipt['path']).resolve(strict=True)
    if sha(labels) != label_receipt['sha256']:
        raise ValueError('New linked label receipt differs')
    sources = [(authors, json.loads(authors.read_bytes())['entities']),
               (labels, json.loads(labels.read_bytes())['entities'])]
    projected = record.model_copy(deep=True)
    proof = projected.extra.setdefault('linked_label_provenance', {})
    for prop in ('P50', 'P495', 'P179'):
        for value in claim_values({'claims': projected.extra['claims']}, prop):
            qid = value['id']
            matches = [(path, entities[qid]) for path, entities in sources if qid in entities]
            if len(matches) != 1:
                # Non-admitted cases may reference other series. Keep their
                # unsupported fields and original holds; never invent names.
                continue
            path, entity = matches[0]
            label = entity['labels']['en']['value']
            proof[qid] = {'label': label, 'raw_path': str(path.resolve()), 'raw_sha256': sha(path)}
    projected.region = [proof.get(q, {}).get('label', q) for q in projected.region]
    if projected.series:
        projected.series = '; '.join(proof.get(q, {}).get('label', q) for q in projected.series.split('; '))
    return projected


def select(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(BASE / 'preparation'):
        raise ValueError('Fresh preparation namespace required')
    saved = BASE / 'evaluation/phase11-new-authority-v1'
    candidates = json.loads((saved / 'candidate-results.json').read_bytes())
    if len(candidates) != 10:
        raise ValueError('Only the ten frozen saved cases are in scope')
    originals = {r.source_id: r for r in (SourceRecord.model_validate_json(line)
        for line in (saved / 'wd-records.jsonl').read_text(encoding='utf-8').splitlines())}
    label_receipt = json.loads((BASE / 'evaluation/phase12-linked-labels-v1/receipt.json').read_bytes())
    originals = {qid: bind_saved_labels(record, saved, label_receipt) for qid, record in originals.items()}
    output.mkdir(parents=True)
    original = originals['Q224078']
    aggregate = BASE / 'evaluation/phase12-identity-v1/raw/OL471547W.json'
    claims = original.extra['claims']['P648']
    indices = [i for i, s in enumerate(claims) if s.get('mainsnak', {}).get('datavalue', {}).get('value') == 'OL471547W']
    if len(indices) != 1:
        raise ValueError('One exact quarantined assertion required')
    packet = {'schema': 'authority-identity-correction-v1',
        'original_record': original.model_dump(mode='json'),
        'original_record_sha256': hashlib.sha256(canonical_json(original.model_dump(mode='json')).encode()).hexdigest(),
        'wikidata_raw': {'path': original.raw_path, 'sha256': original.raw_sha256,
            'entity_id': original.source_id,
            'claim_locator': f'/entities/Q224078/claims/P648/{indices[0]}/mainsnak/datavalue/value'},
        'quarantined_identifier': '/works/OL471547W',
        'aggregate_proof': {'official_url': 'https://openlibrary.org/works/OL471547W.json',
            'raw_path': str(aggregate.resolve()), 'raw_sha256': sha(aggregate)}}
    correction = output / 'identity-correction.json'
    save(correction, packet)
    projected = project_identity_correction(original, correction, sha(correction))
    save(output / 'projected-Q224078.json', projected.model_dump(mode='json'))
    records, eligible, outcomes = [], [], []
    for case in candidates:
        qid = case['wikidata_qid']
        wd = projected if qid == original.source_id else originals[qid]
        if sha(wd.raw_path) != wd.raw_sha256 or sha(case['opf_path']) != case['opf_sha256']:
            raise ValueError('Frozen source bytes changed')
        try:
            edition = ingest_editorial_description(case['opf_path'], source_url=case['source_url'],
                retrieved_at=case['retrieved_at'], wikidata_record=wd)
            editorial = editorial_work_record(edition)
            with DatasetDB(':memory:') as db:
                db.ingest([wd, edition, editorial])
                works = [w for w in db.work_inputs() if w.expression_id is None]
                decisions = [dict(r) for r in db.connection.execute('SELECT status,reason FROM resolution_decisions')]
            if len(works) != 1 or len(works[0].evidence) != 1 or len(works[0].source_record_ids) != 3:
                outcomes.append({'qid': qid, 'title': case['title'], 'status': 'held',
                    'reason': 'Complete authority and editorial records do not resolve to one work',
                    'work_count': len(works), 'decisions': decisions})
                continue
            records.append(wd)
            eligible.append(case)
            outcomes.append({'qid': qid, 'title': case['title'], 'status': 'eligible',
                'complete_input_sources': len(works[0].source_record_ids)})
        except ValueError as exc:
            outcomes.append({'qid': qid, 'title': case['title'], 'status': 'held', 'reason': str(exc)})
    save(output / 'case-outcomes.json', outcomes)
    if not eligible:
        return {'eligible': 0, 'held': len(outcomes), 'model_calls': 0}
    authority = output / 'authority.sqlite'
    with DatasetDB(authority) as db:
        db.ingest(records)
        rows = list(db.connection.execute("SELECT value,entity_id FROM external_ids WHERE source='wikidata' AND scope='work'"))
        ids = {r['value']: r['entity_id'] for r in rows}
    with sqlite3.connect(authority) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    cases = {'eligible_cases': [{**case, 'delivery_work_id': ids[case['wikidata_qid']]} for case in eligible],
        'frozen_selection_path': str(saved / 'frozen-selection.json'),
        'frozen_selection_sha256': sha(saved / 'frozen-selection.json')}
    cases_path = output / 'eligible-cases.json'
    save(cases_path, cases)
    authority_next = output.with_name(output.name + '-authority-next')
    build_next_database(source_database=authority, output_dir=authority_next)
    report = prepare(cases_path, output / 'batch', authority_database=authority_next / 'dataset.sqlite')
    save(output / 'selection-report.json', {'eligible': len(eligible), 'held': len(outcomes)-len(eligible),
        'authority_database_sha256': sha(authority), 'correction_packet_sha256': sha(correction),
        'model_calls': 0, 'batch': report})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(select(parser.parse_args().output), ensure_ascii=False))
