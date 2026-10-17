"""One audited identity quarantine; original authority claims remain immutable.

The packet is trusted only through an externally supplied SHA. It records a
collection mismatch, never an equivalence between two Roger Ackroyd OL works.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord

SCHEMA = 'authority-identity-correction-v1'
RECIPE_MODULE = 'recommend_book/authority_identity_correction.py'
QUARANTINE = '/works/OL471547W'
CONTENTS = ('Murder of Roger Ackroyd', 'Les quatre', 'Le train bleu',
            'Les sept cadrans', 'Le crime est notre affaire', "L'affaire Protheroe")


def _sha(payload):
    return hashlib.sha256(payload).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _read(path, expected):
    path = Path(path)
    _require(path.is_absolute(), 'correction artifact path must be absolute')
    raw = path.read_bytes()
    _require(_sha(raw) == expected, 'correction artifact SHA differs')
    return json.loads(raw)


def project_identity_correction(original: SourceRecord, correction_path, expected_sha256: str) -> SourceRecord:
    """Derive a fresh record using the externally pinned audit packet."""
    path = Path(correction_path).resolve()
    packet = _read(path, expected_sha256)
    _require(packet['schema'] == SCHEMA and packet['quarantined_identifier'] == QUARANTINE,
             'unsupported identity quarantine')
    saved = SourceRecord.model_validate(packet['original_record'])
    _require(saved.model_dump(mode='json') == original.model_dump(mode='json'), 'original record differs')
    _require(_sha(canonical_json(saved.model_dump(mode='json')).encode()) == packet['original_record_sha256'],
             'original normalized record SHA differs')
    _require((saved.source, saved.provider_id, saved.record_kind, saved.rights_policy_id, saved.source_id, saved.source_url)
             == ('wikidata', 'wikidata', 'work', 'wikidata-cc0', 'Q224078', 'https://www.wikidata.org/wiki/Q224078'),
             'correction source scope differs')
    _require(not saved.evidence and 'identity_correction' not in saved.extra, 'original is not an unprojected metadata record')
    binding = packet['wikidata_raw']
    _require(binding['path'] == saved.raw_path and binding['sha256'] == saved.raw_sha256
             and binding['entity_id'] == saved.source_id, 'original raw binding differs')
    entity = _read(saved.raw_path, saved.raw_sha256)['entities'][saved.source_id]
    _require(entity.get('id') == saved.source_id and entity.get('claims') == saved.extra.get('claims'),
             'original raw entity/claims differ')
    claims = entity['claims']
    active = lambda prop: [s for s in claims.get(prop, []) if s.get('rank') != 'deprecated']
    forms = {s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
             for prop in ('P31', 'P7937') for s in active(prop)}
    conflicting_units = {'Q3331189', 'Q53731850', 'Q49084', 'Q277759', 'Q1667921', 'Q1279564', 'Q105420'}
    _require('Q8261' in forms and not active('P629') and not forms & conflicting_units,
             'direct novel/non-edition proof absent or conflicting bibliographic unit')
    match = re.fullmatch(r'/entities/Q224078/claims/P648/(0|[1-9][0-9]*)/mainsnak/datavalue/value', binding['claim_locator'])
    _require(match is not None, 'exact P648 locator absent')
    index = int(match[1])
    _require(index < len(claims.get('P648', [])), 'P648 locator out of range')
    statement = claims['P648'][index]
    snak = statement.get('mainsnak', {})
    _require(statement.get('rank') != 'deprecated' and snak.get('property') == 'P648'
             and snak.get('snaktype') == 'value' and snak.get('datavalue', {}).get('value') == 'OL471547W',
             'exact nondeprecated P648 claim differs')
    proof = packet['aggregate_proof']
    _require(proof['official_url'] == 'https://openlibrary.org/works/OL471547W.json', 'official collection URL differs')
    aggregate = _read(proof['raw_path'], proof['raw_sha256'])
    _require(aggregate.get('key') == QUARANTINE and aggregate.get('type', {}).get('key') == '/type/work'
             and aggregate.get('title') == 'Agatha Christie, tome 2'
             and aggregate.get('subtitle') == 'Les Années 1926-1930', 'collection identity/unit differs')
    description = aggregate.get('description', {})
    description = description.get('value', '') if isinstance(description, dict) else ''
    lines = description.splitlines()
    _require(len(lines) == 7 and lines[0] == 'Contains:', 'explicit collection contents absent')
    expected = [f'[{CONTENTS[0]}](https://openlibrary.org/works/OL472086W/The_Murder_of_Roger_Ackroyd)',
                *[title + ' --' for title in CONTENTS[1:-1]], CONTENTS[-1] + '.']
    _require(lines[1:] == expected, 'six distinct collection titles not reproduced')
    target = lambda item: (item.source, item.value, item.scope) == ('openlibrary', QUARANTINE, 'work')
    _require(any(target(item) for item in [*saved.identifiers, *saved.work_refs]), 'quarantined identity absent')
    _require(any((item.source, item.value, item.scope) == ('openlibrary', '/works/OL471932W', 'work')
                 for item in [*saved.identifiers, *saved.work_refs]), 'retained work identity absent')
    projected = saved.model_copy(deep=True)
    projected.identifiers = [item for item in projected.identifiers if not target(item)]
    projected.work_refs = [item for item in projected.work_refs if not target(item)]
    projected.extra['identity_correction'] = {
        'packet_path': str(path), 'packet_sha256': expected_sha256,
        'recipe': {'module': RECIPE_MODULE, 'sha256': _sha(Path(__file__).read_bytes())}}
    return projected


def verify_identity_correction(record: SourceRecord) -> list[str]:
    """Reproduce every output field; return proof hashes for admission binding."""
    receipt = record.extra['identity_correction']
    _require(receipt['recipe'] == {'module': RECIPE_MODULE, 'sha256': _sha(Path(__file__).read_bytes())},
             'identity correction recipe differs')
    packet = _read(receipt['packet_path'], receipt['packet_sha256'])
    original = SourceRecord.model_validate(packet['original_record'])
    expected = project_identity_correction(original, receipt['packet_path'], receipt['packet_sha256'])
    _require(expected.model_dump(mode='json') == record.model_dump(mode='json'), 'identity correction output differs')
    return [receipt['packet_sha256'], receipt['recipe']['sha256'], packet['original_record_sha256'],
            packet['wikidata_raw']['sha256'], packet['aggregate_proof']['raw_sha256']]
