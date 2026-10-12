"""Post-run strict auditing; never rewrites live inputs, attempts, or decisions."""
from __future__ import annotations
import json
import time
from collections import Counter
from pathlib import Path
from hashlib import sha256
from recommend_book.autonomous_resolution import canonical,digest,prompt,validate_decision
from recommend_book.classification.runner import parse_events


class RetryableAttemptError(RuntimeError):
    def __init__(self,category,message):
        if category not in ('rate_limit','timeout','transient_cli'):raise ValueError('Permanent failures cannot be marked retryable')
        self.category=category
        super().__init__(message)


def bounded_retry(action,record,*,sleep=time.sleep,max_attempts=3):
    """Future subset only: callback must save unique raw attempts before returning.

    Authentication/quota/validation/tool-use failures are permanent. This helper
    does not retrofit retries or rewrite any completed v3 observations.
    """
    if type(max_attempts) is not int or not 1<=max_attempts<=3:raise ValueError('At most three attempts')
    for n in range(1,max_attempts+1):
        try:
            result=action(n)
            record({'attempt':n,'status':'completed','actor':'AI','retryable':False})
            return result
        except Exception as exc:
            retryable=isinstance(exc,RetryableAttemptError)
            record({'attempt':n,'status':'failed','actor':'AI','retryable':retryable,'error_type':type(exc).__name__,
                    'category':getattr(exc,'category','permanent'),'error':str(exc),'next_backoff_seconds':2**(n-1) if retryable and n<max_attempts else None})
            if not retryable or n==max_attempts:raise
            sleep(2**(n-1))


def strict_json(raw):
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('Duplicate JSON key')
            out[k]=v
        return out
    def invalid(value):raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def validate_output_boundary(output,run,role_audit,protected_paths):
    destination=Path(output).resolve()
    roots={Path(run).resolve(),Path(role_audit).resolve().parent}
    roots.update(Path(p).resolve().parent for p in protected_paths)
    if destination.exists():raise ValueError('Existing audit namespace refused')
    if any(destination==root or destination.is_relative_to(root) for root in roots):
        raise ValueError('Audit output enters protected input/source/run tree')
    return destination


def verify_stdout_payload(stdout,payload):
    events=[strict_json(line) for line in stdout.splitlines() if line.strip()]
    if any(not isinstance(event,dict) for event in events):raise ValueError('Non-object CLI event')
    if sum(event.get('type')=='turn.completed' for event in events)!=1:
        raise ValueError('Exactly one terminal turn required')
    messages=[event['item']['text'] for event in events if event.get('type')=='item.completed'
              and isinstance(event.get('item'),dict) and event['item'].get('type')=='agent_message']
    if not messages or strict_json(messages[-1])!=payload:
        raise ValueError('Last completed agent payload differs from raw final')


def verify_terminal_summary(manifest,finals,attempts):
    counts=dict(Counter(f['original_decision'] for f in finals))
    adjudicated=sum(len(a['decisions']) for a in attempts if a['lane']=='C')
    usage=Counter()
    for a in attempts:usage.update(a['usage'])
    if manifest['decision_counts']!=counts or manifest['adjudicated']!=adjudicated:
        raise ValueError('Manifest decision/adjudication totals differ from bound raw results')
    if manifest['cli_calls_this_execution']!=len(attempts) or manifest['usage_this_execution']!=dict(usage):
        raise ValueError('Manifest CLI calls/usage differ from bound raw observations')


def shared_work_identity(case):
    sets=[]
    for side in case['sides']:
        identifiers=set()
        for e in side['evidence']:
            if e.get('field') in ('identifiers','work_refs'):
                for i in strict_json(e['text']):
                    if i.get('scope')=='work':identifiers.add((i['source'],i['value']))
        sets.append(identifiers)
    return len(sets)==2 and bool(sets[0]&sets[1])


