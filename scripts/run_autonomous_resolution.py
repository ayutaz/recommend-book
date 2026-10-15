"""Freeze evidence and run two independent bounded Sol lanes plus disagreements."""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import threading

from recommend_book.autonomous_resolution import blind_side,canonical,digest,validate_decision,assemble_final,output_schema,prompt,ndl_raw_fields
from recommend_book.normalization import normalize_text
from recommend_book.classification.runner import resolve_executable,cli_version,parse_events

ROOT=Path(__file__).resolve().parents[1]


def write(path,value):path.write_text(canonical(value)+'\n',encoding='utf8')


def prepare(output,pairs,database):
    if output.exists():raise ValueError('Existing namespace refused')
    cases=[];raws={};source=Path(pairs)
    conn=sqlite3.connect('file:'+Path(database).resolve().as_posix()+'?mode=ro&immutable=1',uri=True)
    conn.execute('pragma query_only=on')
    records={v:json.loads(r) for v,r in conn.execute('select version_id,record_json from source_records')}
    for line in source.read_text(encoding='utf8').splitlines():
        original=json.loads(line);sides=[]
        for side in original['sides']:
            version=side['version_id'];record=side['record']
            if records.get(version)!=record:raise ValueError('Old pair source snapshot differs from latest retained immutable record')
            sides.append(blind_side(side))
            raw=Path(side['provenance']['raw_path']);expected=side['provenance']['raw_sha256']
            if digest(raw.read_bytes())!=expected:raise ValueError('Original raw hash changed')
            raws[str(raw.resolve())]=expected
        cases.append({'case_id':original['case_id'],'case_kind':'hard_pair','sides':sides})
    unresolved=[]
    for wid,version in conn.execute('select work_id,source_version_id from works where resolved=0 order by work_id'):
        r=records[version];raw=Path(r['raw_path'])
        if digest(raw.read_bytes())!=r['raw_sha256']:raise ValueError('Unresolved raw changed')
        raws[str(raw.resolve())]=r['raw_sha256']
        side=blind_side({'version_id':version,'record':r});raw_fields=ndl_raw_fields(raw.read_bytes(),r['source_id']) if r['source']=='ndl' else []
        for i,field in enumerate(raw_fields):side['evidence'].append({'evidence_id':version+':raw:'+str(i),'field':'raw_bibliographic','text':canonical(field)})
        alternatives={normalize_text(f['text']) for f in raw_fields if f['tag'].endswith('}alternative')}
        candidates=[(v,other) for v,other in records.items() if v!=version and other['source']!=r['source'] and alternatives & {normalize_text(t['value']) for t in other.get('titles',[])}]
        # Exact original-title matches propose candidates; they never prove identity.
        candidates.sort(key=lambda x:x[0]);candidate=candidates[0] if len(candidates)==1 else None
        sides=[side]
        if candidate:
            v,other=candidate;other_raw=Path(other['raw_path'])
            if digest(other_raw.read_bytes())!=other['raw_sha256']:raise ValueError('Candidate raw changed')
            raws[str(other_raw.resolve())]=other['raw_sha256'];sides.append(blind_side({'version_id':v,'record':other}))
        item={'case_id':'unresolved_'+wid,'case_kind':'unresolved_record','sides':sides}
        cases.append(item);unresolved.append({'case_id':item['case_id'],'source_version_id':version,'raw_path':str(raw),'raw_sha256':r['raw_sha256'],
            'status':'held','actor':'mechanical_audit','raw_bibliographic_fields':raw_fields,'exact_original_title_candidate_count':len(candidates),
            'candidate_version_id':candidate[0] if candidate else None,
            'reason':'Original title match is a candidate only; no explicit cross-provider authority identity established. Require author, translation/volume/contents evidence; ambiguous or absent candidates held.', 'human_gold':False})
    conn.close();output.mkdir(parents=True);(output/'workspace').mkdir();(output/'attempts').mkdir();(output/'lanes').mkdir()
    (output/'inputs.jsonl').write_text(''.join(canonical(c)+'\n' for c in cases),encoding='utf8')
    write(output/'unresolved-source-ledger.json',unresolved);write(output/'schema.json',output_schema())
    write(output/'manifest.json',{'version':'autonomous-resolution-v1','status':'prepared','actor':'AI','human_gold':False,'ai_agreement_is_authority':False,
        'case_count':len(cases),'pair_count':len(cases)-len(unresolved),'unresolved_count':len(unresolved),'selection_bias':'Existing hard-pair selection retained; proposals/scores/strata/triggers removed from blind input',
        'source_sha256':{str(source.resolve()):digest(source.read_bytes()),str(Path(database).resolve()):digest(Path(database).read_bytes()),**raws},
        'input_sha256':digest((output/'inputs.jsonl').read_bytes()),'schema_sha256':digest((output/'schema.json').read_bytes()),
        'execution_artifact_sha256':{str(p.resolve()):digest(p.read_bytes()) for p in [Path(__file__),ROOT/'src/recommend_book/autonomous_resolution.py',ROOT/'config/autonomous-evaluation-v1.json']},
        'database_mutated':False,'authority_decisions_accepted':0})
    return cases


