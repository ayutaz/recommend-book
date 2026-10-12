"""Read-only independent audit of append-only recovery and full coverage."""
from collections import Counter
from pathlib import Path
from recommend_book.autonomous_resolution import canonical,digest,prompt
from recommend_book.autonomous_resolution_audit import strict_json,verify_stdout_payload,validate_output_boundary
from recommend_book.autonomous_resolution_recovery import audit_failed_partial,assert_snapshot,snapshot,validate_batch,failure_category,_argv,write_once,now
from recommend_book.classification.runner import parse_events


def merge_proposals(cases,original,recovery):
    indexed={r['case_id']:r for r in original+recovery}
    if len(indexed)!=len(original)+len(recovery) or set(indexed)!={c['case_id'] for c in cases}:raise ValueError('Full original coverage has missing/overlapping proposals')
    return [indexed[c['case_id']] for c in cases]


def verify_recovery_summary(terminal,finals,attempts):
    usage=Counter()
    for a in attempts:usage.update(a['observation']['events']['usage'])
    expected={'processed':len(finals),'cli_attempts':len(attempts),'retry_attempts':sum(a['observation']['attempt']>1 for a in attempts),'held':sum(f['status']=='held' for f in finals),'adjudicated':sum(f['lane_c'] is not None for f in finals),'usage':dict(usage)}
    if any(terminal.get(k)!=v for k,v in expected.items()):raise ValueError('Recovery terminal counts/usage differ from bound cases and attempts')


def verify_append_only_lane(directory,lane,batch,prior,model,version,command):
    key=digest(canonical({'lane':lane,'cases':batch,'prior':prior,'cli_version':version,'model':model}).encode());text=prompt(batch,lane,prior);attempts=[];expected_files=set();last=None
    for n in range(1,4):
        path=directory/'attempts'/(lane+'-'+key+'-'+str(n))
        if not path.exists():
            if n==1:raise ValueError('Required independent lane attempt absent')
            break
        if n>1 and not attempts[-1]['observation']['retryable']:raise ValueError('Permanent failure retried')
        hashes=strict_json((path/'file-hashes.json').read_bytes());actual=snapshot(p for p in path.iterdir() if p.is_file() and p.name!='file-hashes.json')
        if hashes!=actual:raise ValueError('Append-only attempt raw hashes differ')
        minimum={'prompt.txt','command.json','stdout.jsonl','stderr.txt','observation.json'}
        if not minimum<={Path(p).name for p in hashes} or set(Path(p).name for p in hashes)-minimum-{'final.json'}:raise ValueError('Unexpected/missing attempt raw files')
        expected_files.update(hashes);expected_files.add(str(path/'file-hashes.json'))
        saved=strict_json((path/'command.json').read_bytes());obs=strict_json((path/'observation.json').read_bytes());stdout=(path/'stdout.jsonl').read_bytes().decode('utf8',errors='replace');stderr=(path/'stderr.txt').read_bytes().decode('utf8',errors='replace');events=parse_events(stdout)
        if saved!={'argv':_argv(command,directory,path/'final.json',model),'stdin_sha256':digest(text.encode()),'actor':'AI','attempt':n,'job_key':key,'lane':lane} or (path/'prompt.txt').read_bytes()!=text.encode():raise ValueError('Exact append-only command/passed string recipe differs')
        if obs['events']!=events or obs['actor']!='AI' or obs['attempt']!=n or obs['lane']!=lane or obs['job_key']!=key:raise ValueError('Raw observation/event identity differs')
        category=None;retryable=False;statuses=None
        if obs['timed_out']:
            if obs['exit_code'] is not None or obs['process_exit_observed'] is not False:raise ValueError('Timeout misreported as observed successful exit')
            category,retryable='timeout',True
        else:
            if obs['process_exit_observed'] is not True or type(obs['exit_code']) is not int:raise ValueError('Actual CLI exit observation missing')
            if events['tool_calls']:category,retryable='tool_use',False
            elif obs['exit_code'] or events['event_errors']:category,retryable=failure_category(stderr+' '+str(events['event_errors']))
            else:
                try:
                    payload=strict_json((path/'final.json').read_bytes());verify_stdout_payload(stdout,payload);statuses=validate_batch(payload,batch)
                    if any(s['status']=='held' for s in statuses):category,retryable='invalid_semantic',False
                except (ValueError,KeyError,TypeError,FileNotFoundError):category,retryable='invalid_semantic',False
        if (category,retryable)!=(obs['category'],obs['retryable']):raise ValueError('Failure classification or retry eligibility differs')
        attempts.append({'path':str(path),'observation':obs,'raw_file_sha256':hashes,'batch_case_ids':[c['case_id'] for c in batch]})
        if statuses is None:statuses=[{'case_id':c['case_id'],'status':'held','decision':None,'raw_decision':None,'reason':category or 'invalid_semantic'} for c in batch]
        last={'lane':lane,'job_key':key,'attempt':str(path),'statuses':statuses}
        if not retryable:break
        if n<3 and not (directory/'attempts'/(lane+'-'+key+'-'+str(n+1))).exists():raise ValueError('Retryable failed attempt stopped before declared bound')
    return last,attempts,expected_files


