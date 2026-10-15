"""Append-only recovery and failed-run partial audit; no original-file mutation."""
from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import json
import sqlite3
import subprocess
import time

from recommend_book.autonomous_resolution import canonical,digest,prompt,validate_decision,blind_side,ndl_raw_fields
from recommend_book.autonomous_resolution_audit import strict_json,decision_issues,verify_cached_attempt,verify_stdout_payload,validate_output_boundary
from recommend_book.classification.runner import resolve_executable,parse_events

ROOT=Path(__file__).resolve().parents[2]


def now():return datetime.now(timezone.utc).isoformat()


def write_once(path,value):
    path=Path(path)
    raw=value if isinstance(value,bytes) else (canonical(value)+'\n').encode('utf8')
    with path.open('xb') as handle:handle.write(raw)


def pending_subset(cases,finalized):
    ids=[c['case_id'] for c in cases]
    if len(set(ids))!=len(ids) or not set(finalized)<=set(ids):raise ValueError('Invalid finalized subset')
    return [c for c in cases if c['case_id'] not in finalized]


def validate_batch(payload,batch):
    rows=payload.get('decisions') if isinstance(payload,dict) else None
    if not isinstance(rows,list) or len(rows)!=len(batch) or [r.get('case_id') for r in rows]!=[c['case_id'] for c in batch]:
        return [{'case_id':c['case_id'],'status':'held','decision':None,'raw_decision':None,'reason':'invalid_semantic: batch count/order/identity'} for c in batch]
    statuses=[]
    for row,case in zip(rows,batch):
        try:
            issues=decision_issues(row,case)
            if issues:raise ValueError('; '.join(issues))
            statuses.append({'case_id':case['case_id'],'status':'completed','decision':row,'raw_decision':row,'reason':None})
        except (ValueError,KeyError,TypeError) as exc:
            statuses.append({'case_id':case['case_id'],'status':'held','decision':None,'raw_decision':row,'reason':'invalid_semantic: '+str(exc)})
    return statuses


def failure_category(message):
    text=message.lower()
    for label,needles in [('authentication',('authentication','unauthorized','401','invalid api key')),('quota',('quota','usage limit','weekly limit')),('tool_use',('tool_use',)),('invalid_semantic',('invalid_semantic',))]:
        if any(n in text for n in needles):return label,False
    if '429' in text or 'rate limit' in text:return 'rate_limit',True
    if 'timeout' in text:return 'timeout',True
    return 'transient_cli',True


def snapshot(paths):return {str(Path(p).resolve()):digest(Path(p).read_bytes()) for p in paths}


def assert_snapshot(saved):
    if snapshot(saved)!=saved:raise ValueError('Protected bytes changed')


def _argv(command,directory,final,model):
    return [*command,'exec','--model',model,'--sandbox','read-only','--ephemeral','--ignore-user-config','--skip-git-repo-check','--json','--color','never','-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.multi_agent=false','--cd',str(directory/'workspace'),'--output-schema',str(directory/'schema.json'),'-o',str(final),'-']


