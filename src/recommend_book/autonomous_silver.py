"""Independent same-model AI silver, never human annotation or accuracy."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.classification import input_hash, validate_result, load_taxonomy, read_results
from recommend_book.classification.runner import run_classification, parse_events, build_run_config
from recommend_book.database import DatasetDB
from recommend_book.jobs import atomic_json, canonical_json, job_key
from recommend_book.models import WorkInput

CORE = ('config/sources.json','config/taxonomy.json','schemas/classification.schema.json',
    'src/recommend_book/classification/prompt.py','src/recommend_book/classification/validation.py',
    'src/recommend_book/classification/runner.py','src/recommend_book/models.py',
    'src/recommend_book/jobs.py','pyproject.toml','uv.lock')
ROOT = Path(__file__).resolve().parents[2]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _rows(path):
    rows=[WorkInput.model_validate_json(l) for l in Path(path).read_bytes().splitlines() if l.strip()]
    if len({(w.work_id,w.expression_id) for w in rows}) != len(rows):
        raise ValueError('Duplicate work/expression input')
    return rows


def _db(path):
    path=Path(path).resolve(strict=True)
    for suffix in ('-wal','-journal'):
        side=Path(str(path)+suffix)
        if side.exists() and side.stat().st_size:
            raise ValueError('Database has uncheckpointed sidecar')
    db=DatasetDB.__new__(DatasetDB)
    db.path=path
    db.connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    db.connection.row_factory=sqlite3.Row
    db.connection.execute('PRAGMA query_only=ON')
    return db


def prepare_blind_inputs(review_pack, latest_inputs, canonical_crosswalk, database,
                         output_dir, *, expected_count=400, baseline_manifest=None):
    """Retain fixed old membership; verify latest inputs against immutable SQL."""
    source_paths=[Path(p).resolve(strict=True) for p in (review_pack,latest_inputs,canonical_crosswalk,database)]
    pack_path,input_path,cross_path,db_path=source_paths
    output=Path(output_dir).resolve()
    if output.exists() or any(output.is_relative_to(p.parent) for p in source_paths):
        raise ValueError('New silver namespace must be outside source trees')
    protected={str(p):_sha(p) for p in source_paths}
    for name in CORE:protected[str(ROOT/name)]=_sha(ROOT/name)
    if baseline_manifest is not None:
        baseline_path=Path(baseline_manifest).resolve(strict=True)
        protected[str(baseline_path)]=_sha(baseline_path)
        for row in _json(baseline_path)['artifacts']:
            path=(ROOT/row['path']).resolve(strict=True)
            if _sha(path)!=row['sha256']:raise ValueError('Baseline artifact drift: '+row['path'])
            protected[str(path)]=row['sha256']
    pack=_json(pack_path)
    if 'pack_sha256' in pack:
        digest=hashlib.sha256(canonical_json({k:v for k,v in pack.items() if k!='pack_sha256'}).encode()).hexdigest()
        if digest!=pack['pack_sha256']:raise ValueError('Review pack SHA mismatch')
    original=[WorkInput.model_validate(w) for w in pack['works']]
    if len(original)!=expected_count or len({w.work_id for w in original})!=expected_count:
        raise ValueError('Review membership count or duplicate identity')
    if any(w.expression_id is not None for w in original):raise ValueError('Review membership requires original work-level rows')
    latest=_rows(input_path)
    if any(w.expression_id is not None for w in latest):raise ValueError('Latest input requires work-level rows')
    latest_by_id={w.work_id:w for w in latest}
    cross=_json(cross_path); mapping={}
    for row in cross:
        old=row['old_canonical_id'];targets=row['new_canonical_ids'];nodes=row['old_work_node_ids']
        if old in mapping or len(targets)!=1 or targets[0] not in latest_by_id or old not in nodes or len(nodes)!=len(set(nodes)):
            raise ValueError('Crosswalk duplicate, split, unknown or missing immutable node')
        mapping[old]=row
    if len({r['new_canonical_ids'][0] for r in mapping.values()})!=len(mapping) or set(r['new_canonical_ids'][0] for r in mapping.values())!=set(latest_by_id):
        raise ValueError('Crosswalk is not a complete one-to-one mapping')
    db=_db(db_path)
    try:
        reconstructed={w.work_id:w for w in db.work_inputs() if w.expression_id is None}
        if set(reconstructed)!=set(latest_by_id) or any(input_hash(w)!=input_hash(reconstructed[k]) for k,w in latest_by_id.items()):
            raise ValueError('Latest input hash/source references differ from SQL snapshot')
        for row in mapping.values():
            target=row['new_canonical_ids'][0]
            for node in row['old_work_node_ids']:
                actual=db.connection.execute('SELECT resolved,canonical_id FROM works WHERE work_id=?',(node,)).fetchone()
                if actual is None or actual['resolved']!=1 or actual['canonical_id']!=target:
                    raise ValueError('Crosswalk differs from immutable SQL canonical nodes')
        selected=[];membership=[]
        for old in original:
            if old.work_id not in mapping:raise ValueError('Review identity absent from crosswalk')
            new=latest_by_id[mapping[old.work_id]['new_canonical_ids'][0]]
            selected.append(new)
            membership.append({'old_work_id':old.work_id,'new_work_id':new.work_id,
                'old_input_hash':input_hash(old),'new_input_hash':input_hash(new),
                'source_record_ids':new.source_record_ids,'identity_changed':old.work_id!=new.work_id,
                'input_changed':input_hash(old)!=input_hash(new)})
    finally:db.close()
    if any(_sha(p)!=h for p,h in protected.items()):raise ValueError('Protected source changed during preparation')
    output.mkdir(parents=True)
    raw=''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in selected).encode('utf8')
    (output/'blind-inputs.jsonl').write_bytes(raw)
    (output/'original-membership-inputs.jsonl').write_text(''.join(canonical_json(w.model_dump(mode='json'))+'\n' for w in original),encoding='utf8')
    atomic_json(output/'membership-crosswalk.json',membership)
    for lane in ('lane-a','lane-b','adjudication'):(output/lane/'workspace').mkdir(parents=True)
    for name in CORE:
        dest=output/'artifacts'/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/name).read_bytes())
    manifest={'version':'ai-silver-preparation-v1','provenance':'ai_silver','created_at':datetime.now(timezone.utc).isoformat(),
        'membership_count':len(selected),'input_changed_count':sum(r['input_changed'] for r in membership),
        'identity_changed_count':sum(r['identity_changed'] for r in membership),'sql_input_exact_match':True,
        'blind_inputs_sha256':hashlib.sha256(raw).hexdigest(),'source_sha256':protected,
        'source_protected_before_after':True,'predictions_in_blind_input':False,
        'original_input_not_sent':'original-membership-inputs.jsonl is provenance only; run uses blind-inputs.jsonl',
        'human_gold_created':False,'human_verified':False,'llm_started':False,
        'blindness':'Independent ephemeral CLI contexts, same supplied evidence and definitions; no original predictions, peer judgments or past conversation. Tools rejected by runner.',
        'core_sha256':{name:_sha(ROOT/name) for name in CORE}}
    atomic_json(output/'preparation-manifest.json',manifest)
    return manifest


def _prepared(namespace):
    namespace=Path(namespace).resolve(strict=True);manifest=_json(namespace/'preparation-manifest.json')
    if _sha(namespace/'blind-inputs.jsonl')!=manifest['blind_inputs_sha256']:raise ValueError('Blind input drift')
    for name,h in manifest['core_sha256'].items():
        if _sha(ROOT/name)!=h or _sha(namespace/'artifacts'/name)!=h:raise ValueError('Classification context drift')
    for p,h in manifest['source_sha256'].items():
        if _sha(p)!=h:raise ValueError('Protected source drift')
    return namespace,_rows(namespace/'blind-inputs.jsonl'),manifest


def run_blind_lanes(namespace, *, pilot_count=None, timeout=900, max_run_seconds=7200,
                    max_observed_tokens=5000000, executable=None, runner=None,
                    usage_ledger=None,execution_spec='guarded-v3-baseline',expected_usage_ledger_sha256=None):
    """Two independent existing-runner namespaces; maximum four CLI workers."""
    from recommend_book.classification.runner import validate_controls
    from recommend_book.classification.specs import get_spec,NATIVE_SPECS
    from recommend_book.usage_guard import UsageGuard
    validate_controls(concurrency=2,batch_size=5,timeout=timeout,
        max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens)
    get_spec(execution_spec)
    if execution_spec in NATIVE_SPECS:raise ValueError('Silver lanes require the world classification task')
    if usage_ledger is None:raise ValueError('A current usage ledger is required for silver classification')
    usage_ledger=Path(usage_ledger)
    namespace,works,prepared=_prepared(namespace)
    if pilot_count is not None:
        if isinstance(pilot_count,bool) or not isinstance(pilot_count,int) or not 1<=pilot_count<=len(works):raise ValueError('Invalid pilot count')
        works=works[:pilot_count]
    ledger_sha=_sha(usage_ledger)
    if expected_usage_ledger_sha256 is not None and ledger_sha!=expected_usage_ledger_sha256:raise ValueError('Frozen usage ledger changed')
    UsageGuard(usage_ledger,expected_ledger_sha256=ledger_sha).assert_inputs_allowed(works,'external_llm')
    runner=runner or run_classification
    phase='pilot' if pilot_count is not None else 'full'
    report_path=namespace/f'{phase}-lanes-report.json'
    if report_path.exists():raise ValueError('Run report exists; refusing an implicit rerun')
    def lane(name):
        output=namespace/name if pilot_count is None else namespace/f'pilot-{pilot_count}'/name
        return runner(works,output,concurrency=2,batch_size=5,model='gpt-6.1-sol',
            timeout=timeout,max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens,
            stop_on_rate_limit=True,executable=executable,usage_ledger=usage_ledger,execution_spec=execution_spec,
            expected_usage_ledger_sha256=ledger_sha)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={name:pool.submit(lane,name) for name in ('lane-a','lane-b')}
        manifests={name:future.result() for name,future in futures.items()}
    _prepared(namespace)
    report={'version':'ai-silver-lanes-v1','provenance':'ai_silver','selected':len(works),'pilot':pilot_count is not None,
        'blind_inputs_sha256':prepared['blind_inputs_sha256'],'maximum_cli_concurrency':4,
        'human_gold_created':False,'human_verified':False,'lanes':manifests,
        'successful':all(m.get('completed')==len(works) and m.get('failed',0)==0 and m.get('pending',0)==0 and not m.get('stopped') for m in manifests.values())}
    atomic_json(report_path,report)
    return report


def _result_index(results, works):
    expected={w.work_id:w for w in works};index={}
    for row in results:
        key=row['work_id']
        if key not in expected or key in index:raise ValueError('Unknown/duplicate reviewer result')
        if row.get('prompt_version') in ('3.0-definitions','3.1-context'):
            from recommend_book.classification.specs import get_spec
            get_spec('definitions-v3' if row['prompt_version']=='3.0-definitions' else 'context-v3').validate_result(row,expected[key],load_taxonomy())
        else:validate_result(row,expected[key])
        index[key]=row
    return index


def _labels(result):
    return {(r['label_id'],r['scope'],r['expression_id']):r for r in result['labels']}


def _decisions(result):
    return {k:(r['decision'],r['relevance']) for k,r in _labels(result).items() if r['decision']!='unknown'}


def compare_silver(inputs, lane_a, lane_b, original_results=None):
    """Compare only after reviewer predictions are fixed. Omission=unknown."""
    works=[w if isinstance(w,WorkInput) else WorkInput.model_validate(w) for w in inputs]
    if len({w.work_id for w in works})!=len(works):raise ValueError('Duplicate comparison inputs')
    a=_result_index(lane_a,works);b=_result_index(lane_b,works)
    original=_result_index(original_results or [],works)
    known_pairs=agree=both_known=one_null=both_null=0;errors=[];disagreements=[];decision_disagreements=[];rows=[];axes=Counter();unknown=Counter();conflicts=Counter()
    for w in works:
        key=w.work_id
        if key not in a or key not in b:continue
        al,bl=_labels(a[key]),_labels(b[key]);known={k for k,r in al.items() if r['decision']!='unknown'}|{k for k,r in bl.items() if r['decision']!='unknown'}
        differing=[];rel_differing=[]
        for k in sorted(known,key=str):
            x,y=al.get(k),bl.get(k);xd=x['decision'] if x else 'unknown';yd=y['decision'] if y else 'unknown'
            known_pairs+=1;agree+=xd==yd;axes[k[0].split('.')[0]]+=1
            unknown['lane_a']+=xd=='unknown';unknown['lane_b']+=yd=='unknown'
            if xd!=yd:differing.append({'label_id':k[0],'scope':k[1],'expression_id':k[2],'lane_a':xd,'lane_b':yd})
            elif xd!='unknown':
                xr,yr=x['relevance'],y['relevance']
                if xr is None and yr is None:both_null+=1
                elif xr is None or yr is None:one_null+=1;rel_differing.append(k[0])
                else:
                    both_known+=1;errors.append(abs(xr-yr))
                    if xr!=yr:rel_differing.append(k[0])
        if differing:decision_disagreements.append(key)
        if differing or rel_differing or a[key]['conflicts'] or b[key]['conflicts']:disagreements.append(key)
        conflicts['lane_a']+=len(a[key]['conflicts']);conflicts['lane_b']+=len(b[key]['conflicts'])
        rows.append({'work_id':key,'input_hash':input_hash(w),'decision_disagreements':differing,
            'relevance_disagreement_label_ids':rel_differing,'allunknown_a':not _decisions(a[key]),'allunknown_b':not _decisions(b[key]),
            'original_compared':key in original,
            'lane_a_differs_original':_decisions(a[key])!=_decisions(original[key]) if key in original else None,
            'lane_b_differs_original':_decisions(b[key])!=_decisions(original[key]) if key in original else None})
    return {'version':'ai-silver-comparison-v1','provenance':'ai_silver','human_gold_created':False,'human_verified':False,
        'agreement_is_correctness':False,'same_model_common_error_limit':True,'work_count':len(works),
        'compared_work_count':len(rows),'missing_lane_a':[w.work_id for w in works if w.work_id not in a],
        'missing_lane_b':[w.work_id for w in works if w.work_id not in b],
        'known_union':{'decision_pairs':known_pairs,'agreement_pairs':agree,'agreement_rate':agree/known_pairs if known_pairs else None,
            'denominator':'Union of non-unknown scoped labels; omitted labels are unknown, never absent. Joint unknown omitted labels excluded.'},
        'relevance':{'both_known_pairs':both_known,'one_null_pairs':one_null,'both_null_pairs':both_null,'mean_absolute_difference':sum(errors)/len(errors) if errors else None,
            'raw_confidence_used':False,'calibrated':False},'axis_pair_counts':dict(axes),'unknown_in_known_union':dict(unknown),
        'conflict_counts':dict(conflicts),'disagreement_work_ids':disagreements,'decision_disagreement_work_ids':decision_disagreements,'rows':rows}


def adjudicate_silver(inputs,lane_a,lane_b,third_results):
    """Third independent judgment resolves exact decision/centrality agreement only."""
    a=_result_index(lane_a,inputs);b=_result_index(lane_b,inputs);third=_result_index(third_results,inputs);rows=[]
    for w in inputs:
        key=w.work_id;selected=None
        if key not in a or key not in b:status='held_missing_reviewer'
        elif _decisions(a[key])==_decisions(b[key]) and not a[key]['conflicts'] and not b[key]['conflicts']:
            status='blind_lanes_agree';selected=a[key]
        elif key not in third:status='held_missing_adjudication'
        elif third[key]['conflicts']:status='held_third_conflicts'
        elif _decisions(third[key])==_decisions(a[key]):status='third_supported_lane_a';selected=third[key]
        elif _decisions(third[key])==_decisions(b[key]):status='third_supported_lane_b';selected=third[key]
        else:status='held_third_differs_both'
        rows.append({'work_id':key,'input_hash':input_hash(w),'adjudication_status':status,
            'selected_result':selected,'third_result':third.get(key),'provenance':'ai_silver','human_verified':False})
    return {'version':'ai-silver-adjudication-v1','provenance':'ai_silver','human_verified':False,'human_gold_created':False,
        'policy':'Retain exact valid reviewer result; no merged fabricated classification. Third run is independent and blind. Unresolved disagreements remain held.',
        'status_counts':dict(Counter(r['adjudication_status'] for r in rows)),'rows':rows}


def latest_original_results(namespace, old_database, delta_results):
    """Readonly latest originals by unchanged input hash; never rebind old IDs."""
    namespace,works,_=_prepared(namespace)
    delta_path=Path(delta_results)
    delta={r['work_id']:r for r in read_results(delta_path.parent if delta_path.is_file() else delta_path)}
    conn=sqlite3.connect(Path(old_database).resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        result=[]
        for w in works:
            if w.work_id in delta:r=delta[w.work_id]
            else:
                row=conn.execute('SELECT result_json FROM classification_inputs WHERE work_id=? AND input_hash=? AND status=?',(w.work_id,input_hash(w),'completed')).fetchone()
                if row is None:raise ValueError('Original latest result not bound to selected input')
                r=json.loads(row[0])
            validate_result(r,w);result.append(r)
        return result
    finally:conn.close()


def audit_silver_run(output_dir, inputs):
    """Readonly terminal index/durable/job/event audit, including reused attempts."""
    root=Path(output_dir).resolve(strict=True);works={w.work_id:w for w in inputs};manifest=_json(root/'manifest.json')
    if len(works)!=len(inputs) or manifest['selected']!=len(works):raise ValueError('Audit selected identity mismatch')
    config=manifest['config']
    if config!=build_run_config(config['model'],config['cli_version'],load_taxonomy(),config['batch_size'],execution_spec=config.get('execution_spec')):
        raise ValueError('Audit current execution config mismatch')
    results=_result_index(read_results(root),inputs);events={};file_sha={str(root/'manifest.json'):_sha(root/'manifest.json'),str(root/'results.jsonl'):_sha(root/'results.jsonl')}
    def relative(value):
        path=(root/value).resolve(strict=True)
        if not path.is_relative_to(root):raise ValueError('Run provenance path escapes namespace')
        file_sha[str(path)]=_sha(path)
        return path
    expected_keys={job_key(input_hash(w),config):w for w in inputs}
    if set(manifest['jobs'])!=set(expected_keys):raise ValueError('Manifest job keys differ from bound input/config')
    completed=0
    for key,state in manifest['jobs'].items():
        work=expected_keys[key]
        if state['input_hash']!=input_hash(work) or state['work_id']!=work.work_id or state['config']!=config:raise ValueError('Job/input/config mismatch')
        actual=_json(relative(f'jobs/{key}.json'))
        if actual!=state:raise ValueError('Terminal manifest/job state mismatch')
        for history in state.get('history',[]):
            path=relative(history['events_path']);parsed=parse_events(path.read_text(encoding='utf8'))
            if parsed['usage']!=history['usage']:raise ValueError('Event/job observed usage mismatch')
            if str(path) in events and events[str(path)]['returncode']!=history['returncode']:raise ValueError('Shared attempt exit mismatch')
            events[str(path)]={**parsed,'returncode':history['returncode'],'error_category':history['error_category']}
            if history.get('final_path') and (root/history['final_path']).exists():relative(history['final_path'])
        if state['status']=='completed':
            completed+=1
            durable=_json(relative(state['result_path']))
            if work.work_id not in results or canonical_json(durable)!=canonical_json(results[work.work_id]):raise ValueError('Index/durable result mismatch')
            _result_index([durable],[work])
            if state.get('history'):
                last=state['history'][-1];last_events=events[str((root/last['events_path']).resolve())]
                if last['returncode']!=0 or last['error_category'] is not None or last_events['event_errors'] or last_events['tool_calls']:raise ValueError('Completed result has unsuccessful final attempt')
        elif work.work_id in results:raise ValueError('Noncompleted job has an adopted result')
    if completed!=manifest['completed'] or completed!=len(results):raise ValueError('Completed coverage mismatch')
    label_axis=Counter();label_scope=Counter();missing=Counter();evidence_refs=0;null_relevance=0
    for result in results.values():
        for label in result['labels']:
            label_axis[label['axis']]+=1;label_scope[label['scope']]+=1;evidence_refs+=len(label['evidence']);null_relevance+=label['relevance'] is None
        missing.update(r['axis'] for r in result['missing_axes'])
    if any(_sha(path)!=h for path,h in file_sha.items()):raise ValueError('Run artifact changed during terminal read')
    usage=Counter()
    for item in events.values():usage.update(item['usage'])
    return {'version':'ai-silver-terminal-audit-v1','provenance':'mechanical_audit','human_verified':False,'selected':len(works),
        'completed':completed,'failed':manifest['failed'],'pending':manifest['pending'],'stopped':manifest['stopped'],
        'unique_cli_invocations':len(events),'events_include_reused_pilot_attempts':True,
        'returncode_counts':dict(Counter(str(e['returncode']) for e in events.values())),
        'error_category_counts':dict(Counter(str(e['error_category']) for e in events.values())),
        'tool_calls':sum(len(e['tool_calls']) for e in events.values()),'event_errors':sum(len(e['event_errors']) for e in events.values()),
        'observed_usage_all_unique_attempts':dict(usage),'usage_source':'CLI events, not API billing; includes reused pilot attempts once',
        'labels_by_axis':dict(label_axis),'labels_by_scope':dict(label_scope),'missing_axes':dict(missing),
        'label_evidence_refs':evidence_refs,'null_relevance_labels':null_relevance,
        'exact_index_durable_job_input_config':True,'source_sha256':file_sha,'protected_before_after':True}