def aggregate(original,recovery,role_audit,terminal_observation,output):
    root=Path(__file__).resolve().parents[2]
    audit_code=snapshot([Path(__file__),root/'scripts/audit_autonomous_resolution_aggregate.py',root/'tests/test_autonomous_resolution_aggregate.py'])
    original=Path(original).resolve();recovery=Path(recovery).resolve();role_audit=Path(role_audit).resolve();output=Path(output).resolve()
    old=strict_json((original/'manifest.json').read_bytes());prepared=strict_json((recovery/'prepared-manifest.json').read_bytes());terminal=strict_json((recovery/'terminal-manifest.json').read_bytes())
    pre=strict_json((recovery/'prelaunch-source-code-snapshot.json').read_bytes())
    validate_output_boundary(output,original,role_audit,[recovery/'inputs.jsonl',recovery/'schema.json',*old['source_sha256'],*old['execution_artifact_sha256'],*pre['sha256']])
    if type(terminal_observation['exit_code']) is not int or terminal_observation['exit_code']!=0 or terminal_observation.get('actor')!='AI':raise ValueError('Actual observed recovery terminal exit0 required')
    inventory=snapshot(p for p in recovery.rglob('*') if p.is_file());post=strict_json((recovery/'postrun-source-code-snapshot.json').read_bytes())
    if pre['sha256']!=post['sha256']:raise ValueError('Actual before/after recovery snapshots differ')
    assert_snapshot(pre['sha256'])
    original_observation=strict_json((recovery/'original-terminal-observation.json').read_bytes())
    if type(original_observation['exit_code']) is not int or original_observation['exit_code']!=1 or original_observation.get('actor')!='AI':raise ValueError('Original failed native observation required')
    partial=audit_failed_partial(original,role_audit)
    if strict_json((recovery/'original-partial-audit.json').read_bytes())!=partial:raise ValueError('Saved original partial audit differs from independent current re-audit')
    allcases=[strict_json(x) for x in (original/'inputs.jsonl').read_bytes().splitlines()];original_ids={c['case_id'] for c in partial['cases']};cases=[strict_json(x) for x in (recovery/'inputs.jsonl').read_bytes().splitlines()]
    if cases!=[c for c in allcases if c['case_id'] not in original_ids] or prepared['case_count']!=len(cases) or prepared['source_case_count']!=len(allcases) or prepared['original_finalized']!=len(original_ids):raise ValueError('Exact pending original subset differs')
    if prepared['input_sha256']!=digest((recovery/'inputs.jsonl').read_bytes()) or prepared['schema_sha256']!=digest((recovery/'schema.json').read_bytes()):raise ValueError('Prepared input/schema hash fields differ')
    if prepared['model']!=old['model'] or prepared['cli_version']!=old['cli_version'] or prepared['max_concurrency']!=2 or prepared['max_attempts']!=3 or (recovery/'schema.json').read_bytes()!=(original/'schema.json').read_bytes():raise ValueError('Definition/schema/model/isolation contract differs')
    version_obs=strict_json((recovery/'cli-version-observation.json').read_bytes());command=version_obs['argv'][:-1]
    if version_obs['exit_code']!=0 or version_obs['argv'][-1]!='--version' or (recovery/'cli-version.stdout').read_bytes().decode('utf8').strip()!=old['cli_version']:raise ValueError('Raw actual CLI identity differs')
    expected_files={str(recovery/name) for name in ['original-partial-audit.json','original-terminal-observation.json','inputs.jsonl','schema.json','cli-version.stdout','cli-version.stderr','cli-version-observation.json','prepared-manifest.json','prelaunch-source-code-snapshot.json','postrun-source-code-snapshot.json','results.jsonl','terminal-manifest.json']}
    attempts=[];finals=[];proposals=[];role_versions={r['source_version_id'] for r in strict_json(role_audit.read_bytes())['records'] if r['role_ambiguity']}
    for pos in range(0,len(cases),prepared['batch_size']):
        batch=cases[pos:pos+prepared['batch_size']]
        a,aa,af=verify_append_only_lane(recovery,'A',batch,None,old['model'],old['cli_version'],command);b,ba,bf=verify_append_only_lane(recovery,'B',batch,None,old['model'],old['cli_version'],command);attempts.extend(aa+ba);expected_files.update(af|bf)
        dis=[i for i,(x,y) in enumerate(zip(a['statuses'],b['statuses'])) if x['decision'] is not None and y['decision'] is not None and x['decision']['decision']!=y['decision']['decision']]
        if dis:
            c,ca,cf=verify_append_only_lane(recovery,'C',[batch[i] for i in dis],[{'a':a['statuses'][i]['decision'],'b':b['statuses'][i]['decision']} for i in dis],old['model'],old['cli_version'],command);attempts.extend(ca);expected_files.update(cf);third=dict(zip(dis,c['statuses']))
        else:c=None;third={}
        for i,case in enumerate(batch):
            path=recovery/'cases'/(case['case_id']+'.json');saved=strict_json(path.read_bytes());expected_files.add(str(path));x=a['statuses'][i];y=b['statuses'][i];selected=third.get(i,x);reasons=[z['reason'] for z in [x,y,selected] if z['status']=='held'];decision=None if reasons else selected['decision']
            expected={'case_id':case['case_id'],'status':'held' if reasons or decision and decision['decision']=='uncertain' else 'completed','decision':decision,'held_reasons':reasons,'lane_a':x,'lane_b':y,'lane_c':third.get(i),'attempt_a':a['attempt'],'attempt_b':b['attempt'],'attempt_c':c['attempt'] if i in third else None,'actor':'AI','human_gold':False,'authority_merge_approved':False,'origin_run':str(recovery)}
            if saved!=expected:raise ValueError('Recovery case output differs from actual independent raw lanes')
            finals.append(saved);issues=list(dict.fromkeys(reasons))
            if case['case_kind']=='hard_pair' and any(s['version_id'] in role_versions for s in case['sides']):issues.append('derived_author_role_unverified_against_original_provider')
            proposals.append({'case_id':case['case_id'],'original_decision':decision['decision'] if decision else None,'audited_decision':None if decision is None else ('uncertain' if issues else decision['decision']),'status':'held' if issues or saved['status']=='held' else 'completed','issues':issues,'raw_final':saved,'origin_run':str(recovery),'origin_sha256':inventory[str(path)],'actor':'AI','human_gold':False,'authority_merge_approved':False})
    indexed=[strict_json(x) for x in (recovery/'results.jsonl').read_bytes().splitlines()]
    if indexed!=finals:raise ValueError('Recovery results index/order differs')
    verify_recovery_summary(terminal,finals,attempts)
    if terminal['status']!=('processed_with_holds' if terminal['held'] else 'completed') or terminal.get('pre_post_snapshot_identical') is not True:raise ValueError('Recovery terminal status differs')
    if expected_files!=set(inventory):raise ValueError('Recovery full expected file inventory differs')
    combined=merge_proposals(allcases,partial['cases'],proposals);assert_snapshot(inventory);assert_snapshot(pre['sha256']);assert_snapshot(audit_code)
    report={'version':'autonomous-resolution-all-cases-audit-v1','actor':'AI','case_count':len(combined),'pair_count':sum(c['case_kind']=='hard_pair' for c in allcases),'unresolved_count':sum(c['case_kind']=='unresolved_record' for c in allcases),'original_failed_exit_code':1,'original_finalized':len(partial['cases']),'recovery_processed':len(finals),'processed_case_count':len(combined),'missing_cases':0,'duplicate_cases':0,'audited_decision_counts':dict(Counter(p['audited_decision'] if p['audited_decision'] is not None else 'no_valid_ai_decision' for p in combined)),'audited_status_counts':dict(Counter(p['status'] for p in combined)),'strict_issue_counts':dict(Counter(issue for p in combined for issue in p['issues'])),'original_cli_calls':partial['original_cli_calls'],'original_usage':partial['original_usage'],'recovery_terminal':terminal,'recovery_terminal_observation':terminal_observation,'original_partial_audit_sha256':inventory[str(recovery/'original-partial-audit.json')],'prelaunch_snapshot_sha256':inventory[str(recovery/'prelaunch-source-code-snapshot.json')],'postrun_snapshot_sha256':inventory[str(recovery/'postrun-source-code-snapshot.json')],'recovery_file_sha256':inventory,'expected_full_file_inventory_verified':True,'raw_stdout_final_binding_verified':True,'exact_all_case_sql_raw_material_verified':True,'actual_recovery_pre_post_snapshots_verified':True,'human_gold_count':0,'accepted_authority_decisions':0,'cases':combined,'recovery_attempts':attempts,'quality_limit':'Same-model agreement and exact quotes do not establish semantic truth, human precision, or generalization. Invalid outputs are held without inventing an AI decision.'}
    report['audit_execution_code_sha256']=audit_code
    output.mkdir(parents=True);write_once(output/'audit.json',report);write_once(output/'audited-proposals.jsonl',b''.join((canonical(c)+'\n').encode() for c in combined));write_once(output/'accepted-authority-decisions.json',[])
    write_once(output/'terminal-proof.json',{'actor':'AI','created_at':now(),'original_failed_terminal':strict_json((recovery/'original-terminal-observation.json').read_bytes()),'recovery_observed_terminal':terminal_observation,'aggregate_audit_sha256':digest((output/'audit.json').read_bytes()),'case_count':len(combined),'all_expected_provenance_verified':True,'human_gold':0,'accepted_authority':0,'scope':'350 finalized proposals from failed v3 plus 76 independently processed recovery cases; not a successful v3 full run'})
    return {k:v for k,v in report.items() if k not in ['cases','recovery_attempts','recovery_file_sha256','original_usage']}
