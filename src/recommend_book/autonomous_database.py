"""Offline derived bibliographic databases and authentic multi-run snapshots.

Original immutable versions and model values are retained. AI review agreement
is neither an authority relation nor human gold. Publication uses a new namespace.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import importlib.util
import hashlib
from pathlib import Path
import shutil
import sqlite3
import uuid
from urllib.parse import parse_qs, urlparse

from recommend_book.classification.validation import ROOT, input_hash, load_taxonomy, validate_result
from recommend_book.classification.runner import build_run_config
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.recommendation import overlay
from recommend_book.recommendation.world import _json, _sha, _value_sha, load_world_database


def _importer():
    path = ROOT / 'scripts/import_classification_database.py'
    spec = importlib.util.spec_from_file_location('autonomous_classification_import_helpers', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _save(path, value):
    Path(path).write_text(canonical_json(value) + '\n', encoding='utf-8')


def _write_inputs(path, works):
    Path(path).write_text(''.join(canonical_json(w.model_dump(mode='json')) + '\n' for w in works), encoding='utf-8')


def _view(connection):
    obj = DatasetDB.__new__(DatasetDB)
    obj.connection = connection
    return obj


def _integrity(connection):
    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or connection.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database integrity/FK violation')


def _backup(source, target):
    db = sqlite3.connect(target)
    try:
        source.backup(db)
        db.execute('PRAGMA journal_mode=DELETE')
        _integrity(db)
    finally:
        db.close()


def _origin_runs(connection, taxonomy):
    """Share potentially megabyte-sized immutable provenance once per run."""
    origins = {}
    for row in connection.execute('SELECT * FROM classification_runs'):
        raw = dict(row)
        decoded = {name: _json(raw[column]) for name, column in (
            ('config', 'config_json'), ('context', 'execution_context_json'),
            ('migration', 'migration_manifest_json'), ('proof', 'terminal_proof_json'),
            ('provenance', 'provenance_json'))}
        config = decoded['config']
        if config != build_run_config(config['model'], config['cli_version'], taxonomy, config['batch_size']):
            raise ValueError('Original baseline execution hashes differ from current frozen core')
        origins[raw['run_id']] = {'original_run': raw, **decoded}
    return origins


def _original_inputs(connection):
    inputs = defaultdict(list)
    for row in connection.execute('SELECT * FROM classification_inputs WHERE expression_id IS NULL'):
        inputs[row['work_id']].append(dict(row))
    return inputs


def reconstruct_baseline(**arguments):
    """Verify the frozen 1752+268 composition and retain each original run.