def material_audit(directory,cases,manifest):
    databases=[Path(p) for p in manifest['source_sha256'] if str(p).endswith('.sqlite')]
    pairpaths=[Path(p) for p in manifest['source_sha256'] if Path(p).name=='pairs.jsonl']
    if len(databases)!=1 or len(pairpaths)!=1:raise ValueError('Unique source database and pair pack required')
    db=sqlite3.connect('file:'+databases[0].as_posix()+'?mode=ro&immutable=1',uri=True);db.execute('pragma query_only=on')
    records={v:strict_json(r) for v,r in db.execute('select version_id,record_json from source_records')}
    expected=[]
    for original in map(strict_json,pairpaths[0].read_text(encoding='utf8').splitlines()):
        for s in original['sides']:
            if records[s['version_id']]!=s['record']:raise ValueError('Pair record differs from retained SQL version')
        expected.append({'case_id':original['case_id'],'case_kind':'hard_pair','sides':[blind_side(s) for s in original['sides']]})
    for wid,version in db.execute('select work_id,source_version_id from works where resolved=0 order by work_id'):
        r=records[version];side=blind_side({'version_id':version,'record':r})
        for i,field in enumerate(ndl_raw_fields(Path(r['raw_path']).read_bytes(),r['source_id'])):
            side['evidence'].append({'evidence_id':version+':raw:'+str(i),'field':'raw_bibliographic','text':canonical(field)})
        expected.append({'case_id':'unresolved_'+wid,'case_kind':'unresolved_record','sides':[side]})
    db.close()
    if expected!=cases:raise ValueError('All original case/evidence values differ from immutable SQL/raw reconstruction')
    return {'cases':len(cases),'pairs':sum(c['case_kind']=='hard_pair' for c in cases),'unresolved':sum(c['case_kind']=='unresolved_record' for c in cases),'all_original_cases_sql_raw_exact':True,'human_gold':0}


