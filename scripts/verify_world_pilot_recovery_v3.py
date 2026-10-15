"""Windows direct-interpreter entrypoint for the unchanged v2 coordinator.

The venv Python redirector may have a different PID from its interpreter. This
entrypoint launches the actual base interpreter with explicit existing module
paths. All validation and publication code remains the separately pinned v2.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

import verify_world_pilot_recovery_v2 as coordinator

base=coordinator.base


def prepare(args):
    output=args.output.resolve()
    if output.exists():raise ValueError('Fresh direct-interpreter recovery namespace required')
    result=coordinator.prepare(args.preparation_root,args.adopted_database,args.source_database,output/'coordinator-plan',args.delivery_output)
    plan=base.read(result['plan'])
    plan.update(version='real-world-pilot-direct-interpreter-v3',script_path=str(Path(__file__).resolve()),
        script_sha256=base.sha(__file__),coordinator_implementation_path=str(Path(coordinator.__file__).resolve()))
    plan['fixed_sha256'][str(Path(__file__).resolve())]=base.sha(__file__)
    base.save(output/'plan.json',plan)
    return {'stage':'direct_interpreter_recovery_prepared','plan':str(output/'plan.json'),
        'plan_sha256':base.sha(output/'plan.json'),'script_sha256':base.sha(__file__)}


def interrupt(plan_path):
    plan=base.read(plan_path);base.check_fixed(plan)
    if (plan_path.parent/'checkpoint.json').exists():raise ValueError('Interruption already attempted')
    command=[getattr(sys,'_base_executable',sys.executable),str(Path(__file__).resolve()),'_boundary-worker','--plan',str(plan_path)]
    env=dict(os.environ,PYTHONPATH=os.pathsep.join(p for p in sys.path if p),PYTHONDONTWRITEBYTECODE='1')
    base.save(plan_path.parent/'interrupt-request.json',{'argv':command,'shell':False,'PYTHONPATH':env['PYTHONPATH'],
        'entrypoint_sha256':base.sha(__file__),'coordinator_sha256':base.sha(coordinator.__file__),'started_at':base.now()})
    with (plan_path.parent/'boundary.stdout.txt').open('xb') as out,(plan_path.parent/'boundary.stderr.txt').open('xb') as err:
        with subprocess.Popen(command,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,shell=False) as child:
            base.save(plan_path.parent/'boundary-process-start.json',{'pid':child.pid,'argv':command,'started_at':base.now()})
            deadline=time.monotonic()+180;marker=plan_path.parent/'boundary-ready.json'
            while not marker.exists():
                if child.poll() is not None:raise ValueError('Actual coordinator failed; inspect boundary.stderr.txt')
                if time.monotonic()>deadline:
                    child.kill();child.wait();raise TimeoutError('Actual checkpoint validation timed out')
                time.sleep(.1)
            ready=base.read(marker)
            if ready['worker_pid']!=child.pid or ready['checkpoint_sha256']!=base.sha(plan_path.parent/'checkpoint.json'):
                child.kill();child.wait();raise ValueError('Direct interpreter coordinator identity differs')
            child.kill();exit_code=child.wait();pid=child.pid
    if exit_code==0:raise ValueError('Actual coordinator was not interrupted')
    base.check_fixed(plan)
    if Path(plan['first10_delivery_output']).exists():raise ValueError('Delivery published before controlled boundary')
    result={'version':'actual-publication-interrupt-v3','popen_pid':pid,'worker_pid':ready['worker_pid'],
        'actual_exit_code':exit_code,'process_exit_observed':True,
        'checkpoint_sha256':base.sha(plan_path.parent/'checkpoint.json'),'plan_sha256':base.sha(plan_path),
        'observed_at':base.now(),'first10_delivery_published':False,'first10_adopted_database_unchanged':True,
        'actual_model_calls':0,'remaining26_held':True}
    base.save(plan_path.parent/'interruption.json',result)
    return {'stage':'actual_publication_coordinator_interrupted','worker_pid':pid,'exit_code':exit_code,'actual_model_calls':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','interrupt','resume','_boundary-worker'))
    for flag in ('plan','preparation-root','adopted-database','source-database','output','delivery-output'):
        parser.add_argument('--'+flag,type=Path)
    parser.add_argument('--publish-first10',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':result=prepare(args)
    elif args.command=='interrupt':result=interrupt(args.plan.resolve())
    elif args.command=='_boundary-worker':result=coordinator.boundary_worker(args.plan.resolve())
    else:result=coordinator.resume(args.plan.resolve(),args.publish_first10)
    print(base.json.dumps(result,ensure_ascii=True))
    if result.get('actual_exit_code',0):raise SystemExit(result['actual_exit_code'])


if __name__=='__main__':main()