Counts are dataset-dependent; no 2020/268 constant masks incomplete inputs.
Returns validated complete inputs and authentic results, never feature-only rows.
"""
    base_rows = load_world_database(arguments['base_database'])['rows']
    verified = overlay.apply_classification_overlay(base_rows, **arguments)
    paths = {k: Path(v).resolve(strict=True) for k, v in arguments.items()}
    manifest = _json(paths['delta_manifest'].read_bytes())
    whole = overlay._inputs(paths['delta_manifest'].parent / 'all-inputs.jsonl')
    delta = {w.work_id: w for w in overlay._inputs(paths['delta_inputs'])}
    run = _json(paths['run_manifest'].read_bytes())
    proof = _json(paths['terminal_proof'].read_bytes())
    delta_results = [_json(line) for line in paths['results'].read_bytes().splitlines()]
    delta_by_id = {r['work_id']: r for r in delta_results}
    mapping = verified['audit']['old_to_new_canonical']
    inverse = {new: old for old, new in mapping.items()}
    entries = []
    old = overlay._readonly(paths['base_database'])
    try:
        taxonomy = load_taxonomy()
        origins = _origin_runs(old, taxonomy)
        saved_inputs = _original_inputs(old)
        for position, work in enumerate(whole):
            if work.work_id in delta:
                config = run['config']
                key = job_key(input_hash(work), config)
                entry = dict(work=work, result=delta_by_id[work.work_id], state=run['jobs'][key], key=key,
                    run_id=run['run_id'], config=config, context=manifest, migration={}, proof=proof,
                    provenance={'origin': 'verified_delta_run', 'source_sha256': verified['audit']['source_sha256']},
                    original_run=None)
            else:
                candidates = saved_inputs[inverse[work.work_id]]
                if len(candidates) != 1 or candidates[0]['status'] != 'completed':
                    raise ValueError('Unique completed original baseline result required')
                saved = candidates[0]
                if saved['input_hash'] != input_hash(work) or WorkInput.model_validate(_json(saved['input_json'])) != work:
                    raise ValueError('Unchanged baseline input/source refs differ')
                origin = origins[saved['run_id']]
                config = origin['config']
                if saved['job_key'] != job_key(input_hash(work), config):
                    raise ValueError('Original baseline job key differs')
                result = _json(saved['result_json'])
                if saved['result_sha256'] != _value_sha(result):
                    raise ValueError('Original baseline result SHA differs')
                entry = dict(work=work, result=result, state=_json(saved['job_state_json']), key=saved['job_key'],
                    run_id=saved['run_id'], **origin)
            validate_result(entry['result'], work, taxonomy)
            if entry['result']['model'] != entry['config']['model'] or entry['result']['cli_version'] != entry['config']['cli_version']:
                raise ValueError('Authentic baseline model/CLI/config differs')
            entry['position'] = position
            entries.append(entry)
    finally:
        old.close()
    if len(entries) != verified['audit']['work_count']:
        raise ValueError('Complete baseline coverage required')
    return entries, verified['audit']


def _classify_database(source_path, target, entries):
    """Compose existing authentic runs; preserve their historical run records."""
    helpers = _importer()
    source = overlay._readonly(Path(source_path))
    db = sqlite3.connect(target)
    db.row_factory = sqlite3.Row
    try:
        baseline_signatures = helpers.table_signatures(source)
        if any(name.startswith('classification_') for name in baseline_signatures):
            raise ValueError('Classified snapshot source must be bibliographic only')
        source.backup(db)
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        for ddl in helpers.DDL:
            db.execute(ddl)
        groups = defaultdict(list)
        for entry in entries:
            groups[entry['run_id']].append(entry)
        for run_id, members in groups.items():
            first = members[0]
            if any(e['config'] != first['config'] for e in members):
                raise ValueError('Origin run ID has inconsistent configurations')
            expected = {e['key']: e['work'] for e in members}
            manifest = {'run_id': run_id, 'config': first['config'], 'jobs': {e['key']: e['state'] for e in members}}
            helpers.insert_rows(db, source, manifest, first['context'], first['migration'], first['proof'],
                first['provenance'], expected, {e['key']: e['result'] for e in members})
            if first['original_run'] is not None:
                original = first['original_run']
                columns = list(original)
                db.execute('UPDATE classification_runs SET ' + ','.join('"' + c + '"=?' for c in columns) + ' WHERE run_id=?',
                           [original[c] for c in columns] + [run_id])
            # Move all temporary per-run indices away before assigning global
            # positions; gaps otherwise collide with another row's old index.
            db.execute('UPDATE classification_inputs SET selected_position=-(selected_position+1) WHERE run_id=?', (run_id,))
            for entry in members:
                db.execute('UPDATE classification_inputs SET selected_position=? WHERE job_key=?', (entry['position'], entry['key']))
        _integrity(db)
        after = helpers.table_signatures(db)
        if any(after.get(name) != signature for name, signature in baseline_signatures.items()):
            raise ValueError('Original bibliographic table values changed during classification composition')
        db.commit()
    finally:
        db.close()
        source.close()
    loaded = load_world_database(target)
    if len(loaded['rows']) != len(entries):
        raise ValueError('Classified snapshot is incomplete')
    return loaded['audit']


def _coverage(path):
    db = overlay._readonly(path)
    try:
        return {'run_counts_scope': 'historical originating run; not current snapshot subset counts',
            'single_run_claimed': False, 'runs': [dict(r) for r in db.execute('''SELECT r.run_id,r.selected AS originating_selected,
                r.completed AS originating_completed,r.failed AS originating_failed,count(i.job_key) AS snapshot_selected
                FROM classification_runs r LEFT JOIN classification_inputs i ON i.run_id=r.run_id
                GROUP BY r.run_id ORDER BY r.run_id''')]}
    finally:
        db.close()


def _before_publish():
    """Fixture mutation boundary; deliberately no action."""


def _jsonl(path):
    lines = Path(path).read_bytes().splitlines()
    if any(not line.strip() for line in lines):
        raise ValueError('Empty JSONL row')
    return [_json(line) for line in lines]


def _artifact_path(value, parent):
    path = Path(value)
    candidates = [path] if path.is_absolute() else [ROOT / path, parent / path]
    existing = {p.resolve(strict=True) for p in candidates if p.is_file()}
    if len(existing) != 1:
        raise ValueError('Unambiguous existing raw artifact required')
    return existing.pop()


def _accepted(source, entries, records_path, proposals_path, ledger_path):
    baseline = {e['work'].work_id: e['work'] for e in entries}
    proposals = [WorkInput.model_validate(v) for v in _jsonl(proposals_path)]
    if len({w.work_id for w in proposals}) != len(proposals) or any(w.expression_id is not None for w in proposals):
        raise ValueError('Unique original work proposals required')
    proposal_map = {w.work_id: w for w in proposals}
    records = [SourceRecord.model_validate(v) for v in _jsonl(records_path)]
    if len({(r.source, r.source_id) for r in records}) != len(records):
        raise ValueError('Duplicate accepted source record')
    ledger = _jsonl(ledger_path)
    keys = [(r['work_id'], r['evidence_id']) for r in ledger]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate accepted evidence ledger')
    by_evidence = dict(zip(keys, ledger))
    used = set()
    raw_files = set()
    registry = RightsRegistry.default()
    changes = []
    for record in records:
        head = source.execute('''SELECT r.* FROM source_heads h JOIN source_records r ON r.version_id=h.version_id
            WHERE h.source=? AND h.source_id=?''', (record.source, record.source_id)).fetchone()
        if head is None:
            raise ValueError('Enrichment must update an existing active source')
        original = SourceRecord.model_validate(_json(head['record_json']))
        nodes = source.execute('SELECT work_id,canonical_id,resolved FROM works WHERE source_version_id=?', (head['version_id'],)).fetchall()
        if len(nodes) != 1 or nodes[0]['resolved'] != 1 or original.record_kind != 'work':
            raise ValueError('Exactly one resolved original work target required')
        work_id = nodes[0]['canonical_id']
        if work_id not in baseline or work_id not in proposal_map:
            raise ValueError('Target outside complete baseline/proposals')
        fields = original.model_dump(mode='json')
        proposed = record.model_dump(mode='json')
        if any(proposed[k] != v for k, v in fields.items() if k != 'evidence'):
            raise ValueError('Only evidence append is permitted; bibliography/identity/raw/rights must remain unchanged')
        if record.evidence[:len(original.evidence)] != original.evidence or len(record.evidence) <= len(original.evidence):
            raise ValueError('Accepted source must append evidence while retaining original values/order')
        added = record.evidence[len(original.evidence):]
        known_ids = {e.evidence_id for e in baseline[work_id].evidence}
        for e in added:
            key = work_id, e.evidence_id
            if e.evidence_id in known_ids or key in used or key not in by_evidence:
                raise ValueError('New evidence must have a unique exact ledger entry')
            used.add(key)
            row = by_evidence[key]
            expected = {'source_url': e.source_url, 'locator': e.locator, 'target_source': record.source,
                'target_source_id': record.source_id, 'target_old_version_id': head['version_id'],
                'rights_policy_id': e.rights_policy_id, 'license': e.license, 'license_url': e.license_url}
            if any(row.get(k) != v for k, v in expected.items()):
                raise ValueError('Evidence ledger target/provenance/rights differs')
            if not e.llm_input_allowed or e.expression_ref is not None:
                raise ValueError('Allowed work-level evidence only')
            policy = registry.policies.get(e.rights_policy_id)
            if policy is None or row.get('usages') != {u: policy.usages.get(u, 'unresolved') for u in USAGES}:
                raise ValueError('Purpose-specific policy states differ')
            fulfilled = row.get('fulfilled_obligations')
            if not isinstance(fulfilled, list) or any(not isinstance(x, str) for x in fulfilled):
                raise ValueError('Explicit fulfilled obligations required')
            if set(row.get('usage_permitted', {})) != set(USAGES) or any(row['usage_permitted'][u] is not True or
                not registry.permits(e.rights_policy_id, e.kind, u, set(fulfilled)) for u in USAGES):
                raise ValueError('Every usage needs allowed/fulfilled conditional permission')
            if e.license != policy.license or e.rights_policy_id != 'wikipedia-screened-prose':
                raise ValueError('Screened Wikipedia prose retains its own CC BY-SA policy')
            if e.license_url != 'https://creativecommons.org/licenses/by-sa/4.0/':
                raise ValueError('Exact CC BY-SA license URL required')
            attribution = e.attribution
            if (attribution.get('fulfilled_obligations') != fulfilled or
                not set(policy.obligations) <= set(e.inherited_restrictions) or
                any(not isinstance(attribution.get(k), str) or not attribution[k] for k in ('credit', 'permalink', 'history_url', 'changes'))):
                raise ValueError('Attribution/share-alike/change/screening obligations must travel with evidence')
            if any(attribution.get(k) != row.get(k) for k in ('raw_path', 'raw_sha256', 'identity_proof')):
                raise ValueError('Separate secondary raw provenance differs')
            identity = row['identity_proof']
            qid = identity.get('wikidata_work_id')
            if not isinstance(qid, str) or {i.value for i in original.identifiers if i.source == 'wikidata' and i.scope == 'work'} != {qid}:
                raise ValueError('Single explicit Wikidata work identity required')
            if identity.get('pageprops_wikibase_item') != qid or identity.get('target_raw_path') != original.raw_path or identity.get('target_raw_sha256') != original.raw_sha256 or identity.get('target_raw_locator') != 'entities.' + qid:
                raise ValueError('Identity/raw original scope differs')
            raw = _artifact_path(row['raw_path'], Path(ledger_path).parent)
            target_raw = _artifact_path(original.raw_path, Path(ledger_path).parent)
            raw_files.update((raw, target_raw))
            if _sha(raw) != row['raw_sha256'] or _sha(target_raw) != original.raw_sha256:
                raise ValueError('Raw artifact SHA differs')
            target_payload = _json(target_raw.read_bytes())
            entity = target_payload.get('entities', {}).get(qid)
            if entity is None and target_payload.get('id') == qid:
                entity = target_payload
            if not isinstance(entity, dict) or entity.get('id') != qid:
                raise ValueError('Original Wikidata raw entity missing')
            payload = _json(raw.read_bytes())
            pages = payload.get('query', {}).get('pages', [])
            pages = list(pages.values()) if isinstance(pages, dict) else pages
            matching = [page for page in pages if page.get('pageid') == identity.get('page_id')]
            if len(matching) != 1 or matching[0].get('pageprops', {}).get('wikibase_item') != qid:
                raise ValueError('Raw pageprops work identity differs')
            revisions = matching[0].get('revisions', [])
            if len(revisions) != 1 or revisions[0].get('revid') != identity.get('revision_id'):
                raise ValueError('Exact raw revision identity required')
            if any(type(identity.get(k)) is not int for k in ('page_id', 'revision_id')):
                raise ValueError('Page/revision integer identities required')
            url = urlparse(e.source_url)
            if (e.language != identity.get('language') or not isinstance(e.language, str) or
                url.scheme != 'https' or url.hostname != e.language + '.wikipedia.org' or
                parse_qs(url.query).get('oldid') != [str(identity['revision_id'])] or attribution['permalink'] != e.source_url):
                raise ValueError('Exact language/revision permalink required')
            if attribution.get('page_id') != identity['page_id'] or attribution.get('revision_id') != identity['revision_id']:
                raise ValueError('Evidence revision attribution differs')
            if row.get('evidence_text_sha256') != hashlib.sha256(e.text.encode()).hexdigest() or not row.get('screen_version') or not isinstance(row.get('screening'), dict):
                raise ValueError('Screened text hash and screening record required')
        changes.append({'work_id': work_id, 'source': record.source, 'source_id': record.source_id,
            'previous_version_id': head['version_id'], 'original_raw_sha256': original.raw_sha256,
            'added_evidence_ids': [e.evidence_id for e in added]})
    if used != set(keys) or set(proposal_map) != {c['work_id'] for c in changes}:
        raise ValueError('Ledger/proposals/source updates must cover the same exact accepted set')
    for work_id, proposal in proposal_map.items():
        original = baseline[work_id]
        if proposal.source_record_ids != original.source_record_ids:
            raise ValueError('Proposal must retain exact original immutable source refs')
        left, right = proposal.model_dump(mode='json'), original.model_dump(mode='json')
        if any(left[k] != v for k, v in right.items() if k != 'evidence'):
            raise ValueError('Proposal can only append accepted evidence')
        added = [e for r in records for e in r.evidence if (work_id, e.evidence_id) in used]
        expected = list(original.evidence) + added
        if sorted(canonical_json(e.model_dump(mode='json')) for e in proposal.evidence) != sorted(canonical_json(e.model_dump(mode='json')) for e in expected):
            raise ValueError('Proposal evidence differs from accepted source/ledger')
    return records, proposals, changes, raw_files


def _roles(source, audit_path, database_path):
    unresolved_ndl = {r[0] for r in source.execute('''SELECT DISTINCT w.source_version_id FROM works w
        JOIN source_records r ON r.version_id=w.source_version_id WHERE r.source='ndl' AND w.resolved=0''')}
    if audit_path is None:
        if unresolved_ndl:
            raise ValueError('Unresolved NDL records require the explicit author-role audit')
        return {'records': [], 'ambiguity_count': 0, 'resolved_ambiguous_count': 0,
                'authority_merge_allowed': False, 'corrective_source_versions_created': False}, set()
    path = Path(audit_path).resolve(strict=True)
    audit = _json(path.read_bytes())
    if (audit.get('version') != 'ndl-source-role-ambiguity-audit-v1' or audit.get('source_database_sha256') != _sha(database_path)
        or audit.get('actor') != 'mechanical_audit' or audit.get('human_gold') is not False or audit.get('database_written') is not False):
        raise ValueError('Mechanical role audit must bind the original bibliographic database')
    records = audit['records']
    versions = [r['source_version_id'] for r in records]
    if len(set(versions)) != len(versions) or set(versions) != unresolved_ndl:
        raise ValueError('Role audit must cover every unresolved NDL source version exactly')
    protected = {path}
    ambiguous = 0
    for row in records:
        saved = source.execute('SELECT * FROM source_records WHERE version_id=?', (row['source_version_id'],)).fetchone()
        if saved is None or any(row.get(k) != saved[k] for k in ('source', 'source_id', 'raw_path', 'raw_sha256')):
            raise ValueError('Role audit source version/raw provenance differs')
        record = SourceRecord.model_validate_json(saved['record_json'])
        if row['derived_contributors'] != [c.model_dump(mode='json') for c in record.contributors]:
            raise ValueError('Role audit derived contributors differ')
        nodes = [{'work_node': r['work_id'], 'canonical_id': r['canonical_id']} for r in source.execute(
            'SELECT work_id,canonical_id FROM works WHERE source_version_id=? ORDER BY work_id', (row['source_version_id'],))]
        if sorted(row['work_nodes'], key=lambda r: r['work_node']) != nodes or type(row['role_ambiguity']) is not bool:
            raise ValueError('Role audit work nodes/scope differs')
        if row['role_ambiguity'] and source.execute('SELECT 1 FROM works WHERE source_version_id=? AND resolved=1', (row['source_version_id'],)).fetchone():
            raise ValueError('Ambiguous creator authority cannot enter resolved work outputs')
        if row['role_ambiguity'] and (not row.get('raw_dc_creator') or not row.get('raw_generic_creator_authorities')):
            raise ValueError('Role ambiguity requires original field/authority evidence')
        raw = _artifact_path(saved['raw_path'], path.parent)
        if _sha(raw) != saved['raw_sha256']:
            raise ValueError('Role audit raw SHA differs')
        protected.add(raw)
        ambiguous += row['role_ambiguity']
    if type(audit.get('unique_records')) is not int or audit['unique_records'] != len(records) or type(audit.get('role_ambiguity_record_count')) is not int or audit['role_ambiguity_record_count'] != ambiguous:
        raise ValueError('Role audit strict counts differ')
    return {'version': 'autonomous-author-role-ambiguities-v1', 'source_audit_sha256': _sha(path),
        'records': records, 'ambiguity_count': ambiguous, 'resolved_ambiguous_count': 0,
        'authority_merge_allowed': False, 'corrective_source_versions_created': False,
        'input_2020_effect': 0, 'human_verified': False}, protected


def build_autonomous_database(*, baseline_overlay, output_dir, accepted_source_records=None,
                              accepted_inputs=None, accepted_ledger=None, author_role_audit=None):
    """Create an immutable baseline plus a new bibliographic/input namespace."""
    output = Path(output_dir).resolve()
    paths = {k: Path(v).resolve(strict=True) for k, v in baseline_overlay.items()}
    if output.exists() or any(output.is_relative_to(p.parent) for p in paths.values()):
        raise ValueError('Output must be new and outside protected input directories')
    accepted_paths = [accepted_source_records, accepted_inputs, accepted_ledger]
    if any(p is not None for p in accepted_paths) and any(p is None for p in accepted_paths):
        raise ValueError('All three accepted evidence artifacts are required')
    entries, baseline_audit = reconstruct_baseline(**paths)
    run_inventory = {p.resolve() for p in paths['run_manifest'].parent.rglob('*') if p.is_file()}
    protected = dict(baseline_audit['source_sha256'])
    protected[str(Path(__file__).resolve())] = _sha(Path(__file__).resolve())
    protected[str(ROOT / 'scripts/import_classification_database.py')] = _sha(ROOT / 'scripts/import_classification_database.py')
    protected[str(ROOT / 'src/recommend_book/rights.py')] = _sha(ROOT / 'src/recommend_book/rights.py')
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.' + output.name + '.build-' + uuid.uuid4().hex)
    source = overlay._readonly(paths['enriched_database'])
    try:
        roles, role_files = _roles(source, author_role_audit, paths['enriched_database'])
        protected.update({str(p): _sha(p) for p in role_files})
        updates, proposals, changes = [], [], []
        if accepted_source_records is not None:
            accepted_paths = [Path(p).resolve(strict=True) for p in accepted_paths]
            if any(output.is_relative_to(p.parent) for p in accepted_paths):
                raise ValueError('Output must be new and outside accepted input directories')
            for p in accepted_paths:
                protected[str(p)] = _sha(p)
            updates, proposals, changes, raw_files = _accepted(source, entries, *accepted_paths)
            protected.update({str(p): _sha(p) for p in raw_files})
        stage.mkdir()
        _backup(source, stage / 'dataset.sqlite')
        classified_audit = _classify_database(paths['enriched_database'], stage / 'baseline-classified.sqlite', entries)
        _save(stage / 'baseline-classification-run-coverage.json', _coverage(stage / 'baseline-classified.sqlite'))
        baseline_works = [entry['work'] for entry in entries]
        _write_inputs(stage / 'baseline-inputs.jsonl', baseline_works)
        old_versions = overlay._source_records(source)
        canonical_nodes = {r['work_id']: (r['canonical_id'], r['resolved']) for r in source.execute('SELECT work_id,canonical_id,resolved FROM works')}
        with DatasetDB(stage / 'dataset.sqlite') as db:
            if updates:
                db.ingest(updates)
            current = {w.work_id: w for w in db.work_inputs() if w.expression_id is None}
            new_nodes = {r['work_id']: (r['canonical_id'], r['resolved']) for r in db.connection.execute('SELECT work_id,canonical_id,resolved FROM works')}
            if set(current) != {w.work_id for w in baseline_works} or new_nodes != canonical_nodes:
                raise ValueError('Enrichment changed canonical partition or resolved/held scope')
            new_versions = overlay._source_records(db.connection)
            if any(new_versions.get(version) != row for version, row in old_versions.items()):
                raise ValueError('Original immutable source versions changed')
            for change in changes:
                head = db.connection.execute('SELECT version_id FROM source_heads WHERE source=? AND source_id=?', (change['source'], change['source_id'])).fetchone()[0]
                row = db.connection.execute('SELECT parent_version_id,raw_sha256 FROM source_records WHERE version_id=?', (head,)).fetchone()
                if row['parent_version_id'] != change['previous_version_id'] or row['raw_sha256'] != change['original_raw_sha256']:
                    raise ValueError('New immutable source parent/raw lineage differs')
                change['new_version_id'] = head
            _integrity(db.connection)
            db.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        works = [current[w.work_id] for w in baseline_works]
        changed = [w for w, old in zip(works, baseline_works) if input_hash(w) != input_hash(old)]
        if {w.work_id for w in changed} != {p.work_id for p in proposals}:
            raise ValueError('Changed input set differs from accepted proposals')
        for proposal in proposals:
            actual = current[proposal.work_id]
            left, right = proposal.model_dump(mode='json'), actual.model_dump(mode='json')
            if any(left[k] != v for k, v in right.items() if k not in ('source_record_ids', 'evidence')) or sorted(canonical_json(e.model_dump(mode='json')) for e in proposal.evidence) != sorted(canonical_json(e.model_dump(mode='json')) for e in actual.evidence):
                raise ValueError('Accepted proposal differs from DB-regenerated input')
        _write_inputs(stage / 'new-all-inputs.jsonl', works)
        _write_inputs(stage / 'changed-inputs.jsonl', changed)
        classification_inputs = stage / 'classification-inputs'
        classification_inputs.mkdir()
        _write_inputs(classification_inputs / 'ordered-inputs.jsonl', changed)
        _save(classification_inputs / 'manifest.json', {'version': 'autonomous-changed-inputs-v1',
            'ordered_inputs_sha256': _sha(classification_inputs / 'ordered-inputs.jsonl'),
            'work_inputs_sha256': _sha(classification_inputs / 'ordered-inputs.jsonl'),
            'work_count': len(changed), 'source_database_path': str(output / 'dataset.sqlite'),
            'source_database_sha256': _sha(stage / 'dataset.sqlite'),
            'processing_context': {'origin': 'DB-regenerated accepted evidence', 'old_results_rebound': False}})
        _save(stage / 'source-version-changes.json', {'updates': changes, 'reversal': 'undo_ingest(new_version_id) in reverse update order', 'old_raw_sha_preserved': True, 'human_merge_created': False})
        _save(stage / 'pending-classification.json', {'work_ids': [w.work_id for w in changed], 'input_hashes': {w.work_id: input_hash(w) for w in changed}, 'state': 'awaiting_changed_only_classification', 'old_results_rebound': False})
        holds = [dict(r) for r in source.execute('SELECT * FROM works WHERE resolved=0 ORDER BY work_id')]
        _save(stage / 'unresolved-holds.json', {'records': holds, 'review_queue': _view(source).review_queue(),
            'ai_agreement_is_authority': False, 'human_verified': False, 'merges_created': False})
        _save(stage / 'author-role-ambiguities.json', roles)
        if author_role_audit is not None:
            shutil.copyfile(author_role_audit, stage / 'author-role-audit.source.json')
        report = {'version': 'autonomous-database-v1', 'state': 'bibliographic_ready',
            'work_count': len(works), 'changed_count': len(changed), 'unchanged_count': len(works) - len(changed),
            'source_sha256': protected, 'baseline_overlay_audit': baseline_audit,
            'baseline_classified_audit': classified_audit, 'unresolved_count': len({r['canonical_id'] for r in holds}),
            'author_role_ambiguity_count': roles['ambiguity_count'], 'resolved_ambiguous_creator_count': 0,
            'classification_scope': 'baseline complete; updated bibliographic database awaits changed-only finalization',
            'single_run_claimed': False, 'human_verified': False, 'llm_started': False,
            'old_source_version_count': len(old_versions), 'new_source_version_count': len(new_versions),
            'ordered_inputs_sha256': _sha(stage / 'new-all-inputs.jsonl'), 'work_inputs_sha256': _sha(stage / 'new-all-inputs.jsonl'),
            'changed_inputs_sha256': _sha(stage / 'changed-inputs.jsonl'),
            'output_sha256': {p.relative_to(stage).as_posix(): _sha(p) for p in stage.rglob('*') if p.is_file()}}
        _save(stage / 'manifest.json', report)
        report_bytes = (stage / 'manifest.json').read_bytes()
        _before_publish()
        if any(not Path(p).is_file() or _sha(Path(p)) != digest for p, digest in protected.items()):
            raise ValueError('Protected source changed before publication')
        if any(_sha(stage / name) != digest for name, digest in report['output_sha256'].items()):
            raise ValueError('Staged output changed before publication')
        if (stage / 'manifest.json').read_bytes() != report_bytes or set(report['output_sha256']) | {'manifest.json'} != {p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.is_file()}:
            raise ValueError('Staged manifest/inventory changed before publication')
        if run_inventory != {p.resolve() for p in paths['run_manifest'].parent.rglob('*') if p.is_file()}:
            raise ValueError('Origin run inventory changed before publication')
        for p in (paths['base_database'], paths['enriched_database']):
            overlay._no_wal(p)
        if output.exists():
            raise ValueError('Output must be new')
        stage.rename(output)
        return report
    finally:
        source.close()
        if stage.exists():
            # Only the UUID stage created directly under the verified output parent.
            if stage.parent != output.parent or not stage.name.startswith('.' + output.name + '.build-'):
                raise ValueError('Unsafe staging cleanup')
            shutil.rmtree(stage)


def _saved_entries(path, works):
    source = overlay._readonly(path)
    entries = []
    try:
        load_world_database(path)
        taxonomy = load_taxonomy()
        origins = _origin_runs(source, taxonomy)
        saved_inputs = _original_inputs(source)
        for position, work in enumerate(works):
            rows = saved_inputs[work.work_id]
            if len(rows) != 1 or rows[0]['status'] != 'completed':
                raise ValueError('Unique authentic completed baseline result required')
            row = rows[0]
            if row['input_hash'] != input_hash(work) or WorkInput.model_validate(_json(row['input_json'])) != work:
                raise ValueError('Old classification cannot be rebound to changed input/source refs')
            origin = origins[row['run_id']]
            config = origin['config']
            result = _json(row['result_json'])
            if row['job_key'] != job_key(input_hash(work), config) or row['result_sha256'] != _value_sha(result):
                raise ValueError('Saved authentic job/result hash differs')
            validate_result(result, work, taxonomy)
            if result['model'] != config['model'] or result['cli_version'] != config['cli_version']:
                raise ValueError('Saved baseline model/CLI differs from original run')
            entries.append(dict(work=work, result=result, state=_json(row['job_state_json']), key=row['job_key'],
                run_id=row['run_id'], **origin, position=position))
    finally:
        source.close()
    return entries


def _scope_sources(connection, work_ids):
    active = defaultdict(list)
    for row in connection.execute('''SELECT DISTINCT w.canonical_id,r.* FROM works w
            JOIN source_records r ON r.version_id=w.source_version_id
            JOIN source_heads h ON h.version_id=r.version_id WHERE w.resolved=1'''):
        if row['canonical_id'] in work_ids:
            active[row['canonical_id']].append(dict(row))
    if set(active) != set(work_ids):
        raise ValueError('Scope eligibility source coverage differs from historical works')
    return active


def _verify_scope_quarantine(connection, work_ids, document, base_dir):
    """Verify the complete P31 scan, without asserting canonical human identity."""
    active = _scope_sources(connection, work_ids)
    if document is None:
        if any(r['source'] == 'wikidata' for rows in active.values() for r in rows):
            raise ValueError('Wikidata historical works require scope quarantine audit')
        document = {'version': 'work-scope-quarantine-v1', 'status': 'mechanical_audit',
            'baseline_world_works': len(work_ids), 'scanned_wikidata_canonical_works': 0,
            'non_wikidata_canonical_works': [{'work_id': w} for w in sorted(work_ids)],
            'scope_class': 'Q3331189', 'quarantine_count': 0, 'quarantines': [],
            'class_raw_proofs': [], 'baseline_modified': False}
    if document.get('version') != 'work-scope-quarantine-v1' or document.get('status') != 'mechanical_audit' or document.get('scope_class') != 'Q3331189' or document.get('baseline_modified') is not False:
        raise ValueError('Invalid scope quarantine audit policy')
    for name in ('baseline_world_works', 'scanned_wikidata_canonical_works', 'quarantine_count'):
        if type(document.get(name)) is not int or document[name] < 0:
            raise ValueError('Scope quarantine count must be an integer')
    if document['baseline_world_works'] != len(work_ids):
        raise ValueError('Scope quarantine historical coverage differs')
    quarantines = document['quarantines']
    if len(quarantines) != document['quarantine_count'] or len({q['work_id'] for q in quarantines}) != len(quarantines):
        raise ValueError('Scope quarantine count/unique works differs')
    declared = {q['work_id']: q for q in quarantines}
    files = set()
    raw_payloads = {}
    raw_digests = {}
    matches = {}
    scanned = set()
    for work_id, rows in active.items():
        for row in rows:
            if row['source'] != 'wikidata':
                continue
            scanned.add(work_id)
            record = SourceRecord.model_validate_json(row['record_json'])
            qid = row['source_id']
            if not any(i.source == 'wikidata' and i.value == qid and i.scope == 'work' for i in record.identifiers):
                raise ValueError('Scope source has no exact Wikidata work identifier')
            raw = _artifact_path(row['raw_path'], Path(base_dir))
            files.add(raw)
            if raw not in raw_payloads:
                raw_digests[raw] = _sha(raw)
                raw_payloads[raw] = _json(raw.read_bytes())
            if raw_digests[raw] != row['raw_sha256'] or record.raw_sha256 != row['raw_sha256']:
                raise ValueError('Scope raw SHA differs from immutable source version')
            entity = raw_payloads[raw].get('entities', {}).get(qid)
            if not isinstance(entity, dict) or entity.get('id') != qid:
                raise ValueError('Scope Wikidata raw entity differs')
            claims = entity.get('claims', {})
            p31 = claims.get('P31', [])
            indexes = [i for i, c in enumerate(p31) if c.get('rank') != 'deprecated'
                and c.get('mainsnak', {}).get('snaktype') == 'value'
                and c.get('mainsnak', {}).get('property') == 'P31'
                and c.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') == 'Q3331189']
            if not indexes:
                continue
            if work_id in matches:
                raise ValueError('Multiple edition assertions require explicit expanded quarantine proof')
            q = declared.get(work_id)
            if q is None or any(q.get(k) != v for k, v in {
                    'source': 'wikidata', 'source_id': qid, 'wikidata_id': qid,
                    'target_source_version_id': row['version_id'], 'raw_path': record.raw_path,
                    'raw_sha256': row['raw_sha256'], 'class_match_indexes': indexes}.items()):
                raise ValueError('Scope quarantine source/version/canonical binding differs')
            if q.get('human_reviewed') is not False or q.get('originals_modified') is not False:
                raise ValueError('Scope audit must not claim human truth or original changes')
            declared_sources = q['all_active_source_versions']
            expected_sources = [{'source': r['source'], 'source_id': r['source_id'], 'version_id': r['version_id']} for r in rows]
            if sorted(declared_sources, key=canonical_json) != sorted(expected_sources, key=canonical_json):
                raise ValueError('Scope quarantine active source versions differ')
            for prop, raw_claims in q['raw_claims'].items():
                if prop not in ('P31', 'P7937', 'P629', 'P212', 'P957') or raw_claims != claims.get(prop, []) or q['locators'].get(prop) != 'entities.' + qid + '.claims.' + prop:
                    raise ValueError('Scope raw claim/locator differs')
            if 'P31' not in q['raw_claims']:
                raise ValueError('Scope P31 assertion proof required')
            legacy = {'record_kind': record.record_kind, 'work_kind': record.work_kind,
                'titles': [t.model_dump(mode='json') for t in record.titles],
                'identifiers': [i.model_dump(mode='json') for i in record.identifiers]}
            if q.get('legacy_normalized_values') != legacy or record.record_kind != 'work':
                raise ValueError('Scope legacy normalized assertion differs')
            matches[work_id] = q
    non_wd = document['non_wikidata_canonical_works']
    if len(non_wd) != len({r['work_id'] for r in non_wd}) or {r['work_id'] for r in non_wd} != set(work_ids) - scanned or len(scanned) != document['scanned_wikidata_canonical_works'] or set(matches) != set(declared):
        raise ValueError('Scope full scan/quarantine/non-Wikidata coverage differs')
    for proof in document['class_raw_proofs']:
        raw = _artifact_path(proof['raw_path'], Path(base_dir))
        files.add(raw)
        entity = _json(raw.read_bytes()).get('entities', {}).get('Q3331189', {})
        if _sha(raw) != proof['raw_sha256'] or proof.get('entity_id') != 'Q3331189' or proof.get('locator') != 'entities.Q3331189.labels' or proof.get('labels') != entity.get('labels') or entity.get('labels', {}).get('en', {}).get('value') != 'version, edition or translation':
            raise ValueError('Scope class raw label proof differs')
    if any(_sha(p) != digest for p, digest in raw_digests.items()):
        raise ValueError('Scope raw changed during complete scan')
    return document, matches, files


def _install_scope_eligibility(connection, works, document, base_dir):
    ids = {w.work_id for w in works}
    document, quarantined, files = _verify_scope_quarantine(connection, ids, document, base_dir)
    raw = canonical_json(document)
    connection.execute('''CREATE TABLE autonomous_scope_audit (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), audit_json TEXT NOT NULL,
        audit_sha256 TEXT NOT NULL, historical_work_count INTEGER NOT NULL)''')
    connection.execute('''CREATE TABLE autonomous_work_eligibility (
        work_id TEXT PRIMARY KEY REFERENCES works(work_id), eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
        source_version_id TEXT REFERENCES source_records(version_id), raw_sha256 TEXT,
        assertion_locator TEXT, reason TEXT NOT NULL, proof_json TEXT NOT NULL)''')
    connection.execute('INSERT INTO autonomous_scope_audit VALUES(1,?,?,?)', (raw, _value_sha(document), len(ids)))
    for work_id in sorted(ids):
        q = quarantined.get(work_id)
        connection.execute('INSERT INTO autonomous_work_eligibility VALUES(?,?,?,?,?,?,?)', (work_id, int(q is None),
            q['target_source_version_id'] if q else None, q['raw_sha256'] if q else None,
            q['locators']['P31'] if q else None,
            'source_work_scope_conflict_hold' if q else 'no_verified_P31_edition_scope_conflict_in_audit',
            canonical_json(q or {})))
    _integrity(connection)
    return files


def load_autonomous_world_database(path):
    """Audit all historical classifications, then apply verified work eligibility."""
    path = Path(path).resolve(strict=True)
    before = _sha(path)
    historical = load_world_database(path)
    db = overlay._readonly(path)
    try:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'autonomous_scope_audit', 'autonomous_work_eligibility'} <= names:
            raise ValueError('Mandatory autonomous scope eligibility ledger missing')
        rows = db.execute('SELECT * FROM autonomous_scope_audit').fetchall()
        if len(rows) != 1 or rows[0]['singleton'] != 1:
            raise ValueError('Unique scope eligibility audit required')
        saved = rows[0]
        document = _json(saved['audit_json'])
        if _value_sha(document) != saved['audit_sha256']:
            raise ValueError('Scope eligibility audit hash differs')
        ids = {r['work_id'] for r in historical['rows']}
        if saved['historical_work_count'] != len(ids):
            raise ValueError('Scope eligibility historical count differs')
        _, quarantined, files = _verify_scope_quarantine(db, ids, document, path.parent)
        file_sha = {p: _sha(p) for p in files}
        eligibility = {r['work_id']: dict(r) for r in db.execute('SELECT * FROM autonomous_work_eligibility')}
        if set(eligibility) != ids:
            raise ValueError('Scope eligibility ledger must cover every historical work')
        for work_id, row in eligibility.items():
            q = quarantined.get(work_id)
            if row['eligible'] != int(q is None) or _json(row['proof_json']) != (q or {}) or row['reason'] != ('source_work_scope_conflict_hold' if q else 'no_verified_P31_edition_scope_conflict_in_audit'):
                raise ValueError('Scope eligibility value/proof differs')
            if (row['source_version_id'], row['raw_sha256'], row['assertion_locator']) != (
                    q['target_source_version_id'] if q else None, q['raw_sha256'] if q else None,
                    q['locators']['P31'] if q else None):
                raise ValueError('Scope eligibility relational source version/raw/locator differs')
        result_rows = [r for r in historical['rows'] if eligibility[r['work_id']]['eligible'] == 1]
        audit = dict(historical['audit'], historical_work_count=len(ids), eligible_work_count=len(result_rows),
            scope_quarantined_count=len(quarantined), scope_quarantine_verified=True,
            scope_quarantined_work_ids=sorted(quarantined), scope_quarantine_audit_sha256=saved['audit_sha256'],
            scope_quarantine_policy='source P31 edition assertion conflicts with work scope; canonical identity not adjudicated',
            world_adapter_counts_scope='historical classifications; eligible pool counts separately recorded',
            eligible_empty_features=sum(not r['features'] for r in result_rows),
            eligible_works_with_features=sum(bool(r['features']) for r in result_rows),
            canonical_is_edition_claimed=False, human_scope_quality_verified=False)
        if _sha(path) != before or any(_sha(p) != digest for p, digest in file_sha.items()):
            raise ValueError('Scope database/raw evidence changed during read')
        return {'rows': result_rows, 'audit': audit}
    finally:
        db.close()


def finalize_autonomous_database(*, namespace, output_dir, run_dir=None,
        execution_context_manifest=None, migration_manifest=None, terminal_proof=None, cli_profile=None,
        scope_quarantine=None):
    """Publish all original work classifications with authentic per-run provenance.

