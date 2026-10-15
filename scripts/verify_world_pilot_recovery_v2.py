"""Actual first10 publication coordinator with a durable interruption boundary.

Imports read-only validation helpers from the separately pinned v1 withdrawal
script. No command can start a model; rest26 remains held.
"""
import argparse
from pathlib import Path
import os
import subprocess
import sys
import time

import verify_world_pilot_recovery_v1 as base


def prepare(preparation, adopted, source, output, delivery):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Fresh recovery namespace required')
    original=base.prepare(preparation,adopted,source,output/'source-plan',delivery)
    plan=base.read(original['plan'])
    plan.update(version='real-world-pilot-publication-recovery-v2',script_path=str(Path(__file__).resolve()),script_sha256=base.sha(__file__))
    plan['fixed_sha256'][str(Path(__file__).resolve())]=base.sha(__file__)
    # base.prepare already pins v1 itself, its source DB and every actual receipt.
    base.save(output/'plan.json',plan)
    return {'stage':'actual_publication_recovery_prepared','plan':str(output/'plan.json'),
        'plan_sha256':base.sha(output/'plan.json'),'script_sha256':base.sha(__file__)}


def publication_result(plan_path, validation, origin):
    plan=base.read(plan_path)
    return {'version':'real-world-pilot-resume-v2','coordinator_path':origin,
        'first10_verified_and_reused':validation['native_results_verified'],
        'first10_model_calls':0,'actual_model_calls':0,'remaining26_held':True,
        'first10_adopted_database_sha256':base.sha(plan['adopted_first10_database']),
        'checkpoint_sha256':base.sha(plan_path.parent/'checkpoint.json'),
        'validation':validation,'resumed_at':base.now()}


def publish_delivery(plan_path,result):
    """The same genuine production continuation for normal and resumed runs."""
    plan=base.read(plan_path);base.check_fixed(plan)
    output=Path(plan['first10_delivery_output'])
    if output.exists():raise ValueError('First10 delivery already published; duplicate publication refused')
    command=plan['delivery_command']
    base.save(plan_path.parent/'publication-admission.json',{**result,'argv':command,
        'script_sha256':base.sha(__file__),'plan_sha256':base.sha(plan_path)})
    with (plan_path.parent/'publication.stdout.txt').open('xb') as out,(plan_path.parent/'publication.stderr.txt').open('xb') as err:
        with subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=out,stderr=err,shell=False) as child:
            base.save(plan_path.parent/'publication-process-start.json',{'pid':child.pid,'argv':command,'started_at':base.now()})
            exit_code=child.wait();pid=child.pid
    base.check_fixed(plan)
    result.update(actual_delivery_builder_pid=pid,actual_exit_code=exit_code,process_exit_observed=True,
        first10_run_unchanged=True,first10_database_unchanged=True,observed_at=base.now())
    if exit_code==0:
        report=base.read(output/'report.json')
        result.update(delivery_output=str(output),delivery_inventory=base.inventory(output),
            new_display_n=report['new_display_n'],new_feature_bearing_n=report['new_feature_bearing_n'],
            merged_display_n=report['merged_display_n'],private_profile_connection_n=report['private_profile_connection_n'])
    base.save(plan_path.parent/'publication-process-exit.json',result)
    return result


def boundary_worker(plan_path):
    plan=base.read(plan_path)
    _,validation=base.validate_first10(plan)
    checkpoint={'version':'actual-publication-checkpoint-v2','plan_sha256':base.sha(plan_path),
        'script_sha256':base.sha(__file__),'worker_pid':os.getpid(),'verified_at':base.now(),
        'validation':validation,'first10_adopted_database_sha256':base.sha(plan['adopted_first10_database']),
        'first10_job_keys':plan['first10_job_keys'],'remaining26_held':True,
        'first10_delivery_output':plan['first10_delivery_output'],'delivery_published':False}
    base.save(plan_path.parent/'checkpoint.json',checkpoint)
    base.save(plan_path.parent/'boundary-ready.json',{'worker_pid':os.getpid(),
        'checkpoint_sha256':base.sha(plan_path.parent/'checkpoint.json')})
    # The test parent kills this real coordinator before releasing publication.
    # An uninterrupted parent can release the same genuine production builder.
    if sys.stdin.buffer.read(1)!=b'P':raise ValueError('Publication continuation was not released')
    result=publication_result(plan_path,validation,'released-boundary')
    return publish_delivery(plan_path,result)


