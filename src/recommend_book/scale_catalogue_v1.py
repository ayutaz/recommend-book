"""Incremental raw-backed novel catalogue and explicitly coarse source similarity.

This separate metadata projection does not issue WorkInput/UsageGuard admissions,
adopt strict taxonomy labels, or inherit rights over a linked page's contents.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

from .ingestion.wikidata import LANGUAGE_CODES, claim_values
from .models import SourceRecord
from .rights import RightsRegistry

VERSION = 'scale-catalogue-v1'
CONFLICT_TYPES = {'Q3331189', 'Q53731850', 'Q49084', 'Q277759', 'Q1667921',
                  'Q1279564', 'Q105420', 'Q2668072', 'Q104213567', 'Q5398426', 'Q11424', 'Q7725310'}
READ_STATUSES = {'unknown', 'unread', 'reading', 'read'}
CLASSIFICATION_AXES = ('genre', 'theme', 'structure', 'protagonist', 'style', 'atmosphere')
NATIVE_EXPORT_VERSIONS = {'scale-native-classification-export-v1',
                          'scale-content-native-classification-export-v2',
                          'scale-content-native-classification-export-v3'}
_NATIVE_PIN_WORKERS = 4


def collective_description(entity):
    """Return an explicit collective description, not a novel's series membership."""
    for description in entity.get('descriptions', {}).values():
        text = description.get('value', '')
        if re.match(r'^(?:novel series\b|série de romans\b|dylogie de romans?\b)', text, re.IGNORECASE):
            return text
    return None


def _display_native_labels(labels):
    """Apply a conservative semantic hold without rewriting authenticated output.

    Separate review found that 'romantic fiction' alone does not establish
    the taxonomy's central love-relationship plot. Preserve source genres and
    raw labels; a clear romance citation can still support the native label.
    """
    admitted, held = [], []
    clear = {'romance novel', 'romance novels', 'romantic novel', '恋愛小説'}
    for label in labels:
        quotes = [' '.join(ref['quote'].casefold().split())
                  for ref in label.get('evidence', [])
                  if isinstance(ref, dict) and isinstance(ref.get('quote'), str)]
        ambiguous = any('romantic fiction' in quote for quote in quotes)
        supported = any(any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', quote)
                            for term in clear) for quote in quotes)
        if label.get('label_id') == 'genre.romance' and ambiguous and not supported:
            held.append({'label_id': 'genre.romance', 'reason': 'ambiguous_romantic_fiction_citation'})
        else:
            admitted.append(label)
    return admitted, held


def _native_axis_summary(native_row, displayed, display_held):
    """Expose counts from the authenticated export, never SQL provenance text."""
    raw = native_row.get('native_result', {})
    labels = [*raw.get('labels', []), *native_row['admitted_labels']]
    label_axes = {item['label_id']: item['axis'] for item in labels}
    held = {item['label_id'] for item in [
        *native_row.get('label_admission', {}).get('held_labels', []), *display_held]}
    missing = {item['axis'] for item in raw.get('missing_axes', [])}
    result = []
    for axis in CLASSIFICATION_AXES:
        adopted_count = sum(item.get('axis') == axis for item in displayed)
        held_count = sum(label_axes.get(label_id, label_id.split('.', 1)[0]) == axis for label_id in held)
        result.append({'axis': axis, 'adopted_count': adopted_count, 'held_count': held_count,
                       'unknown': axis in missing or (adopted_count == 0 and held_count == 0)})
    return result


class _NativeOperation(set):
    """Request-local pin set and parsed manifest data, never usage decisions."""
    def __init__(self):
        super().__init__()
        self.manifests = {}


@lru_cache(maxsize=1)
def _windows_proof_api():
    import ctypes
    from ctypes import wintypes

    class FileBasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ('CreationTime', 'LastAccessTime', 'LastWriteTime', 'ChangeTime')] + [
                        ('FileAttributes', wintypes.DWORD)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api, FileBasicInfo


def _native_proof_signature(path):
    stat = path.stat()
    change_time = stat.st_ctime_ns
    if os.name == 'nt':
        # Python's Windows st_ctime is creation time. NTFS ChangeTime also
        # detects a same-size rewrite whose LastWriteTime was restored.
        import ctypes
        api, basic_type = _windows_proof_api()
        handle = api.CreateFileW(str(path), 0x80, 7, None, 3, 0, None)  # read attributes; share read/write/delete
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = basic_type()
            if not api.GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            change_time = info.ChangeTime
        finally:
            api.CloseHandle(handle)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, change_time)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _qid(value):
    value = str(value).removeprefix('wd:')
    if not re.fullmatch(r'Q[1-9][0-9]*', value):
        raise ValueError('invalid Wikidata work ID')
    return value


def _all_refs(entity, prop):
    return [s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
            for s in entity.get('claims', {}).get(prop, [])
            if s.get('rank') != 'deprecated' and isinstance(s.get('mainsnak', {}).get('datavalue', {}).get('value'), dict)]


def _refs(entity, prop):
    return list(dict.fromkeys(v['id'] for v in claim_values(entity, prop)
                             if isinstance(v, dict) and re.fullmatch(r'Q[1-9][0-9]*', str(v.get('id', '')))))


