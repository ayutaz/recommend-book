"""Candidate-only v3 audit; no classifier input or model is emitted/launched."""
from __future__ import annotations
import argparse,csv,json,sys,time,urllib.request,urllib.error
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from recommend_book.autonomous_reference import COMMIT,METADATA_SHA,sha256,strict_json,assess_metadata_rights
from recommend_book.next_reference import plan_cohort,hold_category,evaluation_contract,audit_native_source,context_proof,candidate_materials,VERSION

def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode())
def write_rows(path,rows):path.write_bytes(''.join(json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n' for v in rows).encode())
def fetch(url):
    if not url.startswith(f'https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/master/master/ne'):raise ValueError('unapproved source')
    for attempt in range(3):
        time.sleep(1.1)
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'recommend-book-v3-reference-audit/1.0 (https://github.com/ayutaz; literary source audit)'}),timeout=45) as r:
                if r.geturl()!=url:raise ValueError('unexpected redirect')
                data=r.read(15_000_001)
                if len(data)>15_000_000:raise ValueError('source exceeds reviewed size')
                return data
        except urllib.error.HTTPError as e:
            if e.code not in {429,500,502,503,504} or attempt==2:raise
            retry=e.headers.get('Retry-After')
            if retry:
                from email.utils import parsedate_to_datetime
                try:delay=float(retry)
                except ValueError:delay=(parsedate_to_datetime(retry)-datetime.now(timezone.utc)).total_seconds()
                if delay>60:raise RuntimeError('Retry-After exceeds bounded run; deferred without retry') from e
            else:delay=2**attempt
            time.sleep(max(1.1,delay))

