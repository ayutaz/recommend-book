"""Version-three work projections; historical source/identity data stay immutable.

Inspection is an assertion inventory, not rights clearance, human gold or a
merge decision. P1476 published titles never prove original/translation roles.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import shutil
import sqlite3
import uuid
import re
from urllib.parse import urlparse, unquote

from recommend_book.autonomous_database import _artifact_path, _integrity, _scope_sources, _view, _backup, _importer, _verify_scope_quarantine
from recommend_book.classification.validation import ROOT, input_hash, validate_result, load_taxonomy
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.recommendation.overlay import _readonly, _source_records
from recommend_book.recommendation.world import _json, _sha, _value_sha

NEXT_DATABASE_VERSION = '3.2'
FEATURE_ADOPTION_POLICY = 'native-conflict-hold-v1'
FIELDS = ('title', 'authors', 'original_language', 'first_publication_year', 'region', 'series')
RELATIONS = {'P629': 'version_of', 'P747': 'has_version', 'P527': 'has_part', 'P361': 'part_of', 'P179': 'series_member'}


def build_next_input_proposals(source_database):
    """Project all eligible books, without establishing any usage permission.

    Prose removal creates a new input, never a repaired old classification.
    Source references remain complete even for metadata-only projections.
    """
    path = Path(source_database).resolve(strict=True)
    before = _sha(path)
    db = _readonly(path)
    try:
        _integrity(db)
        view = _view(db)
        works = [work for work in view.work_inputs() if work.expression_id is None]
        table = db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_work_eligibility'").fetchone()
        eligible = {row['work_id'] for row in db.execute('SELECT work_id FROM autonomous_work_eligibility WHERE eligible=1')} if table else {w.work_id for w in works}
        if table and db.execute('SELECT count(*) FROM autonomous_work_eligibility').fetchone()[0] != len(works):
            raise ValueError('Mandatory scope coverage differs')
        sources = _scope_sources(db, {w.work_id for w in works})
        result = {'bibliographic': [], 'classification': [], 'aliases': [], 'alias_metadata': {},
                  'historical_work_count': len(works), 'scope_quarantined_count': len(works) - len(eligible)}
        for work in works:
            if work.work_id not in eligible:
                continue
            bibliography = work.model_copy(update={'evidence': []}, deep=True)
            result['bibliographic'].append(bibliography)
            evidence = [ev for ev in work.evidence if ev.llm_input_allowed and
                        ':wikipedia-prose-v2' not in ev.evidence_id and 'wikisource' not in ev.rights_policy_id.casefold()]
            if evidence:
                result['classification'].append(work.model_copy(update={'evidence': evidence}, deep=True))
            by_title = defaultdict(list)
            for row in sources[work.work_id]:
                record = SourceRecord.model_validate_json(row['record_json'])
                if record.record_kind != 'work':
                    continue
                for title in record.titles:
                    if title.language == 'ja' and title.value != bibliography.title:
                        by_title[title.value].append(row['version_id'])
            for title, versions in sorted(by_title.items()):
                alias = bibliography.model_copy(update={'title': title}, deep=True)
                result['aliases'].append(alias)
                result['alias_metadata'][input_hash(alias)] = {'language': 'ja', 'role': 'unknown',
                    'source_version_ids': sorted(set(versions))}
        if _sha(path) != before:
            raise ValueError('Source database changed during projection')
        return result
    finally:
        db.close()


def _metadata_payload(work):
    return {'work_id': work.work_id, **{field: getattr(work, field) for field in FIELDS}}


def _projection_packet(work, kind, metadata=None):
    if kind == 'alias':
        payload = {'work_id': work.work_id, 'title': work.title, 'language': metadata['language'], 'role': 'unknown'}
        material_versions = metadata['source_version_ids']
    else:
        payload = _metadata_payload(work)
        material_versions = list(work.source_record_ids)
    return {'kind': 'title_alias' if kind == 'alias' else 'bibliographic_metadata', 'payload': payload,
            'value_sha256': _value_sha(payload), 'originating_input_hashes': [input_hash(work)],
            'source_version_ids': material_versions,
            'input_json': work.model_dump(mode='json'), 'input_hash': input_hash(work),
            'recipe': {'path': str(Path(__file__).resolve()), 'sha256': _sha(Path(__file__))}}


def _before_next_publish():
    """Fixture hook for detecting source changes at publication."""


def _annotation_bindings(source, relation_path=None, supplementary_path=None):
    """Bind independently reviewed assertions without changing the identity graph."""
    files = {}
    relations = []
    cases = []
    records = _source_records(source)
    def binding(work_id, version, raw_path, raw_sha):
        row = records.get(version)
        if row is None or row['raw_sha256']!=raw_sha or row['raw_path']!=raw_path:
            raise ValueError('Annotation immutable source version/raw binding differs')
        if not source.execute('SELECT 1 FROM works WHERE canonical_id=? AND source_version_id=?',(work_id,version)).fetchone():
            raise ValueError('Annotation source does not belong to subject canonical')
        raw = _artifact_path(raw_path,ROOT)
        if _sha(raw)!=raw_sha:
            raise ValueError('Annotation raw artifact changed')
        files[str(raw)]=raw_sha
        return row,raw
    def raw_claim(raw, locator, assertion):
        value=_json(raw.read_bytes())
        for component in re.findall(r'([^\.\[\]]+)|\[(\d+)\]',locator):
            value=value[component[0]] if component[0] else value[int(component[1])]
        if value!=assertion:
            raise ValueError('Annotation statement differs from raw assertion locator')
    if relation_path:
        path=Path(relation_path).resolve(strict=True)
        files[str(path)]=_sha(path)
        relations=_json(path.read_bytes())
        if not isinstance(relations,list):
            raise ValueError('Relationship annotation list required')
        for relation in relations:
            original_role = relation.get('original_work_identity_verified')
            if relation.get('authority_merge_approved') is not False or relation.get('human_verified') is not False or (original_role is not False and original_role is not None):
                raise ValueError('Metadata annotation cannot assert authority/original identity')
            row,raw=binding(relation['subject_work_id'],relation['subject_source_version_id'],relation['raw_path'],relation['raw_sha256'])
            if row['source']!=relation['source'] or row['source_id']!=relation['source_id']:
                raise ValueError('Annotation source identity differs')
            if row['source']=='wikidata':
                raw_claim(raw,relation['assertion_locator'],relation['assertion_json'])
            elif row['source']=='wikipedia':
                pageprops=re.search(r'query\.pages\[(\d+)\]',relation['assertion_locator'])
                if not pageprops:
                    raise ValueError('Page binding annotation locator absent')
                page=_json(raw.read_bytes())['query']['pages'][int(pageprops[1])]
                assertion=relation['assertion_json']
                if (page['pageid'],page['revisions'][0]['revid'],page.get('pageprops',{}).get('wikibase_item'))!=(assertion['pageid'],assertion['revision_id'],assertion['wikibase_item']):
                    raise ValueError('Page revision/entity annotation differs from raw')
            elif relation['status']!='held':
                raise ValueError('Unverified provider annotations must remain held')
            if relation.get('target_source_version_id') and relation['target_source_version_id'] not in records:
                raise ValueError('Annotation target source version absent')
    if supplementary_path:
        path=Path(supplementary_path).resolve(strict=True)
        files[str(path)]=_sha(path)
        document=_json(path.read_bytes())
        if type(document.get('human_gold')) is not int or document['human_gold']!=0 or type(document.get('new_merges')) is not int or document['new_merges']!=0:
            raise ValueError('Supplementary annotation cannot create merge/human authority')
        cases=document['cases']
        for case in cases:
            if case.get('authority_merge_approved') is not False or case.get('human_verified') is not False or case.get('original_work_identity_verified') is not False:
                raise ValueError('Supplementary source scope is not original identity authority')
            for evidence in case['active_source_evidence']:
                _,raw=binding(case['subject_work_id'],evidence['source_version_id'],evidence['raw_path'],evidence['raw_sha256'])
                for claims in evidence.get('raw_claims',{}).values():
                    for claim in claims:
                        raw_claim(raw,claim['locator'],claim['assertion_json'])
    return relations,cases,files


def build_next_database(*, source_database, output_dir, authority_metadata_relations=None, supplementary_type_audit=None):
    """Create a bibliographic baseline; all historical classifications are audit only.

    Permission is evaluated on reading using a separately sealed usage ledger.
    No classifications are admitted by this baseline construction.
    """
    source = Path(source_database).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError('Refusing existing output')
    if output == source.parent or source.parent in output.parents:
        raise ValueError('Output must be outside source directory')
    source_sha = _sha(source)
    code_sha = _sha(Path(__file__))
    wal = Path(str(source) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Source WAL present; snapshot is not immutable')
    proposals = build_next_input_proposals(source)
    inspection = inspect_next_database(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.' + output.name + '.next-' + uuid.uuid4().hex)
    stage.mkdir()
    db = None
    try:
        original = _readonly(source)
        try:
            relations,scope_annotations,annotation_files=_annotation_bindings(original,authority_metadata_relations,supplementary_type_audit)
            _backup(original, stage / 'dataset.sqlite')
        finally:
            original.close()
        db = sqlite3.connect(stage / 'dataset.sqlite')
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''
        CREATE TABLE next_projection_inputs(
          kind TEXT NOT NULL CHECK(kind IN ('bibliographic','classification','alias')),
          input_hash TEXT NOT NULL, work_id TEXT NOT NULL REFERENCES works(work_id),
          input_json TEXT NOT NULL, metadata_json TEXT NOT NULL, packet_json TEXT,
          PRIMARY KEY(kind,input_hash));
        CREATE TABLE next_source_refs(
          kind TEXT NOT NULL,input_hash TEXT NOT NULL,position INTEGER NOT NULL,
          source_version_id TEXT NOT NULL REFERENCES source_records(version_id),
          PRIMARY KEY(kind,input_hash,position),
          FOREIGN KEY(kind,input_hash) REFERENCES next_projection_inputs(kind,input_hash));
        CREATE TABLE next_book_inventory(
          work_id TEXT PRIMARY KEY REFERENCES works(work_id),eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
          inspection_json TEXT NOT NULL);
        CREATE TABLE next_database_provenance(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        CREATE TABLE next_source_relationship_assertions(assertion_id TEXT PRIMARY KEY,
          subject_work_id TEXT NOT NULL REFERENCES works(work_id),
          subject_source_version_id TEXT NOT NULL REFERENCES source_records(version_id),
          target_work_id TEXT REFERENCES works(work_id),target_source_version_id TEXT REFERENCES source_records(version_id),
          relation_type TEXT NOT NULL,status TEXT NOT NULL,packet_json TEXT NOT NULL);
        CREATE TABLE next_work_scope_annotations(work_id TEXT PRIMARY KEY REFERENCES works(work_id),packet_json TEXT NOT NULL);
        ''')
        for item in inspection['works']:
            db.execute('INSERT INTO next_book_inventory VALUES(?,?,?)', (item['work_id'], int(item['eligible']), canonical_json(item)))
        for relation in relations:
            db.execute('INSERT INTO next_source_relationship_assertions VALUES(?,?,?,?,?,?,?,?)',
                (_value_sha(relation),relation['subject_work_id'],relation['subject_source_version_id'],relation.get('target_work_id'),
                 relation.get('target_source_version_id'),relation['relation_type'],relation['status'],canonical_json(relation)))
        for annotation in scope_annotations:
            db.execute('INSERT INTO next_work_scope_annotations VALUES(?,?)',(annotation['subject_work_id'],canonical_json(annotation)))
        for kind in ('bibliographic', 'classification', 'aliases'):
            saved_kind = 'alias' if kind == 'aliases' else kind
            for work in proposals[kind]:
                key = input_hash(work)
                metadata = proposals['alias_metadata'].get(key, {}) if saved_kind == 'alias' else {}
                packet = _projection_packet(work, saved_kind, metadata) if saved_kind != 'classification' else None
                db.execute('INSERT INTO next_projection_inputs VALUES(?,?,?,?,?,?)',
                    (saved_kind, key, work.work_id, canonical_json(work.model_dump(mode='json')), canonical_json(metadata),
                     canonical_json(packet) if packet else None))
                for index, version in enumerate(work.source_record_ids):
                    db.execute('INSERT INTO next_source_refs VALUES(?,?,?,?)', (saved_kind, key, index, version))
        manifest = {'version': NEXT_DATABASE_VERSION, 'state': 'bibliographic_baseline',
            'source_database': str(source), 'source_database_sha256': source_sha,
            'historical_work_count': proposals['historical_work_count'],
            'bibliographic_work_count': len(proposals['bibliographic']),
            'scope_quarantined_count': proposals['scope_quarantined_count'],
            'classification_candidate_count': len(proposals['classification']), 'classification_adopted_count': 0,
            'alias_input_count': len(proposals['aliases']), 'legacy_classifications_current': False,
            'usage_permission_established': False, 'authority_merges': 0, 'human_verified': False,
            'code_sha256': code_sha, 'source_raw_sha256': inspection['audit']['raw_source_sha256']}
        manifest.update(relationship_assertion_count=len(relations),supplementary_scope_annotation_count=len(scope_annotations),annotation_files_sha256=annotation_files)
        db.execute('INSERT INTO next_database_provenance VALUES(?,?)', ('manifest', canonical_json(manifest)))
        db.commit()
        _integrity(db)
        db.close()
        db = None
        for kind in ('bibliographic', 'classification', 'aliases'):
            (stage / (kind + '-inputs.jsonl')).write_text(''.join(canonical_json(w.model_dump(mode='json')) + '\n' for w in proposals[kind]), encoding='utf-8')
        packets = [_projection_packet(work, 'bibliographic') for work in proposals['bibliographic']]
        packets += [_projection_packet(work, 'alias', proposals['alias_metadata'][input_hash(work)]) for work in proposals['aliases']]
        (stage / 'derivations.jsonl').write_text(''.join(canonical_json(packet) + '\n' for packet in packets), encoding='utf-8')
        manifest['output_sha256'] = {p.name: _sha(p) for p in stage.iterdir()}
        (stage / 'manifest.json').write_text(canonical_json(manifest) + '\n', encoding='utf-8')
        _before_next_publish()
        if output.exists() or _sha(source) != source_sha or _sha(Path(__file__))!=code_sha or any(_sha(Path(p)) != h for p, h in {**manifest['source_raw_sha256'],**annotation_files}.items()):
            raise ValueError('Source/output changed before publication')
        stage.rename(output)
        return manifest
    finally:
        if db is not None:
            db.close()
        if stage.exists():
            if stage.parent != output.parent or not stage.name.startswith('.' + output.name + '.next-'):
                raise ValueError('Unsafe next staging cleanup')
            shutil.rmtree(stage)


def get_next_inputs(path):
    """Return complete purpose projections, never legacy classification inputs."""
    db = _readonly(Path(path).resolve(strict=True))
    try:
        source_records = _source_records(db)
        result = {'bibliographic': [], 'classification': [], 'aliases': []}
        for row in db.execute('SELECT * FROM next_projection_inputs ORDER BY work_id,kind,input_hash'):
            work = WorkInput.model_validate_json(row['input_json'])
            if work.work_id != row['work_id'] or input_hash(work) != row['input_hash']:
                raise ValueError('Next input hash/identity changed')
            refs = [r[0] for r in db.execute('SELECT source_version_id FROM next_source_refs WHERE kind=? AND input_hash=? ORDER BY position', (row['kind'], row['input_hash']))]
            if refs != work.source_record_ids:
                raise ValueError('Next source version references changed')
            if not refs or any(ref not in source_records for ref in refs):
                raise ValueError('Next immutable source refs missing')
            result['aliases' if row['kind'] == 'alias' else row['kind']].append(work)
        return result
    finally:
        db.close()


def _feature_adoption(result):
    """Mechanical adoption status; never human verification or identity proof."""
    conflicts = result['conflicts']
    status = 'held_conflict' if conflicts else ('adopted' if any(
        label['decision']=='present' and label['scope']=='work' for label in result['labels']) else 'abstained')
    return {'policy':FEATURE_ADOPTION_POLICY,'status':status,'reasons':list(conflicts),
            'needs_human_review':result['needs_human_review'],'human_verified':False}


def extract_classification_features(work, result, job_key, run_id):
    features = {}
    provenance = defaultdict(list)
    if _feature_adoption(result)['status']=='held_conflict':
        return {'work_id':work.work_id,'features':{},'feature_provenance':{}}
    for label in result['labels']:
        if label['decision'] != 'present' or label['scope'] != 'work':
            continue
        strength = float(label['relevance']) if label['relevance'] is not None else 1.0
        features[label['label_id']] = max(features.get(label['label_id'], 0.0), strength)
        provenance[label['label_id']].append({'label_id': label['label_id'], 'axis': label['axis'],
            'decision': label['decision'], 'scope': label['scope'], 'expression_id': label['expression_id'],
            'relevance': label['relevance'], 'raw_confidence': label['raw_confidence'],
            'calibrated_confidence': label['calibrated_confidence'], 'calibration_status': label['calibration_status'],
            'calibration_version': label['calibration_version'], 'human_verified': False,
            'raw_confidence_used_as_weight': False, 'feature_strength': strength,
            'null_relevance_policy': 1.0, 'evidence': label['evidence'],
            'input_hash': input_hash(work), 'source_record_ids': work.source_record_ids, 'job_key': job_key, 'run_id': run_id})
    return {'work_id': work.work_id, 'features': features, 'feature_provenance': dict(provenance)}


QUALITY_REVIEW_VERSION = 'next-quality-adoption-review-v1'
QUALITY_REASON_CODES = {'evidence_subject_scope_mismatch','unresolved_version_scope_conflict','semantic_entailment_uncertain'}
QUALITY_FALSE_FLAGS = {'human_gold','authority_merge_approved','confidence_calibrated'}


def _quality_document(path):
    document = _json(path.read_bytes())
    required = {'schema_version','actor','run_id','ordered_inputs_sha256','run_manifest_sha256','results_sha256',
                'execution_context_manifest_sha256','source_audits','cases'} | QUALITY_FALSE_FLAGS
    if set(document)!=required or document['schema_version']!=QUALITY_REVIEW_VERSION or document['actor']!='ai_internal_review' or any(document[key] is not False for key in QUALITY_FALSE_FLAGS):
        raise ValueError('Quality review strict schema/actor/false authority flags required')
    files = {str(path):_sha(path)}
    audits = document['source_audits']
    if not isinstance(audits,list) or not audits:
        raise ValueError('Quality source audits required')
    for binding in audits:
        if set(binding)!={'path','sha256'} or not isinstance(binding['path'],str) or not Path(binding['path']).is_absolute():
            raise ValueError('Quality source audit absolute binding required')
        raw = Path(binding['path']).resolve(strict=True)
        if _sha(raw)!=binding['sha256']:
            raise ValueError('Quality source audit raw SHA differs')
        files[str(raw)] = binding['sha256']
    hashes = {binding['sha256'] for binding in audits}
    if len(hashes)!=len(audits):
        raise ValueError('Quality source audit bindings duplicate')
    cases = {}
    case_fields = {'work_id','input_hash','job_key','native_result_value_sha256','status','reason_codes','reasons',
                   'label_ids','source_audit_sha256'} | QUALITY_FALSE_FLAGS
    if not isinstance(document['cases'],list):
        raise ValueError('Quality cases must be a list')
    for case in document['cases']:
        if set(case)!=case_fields or any(case[key] is not False for key in QUALITY_FALSE_FLAGS):
            raise ValueError('Quality case strict schema/false authority flags required')
        for field in ('work_id','input_hash','job_key','native_result_value_sha256','status'):
            if not isinstance(case[field],str) or not case[field]:
                raise ValueError('Quality case identity strings required')
        for field in ('reason_codes','reasons','label_ids','source_audit_sha256'):
            values=case[field]
            if not isinstance(values,list) or not values or any(not isinstance(value,str) or not value for value in values) or len(set(values))!=len(values):
                raise ValueError('Quality case unique nonempty string lists required')
        reasons=set(case['reason_codes'])
        if not reasons<=QUALITY_REASON_CODES or not set(case['source_audit_sha256'])<=hashes:
            raise ValueError('Quality reason/source audit binding unknown')
        held = bool(reasons-{'semantic_entailment_uncertain'})
        if case['status']!=('held' if held else 'uncertain') or case['job_key'] in cases:
            raise ValueError('Quality whole-result status/duplicate binding invalid')
        cases[case['job_key']] = case
    return document,cases,files


def _quality_case(case,work,result,key):
    if (case['work_id']!=work.work_id or case['input_hash']!=input_hash(work) or case['job_key']!=key or
        case['native_result_value_sha256']!=_value_sha(result) or not set(case['label_ids'])<={label['label_id'] for label in result['labels']}):
        raise ValueError('Quality case does not match exact complete native result/input')


def import_next_classifications(*, source_database, ordered_inputs, run_dir,
        execution_context_manifest, migration_manifest, terminal_proof, cli_profile, output_dir, guard,
        quality_adoption_review=None):
    """Import an authentic new terminal run against exact purpose projections.

    Historical results remain separate; removing held text cannot rebind them.
    Completed and held states retain their original values and source scopes.
    """
    helper = _importer()
    source, inputs, run, context_path, migration_path, proof_path, profile = [Path(p).resolve(strict=True)
        for p in (source_database, ordered_inputs, run_dir, execution_context_manifest, migration_manifest, terminal_proof, cli_profile)]
    output = Path(output_dir).resolve()
    trees = (source.parent, inputs.parent, run, context_path.parent, migration_path.parent, proof_path.parent)
    if output.exists() or any(output.is_relative_to(tree) for tree in trees):
        raise ValueError('Output must be new and outside protected namespaces')
    review_path=Path(quality_adoption_review).resolve(strict=True) if quality_adoption_review else None
    if review_path and output.is_relative_to(review_path.parent):
        raise ValueError('Output must be outside quality review directory')
    protected_paths={source,inputs,inputs.with_name('manifest.json'),context_path,migration_path,proof_path,profile,Path(__file__).resolve()}
    if review_path:
        protected_paths.add(review_path)
    protected = helper._protected(protected_paths,run,context_path)
    before = {str(path): _sha(path) for path in protected}
    run_inventory = helper._inventory(run)
    proof = helper.read_json(proof_path)
    helper.check_proof(proof, source_db=source, ordered_inputs=inputs, run_dir=run,
        execution_context_manifest=context_path, migration_manifest=migration_path, cli_profile=profile)
    if proof['exit_code'] != 0:
        raise ValueError('Successful terminal exit zero required')
    context = helper.read_json(context_path)
    from recommend_book.classification.runner import build_run_config
    from recommend_book.classification.specs import get_spec
    spec_name = context['execution_context']['run_config'].get('execution_spec')
    if not spec_name:
        raise ValueError('Explicit next execution spec required')
    spec = get_spec(spec_name)
    helper.build_run_config = lambda model,version,taxonomy,batch: build_run_config(model,version,taxonomy,batch,execution_spec=spec_name)
    helper.validate_result = spec.validate_result
    config, taxonomy = helper.check_context(context,inputs)
    for relative, digest in context['output_sha256'].items():
        if _sha(helper.contained(context_path.parent,relative)) != digest:
            raise ValueError('Prepared artifact raw copy changed')
    if _sha(helper.contained(context_path.parent,'inputs/ordered.jsonl')) != _sha(inputs) or _sha(helper.contained(context_path.parent,'inputs/manifest.json')) != _sha(inputs.with_name('manifest.json')):
        raise ValueError('Prepared input raw copies differ')
    works = [WorkInput.model_validate(_json(line)) for line in inputs.read_bytes().splitlines() if line.strip()]
    if not works or len({(w.work_id,w.expression_id) for w in works}) != len(works):
        raise ValueError('Selected inputs empty or duplicate')
    candidates = {input_hash(w):w for w in get_next_inputs(source)['classification']}
    for work in works:
        if candidates.get(input_hash(work)) != work or not work.source_record_ids or work.expression_id is not None:
            raise ValueError('Run input differs from exact next content projection')
        if not guard.check_input(work,'external_llm').allowed:
            raise ValueError('New classification input usage denied')
    manifest = helper.read_json(run/'manifest.json')
    expected, durable = helper.check_run(run,manifest,proof,works,config,taxonomy)
    terminal_snapshot=helper.read_json(helper.contained(run,'manifest.'+manifest['run_id']+'.json'))
    if terminal_snapshot!=manifest:
        raise ValueError('Immutable terminal run manifest differs from observed run')
    from recommend_book.classification.native_attempt import verify_native_attempt
    native_descriptors = {}
    by_origin = {input_hash(work):work for work in works}
    for key,result in durable.items():
        state = manifest['jobs'][key]
        histories = [history for history in state.get('history',[]) if history.get('source_manifest_path')]
        if not histories:
            raise ValueError('Typed native source manifest required')
        history = histories[-1]
        descriptor_path = helper.contained(run,history['source_manifest_path'])
        if _sha(descriptor_path)!=history.get('source_manifest_sha256'):
            raise ValueError('Typed native descriptor SHA differs')
        descriptor = helper.read_json(descriptor_path)
        for artifact in descriptor.get('artifacts',{}).values():
            artifact_path=Path(artifact['path']).resolve(strict=True)
            if not artifact_path.is_relative_to(run):
                raise ValueError('Native artifact escapes actual run namespace')
            if helper.contained(run,str(artifact_path.relative_to(run)))!=artifact_path:
                raise ValueError('Native artifact namespace binding differs')
        request=helper.read_json(Path(descriptor['artifacts']['request']['path']))
        origins = descriptor.get('originating_input_hashes',[])
        if any(origin not in by_origin for origin in origins):
            raise ValueError('Native complete batch outside selected next inputs')
        batch = [by_origin[origin] for origin in origins]
        from recommend_book.jobs import job_key
        if request.get('run_id')!=manifest['run_id'] or request.get('job_keys')!=[job_key(origin,config) for origin in origins]:
            raise ValueError('Native request belongs to another actual run or batch')
        verify_native_attempt(descriptor,result,origins,expected_config_sha256=_value_sha(config),works=batch)
        if not guard.check_derived(result,origins,'external_llm',source_manifest=descriptor).allowed:
            raise ValueError('Native result usage adoption denied')
        native_descriptors[key] = descriptor
        for path in [descriptor_path, *(Path(artifact['path']).resolve(strict=True) for artifact in descriptor['artifacts'].values())]:
            before.setdefault(str(path),_sha(path))
    quality_cases = {}
    review_binding = None
    if review_path:
        review,quality_cases,review_files = _quality_document(review_path)
        required_binding={'run_id':manifest['run_id'],'ordered_inputs_sha256':_sha(inputs),
                          'run_manifest_sha256':_sha(run/'manifest.json'),'results_sha256':_sha(run/'results.jsonl'),
                          'execution_context_manifest_sha256':_sha(context_path)}
        if any(review[key]!=value for key,value in required_binding.items()) or not set(quality_cases)<=set(durable):
            raise ValueError('Quality review belongs to another actual terminal run')
        for key,case in quality_cases.items():
            _quality_case(case,expected[key],durable[key],key)
        before.update(review_files)
        review_binding={'path':str(review_path),'sha256':review_files[str(review_path)],'source_audits':review['source_audits']}
    output.parent.mkdir(parents=True,exist_ok=True)
    stage = output.parent / ('.'+output.name+'.classify-'+uuid.uuid4().hex)
    original = _readonly(source)
    target = None
    try:
        _integrity(original)
        _source_records(original)
        signatures = helper.table_signatures(original)
        if any(name.startswith('next_classification_') for name in signatures):
            raise ValueError('Next classification adoption already exists')
        stage.mkdir()
        _backup(original,stage/'dataset.sqlite')
        target = sqlite3.connect(stage/'dataset.sqlite')
        target.execute('PRAGMA foreign_keys=ON')
        for statement in helper.DDL:
            target.execute(statement.replace('classification_','next_classification_'))
        target.execute('CREATE TABLE next_classification_derivations(job_key TEXT PRIMARY KEY REFERENCES next_classification_inputs(job_key),packet_json TEXT NOT NULL)')
        target.execute('CREATE TABLE next_quality_adoption_review(job_key TEXT PRIMARY KEY REFERENCES next_classification_inputs(job_key),packet_json TEXT NOT NULL)')
        class NextWriter:
            def execute(self, sql, parameters=()):
                return target.execute(sql.replace('classification_','next_classification_'),parameters)
        provenance = {'source_files_sha256':before,'new_input_projection':True,'legacy_results_rebound':False,
                      'source_records_preserved':True,'usage_ledger':str(guard.ledger_path) if hasattr(guard,'ledger_path') else 'synthetic-fixture',
                      'quality_adoption_review':review_binding}
        helper.insert_rows(NextWriter(),original,manifest,context,helper.read_json(migration_path),proof,provenance,expected,durable)
        for key,case in quality_cases.items():
            target.execute('INSERT INTO next_quality_adoption_review VALUES(?,?)',(key,canonical_json({'review':review_binding,'case':case})))
        packets = []
        for key,work in expected.items():
            if key not in durable:
                continue
            result_path = helper.contained(run,manifest['jobs'][key]['result_path'])
            payload = extract_classification_features(work,durable[key],key,manifest['run_id'])
            feature_recipe={'path':str(Path(__file__).resolve()),'sha256':_sha(Path(__file__)),
                            'extraction':'work-present-relevance-max-v1','native_result_sha256':_value_sha(durable[key])}
            packet = {'kind':'classification_features','payload':payload,'value_sha256':_value_sha(payload),
                'originating_input_hashes':native_descriptors[key]['originating_input_hashes'],'source_version_ids':work.source_record_ids,
                'features_recipe':feature_recipe,'native_result':durable[key],
                'feature_adoption':_feature_adoption(durable[key]),
                'source_manifest':native_descriptors[key],
                'adoption_provenance':{'type':'native-classification-run','work_id':work.work_id,
                    'input_hash':input_hash(work),'job_key':key,'run_id':manifest['run_id'],
                    'ordered_inputs_path':str(inputs),'ordered_inputs_sha256':_sha(inputs),
                    'execution_context_manifest_path':str(context_path),'execution_context_manifest_sha256':_sha(context_path),
                    'run_manifest_path':str(run/'manifest.json'),'run_manifest_sha256':_sha(run/'manifest.json'),
                    'results_index_path':str(run/'results.jsonl'),'results_index_sha256':_sha(run/'results.jsonl'),
                    'terminal_proof_path':str(proof_path),'terminal_proof_sha256':_sha(proof_path),
                    'result_artifacts':[{'path':str(result_path),'sha256':_sha(result_path)}],
                    'taxonomy_sha256':config['taxonomy_hash'],'config':config}}
            target.execute('INSERT INTO next_classification_derivations VALUES(?,?)',(key,canonical_json(packet)))
            packets.append(packet)
        target.commit()
        _integrity(target)
        copied = helper.table_signatures(target)
        if any(copied.get(name) != digest for name,digest in signatures.items()):
            raise ValueError('Original source/historical tables changed')
        target.close()
        target = None
        report = {'version':NEXT_DATABASE_VERSION,'state':'content_classified_subset','source_database_sha256':_sha(source),
            'selected':len(expected),'completed':len(durable),'held':sum(s['status']=='held' for s in manifest['jobs'].values()),
            'legacy_features_adopted':0,'human_verified':False,'confidence_calibrated':False,'llm_started':False,
            'source_files_sha256':before,'source_files_unchanged':True,'run_id':manifest['run_id']}
        adoption_counts = Counter(packet['feature_adoption']['status'] for packet in packets)
        quality_counts = Counter(case['status'] for case in quality_cases.values())
        report.update(raw_classification_saved_count=len(durable),feature_adopted_work_count=sum(
                          packet['feature_adoption']['status']=='adopted' and quality_cases.get(packet['adoption_provenance']['job_key'],{}).get('status')!='held' for packet in packets),
                      conflict_held_count=adoption_counts['held_conflict'],abstained_work_count=adoption_counts['abstained'],
                      quality_held_count=quality_counts['held'],quality_uncertain_count=quality_counts['uncertain'],
                      feature_adoption_policy=FEATURE_ADOPTION_POLICY,quality_adoption_review=review_binding)
        (stage/'classification-derivations.jsonl').write_text(''.join(canonical_json(p)+'\n' for p in packets),encoding='utf-8')
        report['output_sha256']={p.name:_sha(p) for p in stage.iterdir()}
        (stage/'manifest.json').write_text(canonical_json(report)+'\n',encoding='utf-8')
        _before_next_publish()
        if output.exists() or helper._inventory(run)!=run_inventory or any(_sha(Path(p))!=h for p,h in before.items()):
            raise ValueError('Classification source changed before publication')
        stage.rename(output)
        return report
    finally:
        original.close()
        if target is not None:
            target.close()
        if stage.exists():
            if stage.parent!=output.parent or not stage.name.startswith('.'+output.name+'.classify-'):
                raise ValueError('Unsafe classify staging cleanup')
            shutil.rmtree(stage)


def load_next_world_database(path, *, guard, usage='display', content_classified=False):
    """Read purpose-checked projections. Legacy features are never current.

    Book inventory and purpose-eligible counts intentionally differ. A sealed
    derived-artifact ledger must permit the exact values as well as all origins.
    """
    path = Path(path).resolve(strict=True)
    before = _sha(path)
    projections = get_next_inputs(path)
    db = _readonly(path)
    try:
        _integrity(db)
        inventory = {row['work_id']: row['eligible'] for row in db.execute('SELECT work_id,eligible FROM next_book_inventory')}
        scope_files = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_scope_audit'").fetchone():
            audits=db.execute('SELECT * FROM autonomous_scope_audit').fetchall()
            if len(audits)!=1 or _value_sha(_json(audits[0]['audit_json']))!=audits[0]['audit_sha256']:
                raise ValueError('Mandatory scope audit hash differs')
            _,quarantined,files=_verify_scope_quarantine(db,set(inventory),_json(audits[0]['audit_json']),path.parent)
            if {key for key,value in inventory.items() if not value} != set(quarantined):
                raise ValueError('Next scope inventory differs from raw quarantine')
            scope_files={file:_sha(file) for file in files}
        elif db.execute("SELECT 1 FROM sqlite_master WHERE name='classification_inputs'").fetchone():
            raise ValueError('Mandatory historical scope audit missing')
        bibliographic = projections['bibliographic']
        if {w.work_id for w in bibliographic} != {key for key, value in inventory.items() if value == 1}:
            raise ValueError('Complete bibliographic inventory coverage differs')
        audit = {'version': NEXT_DATABASE_VERSION, 'historical_work_count': len(inventory),
            'bibliographic_work_count': len(bibliographic), 'scope_quarantined_count': sum(not v for v in inventory.values()),
            'scope_quarantine_verified': True, 'purpose_held_count': 0, 'alias_purpose_held_count': 0,
            'content_classified': content_classified, 'legacy_features_adopted': 0,
            'raw_classification_saved_count':0,'feature_adopted_work_count':0,'conflict_held_count':0,'abstained_work_count':0,
            'quality_held_count':0,'quality_uncertain_count':0,
            'feature_adoption_policy':FEATURE_ADOPTION_POLICY,
            'raw_confidence_used_as_weight': False, 'human_verified': False}
        rows = []
        aliases = defaultdict(list)
        content = {}
        if content_classified:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='next_classification_inputs'").fetchone():
                raise ValueError('No independently verified next classification adoption is available')
            taxonomy = load_taxonomy()
            quality_by_job = {}
            quality_documents = {}
            expected_quality_packets = {}
            for saved_run in db.execute('SELECT run_id,provenance_json FROM next_classification_runs'):
                binding = _json(saved_run['provenance_json']).get('quality_adoption_review')
                if not binding:
                    continue
                review_path = Path(binding['path']).resolve(strict=True)
                if _sha(review_path)!=binding['sha256']:
                    raise ValueError('Quality review raw SHA changed')
                review,cases,review_files = _quality_document(review_path)
                if review['run_id']!=saved_run['run_id'] or review['source_audits']!=binding['source_audits']:
                    raise ValueError('Quality run/source audit binding differs')
                scope_files.update({Path(file):digest for file,digest in review_files.items()})
                for key,case in cases.items():
                    if key in quality_by_job:
                        raise ValueError('Quality duplicate actual job binding')
                    quality_by_job[key] = case
                    quality_documents[key] = review
                    expected_quality_packets[key] = {'review':binding,'case':case}
            quality_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='next_quality_adoption_review'").fetchone()
            saved_quality = {row['job_key']:_json(row['packet_json']) for row in db.execute('SELECT * FROM next_quality_adoption_review')} if quality_table else {}
            if saved_quality!=expected_quality_packets:
                raise ValueError('Quality cases differ from immutable full review inventory')
            for saved_result in db.execute("SELECT * FROM next_classification_inputs WHERE status='completed' ORDER BY selected_position"):
                origin = WorkInput.model_validate_json(saved_result['input_json'])
                if input_hash(origin) != saved_result['input_hash'] or origin.work_id != saved_result['work_id']:
                    raise ValueError('Classification input identity/hash changed')
                result = _json(saved_result['result_json'])
                if _value_sha(result) != saved_result['result_sha256']:
                    raise ValueError('Classification result value hash changed')
                from recommend_book.classification.specs import get_spec
                config = _json(db.execute('SELECT config_json FROM next_classification_runs WHERE run_id=?',(saved_result['run_id'],)).fetchone()[0])
                get_spec(config['execution_spec']).validate_result(result,origin,taxonomy)
                expected_labels = {label['label_id']:label for label in result['labels']}
                saved_labels = {label['label_id']:label for label in db.execute('SELECT * FROM next_classification_labels WHERE job_key=?',(saved_result['job_key'],))}
                if set(expected_labels) != set(saved_labels) or any(_json(saved_labels[key]['label_json']) != value or saved_labels[key]['human_verified'] != 0 for key,value in expected_labels.items()):
                    raise ValueError('Classification labels differ from authentic result')
                for key,label in expected_labels.items():
                    if any(saved_labels[key][field]!=label[field] for field in ('axis','decision','scope','expression_id','relevance','raw_confidence','calibrated_confidence','calibration_status','calibration_version')):
                        raise ValueError('Classification label numeric/scope columns differ')
                packet_row = db.execute('SELECT packet_json FROM next_classification_derivations WHERE job_key=?',(saved_result['job_key'],)).fetchone()
                if packet_row is None:
                    raise ValueError('Classification typed derivation absent')
                packet = _json(packet_row[0])
                if packet.get('feature_adoption')!=_feature_adoption(result):
                    raise ValueError('Classification semantic feature adoption differs from authentic result')
                extracted = extract_classification_features(origin,result,saved_result['job_key'],saved_result['run_id'])
                if packet.get('kind') != 'classification_features' or packet.get('payload') != extracted or packet.get('value_sha256') != _value_sha(extracted) or input_hash(origin) not in packet.get('originating_input_hashes',[]):
                    raise ValueError('Classification derivation cannot reproduce exact features')
                native = packet.get('adoption_provenance',{})
                if native.get('input_hash') != input_hash(origin) or native.get('job_key') != saved_result['job_key'] or native.get('run_id') != saved_result['run_id']:
                    raise ValueError('Classification native source binding differs')
                artifact = native.get('result_artifacts',[])
                if len(artifact)!=1 or _sha(Path(artifact[0]['path']))!=artifact[0]['sha256'] or _json(Path(artifact[0]['path']).read_bytes())!=result:
                    raise ValueError('Classification durable raw artifact differs')
                if packet.get('native_result')!=result or packet.get('features_recipe',{}).get('native_result_sha256')!=_value_sha(result):
                    raise ValueError('Classification feature recipe native result differs')
                if saved_result['job_key'] in quality_by_job:
                    case = quality_by_job[saved_result['job_key']]
                    _quality_case(case,origin,result,saved_result['job_key'])
                    review = quality_documents[saved_result['job_key']]
                    expected_binding = {'run_id':native['run_id'],'ordered_inputs_sha256':native['ordered_inputs_sha256'],
                        'run_manifest_sha256':native['run_manifest_sha256'],'results_sha256':native['results_index_sha256'],
                        'execution_context_manifest_sha256':native['execution_context_manifest_sha256']}
                    if any(review[key]!=value for key,value in expected_binding.items()):
                        raise ValueError('Quality source run does not match authentic native provenance')
                content[origin.work_id]=(origin,packet)
            completed_keys = {packet['adoption_provenance']['job_key'] for _,packet in content.values()}
            if not set(quality_by_job)<=completed_keys:
                raise ValueError('Quality review has no completed authentic result')
            statuses = Counter(packet['feature_adoption']['status'] for _,packet in content.values())
            quality_statuses = Counter(case['status'] for case in quality_by_job.values())
            audit.update(raw_classification_saved_count=len(content),conflict_held_count=statuses['held_conflict'],
                         abstained_work_count=statuses['abstained'],quality_held_count=quality_statuses['held'],
                         quality_uncertain_count=quality_statuses['uncertain'])
        for work in projections['aliases']:
            aliases[work.work_id].append(work)
        for work in bibliographic:
            key = input_hash(work)
            saved = db.execute("SELECT * FROM next_projection_inputs WHERE kind='bibliographic' AND input_hash=?", (key,)).fetchone()
            packet = _json(saved['packet_json'])
            expected = _projection_packet(work, 'bibliographic')
            for field in ('kind','payload','value_sha256','originating_input_hashes','source_version_ids','input_json','input_hash'):
                if packet.get(field) != expected[field]:
                    raise ValueError('Bibliographic derivation differs from exact input')
            if not guard.check_input(work, usage).allowed or not guard.check_derived(packet['payload'], [key], usage).allowed:
                audit['purpose_held_count'] += 1
                continue
            row = {**_metadata_payload(work), 'features': {}, 'feature_provenance': {}, 'rating': None,
                'read_status': 'unknown', 'popularity': None, 'author_ids': [], 'series_ids': [],
                'external_ids': [], 'source_urls': [], 'bibliographic_originating_input_hashes': [key],
                'classification_originating_input_hashes': [], 'bibliographic_derivation': packet,
                'classification_derivation': None, 'display_title_aliases': []}
            for alias in aliases[work.work_id]:
                alias_key = input_hash(alias)
                saved_alias = db.execute("SELECT * FROM next_projection_inputs WHERE kind='alias' AND input_hash=?", (alias_key,)).fetchone()
                metadata = _json(saved_alias['metadata_json'])
                alias_packet = _json(saved_alias['packet_json'])
                expected_alias = _projection_packet(alias, 'alias', metadata)
                for field in ('kind','payload','value_sha256','originating_input_hashes','source_version_ids','input_json','input_hash'):
                    if alias_packet.get(field) != expected_alias[field]:
                        raise ValueError('Title alias derivation differs from exact input')
                if not guard.check_input(alias, usage).allowed or not guard.check_derived(alias_packet['payload'], [alias_key], usage).allowed:
                    audit['alias_purpose_held_count'] += 1
                    continue
                row['display_title_aliases'].append({**alias_packet['payload'],
                    'originating_input_hashes': [alias_key], 'source_version_ids': metadata['source_version_ids'],
                    'derivation_packet': alias_packet})
            if content_classified:
                if work.work_id not in content:
                    audit['purpose_held_count'] += 1
                    continue
                origin, classification_packet = content[work.work_id]
                quality_case = quality_by_job.get(classification_packet['adoption_provenance']['job_key'])
                if quality_case and quality_case['status']=='held':
                    continue
                if not guard.check_input(origin,usage).allowed or not guard.check_derived(classification_packet['payload'],classification_packet['originating_input_hashes'],usage,
                        source_manifest=classification_packet['source_manifest'],derivation_recipe=classification_packet['features_recipe']).allowed:
                    audit['purpose_held_count'] += 1
                    continue
                row.update(classification_packet['payload'])
                row['classification_originating_input_hashes']=classification_packet['originating_input_hashes']
                row['classification_derivation']={'kind':'classification','payload':classification_packet['native_result'],
                    'value_sha256':_value_sha(classification_packet['native_result']),
                    'originating_input_hashes':classification_packet['originating_input_hashes'],
                    'source_manifest':classification_packet['source_manifest']}
                row['feature_derivation']=classification_packet
            rows.append(row)
        if _sha(path) != before or any(_sha(file)!=sha for file,sha in scope_files.items()):
            raise ValueError('Next database changed during readonly loading')
        audit['eligible_work_count'] = len(rows)
        audit['work_count'] = len(rows)
        audit['empty_features_count'] = sum(not row['features'] for row in rows)
        audit['feature_adopted_work_count'] = sum(bool(row['features']) for row in rows)
        return {'rows': rows, 'audit': audit}
    finally:
        db.close()


def _statements(entity, prop):
    return [(index, statement) for index, statement in enumerate(entity.get('claims', {}).get(prop, []))
        if statement.get('rank') != 'deprecated']


def _values(entity, prop):
    values = []
    for index, statement in _statements(entity, prop):
        value = statement.get('mainsnak', {}).get('datavalue', {}).get('value')
        if isinstance(value, dict) and isinstance(value.get('id'), str):
            values.append((index, value['id'], statement))
    return values


def _work_record_priority(row):
    record = SourceRecord.model_validate_json(row['record_json'])
    return (record.record_kind != 'work', {'wikidata': 0, 'openlibrary': 1, 'wikipedia': 3}.get(record.source, 2), record.source, record.source_id)


def _metadata_candidates(work, sources, records, enriched, authorities, assertion_index):
    fields = {}
    for field in FIELDS:
        value = getattr(work, field)
        candidates = []
        for row, record in zip(sources, records):
            if record.record_kind != 'work':
                continue
            extra_author_versions = []
            if field == 'title':
                matched = value in [t.value for t in record.titles]
                locator = 'SourceRecord.titles'
            elif field == 'authors':
                represented = enriched.get(row['version_id'], record)
                matched = list(dict.fromkeys(c.name for c in represented.contributors if c.role == 'author')) == value
                locator = 'SourceRecord.contributors[role=author]'
                if matched:
                    ids = {(i.source, i.value) for c in represented.contributors if c.role == 'author' for i in c.identifiers if i.scope == 'agent'}
                    extra_author_versions = [version for version, authority in authorities if
                        any((i.source, i.value) in ids for i in authority.identifiers if i.scope == 'agent')]
            else:
                matched = getattr(record, field) == value
                locator = 'SourceRecord.' + field
            if not matched or value in (None, [], ''):
                continue
            assertions = assertion_index.get((row['version_id'], 'contributors' if field == 'authors' else 'titles' if field == 'title' else field), [])
            candidates.append({'source': row['source'], 'source_id': row['source_id'], 'source_version_id': row['version_id'],
                'record_kind': record.record_kind, 'record_payload_sha256': row['payload_sha256'], 'raw_path': row['raw_path'],
                'raw_sha256': row['raw_sha256'], 'source_url': row['source_url'], 'rights_policy_id': record.rights_policy_id,
                'material_field': 'bibliographic', 'locator': locator, 'field_assertions': assertions,
                'author_authority_source_version_ids': extra_author_versions,
                'imported_bibliography_from': record.extra.get('imported_bibliography_from'),
                'current_usage_permission_established': False})
        fields[field] = {'value': value, 'value_sha256': _value_sha(value), 'unknown': value in (None, [], ''), 'candidates': candidates}
    return {'work_id': work.work_id, 'historical_input_hash': input_hash(work),
        'source_record_ids': work.source_record_ids, 'fields': fields, 'candidate_identity_is_not_permission': True}


def inspect_next_database(source_database):
    """Read every current canonical work, retaining scope and raw assertions."""
    path = Path(source_database).resolve(strict=True)
    before = _sha(path)
    db = _readonly(path)
    raw_cache = {}
    raw_sha = {}
    try:
        _integrity(db)
        all_records = _source_records(db)
        view = _view(db)
        works = [w for w in view.work_inputs() if w.expression_id is None]
        active = _scope_sources(db, {w.work_id for w in works})
        enriched = {version: record for version, record in view._active_records().values()}
        authorities = [(version, record) for version, record in view._source_records() if record.record_kind == 'agent']
        assertion_index = defaultdict(list)
        for row in db.execute('SELECT source_version_id,assertion_id,field_name,value_json,rights_policy_id,source_url FROM field_assertions ORDER BY assertion_id'):
            assertion_index[(row['source_version_id'], row['field_name'])].append({k: row[k] for k in row.keys() if k != 'source_version_id'})
        eligible_table = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_work_eligibility'").fetchone())
        eligibility = {r['work_id']: bool(r['eligible']) for r in db.execute('SELECT work_id,eligible FROM autonomous_work_eligibility')} if eligible_table else {w.work_id: True for w in works}
        if set(eligibility) != {w.work_id for w in works}:
            raise ValueError('Historical eligibility ledger coverage differs')
        qid_to_canonical = defaultdict(set)
        for work_id, rows in active.items():
            for row in rows:
                record = SourceRecord.model_validate_json(row['record_json'])
                for identity in record.identifiers:
                    if identity.source == 'wikidata' and identity.scope == 'work':
                        qid_to_canonical[identity.value].add(work_id)
        inspected = []
        metadata = []
        relation_counts = Counter()
        kind_counts = Counter()
        scope_counts = Counter()
        title_role_counts = Counter()
        for work in works:
            sources = sorted(active[work.work_id], key=_work_record_priority)
            records = [SourceRecord.model_validate_json(r['record_json']) for r in sources]
            kinds = [r.work_kind for r in records if r.record_kind == 'work' and r.work_kind]
            kind = kinds[0] if kinds else None
            kind_counts[kind or 'unknown'] += 1
            aliases = []
            relationships = []
            forms = []
            for row, record in zip(sources, records):
                scope_counts[record.source + ':' + record.record_kind] += 1
                for index, title in enumerate(record.titles):
                    title_role_counts[title.kind] += 1
                    aliases.append({'value': title.value, 'language': title.language,
                        'assertion_type': 'legacy_normalized_title', 'reported_role': title.kind,
                        'original_role_established': False, 'source_version_id': row['version_id'],
                        'source_url': record.source_url, 'raw_sha256': record.raw_sha256,
                        'rights_policy_id': record.rights_policy_id, 'locator': f'SourceRecord.titles[{index}]'})
                if record.source != 'wikidata':
                    continue
                raw = _artifact_path(record.raw_path, path.parent)
                if raw not in raw_cache:
                    raw_sha[raw] = _sha(raw)
                    if raw_sha[raw] != record.raw_sha256:
                        raise ValueError('Wikidata raw SHA differs from immutable source record')
                    raw_cache[raw] = _json(raw.read_bytes())
                if raw_sha[raw] != record.raw_sha256:
                    raise ValueError('Wikidata raw SHA differs from immutable source record')
                entity = raw_cache[raw].get('entities', {}).get(record.source_id)
                if not isinstance(entity, dict) or entity.get('id') != record.source_id:
                    raise ValueError('Wikidata raw entity binding differs')
                base = {'source_version_id': row['version_id'], 'source_url': record.source_url,
                    'raw_sha256': record.raw_sha256, 'raw_path': record.raw_path, 'rights_policy_id': record.rights_policy_id}
                for language, label in entity.get('labels', {}).items():
                    aliases.append(dict(base, value=label['value'], language=label.get('language', language),
                        assertion_type='source_label', original_role_established=False,
                        locator=f'entities.{record.source_id}.labels.{language}'))
                for language, labels in entity.get('aliases', {}).items():
                    for index, label in enumerate(labels):
                        aliases.append(dict(base, value=label['value'], language=label.get('language', language),
                            assertion_type='source_alias', original_role_established=False,
                            locator=f'entities.{record.source_id}.aliases.{language}[{index}]'))
                for index, statement in _statements(entity, 'P1476'):
                    value = statement.get('mainsnak', {}).get('datavalue', {}).get('value')
                    if isinstance(value, dict) and isinstance(value.get('text'), str):
                        aliases.append(dict(base, value=value['text'], language=value.get('language'),
                            assertion_type='published_title_candidate', original_role_established=False,
                            locator=f'entities.{record.source_id}.claims.P1476[{index}]', assertion_json=statement))
                for prop in ('P31', 'P7937'):
                    for index, target, statement in _values(entity, prop):
                        forms.append(dict(base, class_id=target, locator=f'entities.{record.source_id}.claims.{prop}[{index}]', assertion_json=statement))
                for prop, relation_type in RELATIONS.items():
                    for index, target, statement in _values(entity, prop):
                        target_ids = sorted(qid_to_canonical.get(target, set()))
                        relationships.append(dict(base, relation_type=relation_type, target_external_id=target,
                            target_work_id=target_ids[0] if len(target_ids) == 1 else None,
                            locator=f'entities.{record.source_id}.claims.{prop}[{index}]', assertion_json=statement,
                            identity_merge_allowed=False, relation_target_identified=len(target_ids) == 1))
                        relation_counts[relation_type] += 1
            metadata.append(_metadata_candidates(work, sources, records, enriched, authorities, assertion_index))
            inspected.append({'work_id': work.work_id, 'historical_input_hash': input_hash(work),
                'eligible': eligibility[work.work_id], 'normalized_kind': kind, 'kind_conflict': len(set(kinds)) > 1,
                'kind_assertions': forms, 'title_assertions': aliases,
                'japanese_display_candidates': [a for a in aliases if a['language'] == 'ja'],
                'original_title': None, 'original_title_role_verified': False,
                'source_record_ids': work.source_record_ids, 'relationships': relationships,
                'expression_ids': [r[0] for r in db.execute('SELECT expression_id FROM expressions WHERE work_id=? ORDER BY expression_id', (work.work_id,))],
                'edition_ids': [r[0] for r in db.execute('SELECT DISTINCT edition_id FROM edition_contents WHERE work_id=? ORDER BY edition_id', (work.work_id,))]})
        unknown = {field: sum(getattr(w, field) in (None, [], '') for w in works) for field in FIELDS}
        eligible_unknown = {field: sum(getattr(w, field) in (None, [], '') for w in works if eligibility[w.work_id]) for field in FIELDS}
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        table_counts = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in names}
        if _sha(path) != before or any(_sha(p) != digest for p, digest in raw_sha.items()):
            raise ValueError('Source database/raw changed during complete inspection')
        audit = {'version': 'next-database-inspection-v3', 'source_database_sha256': before,
            'historical_work_count': len(works), 'eligible_work_count': sum(eligibility.values()),
            'scope_hold_count': sum(not v for v in eligibility.values()), 'kind_counts': dict(kind_counts),
            'kind_conflict_count': sum(w['kind_conflict'] for w in inspected), 'source_record_scope_counts': dict(scope_counts),
            'unknown_fields': unknown, 'eligible_unknown_fields': eligible_unknown,
            'raw_relationship_counts': dict(relation_counts), 'legacy_title_role_counts': dict(title_role_counts),
            'works_with_japanese_display_candidates': sum(bool(w['japanese_display_candidates']) for w in inspected),
            'original_title_role_policy': 'unknown; P1476 published titles and label languages do not establish original/translation role',
            'series_identity_policy': 'explicit source IDs; names alone do not unify series',
            'table_counts': table_counts, 'raw_source_sha256': {str(p): h for p, h in raw_sha.items()},
            'database_written': False, 'source_unchanged': True, 'identity_merges': 0, 'human_verified': False}
        return {'audit': audit, 'works': inspected, 'metadata_lineage': metadata}
    finally:
        db.close()


def write_next_inspection(source_database, output_dir):
    source = Path(source_database).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists() or output.is_relative_to(source.parent):
        raise ValueError('Inspection output must be new and outside source directory')
    result = inspect_next_database(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ('.' + output.name + '.inspection-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        for name, value in [('work-inspection.jsonl', result['works']), ('metadata-lineage-candidates.jsonl', result['metadata_lineage'])]:
            (stage / name).write_text(''.join(canonical_json(row) + '\n' for row in value), encoding='utf-8')
        report = dict(result['audit'], output_sha256={p.name: _sha(p) for p in stage.iterdir()},
            code_sha256=_sha(Path(__file__).resolve()), identity_relation_authority_approved=False,
            usage_permission_established=False)
        (stage / 'manifest.json').write_text(canonical_json(report) + '\n', encoding='utf-8')
        if _sha(source) != report['source_database_sha256'] or any(_sha(Path(p)) != h for p, h in report['raw_source_sha256'].items()):
            raise ValueError('Inspection source/raw changed before publication')
        if output.exists():
            raise ValueError('Inspection output must be new')
        stage.rename(output)
        return report
    finally:
        if stage.exists():
            if stage.parent != output.parent or not stage.name.startswith('.' + output.name + '.inspection-'):
                raise ValueError('Unsafe inspection cleanup')
            shutil.rmtree(stage)


def _ncode_url(value):
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('ncode.syosetu.com', 'novel18.syosetu.com') or parsed.username or parsed.password:
        return None
    match = re.fullmatch(r'/(n[0-9]{4}[a-z]{1,2})/?', unquote(parsed.path).lower())
    return match.group(1) if match else None


def inspect_rating_world_binding(world_database, rating_database, output_dir):
    """Private exact identifier intersection. Never reads API genre/body/cache."""
    world_path = Path(world_database).resolve(strict=True)
    rating_path = Path(rating_database).resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists() or any(output.is_relative_to(p.parent) for p in (world_path, rating_path)):
        raise ValueError('Binding report must use a new separate private namespace')
    world_sha, rating_sha = _sha(world_path), _sha(rating_path)
    world = _readonly(world_path)
    owned = sqlite3.connect(rating_path.as_uri() + '?mode=ro&immutable=1', uri=True)
    owned.row_factory = sqlite3.Row
    owned.execute('PRAGMA query_only=ON')
    raw_cache, raw_hashes = {}, {}
    try:
        ratings = [dict(r) for r in owned.execute('''WITH ranked AS (
            SELECT o.ncode,o.title,o.my_rating,o.snapshot_id,i.observed_at,i.imported_at,
              ROW_NUMBER() OVER(PARTITION BY o.ncode ORDER BY i.observed_at DESC,i.imported_at DESC,i.snapshot_id DESC) AS rank
            FROM rating_observations o JOIN imports i USING(snapshot_id))
            SELECT ncode,title,my_rating,snapshot_id,observed_at,imported_at FROM ranked WHERE rank=1 ORDER BY ncode''')]
        private = {}
        for row in ratings:
            code = row['ncode'].lower()
            if not re.fullmatch(r'n[0-9]{4}[a-z]{1,2}', code) or code in private or type(row['my_rating']) is not int or not 1 <= row['my_rating'] <= 5:
                raise ValueError('Owned rating identity/value contract invalid')
            private[code] = row
        works = [w for w in _view(world).work_inputs() if w.expression_id is None]
        active = _scope_sources(world, {w.work_id for w in works})
        has_eligibility = bool(world.execute("SELECT 1 FROM sqlite_master WHERE name='autonomous_work_eligibility'").fetchone())
        eligible = {r['work_id']: bool(r['eligible']) for r in world.execute('SELECT work_id,eligible FROM autonomous_work_eligibility')} if has_eligibility else {w.work_id: True for w in works}
        candidates = []
        for work in works:
            for row in active[work.work_id]:
                record = SourceRecord.model_validate_json(row['record_json'])
                if record.source != 'wikidata':
                    continue
                raw = _artifact_path(record.raw_path, world_path.parent)
                if raw not in raw_cache:
                    raw_hashes[raw] = _sha(raw)
                    if raw_hashes[raw] != record.raw_sha256:
                        raise ValueError('Identifier raw SHA binding differs')
                    raw_cache[raw] = _json(raw.read_bytes())
                if raw_hashes[raw] != record.raw_sha256:
                    raise ValueError('Identifier raw SHA binding differs')
                entity = raw_cache[raw].get('entities', {}).get(record.source_id)
                if not entity or entity.get('id') != record.source_id:
                    raise ValueError('Identifier Wikidata raw binding differs')
                for prop in ('P856', 'P953'):
                    for index, statement in _statements(entity, prop):
                        value = statement.get('mainsnak', {}).get('datavalue', {}).get('value')
                        code = _ncode_url(value)
                        if code is None:
                            continue
                        reasons = []
                        if code not in private: reasons.append('not_in_owned_rating_snapshot')
                        if not eligible[work.work_id]: reasons.append('historical_work_scope_quarantined')
                        if record.record_kind != 'work': reasons.append('source_is_not_work_scope')
                        if _values(entity, 'P629') or any(target == 'Q3331189' for _, target, _ in _values(entity, 'P31')): reasons.append('explicit_version_edition_translation_scope')
                        if record.volume or _values(entity, 'P179'): reasons.append('series_or_volume_serial_identity_unestablished')
                        if record.original_language != 'ja' or {target for _, target, _ in _values(entity, 'P407')} != {'Q5287'}: reasons.append('original_japanese_web_work_scope_unestablished')
                        forms = {target for prop in ('P31', 'P7937') for _, target, _ in _values(entity, prop)}
                        expected_form = {'novel': 'Q8261', 'short_story': 'Q49084', 'light_novel': 'Q747381'}.get(record.work_kind)
                        if expected_form is None or expected_form not in forms: reasons.append('literary_work_form_unestablished')
                        if statement.get('qualifiers'): reasons.append('website_qualified_scope_requires_review')
                        if any(c.role == 'translator' for c in record.contributors): reasons.append('translated_expression_scope_requires_review')
                        candidates.append({'work_id': work.work_id, 'ncode': code, 'status': 'held' if reasons else 'verified',
                            'reasons': reasons, 'source_version_id': row['version_id'], 'record_payload_sha256': row['payload_sha256'],
                            'wikidata_id': record.source_id, 'raw_path': str(raw), 'raw_sha256': record.raw_sha256,
                            'assertion_locator': f'entities.{record.source_id}.claims.{prop}[{index}]', 'assertion_json': statement,
                            'original_language': record.original_language, 'work_kind': record.work_kind,
                            'personal_rating_origin': private.get(code), 'identity_merge_allowed': False,
                            'title_equality_used': False, 'narou_api_annotation_used': False})
        by_code = defaultdict(set)
        for candidate in candidates:
            by_code[candidate['ncode']].add(candidate['work_id'])
        for candidate in candidates:
            if len(by_code[candidate['ncode']]) != 1:
                candidate['status'] = 'held'
                candidate['reasons'].append('same_external_ncode_multiple_canonical_work_entities')
        # One observed rating is never multiplied by repeat website statements.
        verified = {(c['ncode'], c['work_id']): c for c in candidates if c['status'] == 'verified'}
        report = {'version': 'owned-rating-world-external-binding-v3', 'private_local_only': True,
            'world_database_sha256': world_sha, 'rating_database_sha256': rating_sha,
            'historical_world_work_count': len(works), 'owned_ratings_count': len(ratings),
            'explicit_ncode_candidate_count': len(candidates), 'intersecting_owned_ncodes': len({c['ncode'] for c in candidates if c['ncode'] in private}),
            'verified_binding_count': len(verified), 'held_candidate_count': sum(c['status'] == 'held' for c in candidates),
            'held_reason_counts': dict(Counter(reason for c in candidates for reason in c['reasons'])),
            'title_only_binding_count': 0, 'narou_api_metadata_read': False, 'narou_api_annotations_read': False,
            'original_rating_values_changed': False, 'new_ratings_created': 0, 'identity_merges': 0,
            'source_raw_sha256': {str(p): h for p, h in raw_hashes.items()},
            'source_bytes_unchanged': True, 'new_llm_requests': 0, 'human_world_preference_accuracy_verified': False}
        if _sha(world_path) != world_sha or _sha(rating_path) != rating_sha or any(_sha(p) != h for p, h in raw_hashes.items()):
            raise ValueError('Private owned/world source changed during binding inspection')
        output.parent.mkdir(parents=True, exist_ok=True)
        stage = output.parent / ('.' + output.name + '.binding-' + uuid.uuid4().hex)
        stage.mkdir()
        try:
            (stage / 'candidates.jsonl').write_text(''.join(canonical_json(c) + '\n' for c in candidates), encoding='utf-8')
            (stage / 'bindings.jsonl').write_text(''.join(canonical_json(c) + '\n' for c in verified.values()), encoding='utf-8')
            (stage / 'owned-rating-origin.jsonl').write_text(''.join(canonical_json(r) + '\n' for r in ratings), encoding='utf-8')
            report['output_sha256'] = {p.name: _sha(p) for p in stage.iterdir()}
            (stage / 'manifest.json').write_text(canonical_json(report) + '\n', encoding='utf-8')
            if output.exists() or _sha(world_path) != world_sha or _sha(rating_path) != rating_sha:
                raise ValueError('Private binding source/output changed before publication')
            stage.rename(output)
            return report
        finally:
            if stage.exists():
                if stage.parent != output.parent or not stage.name.startswith('.' + output.name + '.binding-'):
                    raise ValueError('Unsafe private binding staging cleanup')
                shutil.rmtree(stage)
    finally:
        world.close()
        owned.close()