def export_legacy_crosswalk(dataset_paths, delivery_path, output_path):
    """Snapshot only public IDs backed by current Wikidata source heads.

    The historical database is read once using its source index. No complete
    database hash/copy, title matching, or historical feature adoption occurs.
    """
    if isinstance(dataset_paths, (str, Path)):
        dataset_paths = [dataset_paths]
    delivery_path, output_path = Path(delivery_path).resolve(), Path(output_path).resolve()
    delivery_raw = delivery_path.read_bytes()
    public = json.loads(delivery_raw)['ranking']
    public_ids = {row['work_id'] for row in public}
    explicit = {}
    for row in public:
        value = row.get('source_qid')
        if value:
            explicit.setdefault(row['work_id'], set()).add(_qid(value))
    explicit_qids = {q for ids in explicit.values() for q in ids}
    candidates, sources, observations = {}, {}, []
    for path in dataset_paths:
        dataset_path = Path(path).resolve()
        before = dataset_path.stat()
        signature = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        db = sqlite3.connect(dataset_path.as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            for row in db.execute('''SELECT e.entity_id,e.value,s.version_id,s.record_json,s.raw_path,s.raw_sha256
                FROM external_ids e JOIN source_records origin ON origin.version_id=e.source_version_id
                JOIN source_heads h ON h.source=origin.source AND h.source_id=origin.source_id
                JOIN source_records s ON s.version_id=h.version_id
                WHERE e.source='wikidata' AND e.scope='work' AND s.source='wikidata' '''):
                if row['entity_id'] not in public_ids and row['value'] not in explicit_qids:
                    continue
                record = SourceRecord.model_validate_json(row['record_json'])
                if record.source_id != row['value'] or record.record_kind != 'work':
                    continue
                qid = _qid(row['value'])
                sources[qid] = {'source_version_id': row['version_id'], 'record_sha256': _sha(_json(record.model_dump(mode='json')).encode()),
                                'raw_path': row['raw_path'], 'raw_sha256': row['raw_sha256']}
                if row['entity_id'] in public_ids:
                    candidates.setdefault(row['entity_id'], set()).add(qid)
        finally:
            db.close()
        after = dataset_path.stat()
        if signature != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('legacy source database changed during indexed crosswalk export')
        observations.append({'path': str(dataset_path), 'size': signature[0], 'mtime_ns': signature[1]})
    for old_id, qids in explicit.items():
        candidates.setdefault(old_id, set()).update(q for q in qids if q in sources)
    rows, held = [], []
    for old_id, qids in sorted(candidates.items()):
        if len(qids) != 1:
            held.append({'old_work_id': old_id, 'reason': 'multiple Wikidata work authorities' if qids else 'Wikidata source authority absent from selected master'})
            continue
        qid = next(iter(qids))
        rows.append({'old_work_id': old_id, 'qid': qid, 'source': sources[qid]})
    snapshot = {'version': 'scale-legacy-crosswalk-v1', 'rows': rows, 'held': held,
                'legacy_delivery': {'path': str(delivery_path), 'sha256': _sha(delivery_raw)},
                'source_database_observations': observations,
                'matching': 'explicit public authority field or source-bound work Wikidata identifier; no title matching',
                'unmapped_public_ids': len(public_ids) - len(rows), 'legacy_features_adopted': False}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw = (_json(snapshot) + '\n').encode('utf-8')
    temporary = output_path.with_suffix(output_path.suffix + '.tmp')
    temporary.write_bytes(raw)
    temporary.replace(output_path)
    return {'path': str(output_path), 'sha256': _sha(raw), 'mapped': len(rows), 'held': len(held)}


class ScaleCatalogue:
    def __init__(self, db_path, *, source_conditions_path=None, source_conditions_sha256=None):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._raw_cache = OrderedDict()
        self._json_cache = OrderedDict()
        self._registry = RightsRegistry.default()
        with self._connect() as db:
            # Bulk source-version writes must not block the running search UI.
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS catalogue_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS source_versions(
                    version_id TEXT PRIMARY KEY,qid TEXT NOT NULL,record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL,raw_path TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,conditions_sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS source_versions_qid ON source_versions(qid);
                CREATE TRIGGER IF NOT EXISTS immutable_scale_source_update BEFORE UPDATE ON source_versions BEGIN
                    SELECT RAISE(ABORT,'source versions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_scale_source_delete BEFORE DELETE ON source_versions BEGIN
                    SELECT RAISE(ABORT,'source versions are immutable'); END;
                CREATE TABLE IF NOT EXISTS works(
                    qid TEXT PRIMARY KEY,version_id TEXT NOT NULL,title TEXT NOT NULL,
                    search_text TEXT NOT NULL,language TEXT,year INTEGER,
                    recommendable INTEGER NOT NULL,display_allowed INTEGER NOT NULL,
                    row_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS works_language ON works(language,year);
                CREATE TABLE IF NOT EXISTS work_genres(qid TEXT NOT NULL,genre_qid TEXT NOT NULL,
                    label TEXT,PRIMARY KEY(qid,genre_qid));
                CREATE INDEX IF NOT EXISTS genre_lookup ON work_genres(genre_qid,qid);
                CREATE TABLE IF NOT EXISTS read_status(qid TEXT PRIMARY KEY,status TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ingestion_failures(id INTEGER PRIMARY KEY,qid TEXT,
                    error TEXT NOT NULL,observed_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS crosswalk(old_work_id TEXT PRIMARY KEY,qid TEXT NOT NULL,proof_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS raw_artifacts(path TEXT PRIMARY KEY,sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS native_classifications(
                    qid TEXT PRIMARY KEY,manifest_path TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
                    labels_json TEXT NOT NULL,provenance_json TEXT NOT NULL);
            ''')
            existing = dict(db.execute('SELECT key,value FROM catalogue_meta'))
            if source_conditions_path is not None:
                if not source_conditions_sha256:
                    raise ValueError('source conditions SHA pin required')
                chosen = {'version': VERSION, 'source_conditions_path': str(Path(source_conditions_path).resolve()),
                          'source_conditions_sha256': source_conditions_sha256}
                if existing and any(existing.get(k) != v for k, v in chosen.items()):
                    raise ValueError('catalogue source conditions binding differs; use separate database')
                db.executemany('INSERT OR IGNORE INTO catalogue_meta VALUES(?,?)', chosen.items())
                existing = chosen
        self._meta = existing

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

    def _artifact(self, path, sha):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('raw proof path must be absolute')
        stat = path.stat()
        key = (str(path), sha)
        signature = (stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns)
        cached = self._raw_cache.get(key)
        if cached and cached[0] == signature:
            self._raw_cache.move_to_end(key)
            return cached[1]
        raw = path.read_bytes()
        if _sha(raw) != sha:
            raise ValueError('source raw hash changed: ' + str(path))
        self._raw_cache[key] = (signature, raw)
        self._raw_cache.move_to_end(key)
        while len(self._raw_cache) > 96:
            self._raw_cache.popitem(last=False)
        return raw

    def _payload(self, path, sha):
        raw = self._artifact(path, sha)
        key = (str(path), sha)
        if key not in self._json_cache:
            self._json_cache[key] = json.loads(raw)
        self._json_cache.move_to_end(key)
        while len(self._json_cache) > 48:
            self._json_cache.popitem(last=False)
        return self._json_cache[key]

    def _conditions(self):
        if not self._meta.get('source_conditions_path'):
            raise ValueError('catalogue has no pinned source conditions')
        raw = Path(self._meta['source_conditions_path']).read_bytes()
        if _sha(raw) != self._meta['source_conditions_sha256']:
            raise ValueError('source conditions hash changed')
        data = json.loads(raw)
        if (data.get('version'), data.get('source'), data.get('policy_id'), data.get('metadataonly_scope')) != (
                'scale-source-conditions-v1', 'wikidata', 'wikidata-cc0', True):
            raise ValueError('source conditions metadata scope differs')
        date = datetime.fromisoformat(data['retrieved_at'].replace('Z', '+00:00'))
        if date.tzinfo is None or date > datetime.now(timezone.utc) or (datetime.now(timezone.utc) - date).days > 7:
            raise ValueError('source conditions receipt is not current')
        primary = data['policy_snapshot']
        text = self._artifact(primary['path'], primary['sha256']).decode('utf-8').lower()
        if 'cc0' not in text:
            raise ValueError('primary CC0 condition clause absent')
        policy = self._registry.policies['wikidata-cc0']
        if policy.source != 'wikidata' or policy.provider_id != 'wikidata' or policy.license != 'CC0-1.0':
            raise ValueError('source registry metadata policy differs')
        usages = {usage: ('allowed' if state == 'allowed' and policy.usages.get(usage) == 'allowed' else 'hold')
                  for usage, state in data['usages'].items()}
        for usage in ('acquire', 'local_storage', 'display', 'external_llm', 'commercial', 'redistribution'):
            usages.setdefault(usage, 'hold')
        return data, usages

    def _projection(self, record, conditions, rights):
        if not isinstance(record, SourceRecord):
            record = SourceRecord.model_validate(record)
        if (record.source, record.provider_id, record.record_kind, record.rights_policy_id) != (
                'wikidata', 'wikidata', 'work', 'wikidata-cc0'):
            raise ValueError('only structured Wikidata work metadata is admitted')
        qid = _qid(record.source_id)
        if record.source_url != f'https://www.wikidata.org/wiki/{qid}' or urlparse(record.source_url).hostname not in conditions['allowed_domains']:
            raise ValueError('source URL identity/domain differs')
        if rights['acquire'] != 'allowed' or rights['local_storage'] != 'allowed':
            raise ValueError('source local storage usage held')
        payload = self._payload(record.raw_path, record.raw_sha256)
        entity = payload['entities'][qid]
        if entity.get('id') != qid or 'missing' in entity or entity.get('type', 'item') != 'item':
            raise ValueError('raw work identity differs')
        types = set(_all_refs(entity, 'P31') + _all_refs(entity, 'P7937'))
        if any(s.get('rank') != 'deprecated' for s in entity.get('claims', {}).get('P629', [])) or types & CONFLICT_TYPES:
            raise ValueError('conflicting edition/story/collection/series/adaptation scope')
        if not types & {'Q8261', 'Q747381'}:
            raise ValueError('novel/light novel direct source assertion absent')
        if collective_description(entity):
            raise ValueError('explicit source description identifies a collective, not an individual novel')
        # Deliberately no subclass inference: a future extension must reproduce
        # every P279 link from independently pinned ontology raw artifacts.
        title_values = [x['value'] for x in entity.get('labels', {}).values()]
        title_values += [x['value'] for xs in entity.get('aliases', {}).values() for x in xs]
        title_values += [s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('text')
                         for s in entity.get('claims', {}).get('P1476', []) if s.get('rank') != 'deprecated']
        if not record.titles or any(t.value not in title_values for t in record.titles):
            raise ValueError('record title is not reproduced by raw labels/aliases/P1476')
        proofs = [{'raw_path': record.raw_path, 'raw_sha256': record.raw_sha256}]

        def label(ref):
            origin = record.extra.get('linked_label_provenance', {}).get(ref)
            linked = payload.get('entities', {}).get(ref)
            if origin:
                linked = self._payload(origin['raw_path'], origin['raw_sha256'])['entities'][ref]
                proofs.append({'raw_path': origin['raw_path'], 'raw_sha256': origin['raw_sha256']})
            if linked is None:
                return None
            if linked.get('id') != ref:
                raise ValueError('linked entity identity differs')
            labels = linked.get('labels', {})
            if origin:
                if origin['label'] not in [v['value'] for v in labels.values()]:
                    raise ValueError('linked label not reproduced by source raw')
                return origin['label']
            return next((labels[lang]['value'] for lang in ('ja', 'en') if lang in labels),
                        next((v['value'] for v in labels.values()), None))

        authors = [{'qid': ref, 'name': label(ref)} for ref in _refs(entity, 'P50')]
        # A supplied human name must be proven even though our projection derives
        # displayed names independently. Missing authority names remain null.
        for contributor in record.contributors:
            if contributor.role != 'author':
                continue
            refs = [i.value for i in contributor.identifiers if i.source == 'wikidata' and i.scope == 'agent']
            if len(refs) != 1 or refs[0] not in _refs(entity, 'P50'):
                raise ValueError('contributor authority is absent from P50')
            proven = label(refs[0])
            same_raw = payload.get('entities', {}).get(refs[0], {})
            names = {v['value'] for v in same_raw.get('labels', {}).values()}
            if contributor.name != refs[0] and contributor.name != proven and contributor.name not in names:
                raise ValueError('contributor name is not reproduced by linked authority')
        genres = [{'qid': ref, 'label': label(ref)} for ref in _refs(entity, 'P136')]
        regions = [{'qid': ref, 'label': label(ref)} for ref in _refs(entity, 'P495')]
        language_refs = _refs(entity, 'P407')
        language = LANGUAGE_CODES.get(language_refs[0], language_refs[0]) if len(language_refs) == 1 else None
        if record.original_language is not None and record.original_language != language:
            raise ValueError('record language not reproduced by raw P407')
        years = []
        for value in claim_values(entity, 'P577'):
            if isinstance(value, dict) and value.get('precision', 0) >= 9:
                match = re.match(r'\+(\d+)-', value.get('time', ''))
                if match:
                    years.append(int(match[1]))
        if record.first_publication_year is not None and record.first_publication_year != (min(years) if years else None):
            raise ValueError('record publication year not reproduced by raw P577')
        if any(region not in _refs(entity, 'P495') for region in record.region):
            raise ValueError('record origin region authority absent from raw P495')
        row = {'qid': qid, 'work_id': 'wd:' + qid, 'title': record.titles[0].value,
               'aliases': list(dict.fromkeys(t for t in title_values if t and t != record.titles[0].value)),
               'authors': authors, 'original_language': language, 'year': min(years) if years else None,
               'region': regions, 'genres': genres, 'recommendable': bool(genres) and rights['display'] == 'allowed',
               'source_url': record.source_url, 'rights': rights,
               'recommendation_mode': 'source_similarity' if genres else 'insufficient_evidence',
               'strict72_features': [], 'llm_features': [],
               'provenance': {'raw_path': record.raw_path, 'raw_sha256': record.raw_sha256,
                              'rights_policy_id': record.rights_policy_id, 'retrieved_at': record.retrieved_at,
                              'source_conditions_path': self._meta['source_conditions_path'],
                              'source_conditions_sha256': self._meta['source_conditions_sha256'],
                              'field_locators': {'genres': 'claims.P136', 'authors': 'claims.P50;linked.labels',
                                                 'original_language': 'claims.P407', 'year': 'claims.P577'},
                              'artifacts': list({p['raw_path']: p for p in proofs}.values())}}
        return record, row

    def ingest(self, records, *, chunk_size=1000):
        if not 1 <= chunk_size <= 1000:
            raise ValueError('chunk_size must be 1..1000')
        conditions, rights = self._conditions()
        result = {'inserted': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'failures': [], 'chunks': 0}
        pending = []
        for record in records:
            pending.append(record)
            if len(pending) == chunk_size:
                self._ingest_chunk(pending, conditions, rights, result)
                pending = []
        if pending:
            self._ingest_chunk(pending, conditions, rights, result)
        return result

    def _ingest_chunk(self, records, conditions, rights, result):
        # One transaction per <=1000 inputs; failed records are isolated by a
        # savepoint. Earlier committed chunks survive process interruption.
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for source in records:
                identity = source.source_id if isinstance(source, SourceRecord) else (
                    source.get('source_id') if isinstance(source, dict) else None)
                qid = identity if isinstance(identity, str) and len(identity) <= 32 and re.fullmatch(
                    r'Q[1-9][0-9]*', identity) else None
                db.execute('SAVEPOINT record')
                try:
                    record, row = self._projection(source, conditions, rights)
                    qid = record.source_id
                    for artifact in row['provenance']['artifacts']:
                        bound = db.execute('SELECT sha256 FROM raw_artifacts WHERE path=?', (artifact['raw_path'],)).fetchone()
                        if bound and bound['sha256'] != artifact['raw_sha256']:
                            raise ValueError('raw snapshot path reused with different content')
                        db.execute('INSERT OR IGNORE INTO raw_artifacts VALUES(?,?)',
                                   (artifact['raw_path'], artifact['raw_sha256']))
                    record_json = _json(record.model_dump(mode='json'))
                    record_sha = _sha(record_json.encode('utf-8'))
                    version_id = f'wd:{qid}:{record_sha}'
                    existing = db.execute('SELECT version_id FROM works WHERE qid=?', (qid,)).fetchone()
                    if existing and existing['version_id'] == version_id:
                        result['unchanged'] += 1
                    else:
                        db.execute('INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,?)',
                                   (version_id, qid, record_sha, record_json, record.raw_path, record.raw_sha256,
                                    record.retrieved_at, self._meta['source_conditions_sha256']))
                        search = ' '.join([row['title'], *row['aliases'], *[a['name'] or a['qid'] for a in row['authors']]]).casefold()
                        db.execute('INSERT OR REPLACE INTO works VALUES(?,?,?,?,?,?,?,?,?)',
                                   (qid, version_id, row['title'], search, row['original_language'], row['year'],
                                    int(row['recommendable']), int(rights['display'] == 'allowed'), _json(row)))
                        db.execute('DELETE FROM work_genres WHERE qid=?', (qid,))
                        db.executemany('INSERT INTO work_genres VALUES(?,?,?)',
                                       [(qid, g['qid'], g['label']) for g in row['genres']])
                        result['updated' if existing else 'inserted'] += 1
                    db.execute('RELEASE record')
                except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                    db.execute('ROLLBACK TO record')
                    db.execute('RELEASE record')
                    failure = {'qid': qid, 'error': str(exc)}
                    db.execute('INSERT INTO ingestion_failures(qid,error,observed_at) VALUES(?,?,?)',
                               (qid, str(exc), datetime.now(timezone.utc).isoformat()))
                    result['failed'] += 1
                    if len(result['failures']) < 100:
                        result['failures'].append(failure)
        result['chunks'] += 1

    def _row(self, stored, *, detail=False, native_verified=None, include_native=True):
        row = json.loads(stored['row_json'])
        for artifact in row['provenance']['artifacts']:
            self._artifact(artifact['raw_path'], artifact['raw_sha256'])
        row['read_status'] = stored['read_status'] or 'unknown'
        native = None
        if detail and include_native:
            with self._connect() as db:
                native = db.execute('SELECT * FROM native_classifications WHERE qid=?', (row['qid'],)).fetchone()
        if native:
            native_row = self._native_proofs(native, verified=native_verified)
            row['llm_features'], row['llm_held_features'] = _display_native_labels(json.loads(native['labels_json']))
            row['classification_axes'] = _native_axis_summary(native_row, row['llm_features'], row['llm_held_features'])
            if detail and 'source_attributions' in native_row:
                row['content_sources'] = native_row['source_attributions']
        if detail:
            if _sha(stored['record_json'].encode('utf-8')) != stored['record_sha256']:
                raise ValueError('source version record hash changed')
            row['source_record_json'] = json.loads(stored['record_json'])
        else:
            for key in ('provenance', 'strict72_features', 'llm_features'):
                row.pop(key, None)
        return row

    def _native_proofs(self, native, *, verified=None):
        # This set belongs to one operation, never an admission or a later
        # request. Every row's full provenance is still parsed below.
        if verified is None:
            verified = _NativeOperation()
        document = self._native_manifest(native, verified)
        proof = json.loads(native['provenance_json'])
        if not proof.get('artifacts'):
            raise ValueError('native classification mutable dependency pins absent')
        supplied = {(str(Path(item['path'])), item['sha256']) for item in proof['artifacts']}
        required = {(str(Path(path)), sha) for path, sha in document['artifact_sha256'].items()}
        if not required <= supplied:
            raise ValueError('native provenance omits pinned export dependencies')
        self._check_native_pins_parallel(proof['artifacts'], verified)
        if 'qid' in native.keys() or 'labels_json' in native.keys():
            return self._native_label_binding(native, document)

    def _check_native_pin(self, path, sha, verified):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('native proof path must be absolute')
        key = (str(path), sha)
        if key not in verified:
            self._native_proof_artifact(path, sha)
            verified.add(key)

    def _check_native_pins_parallel(self, artifacts, verified):
        """Verify this operation's pending pins with bounded file I/O.

        The unchanged byte verifier remains the authority. Keys enter the
        existing request-local set only after every pending verification has
        succeeded, so a failed batch cannot authorize a partial batch.
        """
        pending = []
        seen = set()
        for artifact in artifacts:
            path = Path(artifact['path'])
            if not path.is_absolute():
                raise ValueError('native proof path must be absolute')
            key = (str(path), artifact['sha256'])
            if key in verified or key in seen:
                continue
            seen.add(key)
            pending.append((path, artifact['sha256'], key))
        if not pending:
            return

        def verify(item):
            path, sha, _ = item
            self._native_proof_artifact(path, sha)

        failures = []
        with ThreadPoolExecutor(max_workers=min(_NATIVE_PIN_WORKERS, len(pending))) as executor:
            futures = [executor.submit(verify, item) for item in pending]
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    failures.append(exc)
        if failures:
            raise failures[0]
        verified.update(item[2] for item in pending)

    def _native_manifest(self, native, verified):
        from .scale_coordinator_proof_v1 import coordinator_chain_pins
        key = (native['manifest_path'], native['manifest_sha256'])
        if key not in verified.manifests:
            self._check_native_pin(*key, verified)
            document = self._payload(*key)
            if document.get('version') not in NATIVE_EXPORT_VERSIONS or not isinstance(document.get('artifact_sha256'), dict):
                raise ValueError('unsupported native classification export')
            for artifact in coordinator_chain_pins(document):
                self._check_native_pin(artifact['path'], artifact['sha256'], verified)
            verified.manifests[key] = document
        return verified.manifests[key]

    def _native_label_binding(self, native, document):
        qid = _qid(native['qid'])
        rows = [row for row in document['verified_projection']['rows'] if row['qid'] == qid]
        if len(rows) != 1 or rows[0]['work_id'] != 'wd:' + qid or _json(json.loads(native['labels_json'])) != _json(rows[0]['admitted_labels']):
            raise ValueError('native catalogue identity or labels differ from pinned admitted export')
        return rows[0]

    def _native_proof_artifact(self, path, sha):
        """Verify exact bytes per operation; timestamps never authorize reuse."""
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('native proof path must be absolute')
        signature = _native_proof_signature(path)
        with path.open('rb') as stream:
            actual_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual_sha != sha:
            raise ValueError('native proof hash changed: ' + str(path))
        if _native_proof_signature(path) != signature:
            raise ValueError('native proof changed during hash verification: ' + str(path))
        # Windows metadata can collide for rapid same-size writes. Reopen and
        # reread bytes before accepting the pin, even if its signature matches.
        # The caller's operation-local set still removes all duplicate pins.
        with path.open('rb') as stream:
            confirmed_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if confirmed_sha != sha or _native_proof_signature(path) != signature:
            raise ValueError('native proof changed during hash verification: ' + str(path))

    def import_classifications(self, manifest_path, expected_sha256):
        """Adopt only a versioned authentic verifier's display-authorized labels.

        The verifier runs the actual read-only UsageGuard/native lineage loader;
        a JSON flag or a model result alone cannot enter this projection.
        """
        from .classification.validation import load_taxonomy
        self._conditions()
        manifest_path = str(Path(manifest_path).resolve())
        raw = Path(manifest_path).read_bytes()
        if not expected_sha256 or _sha(raw) != expected_sha256:
            raise ValueError('Classification export manifest SHA differs')
        version = json.loads(raw).get('version')
        if version == 'scale-native-classification-export-v1':
            from .scale_classification_v1 import verify_catalogue_classification
        elif version == 'scale-content-native-classification-export-v2':
            from .scale_content_classification_v2 import verify_catalogue_classification
        elif version == 'scale-content-native-classification-export-v3':
            from .scale_content_classification_v3 import verify_catalogue_classification
        elif version == 'scale-content-native-classification-export-v1':
            raise ValueError('Content classification output rights review required before publication')
        else:
            raise ValueError('Unknown classification export version')
        rows = verify_catalogue_classification(manifest_path, expected_sha256)
        label_axes = {label['label_id']: label['axis'] for label in load_taxonomy()['labels']}
        result = {'raw_classified': 0, 'adopted': 0, 'failed': 0, 'unchanged': 0, 'failures': []}
        native_verified = _NativeOperation()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for native in rows:
                qid = _qid(native['qid'])
                if native['work_id'] != 'wd:' + qid:
                    raise ValueError('native classification catalogue identity differs')
                labels = native['labels']
                if any(label.get('decision') != 'present' or label.get('scope') != 'work'
                       or label.get('label_id') not in label_axes
                       or label.get('axis') != label_axes.get(label.get('label_id'))
                       for label in labels):
                    raise ValueError('native classifier verifier returned unadmitted labels')
                proof = {'manifest_path': manifest_path, 'manifest_sha256': expected_sha256,
                         'provenance_json': _json(native['provenance']), 'qid': qid, 'labels_json': _json(labels)}
                self._native_proofs(proof, verified=native_verified)
                work = db.execute('SELECT qid FROM works WHERE qid=?', (qid,)).fetchone()
                if work is None:
                    result['failed'] += 1
                    result['failures'].append({'qid': qid, 'error': 'native work not present in catalogue'})
                    continue
                previous = db.execute('SELECT manifest_sha256 FROM native_classifications WHERE qid=?', (qid,)).fetchone()
                if previous and previous['manifest_sha256'] == expected_sha256:
                    result['unchanged'] += 1
                db.execute('INSERT OR REPLACE INTO native_classifications VALUES(?,?,?,?,?)',
                           (qid, manifest_path, expected_sha256, _json(labels), proof['provenance_json']))
                result['raw_classified'] += 1
                result['adopted'] += bool(_display_native_labels(labels)[0])
                old_id = native.get('input_work_id')
                if old_id:
                    previous = db.execute('SELECT qid FROM crosswalk WHERE old_work_id=?', (old_id,)).fetchone()
                    if previous and previous['qid'] != qid:
                        raise ValueError('native crosswalk conflicts with existing identity')
                    db.execute('INSERT OR IGNORE INTO crosswalk VALUES(?,?,?)', (old_id, qid, _json(proof)))
        return result

    def catalogue_facets(self):
        """Source-only filter choices, independent of native classification proofs."""
        _, rights = self._conditions()
        if rights['display'] != 'allowed':
            return {'languages': [], 'genres': []}
        with self._connect() as db:
            db.execute('BEGIN')
            languages = [dict(r) for r in db.execute(
                'SELECT language AS value,count(*) AS count FROM works '
                'WHERE display_allowed=1 AND language IS NOT NULL '
                'GROUP BY language ORDER BY count(*) DESC,language')]
            genres = [dict(r) for r in db.execute(
                'SELECT g.genre_qid AS qid,max(g.label) AS label,count(*) AS count '
                'FROM work_genres g JOIN works w USING(qid) WHERE w.display_allowed=1 '
                'GROUP BY g.genre_qid ORDER BY count(*) DESC,g.genre_qid')]
        return {'languages': languages, 'genres': genres}

    def preference_facets(self):
        """Return safe preference choices and source filters.

        Native choices are resolved by the preference backend and are never
        treated as proof merely because a row is present in SQLite.
        """
        from .scale_preferences_v1 import preference_facets
        return preference_facets(self)

    def catalogue_stats(self):
        _, rights = self._conditions()
        with self._connect() as db:
            db.execute('BEGIN')
            n = db.execute('SELECT count(*) FROM works').fetchone()[0]
            shown = db.execute('SELECT count(*) FROM works WHERE display_allowed=1').fetchone()[0] if rights['display'] == 'allowed' else 0
            featured = db.execute('SELECT count(*) FROM works WHERE recommendable=1 AND display_allowed=1').fetchone()[0] if shown else 0
            native_proofs = db.execute("""SELECT DISTINCT manifest_path,manifest_sha256,
                json_object('artifacts',json_extract(provenance_json,'$.artifacts')) AS provenance_json
                FROM native_classifications""").fetchall()
            native_verified = _NativeOperation()
            for native in native_proofs:
                self._native_proofs(native, verified=native_verified)
            adopted_qids, semantic_held = set(), 0
            for native in db.execute('SELECT qid,labels_json,manifest_path,manifest_sha256 FROM native_classifications'):
                self._native_label_binding(native, self._native_manifest(native, native_verified))
                labels, held = _display_native_labels(json.loads(native['labels_json']))
                semantic_held += len(held)
                if labels:
                    adopted_qids.add(native['qid'])
            displayed = {r['qid'] for r in db.execute('SELECT qid FROM works WHERE display_allowed=1')} if shown else set()
            native_n = len(adopted_qids & displayed)
            raw_classified_n = db.execute('SELECT count(*) FROM native_classifications').fetchone()[0]
            eligible_classified_n = db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) WHERE w.display_allowed=1').fetchone()[0] if shown else 0
            source_featured = {r['qid'] for r in db.execute('SELECT qid FROM works WHERE display_allowed=1 AND recommendable=1')} if shown else set()
            union_features = len((source_featured | adopted_qids) & displayed)
            languages = [dict(r) for r in db.execute('SELECT language AS value,count(*) AS count FROM works WHERE display_allowed=1 AND language IS NOT NULL GROUP BY language ORDER BY count(*) DESC,language')] if shown else []
            genres = [dict(r) for r in db.execute('SELECT g.genre_qid AS qid,max(g.label) AS label,count(*) AS count FROM work_genres g JOIN works w USING(qid) WHERE w.display_allowed=1 GROUP BY g.genre_qid ORDER BY count(*) DESC,g.genre_qid')] if shown else []
            statuses = dict(db.execute("SELECT coalesce(r.status,'unknown'),count(*) FROM works w LEFT JOIN read_status r USING(qid) GROUP BY coalesce(r.status,'unknown')"))
            failures = db.execute('SELECT count(*) FROM ingestion_failures').fetchone()[0]
        return {'works': n, 'searchable_works': shown, 'held_works': n - shown,
                'feature_bearing_works': union_features, 'recommendable_works': featured,
                'source_declared_works': shown, 'source_genre_works': featured,
                'strict72_works': native_n, 'llm_works': native_n, 'raw_classified_works': raw_classified_n,
                'eligible_classified_works': eligible_classified_n,
                'semantic_held_labels': semantic_held,
                'personal_score_works': 0, 'content_history_links': 0, 'bibliographic_history_links': 0,
                'personalized': False, 'languages': languages, 'genres': genres,
                'read_status_counts': statuses, 'ingestion_failures': failures}

    def search_catalogue(self, query='', genre=None, language=None, year_from=None, year_to=None,
                         read_filter='all', page=1, page_size=30):
        _, rights = self._conditions()
        if not 1 <= page_size <= 100 or page < 1:
            raise ValueError('page must be positive and page_size 1..100')
        if read_filter not in READ_STATUSES | {'all', 'exclude_read'}:
            raise ValueError('invalid read filter')
        clauses, parameters = ['w.display_allowed=1'], []
        if rights['display'] != 'allowed':
            clauses.append('0=1')
        if query.strip():
            clauses.append("w.search_text LIKE ? ESCAPE '\\'")
            literal = query.strip().casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            parameters.append('%' + literal + '%')
        if genre:
            clauses.append('EXISTS(SELECT 1 FROM work_genres g WHERE g.qid=w.qid AND g.genre_qid=?)')
            parameters.append(_qid(genre))
        if language:
            clauses.append('w.language=?')
            parameters.append(language)
        for value, operator in ((year_from, '>='), (year_to, '<=')):
            if value not in (None, ''):
                clauses.append(f'w.year{operator}?')
                parameters.append(int(value))
        if read_filter == 'exclude_read':
            clauses.append("coalesce(r.status,'unknown')!='read'")
        elif read_filter != 'all':
            clauses.append("coalesce(r.status,'unknown')=?")
            parameters.append(read_filter)
        base = ' FROM works w LEFT JOIN read_status r USING(qid) WHERE ' + ' AND '.join(clauses)
        with self._connect() as db:
            total = db.execute('SELECT count(*)' + base, parameters).fetchone()[0]
            rows = db.execute('SELECT w.row_json,r.status AS read_status' + base + ' ORDER BY w.title,w.qid LIMIT ? OFFSET ?',
                              parameters + [page_size, (page - 1) * page_size]).fetchall()
        native_verified = _NativeOperation()
        return {'items': [self._row(r, native_verified=native_verified) for r in rows], 'total': total, 'page': page, 'page_size': page_size,
                'pages': (total + page_size - 1) // page_size,
                'filters': {'query': query, 'genre': genre, 'language': language,
                            'year_from': year_from, 'year_to': year_to, 'read_filter': read_filter}}

    def work_detail(self, qid, *, _native_verified=None, _include_native=True):
        _, rights = self._conditions()
        if rights['display'] != 'allowed':
            return None
        native_verified = _NativeOperation() if _native_verified is None else _native_verified
        qid = self._resolve_qid(qid, native_verified=native_verified)
        with self._connect() as db:
            row = db.execute('''SELECT w.row_json,r.status AS read_status,s.record_json,s.record_sha256 FROM works w
                JOIN source_versions s USING(version_id) LEFT JOIN read_status r ON r.qid=w.qid
                WHERE w.qid=? AND w.display_allowed=1''', (qid,)).fetchone()
        return self._row(row, detail=True, native_verified=native_verified,
                         include_native=_include_native) if row else None

    def _resolve_qid(self, value, *, native_verified=None):
        try:
            return _qid(value)
        except ValueError:
            with self._connect() as db:
                row = db.execute('SELECT qid,proof_json FROM crosswalk WHERE old_work_id=?', (str(value),)).fetchone()
            if row is None:
                raise ValueError('unknown catalogue work ID') from None
            proof = json.loads(row['proof_json'])
            if proof.get('kind') == 'legacy_source_crosswalk':
                for artifact in proof['artifacts']:
                    self._artifact(artifact['path'], artifact['sha256'])
            else:
                self._native_proofs(proof, verified=native_verified)
            return _qid(row['qid'])

    def import_crosswalk(self, snapshot_path, expected_sha256):
        snapshot_path = str(Path(snapshot_path).resolve())
        snapshot = json.loads(self._artifact(snapshot_path, expected_sha256))
        if snapshot.get('version') != 'scale-legacy-crosswalk-v1' or snapshot.get('legacy_features_adopted') is not False:
            raise ValueError('unsupported legacy authority-only crosswalk')
        delivery = snapshot['legacy_delivery']
        public = json.loads(self._artifact(delivery['path'], delivery['sha256']))
        public_ids = {row['work_id'] for row in public['ranking']}
        result = {'imported': 0, 'unchanged': 0, 'held': 0}
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in snapshot['rows']:
                qid = _qid(row['qid'])
                if row['old_work_id'] not in public_ids:
                    raise ValueError('legacy mapping ID absent from pinned public delivery')
                origin = row['source']
                entity = self._payload(origin['raw_path'], origin['raw_sha256'])['entities'][qid]
                if entity.get('id') != qid:
                    raise ValueError('legacy raw work authority identity differs')
                types = set(_all_refs(entity, 'P31') + _all_refs(entity, 'P7937'))
                if not types & {'Q8261', 'Q747381'} or types & CONFLICT_TYPES or _all_refs(entity, 'P629'):
                    result['held'] += 1
                    continue
                proof = {'kind': 'legacy_source_crosswalk', 'legacy_features_adopted': False,
                         'source_version_id': origin['source_version_id'], 'record_sha256': origin['record_sha256'],
                         'artifacts': [{'path': snapshot_path, 'sha256': expected_sha256}, delivery,
                                       {'path': origin['raw_path'], 'sha256': origin['raw_sha256']}]}
                previous = db.execute('SELECT qid FROM crosswalk WHERE old_work_id=?', (row['old_work_id'],)).fetchone()
                if previous and previous['qid'] != qid:
                    raise ValueError('legacy authority crosswalk conflicts with current mapping')
                db.execute('INSERT OR IGNORE INTO crosswalk VALUES(?,?,?)', (row['old_work_id'], qid, _json(proof)))
                result['unchanged' if previous else 'imported'] += 1
        return result

    def set_read_status(self, qid, status):
        qid = self._resolve_qid(qid)
        if status not in READ_STATUSES:
            raise ValueError('invalid read status')
        if self.work_detail(qid) is None:
            return None
        with self._connect() as db:
            db.execute('INSERT OR REPLACE INTO read_status VALUES(?,?,?)',
                       (qid, status, datetime.now(timezone.utc).isoformat()))
        return {'qid': qid, 'work_id': 'wd:' + qid, 'read_status': status}

    def recommend_preferences(self, *, liked_qids=None, preferred_features=None,
                              avoided_features=None, axis_weights=None, genre=None,
                              language=None, year_from=None, year_to=None,
                              exclude_read=True, limit=12):
        """Rank works using explicit preferences and authenticated features."""
        from .scale_preferences_v1 import recommend_preferences
        return recommend_preferences(
            self, liked_qids=liked_qids, preferred_features=preferred_features,
            avoided_features=avoided_features, axis_weights=axis_weights,
            genre=genre, language=language, year_from=year_from, year_to=year_to,
            exclude_read=exclude_read, limit=limit)

    def similar_works(self, qid, limit=12, exclude_read=True):
        if not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        native_verified = _NativeOperation()
        # This ranking uses source bibliography only and returns no native
        # labels. Keep native proof work at the boundary that actually uses it.
        source = self.work_detail(qid, _native_verified=native_verified, _include_native=False)
        result = {'mode': 'insufficient_evidence', 'personalized': False, 'items': [],
                  'explanation': '資料記載ジャンルが不足しています。個人向けスコアは計算していません。'}
        if source is None or not source['genres']:
            return result
        genres = {g['qid']: g['label'] for g in source['genres']}
        placeholders = ','.join('?' for _ in genres)
        with self._connect() as db:
            candidates = db.execute(f'''SELECT w.row_json,r.status AS read_status FROM works w
                LEFT JOIN read_status r USING(qid) WHERE w.qid!=? AND w.display_allowed=1 AND w.recommendable=1
                AND EXISTS(SELECT 1 FROM work_genres g WHERE g.qid=w.qid AND g.genre_qid IN ({placeholders}))
                {"AND coalesce(r.status,'unknown')!='read'" if exclude_read else ''}''',
                                    [source['qid'], *genres]).fetchall()
        ranked = []
        source_authors = {a['qid'] for a in source['authors']}
        for stored in candidates:
            candidate = json.loads(stored['row_json'])
            other_genres = {g['qid'] for g in candidate['genres']}
            shared = set(genres) & other_genres
            score = 0.7 * len(shared) / len(set(genres) | other_genres)
            reasons = [{'kind': 'source_genre', 'shared': [{'qid': q, 'label': genres[q]} for q in sorted(shared)],
                        'weight': round(score, 6), 'source_locator': 'claims.P136'}]
            shared_authors = source_authors & {a['qid'] for a in candidate['authors']}
            if shared_authors:
                score += 0.15
                reasons.append({'kind': 'same_author', 'shared': sorted(shared_authors), 'weight': 0.15})
            if source['original_language'] and source['original_language'] == candidate['original_language']:
                score += 0.1
                reasons.append({'kind': 'same_language', 'shared': [source['original_language']], 'weight': 0.1})
            if source['year'] is not None and candidate['year'] is not None and abs(source['year'] - candidate['year']) <= 20:
                score += 0.05
                reasons.append({'kind': 'publication_period', 'shared': [source['year'], candidate['year']], 'weight': 0.05})
            ranked.append((score, candidate['title'], candidate['qid'], stored, reasons))
        ranked.sort(key=lambda x: (-x[0], x[1], x[2]))
        for score, _, _, stored, reasons in ranked[:limit]:
            row = self._row(stored, native_verified=native_verified)
            row.update({'score': round(score, 6), 'reasons': reasons, 'score_kind': 'uncalibrated_source_similarity'})
            result['items'].append(row)
        result.update({'mode': 'source_similarity',
                       'explanation': '資料記載ジャンル70%・同著者15%・同言語10%・刊行年差20年以内5%の粗い類似度です。個人履歴との接続・内容精度は未検証です。'})
        return result
