"""Prepare one authority-linked editorial synopsis in a new local namespace.

No network or model calls. The existing source DB is read-only, and the root
rights registry and historical inputs are never changed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import editorial_work_record, ingest_editorial_description
from recommend_book.models import SourceRecord
from recommend_book.next_database import build_next_database, get_next_inputs
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/autonomous-next-v3'
REFERENCE = BASE / 'evaluation/phase9-standardebooks-reference-v1'
SOURCE_DB = BASE / 'database/world-subject-sealed-v1/dataset.sqlite'
DEFAULT_OUTPUT = BASE / 'preparation/phase9-standardebooks-pilot-v1'
POLICY_ID = 'standardebooks-editorial-cc0'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def prepare(output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Existing preparation is immutable; select a new namespace')
    root_registry_sha = sha(ROOT / 'config/sources.json')
    audit = json.loads((REFERENCE / 'description-identity-audit.json').read_bytes())
    cases = [v for v in audit['cases'] if v['record_key'] == 'age-of-innocence']
    if len(cases) != 1 or cases[0]['identity_decision'] != 'authority_bridge_supported_work_level_description_only':
        raise ValueError('One exact work-level authority bridge is required')
    case = cases[0]
    with sqlite3.connect(SOURCE_DB.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
        ids = {(row[0], row[1]) for row in db.execute(
            'SELECT source,value FROM external_ids WHERE entity_id=? AND scope=?',
            (case['delivery_work_id'], 'work'))}
        if not {('wikidata', 'Q1045464'), ('openlibrary', '/works/OL98491W')} <= ids:
            raise ValueError('Current candidate authority identifiers differ')
        rows = list(db.execute('SELECT r.record_json FROM source_heads h JOIN source_records r ON r.version_id=h.version_id '
                               'WHERE h.source=? AND h.source_id=?', ('wikidata', 'Q1045464')))
    if len(rows) != 1:
        raise ValueError('Wikidata source head is not unique')
    original = SourceRecord.model_validate_json(rows[0][0])
    if sha(original.raw_path) != original.raw_sha256 or original.raw_sha256 != case['saved_wikidata_expected_sha256']:
        raise ValueError('Original Wikidata source bytes differ')
    # New normalized source version; no old version ID or permission is rebound.
    wikidata = original.model_copy(update={'evidence': []}, deep=True)
    retrieved = json.loads((REFERENCE / 'retrieval-manifest.json').read_bytes())
    opf = next(v for v in retrieved if v['name'] == 'age-of-innocence-opf')
    if sha(ROOT / opf['path']) != opf['sha256']:
        raise ValueError('OPF raw receipt differs')
    edition = ingest_editorial_description(ROOT / opf['path'], source_url=opf['url'],
                                           retrieved_at=opf['retrieved_at'], wikidata_record=wikidata)
    editorial = editorial_work_record(edition)
    if len(editorial.evidence) != 1 or editorial.evidence[0].kind != 'synopsis':
        raise ValueError('Only the source-authored editorial synopsis is selected')
    output.mkdir(parents=True)
    source = output / 'sources/source-bindings.sqlite'
    source.parent.mkdir()
    with DatasetDB(source) as db:
        ingested = db.ingest([wikidata, edition, editorial])
        works = [w for w in db.work_inputs() if w.expression_id is None]
    with sqlite3.connect(source) as db:
        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        db.execute('PRAGMA journal_mode=DELETE')
    if len(works) != 1 or len(works[0].evidence) != 1:
        raise ValueError('Sources must resolve to exactly one work with one synopsis')
    registry_data = deepcopy(json.loads((ROOT / 'config/sources.json').read_bytes()))
    registry_data['rights_policies'].append({
        'policy_id': POLICY_ID, 'source': 'standardebooks', 'provider_id': 'standardebooks',
        'fields': ['bibliographic', 'synopsis'], 'license': 'CC0-1.0',
        'usages': dict.fromkeys(USAGES, 'allowed'), 'verified_at': '2026-10-16',
        'evidence_urls': ['https://standardebooks.org/about', 'https://standardebooks.org/manual/1.9.1/9-metadata'],
        'scope': 'Standard Ebooks-authored OPF title/author facts and editorial description only. '
                 'Exact underlying work authority must be reproduced. No ebook body, covers, third-party LCSH, '
                 'genre gold, collection transfer, translation style, or original-prose style permission is inferred.'})
    registry_path = output / 'source-registry.json'
    save(registry_path, registry_data)
    registry = RightsRegistry.from_file(registry_path)
    official = json.loads((BASE / 'rights/phase7-world-pilot-v1/official-conditions.json').read_bytes())
    official = {'wikidata-cc0': official['wikidata-cc0'], POLICY_ID: [
        {'source_url': row['url'], 'raw_path': str((ROOT / row['path']).resolve()),
         'raw_sha256': row['sha256'], 'verified_at': row['retrieved_at']}
        for row in retrieved if row['name'] in ('about', 'metadata-manual')]}
    ordered = output / 'ordered-inputs.jsonl'
    ordered.write_text(''.join(w.model_dump_json() + '\n' for w in works), encoding='utf-8', newline='\n')
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path),
              'registry_file_sha256': sha(registry_path),
              'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': official, 'inputs': {}, 'revoked_input_hashes': [],
              'derived_artifacts': {}, 'execution_recipes': {}}
    for line, work in enumerate(works, 1):
        refs = {ref: source_binding(source, ref) for ref in work.source_record_ids}
        ledger['inputs'][model_hash(work)] = build_input_entry(work, refs,
            input_ref={'path': str(ordered), 'sha256': sha(ordered), 'line_number': line})
    ledger_path = output / 'usage-ledger-inputs.json'
    save(ledger_path, ledger)
    guard = UsageGuard(ledger_path, expected_ledger_sha256=sha(ledger_path))
    receipts = {usage: [guard.check_input(w, usage=usage).model_dump(mode='json') for w in works] for usage in USAGES}
    save(output / 'admission-receipts.json', receipts)
    failures = [dict(usage=usage, reasons=r['reasons']) for usage, rows in receipts.items() for r in rows if not r['allowed']]
    if failures:
        save(output / 'admission-failures.json', failures)
        raise ValueError('Source admission held; inspect recorded reasons')
    next_dir = output / 'next-source'
    build_next_database(source_database=source, output_dir=next_dir)
    groups = get_next_inputs(next_dir / 'dataset.sqlite')
    if groups['classification'] != works:
        raise ValueError('Actual next-DB classification input differs from selected source input')
    for kind in ('classification', 'bibliographic', 'aliases'):
        path = next_dir / (kind + '-inputs.jsonl')
        for number, work in enumerate(groups[kind], 1):
            refs = {ref: source_binding(next_dir / 'dataset.sqlite', ref) for ref in work.source_record_ids}
            ledger['inputs'][model_hash(work)] = build_input_entry(work, refs,
                input_ref={'path': str(path), 'sha256': sha(path), 'line_number': number})
    packets_path = next_dir / 'derivations.jsonl'
    for number, line in enumerate(packets_path.read_text(encoding='utf-8').splitlines(), 1):
        packet = json.loads(line)
        ledger['derived_artifacts'][model_hash(packet['payload'])] = {
            'kind': packet['kind'], 'value_sha256': packet['value_sha256'],
            'originating_input_hashes': packet['originating_input_hashes'], 'raw_path': str(packets_path),
            'raw_sha256': sha(packets_path), 'format': 'jsonl', 'line_number': number,
            'locator': '/payload', 'recipe': packet['recipe']}
    save(output / 'usage-ledger-projections.json', ledger)
    if sha(ROOT / 'config/sources.json') != root_registry_sha:
        raise ValueError('Protected root registry changed')
    result = {'version': 'standardebooks-one-work-pilot-preparation-v1', 'stage': 'prepared_not_started',
              'source_records_ingested': ingested, 'new_editorial_sources': 1,
              'input_work_id': works[0].work_id, 'existing_delivery_work_id': case['delivery_work_id'],
              'same_work_not_new_novel': True, 'classification_inputs': len(works),
              'input_sha256': sha(ordered), 'next_source_database': str(next_dir / 'dataset.sqlite'),
              'next_database_sha256': sha(next_dir / 'dataset.sqlite'), 'root_registry_unchanged': True,
              'model_calls': 0, 'private_history_read': False,
              'independent_genre_gold_n': 0, 'source_opf_sha256': editorial.raw_sha256}
    save(output / 'preparation-report.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    print(json.dumps(prepare(parser.parse_args().output), ensure_ascii=True))
