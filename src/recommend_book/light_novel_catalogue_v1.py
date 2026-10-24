"""Separate, raw-backed Japanese light-novel catalogue with typed identities.

Metadata never grants permission over Wikipedia prose. Every public operation
revalidates its local evidence; request-local caches only avoid duplicate reads.
Series features are verified by their own export verifier and never copied to a
volume. Read state and the original scale catalogue remain independent.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata

READ_STATUSES = {'unknown', 'unread', 'reading', 'read'}
AXES = ('genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere')
CONFLICT_TYPES = {'Q5398426', 'Q639669', 'Q21198342', 'Q8274', 'Q202866',
                  'Q4167410', 'Q4167836', 'Q11424', 'Q24856', 'Q11346433', 'Q136747113'}
RELATIONS = ('P179', 'P527', 'P629', 'P155', 'P156', 'P144')


def normalize_search(value):
    text = unicodedata.normalize('NFKC', str(value)).casefold()
    return ''.join(chr(ord(c) + 0x60) if '\u3041' <= c <= '\u3096' else c
                   for c in text if not c.isspace())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _resolved_path(path, cache):
    """Canonicalize each spelling once per operation, without caching evidence."""
    key = ('resolved_path', str(path))
    if key not in cache:
        cache[key] = Path(path).resolve()
    return cache[key]


def _qid(value):
    if not isinstance(value, str) or not re.fullmatch(r'Q[1-9][0-9]*', value):
        raise ValueError('invalid Wikidata QID')
    return value


def _entry(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:ln[sw]:wd:Q[1-9][0-9]*|ln[se]:ndl:[A-Za-z0-9._-]+)', value):
        raise ValueError('a typed light-novel entry ID is required')
    return value


def _items(entity, prop):
    result = []
    for st in entity.get('claims', {}).get(prop, []):
        snak = st.get('mainsnak', {})
        value = snak.get('datavalue', {}).get('value')
        if st.get('rank') != 'deprecated' and snak.get('snaktype') == 'value' and isinstance(value, dict) and re.fullmatch(r'Q[1-9][0-9]*', value.get('id', '')):
            result.append(value['id'])
    return list(dict.fromkeys(result))


def _safe(value):
    """Public evidence allowlist: internal artifact paths never cross the API."""
    allowed = {'source', 'source_id', 'source_url', 'url', 'title', 'label', 'qid', 'license', 'license_id', 'license_url', 'kind',
               'attribution', 'credit', 'revision', 'entity_revision', 'scope', 'evidence_id',
               'locator', 'quote', 'summary', 'directness', 'display_allowed', 'creator',
               'rights_policy_id', 'retrieved_at', 'modified_at', 'provider', 'record_id'}
    result = {k: v for k, v in value.items() if k in allowed and isinstance(v, (str, int, bool, type(None)))}
    if isinstance(value.get('attribution'), dict):
        keys = {'revision_id', 'page_id', 'title', 'article_title', 'source_url', 'history_url', 'permalink',
                'license_url', 'license', 'changes', 'credit', 'provider', 'revision_url', 'authors_url'}
        result['attribution'] = {k: v for k, v in value['attribution'].items()
                                 if k in keys and isinstance(v, (str, int, bool, type(None)))}
    return result


def _content_source(source):
    """Publish permissions only for the source contract the verifier replays."""
    result = _safe(source)
    attribution = source.get('attribution', {})
    allowed = (source.get('rights_policy_id') == 'light-novel-series-wikipedia-screened-v1'
               and source.get('license') == 'CC-BY-SA-4.0')
    result.update(display_allowed=allowed, quote_allowed=False,
                  evidence_ids=[source['evidence_id']] if isinstance(source.get('evidence_id'), str) else [],
                  revision=attribution.get('revision_id'), permalink=attribution.get('permalink'),
                  history_url=attribution.get('history_url'), changed_from_source=attribution.get('changes'))
    if allowed:
        result.update(output_license='CC-BY-SA-4.0', output_license_url='https://creativecommons.org/licenses/by-sa/4.0/')
    return result


class LightNovelCatalogue:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS entries(
                    entry_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS read_status(
                    entry_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    status TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS features(
                    entry_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    manifest_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ingestion_failures(
                    source_id TEXT, reason TEXT, recorded_at TEXT);
                CREATE TABLE IF NOT EXISTS edition_series_memberships(
                    edition_id TEXT PRIMARY KEY REFERENCES entries(entry_id),
                    series_id TEXT NOT NULL REFERENCES entries(entry_id),
                    proof_json TEXT NOT NULL,proof_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS edition_book_confirmations(
                    edition_id TEXT NOT NULL REFERENCES entries(entry_id),provider TEXT NOT NULL,
                    proof_json TEXT NOT NULL,proof_sha256 TEXT NOT NULL,
                    PRIMARY KEY(edition_id,provider));
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _artifact(self, path, expected, cache):
        key = (str(_resolved_path(path, cache)), expected)
        if key not in cache:
            raw = Path(path).read_bytes()
            if not isinstance(expected, str) or hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('source artifact SHA mismatch')
            cache[key] = raw
        return cache[key]

    def _payload(self, path, expected, cache):
        key = ('json', str(_resolved_path(path, cache)), expected)
        if key not in cache:
            cache[key] = json.loads(self._artifact(path, expected, cache))
        return cache[key]

    def _conditions(self, provenance, source, cache):
        document = self._payload(provenance['conditions_path'], provenance['conditions_sha256'], cache)
        if document.get('version') != 'light-novel-source-conditions-v1':
            raise ValueError('unsupported source conditions')
        policy = document.get(source, {})
        if not all(policy.get('usages', {}).get(u) is True for u in ('local_storage', 'metadata_display', 'metadata_recommendation')):
            raise ValueError('metadata usage held')
        wanted = {'https://www.wikidata.org/wiki/Wikidata:Licensing'} if source == 'wikidata' else {'https://www.ndl.go.jp/data/data_service/quickguide', 'https://ndlsearch.ndl.go.jp/help/api'}
        snapshots = {s['url']: s for s in document.get('snapshots', [])}
        if not wanted <= snapshots.keys():
            raise ValueError('source conditions snapshots absent')
        for url in wanted:
            proof = snapshots[url]
            self._artifact(proof['raw_path'], proof['raw_sha256'], cache)
        if source == 'wikidata' and (policy.get('license') != 'CC0' or policy.get('prose_excluded') is not True):
            raise ValueError('structured Wikidata metadata policy differs')
        if source == 'ndl' and policy.get('provider') != 'iss-ndl-opac':
            raise ValueError('NDL-created metadata policy absent')
        return policy

    def _project(self, record, cache):
        if record.get('schema_version') == 'light-novel-ndl-series-v1':
            return self._project_ndl_series(record, cache)
        if record.get('schema_version') == 'light-novel-edition-v1':
            return self._project_edition(record, cache)
        if record.get('schema_version') not in {'light-novel-metadata-v1', 'light-novel-metadata-v2'} or record.get('source') != 'wikidata':
            raise ValueError('unsupported metadata contract')
        if record.get('admission', {}).get('accepted') is not True or record['admission'].get('reasons'):
            raise ValueError('metadata admission held')
        qid = _qid(record['id'])
        proof = record['provenance']
        self._conditions(proof, 'wikidata', cache)
        payload = self._payload(proof['raw_path'], proof['raw_sha256'], cache)
        entity = payload.get('entities', {}).get(qid, {})
        if entity.get('id') != qid or 'missing' in entity or entity.get('type', 'item') != 'item':
            raise ValueError('raw identity differs')
        if proof.get('url') != f'https://www.wikidata.org/wiki/{qid}' or proof.get('entity_revision') != entity.get('lastrevid'):
            raise ValueError('source URL/revision differs')
        types = set(_items(entity, 'P31') + _items(entity, 'P7937'))
        kind = 'series' if 'Q104213567' in types else 'work' if 'Q747381' in types else None
        if kind is None or record.get('kind') != kind or types & CONFLICT_TYPES or _items(entity, 'P629'):
            raise ValueError('light novel scope absent or conflicting')
        languages = _items(entity, 'P407')
        identity_basis = 'explicit_original_language'
        if record.get('original_languages') != languages:
            raise ValueError('Japanese original language unproved')
        if set(languages) != {'Q5287'}:
            if languages or record.get('schema_version') != 'light-novel-metadata-v2':
                raise ValueError('Japanese original language unproved')
            resolution = record.get('original_language_resolution', {})
            if resolution.get('method') != 'wikidata_description_explicit_japanese_literary_original_v1':
                raise ValueError('Japanese literary identity evidence absent')
            resolved = self._payload(resolution['raw_path'], resolution['raw_sha256'], cache).get('entities', {}).get(qid, {})
            text = resolved.get('descriptions', {}).get('en', {}).get('value', '')
            forms = r'(?:light novel series|novel series)' if kind == 'series' else r'light novel'
            if (resolved.get('id') != qid or resolution.get('source_url') != proof['url']
                    or resolution.get('entity_revision') != resolved.get('lastrevid')
                    or resolution.get('locator') not in {'descriptions.en', 'descriptions.en.value', f'entities.{qid}.descriptions.en.value'}
                    or resolution.get('text') != text
                    or not re.match(r'^(?:a\s+)?Japanese\s+' + forms + r'\b', text, re.IGNORECASE)):
                raise ValueError('Japanese literary description not reproduced by raw')
            identity_basis = 'explicit_wikidata_description'
        labels = entity.get('labels', {})
        titles = list(dict.fromkeys([v['value'] for v in labels.values()] +
                     [v['value'] for group in entity.get('aliases', {}).values() for v in group]))
        title = next((labels[x]['value'] for x in ('ja', 'en') if x in labels), qid)
        if record.get('title') != title or record.get('aliases', []) != titles:
            raise ValueError('metadata title/aliases not reproduced by raw')

        def named(prop, field):
            values = []
            for ref in _items(entity, prop):
                linked = payload.get('entities', {}).get(ref, {'id': ref})
                origin = proof.get('linked_provenance', {}).get(ref)
                if origin:
                    linked = self._payload(origin['raw_path'], origin['raw_sha256'], cache).get('entities', {}).get(ref, {})
                    if linked.get('id') != ref:
                        raise ValueError('linked authority identity differs')
                labels = linked.get('labels', {})
                name = next((labels[x]['value'] for x in ('ja', 'en') if x in labels), ref)
                values.append({'id': ref, 'label': name})
            if record.get(field, []) != values:
                raise ValueError('linked metadata not reproduced by raw')
            return [{'qid': v['id'], 'name': v['label']} if field == 'authors' else
                    {'qid': v['id'], 'label': v['label']} for v in values]

        authors = named('P50', 'authors')
        genres = named('P136', 'genres')
        subjects = named('P921', 'subjects')
        publishers = named('P123', 'publishers')
        dates = [s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('time')
                 for s in entity.get('claims', {}).get('P577', []) if s.get('rank') != 'deprecated']
        dates = [d for d in dates if d]
        if record.get('publication_dates', []) != dates or record.get('sitelinks', {}) != entity.get('sitelinks', {}):
            raise ValueError('publication dates/sitelinks not reproduced')
        relations = {p: _items(entity, p) for p in RELATIONS}
        if any(record.get('relations', {}).get(p, []) != refs for p, refs in relations.items()):
            raise ValueError('relations not reproduced by raw')
        years = [int(m.group(1)) for d in dates if (m := re.match(r'^\+(\d+)-', d))]
        entry_id = ('lns:wd:' if kind == 'series' else 'lnw:wd:') + qid
        return {'entry_id': entry_id, 'entry_kind': kind, 'work_id': entry_id, 'qid': qid,
                'family': 'light_novel', 'title': title, 'aliases': [v for v in titles if v != title],
                'authors': authors, 'genres': genres, 'subjects': subjects, 'publishers': publishers,
                'original_language': 'ja' if languages else None,
                'original_language_status': 'explicit_source_assertion' if languages else 'unknown',
                'japanese_identity_basis': identity_basis, 'year': min(years) if years else None,
                'publication_dates': dates, 'sitelinks': entity.get('sitelinks', {}),
                'source_url': proof['url'], 'entity_revision': proof.get('entity_revision'),
                'relations': relations, 'read_status': 'unknown', 'recommendable': bool(genres),
                'llm_features': [], 'native_features': [], 'strict72_features': [],
                'content_sources': [], 'source_attributions': [{
                    'source': 'wikidata', 'source_url': proof['url'], 'license': 'CC0',
                    'scope': kind, 'entity_revision': proof.get('entity_revision')}],
                'classification_scope': kind, 'content_features_adopted': False,
                'recommendation_mode': 'source_similarity' if genres else 'insufficient_evidence'}

    def _project_edition(self, record, cache):
        """Reproduce NDL-created editions, leaving story/work relations unknown."""
        import xml.etree.ElementTree as ET
        from .ingestion.core import RawArtifact
        from .ingestion.ndl import NDLAdapter, SRU
        if record.get('source') != 'ndl' or record.get('provider') != 'iss-ndl-opac':
            raise ValueError('only NDL-created bibliographic editions are admitted')
        source_id = record['id']
        if not re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', source_id):
            raise ValueError('NDL-created repository identity absent')
        if record.get('original_work_id') or record.get('series_entry_id'):
            raise ValueError('NDL edition to story relation requires separate source review')
        proof = record['provenance']
        self._conditions(proof, 'ndl', cache)
        source_record = record['source_record']
        raw = self._artifact(proof['raw_path'], proof['raw_sha256'], cache)
        key = ('ndl_records', proof['raw_path'], proof['raw_sha256'], source_record['retrieved_at'])
        node_key = ('ndl_nodes', proof['raw_path'], proof['raw_sha256'])
        if node_key not in cache:
            try:
                xml = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise ValueError('NDL raw XML is malformed') from exc
            nodes = xml.findall(f'.//{{{SRU}}}recordData')
            if not nodes:
                nodes = [xml]
            indexed = {}
            for index, node in enumerate(nodes, 1):
                if len(node) == 0 and node.text:
                    try:
                        node = ET.fromstring(node.text)
                    except ET.ParseError as exc:
                        raise ValueError('NDL record XML is malformed') from exc
                identities = {v.split('#', 1)[0].rsplit('/', 1)[-1] for child in node.iter()
                              for v in child.attrib.values() if v.startswith('https://ndlsearch.ndl.go.jp/books/')}
                for identity in identities:
                    if identity in indexed:
                        raise ValueError('duplicate NDL identity within raw response')
                    indexed[identity] = (node, index)
            cache[node_key] = indexed
        if key not in cache:
            cache[key] = {}
        if source_id not in cache[key] and source_id in cache[node_key]:
            node, index = cache[node_key][source_id]
            adapter = NDLAdapter(None, approved_providers={'iss-ndl-opac'})
            artifact = RawArtifact(proof['raw_sha256'], proof['raw_path'], len(raw), source_record['retrieved_at'], 'https://ndlsearch.ndl.go.jp/api/sru')
            item = adapter._normalize(node, artifact, 'iss-ndl-opac', locator=f'records[{index}]/recordData')
            cache[key][source_id] = item.model_dump(mode='json')
        actual = cache[key].get(source_id)
        if not actual or actual != source_record:
            raise ValueError('NDL source record not reproduced by raw')
        wanted_url = f'https://ndlsearch.ndl.go.jp/books/{source_id}'
        if proof.get('url') != wanted_url or record.get('source_url') != wanted_url or actual['source_url'] != wanted_url:
            raise ValueError('NDL record URL identity differs')
        fields = {'title': actual['titles'][0]['value'] if actual['titles'] else '',
                  'volume': actual['volume'], 'authors': [c['name'] for c in actual['contributors'] if c['role'] == 'author'],
                  'publisher': actual['publisher'], 'date': actual['publication_year'],
                  'isbn': [i['value'] for i in actual['identifiers'] if i['source'] == 'isbn'],
                  'languages': [actual['language']] if actual['language'] else [], 'label': actual['series'],
                  'ndc': actual['extra'].get('ndc_codes', [])}
        if any(record.get(k) != v for k, v in fields.items()):
            raise ValueError('NDL edition fields differ from raw')
        if not fields['title']:
            raise ValueError('NDL edition title absent')
        if record.get('admission', {}).get('light_novel_confirmed') is not False:
            raise ValueError('label alone cannot confirm a light novel')
        label_candidate = any(actual['series'] and e.get('query_term', '') and e['query_term'] in actual['series']
                              for e in record.get('label_evidence', []))
        if record.get('label_candidate') != label_candidate:
            raise ValueError('label candidate not reproduced by returned series field')
        entry_id = 'lne:ndl:' + source_id
        language = {'jpn': 'ja', 'eng': 'en'}.get(actual['language'], actual['language'])
        return {'entry_id': entry_id, 'entry_kind': 'edition', 'work_id': entry_id, 'qid': None,
                'family': 'light_novel', 'title': fields['title'], 'aliases': actual['extra'].get('title_readings', []),
                'authors': [{'qid': None, 'name': name} for name in fields['authors']], 'genres': [], 'subjects': [],
                'publishers': [{'qid': None, 'label': fields['publisher']}] if fields['publisher'] else [],
                'original_language': None, 'language': language, 'year': fields['date'], 'volume': fields['volume'],
                'isbn': fields['isbn'], 'publication_label': fields['label'], 'label_candidate': label_candidate,
                'light_novel_confirmed': False, 'admission_status': 'bibliographic_candidate_only',
                'relation_resolution': 'original_work_and_story_series_unresolved',
                'source_url': wanted_url, 'relations': {p: [] for p in RELATIONS}, 'read_status': 'unknown',
                'recommendable': False, 'llm_features': [], 'native_features': [], 'strict72_features': [],
                'content_sources': [], 'source_attributions': [{'source': 'ndl', 'provider': 'iss-ndl-opac',
                    'source_url': wanted_url, 'scope': 'edition', 'credit': 'NDLサーチAPIの国立国会図書館書誌データを加工して作成',
                    'license_url': 'https://www.ndl.go.jp/data/data_service/quickguide'}],
                'classification_scope': 'edition', 'content_features_adopted': False,
                'recommendation_mode': 'insufficient_evidence'}

    def ingest(self, records):
        result = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'failures': []}
        cache = {}
        with self._connect() as db:
            for record in records:
                try:
                    row = self._project(record, cache)
                    encoded = _json(record)
                    digest = hashlib.sha256(encoded.encode()).hexdigest()
                    previous = db.execute('SELECT record_sha256 FROM entries WHERE entry_id=?', (row['entry_id'],)).fetchone()
                    if previous and previous['record_sha256'] == digest:
                        result['unchanged'] += 1
                        continue
                    db.execute('''INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET
                        kind=excluded.kind,record_json=excluded.record_json,record_sha256=excluded.record_sha256''',
                        (row['entry_id'], row['entry_kind'], encoded, digest))
                    result['updated' if previous else 'inserted'] += 1
                except (KeyError, ValueError, OSError, TypeError) as exc:
                    # Local diagnostics are explicit but never exported in API rows.
                    reason = str(exc)
                    result['failed'] += 1
                    result['failures'].append({'source_id': record.get('id'), 'reason': reason})
                    db.execute('INSERT INTO ingestion_failures VALUES(?,?,?)', (record.get('id'), reason, datetime.now(timezone.utc).isoformat()))
        return result

    def import_metadata(self, metadata_path):
        with Path(metadata_path).open(encoding='utf-8') as handle:
            return self.ingest(json.loads(line) for line in handle if line.strip())

    def import_editions(self, records):
        return self.ingest(records)

    def _project_ndl_series(self, record, cache):
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        if record.get('source') != 'ndl':
            raise ValueError('NDL series source differs')
        anchor = record['anchor_edition_record']
        base = self._project_edition(anchor, cache)
        if record['id'] != anchor['id']:
            raise ValueError('NDL series anchor identity differs')
        proof = record['publisher_series_proof']
        assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
        if assertion is None:
            raise ValueError('official novel series assertion absent')
        match = match_bibliography(assertion, self._source_with_title_parts(anchor, cache), normalize_search, allow_unnumbered=True)
        if match is None:
            raise ValueError('NDL anchor does not reproduce publisher series identity')
        entry_id = 'lns:ndl:' + anchor['id']
        return {**base, 'entry_id': entry_id, 'work_id': entry_id, 'entry_kind': 'series',
                'title': match['title'], 'aliases': anchor['source_record']['extra'].get('title_readings', []),
                'title_origin': match['title_origin'],
                'authors': [{'qid': None, 'name': a['name'], 'ndl_authorities': a['authority_ids']} for a in match['authors']],
                'original_language': None, 'language': None, 'original_language_status': 'unknown',
                'year': None, 'anchor_edition_year': base['year'],
                'volume': None, 'isbn': [], 'classification_scope': 'series',
                'light_novel_confirmed': True, 'label_candidate': False,
                'admission_status': 'ndl_series_identity_verified', 'relation_resolution': 'series_unit_and_anchor_verified',
                'series_identity_basis': 'publisher_series_and_ndl_bibliography',
                'series_assertion_source_url': assertion['source_url'],
                'publisher_series_reference_id': assertion['reference_id'],
                'source_attributions': [{**s, 'scope': 'series_bibliographic_metadata'} for s in base['source_attributions']]}

    def _source_with_title_parts(self, edition, cache):
        """Parse only explicit ISBD separators in reproduced RDF title fields."""
        from .ingestion.ndl import DC, DCTERMS, _value_text
        source = edition['source_record']
        proof = edition['provenance']
        key = ('ndl_nodes', proof['raw_path'], proof['raw_sha256'])
        if key not in cache:
            self._project_edition(edition, cache)
        node, index = cache[key][source['source_id']]
        originals = {title['value'] for title in source['titles']}
        parts = []
        for title_node in node.iter():
            if title_node.tag not in {f'{{{DC}}}title', f'{{{DCTERMS}}}title'}:
                continue
            value = _value_text(title_node)
            if value not in originals:
                continue
            main = re.split(r' (?:=|:) ', value, maxsplit=1)[0].strip()
            if main and main != value:
                parts.append({'value': main, 'locator': f'records[{index}]/recordData/{title_node.tag}',
                              'kind': 'rdf_title_isbd_main', 'source_value': value})
        return {**source, '_cataloguing_main_titles': parts}

    def _ndl_membership_match(self, series, series_record, edition_record, cache):
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        proof = series_record['publisher_series_proof']
        assertion = publisher_assertions(proof, self._artifact, cache).get(proof['reference_id'])
        source = self._source_with_title_parts(edition_record, cache)
        anchor = self._source_with_title_parts(series_record['anchor_edition_record'], cache)
        match = match_bibliography(assertion, source, normalize_search,
                                  allow_unnumbered=source['source_id'] == anchor['source_id']) if assertion else None
        anchor_match = match_bibliography(assertion, anchor, normalize_search, allow_unnumbered=True) if assertion else None
        if not match or not anchor_match:
            return None
        def ids(value):
            return {authority.rsplit('/', 1)[-1] for a in value['authors'] for authority in a['authority_ids']}
        if ids(match) != ids(anchor_match):
            return None
        return {'rule_version': 'publisher-ndl-series-membership-v1', 'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': match['title'], 'volume': match['volume'], 'volume_number': match['volume_number'],
                'author_authority_ids': sorted(ids(match)), 'imprint': match['imprint'], 'ndc_codes': match['ndc_codes'],
                'sources': [source['source_url'], assertion['source_url']],
                'field_locators': {'title': 'recordData/dcterms:title;official.series_card.title',
                    'author_authority': 'recordData/dcterms:creator/rdf:about;official.series_card.author',
                    'imprint': 'recordData/dcndl:seriesTitle;official.series_index.label',
                    'publisher': 'recordData/dcterms:publisher;official.publisher',
                    'volume': 'recordData/dcndl:volume', 'novel_class': 'recordData/subject/NDC913'}}

    def _ndl_membership_candidates(self, rows, stored, cache):
        series_rows = [r for r in rows if r['entry_id'].startswith('lns:ndl:')]
        records = {key: json.loads(value['record_json']) for key, value in stored.items()}
        index = {}
        for series in series_rows:
            index.setdefault(normalize_search(series['title']), []).append(series)
        result = {}
        for edition in rows:
            if edition['entry_kind'] != 'edition':
                continue
            record = records[edition['entry_id']]
            source = self._source_with_title_parts(record, cache)
            candidates = {s['entry_id']: s for t in [*source['titles'], *source.get('_cataloguing_main_titles', [])]
                          for s in index.get(normalize_search(t['value']), [])}
            matched = [match for series in candidates.values()
                       if (match := self._ndl_membership_match(series, records[series['entry_id']], record, cache))]
            if len(matched) == 1:
                result[edition['entry_id']] = matched[0]
        return result

    def import_ndl_series_from_publishers(self, frames_path, expected_sha256, *, source_root,
                                         sbcr_series_path=None, sbcr_series_sha256=None,
                                         label_aliases_path=None, label_aliases_sha256=None):
        """Admit uniquely cross-checked series; retain a stable NDL anchor ID."""
        from .light_novel_ndl_series_v1 import publisher_assertions, match_bibliography
        cache = {}
        # Freeze the small receipt document; later collection may extend its source.
        raw_frames = self._artifact(frames_path, expected_sha256, cache)
        frozen = self.db_path.parent / 'publisher-series-sourceframes' / (expected_sha256 + '.json')
        frozen.parent.mkdir(parents=True, exist_ok=True)
        try:
            with frozen.open('xb') as handle:
                handle.write(raw_frames)
        except FileExistsError:
            self._artifact(frozen, expected_sha256, cache)
        source_proof = {'frames_path': str(frozen.resolve()), 'frames_sha256': expected_sha256,
                        'source_root': str(Path(source_root).resolve())}
        for name, path, digest in [('sbcr_series', sbcr_series_path, sbcr_series_sha256),
                                   ('label_aliases', label_aliases_path, label_aliases_sha256)]:
            if bool(path) != bool(digest):
                raise ValueError('publisher supplemental path and SHA are required together')
            if path:
                content = self._artifact(path, digest, cache)
                snapshot = frozen.parent / (digest + '.json')
                try:
                    with snapshot.open('xb') as handle:
                        handle.write(content)
                except FileExistsError:
                    self._artifact(snapshot, digest, cache)
                source_proof[name + '_path'] = str(snapshot.resolve())
                source_proof[name + '_sha256'] = digest
        assertions = publisher_assertions(source_proof, self._artifact, cache)
        rows = self._rows(include_features=False, _cache=cache)
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries')}
        records = {key: json.loads(value['record_json']) for key, value in stored.items()}
        editions = {}
        for row in rows:
            if row['entry_kind'] == 'edition':
                source = self._source_with_title_parts(records[row['entry_id']], cache)
                for title in [*source['titles'], *source.get('_cataloguing_main_titles', [])]:
                    editions.setdefault(normalize_search(title['value']), {})[row['entry_id']] = records[row['entry_id']]
        wd_titles = {normalize_search(title) for row in rows if row['entry_kind'] == 'series' and row['qid']
                     for title in [row['title'], *row['aliases']]}
        existing = {record['publisher_series_proof']['reference_id']: record
                    for record in records.values() if record.get('schema_version') == 'light-novel-ndl-series-v1'}
        result = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'held': 0, 'held_reasons': {}, 'failures': []}
        def hold(reason):
            result['held'] += 1
            result['held_reasons'][reason] = result['held_reasons'].get(reason, 0) + 1
        proposals = []
        for reference, assertion in assertions.items():
            key = normalize_search(assertion['title'])
            if key in wd_titles:
                hold('potential_wikidata_series_overlap_requires_authority_review')
                continue
            qualified = []
            for edition in editions.get(key, {}).values():
                match = match_bibliography(assertion, self._source_with_title_parts(edition, cache), normalize_search, allow_unnumbered=True)
                if match:
                    qualified.append((edition, match))
            if not qualified:
                hold('no_qualified_ndl_anchor')
                continue
            author_groups = {tuple(sorted({value.rsplit('/', 1)[-1] for a in match['authors'] for value in a['authority_ids']})) for _, match in qualified}
            if len(author_groups) != 1:
                hold('ambiguous_ndl_author_authorities')
                continue
            previous = existing.get(reference)
            anchor = previous['anchor_edition_record'] if previous else min(qualified, key=lambda pair: (
                pair[1]['volume_number'] is None, pair[1]['volume_number'] or 0,
                pair[0]['source_record']['publication_year'] or 9999, pair[0]['id']))[0]
            proposals.append({'schema_version': 'light-novel-ndl-series-v1', 'source': 'ndl', 'id': anchor['id'],
                              'anchor_edition_record': anchor, 'publisher_series_proof': {**source_proof, 'reference_id': reference}})
        anchors = {}
        for proposal in proposals:
            anchors.setdefault(proposal['id'], []).append(proposal)
        admitted = []
        for group in anchors.values():
            if len(group) != 1:
                for _ in group:
                    hold('ambiguous_publisher_series_for_same_ndl_anchor')
                continue
            admitted.extend(group)
        imported = self.ingest(admitted)
        for key in ('inserted', 'updated', 'unchanged'):
            result[key] += imported[key]
        result['failures'].extend(imported['failures'])
        for _ in range(imported['failed']):
            hold('projection_validation_failed')
        # Persist edition membership separately; it never creates an original work.
        rows = self._rows(include_features=False)
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries')}
        memberships = self._ndl_membership_candidates(rows, stored, {})
        with self._connect() as db:
            for edition_id, membership in memberships.items():
                series_id = membership['series_entry_id']
                proof = {'kind': 'publisher_ndl_series_membership', 'edition_record_sha256': stored[edition_id]['record_sha256'],
                         'series_record_sha256': stored[series_id]['record_sha256'], 'membership': membership}
                encoded = _json(proof)
                db.execute('INSERT OR REPLACE INTO edition_series_memberships VALUES(?,?,?,?)',
                           (edition_id, series_id, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
        result['verified_edition_memberships'] = len(memberships)
        return result

    def _author_authorities(self, manifest_path, expected_sha256, cache):
        key = ('author_authorities', str(_resolved_path(manifest_path, cache)), expected_sha256)
        if key in cache:
            return cache[key]
        manifest = self._payload(manifest_path, expected_sha256, cache)
        if manifest.get('version') != 'light-novel-author-authorities-v1':
            raise ValueError('unsupported author authority manifest')
        result = {}
        for row in manifest['authors']:
            qid = _qid(row['author_qid'])
            if qid in result:
                raise ValueError('duplicate author authority identity')
            entity = self._payload(row['raw_path'], row['raw_sha256'], cache).get('entities', {}).get(qid, {})
            if entity.get('id') != qid or 'missing' in entity:
                raise ValueError('author authority raw identity differs')
            ids = set()
            for statement in entity.get('claims', {}).get('P349', []):
                snak = statement.get('mainsnak', {})
                value = snak.get('datavalue', {}).get('value')
                if (statement.get('rank') != 'deprecated' and snak.get('snaktype') == 'value'
                        and isinstance(value, str) and re.fullmatch(r'[0-9]{6,12}', value)):
                    ids.add(value)
            result[qid] = ids
        cache[key] = result
        return result

    @staticmethod
    def _publisher_books(provider, manifest_path, expected_sha256):
        if provider == 'kadokawa':
            from .light_novel_publisher_books_v1 import verify_kadokawa_books
            return verify_kadokawa_books(manifest_path, expected_sha256)
        if provider == 'shueisha-dx':
            from .light_novel_shueisha_books_v1 import verify_shueisha_books
            return verify_shueisha_books(manifest_path, expected_sha256)
        if provider == 'ichijinsha-iris':
            from .light_novel_ichijinsha_books_v1 import verify_ichijinsha_books
            return verify_ichijinsha_books(manifest_path, expected_sha256)
        raise ValueError('unknown bibliographic publisher provider')

    def _publisher_edition_match(self, provider, publisher, edition, cache):
        from .normalization import normalize_isbn
        from .light_novel_ndl_series_v1 import person_key
        scopes = {'kadokawa': {'電撃文庫', 'MF文庫J', 'ファンタジア文庫', '角川スニーカー文庫'},
                  'shueisha-dx': {'ダッシュエックス文庫', 'スーパーダッシュ文庫', 'ダッシュエックス文庫DIGITAL',
                                  'Dノベルf／ダッシュエックスノベルエフ', 'Dノベル／ダッシュエックスノベル'},
                  'ichijinsha-iris': {'一迅社文庫アイリス', 'アイリスNEO', '一迅社文庫アイリスNEO'}}
        if publisher.get('label') not in scopes.get(provider, set()):
            return None
        source = self._source_with_title_parts(edition, cache)
        if (source['language'] not in {'ja', 'jpn'} or not source.get('publisher')
                or not any(code.startswith('913') for code in source.get('extra', {}).get('ndc_codes', []))):
            return None
        isbn = normalize_isbn(str(publisher.get('isbn') or ''))
        if not isbn or isbn not in {normalize_isbn(row['value']) for row in source['identifiers'] if row['source'] == 'isbn'}:
            return None
        def title_key(value):
            return ''.join(c for c in normalize_search(value) if not unicodedata.category(c).startswith('P'))
        ndl_titles = [*source['titles'], *source.get('_cataloguing_main_titles', [])]
        matched_title = next((row['value'] for row in ndl_titles if title_key(row['value']) == title_key(publisher['title'])), None)
        if matched_title is None:
            return None
        imprint = unicodedata.normalize('NFKC', source.get('series') or '').split(';')[0].strip()
        official_labels = [publisher['label']]
        if provider == 'shueisha-dx' and '／' in publisher['label']:
            official_labels += publisher['label'].split('／')
        if not {normalize_search(label) for label in official_labels} & {normalize_search(value) for value in imprint.split(' = ')}:
            return None
        official_authors = [credit['name'] for credit in publisher.get('credits', [])
                            if credit.get('role') in {'author', '著', '著者', '原作', '著者・イラスト', '著・イラスト'}]
        if not official_authors:
            return None
        matched_authors = []
        for name in official_authors:
            candidates = [author for author in source['contributors']
                          if author['role'] == 'author' and person_key(author['name']) == person_key(name)]
            authorities = sorted({identifier['value'] for author in candidates for identifier in author['identifiers']
                if identifier['source'] == 'ndl-authority' and re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}', identifier['value'])})
            if not authorities:
                return None
            matched_authors.append({'official_credit': name, 'ndl_authorities': authorities})
        return {'rule_version': 'publisher-ndl-edition-confirmation-v1', 'scope': 'edition_only',
                'isbn13': isbn, 'matched_ndl_title': matched_title, 'authors': matched_authors,
                'ndl_imprint': imprint, 'official_label': publisher['label'],
                'ndl_publisher': source['publisher'], 'original_work_identity_asserted': False,
                'story_series_identity_asserted': False,
                'sources': [source['source_url'], publisher['source_url']]}

    def confirm_publisher_editions(self, provider, manifest_path, expected_sha256):
        """Confirm existing NDL editions, without making a work or series."""
        from .normalization import normalize_isbn
        books = self._publisher_books(provider, manifest_path, expected_sha256)
        cache = {}
        rows = self._rows(include_features=False, _cache=cache)
        with self._connect() as db:
            stored = {row['entry_id']: dict(row) for row in db.execute('SELECT * FROM entries WHERE kind=?', ('edition',))}
        by_isbn = {}
        for book in books:
            isbn = normalize_isbn(str(book.get('isbn') or ''))
            if isbn:
                by_isbn.setdefault(isbn, {})[book['publisher_book_id']] = book
        result = {'publisher_books': len(books), 'publisher_books_with_valid_isbn': sum(len(v) for v in by_isbn.values()),
                  'ndl_editions_considered': len(stored), 'confirmed': 0, 'unchanged': 0}
        matched = set()
        with self._connect() as db:
            for entry in stored.values():
                edition = json.loads(entry['record_json'])
                candidates = {}
                for identifier in edition['source_record']['identifiers']:
                    if identifier['source'] == 'isbn':
                        candidates.update(by_isbn.get(normalize_isbn(identifier['value']), {}))
                accepted = [(book, match) for book in candidates.values()
                            if (match := self._publisher_edition_match(provider, book, edition, cache))]
                if not accepted:
                    continue
                book, match = min(accepted, key=lambda pair: pair[0]['publisher_book_id'])
                matched.update(candidate['publisher_book_id'] for candidate, _ in accepted)
                proof = {'provider': provider, 'manifest_path': str(Path(manifest_path).resolve()),
                         'manifest_sha256': expected_sha256, 'publisher_book_id': book['publisher_book_id'],
                         'edition_record_sha256': entry['record_sha256'], 'match': match}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute('SELECT proof_sha256 FROM edition_book_confirmations WHERE edition_id=? AND provider=?',
                                      (entry['entry_id'], provider)).fetchone()
                if previous and previous['proof_sha256'] == digest:
                    result['unchanged'] += 1
                else:
                    db.execute('INSERT OR REPLACE INTO edition_book_confirmations VALUES(?,?,?,?)',
                               (entry['entry_id'], provider, encoded, digest))
                    result['confirmed'] += 1
        result['matched_publisher_books'] = len(matched)
        result['unmatched_publisher_books'] = len(books) - len(matched)
        return result

    def _attach_book_confirmations(self, rows, stored, cache):
        with self._connect() as db:
            bindings = db.execute('SELECT * FROM edition_book_confirmations').fetchall()
        indexed = {row['entry_id']: row for row in rows}
        stored = {row['entry_id']: row for row in stored}
        documents = {}
        for binding in bindings:
            try:
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                proof = json.loads(binding['proof_json'])
                entry_id = binding['edition_id']
                if (entry_id not in indexed or proof['provider'] != binding['provider']
                        or stored[entry_id]['record_sha256'] != proof['edition_record_sha256']):
                    continue
                key = (proof['provider'], proof['manifest_path'], proof['manifest_sha256'])
                if key not in documents:
                    documents[key] = {book['publisher_book_id']: book for book in self._publisher_books(*key)}
                book = documents[key].get(proof['publisher_book_id'])
                edition = json.loads(stored[entry_id]['record_json'])
                match = self._publisher_edition_match(proof['provider'], book, edition, cache) if book else None
                if match != proof['match']:
                    continue
                row = indexed[entry_id]
                row.update(light_novel_confirmed=True, edition_bibliography_confirmed=True,
                           edition_identity_basis='publisher_isbn_and_ndl_bibliography',
                           confirmation_source_url=book['source_url'], edition_confirmation_evidence=match)
                if row.get('admission_status') != 'edition_series_membership_verified':
                    row['admission_status'] = 'publisher_ndl_edition_verified'
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @staticmethod
    def _membership_match(series, source_record, authorities):
        """Strict edition membership, never an assertion of original-work identity.

        All six bibliographic checks are mandatory. A publisher's literary
        series field is only an imprint check and cannot supply story identity.
        """
        if source_record['language'] not in {'jpn', 'ja'}:
            return None
        names = {normalize_search(t) for t in [series['title'], *series['aliases']]}
        matched_titles = [t['value'] for t in source_record['titles'] if normalize_search(t['value']) in names]
        if not matched_titles:
            return None
        volume = unicodedata.normalize('NFKC', source_record.get('volume') or '').strip()
        number = re.match(r'^(?:第)?([0-9]+)(?:巻)?(?:$|\s|[(:])', volume)
        if not number or not 1 <= int(number[1]) <= 10000:
            return None
        ndc = source_record.get('extra', {}).get('ndc_codes', [])
        if not any(code.startswith('913') for code in ndc):
            return None
        imprint = unicodedata.normalize('NFKC', source_record.get('series') or '').split(';')[0].strip()
        # NDL explicitly separates parallel imprint titles using '='; compare
        # complete forms, never a substring of a different publication label.
        imprint_forms = {normalize_search(v) for v in imprint.split(' = ')}
        publishers = [p for p in series['publishers'] if normalize_search(p['label']) in imprint_forms]
        if not imprint or len(publishers) != 1:
            return None
        ndl_ids = set()
        for contributor in source_record['contributors']:
            if contributor['role'] != 'author':
                continue
            for identifier in contributor['identifiers']:
                if identifier['source'] == 'ndl-authority':
                    match = re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/([0-9]{6,12})', identifier['value'])
                    if match:
                        ndl_ids.add(match[1])
        author_matches = []
        for author in series['authors']:
            shared = ndl_ids & authorities.get(author['qid'], set())
            if not shared:
                return None
            author_matches.extend({'author_qid': author['qid'], 'ndl_authority_id': value} for value in sorted(shared))
        if not author_matches or ndl_ids - {a['ndl_authority_id'] for a in author_matches}:
            return None
        return {'rule_version': 'ndl-edition-series-membership-v1', 'scope': 'edition_membership_only',
                'edition_id': 'lne:ndl:' + source_record['source_id'], 'series_entry_id': series['entry_id'],
                'matched_title': matched_titles[0], 'volume': source_record['volume'], 'volume_number': int(number[1]),
                'author_matches': author_matches, 'imprint': publishers[0], 'ndc_codes': ndc,
                'sources': [series['source_url'], source_record['source_url']],
                'field_locators': {'title': 'recordData/dcterms:title', 'volume': 'recordData/dcndl:volume',
                    'author_authority': 'recordData/dcterms:creator/rdf:about;wikidata.claims.P349',
                    'imprint': 'recordData/dcndl:seriesTitle;wikidata.claims.P123',
                    'language': 'recordData/dcterms:language', 'novel_class': 'recordData/subject/NDC913'}}

    def _membership_candidates(self, rows, stored, authorities):
        index = {}
        for series in rows:
            if series['entry_kind'] == 'series':
                for title in {normalize_search(t) for t in [series['title'], *series['aliases']]}:
                    index.setdefault(title, {})[series['entry_id']] = series
        result = {}
        for edition in rows:
            if edition['entry_kind'] != 'edition':
                continue
            source = json.loads(stored[edition['entry_id']]['record_json'])['source_record']
            candidates = {}
            for title in source['titles']:
                candidates.update(index.get(normalize_search(title['value']), {}))
            proven = [proof for series in candidates.values() if (proof := self._membership_match(series, source, authorities))]
            if len(proven) == 1:
                result[edition['entry_id']] = proven[0]
        return result

    def resolve_edition_series_memberships(self, manifest_path, expected_sha256):
        """Resolve only uniquely supported edition membership from pinned raws."""
        authorities = self._author_authorities(manifest_path, expected_sha256, {})
        rows = self._rows(include_features=False)
        with self._connect() as db:
            stored = {r['entry_id']: dict(r) for r in db.execute('SELECT * FROM entries')}
        candidates = self._membership_candidates(rows, stored, authorities)
        result = {'linked': 0, 'unchanged': 0, 'unresolved_editions': sum(r['entry_kind'] == 'edition' for r in rows) - len(candidates)}
        with self._connect() as db:
            for edition_id, membership in candidates.items():
                series_id = membership['series_entry_id']
                proof = {'manifest_path': str(Path(manifest_path).resolve()), 'manifest_sha256': expected_sha256,
                         'edition_record_sha256': stored[edition_id]['record_sha256'],
                         'series_record_sha256': stored[series_id]['record_sha256'], 'membership': membership}
                encoded = _json(proof)
                digest = hashlib.sha256(encoded.encode()).hexdigest()
                previous = db.execute('SELECT proof_sha256 FROM edition_series_memberships WHERE edition_id=?', (edition_id,)).fetchone()
                if previous and previous['proof_sha256'] == digest:
                    result['unchanged'] += 1
                else:
                    db.execute('INSERT OR REPLACE INTO edition_series_memberships VALUES(?,?,?,?)', (edition_id, series_id, encoded, digest))
                    result['linked'] += 1
        return result

    def _attach_memberships(self, rows, stored, cache):
        with self._connect() as db:
            bindings = db.execute('SELECT * FROM edition_series_memberships').fetchall()
        if not bindings:
            return
        indexed = {r['entry_id']: r for r in rows}
        stored = {r['entry_id']: r for r in stored}
        plans = {}
        for binding in bindings:
            try:
                if hashlib.sha256(binding['proof_json'].encode()).hexdigest() != binding['proof_sha256']:
                    continue
                proof = json.loads(binding['proof_json'])
                edition_id, series_id = binding['edition_id'], binding['series_id']
                if (edition_id not in indexed or series_id not in indexed
                        or stored[edition_id]['record_sha256'] != proof['edition_record_sha256']
                        or stored[series_id]['record_sha256'] != proof['series_record_sha256']):
                    continue
                if proof.get('kind') == 'publisher_ndl_series_membership':
                    key = ('publisher_ndl_series_memberships',)
                    if key not in plans:
                        plans[key] = self._ndl_membership_candidates(rows, stored, cache)
                    membership = plans[key].get(edition_id)
                    if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                        continue
                    indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                        relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                        admission_status='edition_series_membership_verified', series_membership_evidence=membership)
                    continue
                key = (proof['manifest_path'], proof['manifest_sha256'])
                if key not in plans:
                    authorities = self._author_authorities(*key, cache)
                    plans[key] = self._membership_candidates(rows, stored, authorities)
                membership = plans[key].get(edition_id)
                if membership != proof['membership'] or membership.get('series_entry_id') != series_id:
                    continue
                indexed[edition_id].update(series_entry_id=series_id, volume_number=membership['volume_number'],
                    relation_resolution='edition_series_membership_verified', light_novel_confirmed=True,
                    admission_status='edition_series_membership_verified', series_membership_evidence=membership)
            except (KeyError, TypeError, ValueError, OSError):
                # A stale relation cannot prevent access to independently valid bibliography.
                continue

    def _feature_rows(self, entry_ids=None):
        from .light_novel_classification_v1 import verify_feature_export
        with self._connect() as db:
            if entry_ids is None:
                bindings = db.execute('SELECT * FROM features').fetchall()
            elif not entry_ids:
                return {}
            else:
                entry_ids = list(entry_ids)
                bindings = db.execute('SELECT * FROM features WHERE entry_id IN (' + ','.join('?' for _ in entry_ids) + ')', entry_ids).fetchall()
        manifests, result = {}, {}
        for binding in bindings:
            key = (binding['manifest_path'], binding['manifest_sha256'])
            try:
                if key not in manifests:
                    manifests[key] = verify_feature_export(*key)
                for row in manifests[key]['rows']:
                    if row.get('entry_id', row.get('series_id')) == binding['entry_id']:
                        result[binding['entry_id']] = row
                        break
            except (ValueError, OSError, KeyError, TypeError):
                # Invalid features cannot erase still-valid bibliographic access.
                continue
        return result

    def import_features(self, manifest_path, expected_sha256):
        from .light_novel_classification_v1 import verify_feature_export
        document = verify_feature_export(manifest_path, expected_sha256)
        known = {r['entry_id']: r for r in self._rows(include_features=False)}
        result = {'imported': 0, 'held': 0}
        with self._connect() as db:
            for row in document['rows']:
                entry_id = _entry(row.get('entry_id', row.get('series_id')))
                if entry_id not in known or known[entry_id]['entry_kind'] != 'series' or row.get('scope') != 'series':
                    result['held'] += 1
                    continue
                db.execute('INSERT OR REPLACE INTO features VALUES(?,?,?)', (entry_id, str(Path(manifest_path).resolve()), expected_sha256))
                result['imported'] += 1
        return result

    @staticmethod
    def _apply_feature(row, feature):
        if not feature:
            return
        public_label_keys = {'label_id', 'label', 'axis', 'decision', 'scope', 'relevance',
            'raw_confidence', 'calibrated_confidence', 'calibration_status', 'calibration_version',
            'evidence', 'missing_reason', 'spoiler'}
        labels = [{k: v for k, v in label.items() if k in public_label_keys}
                  for label in feature.get('labels', feature.get('admitted_labels', []))
                  if label.get('scope') == 'series' and label.get('decision') == 'present']
        content_ids = {s.get('evidence_id') for s in feature.get('source_attributions', [])
                       if s.get('kind') in ('synopsis', 'excerpt', 'note')}
        for label in labels:
            label['evidence'] = [_safe(e) for e in label.get('evidence', [])]
            for evidence in label['evidence']:
                evidence['quote'] = None
            label['evidence_basis'] = 'content' if any(e.get('evidence_id') in content_ids for e in label['evidence']) else 'bibliographic'
        row['native_features'] = row['llm_features'] = labels
        row['llm_held_features'] = [{k: v for k, v in label.items() if k in {'label_id', 'reason', 'axis'}}
                                  for label in feature.get('held_labels', [])]
        row['content_sources'] = [_content_source(s) for s in feature.get('source_attributions', feature.get('source_attribution', []))]
        row['content_features_adopted'] = bool(labels) and feature.get('content_features_adopted') is True
        row['recommendable'] = bool(row['genres'] or labels)
        row['recommendation_mode'] = 'series_content_preferences' if row['content_features_adopted'] else row['recommendation_mode']
        row['classification_axes'] = [{'axis': axis, 'adopted_count': sum(label.get('axis') == axis for label in labels),
            'held_count': sum(label.get('axis', label.get('label_id', '').split('.')[0]) == axis for label in feature.get('held_labels', [])),
            'unknown': not any(label.get('axis') == axis for label in labels)} for axis in AXES]

    def _rows(self, *, include_features=True, feature_entry_ids=None, _cache=None):
        cache = {} if _cache is None else _cache
        with self._connect() as db:
            stored = db.execute('SELECT e.*, r.status FROM entries e LEFT JOIN read_status r USING(entry_id)').fetchall()
            has_features = db.execute('SELECT count(*) FROM features').fetchone()[0]
        features = self._feature_rows(feature_entry_ids) if include_features and has_features else {}
        rows = []
        for entry in stored:
            if hashlib.sha256(entry['record_json'].encode()).hexdigest() != entry['record_sha256']:
                raise ValueError('stored metadata SHA mismatch')
            record = json.loads(entry['record_json'])
            row = self._project(record, cache)
            if row['entry_id'] != entry['entry_id'] or row['entry_kind'] != entry['kind']:
                raise ValueError('stored typed identity differs')
            row['read_status'] = entry['status'] or 'unknown'
            self._apply_feature(row, features.get(row['entry_id']))
            rows.append(row)
        self._attach_memberships(rows, stored, cache)
        self._attach_book_confirmations(rows, stored, cache)
        return rows

    @staticmethod
    def _families(rows):
        by_qid = {r['qid']: r for r in rows if r['qid']}
        parent = {r['entry_id']: r['entry_id'] for r in rows}

        def find(key):
            while parent[key] != key:
                parent[key] = parent[parent[key]]
                key = parent[key]
            return key

        def join(a, b):
            parent[find(a)] = find(b)

        for row in rows:
            series_id = row.get('series_entry_id')
            if row['entry_kind'] == 'edition' and series_id in parent:
                join(row['entry_id'], series_id)
            for qid in row['relations'].get('P179', []):
                other = by_qid.get(qid)
                if other and other['entry_kind'] == 'series' and row['entry_kind'] == 'work':
                    join(row['entry_id'], other['entry_id'])
            if row['entry_kind'] == 'series':
                for qid in row['relations'].get('P527', []):
                    other = by_qid.get(qid)
                    if other and other['entry_kind'] == 'work':
                        join(other['entry_id'], row['entry_id'])
        return {key: find(key) for key in parent}

    @staticmethod
    def _filtered(rows, *, query='', genre=None, language=None, year_from=None, year_to=None,
                  read_filter='all', entry_kind=None):
        if read_filter not in READ_STATUSES | {'all', 'exclude_read'}:
            raise ValueError('invalid read filter')
        if entry_kind not in (None, '', 'series', 'work', 'edition'):
            raise ValueError('invalid entry kind')
        years = []
        for value in (year_from, year_to):
            if value in (None, ''):
                years.append(None)
            elif isinstance(value, bool):
                raise ValueError('invalid publication year')
            else:
                years.append(int(value))
        key = normalize_search(query)
        result = []
        for row in rows:
            if key and key not in normalize_search(' '.join([row['title'], *row['aliases'], *[a['name'] or a['qid'] for a in row['authors']]])):
                continue
            if genre and genre not in {g['qid'] for g in row['genres']}:
                continue
            if language and language != (row.get('language') or row['original_language']):
                continue
            if entry_kind and row['entry_kind'] != entry_kind:
                continue
            if years[0] is not None and (row['year'] is None or row['year'] < years[0]):
                continue
            if years[1] is not None and (row['year'] is None or row['year'] > years[1]):
                continue
            if read_filter == 'exclude_read' and row['read_status'] == 'read':
                continue
            if read_filter in READ_STATUSES and row['read_status'] != read_filter:
                continue
            result.append(row)
        return result

    def search_catalogue(self, query='', genre=None, language=None, year_from=None, year_to=None,
                         read_filter='all', page=1, page_size=30, entry_kind=None, exclude_work_qids=None):
        if isinstance(page, bool) or isinstance(page_size, bool) or not 1 <= page_size <= 100 or page < 1:
            raise ValueError('page must be positive and page_size 1..100')
        filters = dict(query=query, genre=genre, language=language, year_from=year_from,
                       year_to=year_to, read_filter=read_filter, entry_kind=entry_kind)
        query_key = normalize_search(query)
        kind_priority = {'series': 0, 'work': 1, 'edition': 2}
        rows = sorted(self._filtered(self._rows(include_features=False), **filters), key=lambda r: (
            0 if query_key and normalize_search(r['title']) == query_key else 1,
            kind_priority[r['entry_kind']], r['title'], r['entry_id']))
        if exclude_work_qids:
            rows = [r for r in rows if r['entry_kind'] != 'work' or r['qid'] not in exclude_work_qids]
        selected = rows[(page - 1) * page_size:page * page_size]
        features = self._feature_rows({row['entry_id'] for row in selected})
        for row in selected:
            self._apply_feature(row, features.get(row['entry_id']))
        return {'items': selected, 'total': len(rows),
                'page': page, 'page_size': page_size, 'pages': math.ceil(len(rows) / page_size), 'filters': filters}

    def work_detail(self, entry_id, **_):
        entry_id = _entry(entry_id)
        rows = self._rows(feature_entry_ids={entry_id})
        row = next((r for r in rows if r['entry_id'] == entry_id), None)
        if row:
            families = self._families(rows)
            member_keys = ('entry_id', 'entry_kind', 'qid', 'title', 'year', 'read_status', 'source_url',
                           'light_novel_confirmed', 'admission_status', 'language', 'original_language',
                           'relation_resolution', 'volume', 'volume_number', 'publication_label', 'series_entry_id',
                           'series_membership_evidence')
            row['members'] = [{k: r[k] for k in member_keys if k in r}
                              for r in rows if r['entry_id'] != entry_id and families[r['entry_id']] == families[entry_id]]
            row['members'].sort(key=lambda r: (r.get('volume_number') or 0, r['title'], r['entry_id']))
            row.setdefault('relation_resolution', 'explicit_source_assertions_only')
        return row

    def set_read_status(self, entry_id, status):
        entry_id = _entry(entry_id)
        if status not in READ_STATUSES:
            raise ValueError('invalid read status')
        if self.work_detail(entry_id) is None:
            return None
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO read_status VALUES(?,?,?)', (entry_id, status, datetime.now(timezone.utc).isoformat()))
        return {'entry_id': entry_id, 'work_id': entry_id, 'read_status': status}

    def catalogue_facets(self):
        rows = self._rows()
        genres = {}
        kinds = {}
        languages = {}
        for row in rows:
            kinds[row['entry_kind']] = kinds.get(row['entry_kind'], 0) + 1
            language = row.get('language') or row['original_language']
            if language:
                languages[language] = languages.get(language, 0) + 1
            for g in row['genres']:
                genres.setdefault(g['qid'], {**g, 'count': 0})['count'] += 1
        return {'languages': [{'value': k, 'count': v} for k, v in sorted(languages.items())],
                'genres': sorted(genres.values(), key=lambda g: (-g['count'], g['qid'])),
                'entry_kinds': [{'value': k, 'count': v} for k, v in sorted(kinds.items())]}

    def preference_facets(self):
        from .scale_preferences_v1 import _taxonomy
        rows = self._rows()
        features = {}
        taxonomy = _taxonomy()
        for row in rows:
            for label in row['llm_features']:
                label_id = label['label_id']
                features.setdefault(label_id, {**taxonomy.get(label_id, {'label_id': label_id, 'axis': label['axis'], 'label': label.get('label', label_id)}), 'count': 0})['count'] += 1
        return {**self.catalogue_facets(), 'features': list(features.values()), 'axes': list(AXES),
                'classification_scope': 'series', 'calibrated': False}

    def catalogue_stats(self):
        rows = self._rows()
        counts = {k: sum(r['entry_kind'] == k for r in rows) for k in ('series', 'work', 'edition')}
        statuses = {s: sum(r['read_status'] == s for r in rows) for s in READ_STATUSES}
        with self._connect() as db:
            failures = db.execute('SELECT count(*) FROM ingestion_failures').fetchone()[0]
            unresolved_failures = db.execute('''SELECT count(DISTINCT f.source_id) FROM ingestion_failures f
                WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE json_extract(e.record_json,'$.id')=f.source_id)''').fetchone()[0]
        content = sum(r['entry_kind'] == 'series' and r['content_features_adopted'] for r in rows)
        source = sum(r['entry_kind'] == 'series' and bool(r['genres']) for r in rows)
        return {'works': len(rows), 'searchable_works': len(rows), 'series': counts['series'],
                'individual_works': counts['work'], 'editions': counts['edition'], 'held_works': 0,
                'verified_edition_series_relations': sum(bool(r.get('series_entry_id')) for r in rows),
                'confirmed_light_novel_editions': sum(r['entry_kind'] == 'edition' and r.get('light_novel_confirmed') is True for r in rows),
                'publisher_confirmed_editions': sum(r.get('edition_bibliography_confirmed') is True for r in rows),
                'content_feature_series': content, 'source_genre_series': source,
                'feature_bearing_works': sum(bool(r['genres'] or r['llm_features']) for r in rows),
                'recommendable_works': sum(r['recommendable'] for r in rows), 'source_genre_works': sum(bool(r['genres']) for r in rows),
                'llm_works': content, 'strict72_works': 0, 'raw_classified_works': sum(bool(r['llm_features']) for r in rows),
                'personalized': False, 'read_status_counts': statuses, 'ingestion_failures': failures,
                'ingestion_failure_events': failures, 'unresolved_ingestion_failures': unresolved_failures,
                'coverage_complete': False, 'human_quality_verified': False, 'classification_scope': 'series'}

    def similar_works(self, entry_id, limit=12, exclude_read=True):
        return self.recommend_preferences(liked_entry_ids=[_entry(entry_id)], limit=limit, exclude_read=exclude_read)

    def recommend_preferences(self, *, liked_entry_ids=None, preferred_features=None, avoided_features=None,
                              axis_weights=None, genre=None, language=None, year_from=None, year_to=None,
                              exclude_read=True, limit=12, reference_genres=None):
        from .scale_preferences_v1 import _taxonomy, _feature_ids, _weights, _normalise_list
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        taxonomy = _taxonomy()
        preferred = _feature_ids(preferred_features, 'preferred_features', taxonomy)
        avoided = _feature_ids(avoided_features, 'avoided_features', taxonomy)
        weights = _weights(axis_weights)
        liked = [_entry(x) for x in _normalise_list(liked_entry_ids, 'liked_entry_ids')]
        rows = self._rows()
        indexed = {r['entry_id']: r for r in rows}
        if any(key not in indexed for key in liked):
            raise ValueError('liked light-novel entry is absent')
        families = self._families(rows)
        excluded = {families[key] for key in liked}
        if exclude_read:
            excluded |= {families[r['entry_id']] for r in rows if r['read_status'] == 'read'}
        source_genres = {g['qid']: g['label'] for key in liked for g in indexed[key]['genres']}
        # Internal bridge only: the combined catalogue authenticates world-work
        # detail before supplying these source declarations. These are ranking
        # references and never become the candidate's native labels.
        reference_sources = {}
        for reference in reference_genres or []:
            qid = _qid(reference['qid'])
            if reference.get('scope') != 'work' or not str(reference.get('source_url', '')).startswith('https://www.wikidata.org/wiki/Q'):
                raise ValueError('invalid authenticated world-work genre reference')
            source_genres[qid] = reference.get('label') or qid
            reference_sources[qid] = _safe(reference)
        requested_features = preferred | {f['label_id'] for key in liked for f in indexed[key]['llm_features']}
        candidates = self._filtered(rows, genre=genre, language=language, year_from=year_from, year_to=year_to)
        series_families = {families[r['entry_id']] for r in rows if r['entry_kind'] == 'series'}
        ranked = []
        for row in candidates:
            if families[row['entry_id']] in excluded or row['entry_kind'] == 'edition':
                continue
            # A known volume is offered through the series card, not a duplicate recommendation.
            if row['entry_kind'] == 'work' and families[row['entry_id']] in series_families:
                continue
            shared = set(source_genres) & {g['qid'] for g in row['genres']}
            labels = {l['label_id']: l for l in row['llm_features']}
            matches = requested_features & labels.keys()
            if avoided & labels.keys():
                continue
            score = 0.0
            reasons = []
            if shared:
                score += .35 * len(shared) / len(set(source_genres) | {g['qid'] for g in row['genres']})
                reasons.append({'kind': 'source_genre', 'shared': [{'qid': q, 'label': source_genres[q]} for q in sorted(shared)],
                                'source_locator': 'claims.P136', 'scope': row['entry_kind'], 'source_url': row['source_url'],
                                'reference_sources': [reference_sources[q] for q in sorted(shared) if q in reference_sources]})
            for label_id in sorted(matches):
                label = labels[label_id]
                score += weights.get(label['axis'], 0) * float(label.get('relevance', 1))
                reasons.append({'kind': 'series_content_feature' if label.get('evidence_basis') == 'content' else 'series_bibliographic_feature', 'label_id': label_id,
                                'label': taxonomy.get(label_id, {}).get('label', label_id), 'scope': 'series',
                                'evidence': label.get('evidence', []), 'sources': row['content_sources'], 'calibration_status': 'uncalibrated'})
            if reasons:
                ranked.append({**row, 'score': round(score, 6), 'score_kind': 'uncalibrated_series_similarity', 'reasons': reasons})
        ranked.sort(key=lambda r: (-r['score'], r['title'], r['entry_id']))
        chosen, used = [], set()
        for row in ranked:
            family = families[row['entry_id']]
            if family not in used:
                used.add(family)
                chosen.append(row)
                if len(chosen) == limit:
                    break
        return {'mode': 'series_preferences' if chosen else 'insufficient_evidence', 'personalized': False,
                'items': chosen, 'liked_entry_ids': liked, 'classification_scope': 'series',
                'explanation': 'シリーズの内容特徴と資料記載ジャンルによる未校正の類似度です。書誌のみと内容根拠付きの理由を区別しています。' if chosen else '利用できる共通特徴が不足しています。',
                'read_unknown_is_unread': False, 'human_quality_verified': False}