Only changed work results are replaced. A new run must be fully terminal and
successful; all refs/evidence are inserted against the updated bibliographic DB.
"""
    namespace = Path(namespace).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists() or output.is_relative_to(namespace):
        raise ValueError('Final output must be new and outside protected namespace')
    report = _json((namespace / 'manifest.json').read_bytes())
    inventory = {p.resolve() for p in namespace.rglob('*') if p.is_file()}
    if any(not p.is_relative_to(namespace) for p in inventory):
        raise ValueError('Build namespace symlink escapes')
    protected = {str(p): _sha(p) for p in inventory}
    # Builder code is immutable implementation provenance, not a frozen model
    # artifact. A later authorized finalizer may add an eligibility ledger.
    build_source_sha256 = dict(report['source_sha256'])
    protected.update({p: digest for p, digest in build_source_sha256.items() if Path(p).resolve() != Path(__file__).resolve()})
    if any(not Path(p).is_file() or _sha(Path(p)) != digest for p, digest in protected.items()):
        raise ValueError('Built source provenance changed')
    for relative, digest in report['output_sha256'].items():
        if _sha(overlay._contained(namespace, relative)) != digest:
            raise ValueError('Built artifact SHA differs')
    protected.update({str(ROOT / p): _sha(ROOT / p) for p in overlay.ARTIFACTS})
    protected[str(Path(__file__).resolve())] = _sha(Path(__file__).resolve())
    helpers = _importer()
    protected[str(ROOT / 'scripts/import_classification_database.py')] = _sha(ROOT / 'scripts/import_classification_database.py')
    source_path = namespace / 'dataset.sqlite'
    source = overlay._readonly(source_path)
    stage = output.parent / ('.' + output.name + '.finalize-' + uuid.uuid4().hex)
    try:
        _integrity(source)
        works = overlay._inputs(namespace / 'new-all-inputs.jsonl')
        scope_path = Path(scope_quarantine).resolve(strict=True) if scope_quarantine is not None else None
        if scope_path is not None:
            if output.is_relative_to(scope_path.parent):
                raise ValueError('Final output must be outside scope audit directory')
            protected[str(scope_path)] = _sha(scope_path)
        scope_document = _json(scope_path.read_bytes()) if scope_path is not None else None
        if scope_document is not None:
            bound_db = _artifact_path(scope_document['input_database_path'], scope_path.parent)
            if build_source_sha256.get(str(bound_db)) != scope_document.get('input_database_sha256') or _sha(bound_db) != scope_document['input_database_sha256']:
                raise ValueError('Scope quarantine originating database SHA differs from immutable build provenance')
        _, _, scope_raw_files = _verify_scope_quarantine(source, {w.work_id for w in works}, scope_document,
            scope_path.parent if scope_path else namespace)
        protected.update({str(p): _sha(p) for p in scope_raw_files})
        current = {w.work_id: w for w in _view(source).work_inputs() if w.expression_id is None}
        if set(current) != {w.work_id for w in works} or any(input_hash(current[w.work_id]) != input_hash(w) for w in works):
            raise ValueError('Final input hash/scope/source refs differ from updated DB')
        baseline = overlay._inputs(namespace / 'baseline-inputs.jsonl')
        if [w.work_id for w in works] != [w.work_id for w in baseline]:
            raise ValueError('Canonical ordered partition changed')
        changed = [w for w, old in zip(works, baseline) if input_hash(w) != input_hash(old)]
        if type(report['changed_count']) is not int or len(changed) != report['changed_count']:
            raise ValueError('Changed-only coverage differs from build manifest')
        unchanged = [w for w in works if w.work_id not in {c.work_id for c in changed}]
        entries = {e['work'].work_id: e for e in _saved_entries(namespace / 'baseline-classified.sqlite', unchanged)}
        run_files = set()
        if changed:
            if any(p is None for p in (run_dir, execution_context_manifest, migration_manifest, terminal_proof, cli_profile)):
                raise ValueError('Changed inputs require terminal run/context/migration/proof/profile')
            run_dir, context_path, migration_path, proof_path, profile_path = [Path(p).resolve(strict=True) for p in
                (run_dir, execution_context_manifest, migration_manifest, terminal_proof, cli_profile)]
            if any(output.is_relative_to(p if p.is_dir() else p.parent) for p in (run_dir, context_path, migration_path, proof_path, profile_path)):
                raise ValueError('Final output must be outside protected run/context directories')
            ordered = namespace / 'classification-inputs/ordered-inputs.jsonl'
            proof = helpers.read_json(proof_path)
            if type(proof.get('exit_code')) is not int or proof['exit_code'] != 0:
                raise ValueError('Successful exit_code=0 required')
            files = {source_path, ordered, ordered.with_name('manifest.json'), context_path, migration_path, proof_path, profile_path}
            run_files = helpers._protected(files, run_dir, context_path)
            protected.update({str(p): _sha(p) for p in run_files})
            helpers.check_proof(proof, source_db=source_path, ordered_inputs=ordered, run_dir=run_dir,
                execution_context_manifest=context_path, migration_manifest=migration_path, cli_profile=profile_path)
            context = helpers.read_json(context_path)
            config, taxonomy = helpers.check_context(context, ordered)
            for relative, digest in context['output_sha256'].items():
                if _sha(helpers.contained(context_path.parent, relative)) != digest:
                    raise ValueError('Prepared raw artifact/input copy differs')
            selected = helpers.check_inputs(source, ordered, context)
            if selected != changed:
                raise ValueError('Run must cover every changed input in exact fixed order')
            run = helpers.read_json(run_dir / 'manifest.json')
            if run.get('run_id') in {e['run_id'] for e in entries.values()}:
                raise ValueError('New classification run must not impersonate an original run ID')
            expected, durable = helpers.check_run(run_dir, run, proof, selected, config, taxonomy)
            if len(durable) != len(changed) or run['failed'] != 0 or run['completed'] != len(changed):
                raise ValueError('Every changed input must be completed; held cannot reuse old result')
            migration = helpers.read_json(migration_path)
            for key, work in expected.items():
                entries[work.work_id] = dict(work=work, result=durable[key], state=run['jobs'][key], key=key,
                    run_id=run['run_id'], config=config, context=context, migration=migration, proof=proof,
                    provenance={'origin': 'autonomous_changed_only_run', 'source_sha256': protected}, original_run=None)
        elif any(p is not None for p in (run_dir, execution_context_manifest, migration_manifest, terminal_proof, cli_profile)):
            raise ValueError('No changed inputs: do not attach an unrelated run')
        ordered_entries = []
        for position, work in enumerate(works):
            entry = entries[work.work_id]
            entry['position'] = position
            ordered_entries.append(entry)
        if len({e['key'] for e in ordered_entries}) != len(works):
            raise ValueError('Distinct authentic classification job keys required')
        output.parent.mkdir(parents=True, exist_ok=True)
        stage.mkdir()
        audit = _classify_database(source_path, stage / 'dataset.sqlite', ordered_entries)
        final_db = sqlite3.connect(stage / 'dataset.sqlite')
        try:
            final_db.row_factory = sqlite3.Row
            _install_scope_eligibility(final_db, works, scope_document, scope_path.parent if scope_path else namespace)
            final_db.commit()
        finally:
            final_db.close()
        audit = load_autonomous_world_database(stage / 'dataset.sqlite')['audit']
        if scope_path is not None:
            shutil.copyfile(scope_path, stage / 'work-scope-quarantine.source.json')
        _save(stage / 'classification-run-coverage.json', _coverage(stage / 'dataset.sqlite'))
        _write_inputs(stage / 'ordered-inputs.jsonl', works)
        _save(stage / 'unresolved-holds.json', _json((namespace / 'unresolved-holds.json').read_bytes()))
        shutil.copyfile(namespace / 'author-role-ambiguities.json', stage / 'author-role-ambiguities.json')
        if (namespace / 'author-role-audit.source.json').is_file():
            shutil.copyfile(namespace / 'author-role-audit.source.json', stage / 'author-role-audit.source.json')
        final = {'version': 'autonomous-classified-database-v1', 'state': 'finalized', 'work_count': len(works),
            'completed': len(ordered_entries), 'changed_count': len(changed), 'unchanged_count': len(unchanged),
            'model_values_changed': False, 'old_results_rebound': False, 'single_run_claimed': False,
            'human_verified': False, 'calibration_status': 'uncalibrated', 'llm_started': False,
            'origin_run_ids': sorted({e['run_id'] for e in ordered_entries}), 'world_adapter_audit': audit,
            'historical_work_count': audit['historical_work_count'], 'eligible_work_count': audit['eligible_work_count'],
            'scope_quarantined_count': audit['scope_quarantined_count'], 'scope_quarantine_verified': True,
            'build_implementation_source_sha256': build_source_sha256,
            'classification_run_counts_scope': 'historical originating runs; snapshot counts in classification-run-coverage.json',
            'author_role_ambiguity_count': report['author_role_ambiguity_count'], 'resolved_ambiguous_creator_count': 0,
            'source_sha256': protected, 'output_sha256': {p.name: _sha(p) for p in stage.iterdir() if p.is_file()}}
        _save(stage / 'manifest.json', final)
        final_bytes = (stage / 'manifest.json').read_bytes()
        _before_publish()
        if inventory != {p.resolve() for p in namespace.rglob('*') if p.is_file()} or any(
            not Path(p).is_file() or _sha(Path(p)) != digest for p, digest in protected.items()):
            raise ValueError('Protected source changed before final publication')
        if changed and run_files != helpers._protected(files, run_dir, context_path):
            raise ValueError('Run/context inventory changed')
        if any(_sha(stage / p) != digest for p, digest in final['output_sha256'].items()):
            raise ValueError('Staged final output changed')
        if (stage / 'manifest.json').read_bytes() != final_bytes or set(final['output_sha256']) | {'manifest.json'} != {p.name for p in stage.iterdir() if p.is_file()}:
            raise ValueError('Staged final manifest/inventory changed')
        overlay._no_wal(source_path)
        if output.exists():
            raise ValueError('Final output must be new')
        stage.rename(output)
        return final
    finally:
        source.close()
        if stage.exists():
            if stage.parent != output.parent or not stage.name.startswith('.' + output.name + '.finalize-'):
                raise ValueError('Unsafe finalization staging cleanup')
            shutil.rmtree(stage)
