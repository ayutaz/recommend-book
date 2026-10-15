"""Verified changed-only work overlay; no database or artifact writes.

Default work recommendation consumes original (expression_id=None) snapshots.
Canonical changes require a complete SQL-backed one-to-one crosswalk. Changed
features are replaced, never unioned with legacy labels. Terminal proof is an
external successful process observation (exit_code=0), not human semantic truth.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PureWindowsPath
import sqlite3

from recommend_book.classification.runner import build_run_config
from recommend_book.classification.validation import ROOT, VALIDATION_VERSION, input_hash, load_taxonomy, validate_result
from recommend_book.database import DatasetDB
from recommend_book.jobs import canonical_json,job_key
from recommend_book.models import Evidence,WorkInput
from .world import NULL_RELEVANCE_STRENGTH,POLICY_VERSION,_json,_sha,_value_sha,load_world_database

ARTIFACTS=('config/sources.json','config/taxonomy.json','schemas/classification.schema.json',
    'src/recommend_book/classification/prompt.py','src/recommend_book/classification/validation.py',
    'src/recommend_book/classification/runner.py','src/recommend_book/models.py',
    'src/recommend_book/jobs.py','pyproject.toml','uv.lock')
PRIVATE_FIELDS=('rating','read_status','popularity')


def _contained(root,relative):
    if not isinstance(relative,str) or not relative or '\x00' in relative:raise ValueError('Invalid relative artifact path')
    win=PureWindowsPath(relative);normal=relative.replace('\\','/')
    if win.drive or win.root or Path(normal).is_absolute() or '..' in normal.split('/'):
        raise ValueError('Artifact drive/root/parent traversal forbidden')
    try:path=(root/normal).resolve(strict=True)
    except OSError as exc:raise ValueError('Artifact missing') from exc
    if not path.is_relative_to(root):raise ValueError('Artifact escapes namespace')
    return path


def _source_path(parent,value):
    if not isinstance(value,str) or not value:raise ValueError('Source path absent')
    path=Path(value)
    return path.resolve(strict=True) if path.is_absolute() else _contained(parent,value)


def _readonly(path):
    _no_wal(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    if db.execute('PRAGMA user_version').fetchone()[0]!=3:
        db.close();raise ValueError('Expected source bibliographic schema 3')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        db.close();raise ValueError('Database foreign key violation')
    return db


def _no_wal(path):
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:raise ValueError('Immutable source has nonempty WAL')


def _inputs(path):
    lines=path.read_bytes().splitlines()
    if any(not line.strip() for line in lines):raise ValueError('Empty input line')
    works=[WorkInput.model_validate(_json(line)) for line in lines]
    identities=[(w.work_id,w.expression_id) for w in works]
    if len(set(identities))!=len(works) or any(w.expression_id is not None for w in works):
        raise ValueError('Original work snapshot requires unique work IDs and null expression IDs')
    return works


def _reconstruct(db):
    view=DatasetDB.__new__(DatasetDB);view.connection=db
    return {w.work_id:w for w in view.work_inputs() if w.expression_id is None}


def _source_records(db):
    records={row['version_id']:dict(row) for row in db.execute('SELECT * FROM source_records')}
    for row in records.values():
        payload=_json(row['record_json'])
        expected=_value_sha({k:v for k,v in payload.items() if k!='retrieved_at'})
        if row['payload_sha256']!=expected or payload['raw_sha256']!=row['raw_sha256']:
            raise ValueError('Immutable source raw JSON/payload SHA differs')
    return records


def _crosswalk(old_db,new_db,rows,old_ids,new_ids):
    before=defaultdict(set);after={}
    for row in old_db.execute('SELECT work_id,canonical_id FROM works WHERE resolved=1'):
        before[row['canonical_id']].add(row['work_id'])
    for row in new_db.execute('SELECT work_id,canonical_id FROM works WHERE resolved=1'):
        after[row['work_id']]=row['canonical_id']
    if set(before)!=old_ids or set(after.values())!=new_ids:raise ValueError('Canonical SQL/input sets differ')
    mapping={}
    for row in rows:
        old=row['old_canonical_id'];targets=row['new_canonical_ids'];nodes=row['old_work_node_ids']
        if old in mapping or old not in before or not isinstance(targets,list) or len(targets)!=1:
            raise ValueError('Canonical crosswalk must be complete one-to-one')
        if not isinstance(nodes,list) or len(set(nodes))!=len(nodes) or set(nodes)!=before[old]:
            raise ValueError('Crosswalk old work nodes differ from original SQL graph')
        actual={after.get(node) for node in nodes}
        if actual!={targets[0]} or targets[0] not in new_ids:
            raise ValueError('Old work nodes are absent/split/wrongly mapped in enriched SQL graph')
        mapping[old]=targets[0]
    if set(mapping)!=old_ids or len(set(mapping.values()))!=len(mapping) or set(mapping.values())!=new_ids:
        raise ValueError('Canonical partition has merged/split/omitted works')
    return mapping


def _proof(proof,paths,run):
    if (proof.get('schema_version')!='classification-terminal-proof-v1' or
        proof.get('process_exit_observed') is not True or type(proof.get('exit_code')) is not int or
        proof.get('exit_code')!=0 or
        not isinstance(proof.get('session_id'),str) or not proof['session_id'].strip() or proof.get('run_id')!=run.get('run_id')):
        raise ValueError('Observed successful terminal process proof required')
    try:
        observed=datetime.fromisoformat(proof['observed_at'].replace('Z','+00:00'))
        if observed.utcoffset() is None or observed.utcoffset().total_seconds()!=0:raise ValueError()
    except (KeyError,AttributeError,TypeError,ValueError) as exc:raise ValueError('UTC observation required') from exc
    checks={'manifest_sha256':'run_manifest','results_sha256':'results','ordered_inputs_sha256':'delta_inputs',
        'execution_context_manifest_sha256':'delta_manifest','cli_profile_sha256_before':'delta_manifest',
        'cli_profile_sha256_after':'delta_manifest'}
    if any(proof.get(key)!=_sha(paths[name]) for key,name in checks.items()):raise ValueError('Terminal proof raw SHA differs')
    extras=proof.get('artifact_sha256',{})
    if any(extras.get(name)!=_sha(paths[name]) for name in ('base_database','enriched_database','canonical_crosswalk','input_comparison')):
        raise ValueError('Terminal proof source artifact SHA differs')


def _run(paths,manifest,selected):
    run=_json(paths['run_manifest'].read_bytes());proof=_json(paths['terminal_proof'].read_bytes())
    _proof(proof,paths,run)
    config=manifest['execution_context']['run_config'];taxonomy=load_taxonomy()
    if VALIDATION_VERSION!='2.0' or config!=build_run_config(config['model'],config['cli_version'],taxonomy,config['batch_size']):
        raise ValueError('Current v2 run config/validator hash differs')
    artifacts=manifest['execution_context']['artifact_sha256']
    if set(artifacts)!=set(ARTIFACTS) or any(artifacts[name]!=_sha(ROOT/name) for name in ARTIFACTS):
        raise ValueError('Current execution artifact hashes differ')
    for name in ARTIFACTS:
        if _sha(paths['artifact:'+name])!=artifacts[name]:raise ValueError('Copied execution artifact bytes differ')
    count=len(selected)
    for field,value in {'selected':count,'completed':count,'failed':0,'pending':0}.items():
        if type(run.get(field)) is not int or run[field]!=value:raise ValueError('All changed inputs must be completed, terminal and count-exact')
    if run.get('stopped') is not False or run.get('human_quality_verified') is not False or run.get('config')!=config:
        raise ValueError('Stopped/unverified config mismatch')
    expected={job_key(input_hash(w),config):w for w in selected}
    if set(run['jobs'])!=set(expected):raise ValueError('Delta job keys differ')
    lines=paths['results'].read_bytes().splitlines()
    if any(not line.strip() for line in lines):raise ValueError('Empty result line')
    results=[_json(line) for line in lines]
    if len(results)!=count:raise ValueError('All selected results required')
    directory=paths['run_manifest'].parent
    for (key,work),result in zip(expected.items(),results):
        state=run['jobs'][key]
        if state.get('status')!='completed' or state.get('lease_until') is not None:raise ValueError('Unfinished delta job')
        for field,value in {'key':key,'work_id':work.work_id,'expression_id':work.expression_id,
            'input_hash':input_hash(work),'config':config}.items():
            if field not in state or state[field]!=value:raise ValueError('Delta job identity/hash/config differs')
        if _json(_contained(directory,'jobs/'+key+'.json').read_bytes())!=state:raise ValueError('Durable job/index differ')
        path=_contained(directory,state.get('result_path'))
        if path!=(directory/'results'/(key+'.json')).resolve(strict=True):raise ValueError('Wrong delta job result path')
        if _json(path.read_bytes())!=result:raise ValueError('Durable delta result/index differ')
        validate_result(result,work,taxonomy)
        if result['model']!=config['model'] or result['cli_version']!=config['cli_version']:
            raise ValueError('Delta result model/CLI differs')
    return run,results


def _project(db,work,result,run_id,key,source_records):
    """Projection mirrors world-work-features-v1 without synthetic DB tables."""
    features={};provenance={};excluded=Counter();nulls=0
    evidence={e.evidence_id:e for e in work.evidence}
    for label in result['labels']:
        if label['decision']!='present':continue
        if label['scope']!='work':excluded[label['scope']]+=1;continue
        strength=float(label['relevance']) if label['relevance'] is not None else NULL_RELEVANCE_STRENGTH
        if label['relevance'] is None:nulls+=1
        feature=label['label_id'];features[feature]=strength
        provenance[feature]=[{'job_key':key,'run_id':run_id,'input_hash':input_hash(work),
            'source_record_ids':list(work.source_record_ids),'label_id':feature,'axis':label['axis'],
            'decision':label['decision'],'scope':label['scope'],'input_expression_id':work.expression_id,
            'expression_id':label['expression_id'],'target_language':result['target_language'],
            'relevance':label['relevance'],'feature_strength':strength,'null_relevance_policy':NULL_RELEVANCE_STRENGTH,
            'raw_confidence':label['raw_confidence'],'raw_confidence_used_as_weight':False,
            'calibration_status':label['calibration_status'],'human_verified':False,
            'evidence':[{'evidence_id':ref['evidence_id'],'locator':ref['locator'],
                'source_url':evidence[ref['evidence_id']].source_url,'kind':evidence[ref['evidence_id']].kind,
                'rights_policy_id':evidence[ref['evidence_id']].rights_policy_id,
                'llm_input_allowed':evidence[ref['evidence_id']].llm_input_allowed,
                'language':evidence[ref['evidence_id']].language,'directness':ref['directness']} for ref in label['evidence']]}]
    authors=[r[0] for r in db.execute('''SELECT DISTINCT c.agent_id FROM contributions c JOIN agents a ON a.agent_id=c.agent_id
        JOIN works w ON w.work_id=c.work_id WHERE w.resolved=1 AND w.canonical_id=? AND c.role='author'
        AND EXISTS(SELECT 1 FROM external_ids e WHERE e.entity_id=c.agent_id AND e.scope='agent') ORDER BY c.agent_id''',(work.work_id,))]
    series=[r[0] for r in db.execute('''SELECT DISTINCT r.series_id FROM work_relations r JOIN series s ON s.series_id=r.series_id
        JOIN works w ON w.work_id=r.work_id WHERE w.resolved=1 AND w.canonical_id=? AND r.relation_type='series_member'
        ORDER BY r.series_id''',(work.work_id,))]
    external=[{'source':r[0],'value':r[1]} for r in db.execute('''SELECT DISTINCT e.source,e.value FROM external_ids e
        JOIN works w ON w.work_id=e.entity_id WHERE e.scope='work' AND w.resolved=1 AND w.canonical_id=? ORDER BY e.source,e.value''',(work.work_id,))]
    urls={e.source_url for e in work.evidence if e.source_url}
    urls.update(source_records[v]['source_url'] for v in work.source_record_ids if source_records[v]['source_url'])
    row={'work_id':work.work_id,'title':work.title,'features':dict(sorted(features.items())),
        'feature_provenance':dict(sorted(provenance.items())),'author_ids':authors,'series_ids':series,'external_ids':external,
        'authors':list(work.authors),'original_language':work.original_language,'first_publication_year':work.first_publication_year,
        'region':list(work.region),'series':work.series,'source_urls':sorted(urls)}
    return row,excluded,nulls


def apply_classification_overlay(base_rows,*,base_database,enriched_database,delta_inputs,delta_manifest,results,run_manifest,terminal_proof)->dict:
    """Return an in-memory replacement snapshot; preserve unchanged row objects.

