"""Opt-in explicit novel WORK + LN genre; never upgrades a work into a series."""
from copy import deepcopy
import re

from .light_novel_admission_v3 import (
    CONFLICT_TYPES, _require, _conditions, _source_entity, _fields, items,
    stored_title_closure, duplicate_title_key,
)

SCHEMA = 'light-novel-work-metadata-v3'
RULE = 'wikidata-explicit-novel-work-and-light-novel-genre-v1'


def verify_metadata(record):
    _require(record.get('schema_version') == SCHEMA and record.get('source') == 'wikidata',
        'work-v3 metadata contract differs')
    _require(record.get('admission') == {'accepted': True, 'reasons': [], 'version': RULE},
        'work-v3 admission held')
    qid = record['id']
    _require(isinstance(qid, str) and re.fullmatch(r'Q[1-9][0-9]*', qid), 'work-v3 identity differs')
    provenance = record['provenance'];_conditions(provenance)
    entity = _source_entity(provenance, qid)
    _require(provenance.get('url') == 'https://www.wikidata.org/wiki/' + qid
        and provenance.get('entity_revision') == entity.get('lastrevid'), 'work-v3 revision differs')
    proof = record['work_admission_proof']
    _require(proof.get('rule_version') == RULE and set(proof.get('type_authorities', {}))
        == {'Q8261', 'Q747381'}, 'work-v3 authority proof differs')
    required_parents = {'Q8261': {'Q87482979', 'Q3328821', 'Q676', 'Q116518880'},
        'Q747381': {'Q8261'}}
    for authority_id, parents in required_parents.items():
        pin = proof['type_authorities'][authority_id]
        _require(pin.get('conditions_path') == provenance['conditions_path']
            and pin.get('conditions_sha256') == provenance['conditions_sha256'],
            'work-v3 authority conditions differ')
        authority = _source_entity(pin, authority_id)
        _require(parents <= set(items(authority, 'P279')), 'work-v3 authority subclass claims differ')
    kinds = set(items(entity, 'P31') + items(entity, 'P7937'))
    _require('Q8261' in kinds and 'Q747381' in items(entity, 'P136'),
        'work-v3 explicit novel unit and LN genre conjunction absent')
    _require(not kinds & (CONFLICT_TYPES | {'Q1667921', 'Q104213567'})
        and not items(entity, 'P629'), 'work-v3 mixed series/adaptation/edition scope conflicts')
    _require(set(items(entity, 'P407')) == {'Q5287'}, 'work-v3 sole Japanese language unproved')
    expected = _fields(entity, provenance);expected['kind'] = 'work'
    for key, value in expected.items():
        _require(record.get(key) == value, 'work-v3 raw projection differs: ' + key)
    return entity


def prepare_metadata(qid, provenance, type_authorities):
    entity = _source_entity(provenance, qid)
    fields = _fields(entity, provenance);fields['kind'] = 'work'
    row = {'schema_version': SCHEMA, 'id': qid, 'source': 'wikidata', **fields,
        'provenance': deepcopy(provenance),
        'admission': {'accepted': True, 'reasons': [], 'version': RULE},
        'work_admission_proof': {'rule_version': RULE, 'type_authorities': deepcopy(type_authorities)}}
    verify_metadata(row)
    return row


def check_duplicate_titles(record, existing, *, limit=100000):
    """Hold ambiguous identities; preserve explicit cycle members as WORKs."""
    keys = {duplicate_title_key(t) for t in stored_title_closure(record)} - {''}
    for index, row in enumerate(existing):
        _require(index < limit, 'work-v3 duplicate witness scope exceeds finite limit')
        identity = row['entry_id']
        _require(identity != 'lns:wd:' + record['id'], 'work-v3 existing series identity requires crosswalk hold')
        if identity == 'lnw:wd:' + record['id']:
            continue
        if row.get('entry_kind', row.get('kind')) not in {'work', 'series', 'publisher_group'}:
            continue
        witness = row.get('record', row)
        _require(not keys & {duplicate_title_key(t) for t in stored_title_closure(witness)} - {''},
            'work-v3 existing title/alias requires crosswalk hold: ' + identity)