def interrupt(plan_path):
    plan=base.read(plan_path);base.check_fixed(plan)
    if (plan_path.parent/'checkpoint.json').exists():raise ValueError('Interruption already attempted')
    command=[sys.executable,str(Path(__file__).resolve()),'_boundary-worker','--plan',str(plan_path)]
    base.save(plan_path.parent/'interrupt-request.json',{'argv':command,'shell':False,
        'script_sha256':base.sha(__file__),'started_at':base.now()})
    with (plan_path.parent/'boundary.stdout.txt').open('xb') as out,(plan_path.parent/'boundary.stderr.txt').open('xb') as err:
        with subprocess.Popen(command,stdin=subprocess.PIPE,stdout=out,stderr=err,shell=False) as child:
            deadline=time.monotonic()+180;marker=plan_path.parent/'boundary-ready.json'
            while not marker.exists():
                if child.poll() is not None:raise ValueError('Actual coordinator failed; inspect boundary.stderr.txt')
                if time.monotonic()>deadline:
                    child.kill();child.wait();raise TimeoutError('Actual checkpoint validation timed out')
                time.sleep(.1)
            ready=base.read(marker)
            if ready['worker_pid']!=child.pid or ready['checkpoint_sha256']!=base.sha(plan_path.parent/'checkpoint.json'):
                child.kill();child.wait();raise ValueError('Actual coordinator identity differs')
            child.kill();exit_code=child.wait();pid=child.pid
    if exit_code==0:raise ValueError('Actual coordinator was not interrupted')
    base.check_fixed(plan)
    if Path(plan['first10_delivery_output']).exists():raise ValueError('Delivery published before controlled boundary')
    result={'version':'actual-publication-interrupt-v2','worker_pid':pid,'actual_exit_code':exit_code,
        'process_exit_observed':True,'checkpoint_sha256':base.sha(plan_path.parent/'checkpoint.json'),
        'plan_sha256':base.sha(plan_path),'observed_at':base.now(),'first10_delivery_published':False,
        'first10_adopted_database_unchanged':True,'actual_model_calls':0,'remaining26_held':True}
    base.save(plan_path.parent/'interruption.json',result)
    return {'stage':'actual_publication_coordinator_interrupted','worker_pid':pid,'exit_code':exit_code,'actual_model_calls':0}


def resume(plan_path,publish):
    plan=base.read(plan_path);interrupted=base.read(plan_path.parent/'interruption.json');checkpoint=base.read(plan_path.parent/'checkpoint.json')
    if interrupted['checkpoint_sha256']!=base.sha(plan_path.parent/'checkpoint.json') or checkpoint['plan_sha256']!=base.sha(plan_path) or checkpoint['script_sha256']!=base.sha(__file__):
        raise ValueError('Durable actual coordinator checkpoint changed')
    if interrupted['actual_exit_code']==0 or interrupted['process_exit_observed'] is not True:
        raise ValueError('Actual interrupted coordinator exit required')
    _,validation=base.validate_first10(plan)
    result=publication_result(plan_path,validation,'resume-after-actual-kill')
    if not publish:
        base.save(plan_path.parent/'resume-readonly-verification.json',result)
        return result
    return publish_delivery(plan_path,result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','interrupt','resume','_boundary-worker'))
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--preparation-root',type=Path)
    parser.add_argument('--adopted-database',type=Path)
    parser.add_argument('--source-database',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--delivery-output',type=Path)
    parser.add_argument('--publish-first10',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':result=prepare(args.preparation_root,args.adopted_database,args.source_database,args.output,args.delivery_output)
    elif args.command=='interrupt':result=interrupt(args.plan.resolve())
    elif args.command=='_boundary-worker':result=boundary_worker(args.plan.resolve())
    else:result=resume(args.plan.resolve(),args.publish_first10)
    print(base.json.dumps(result,ensure_ascii=True))
    if result.get('actual_exit_code',0):raise SystemExit(result['actual_exit_code'])


if __name__=='__main__':main()