Required manifest: enrichment-classification-delta-v1 paths/raw SHAs,
    complete canonical-crosswalk.source.json and input-comparison.json, current
    execution_context. All changed original work inputs need terminal results.
    The caller may persist rows/audit in a NEW JSON namespace; this function
    neither writes JSON nor adds classification tables to the enriched DB.
    """
    paths={name:Path(value).resolve(strict=True) for name,value in {
        'base_database':base_database,'enriched_database':enriched_database,'delta_inputs':delta_inputs,
        'delta_manifest':delta_manifest,'results':results,'run_manifest':run_manifest,'terminal_proof':terminal_proof}.items()}
    earliest={str(path):_sha(path) for path in paths.values()}
    for name in ('base_database','enriched_database'):_no_wal(paths[name])
    manifest=_json(paths['delta_manifest'].read_bytes());namespace=paths['delta_manifest'].parent
    if manifest.get('version')!='enrichment-classification-delta-v1' or manifest.get('source_files_unchanged_before_publish') is not True:
        raise ValueError('Unsupported/unsealed delta manifest')
    for name,field in [('canonical_crosswalk','canonical_crosswalk_path'),('input_comparison','input_comparison_path')]:
        paths[name]=_contained(namespace,manifest[field])
    paths['old_inputs']=_source_path(namespace,manifest['old_inputs_path'])
    paths['new_all_inputs']=_source_path(namespace,manifest['new_all_inputs_path'])
    paths['all_inputs']=_contained(namespace,'all-inputs.jsonl')
    for name in ARTIFACTS:paths['artifact:'+name]=_contained(namespace,'artifacts/'+name)
    if _source_path(namespace,manifest['new_database_path'])!=paths['enriched_database']:
        raise ValueError('Delta manifest binds another enriched DB')
    checks={'old_inputs_sha256':'old_inputs','new_database_sha256':'enriched_database','new_all_inputs_sha256':'new_all_inputs',
        'canonical_crosswalk_sha256':'canonical_crosswalk','input_comparison_sha256':'input_comparison',
        'ordered_inputs_sha256':'delta_inputs','work_inputs_sha256':'delta_inputs','all_inputs_sha256':'all_inputs'}
    if any(manifest.get(field)!=_sha(paths[name]) for field,name in checks.items()):raise ValueError('Delta source raw SHA differs')
    run_dir=paths['run_manifest'].parent
    def inventory():
        files=set(paths.values())|{(ROOT/name).resolve(strict=True) for name in ARTIFACTS}|{
            ROOT/'src/recommend_book/database.py',ROOT/'src/recommend_book/normalization.py',
            ROOT/'src/recommend_book/resolution.py',Path(__file__).resolve(),Path(__file__).with_name('world.py').resolve()}
        for file in run_dir.rglob('*'):
            if file.is_file():
                resolved=file.resolve()
                if not resolved.is_relative_to(run_dir):raise ValueError('Run symlink escapes namespace')
                files.add(resolved)
        return files
    original_files=inventory();before={str(p):_sha(p) for p in sorted(original_files)}
    if any(before[path]!=digest for path,digest in earliest.items()):raise ValueError('Overlay source changed during setup')
    initial_rows=_value_sha(base_rows)
    base=load_world_database(paths['base_database'])['rows'];by_base={row['work_id']:row for row in base}
    if len(base_rows)!=len(by_base) or {row['work_id'] for row in base_rows}!=set(by_base):raise ValueError('Base rows differ from classified DB work set')
    for row in base_rows:
        for field,value in by_base[row['work_id']].items():
            if field not in PRIVATE_FIELDS and row.get(field)!=value:raise ValueError('Base row source/features differ from classified DB')
    old_db=_readonly(paths['base_database']);new_db=_readonly(paths['enriched_database'])
    try:
        if new_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name LIKE 'classification_%'").fetchone():
            raise ValueError('Enriched source must be bibliographic only, without classification tables')
        old_records=_source_records(old_db);new_records=_source_records(new_db)
        if any(new_records.get(version)!=row for version,row in old_records.items()):
            raise ValueError('Original immutable source versions/raw JSON are not retained')
        old_view=_reconstruct(old_db);new_view=_reconstruct(new_db)
        old=_inputs(paths['old_inputs']);new_all=_inputs(paths['new_all_inputs']);delta=_inputs(paths['delta_inputs'])
        if {w.work_id for w in old}!=set(by_base) or {w.work_id for w in new_all}!=set(new_view):
            raise ValueError('Whole work input snapshots differ from DB canonical bounds')
        for works,current in ((old,old_view),(new_all,new_view),(delta,new_view)):
            if any(w.work_id not in current or input_hash(w)!=input_hash(current[w.work_id]) for w in works):
                raise ValueError('WorkInput identity/hash/source refs/evidence differ from immutable DB reconstruction')
        saved_order=[(r['work_id'],r['input_hash']) for r in old_db.execute('SELECT work_id,input_hash FROM classification_inputs WHERE expression_id IS NULL ORDER BY selected_position,job_key')]
        if [(w.work_id,input_hash(w)) for w in old]!=saved_order:raise ValueError('Old ordered inputs differ from base classification order')
        mapping=_crosswalk(old_db,new_db,_json(paths['canonical_crosswalk'].read_bytes()),set(old_view),set(new_view))
        comparisons=_json(paths['input_comparison'].read_bytes())
        if len(comparisons)!=len(old):raise ValueError('Whole input comparison count differs')
        changed=[];unchanged=[];identity_changed=0
        for work,saved in zip(old,comparisons):
            current=new_view[mapping[work.work_id]]
            values={'old_work_id':work.work_id,'new_work_id':current.work_id,'expression_id':None,
                'old_input_hash':input_hash(work),'new_input_hash':input_hash(current),
                'changed':input_hash(work)!=input_hash(current),'identity_changed':work.work_id!=current.work_id,
                'source_refs_changed':work.source_record_ids!=current.source_record_ids,'evidence_changed':work.evidence!=current.evidence}
            if any(field not in saved or saved[field]!=value or type(saved[field]) is not type(value) for field,value in values.items()):
                raise ValueError('Whole input comparison differs from SQL reconstructed snapshots')
            (changed if values['changed'] else unchanged).append(current.work_id)
            identity_changed+=values['identity_changed']
        for field,value in {'work_count':len(changed),'changed_count':len(changed),'unchanged_count':len(unchanged),
            'total_work_count':len(old),'identity_changed_count':identity_changed}.items():
            if type(manifest.get(field)) is not int or manifest[field]!=value:raise ValueError('Delta manifest count differs')
        if manifest.get('changed_work_ids')!=changed or manifest.get('unchanged_work_ids')!=unchanged or [w.work_id for w in delta]!=changed:
            raise ValueError('Delta exact changed set/order differs; no omission or unchanged reclassification allowed')
        old_lines=paths['old_inputs'].read_bytes().splitlines(keepends=True)
        new_lines={_json(line)['work_id']:line for line in paths['new_all_inputs'].read_bytes().splitlines(keepends=True)}
        expected_all=b''.join(new_lines[mapping[w.work_id]] if mapping[w.work_id] in changed else line
            for w,line in zip(old,old_lines))
        if paths['all_inputs'].read_bytes()!=expected_all:
            raise ValueError('Full overlay inputs must preserve unchanged old bytes and changed producer bytes in old order')
        run,new_results=_run(paths,manifest,delta)
        replacements={};scopes=Counter();nulls=0
        for work,result in zip(delta,new_results):
            row,excluded,null_count=_project(new_db,work,result,run['run_id'],job_key(input_hash(work),run['config']),new_records)
            replacements[work.work_id]=row;scopes.update(excluded);nulls+=null_count
        output=[]
        for row in base_rows:
            new_id=mapping[row['work_id']]
            if new_id in replacements:
                fresh=deepcopy(row);fresh.update(replacements[new_id]);output.append(fresh)
            else:
                if new_id!=row['work_id']:raise ValueError('Representative-ID change requires fresh result; legacy rebind forbidden')
                output.append(row)
    finally:old_db.close();new_db.close()
    _before_return()
    if _value_sha(base_rows)!=initial_rows:raise ValueError('Base rows changed during overlay')
    for name in ('base_database','enriched_database'):_no_wal(paths[name])
    if inventory()!=original_files or any(not Path(p).is_file() or _sha(Path(p))!=digest for p,digest in before.items()):
        raise ValueError('Protected overlay source changed during read')
    return {'rows':output,'audit':{'version':'classification-recommendation-overlay-v1','feature_policy_version':POLICY_VERSION,
        'work_count':len(output),'changed_work_count':len(changed),'unchanged_work_count':len(unchanged),
        'identity_changed_count':identity_changed,'old_to_new_canonical':mapping,'old_labels_rebound':False,
        'changed_labels_union_with_old':False,'unchanged_rows_preserved':True,'base_rows_mutated':False,
        'human_verified':False,'identity_semantic_quality_verified':False,'llm_started':False,'database_tables_written':False,
        'scope_excluded_labels':sum(scopes.values()),'scope_excluded_by_scope':dict(scopes),'null_relevance_features':nulls,
        'null_relevance_strength':NULL_RELEVANCE_STRENGTH,'raw_confidence_used_as_weight':False,
        'source_files_unchanged':True,'source_sha256':before,'run_id':run['run_id'],'empty_features':sum(not r['features'] for r in output)}}


def _before_return():
    """Synthetic mutation-test boundary; deliberately no action."""