def audit_failed_partial(directory,role_audit):
    directory=Path(directory).resolve();m=strict_json((directory/'manifest.json').read_bytes())
    if m['status']!='failed':raise ValueError('Failed original terminal required')
    protected={str(directory/'inputs.jsonl'):m['input_sha256'],str(directory/'schema.json'):m['schema_sha256'],**m['source_sha256'],**m['execution_artifact_sha256']};assert_snapshot(protected)
    inventory=snapshot(p for p in directory.rglob('*') if p.is_file())
    cases=[strict_json(line) for line in (directory/'inputs.jsonl').read_text(encoding='utf8').splitlines()]
    material=material_audit(directory,cases,m)
    role_versions={r['source_version_id'] for r in strict_json(Path(role_audit).read_bytes())['records'] if r['role_ambiguity']}
    finalized={p.name[6:-5] for p in (directory/'lanes').glob('final-*.json')};pending=pending_subset(cases,finalized)
    if finalized!={c['case_id'] for c in cases[:len(finalized)]}:raise ValueError('Finalized cases are not original prefix')
    finals=[];attempts=[];expected_files={str(directory/name) for name in ['manifest.json','inputs.jsonl','schema.json','unresolved-source-ledger.json']}
    for pos in range(0,len(finalized),m['batch_size']):
        batch=cases[pos:pos+m['batch_size']]
        if len(batch)!=m['batch_size']:raise ValueError('Incomplete finalized batch')
        a=verify_cached_attempt(directory,'A',batch,None,m['model'],m['cli_version']);b=verify_cached_attempt(directory,'B',batch,None,m['model'],m['cli_version']);attempts.extend([a,b])
        dis=[i for i,(x,y) in enumerate(zip(a['decisions'],b['decisions'])) if x['decision']!=y['decision']]
        third=verify_cached_attempt(directory,'C',[batch[i] for i in dis],[{'a':a['decisions'][i],'b':b['decisions'][i]} for i in dis],m['model'],m['cli_version']) if dis else None
        if third:attempts.append(third)
        c=dict(zip(dis,third['decisions'])) if third else {}
        for i,case in enumerate(batch):
            path=directory/'lanes'/('final-'+case['case_id']+'.json');stored=strict_json(path.read_bytes());expected=c.get(i,a['decisions'][i]);expected_files.add(str(path))
            if stored['final']!=expected or stored['lane_a']!=a['decisions'][i] or stored['lane_b']!=b['decisions'][i] or stored['lane_c']!=c.get(i) or stored['actor']!='AI' or stored['human_gold'] is not False or stored['authority_merge_approved'] is not False:raise ValueError('Original final provenance differs')
            issues=decision_issues(expected,case)
            if case['case_kind']=='hard_pair' and any(s['version_id'] in role_versions for s in case['sides']):issues.append('derived_author_role_unverified_against_original_provider')
            finals.append({'case_id':case['case_id'],'original_decision':expected['decision'],'audited_decision':'uncertain' if issues else expected['decision'],'status':'held' if issues or expected['decision']=='uncertain' else 'completed','issues':issues,'raw_final':stored,'origin_run':str(directory),'origin_sha256':inventory[str(path)],'actor':'AI','human_gold':False,'authority_merge_approved':False})
    for a in attempts:expected_files.update(a['raw_file_sha256'])
    failed_batch=cases[len(finalized):len(finalized)+m['batch_size']];failed_attempts=[]
    for lane in ['A','B']:
        key=digest(canonical({'lane':lane,'cases':failed_batch,'prior':None,'cli_version':m['cli_version'],'model':m['model']}).encode());stem=directory/'attempts'/(lane+'-'+key)
        paths={s:Path(str(stem)+s) for s in ['.prompt.txt','.command.json','.stdout.jsonl','.stderr.txt','.observation.json','.final.json']}
        if not all(p.is_file() for p in paths.values()):raise ValueError('Failed batch raw missing')
        expected_files.update(map(str,paths.values()));text=prompt(failed_batch,lane)
        command=strict_json(paths['.command.json'].read_bytes());expected_argv=_argv(command['argv'][:-len(_argv([],directory,paths['.final.json'],m['model']))],directory,paths['.final.json'],m['model'])
        if command['argv']!=expected_argv or command['stdin_sha256']!=digest(text.encode()) or paths['.prompt.txt'].read_text(encoding='utf8')!=text:raise ValueError('Failed attempt recipe/argv differs')
        stdout=paths['.stdout.jsonl'].read_text(encoding='utf8');events=parse_events(stdout);obs=strict_json(paths['.observation.json'].read_bytes());payload=strict_json(paths['.final.json'].read_bytes());verify_stdout_payload(stdout,payload)
        if obs['exit_code']!=0 or obs['process_exit_observed'] is not True or obs['events']!=events or events['tool_calls'] or events['event_errors']:raise ValueError('Failed attempt terminal binding differs')
        statuses=validate_batch(payload,failed_batch);cache=directory/'lanes'/(lane+'-'+key+'.json')
        if cache.exists():
            if strict_json(cache.read_bytes())!=payload['decisions']:raise ValueError('Failed batch cache/final differs')
            expected_files.add(str(cache))
        failed_attempts.append({'lane':lane,'job_key':key,'usage':events['usage'],'statuses':statuses,'raw_file_sha256':snapshot(paths.values())})
    usage=Counter()
    for a in attempts+failed_attempts:usage.update(a['usage'])
    if len(attempts)+len(failed_attempts)!=m['cli_calls_this_execution'] or dict(usage)!=m['usage_this_execution']:raise ValueError('All original CLI calls/usage differ')
    if expected_files!=set(inventory):raise ValueError('Unexpected or missing original run files')
    assert_snapshot(protected);assert_snapshot(inventory)
    return {'version':'failed-resolution-partial-strict-audit-v1','original_status':'failed','original_finalized':len(finals),'pending_count':len(pending),'cases':finals,'failed_batch_attempts':failed_attempts,'attempts':attempts,'original_usage':dict(usage),'original_cli_calls':len(attempts)+len(failed_attempts),'source_material_audit':material,'protected_sha256':protected,'original_inventory_sha256':inventory,'all_expected_file_inventory_verified':True,'run_files_unchanged':True,'results_index_scope':'Full results.jsonl absent in failed v3; each original finalized case file independently bound','human_gold':0,'authority_accepted':0}