def decision_issues(result,case):
    validate_decision(result,case);issues=[]
    if result['decision']=='same':
        if result['checks']['author']=='different':issues.append('same_with_different_author_check')
        # Edition identity differs legitimately for translations/reprints, but
        # an explicit shared original-work identity must anchor that interpretation.
        if result['checks']['edition']=='different' and not shared_work_identity(case):
            issues.append('edition_difference_without_explicit_shared_work_identity')
        if result['checks']['record_type']=='different' and not shared_work_identity(case):
            issues.append('record_type_difference_without_explicit_shared_work_identity')
    return issues


def verify_cached_attempt(directory,lane,batch,prior,model,version):
    directory=Path(directory).resolve();key=digest(canonical({'lane':lane,'cases':batch,'prior':prior,'cli_version':version,'model':model}).encode())
    stem=directory/'attempts'/(lane+'-'+key)
    paths={suffix:Path(str(stem)+suffix) for suffix in ['.prompt.txt','.command.json','.stdout.jsonl','.stderr.txt','.observation.json','.final.json']}
    paths['cache']=directory/'lanes'/(lane+'-'+key+'.json')
    if any(not p.is_file() or p.is_symlink() for p in paths.values()):raise ValueError('Cached decision lacks unique raw attempt')
    before={str(p):sha256(p.read_bytes()).hexdigest() for p in paths.values()}
    text=prompt(batch,lane,prior)
    # The saved prompt is a recipe file, normalized by Python text IO; it is
    # not a separate byte-level capture of external CLI stdin.
    if paths['.prompt.txt'].read_text(encoding='utf8')!=text:raise ValueError('Prompt recipe differs')
    command=strict_json(paths['.command.json'].read_bytes());argv=command['argv']
    if command.get('actor')!='AI' or command.get('stdin_sha256')!=digest(text.encode()):raise ValueError('Command/input identity differs')
    expected=['exec','--model',model,'--sandbox','read-only','--ephemeral','--ignore-user-config','--skip-git-repo-check','--json','--color','never','-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.multi_agent=false','--cd',str(directory/'workspace'),'--output-schema',str(directory/'schema.json'),'-o',str(paths['.final.json']),'-']
    if not isinstance(argv,list) or argv[-len(expected):]!=expected:raise ValueError('Actual recorded argv differs from isolation contract')
    observation=strict_json(paths['.observation.json'].read_bytes());stdout=paths['.stdout.jsonl'].read_text(encoding='utf8');events=parse_events(stdout)
    if observation.get('actor')!='AI' or observation.get('process_exit_observed') is not True or type(observation.get('exit_code')) is not int or observation['exit_code']!=0:raise ValueError('Successful observed CLI attempt required')
    if events!=observation.get('events') or events['tool_calls'] or events['event_errors']:raise ValueError('Event/proof discrepancy or tool use')
    payload=strict_json(paths['.final.json'].read_bytes());cached=strict_json(paths['cache'].read_bytes())
    verify_stdout_payload(stdout,payload)
    if set(payload)!={'decisions'} or payload['decisions']!=cached or len(cached)!=len(batch):raise ValueError('Cached result/raw final binding differs')
    for decision,case in zip(cached,batch):validate_decision(decision,case)
    if any(sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in before.items()):raise ValueError('Attempt changed during audit')
    return {'decisions':cached,'raw_file_sha256':before,'usage':events['usage'],'job_key':key,'lane':lane,'surviving_raw_attempt_count':1,'automatic_retries':0,
            'attempt_history_scope':'v3 uses content-key filenames; audit binds surviving raw attempt, cannot establish append-only historic retry provenance',
            'prompt_scope':'Verified recipe and passed-input string SHA; not external stdin byte capture'}


