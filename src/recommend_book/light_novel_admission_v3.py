"""Opt-in structured novel-series + LN genre admission; no IO beyond pinned reads."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata

SCHEMA = 'light-novel-metadata-v3'
RULE = 'wikidata-explicit-novel-series-and-light-novel-genre-v1'
CONFLICT_TYPES = {'Q5398426', 'Q639669', 'Q21198342', 'Q8274', 'Q202866',
    'Q4167410', 'Q4167836', 'Q11424', 'Q24856', 'Q11346433', 'Q136747113'}
RELATIONS = ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _payload(pin):
    raw = Path(pin['raw_path']).read_bytes()
    _require(re.fullmatch(r'[0-9a-f]{64}', pin['raw_sha256']) is not None
        and hashlib.sha256(raw).hexdigest() == pin['raw_sha256'], 'v3 pinned raw SHA differs')
    return json.loads(raw)


def items(entity, prop):
    return list(dict.fromkeys(s['mainsnak']['datavalue']['value']['id']
        for s in entity.get('claims', {}).get(prop, [])
        if s.get('rank') != 'deprecated' and s.get('mainsnak', {}).get('snaktype') == 'value'
        and isinstance(s.get('mainsnak', {}).get('datavalue', {}).get('value'), dict)
        and re.fullmatch(r'Q[1-9][0-9]*', s['mainsnak']['datavalue']['value'].get('id', ''))))


def _label(entity):
    labels = entity.get('labels', {})
    return next((labels[k]['value'] for k in ('ja', 'en') if k in labels), entity['id'])


def _conditions(provenance):
    document = _payload({'raw_path': provenance['conditions_path'],
        'raw_sha256': provenance['conditions_sha256']})
    policy = document.get('wikidata', {})
    _require(document.get('version') == 'light-novel-source-conditions-v1'
        and policy.get('license') == 'CC0' and policy.get('prose_excluded') is True
        and all(policy.get('usages', {}).get(k) is True for k in
            ('local_storage', 'metadata_display', 'metadata_recommendation')), 'v3 source conditions held')
    snapshots = [p for p in document.get('snapshots', [])
        if p.get('url') == 'https://www.wikidata.org/wiki/Wikidata:Licensing']
    _require(len(snapshots) == 1, 'v3 licensing snapshot absent or duplicated')
    pin = snapshots[0]
    raw = Path(pin['raw_path']).read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == pin['raw_sha256'], 'v3 licensing raw SHA differs')


def _source_entity(provenance, qid):
    entity = _payload(provenance).get('entities', {}).get(qid, {})
    _require(entity.get('id') == qid and 'missing' not in entity
        and entity.get('type', 'item') == 'item', 'v3 raw entity identity differs')
    return entity


def _gates(entity, proof, provenance):
    _require(proof.get('rule_version') == RULE and set(proof.get('type_authorities', {}))
        == {'Q1667921', 'Q747381'}, 'v3 admission proof version or authority set differs')
    for qid, required in (('Q1667921', {'Q277759', 'Q15980953'}), ('Q747381', {'Q8261'})):
        pin = proof['type_authorities'][qid]
        _require(pin.get('conditions_path') == provenance['conditions_path']
            and pin.get('conditions_sha256') == provenance['conditions_sha256'],
            'v3 authority source conditions differ')
        authority = _source_entity(pin, qid)
        _require(required <= set(items(authority, 'P279')), 'v3 authority subclass claims differ')
    types = set(items(entity, 'P31') + items(entity, 'P7937'))
    _require('Q1667921' in types and 'Q747381' in items(entity, 'P136'),
        'v3 explicit novel-series unit and LN genre conjunction absent')
    _require(not types & CONFLICT_TYPES and not items(entity, 'P629'),
        'v3 adaptation or edition scope conflicts')
    _require(set(items(entity, 'P407')) == {'Q5287'}, 'v3 sole Japanese original language unproved')


def _fields(entity, provenance):
    linked = provenance.get('linked_provenance', {})
    def named(prop):
        rows = []
        for qid in items(entity, prop):
            _require(bool(linked.get(qid)), 'v3 complete linked authority provenance required')
            rows.append({'id': qid, 'label': _label(_source_entity(linked[qid], qid))})
        return rows
    labels = entity.get('labels', {})
    dates = [s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('time')
        for s in entity.get('claims', {}).get('P577', []) if s.get('rank') != 'deprecated']
    return {'kind': 'series', 'title': _label(entity),
        'aliases': list(dict.fromkeys([v['value'] for v in labels.values()] +
            [v['value'] for group in entity.get('aliases', {}).values() for v in group])),
        'authors': named('P50'), 'genres': named('P136'), 'subjects': named('P921'),
        'publishers': named('P123'), 'original_languages': items(entity, 'P407'),
        'countries': items(entity, 'P495'), 'types': sorted(set(items(entity, 'P31') + items(entity, 'P7937'))),
        'publication_dates': [d for d in dates if d], 'sitelinks': entity.get('sitelinks', {}),
        'relations': {p: items(entity, p) for p in RELATIONS}}


def verify_metadata(record):
    """Replay the entire v3 contract. Existing v1/v2 callers stay separate."""
    _require(record.get('schema_version') == SCHEMA and record.get('source') == 'wikidata',
        'v3 metadata contract differs')
    _require(record.get('admission') == {'accepted': True, 'reasons': [], 'version': RULE},
        'v3 metadata admission held')
    qid = record['id']
    _require(isinstance(qid, str) and re.fullmatch(r'Q[1-9][0-9]*', qid), 'v3 typed identity differs')
    provenance = record['provenance']
    _conditions(provenance)
    entity = _source_entity(provenance, qid)
    _require(provenance.get('url') == 'https://www.wikidata.org/wiki/' + qid
        and provenance.get('entity_revision') == entity.get('lastrevid'), 'v3 source revision differs')
    _gates(entity, record['series_admission_proof'], provenance)
    for key, expected in _fields(entity, provenance).items():
        _require(record.get(key) == expected, 'v3 raw metadata projection differs: ' + key)
    return entity


def prepare_metadata(qid, provenance, type_authorities):
    """Build a fresh v3 row from exact pins; never promote a candidate in place."""
    entity = _source_entity(provenance, qid)
    record = {'schema_version': SCHEMA, 'source': 'wikidata', 'id': qid,
        **_fields(entity, provenance), 'provenance': deepcopy(provenance),
        'admission': {'accepted': True, 'reasons': [], 'version': RULE},
        'series_admission_proof': {'rule_version': RULE, 'type_authorities': deepcopy(type_authorities)}}
    verify_metadata(record)
    return record


def metadata_artifact_pins(record):
    """Complete transitive structured source closure, separate from model prose."""
    verify_metadata(record)
    provenance = record['provenance']
    candidates = [provenance, *provenance.get('linked_provenance', {}).values(),
        *record['series_admission_proof']['type_authorities'].values()]
    candidates.append({'raw_path': provenance['conditions_path'],
        'raw_sha256': provenance['conditions_sha256']})
    conditions = _payload(candidates[-1])
    candidates.extend(conditions.get('snapshots', []))
    pins = {}
    for pin in candidates:
        path = str(Path(pin['raw_path']).resolve())
        expected = pin['raw_sha256']
        _require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected,
            'v3 structured artifact SHA differs')
        _require(path not in pins or pins[path] == expected, 'v3 conflicting structured artifact pins')
        pins[path] = expected
    return pins


def stored_title_closure(record):
    """Conservative saved witnesses, including title-less NDL anchor records.

    Non-explicit NDL projection uses exact source titles or their ISBD main
    splits, and source title readings. No volume suffix is removed here.
    """
    values = []
    def add(value):
        if isinstance(value, str) and value:
            values.append(value)
    def add_list(sequence):
        if isinstance(sequence, list):
            for value in sequence:
                add(value.get('value') if isinstance(value, dict) else value)
    add(record.get('title'));add_list(record.get('aliases', []))
    base = record.get('anchor_edition_record', record)
    if isinstance(base, dict):
        add(base.get('title'));add_list(base.get('aliases', []))
        source = base.get('source_record', {})
        if isinstance(source, dict):
            source_titles = []
            if isinstance(source.get('title'), str):
                source_titles.append(source['title'])
            for row in source.get('titles', []):
                if isinstance(row, dict) and isinstance(row.get('value'), str):
                    source_titles.append(row['value'])
            for value in source_titles:
                add(value)
                add(re.split(r' (?:=|:) ', value, maxsplit=1)[0])
            add_list(source.get('aliases', []))
            extra = source.get('extra', {})
            if isinstance(extra, dict):
                add_list(extra.get('title_readings', []));add_list(extra.get('aliases', []))
    return list(dict.fromkeys(values))


def duplicate_title_key(text):
    """Catalogue normalization plus punctuation removal for conflict holds only."""
    text = ''.join(unicodedata.normalize('NFKC', text).casefold().split())
    text = ''.join(chr(ord(c) + 0x60) if '\u3041' <= c <= '\u3096' else c for c in text)
    return ''.join(c for c in text if not unicodedata.category(c).startswith('P'))


def check_duplicate_titles(record, existing, *, limit=100000):
    """Finite insertion-time conflict screen; never assert an identity crosswalk."""
    names = {duplicate_title_key(t) for t in stored_title_closure(record)} - {''}
    for index, row in enumerate(existing):
        _require(index < limit, 'v3 duplicate-title scope exceeds finite limit')
        _require(row['entry_id'] != 'lnw:wd:' + record['id'],
            'v3 existing Wikidata work identity requires crosswalk hold')
        if row['entry_id'] == 'lns:wd:' + record['id']:
            continue
        if row.get('entry_kind', row.get('kind')) not in {'series', 'publisher_group'}:
            continue
        witness = row.get('record', row)
        if names & {duplicate_title_key(t) for t in stored_title_closure(witness)} - {''}:
            raise ValueError('v3 existing series title/alias requires crosswalk hold: ' + row['entry_id'])