def recover(original,output,role_audit,terminal_observation,*,batch_size=10,timeout=900):
    # Keep offline recovery audits and recipes, but never bypass the central usage guard.
    raise RuntimeError('Archived runner: no fresh calls; guarded path only')
    original=Path(original).resolve();output=Path(output).resolve();role_audit=Path(role_audit).resolve()
    m=strict_json((original/'manifest.json').read_bytes());protected=[original/'inputs.jsonl',original/'schema.json',*m['source_sha256'],*m['execution_artifact_sha256']]
    validate_output_boundary(output,original,role_audit,protected)
    partial=audit_failed_partial(original,role_audit)
    original_lines=(original/'inputs.jsonl').read_bytes().splitlines(keepends=True);allcases=[strict_json(line) for line in original_lines];ids={c['case_id'] for c in partial['cases']};cases=pending_subset(allcases,ids)
    output.mkdir(parents=True);(output/'workspace').mkdir();(output/'attempts').mkdir();(output/'cases').mkdir()
    write_once(output/'original-partial-audit.json',partial);write_once(output/'original-terminal-observation.json',terminal_observation)
    write_once(output/'inputs.jsonl',b''.join(line for line in original_lines if strict_json(line)['case_id'] not in ids));write_once(output/'schema.json',(original/'schema.json').read_bytes())
    command=resolve_executable();version_process=subprocess.run([*command,'--version'],capture_output=True,timeout=30,shell=False)
    write_once(output/'cli-version.stdout',version_process.stdout);write_once(output/'cli-version.stderr',version_process.stderr);write_once(output/'cli-version-observation.json',{'argv':[*command,'--version'],'exit_code':version_process.returncode,'observed_at':now(),'actor':'AI'})
    version=version_process.stdout.decode('utf8').strip()
    if version_process.returncode or version!=m['cli_version']:raise ValueError('Same exact original model CLI identity required')
    code=[Path(__file__),ROOT/'scripts/run_autonomous_resolution_recovery.py',ROOT/'src/recommend_book/autonomous_resolution_audit.py',ROOT/'src/recommend_book/autonomous_resolution.py',ROOT/'src/recommend_book/classification/runner.py',ROOT/'tests/test_autonomous_resolution_recovery.py']
    write_once(output/'prepared-manifest.json',{'case_count':len(cases),'source_case_count':len(allcases),'original_finalized':len(ids),'model':m['model'],'cli_version':version,'input_sha256':digest((output/'inputs.jsonl').read_bytes()),'schema_sha256':digest((output/'schema.json').read_bytes()),'max_concurrency':2,'max_attempts':3,'batch_size':batch_size,'created_at':now(),'actor':'AI','human_gold':0,'authority_accepted':0})
    pre=snapshot([*protected,*partial['original_inventory_sha256'],role_audit,*code,*[p for p in output.iterdir() if p.is_file()]])
    write_once(output/'prelaunch-source-code-snapshot.json',{'observed_at':now(),'actor':'AI','scope':'Actual read immediately before recovery LLM launches; original v3 prelaunch is not retroactively claimed','sha256':pre})
    assert_snapshot(pre)
    started=time.monotonic();attempts=[];finals=[]
    def execute(lane,batch,prior=None):
        key=digest(canonical({'lane':lane,'cases':batch,'prior':prior,'cli_version':version,'model':m['model']}).encode());text=prompt(batch,lane,prior)
        for n in range(1,4):
            attempt=output/'attempts'/(lane+'-'+key+'-'+str(n));attempt.mkdir();final=attempt/'final.json';argv=_argv(command,output,final,m['model'])
            write_once(attempt/'prompt.txt',text.encode());write_once(attempt/'command.json',{'argv':argv,'stdin_sha256':digest(text.encode()),'actor':'AI','attempt':n,'job_key':key,'lane':lane})
            try:
                process=subprocess.run(argv,input=text.encode(),capture_output=True,timeout=timeout,shell=False);stdout=process.stdout;stderr=process.stderr;exit_code=process.returncode;timed_out=False
            except subprocess.TimeoutExpired as exc:
                stdout=exc.stdout or b'';stderr=exc.stderr or b'';exit_code=None;timed_out=True
            write_once(attempt/'stdout.jsonl',stdout);write_once(attempt/'stderr.txt',stderr)
            decoded=stdout.decode('utf8',errors='replace');events=parse_events(decoded);category=None;retryable=False;statuses=None
            try:
                if timed_out:category,retryable='timeout',True
                elif events['tool_calls']:category,retryable='tool_use',False
                elif exit_code or events['event_errors']:category,retryable=failure_category(stderr.decode('utf8',errors='replace')+' '+str(events['event_errors']))
                else:
                    payload=strict_json(final.read_bytes());verify_stdout_payload(decoded,payload);statuses=validate_batch(payload,batch)
                    if any(s['status']=='held' for s in statuses):category,retryable='invalid_semantic',False
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:category,retryable='invalid_semantic',False
            obs={'attempt':n,'lane':lane,'job_key':key,'exit_code':exit_code,'process_exit_observed':not timed_out,'timed_out':timed_out,'events':events,'category':category,'retryable':retryable,'observed_at':now(),'actor':'AI'}
            write_once(attempt/'observation.json',obs)
            hashes=snapshot(p for p in attempt.iterdir() if p.is_file());write_once(attempt/'file-hashes.json',hashes)
            attempts.append({'path':str(attempt),'observation':obs,'raw_file_sha256':hashes,'batch_case_ids':[c['case_id'] for c in batch],'prior':prior})
            if retryable and n<3:time.sleep(2**(n-1));continue
            if statuses is None:statuses=[{'case_id':c['case_id'],'status':'held','decision':None,'raw_decision':None,'reason':category or 'invalid_semantic'} for c in batch]
            return {'lane':lane,'job_key':key,'attempt':str(attempt),'statuses':statuses}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for pos in range(0,len(cases),batch_size):
            batch=cases[pos:pos+batch_size];fa=pool.submit(execute,'A',batch);fb=pool.submit(execute,'B',batch);a=fa.result();b=fb.result()
            dis=[i for i,(x,y) in enumerate(zip(a['statuses'],b['statuses'])) if x['decision'] is not None and y['decision'] is not None and x['decision']['decision']!=y['decision']['decision']]
            c=execute('C',[batch[i] for i in dis],[{'a':a['statuses'][i]['decision'],'b':b['statuses'][i]['decision']} for i in dis]) if dis else None
            third=dict(zip(dis,c['statuses'])) if c else {}
            for i,case in enumerate(batch):
                x=a['statuses'][i];y=b['statuses'][i];selected=third.get(i,x);reasons=[z['reason'] for z in [x,y,selected] if z['status']=='held']
                decision=None if reasons else selected['decision']
                result={'case_id':case['case_id'],'status':'held' if reasons or decision and decision['decision']=='uncertain' else 'completed','decision':decision,'held_reasons':reasons,'lane_a':x,'lane_b':y,'lane_c':third.get(i),'attempt_a':a['attempt'],'attempt_b':b['attempt'],'attempt_c':c['attempt'] if i in third else None,'actor':'AI','human_gold':False,'authority_merge_approved':False,'origin_run':str(output)}
                write_once(output/'cases'/(case['case_id']+'.json'),result);finals.append(result)
            print(canonical({'recovery_processed':len(finals),'selected':len(cases),'cli_attempts':len(attempts)}),flush=True)
    assert_snapshot(pre)
    write_once(output/'postrun-source-code-snapshot.json',{'observed_at':now(),'sha256':snapshot(pre),'actor':'AI'})
    write_once(output/'results.jsonl',b''.join((canonical(f)+'\n').encode() for f in finals))
    usage=Counter()
    for a in attempts:usage.update(a['observation']['events']['usage'])
    write_once(output/'terminal-manifest.json',{'status':'processed_with_holds' if any(f['status']=='held' for f in finals) else 'completed','processed':len(finals),'cli_attempts':len(attempts),'retry_attempts':sum(a['observation']['attempt']>1 for a in attempts),'held':sum(f['status']=='held' for f in finals),'adjudicated':sum(f['lane_c'] is not None for f in finals),'usage':dict(usage),'elapsed_seconds':time.monotonic()-started,'finished_at':now(),'actor':'AI','human_gold':0,'authority_accepted':0,'pre_post_snapshot_identical':True})
    return output
