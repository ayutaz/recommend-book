"""Admit purpose-reviewed exact subsets into a new immutable projection DB.

Historical inputs are archived verbatim. A new hash is a new input lineage,
never a reassignment of an old classification or permission receipt.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
import sqlite3
import uuid

from recommend_book import next_database as next_db
from recommend_book import usage_guard as guard_module
from recommend_book.classification.validation import input_hash
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.recommendation.world import _json, _sha, _value_sha
from recommend_book.usage_guard import GUARD_VERSION, UsageGuard

SEALED_PROJECTION_VERSION = 'sealed-next-projection-v1'


def _jsonl(path):
    rows = []
    for line in path.read_bytes().splitlines():
        if not line.strip():
            raise ValueError('Blank JSONL row is not an input')
        rows.append(_json(line))
    return rows


def _before_publish():
    """Fixture hook; all source bytes are rechecked after it."""


def _receipt(decision):
    if hasattr(decision, 'model_dump'):
        return decision.model_dump(mode='json')
    return {'allowed': decision.allowed, 'reasons': list(decision.reasons)}


def seal_next_projections(*, base_database, bibliography_inputs, classification_inputs,
                          alias_inputs, projection_manifest, usage_ledger, output_dir, guard=None):
    """Write a fresh backup with archived projections and exact safe subset inputs.

    Bibliographic inventory is complete even when display is held. Only allowed
    classification/alias candidates are active. Typed output derivations require
    a subsequent ledger supplement pinned to this writer's output raw SHA.
    """
    base = Path(base_database).resolve(strict=True)
    manifest_path = Path(projection_manifest).resolve(strict=True)
    ledger = Path(usage_ledger).resolve(strict=True)
    files = {'bibliographic':Path(bibliography_inputs).resolve(strict=True),
             'classification':Path(classification_inputs).resolve(strict=True),
             'alias':Path(alias_inputs).resolve(strict=True)}
    output = Path(output_dir).resolve()
    protected_dirs = {path.parent for path in [base,manifest_path,ledger,*files.values()]}
    if output.exists():
        raise ValueError('Refusing existing output')
    if any(output==directory or directory in output.parents for directory in protected_dirs):
        raise ValueError('Output must be outside all input directories')
    wal = Path(str(base)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Source WAL present; snapshot is not immutable')
    manifest = _json(manifest_path.read_bytes())
    pins = {str(path):_sha(path) for path in [base,manifest_path,ledger,*files.values(),Path(__file__).resolve(),Path(next_db.__file__).resolve(),Path(guard_module.__file__).resolve()]}
    if manifest['base_database_sha256']!=pins[str(base)] or manifest['usage_ledger_sha256']!=pins[str(ledger)] or manifest['guard_version']!=GUARD_VERSION:
        raise ValueError('Projection database/ledger/guard binding differs')
    for kind,path in files.items():
        declared = manifest['inputs'][kind]
        declared_path = Path(declared['path'])
        if not declared_path.is_absolute():
            declared_path = manifest_path.parent / declared_path
        if declared_path.resolve(strict=True)!=path or declared['sha256']!=pins[str(path)]:
            raise ValueError('Projection input file binding differs')
    lineage_path = Path(manifest.get('lineage_path','lineage.jsonl'))
    if not lineage_path.is_absolute():
        lineage_path = manifest_path.parent / lineage_path
    lineage_path = lineage_path.resolve(strict=True)
    pins[str(lineage_path)] = _sha(lineage_path)
    if pins[str(lineage_path)]!=manifest['lineage_sha256']:
        raise ValueError('Projection lineage file binding differs')
    if guard is None:
        guard = UsageGuard(ledger,expected_ledger_sha256=pins[str(ledger)])
    if Path(guard.ledger_path).resolve()!=ledger:
        raise ValueError('Guard uses another ledger')
    proposals = next_db.get_next_inputs(base)
    by_hash = {('alias' if key=='aliases' else key, input_hash(work)):work for key,works in proposals.items() for work in works}
    bibliographic = {work.work_id:work for work in proposals['bibliographic']}
    count = manifest['record_scope']['inventory_canonical_n']
    if type(count) is not int or count!=len(bibliographic):
        raise ValueError('Canonical inventory count differs')
    lineages = {}
    for row in _jsonl(lineage_path):
        key = (row['kind'],row['input_hash'])
        if key in lineages:
            raise ValueError('Duplicate projection lineage')
        lineages[key] = row
    source = next_db._readonly(base)
    stage = output.parent / ('.'+output.name+'.projection-'+uuid.uuid4().hex)
    db = None
    try:
        next_db._integrity(source)
        helper = next_db._importer()
        signatures = helper.table_signatures(source)
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='next_projection_inputs_archive'").fetchone():
            raise ValueError('A sealed DB cannot be silently resealed')
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='next_classification_inputs'").fetchone():
            raise ValueError('Resealing classified projections requires a separate historical namespace')
        records = next_db._source_records(source)
        parsed_records = {key:SourceRecord.model_validate_json(row['record_json']) for key,row in records.items()}
        for row in records.values():
            raw = next_db._artifact_path(row['raw_path'],next_db.ROOT)
            if _sha(raw)!=row['raw_sha256']:
                raise ValueError('Immutable source raw artifact differs')
            pins[str(raw)] = row['raw_sha256']
        old_alias_metadata = {row['input_hash']:_json(row['metadata_json']) for row in source.execute("SELECT input_hash,metadata_json FROM next_projection_inputs WHERE kind='alias'")}
        scope_members = {(row['canonical_id'],row['source_version_id']) for row in source.execute('SELECT canonical_id,source_version_id FROM works WHERE resolved=1')}
        scoped_evidence = {(row['canonical_id'],row['source_version_id'],row['evidence_id'],_value_sha(_json(row['evidence_json']))) for row in source.execute(
            'SELECT w.canonical_id,e.source_version_id,e.evidence_id,e.evidence_json FROM evidence e JOIN works w ON w.work_id=e.work_id WHERE e.scope_valid=1 AND e.expression_id IS NULL AND w.resolved=1')}
        active = {'bibliographic':[],'classification':[],'alias':[]}
        inventory = []
        used_lineages = set()
        alias_metadata = {}
        for kind,path in files.items():
            seen = set()
            seen_identities = set()
            for position,payload in enumerate(_jsonl(path),1):
                work = WorkInput.model_validate(payload)
                key = input_hash(work)
                identity = (work.work_id,work.expression_id)
                if key in seen or (kind!='alias' and identity in seen_identities):
                    raise ValueError('Duplicate projection input/work expression')
                seen.add(key)
                seen_identities.add(identity)
                lineage = lineages.get((kind,key))
                if lineage is None or type(lineage.get('line_number')) is not int or lineage['line_number']!=position:
                    raise ValueError('Exact ordered projection lineage absent')
                used_lineages.add((kind,key))
                old = by_hash.get((kind,lineage['base_input_hash']))
                if old is None or old.work_id!=work.work_id or work.work_id not in bibliographic:
                    raise ValueError('Projection is not bound to an eligible base input')
                if work.expression_id!=old.expression_id:
                    raise ValueError('Projection expression scope differs')
                if any(getattr(work,field)!=getattr(old,field) for field in next_db.FIELDS):
                    raise ValueError('Projection metadata differs from its exact source input')
                if not work.source_record_ids or len(set(work.source_record_ids))!=len(work.source_record_ids) or not set(work.source_record_ids)<=set(old.source_record_ids):
                    raise ValueError('Projection source versions are not an exact subset')
                if any((work.work_id,version) not in scope_members for version in work.source_record_ids):
                    raise ValueError('Source version does not belong to canonical work scope')
                if lineage.get('excluded_refs')!=[ref for ref in old.source_record_ids if ref not in work.source_record_ids]:
                    raise ValueError('Excluded source reference inventory differs')
                old_evidence = {ev.evidence_id:ev for ev in old.evidence}
                if len(old_evidence)!=len(old.evidence) or len({ev.evidence_id for ev in work.evidence})!=len(work.evidence):
                    raise ValueError('Ambiguous duplicate evidence IDs')
                evidence_keys = {}
                for ev in work.evidence:
                    value = ev.model_dump(mode='json')
                    if ev.evidence_id not in old_evidence or old_evidence[ev.evidence_id].model_dump(mode='json')!=value:
                        raise ValueError('Projection evidence is not an exact immutable subset')
                    versions = sorted(version for version in work.source_record_ids if
                        (work.work_id,version,ev.evidence_id,_value_sha(value)) in scoped_evidence and
                        any(material.model_dump(mode='json')==value for material in parsed_records[version].evidence))
                    if not versions:
                        raise ValueError('Evidence lacks exact source version/work scope')
                    evidence_keys[ev.evidence_id] = versions
                if lineage['source_evidence_keys']!=evidence_keys:
                    raise ValueError('Exact source evidence key lineage differs')
                if kind!='classification' and work.evidence:
                    raise ValueError('Metadata/alias projections must contain no prose')
                excluded = {ev.evidence_id:_value_sha(ev.model_dump(mode='json')) for ev in old.evidence if ev.evidence_id not in evidence_keys}
                removed = lineage.get('excluded_evidence',[])
                if len(removed)!=len(excluded) or {ev['evidence_id']:ev['model_sha256'] for ev in removed}!=excluded or any(not ev.get('reasons') for ev in removed):
                    raise ValueError('Excluded evidence inventory differs')
                if any(any(field in ev and ev[field]!=getattr(old_evidence[ev['evidence_id']],field) for field in ('kind','rights_policy_id')) for ev in removed):
                    raise ValueError('Excluded evidence audit material differs')
                if kind=='alias':
                    metadata = old_alias_metadata[lineage['base_input_hash']]
                    versions = sorted(version for version in metadata['source_version_ids'] if version in work.source_record_ids and
                        parsed_records[version].record_kind=='work' and any(title.value==work.title and title.language==metadata['language'] for title in parsed_records[version].titles))
                    if not versions or metadata.get('role')!='unknown':
                        raise ValueError('Alias title lacks an exact work source assertion')
                    alias_metadata[key] = {**metadata,'source_version_ids':versions}
                receipts = {}
                declared_purposes = lineage['purposes']
                required = 'external_llm' if kind=='classification' else 'display'
                if required not in declared_purposes:
                    raise ValueError('Required input purpose missing')
                for purpose,declared in declared_purposes.items():
                    receipt = _receipt(guard.check_input(work,purpose))
                    if type(declared.get('allowed')) is not bool or receipt['allowed'] is not declared['allowed']:
                        raise ValueError('Purpose declaration differs from actual guard decision')
                    receipts[purpose] = receipt
                admitted = kind=='bibliographic' or receipts[required]['allowed']
                if admitted:
                    active[kind].append(work)
                inventory.append({'kind':kind,'input_hash':key,'work_id':work.work_id,'lineage':lineage,
                                  'purpose_receipts':receipts,'active_projection':admitted})
        if used_lineages!=set(lineages) or {w.work_id for w in active['bibliographic']}!=set(bibliographic):
            raise ValueError('Full inventory/lineage coverage differs')
        output.parent.mkdir(parents=True,exist_ok=True)
        stage.mkdir()
        next_db._backup(source,stage/'dataset.sqlite')
        db = sqlite3.connect(stage/'dataset.sqlite')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE next_projection_inputs_archive AS SELECT * FROM next_projection_inputs;
        CREATE TABLE next_source_refs_archive AS SELECT * FROM next_source_refs;
        CREATE TABLE next_sealed_projection_inventory(kind TEXT NOT NULL,input_hash TEXT NOT NULL,
          work_id TEXT NOT NULL REFERENCES works(work_id),packet_json TEXT NOT NULL,PRIMARY KEY(kind,input_hash));
        CREATE TABLE next_projection_seal(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        DELETE FROM next_source_refs;
        DELETE FROM next_projection_inputs;
        ''')
        packets = []
        with db:
            for item in inventory:
                db.execute('INSERT INTO next_sealed_projection_inventory VALUES(?,?,?,?)',
                           (item['kind'],item['input_hash'],item['work_id'],canonical_json(item)))
            for kind,works in active.items():
                for work in works:
                    key = input_hash(work)
                    metadata = alias_metadata[key] if kind=='alias' else {}
                    packet = next_db._projection_packet(work,kind,metadata) if kind!='classification' else None
                    db.execute('INSERT INTO next_projection_inputs VALUES(?,?,?,?,?,?)',
                        (kind,key,work.work_id,canonical_json(work.model_dump(mode='json')),canonical_json(metadata),canonical_json(packet) if packet else None))
                    for position,version in enumerate(work.source_record_ids):
                        db.execute('INSERT INTO next_source_refs VALUES(?,?,?,?)',(kind,key,position,version))
                    if packet:
                        packets.append(packet)
            counts = Counter(item['kind'] for item in inventory)
            report = {'version':SEALED_PROJECTION_VERSION,'next_database_version':next_db.NEXT_DATABASE_VERSION,
                'state':'purpose_certified_projections_derivation_seal_pending','base_database':str(base),
                'base_database_sha256':pins[str(base)],'projection_manifest_sha256':pins[str(manifest_path)],
                'usage_ledger_path':str(ledger),'usage_ledger_sha256':pins[str(ledger)],'guard_version':GUARD_VERSION,
                'code_sha256':pins[str(Path(__file__).resolve())],'projection_recipe':{'path':str(Path(next_db.__file__).resolve()),'sha256':pins[str(Path(next_db.__file__).resolve())]},
                'bibliographic_work_count':len(active['bibliographic']),
                'bibliographic_display_allowed_count':sum(item['kind']=='bibliographic' and item['purpose_receipts']['display']['allowed'] for item in inventory),
                'classification_candidate_count':len(active['classification']),'classification_adopted_count':0,
                'alias_input_count':len(active['alias']),'candidate_inventory_counts':dict(counts),
                'historical_work_count':source.execute('SELECT count(*) FROM next_book_inventory').fetchone()[0],
                'scope_quarantined_count':source.execute('SELECT count(*) FROM next_book_inventory WHERE eligible=0').fetchone()[0],
                'legacy_classifications_current':False,'old_projection_recipe_current':False,
                'typed_derivation_permission_established':False,'authority_merges':0,'human_verified':False,
                'preserved_table_signatures':{name:value for name,value in signatures.items() if name not in ('next_projection_inputs','next_source_refs')}}
            db.execute('INSERT INTO next_projection_seal VALUES(?,?)',('manifest',canonical_json(report)))
        after = helper.table_signatures(db)
        if any(after[name]!=value for name,value in report['preserved_table_signatures'].items()) or after['next_projection_inputs_archive']!=signatures['next_projection_inputs'] or after['next_source_refs_archive']!=signatures['next_source_refs']:
            raise ValueError('Historical tables/archived projection values changed')
        next_db._integrity(db)
        db.close()
        db = None
        for kind,works in active.items():
            filename = 'aliases' if kind=='alias' else kind
            (stage/(filename+'-inputs.jsonl')).write_text(''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in works),encoding='utf-8')
        (stage/'derivations.jsonl').write_text(''.join(canonical_json(packet)+'\n' for packet in packets),encoding='utf-8')
        (stage/'projection-inventory.jsonl').write_text(''.join(canonical_json(item)+'\n' for item in inventory),encoding='utf-8')
        report['output_sha256'] = {path.name:_sha(path) for path in stage.iterdir()}
        (stage/'manifest.json').write_text(canonical_json(report)+'\n',encoding='utf-8')
        _before_publish()
        if output.exists() or any(_sha(Path(path))!=expected for path,expected in pins.items()) or (wal.exists() and wal.stat().st_size):
            raise ValueError('Source/input/code/ledger changed before publication')
        stage.rename(output)
        return report
    finally:
        source.close()
        if db is not None:
            db.close()
        if stage.exists():
            if stage.parent!=output.parent or not stage.name.startswith('.'+output.name+'.projection-'):
                raise ValueError('Unsafe staging cleanup')
            shutil.rmtree(stage)