def audit(output:Path,acquire:bool=False,*,source_cache:Path|None=None,exclude_cast_list:bool=False):
    output=output.resolve();base=(ROOT/'data/autonomous-next-v3/reference').resolve()
    if output==base or not output.is_relative_to(base) or output.exists():raise ValueError('new owned output required')
    if source_cache is not None:
        source_cache=source_cache.resolve()
        if not source_cache.is_relative_to(base) or not source_cache.is_dir():raise ValueError('owned read-only source cache required')
    old=ROOT/'data/autonomous-poc-v1/reference';mp=ROOT/'data/reference/conssa-metadata.tsv'
    inputs={'metadata':mp,'eligible269':ROOT/'data/reference/conssa-eligible-native-reference.jsonl','old180_ledger':old/'target100-v1/eligibility-ledger.jsonl','observed60_inputs':old/'target100-v1/work-inputs.jsonl','official_tree':old/'raw/official-tree.json'}
    before={k:sha256(p.read_bytes()) for k,p in inputs.items()}
    if before['metadata']!=METADATA_SHA or before['official_tree']!='496e9f4e3f9807bf78ad33032de41820842f9223a9bc3b7d87c1889a25d9e0c9':raise ValueError('fixed source differs')
    meta_rows=list(csv.DictReader(mp.open(encoding='utf-8'),delimiter='\t'));meta={r['idno']:r for r in meta_rows}
    if len(meta)!=len(meta_rows):raise ValueError('duplicate metadata')
    refs=[strict_json(x) for x in inputs['eligible269'].read_bytes().splitlines() if x];ids={r['reference_id'] for r in refs}
    previous=[strict_json(x) for x in inputs['old180_ledger'].read_bytes().splitlines() if x];prior={r['reference_id']:r for r in previous}
    observed={strict_json(x)['work_id'].rsplit(':',1)[1] for x in inputs['observed60_inputs'].read_bytes().splitlines() if x}
    if len(refs)!=269 or len(ids)!=269 or len(prior)!=180 or len(observed)!=60 or not set(prior)<=ids:raise ValueError('fixed cohort counts differ')
    groups={r['reference_id']:r for r in plan_cohort([meta[r['reference_id']] for r in refs],observed)}
    tree=strict_json(inputs['official_tree'].read_bytes());entries={r['path']:r for r in tree['tree']}
    source_paths=[ROOT/'src/recommend_book/next_reference.py',Path(__file__),ROOT/'tests/test_next_reference.py']
    source_before={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in source_paths}
    output.mkdir(parents=True);(output/'raw').mkdir()
    write(output/'preparation-context.json',{'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'input_sha256':before,'source_sha256':source_before,'acquire_missing_free_sources':acquire,'source_cache':str(source_cache) if source_cache else None,'exclude_cast_list':exclude_cast_list,'llm_execution_authorized':False})
    audits=[];acquisitions=[]
    for index,ref in enumerate(refs,1):
        ident=ref['reference_id'];md=meta[ident];row={**groups[ident],'work_id':'reference:conssa:'+ident,'source_native_certainty':ref['reference_certainty'],'prior180_member':ident in prior,'native_fields':ref['native_labels'],'classifier_input_written':False,'new_human_annotation_generated':False}
        if ident in prior:
            row['historical_status']=prior[ident]['status'];row['historical_reason']=prior[ident]['reason']
            if prior[ident]['status']=='hold':row['historical_hold_category']=hold_category(prior[ident]['reason'])
        try:
            assess_metadata_rights(md);row['metadata_term_envelope_passed']=True
            src=f'https://raw.githubusercontent.com/cligs/conssa/{COMMIT}/master/master/{ident}.xml'
            path=old/'raw'/f'{ident}.xml'
            if not path.exists():
                if source_cache is not None and (source_cache/f'{ident}.xml').is_file():path=source_cache/f'{ident}.xml'
            if not path.exists():
                path=output/'raw'/f'{ident}.xml'
                if not acquire:raise ValueError('raw source not acquired in this audit')
                raw=fetch(src);path.write_bytes(raw)
                acquisitions.append({'reference_id':ident,'source_url':src,'retrieved_at':datetime.now(timezone.utc).isoformat(),'raw_path':str(path.relative_to(ROOT)),'sha256':sha256(raw),'bytes':len(raw)})
                write_rows(output/'acquisition-progress.jsonl',acquisitions)
            raw=path.read_bytes();entry=entries[f'master/master/{ident}.xml']
            import hashlib
            blob=hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()
            if blob!=entry['sha'] or len(raw)!=entry['size']:raise ValueError('official blob identity differs')
            details=audit_native_source(raw,md,ref,source_url=src,exclude_cast_list=exclude_cast_list)
            row.update(status='body_scope_candidate',reason='original TEI/native/edition checks passed; new v3 use guard still pending',source_url=src,raw_path=str(path.relative_to(ROOT)),raw_sha256=sha256(raw),git_blob_sha1=blob,source_audit=details)
        except (ValueError,KeyError,RuntimeError,urllib.error.URLError) as e:
            row.update(status='held',reason=str(e),error_type=type(e).__name__)
        audits.append(row)
        if index%10==0:print(json.dumps({'audited':index,'body_scope_candidates':sum(r['status']=='body_scope_candidate' for r in audits),'held':sum(r['status']=='held' for r in audits)}),flush=True)
    after={k:sha256(p.read_bytes()) for k,p in inputs.items()};source_after={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in source_paths}
    if before!=after or source_before!=source_after:raise ValueError('source bytes changed during audit')
    write_rows(output/'cohort-audit.jsonl',audits);write(output/'evaluation-contract.json',evaluation_contract())
    ready=[r for r in audits if r['status']=='body_scope_candidate'];fresh=[r for r in ready if r['cohort_role']=='unobserved_author_disjoint_candidate']
    report={'version':VERSION,'created_at':datetime.now(timezone.utc).isoformat(),'annotation_candidates':269,'observed_dev_works':60,'observed_dev_author_groups':len({r['author_group'] for r in audits if r['cohort_role']=='observed_dev'}),'historical180_held_count':120,'historical_hold_categories':dict(Counter(r.get('historical_hold_category') for r in audits if r.get('historical_status')=='hold')),'status_counts':dict(Counter(r['status'] for r in audits)),'hold_reasons':dict(Counter(r['reason'] for r in audits if r['status']=='held')),'body_scope_candidate_count':len(ready),'author_disjoint_body_scope_candidates':len(fresh),'author_disjoint_candidate_groups':len({r['author_group'] for r in fresh}),'metadata_rights_only_upper_bound':sum(r.get('metadata_term_envelope_passed',False) for r in audits),'actual_external_llm_approved_inputs':0,'new_holdout_ready':False,'requested_100_body_candidates_reached':len(ready)>=100,'new_classifier_runs':0,'input_sha256_before':before,'input_sha256_after':after,'source_sha256_before':source_before,'source_sha256_after':source_after,'acquired_raw_files':len(acquisitions),'files':{name:sha256((output/name).read_bytes()) for name in ['cohort-audit.jsonl','evaluation-contract.json','preparation-context.json']}}
    write(output/'manifest.json',report);print(json.dumps({k:report[k] for k in ('body_scope_candidate_count','author_disjoint_body_scope_candidates','actual_external_llm_approved_inputs','new_holdout_ready')}));return report

def prepare_context_proofs(audit_path:Path,output:Path):
    base=(ROOT/'data/autonomous-next-v3/reference').resolve();audit_path=audit_path.resolve();output=output.resolve()
    if not audit_path.is_relative_to(base) or not output.is_relative_to(base) or output==base or output.exists():raise ValueError('new owned context output required')
    manifest_raw=(audit_path/'manifest.json').read_bytes();manifest=strict_json(manifest_raw)
    cohort_raw=(audit_path/'cohort-audit.jsonl').read_bytes()
    if sha256(cohort_raw)!=manifest['files']['cohort-audit.jsonl']:raise ValueError('candidate audit changed')
    sources=[ROOT/'src/recommend_book/next_reference.py',Path(__file__),ROOT/'tests/test_next_reference.py']
    before={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in sources}
    rows=[strict_json(x) for x in cohort_raw.splitlines() if x];proofs=[];raw_hashes={}
    if len({r['reference_id'] for r in rows})!=len(rows):raise ValueError('duplicate candidate identity')
    for row in rows:
        if row['status']!='body_scope_candidate':continue
        path=(ROOT/row['raw_path']).resolve()
        if not path.is_relative_to(ROOT/'data'):raise ValueError('raw source outside data')
        raw=path.read_bytes()
        if sha256(raw)!=row['raw_sha256']:raise ValueError('original source changed')
        raw_hashes[str(path.relative_to(ROOT))]=sha256(raw)
        for recipe in ('definitions-v3','context-v3'):
            proof=context_proof(raw,recipe)
            if proof['body_sha256']!=row['source_audit']['body_sha256']:raise ValueError('audited body differs')
            proofs.append({'reference_id':row['reference_id'],'expression_id':f"conssa:{row['reference_id']}:{COMMIT}",
                'source_url':row['source_url'],'raw_path':row['raw_path'],'author_group':row['author_group'],
                'cohort_role':row['cohort_role'],**proof})
    if len(proofs)!=manifest['body_scope_candidate_count']*2:raise ValueError('context coverage mismatch')
    after={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in sources}
    if before!=after or (audit_path/'manifest.json').read_bytes()!=manifest_raw or (audit_path/'cohort-audit.jsonl').read_bytes()!=cohort_raw:raise ValueError('preparation source changed')
    for path,expected in raw_hashes.items():
        if sha256((ROOT/path).read_bytes())!=expected:raise ValueError('raw source changed during preparation')
    output.mkdir();write_rows(output/'context-proofs.jsonl',proofs)
    report={'version':'conssa-context-proofs-v3-v1','created_at':datetime.now(timezone.utc).isoformat(),
        'candidate_works':len(proofs)//2,'proof_count':len(proofs),'classifier_inputs_written':0,'external_llm_approved_inputs':0,
        'audit_manifest_sha256':sha256(manifest_raw),'cohort_sha256':sha256(cohort_raw),'raw_source_sha256':raw_hashes,
        'source_sha256_before':before,'source_sha256_after':after,'files':{'context-proofs.jsonl':sha256((output/'context-proofs.jsonl').read_bytes())}}
    write(output/'manifest.json',report);print(json.dumps({k:report[k] for k in ('candidate_works','proof_count','classifier_inputs_written','external_llm_approved_inputs')}));return report


def prepare_candidate_inputs(audit_path:Path,context_path:Path,output:Path):
    base=(ROOT/'data/autonomous-next-v3/reference').resolve();audit_path=audit_path.resolve();context_path=context_path.resolve();output=output.resolve()
    if any(not p.is_relative_to(base) for p in (audit_path,context_path,output)) or output==base or output.exists():raise ValueError('new owned candidate output required')
    paths={'audit_manifest':audit_path/'manifest.json','source_audit':audit_path/'cohort-audit.jsonl',
        'context_manifest':context_path/'manifest.json','context_proofs':context_path/'context-proofs.jsonl',
        'metadata':ROOT/'data/reference/conssa-metadata.tsv'}
    before={key:sha256(path.read_bytes()) for key,path in paths.items()}
    am=strict_json(paths['audit_manifest'].read_bytes());cm=strict_json(paths['context_manifest'].read_bytes())
    if before['metadata']!=METADATA_SHA or before['source_audit']!=am['files']['cohort-audit.jsonl'] or before['context_proofs']!=cm['files']['context-proofs.jsonl'] or before['audit_manifest']!=cm['audit_manifest_sha256']:raise ValueError('source/context bind differs')
    rows=[strict_json(x) for x in paths['source_audit'].read_bytes().splitlines() if x]
    if len({r['reference_id'] for r in rows})!=len(rows):raise ValueError('duplicate candidate identity')
    contexts=[strict_json(x) for x in paths['context_proofs'].read_bytes().splitlines() if x]
    lookup={(r['reference_id'],r['recipe']):r for r in contexts}
    if len(lookup)!=len(contexts):raise ValueError('duplicate context proof')
    descriptors={key:{'path':str(paths[key].relative_to(ROOT)),'sha256':before[key]} for key in ('metadata','source_audit','context_proofs')}
    sources=[ROOT/'src/recommend_book/next_reference.py',Path(__file__),ROOT/'tests/test_next_reference.py']
    source_before={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in sources}
    works={'definitions-v3':[],'context-v3':[]};records=[];proofs={};raw_hashes={};now=datetime.now(timezone.utc).isoformat()
    for row in rows:
        if row['status']!='body_scope_candidate':continue
        path=(ROOT/row['raw_path']).resolve()
        if not path.is_relative_to(ROOT/'data'):raise ValueError('raw outside data')
        raw=path.read_bytes();raw_hashes[str(path.relative_to(ROOT))]=sha256(raw)
        if sha256(raw)!=row['raw_sha256']:raise ValueError('raw candidate changed')
        for recipe in works:
            expected=context_proof(raw,recipe);bound=lookup[(row['reference_id'],recipe)]
            if any(bound.get(k)!=v for k,v in expected.items()):raise ValueError('candidate context differs')
            record,work,entries=candidate_materials(raw,row,recipe,descriptors=descriptors,retrieved_at=now)
            records.append(record.model_dump(mode='json'));works[recipe].append(work.model_dump(mode='json'))
            if set(entries)&set(proofs):raise ValueError('duplicate evidence identity')
            proofs.update(entries)
    if any(len(v)!=am['body_scope_candidate_count'] for v in works.values()):raise ValueError('candidate input coverage differs')
    if any(sha256(p.read_bytes())!=before[k] for k,p in paths.items()):raise ValueError('input changed during preparation')
    source_after={str(p.relative_to(ROOT)):sha256(p.read_bytes()) for p in sources}
    if source_before!=source_after:raise ValueError('source changed during preparation')
    for path,expected in raw_hashes.items():
        if sha256((ROOT/path).read_bytes())!=expected:raise ValueError('raw changed during preparation')
    output.mkdir();write_rows(output/'source-records.jsonl',records);write(output/'corpus-proofs.json',proofs)
    for recipe,values in works.items():write_rows(output/f'{recipe}-work-inputs.jsonl',values)
    names=['source-records.jsonl','corpus-proofs.json',*[f'{r}-work-inputs.jsonl' for r in works]]
    report={'version':'conssa-unapproved-input-proposals-v3-v1','created_at':now,'candidate_works':am['body_scope_candidate_count'],
        'input_counts':{r:len(v) for r,v in works.items()},'source_records':len(records),'evidence_proofs':len(proofs),
        'external_llm_approved_inputs':0,'llm_input_allowed_flags':False,'new_classifier_runs':0,
        'input_sha256_before':before,'input_sha256_after':before,'source_sha256_before':source_before,'source_sha256_after':source_after,
        'raw_source_sha256':raw_hashes,'files':{name:sha256((output/name).read_bytes()) for name in names}}
    write(output/'manifest.json',report);print(json.dumps({k:report[k] for k in ('candidate_works','input_counts','source_records','external_llm_approved_inputs')}));return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--acquire-new',action='store_true');p.add_argument('--source-cache',type=Path);p.add_argument('--exclude-cast-list',action='store_true');p.add_argument('--context-proof-from',type=Path);p.add_argument('--candidate-inputs-from',type=Path);p.add_argument('--context-proofs',type=Path);a=p.parse_args()
    if a.candidate_inputs_from:
        if not a.context_proofs or a.context_proof_from or a.acquire_new or a.source_cache or a.exclude_cast_list:p.error('candidate proposals require offline source audit and context proofs only')
        prepare_candidate_inputs(a.candidate_inputs_from,a.context_proofs,a.output)
    elif a.context_proof_from:
        if a.acquire_new or a.source_cache or a.exclude_cast_list:p.error('context proofs are offline and use the already audited recipe')
        prepare_context_proofs(a.context_proof_from,a.output)
    else:audit(a.output,a.acquire_new,source_cache=a.source_cache,exclude_cast_list=a.exclude_cast_list)