def audit_run(directory,role_audit=None):
    directory=Path(directory).resolve();m=strict_json((directory/'manifest.json').read_bytes())
    if m['status']!='completed':raise ValueError('Terminal full run required')
    protected={str(directory/'inputs.jsonl'):m['input_sha256'],str(directory/'schema.json'):m['schema_sha256'],**m['source_sha256'],**m['execution_artifact_sha256']}
    if role_audit:protected[str(Path(role_audit).resolve())]=sha256(Path(role_audit).read_bytes()).hexdigest()
    if any(sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in protected.items()):raise ValueError('Frozen input/schema/source/code SHA differs')
    inventory={str(p):sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}
    cases=[strict_json(x) for x in (directory/'inputs.jsonl').read_text(encoding='utf8').splitlines()]
    if len(cases)!=m['case_count'] or m['completed']!=len(cases) or len({x['case_id'] for x in cases})!=len(cases):raise ValueError('Full count/order/identity contract differs')
    role_versions=set()
    if role_audit:
        role_versions={r['source_version_id'] for r in strict_json(Path(role_audit).read_bytes())['records'] if r['role_ambiguity']}
    finals=[];attempts=[]
    for pos in range(0,len(cases),m['batch_size']):
        batch=cases[pos:pos+m['batch_size']];a=verify_cached_attempt(directory,'A',batch,None,m['model'],m['cli_version']);b=verify_cached_attempt(directory,'B',batch,None,m['model'],m['cli_version']);attempts.extend([a,b])
        dis=[i for i,(x,y) in enumerate(zip(a['decisions'],b['decisions'])) if x['decision']!=y['decision']]
        third=verify_cached_attempt(directory,'C',[batch[i] for i in dis],[{'a':a['decisions'][i],'b':b['decisions'][i]} for i in dis],m['model'],m['cli_version']) if dis else None
        if third:attempts.append(third)
        c=dict(zip(dis,third['decisions'])) if third else {}
        for i,case in enumerate(batch):
            stored=strict_json((directory/'lanes'/('final-'+case['case_id']+'.json')).read_bytes())
            expected=c[i] if i in c else a['decisions'][i]
            if stored['final']!=expected or stored['lane_a']!=a['decisions'][i] or stored['lane_b']!=b['decisions'][i] or stored['lane_c']!=c.get(i) or stored['actor']!='AI' or stored['human_gold'] is not False or stored['authority_merge_approved'] is not False:raise ValueError('Final/independent lane provenance differs')
            issues=decision_issues(expected,case)
            if case['case_kind']=='hard_pair' and any(s['version_id'] in role_versions for s in case['sides']):issues.append('derived_author_role_unverified_against_original_provider')
            finals.append({'case_id':case['case_id'],'original_decision':expected['decision'],'audited_decision':'uncertain' if issues else expected['decision'],'held':bool(issues) or expected['decision']=='uncertain','issues':issues,'actor':'AI','human_gold':False,'authority_merge_approved':False})
    indexed=[strict_json(x) for x in (directory/'results.jsonl').read_text(encoding='utf8').splitlines()]
    if len(indexed)!=len(cases) or [r['case_id'] for r in indexed]!=[r['case_id'] for r in cases]:raise ValueError('Ordered results index incomplete')
    for r in indexed:
        if r!=strict_json((directory/'lanes'/('final-'+r['case_id']+'.json')).read_bytes()):raise ValueError('Results index/raw final differs')
    verify_terminal_summary(m,finals,attempts)
    if inventory!={str(p):sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()} or any(sha256(Path(p).read_bytes()).hexdigest()!=h for p,h in protected.items()):raise ValueError('Sources/run files changed during terminal audit')
    return {'version':'autonomous-resolution-strict-terminal-audit-v1','actor':'AI','human_gold_count':0,'authority_decisions_accepted':0,'case_count':len(cases),'attempt_count':len(attempts),'retry_policy_observed':'Original v3 single attempt per lane/batch, fail-stop; no automatic retries implemented','raw_attempt_binding_verified':True,'frozen_input_schema_code_source_end_sha_verified':True,'missing_cases':0,'duplicate_cases':0,'run_files_unchanged':True,'run_file_sha256':inventory,'protected_sha256':protected,'cases':finals,'attempts':attempts,'retry_subset_case_ids':[f['case_id'] for f in finals if f['issues']],'quality_limit':'Exact quotes establish provenance membership, not semantic support. Same-model agreement is not human accuracy.'}