def run(output,cases,*,limit=None,batch_size=5,timeout=900):
    # Historical recipes remain readable; fresh model calls require the usage-guarded pipeline.
    raise RuntimeError('Archived runner: no fresh calls; guarded path only')
    output=output.resolve();command=resolve_executable();version=cli_version(command);model='gpt-6.1-sol'
    manifest=json.loads((output/'manifest.json').read_bytes())
    if digest((output/'inputs.jsonl').read_bytes())!=manifest['input_sha256'] or digest((output/'schema.json').read_bytes())!=manifest['schema_sha256']:raise ValueError('Frozen input or schema changed')
    if any(digest(Path(p).read_bytes())!=h for p,h in manifest.get('execution_artifact_sha256',{}).items()):raise ValueError('Prepared execution artifacts changed')
    manifest.update(status='running',model=model,cli_version=version,max_concurrency=2,batch_size=batch_size,started_at=datetime.now(timezone.utc).isoformat())
    write(output/'manifest.json',manifest)
    chosen=cases if limit is None else cases[:limit]
    usage=Counter();calls=0;lock=threading.Lock();started=time.monotonic()
    def execute(lane,batch,prior=None):
        nonlocal calls
        key=digest(canonical({'lane':lane,'cases':batch,'prior':prior,'cli_version':version,'model':model}).encode());cached=output/'lanes'/f'{lane}-{key}.json'
        if cached.exists():
            result=json.loads(cached.read_bytes());[validate_decision(r,c) for r,c in zip(result,batch)]
            if len(result)!=len(batch):raise ValueError('Cached count differs')
            return result
        stem=output/'attempts'/f'{lane}-{key}';final=Path(str(stem)+'.final.json');text=prompt(batch,lane,prior)
        Path(str(stem)+'.prompt.txt').write_text(text,encoding='utf8')
        argv=[*command,'exec','--model',model,'--sandbox','read-only','--ephemeral','--ignore-user-config','--skip-git-repo-check','--json','--color','never',
            '-c','model_reasoning_effort="medium"','-c','web_search="disabled"','-c','features.multi_agent=false','--cd',str(output/'workspace'),'--output-schema',str(output/'schema.json'),'-o',str(final),'-']
        write(Path(str(stem)+'.command.json'),{'argv':argv,'stdin_sha256':digest(text.encode()),'actor':'AI'})
        completed=subprocess.run(argv,input=text,text=True,encoding='utf8',errors='replace',capture_output=True,timeout=timeout,shell=False)
        Path(str(stem)+'.stdout.jsonl').write_text(completed.stdout,encoding='utf8');Path(str(stem)+'.stderr.txt').write_text(completed.stderr,encoding='utf8')
        events=parse_events(completed.stdout)
        with lock:calls+=1;usage.update(events['usage'])
        write(Path(str(stem)+'.observation.json'),{'exit_code':completed.returncode,'events':events,'actor':'AI','process_exit_observed':True})
        if completed.returncode or events['tool_calls'] or events['event_errors']:raise ValueError('CLI failure, event error or tool use refused')
        data=json.loads(final.read_bytes())['decisions']
        if len(data)!=len(batch):raise ValueError('Result count differs')
        for r,c in zip(data,batch):validate_decision(r,c)
        write(cached,data);return data
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for pos in range(0,len(chosen),batch_size):
                batch=chosen[pos:pos+batch_size];fa=pool.submit(execute,'A',batch);fb=pool.submit(execute,'B',batch)
                a=fa.result();b=fb.result();dis=[i for i,(x,y) in enumerate(zip(a,b)) if x['decision']!=y['decision']]
                thirds=execute('C',[batch[i] for i in dis],[{'a':a[i],'b':b[i]} for i in dis]) if dis else []
                byindex=dict(zip(dis,thirds));finals=[assemble_final(c,x,y,byindex.get(i)) for i,(c,x,y) in enumerate(zip(batch,a,b))]
                for f in finals:write(output/'lanes'/('final-'+f['case_id']+'.json'),f)
                print(canonical({'completed_batch':pos+len(batch),'selected':len(chosen),'calls_this_execution':calls}),flush=True)
        final=[json.loads((output/'lanes'/('final-'+c['case_id']+'.json')).read_bytes()) for c in chosen]
        (output/'results.jsonl').write_text(''.join(canonical(f)+'\n' for f in final),encoding='utf8')
        manifest.update(status='completed' if len(chosen)==len(cases) else 'pilot_completed',completed=len(final),decision_counts=dict(Counter(f['decision'] for f in final)),adjudicated=sum(f['adjudication_required'] for f in final),human_gold_count=0)
    except Exception as exc:
        manifest.update(status='failed',error_type=type(exc).__name__,error=str(exc));raise
    finally:
        if any(digest(Path(p).read_bytes())!=h for p,h in {**manifest['source_sha256'],**manifest.get('execution_artifact_sha256',{})}.items()):manifest.update(status='failed',error='Protected source/code changed')
        manifest.update(cli_calls_this_execution=calls,usage_this_execution=dict(usage),elapsed_seconds_this_execution=time.monotonic()-started,finished_at=datetime.now(timezone.utc).isoformat())
        write(output/'manifest.json',manifest)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--pairs',type=Path,default=ROOT/'data/poc-v1/resolution-review-20261012-v4/pairs.jsonl');p.add_argument('--database',type=Path,default=ROOT/'data/poc-openlibrary-enriched-v1/dataset.sqlite');p.add_argument('--prepare-only',action='store_true');p.add_argument('--limit',type=int);p.add_argument('--batch-size',type=int,default=5);args=p.parse_args()
    if not 1<=args.batch_size<=10 or args.limit is not None and args.limit<1:p.error('Invalid batch/limit')
    if not args.prepare_only:raise RuntimeError('Archived runner: no fresh calls; guarded path only')
    cases=[json.loads(x) for x in (args.output/'inputs.jsonl').read_text(encoding='utf8').splitlines()] if args.output.exists() else prepare(args.output,args.pairs,args.database)
    if not args.prepare_only:run(args.output,cases,limit=args.limit,batch_size=args.batch_size)


if __name__=='__main__':main()
