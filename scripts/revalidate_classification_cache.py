"""Offline, whole-batch revalidation into a new cache namespace only.

Never run against a live source. This does not prove semantic label accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from recommend_book.classification.runner import build_run_config,parse_events
from recommend_book.classification.validation import ROOT,input_hash,load_taxonomy,validate_result
from recommend_book.jobs import JobStore,atomic_json,canonical_json,job_key
from recommend_book.models import WorkInput


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def value_sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def strict_json(raw):
    def pairs(values):
        result={}
        for key,value in values:
            if key in result:raise ValueError('duplicate JSON property: '+key)
            result[key]=value
        return result
    def constant(value):raise ValueError('nonfinite JSON constant: '+value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def read_json(path):return strict_json(Path(path).read_text(encoding='utf-8'))


def contained(root,relative):
    if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('artifact path must be relative to source namespace')
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('artifact escapes source namespace')
    if not path.is_file():raise ValueError('source artifact missing: '+relative)
    return path


def protect(trees,files):
    paths=set()
    for tree in trees:
        for path in tree.rglob('*'):
            if not path.is_file():continue
            resolved=path.resolve()
            if not resolved.is_relative_to(tree.resolve()):raise ValueError('protected source symlink escapes input tree')
            paths.add(resolved)
    paths.update(Path(p).resolve() for p in files)
    return {str(p):file_sha(p) for p in sorted(paths)}


def assert_unchanged(proofs,trees):
    actual={str(p.resolve()) for tree in trees for p in tree.rglob('*') if p.is_file()}
    expected={path for path in proofs if any(Path(path).is_relative_to(tree) for tree in trees)}
    if actual!=expected:raise ValueError('protected source artifact changed during migration (file inventory)')
    if any(not Path(path).is_file() or file_sha(path)!=checksum for path,checksum in proofs.items()):
        raise ValueError('protected source artifact changed during migration')


def verify_input_snapshot(snapshot,ordered_dir=None,frozen_dir=None):
    manifest=read_json(snapshot/'manifest.json');raw=(snapshot/'work-inputs.jsonl').read_bytes()
    if file_sha(snapshot/'work-inputs.jsonl')!=manifest['work_inputs_sha256']:raise ValueError('exact input snapshot SHA mismatch')
    lines=raw.splitlines(keepends=True)
    if any(not line.strip() for line in lines):raise ValueError('empty input row')
    works=[WorkInput.model_validate_json(line) for line in lines]
    identities=[(w.work_id,w.expression_id) for w in works]
    if len(set(identities))!=len(works):raise ValueError('duplicate input identity')
    if len(works)!=manifest['work_count']:raise ValueError('exact input snapshot count mismatch')
    if ordered_dir is not None:
        order_manifest=read_json(ordered_dir/'manifest.json');ordered_raw=(ordered_dir/'ordered-inputs.jsonl').read_bytes()
        if file_sha(ordered_dir/'ordered-inputs.jsonl')!=order_manifest['ordered_inputs_sha256']:raise ValueError('ordered input SHA mismatch')
        if b''.join(ordered_raw.splitlines(keepends=True)[:len(works)])!=raw:raise ValueError('exact input snapshot is not ordered prefix byte copy')
    if frozen_dir is not None:
        frozen_manifest=read_json(frozen_dir/'manifest.json');frozen_raw=(frozen_dir/'work-inputs.jsonl').read_bytes()
        if hashlib.sha256(frozen_raw).hexdigest()!=frozen_manifest['work_inputs_sha256']:raise ValueError('frozen input SHA mismatch')
        frozen=[WorkInput.model_validate_json(line) for line in frozen_raw.splitlines() if line.strip()]
        index={(w.work_id,w.expression_id):w for w in frozen}
        if len(index)!=len(frozen):raise ValueError('duplicate frozen input identity')
        if any((w.work_id,w.expression_id) not in index or input_hash(w)!=input_hash(index[w.work_id,w.expression_id]) for w in works):
            raise ValueError('exact input differs from frozen input hash/source refs')
        if ordered_dir is not None:
            context=read_json(frozen_dir/'processing-context.json')
            if context!=order_manifest['processing_context']:raise ValueError('legacy processing context binding differs')
            for proof in context.values():
                if file_sha(frozen_dir/proof['snapshot'])!=proof['sha256']:raise ValueError('legacy context snapshot SHA mismatch')
    return works


def verify_source(source,works):
    manifest=read_json(source/'manifest.json');states=manifest.get('jobs',{})
    allowed={'completed','held','invalid_cached_result'}
    if (not manifest.get('run_id') or manifest.get('pending')!=0 or len(states)!=manifest.get('selected') or
        len(states)!=len(works) or any(s.get('status') not in allowed or s.get('lease_until') is not None for s in states.values())):
        raise ValueError('source manifest is not terminal for the exact input snapshot')
    completed=sum(s['status']=='completed' for s in states.values())
    if manifest.get('completed')!=completed or manifest.get('failed')!=len(states)-completed:raise ValueError('terminal manifest counts differ from job states')
    old=manifest['config'];by_key={job_key(input_hash(w),old):w for w in works}
    if set(states)!=set(by_key):raise ValueError('terminal manifest job keys differ from exact input/config')
    durable={};groups={}
    for key,state in states.items():
        if value_sha(read_json(contained(source,f'jobs/{key}.json')))!=value_sha(state):raise ValueError('source saved job differs from terminal manifest')
        work=by_key[key]
        for field,expected in {'key':key,'work_id':work.work_id,'expression_id':work.expression_id,'input_hash':input_hash(work),'config':old}.items():
            if state.get(field)!=expected:raise ValueError('source job '+field+' mismatch')
        history=state.get('history',[])
        if not isinstance(state.get('attempts'),int) or state['attempts']!=len(history):raise ValueError('source attempts/history count mismatch')
        if [r.get('attempt') for r in history]!=list(range(1,state['attempts']+1)):raise ValueError('source history attempt order mismatch')
        if state['status']=='completed':
            path=contained(source,state['result_path']);value=read_json(path)
            if value.get('work_id')!=work.work_id or value.get('expression_id')!=work.expression_id or value.get('input_hash')!=input_hash(work):
                raise ValueError('completed durable result identity/hash mismatch')
            durable[key]={'path':path,'value':value}
        for metric in history:
            final=metric.get('final_path');events=metric.get('events_path')
            # Missing or unsuccessful raw artifacts become explicit held candidates.
            if not isinstance(final,str) or not isinstance(events,str):raise ValueError('source history artifact paths absent')
            group=groups.setdefault((final,events),{'members':{},'final_path':final,'events_path':events})
            if key in group['members']:raise ValueError('same raw attempt reused in multiple history entries')
            group['members'][key]=metric
    index=[]
    for line in contained(source,'results.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():index.append(strict_json(line))
    indexed={}
    for value in index:
        identity=(value.get('work_id'),value.get('expression_id'))
        if identity in indexed:raise ValueError('duplicate durable result index identity')
        indexed[identity]=value
    expected={(by_key[key].work_id,by_key[key].expression_id):row['value'] for key,row in durable.items()}
    if set(indexed)!=set(expected) or any(value_sha(indexed[key])!=value_sha(expected[key]) for key in expected):
        raise ValueError('source result index differs from completed durable results')
    return manifest,by_key,durable,groups


def evaluate_candidate(source,group,by_key,durable,old,taxonomy):
    members=group['members'];metrics=list(members.values())
    if any(m.get('shared_batch_size')!=len(members) for m in metrics):raise ValueError('batch history membership is incomplete')
    common={k:v for k,v in metrics[0].items() if k!='attempt'}
    if any(value_sha({k:v for k,v in m.items() if k!='attempt'})!=value_sha(common) for m in metrics):raise ValueError('batch history metrics disagree')
    if common.get('returncode')!=0:raise ValueError('attempt has no successful recorded returncode')
    if common.get('error_category') not in (None,'invalid_result','invalid_schema'):raise ValueError('attempt transport or unsafe error category')
    final=contained(source,group['final_path']);events=contained(source,group['events_path'])
    decoded=read_json(final);stdout=events.read_text(encoding='utf-8');parsed=parse_events(stdout)
    if parsed['event_errors'] or parsed['tool_calls']:raise ValueError('attempt event errors or tool use')
    event_rows=[strict_json(line) for line in stdout.splitlines() if line.strip()]
    if sum(row.get('type')=='turn.completed' for row in event_rows)!=1:raise ValueError('attempt has no unique turn.completed event')
    messages=[row['item']['text'] for row in event_rows if row.get('type')=='item.completed' and isinstance(row.get('item'),dict) and row['item'].get('type')=='agent_message']
    if not messages or value_sha(strict_json(messages[-1]))!=value_sha(decoded):raise ValueError('agent event final does not match immutable raw final')
    if value_sha(parsed['usage'])!=value_sha(common.get('usage')):raise ValueError('actual event usage differs from archived job history')
    if len(members)>1:
        if not isinstance(decoded,dict) or set(decoded)!={'results'}:raise ValueError('batch final wrapper differs')
        values=decoded['results']
    else:values=[decoded]
    if not isinstance(values,list) or not all(isinstance(v,dict) for v in values):raise ValueError('batch final shape differs')
    indexed={(r.get('work_id'),r.get('expression_id')):r for r in values}
    expected={(by_key[key].work_id,by_key[key].expression_id) for key in members}
    if len(values)!=len(expected) or set(indexed)!=expected:raise ValueError('raw batch identities are incomplete, duplicate or unknown')
    results={}
    for key in members:
        work=by_key[key];value=indexed[work.work_id,work.expression_id]
        validate_result(value,work,taxonomy)
        if value['model']!=old['model'] or value['cli_version']!=old['cli_version']:raise ValueError('result model/CLI metadata mismatch')
        if key in durable and value_sha(value)!=value_sha(durable[key]['value']):raise ValueError('raw batch does not match current completed durable result')
        results[key]=value
    return {'results':results,'final_sha256':file_sha(final),'events_sha256':file_sha(events),'usage':parsed['usage']}


def migrate(source_run,input_snapshot,output_dir,*,new_config=None,taxonomy=None,ordered_dir=None,frozen_dir=None):
    source_run,input_snapshot,output_dir=map(lambda p:Path(p).resolve(),(source_run,input_snapshot,output_dir))
    ordered_dir=Path(ordered_dir).resolve() if ordered_dir is not None else None
    frozen_dir=Path(frozen_dir).resolve() if frozen_dir is not None else None
    trees=[source_run,input_snapshot,*([ordered_dir] if ordered_dir else []),*([frozen_dir] if frozen_dir else [])]
    if output_dir.exists():raise ValueError('new cache namespace must not exist')
    if any(output_dir.is_relative_to(tree) for tree in trees):raise ValueError('new namespace is inside a protected source/input tree')
    proofs=protect(trees,[ROOT/'src/recommend_book/classification/validation.py',ROOT/'src/recommend_book/classification/prompt.py',ROOT/'schemas/classification.schema.json',ROOT/'config/taxonomy.json'])
    works=verify_input_snapshot(input_snapshot,ordered_dir,frozen_dir)
    source_manifest,by_key,durable,groups=verify_source(source_run,works)
    taxonomy=taxonomy or load_taxonomy();old=source_manifest['config']
    expected=build_run_config(old['model'],old['cli_version'],taxonomy,old['batch_size'])
    if new_config is None:new_config=expected
    if value_sha(new_config)!=value_sha(expected) or value_sha({k:v for k,v in new_config.items() if k not in ('validation_version','validation_hash')})!=value_sha(old):
        raise ValueError('new config may add only current validation version/hash to identical legacy config')
    if 'validation_version' in old or 'validation_hash' in old:raise ValueError('source already has a validation namespace; legacy migration required')
    candidates=[];eligible={key:[] for key in by_key}
    for group_key,group in sorted(groups.items()):
        info={'final_path':group['final_path'],'events_path':group['events_path'],'old_job_keys':sorted(group['members']),'outcome':'held'}
        try:
            verified=evaluate_candidate(source_run,group,by_key,durable,old,taxonomy)
            info.update(outcome='validated',final_sha256=verified['final_sha256'],events_sha256=verified['events_sha256'])
            for key in group['members']:eligible[key].append((group['members'][key]['attempt'],group_key,group,verified))
        except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as exc:info['reason']=str(exc)
        candidates.append(info)
    assert_unchanged(proofs,trees)
    output_dir.parent.mkdir(parents=True,exist_ok=True)
    stage=output_dir.with_name(output_dir.name+'.migration-'+uuid.uuid4().hex)
    stage.mkdir(exist_ok=False);(stage/'results').mkdir();store=JobStore(stage)
    ledger=[];states={}
    try:
        for work in works:
            old_key=job_key(input_hash(work),old);new_key=job_key(input_hash(work),new_config)
            if not eligible[old_key]:
                states[new_key]={'status':'not_submitted','work_id':work.work_id,'expression_id':work.expression_id}
                continue
            _,_,group,verified=max(eligible[old_key],key=lambda row:(row[0],row[1]))
            value=verified['results'][old_key];destination=stage/'results'/f'{new_key}.json'
            if old_key in durable:shutil.copyfile(durable[old_key]['path'],destination)
            else:atomic_json(destination,value)
            new_value=read_json(destination)
            if value_sha(value)!=value_sha(new_value):raise ValueError('import changed result JSON values')
            metric=group['members'][old_key];record_id=value_sha([old_key,new_key,verified['final_sha256'],metric['attempt']])
            provenance={'record_id':record_id,'new_job_key':new_key,'old_job_key':old_key,'work_id':work.work_id,'expression_id':work.expression_id,'input_hash':input_hash(work),'source_run':str(source_run),'source_manifest_sha256':proofs[str(source_run/'manifest.json')],'source_job_path':f'jobs/{old_key}.json','source_job_sha256':proofs[str(source_run/f'jobs/{old_key}.json')],'source_status':source_manifest['jobs'][old_key]['status'],'source_attempt':{'attempt':metric['attempt'],'final_path':group['final_path'],'final_sha256':verified['final_sha256'],'events_path':group['events_path'],'events_sha256':verified['events_sha256'],'shared_batch_size':len(group['members'])},'source_config_hash':value_sha(old),'new_config_hash':value_sha(new_config),'source_durable_result_path':str(durable[old_key]['path'].relative_to(source_run)) if old_key in durable else None,'source_durable_result_sha256':file_sha(durable[old_key]['path']) if old_key in durable else None,'source_raw_result_value_hash':value_sha(value),'new_result_value_hash':value_sha(new_value),'new_result_path':str(destination.relative_to(stage)),'new_result_sha256':file_sha(destination),'source_validation_policy_recorded':False,'legacy_attempts':source_manifest['jobs'][old_key]['attempts'],'legacy_history':source_manifest['jobs'][old_key]['history'],'human_verified':False}
            lease=store.claim(new_key,metadata={'work_id':work.work_id,'expression_id':work.expression_id,'input_hash':input_hash(work),'config':new_config})
            store.complete(new_key,str(destination.relative_to(stage)),lease_token=lease['lease_token'],attempts=0,history=[],cache_import={'record_id':record_id,'ledger':'migration-provenance.jsonl','source_manifest_sha256':provenance['source_manifest_sha256']})
            ledger.append(provenance);states[new_key]=store.get(new_key)
        (stage/'migration-provenance.jsonl').write_text(''.join(canonical_json(row)+'\n' for row in ledger),encoding='utf-8')
        atomic_json(stage/'migration-candidates.json',candidates)
        atomic_json(stage/'protected-source-sha.json',proofs)
        (stage/'results.jsonl').write_text(''.join(canonical_json(read_json(stage/row['new_result_path']))+'\n' for row in ledger),encoding='utf-8')
        report={'run_id':'cache-import-'+uuid.uuid4().hex,'config':new_config,'selected':len(works),'completed':len(ledger),'cache_imports':len(ledger),'reused':0,'failed':0,'pending':len(works)-len(ledger),'attempts':0,'cli_invocations':0,'usage':{},'usage_source':'No CLI invoked during cache import; legacy usage is provenance only','jobs':states,'source_run':str(source_run),'source_terminal_manifest_sha256':proofs[str(source_run/'manifest.json')],'source_validation_policy_recorded':False,'legacy_source_totals':{key:source_manifest.get(key) for key in ('attempts','cli_invocations','usage','completed','failed')},'human_quality_verified':False,'llm_started':False,'web_requests':0,'selection':'Highest validated attempt per job, then final/events paths lexically; every source batch validated in full','protected_files_unchanged':len(proofs)}
        # Both reports contain the hash inventory; exclude them to avoid self-reference.
        report['output_sha256_excluded']=['manifest.json','migration-manifest.json']
        report['output_sha256']={str(p.relative_to(stage)):file_sha(p) for p in stage.rglob('*') if p.is_file() and str(p.relative_to(stage)) not in report['output_sha256_excluded']}
        atomic_json(stage/'migration-manifest.json',report)
        atomic_json(stage/'manifest.json',report)
        assert_unchanged(proofs,trees)
        if output_dir.exists():raise ValueError('new cache output collision during publication')
        stage.rename(output_dir)
        return report
    except BaseException:
        # Delete only this freshly created, explicitly verified staging sibling.
        if stage.exists() and stage.resolve().parent==output_dir.parent.resolve() and stage.name.startswith(output_dir.name+'.migration-'):
            shutil.rmtree(stage)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,required=True)
    parser.add_argument('--input-snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ordered',type=Path,required=True)
    parser.add_argument('--frozen',type=Path,required=True)
    args=parser.parse_args();report=migrate(args.source_run,args.input_snapshot,args.output,ordered_dir=args.ordered,frozen_dir=args.frozen)
    print(canonical_json({k:v for k,v in report.items() if k not in ('jobs','output_sha256','config','legacy_source_totals')}))
