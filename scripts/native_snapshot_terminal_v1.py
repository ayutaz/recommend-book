"""Run a prepared future namespace and preserve actual parent PID/exit proof.

This command starts the real Codex classifier. Preparation is performed by
prepare_native_snapshot_runtime_v1.py and must already be complete.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.write('\n')


def execute(preparation_root, run_name, source_database):
    preparation_root=Path(preparation_root).resolve(strict=True)
    preflight_path=preparation_root/('preflight.'+run_name+'.json')
    preflight=json.loads(preflight_path.read_bytes())
    namespace=Path(preflight['namespace']).resolve(strict=True)
    runtime=preparation_root/'runtime'
    if not namespace.is_relative_to(runtime/'data/autonomous-next-v3/classification'):
        raise ValueError('Prepared execution namespace differs')
    if sha(namespace/'preparation-manifest.json')!=preflight['preparation_sha256']:
        raise ValueError('Prepared manifest changed')
    source_database=Path(source_database).resolve(strict=True)
    source_sha=sha(source_database)
    observation=preparation_root/('terminal-'+run_name)
    observation.mkdir()
    context=Path(preflight['execution_context_manifest']).resolve(strict=True)
    context_sha=sha(context)
    command=[sys.executable,str(runtime/'scripts/run_guarded_classification_v4.py'),'run',
        '--namespace',str(namespace),'--preparation-sha256',preflight['preparation_sha256']]
    env=dict(os.environ,PYTHONPATH=str(runtime/'src'),PYTHONDONTWRITEBYTECODE='1')
    removed=[]
    for key in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','OPENAI_API_BASE'):
        if key in env:
            env.pop(key)
            removed.append(key)
    request={'version':'native-snapshot-terminal-request-v1','argv':command,'shell':False,
        'stdin':'DEVNULL','cwd':str(Path.cwd()),'PYTHONPATH':env['PYTHONPATH'],
        'removed_api_environment_keys':removed,'preflight_path':str(preflight_path),'preflight_sha256':sha(preflight_path),
        'source_database':str(source_database),'source_database_sha256':source_sha,
        'execution_context_sha256_before':context_sha,'coordinator_path':str(Path(__file__).resolve()),
        'coordinator_sha256':sha(Path(__file__)),'started_at':datetime.now(timezone.utc).isoformat()}
    save(observation/'request.json',request)
    with (observation/'stdout.txt').open('xb') as stdout, (observation/'stderr.txt').open('xb') as stderr:
        with subprocess.Popen(command,cwd=Path.cwd(),env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,shell=False) as child:
            save(observation/'process-start.json',{'pid':child.pid,'argv':command,'started_at':datetime.now(timezone.utc).isoformat()})
            exit_code=child.wait()
            pid=child.pid
    exit_observation={'version':'native-snapshot-terminal-exit-v1','process_pid':pid,'actual_exit_code':exit_code,
        'process_exit_observed':True,'observed_at':datetime.now(timezone.utc).isoformat(),
        'stdout_sha256':sha(observation/'stdout.txt'),'stderr_sha256':sha(observation/'stderr.txt')}
    save(observation/'process-exit.json',exit_observation)
    if exit_code:
        return {'stage':'actual_run_failed','exit_code':exit_code,'process_pid':pid,'observation':str(observation)}
    if sha(source_database)!=source_sha or sha(context)!=context_sha:
        raise ValueError('Protected source/context changed during actual execution')
    run=namespace/'run'
    manifest=json.loads((run/'manifest.json').read_bytes())
    migration=observation/'new-only-migration.json'
    save(migration,{'version':'native-snapshot-new-only-migration-v1','historical_results_rebound':False,
        'source_database_sha256':source_sha,'new_config_sha256':preflight['config_sha256'],'llm_started':False})
    proof={'schema_version':'classification-terminal-proof-v1','observed_at':exit_observation['observed_at'],
        'session_id':'actual-native-snapshot-coordinator-pid-'+str(pid),'exit_code':exit_code,'process_exit_observed':True,
        'process_pid':pid,'run_id':manifest['run_id'],'manifest_sha256':sha(run/'manifest.json'),
        'results_sha256':sha(run/'results.jsonl'),'ordered_inputs_sha256':sha(preflight['ordered_inputs_path']),
        'execution_context_manifest_sha256':context_sha,'migration_manifest_sha256':sha(migration),
        'cli_profile_sha256_before':context_sha,'cli_profile_sha256_after':sha(context),'artifact_sha256':{'database':source_sha},
        'parent_request_sha256':sha(observation/'request.json'),'parent_exit_observation_sha256':sha(observation/'process-exit.json')}
    proof_path=observation/'terminal-proof.json'
    save(proof_path,proof)
    result={'stage':'actual_run_observed','exit_code':exit_code,'process_pid':pid,'selected':manifest['selected'],
        'completed':manifest['completed'],'failed':manifest['failed'],'pending':manifest['pending'],
        'cli_invocations':manifest['cli_invocations'],'terminal_proof':str(proof_path),'migration_manifest':str(migration),
        'run_dir':str(run),'execution_context_manifest':str(context),'ordered_inputs':preflight['ordered_inputs_path'],
        'usage_ledger':preflight['usage_ledger_path'],'cli_profile':str(context),'source_database':str(source_database)}
    save(observation/'adoption-arguments.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation-root',type=Path,required=True)
    parser.add_argument('--run-name',default='first10')
    parser.add_argument('--source-database',type=Path,required=True)
    args=parser.parse_args()
    result=execute(args.preparation_root,args.run_name,args.source_database)
    print(json.dumps(result,ensure_ascii=True))
    return result['exit_code']


if __name__=='__main__':
    raise SystemExit(main())
