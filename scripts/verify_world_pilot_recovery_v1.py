"""Bounded real first10 receipt withdrawal and coordinator recovery verification.

Frozen runtime, original ledgers, adopted DB and classification runs are read
only. Resume publishes first10 through the production delivery builder. The
remaining26 are held and no mode in this script can start a model.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


def read(path):
    return json.loads(Path(path).read_bytes())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')


def now():
    return datetime.now(timezone.utc).isoformat()


def inventory(root):
    return {str(path.resolve()):sha(path) for path in sorted(Path(root).rglob('*')) if path.is_file()}


def runtime_api(preparation):
    source = str(Path(preparation)/'runtime/src')
    sys.path.insert(0,source)
    from recommend_book import next_database_v4
    from recommend_book.usage_guard_v4 import UsageGuard
    if not Path(next_database_v4.__file__).resolve().is_relative_to(Path(source).resolve()):
        raise ValueError('Fresh process must import the fixed version-four runtime')
    return next_database_v4,UsageGuard


def prepare(preparation,adopted_database,source_database,output,delivery_output):
    preparation,adopted_database,source_database,output = [Path(p).resolve() for p in (preparation,adopted_database,source_database,output)]
    if output.exists():
        raise ValueError('A new recovery verification namespace is required')
    first,rest=read(preparation/'preflight.first10.json'),read(preparation/'preflight.rest26.json')
    if first['config_sha256']!=rest['config_sha256'] or first['usage_ledger_sha256']!=rest['usage_ledger_sha256']:
        raise ValueError('Both fixed shards must share their original config and ledger')
    first_run=Path(first['namespace'])/'run'
    pending_run=Path(rest['namespace'])/'run'
    if pending_run.exists() or (preparation/'terminal-rest26').exists():
        raise ValueError('Pending rest26 has already started')
    manifest=read(first_run/'manifest.json')
    if manifest['completed']!=10 or manifest['cli_invocations']!=2 or manifest['pending']!=0:
        raise ValueError('An actual complete first10/two-call checkpoint is required')
    paths={Path(__file__).resolve(),adopted_database,source_database,
        preparation/'preflight.first10.json',preparation/'preflight.rest26.json',
        preparation/'usage-ledger.v4.json',preparation/'source-snapshot-manifest.json',
        Path(first['ordered_inputs_path']),Path(rest['ordered_inputs_path']),
        Path(first['execution_context_manifest']),Path(rest['execution_context_manifest'])}
    builder=Path(__file__).with_name('build_world_pilot_delivery_v1.py').resolve()
    adopted_manifest=read(adopted_database.parent/'manifest.json')
    quality=Path(adopted_manifest['quality_adoption_review']['path']).resolve(strict=True)
    paths.update({builder,quality})
    paths.update(Path(item['path']) for item in adopted_manifest['quality_adoption_review']['source_audits'])
    paths.update(path for path in adopted_database.parent.iterdir() if path.is_file())
    fixed={str(path):sha(path) for path in paths}
    fixed.update(inventory(first_run));fixed.update(inventory(preparation/'terminal-first10'))
    for relative,digest in first['config']['runtime_source_sha256'].items():
        path=preparation/'runtime'/relative
        if sha(path)!=digest:raise ValueError('Frozen execution source changed')
        fixed[str(path)]=digest
    with sqlite3.connect(adopted_database.as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        rows=db.execute('SELECT job_key,input_hash,work_id FROM next_classification_inputs').fetchall()
    if len(rows)!=10 or {row[0] for row in rows}!=set(manifest['jobs']):
        raise ValueError('Adopted first10 DB does not contain the actual first10 jobs')
    rest_works=[json.loads(line) for line in Path(rest['ordered_inputs_path']).read_bytes().splitlines() if line.strip()]
    if len(rest_works)!=26 or {r[2] for r in rows}&{w['work_id'] for w in rest_works}:
        raise ValueError('Pending rest26 differs or overlaps the adopted first10')
    output.mkdir(parents=True)
    delivery_output=Path(delivery_output).resolve()
    if delivery_output.exists():raise ValueError('First delivery publication must be a fresh destination')
    delivery_command=[sys.executable,str(builder),'--runtime-root',str(preparation/'runtime'),
        '--database',str(adopted_database),'--ledger',first['usage_ledger_path'],
        '--ledger-sha256',first['usage_ledger_sha256'],'--selected-inputs',first['ordered_inputs_path'],
        '--quality-review',str(quality),'--uncertain-policy','metadata-only','--output',str(delivery_output)]
    plan={'version':'real-world-pilot-recovery-v1','prepared_at':now(),'preparation_root':str(preparation),
        'adopted_first10_database':str(adopted_database),'source_database_for_rest26':str(source_database),
        'first10_run':str(first_run),'pending_rest26_run':str(pending_run),'pending_count':0,
        'remaining26_held':True,'first10_delivery_output':str(delivery_output),'delivery_command':delivery_command,
        'script_path':str(Path(__file__).resolve()),'script_sha256':sha(__file__),
        'fixed_sha256':fixed,'first10_run_inventory':inventory(first_run),
        'first10_job_keys':sorted(r[0] for r in rows),'first10_work_ids':sorted(r[2] for r in rows),
        'ledger_path':first['usage_ledger_path'],'ledger_sha256':first['usage_ledger_sha256'],
        'config_sha256':first['config_sha256'],'model_calls_during_preparation':0}
    save(output/'plan.json',plan)
    return {'stage':'recovery_prepared','plan':str(output/'plan.json'),'plan_sha256':sha(output/'plan.json'),'script_sha256':sha(__file__)}


def check_fixed(plan):
    if any(sha(path)!=digest for path,digest in plan['fixed_sha256'].items()):
        raise ValueError('Actual completed checkpoint source/proof/code changed')
    if inventory(plan['first10_run'])!=plan['first10_run_inventory']:
        raise ValueError('Original first10 run inventory changed')
    if Path(plan['pending_rest26_run']).exists() or (Path(plan['preparation_root'])/'terminal-rest26').exists():
        raise ValueError('Remaining26 must remain unexecuted after the failed first10 gate')


def validate_first10(plan):
    check_fixed(plan)
    _,Guard=runtime_api(plan['preparation_root'])
    guard=Guard(plan['ledger_path'],expected_ledger_sha256=plan['ledger_sha256'])
    path=Path(plan['adopted_first10_database'])
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    try:
        packets=[json.loads(row[0]) for row in db.execute('SELECT packet_json FROM next_classification_derivations ORDER BY job_key')]
    finally:
        db.close()
    if len(packets)!=10 or {p['adoption_provenance']['job_key'] for p in packets}!=set(plan['first10_job_keys']):
        raise ValueError('Adopted actual result roster differs from the fixed checkpoint')
    with guard.native_scopes.validation():
        for packet in packets:
            decision=guard.check_derived(packet['native_result'],packet['originating_input_hashes'],'external_llm',source_manifest=packet['source_manifest'])
            if not decision.allowed:raise ValueError('Original native first10 proof denied: '+'; '.join(decision.reasons))
    check_fixed(plan)
    return packets,{'native_results_verified':len(packets),'actual_model_calls':0,
        'original_run_reused':True,'original_adopted_database_sha256':sha(path),
        'snapshot_reports':guard.native_scopes.reports}


class CardCounter(HTMLParser):
    def __init__(self):
        super().__init__();self.cards=0
    def handle_starttag(self,tag,attrs):
        if tag=='article':self.cards+=1


def withdrawal(plan_path):
    plan_path=Path(plan_path).resolve();plan=read(plan_path);output=plan_path.parent/'withdrawal'
    output.mkdir()
    packets,validation=validate_first10(plan)
    api,Guard=runtime_api(plan['preparation_root'])
    ledger=read(plan['ledger_path'])
    target=sorted(plan['first10_work_ids'])[0]
    revoked=[key for key,entry in ledger['inputs'].items() if entry['work_id']==target]
    if not revoked:raise ValueError('Selected work has no complete input projections')
    ledger['revoked_input_hashes']=sorted(set(ledger.get('revoked_input_hashes',[]))|set(revoked))
    revoked_path=output/'usage-ledger.revoked.json';save(revoked_path,ledger)
    revoked_guard=Guard(revoked_path,expected_ledger_sha256=sha(revoked_path))
    denied=[]
    with revoked_guard.native_scopes.validation():
        for packet in packets:
            decision=revoked_guard.check_derived(packet['native_result'],packet['originating_input_hashes'],'display',source_manifest=packet['source_manifest'])
            if decision.allowed:raise ValueError('Changed whole ledger unexpectedly adopted an original native result')
            denied.append({'work_id':packet['native_result']['work_id'],'reasons':decision.reasons})
    from recommend_book.recommendation.pipeline import render_recommendations
    states=[]
    for name,guard in [('before',Guard(plan['ledger_path'],expected_ledger_sha256=plan['ledger_sha256'])),
                       ('withdrawn',revoked_guard),('restored',Guard(plan['ledger_path'],expected_ledger_sha256=plan['ledger_sha256']))]:
        loaded=api.load_next_world_database(plan['adopted_first10_database'],guard=guard,usage='display',content_classified=False)
        rows=[]
        for row in loaded['rows']:
            if row['work_id'] not in plan['first10_work_ids']:continue
            rows.append({key:row.get(key) for key in ('work_id','title','authors','original_language','first_publication_year','read_status','source_urls')})
            rows[-1].update(features={},score=None)
        html=render_recommendations({'ranking':rows},{})
        counter=CardCounter();counter.feed(html)
        if counter.cards!=len(rows):raise ValueError('Rendered card count differs from purpose-checked rows')
        state=output/name;state.mkdir()
        (state/'recommendations.html').write_text(html,encoding='utf-8')
        save(state/'delivery.json',{'metadata_only':True,'ranking_computed':False,'private_profile_used':False,
            'features_withheld':True,'rows':rows,'rendered_cards':counter.cards})
        states.append({'state':name,'cards':counter.cards,'work_ids':[row['work_id'] for row in rows],
            'html_path':str(state/'recommendations.html'),'html_sha256':sha(state/'recommendations.html')})
    if [s['cards'] for s in states]!=[10,9,10] or states[0]['work_ids']!=states[2]['work_ids'] or target in states[1]['work_ids']:
        raise ValueError('Actual metadata withdrawal/restoration failed')
    check_fixed(plan)
    report={'version':'real-world-pilot-withdrawal-v1','actual_model_calls':0,'target_work_id':target,
        'revoked_all_projection_hashes':revoked,'original_ledger_unchanged':True,'original_adopted_database_unchanged':True,
        'original_native_proof_validation':validation,'revoked_native_allowed':0,'revoked_native_denied':denied,
        'scope_limit':'Whole-ledger binding invalidates all ten native results. Display verification uses purpose-checked metadata only; targeted content retention is not claimed.',
        'original_ledger_sha256':plan['ledger_sha256'],'revoked_ledger_sha256':sha(revoked_path),'states':states}
    save(output/'report.json',report)
    return {'stage':'withdrawal_verified','cards':[s['cards'] for s in states],'native_denied':len(denied),'report':str(output/'report.json')}


def boundary_worker(plan_path):
    plan_path=Path(plan_path).resolve();plan=read(plan_path)
    _,validation=validate_first10(plan)
    checkpoint={'version':'real-world-pilot-boundary-checkpoint-v1','plan_sha256':sha(plan_path),
        'script_sha256':sha(__file__),'verified_at':now(),'worker_pid':os.getpid(),
        'validation':validation,'adopted_first10_database_sha256':sha(plan['adopted_first10_database']),
        'pending_rest26_source_database_sha256':sha(plan['source_database_for_rest26']),
        'first10_job_keys':plan['first10_job_keys'],'pending_count':0,'remaining26_held':True,
        'first10_delivery_output':plan['first10_delivery_output'],'delivery_published':False}
    save(plan_path.parent/'checkpoint.json',checkpoint)
    save(plan_path.parent/'boundary-ready.json',{'worker_pid':os.getpid(),'checkpoint_sha256':sha(plan_path.parent/'checkpoint.json')})
    # This is the real coordinator's boundary after actual receipt validation
    # and durable checkpoint, immediately before first10 delivery publication.
    # Its parent kills it here. Only resume invokes the production builder.
    if sys.stdin.buffer.read(1):
        raise ValueError('Boundary worker cannot publish; resume is a separate process')


def interrupt(plan_path):
    plan_path=Path(plan_path).resolve();plan=read(plan_path);check_fixed(plan)
    if (plan_path.parent/'checkpoint.json').exists():raise ValueError('Boundary interruption has already been attempted')
    command=[sys.executable,str(Path(__file__).resolve()),'_boundary-worker','--plan',str(plan_path)]
    save(plan_path.parent/'interrupt-request.json',{'argv':command,'shell':False,'script_sha256':sha(__file__),'started_at':now()})
    stdout=plan_path.parent/'boundary.stdout.txt';stderr=plan_path.parent/'boundary.stderr.txt'
    with stdout.open('xb') as out,stderr.open('xb') as err:
        with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=err,shell=False) as child:
            deadline=time.monotonic()+180
            marker=plan_path.parent/'boundary-ready.json'
            while not marker.exists():
                if child.poll() is not None:raise ValueError('Actual checkpoint worker failed; inspect boundary.stderr.txt')
                if time.monotonic()>deadline:
                    child.kill();child.wait();raise TimeoutError('Actual checkpoint validation timed out')
                time.sleep(.1)
            ready=read(marker)
            if ready['worker_pid']!=child.pid or ready['checkpoint_sha256']!=sha(plan_path.parent/'checkpoint.json'):
                child.kill();child.wait();raise ValueError('Checkpoint worker identity differs')
            child.kill();exit_code=child.wait();pid=child.pid
    if exit_code==0:raise ValueError('Checkpoint worker was not actually interrupted')
    check_fixed(plan)
    result={'version':'real-world-pilot-interrupt-v1','worker_pid':pid,'actual_exit_code':exit_code,
        'process_exit_observed':True,'checkpoint_sha256':sha(plan_path.parent/'checkpoint.json'),
        'plan_sha256':sha(plan_path),'observed_at':now(),'first10_adopted_database_unchanged':True,
        'remaining26_held':True,'first10_delivery_published':False,'actual_model_calls':0}
    save(plan_path.parent/'interruption.json',result)
    return {'stage':'actual_coordinator_interrupted','worker_pid':pid,'exit_code':exit_code,'actual_model_calls':0}


def resume(plan_path,publish):
    plan_path=Path(plan_path).resolve();plan=read(plan_path);interrupted=read(plan_path.parent/'interruption.json')
    checkpoint=read(plan_path.parent/'checkpoint.json')
    if interrupted['checkpoint_sha256']!=sha(plan_path.parent/'checkpoint.json') or checkpoint['plan_sha256']!=sha(plan_path) or checkpoint['script_sha256']!=sha(__file__):
        raise ValueError('Original durable recovery checkpoint changed')
    if interrupted['actual_exit_code']==0 or interrupted['process_exit_observed'] is not True:
        raise ValueError('An actual interrupted coordinator exit is required')
    _,validation=validate_first10(plan)
    if Path(plan['first10_delivery_output']).exists():
        raise ValueError('First10 delivery is already published; refuse duplicate publication')
    result={'version':'real-world-pilot-resume-v1','first10_verified_and_reused':validation['native_results_verified'],
        'first10_model_calls':0,'first10_adopted_database_sha256':sha(plan['adopted_first10_database']),
        'checkpoint_sha256':sha(plan_path.parent/'checkpoint.json'),'pending_count':0,'remaining26_held':True,
        'actual_model_calls':0,'publish_authorized':publish,
        'validation':validation,'resumed_at':now()}
    if not publish:
        save(plan_path.parent/'resume-readonly-verification.json',result)
        return result
    command=plan['delivery_command']
    save(plan_path.parent/'resume-publication-admission.json',{**result,'argv':command,'script_sha256':sha(__file__),'plan_sha256':sha(plan_path)})
    with (plan_path.parent/'resume.stdout.txt').open('xb') as out,(plan_path.parent/'resume.stderr.txt').open('xb') as err:
        with subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,shell=False) as child:
            save(plan_path.parent/'resume-process-start.json',{'pid':child.pid,'argv':command,'started_at':now()})
            exit_code=child.wait();pid=child.pid
    check_fixed(plan)
    result.update(actual_delivery_builder_pid=pid,actual_exit_code=exit_code,process_exit_observed=True,
        first10_run_unchanged=True,first10_database_unchanged=True,observed_at=now())
    if exit_code==0:
        delivery=Path(plan['first10_delivery_output'])
        report=read(delivery/'report.json')
        result.update(delivery_output=str(delivery),delivery_inventory=inventory(delivery),
            new_display_n=report['new_display_n'],new_feature_bearing_n=report['new_feature_bearing_n'],
            merged_display_n=report['merged_display_n'],private_profile_connection_n=report['private_profile_connection_n'])
    save(plan_path.parent/'resume-process-exit.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','withdrawal','interrupt','resume','_boundary-worker'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--preparation-root',type=Path)
    parser.add_argument('--adopted-database',type=Path)
    parser.add_argument('--source-database',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--delivery-output',type=Path)
    parser.add_argument('--publish-first10',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':result=prepare(args.preparation_root,args.adopted_database,args.source_database,args.output,args.delivery_output)
    elif args.command=='withdrawal':result=withdrawal(args.plan)
    elif args.command=='interrupt':result=interrupt(args.plan)
    elif args.command=='_boundary-worker':boundary_worker(args.plan);return
    else:result=resume(args.plan,args.publish_first10)
    print(json.dumps(result,ensure_ascii=True))
    if result.get('actual_exit_code',0):raise SystemExit(result['actual_exit_code'])


if __name__=='__main__':main()
