"""Prepare immutable changed-only classification inputs; never start a model run."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlparse
import uuid

from recommend_book.classification.prompt import build_prompt
from recommend_book.classification.runner import build_run_config, validate_controls
from recommend_book.classification.validation import ROOT, input_hash
from recommend_book.jobs import canonical_json
from recommend_book.models import Evidence, WorkInput

ARTIFACTS = (
    'config/sources.json', 'config/taxonomy.json', 'schemas/classification.schema.json',
    'src/recommend_book/classification/prompt.py', 'src/recommend_book/classification/validation.py',
    'src/recommend_book/classification/runner.py', 'src/recommend_book/models.py',
    'src/recommend_book/jobs.py', 'pyproject.toml', 'uv.lock',
)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON property: '+key)
            result[key]=value
        return result
    def invalid(value):raise ValueError('Nonfinite JSON number: '+value)
    def floating(value):
        import math
        number=float(value)
        return number if math.isfinite(number) else invalid(value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid,parse_float=floating)


def read_json(path):return strict_json(Path(path).read_bytes())


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')


def contained(root,relative):
    if not isinstance(relative,str) or Path(relative).is_absolute():raise ValueError('Source artifact must have a relative path')
    path=(root/relative).resolve(strict=True)
    if not path.is_relative_to(root):raise ValueError('Source artifact escapes its input tree')
    if not path.is_file():raise ValueError('Source artifact is not a file')
    return path


def inventory(trees):
    result={}
    for tree in trees:
        for path in tree.rglob('*'):
            if not path.is_file():continue
            resolved=path.resolve()
            if not resolved.is_relative_to(tree):raise ValueError('Source tree has an escaping symlink')
            result[str(resolved)]=sha(resolved.read_bytes())
    return result


def rows(raw,expected_count):
    lines=raw.splitlines(keepends=True)
    if len(lines)!=expected_count or any(not line.strip() or not line.endswith(b'\n') for line in lines):
        raise ValueError('Input count or newline termination differs')
    works=[WorkInput.model_validate(strict_json(line)) for line in lines]
    if len({w.work_id for w in works})!=len(works):raise ValueError('Duplicate canonical work ID')
    if any(w.expression_id is not None for w in works):raise ValueError('Work-level inputs must not change expression identity')
    if any(w.work_id.startswith('ws:') for w in works):raise ValueError('Legacy ws IDs cannot be rebound')
    if any(len({e.evidence_id for e in w.evidence})!=len(w.evidence) for w in works):raise ValueError('Duplicate evidence IDs')
    return works,dict(zip((w.work_id for w in works),lines))


def verify_crosswalk(crosswalk,old,new):
    if not isinstance(crosswalk,list):raise ValueError('Canonical crosswalk must be a list')
    result={}
    for row in crosswalk:
        old_id=row.get('old_canonical_id')
        targets=row.get('new_canonical_ids')
        nodes=row.get('old_work_node_ids')
        if old_id not in old or old_id in result or not isinstance(targets,list) or len(targets)!=1:
            raise ValueError('Crosswalk is incomplete, duplicate, unknown or split')
        if targets[0] not in new or not isinstance(nodes,list) or old_id not in nodes or len(set(nodes))!=len(nodes):
            raise ValueError('Crosswalk lacks target or old immutable node identity')
        result[old_id]=targets[0]
    if set(result)!=set(old) or set(result.values())!=set(new) or len(set(result.values()))!=len(result):
        raise ValueError('Canonical crosswalk is not a complete one-to-one mapping')
    return result


def coverage(works):
    evidence=[e for w in works for e in w.evidence]
    allowed=[e for e in evidence if e.llm_input_allowed]
    excluded=[e for e in evidence if not e.llm_input_allowed]
    return {'work_count':len(works),'evidence_count':len(evidence),'allowed_count':len(allowed),
            'excluded_count':len(excluded),'works_with_allowed_evidence':sum(any(e.llm_input_allowed for e in w.evidence) for w in works),
            'source_record_refs':sum(len(w.source_record_ids) for w in works),
            'allowed_by_kind':dict(sorted(Counter(e.kind for e in allowed).items())),
            'excluded_by_kind':dict(sorted(Counter(e.kind for e in excluded).items())),
            'allowed_by_source_host':dict(sorted(Counter(urlparse(e.source_url).netloc for e in allowed).items())),
            'excluded_by_source_host':dict(sorted(Counter(urlparse(e.source_url).netloc for e in excluded).items())),
            'allowed_by_policy':dict(sorted(Counter(e.rights_policy_id for e in allowed).items())),
            'excluded_by_policy':dict(sorted(Counter(e.rights_policy_id for e in excluded).items())),
            'original_language_counts':dict(sorted(Counter(w.original_language or 'unknown' for w in works).items()))}


def prompt_copy(work,*,model,cli_version,taxonomy):
    text=build_prompt(work,model=model,cli_version=cli_version,taxonomy=taxonomy)
    payload=strict_json(text.split('\nBEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0])
    expected=[e.model_dump(mode='json') for e in work.evidence if e.llm_input_allowed]
    if canonical_json(payload['work']['evidence'])!=canonical_json(expected):raise ValueError('Prompt did not exclude denied evidence exactly')
    if payload['output_identity']['input_hash']!=input_hash(work):raise ValueError('Prompt input hash differs')
    return text.encode('utf8')


def audit_enrichment_sources(prepared_dir,output_path):
    """Additional read-only source audit, bound to the already immutable delta manifest.

    The separate new output never rewrites the prepared namespace or rereads raw URLs.
    """
    prepared=Path(prepared_dir).resolve(strict=True);output=Path(output_path).resolve()
    report_source=read_json(prepared/'manifest.json');enriched=Path(report_source['enriched_dir']).resolve(strict=True)
    if output.exists() or any(output.is_relative_to(tree) for tree in (prepared,enriched)):
        raise ValueError('Source audit needs a new output outside source/snapshot trees')
    proofs=inventory([prepared,enriched])
    manifest=read_json(enriched/'manifest.json')
    if sha((enriched/'manifest.json').read_bytes())!=report_source['enriched_manifest_sha256']:
        raise ValueError('Enrichment manifest changed since delta preparation')
    path=contained(enriched,'accepted-records.jsonl');raw=path.read_bytes()
    if sha(raw)!=manifest['output_sha256'].get('accepted-records.jsonl'):
        raise ValueError('Accepted source records SHA differs')
    records=[strict_json(line) for line in raw.splitlines() if line.strip()]
    evidence=[Evidence.model_validate(e) for r in records for e in r.get('evidence',[])]
    denied=[e for e in evidence if not e.llm_input_allowed]
    def signature(e):
        return canonical_json({k:v for k,v in e.model_dump(mode='json').items() if k not in ('evidence_id','llm_input_allowed')})
    forbidden={signature(e) for e in denied}
    new_raw=Path(report_source['new_all_inputs_path']).read_bytes()
    if sha(new_raw)!=report_source['new_all_inputs_sha256']:raise ValueError('All enriched inputs changed')
    works,_=rows(new_raw,report_source['total_work_count'])
    input_matches=sum(e.llm_input_allowed and signature(e) in forbidden for w in works for e in w.evidence)
    prompt_matches=0
    for name,checksum in report_source['output_sha256'].items():
        if not name.startswith('prompts/'):continue
        prompt_raw=contained(prepared,name).read_bytes()
        if sha(prompt_raw)!=checksum:raise ValueError('Prepared prompt SHA differs')
        text=prompt_raw.decode('utf8')
        payload=strict_json(text.split('\nBEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0])
        prompt_evidence=[Evidence.model_validate(e) for e in payload['work']['evidence']]
        if any(not e.llm_input_allowed for e in prompt_evidence):raise ValueError('Denied input evidence entered a prompt')
        prompt_matches+=sum(signature(e) in forbidden for e in prompt_evidence)
    if input_matches or prompt_matches:raise ValueError('Denied source prose was rebound to allowed input/prompt evidence')
    result={'version':'enrichment-source-evidence-audit-v1','prepared_manifest_path':str(prepared/'manifest.json'),
            'prepared_manifest_sha256':sha((prepared/'manifest.json').read_bytes()),
            'delta_inputs_sha256':report_source['ordered_inputs_sha256'],'new_all_inputs_sha256':report_source['new_all_inputs_sha256'],
            'accepted_records_path':str(path),'accepted_records_sha256':sha(raw),'accepted_record_count':len(records),
            'accepted_by_kind':dict(sorted(Counter(r['record_kind'] for r in records).items())),
            'source_evidence':{'total_count':len(evidence),'allowed_count':sum(e.llm_input_allowed for e in evidence),
                               'denied_count':len(denied),'denied_by_kind':dict(sorted(Counter(e.kind for e in denied).items())),
                               'denied_by_policy':dict(sorted(Counter(e.rights_policy_id for e in denied).items()))},
            'input_denied_count':sum(not e.llm_input_allowed for w in works for e in w.evidence),
            'denied_evidence_rebound_to_input':input_matches,'denied_evidence_present_in_prompt':prompt_matches,
            'source_versions_before':manifest.get('stats_before',{}).get('source_records'),
            'source_versions_after':manifest.get('stats_after',{}).get('source_records'),
            'producer_raw_sha_verified_files':manifest.get('raw_sha_verified_files'),
            'raw_proof_scope':'Existing producer manifest/proof binding; no raw content retrieval repeated',
            'source_files_unchanged':True,'llm_started':False,'web_requests':0,'raw_retrieval_performed':False}
    if inventory([prepared,enriched])!=proofs:raise ValueError('Source changed during source evidence audit')
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.prepare-'+uuid.uuid4().hex)
    try:
        write_json(temporary,result)
        if output.exists():raise ValueError('Source audit output collision')
        temporary.rename(output)
    finally:
        if temporary.exists():temporary.unlink()
    return result


def prepare_enrichment_classification(old_inputs,enriched_dir,output_dir,*,expected_count=2020,
                                     model='gpt-6.1-sol',cli_version='0.162.1',batch_size=5,
                                     concurrency=8,timeout=900,max_run_seconds=None,
                                     max_observed_tokens=None,stop_on_rate_limit=True):
    """Full WorkInput hashes define the delta, including rights and source refs.

    Crosswalks compare snapshots only; no old result, ID, review pack or label is rewritten.
    """
    validate_controls(batch_size=batch_size,concurrency=concurrency,timeout=timeout,
                      max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens)
    if isinstance(expected_count,bool) or not isinstance(expected_count,int) or expected_count<1:raise ValueError('Expected count must be positive integer')
    if not isinstance(stop_on_rate_limit,bool):raise ValueError('stop_on_rate_limit must be bool')
    if not isinstance(model,str) or not model.strip() or not isinstance(cli_version,str) or not cli_version.strip():raise ValueError('Explicit model and supplied CLI version required')
    old_inputs=Path(old_inputs).resolve(strict=True);enriched_dir=Path(enriched_dir).resolve(strict=True);output=Path(output_dir).resolve()
    trees=[old_inputs.parent,enriched_dir]
    if output.exists():raise ValueError('Output namespace must be new')
    if any(output.is_relative_to(tree) for tree in [*trees,ROOT/'src',ROOT/'config',ROOT/'schemas']):raise ValueError('Output must be outside input/core trees')
    protected=inventory(trees)
    artifact_bytes={name:(ROOT/name).read_bytes() for name in ARTIFACTS}
    artifact_sha={name:sha(raw) for name,raw in artifact_bytes.items()}
    old_manifest_path=old_inputs.with_name('manifest.json');new_manifest_path=enriched_dir/'manifest.json'
    old_manifest=read_json(old_manifest_path);new_manifest=read_json(new_manifest_path)
    old_raw=old_inputs.read_bytes()
    if sha(old_raw)!=old_manifest.get('ordered_inputs_sha256') or old_manifest.get('work_count')!=expected_count:
        raise ValueError('Old ordered input SHA/count differs from its manifest')
    if new_manifest.get('outputs_are_valid') is not True or new_manifest.get('group_connected_partition_unchanged') is not True:
        raise ValueError('Enrichment is not a verified partition-preserving snapshot')
    if any(new_manifest.get(key)!=expected_count for key in ('canonical_works_before','canonical_works_after')):
        raise ValueError('Enriched canonical count differs')
    if any((enriched_dir/('dataset.sqlite'+suffix)).exists() for suffix in ('-wal','-shm','-journal')):
        raise ValueError('Enriched database has a volatile SQLite sidecar')
    declared=new_manifest['output_sha256']
    for name,checksum in declared.items():
        if name.endswith(('-wal','-shm','-journal')):raise ValueError('Volatile SQLite sidecar is not a durable proof')
        if sha(contained(enriched_dir,name).read_bytes())!=checksum:raise ValueError('Enriched durable output SHA differs: '+name)
    input_names=[name for name in ('work-inputs.jsonl','bibliographic-work-inputs.jsonl') if name in declared]
    if len(input_names)!=1 or any(name not in declared for name in ('dataset.sqlite','canonical-crosswalk.json')):
        raise ValueError('Manifest must identify exactly one work input, database and crosswalk')
    new_path=contained(enriched_dir,input_names[0]);new_raw=new_path.read_bytes()
    old_works,old_lines=rows(old_raw,expected_count);new_works,new_lines=rows(new_raw,expected_count)
    old_by_id={w.work_id:w for w in old_works};new_by_id={w.work_id:w for w in new_works}
    crosswalk_path=contained(enriched_dir,'canonical-crosswalk.json');crosswalk_raw=crosswalk_path.read_bytes()
    crosswalk=verify_crosswalk(strict_json(crosswalk_raw),old_by_id,new_by_id)
    taxonomy=strict_json(artifact_bytes['config/taxonomy.json'])
    config=build_run_config(model,cli_version,taxonomy,batch_size)
    for field,name in [('prompt_hash','src/recommend_book/classification/prompt.py'),('schema_hash','schemas/classification.schema.json'),('validation_hash','src/recommend_book/classification/validation.py')]:
        if config[field]!=artifact_sha[name]:raise ValueError('Config/artifact changed during preparation')
    comparisons=[];changed_old=[];changed_new=[];unchanged=[];delta_lines=[];merged_lines=[];prompts={}
    for old in old_works:
        new=new_by_id[crosswalk[old.work_id]]
        old_hash,new_hash=input_hash(old),input_hash(new);changed=old_hash!=new_hash
        before,after=old.model_dump(mode='json'),new.model_dump(mode='json')
        fields=[name for name in before if canonical_json(before[name])!=canonical_json(after[name])]
        comparison={'old_work_id':old.work_id,'new_work_id':new.work_id,'expression_id':new.expression_id,
                    'old_input_hash':old_hash,'new_input_hash':new_hash,'changed':changed,
                    'identity_changed':old.work_id!=new.work_id,'changed_fields':fields,
                    'source_refs_changed':old.source_record_ids!=new.source_record_ids,
                    'old_source_record_ids':old.source_record_ids,'new_source_record_ids':new.source_record_ids,
                    'source_refs_added':sorted(set(new.source_record_ids)-set(old.source_record_ids)),
                    'source_refs_removed':sorted(set(old.source_record_ids)-set(new.source_record_ids)),
                    'evidence_changed':before['evidence']!=after['evidence'],
                    'old_line_sha256':sha(old_lines[old.work_id]),'new_source_line_sha256':sha(new_lines[new.work_id]),
                    'before_evidence':coverage([old]),'after_evidence':coverage([new])}
        if changed:
            changed_old.append(old);changed_new.append(new);delta_lines.append(new_lines[new.work_id]);merged_lines.append(new_lines[new.work_id])
            prompt=prompt_copy(new,model=model,cli_version=cli_version,taxonomy=taxonomy)
            old_prompt=prompt_copy(old,model=model,cli_version=cli_version,taxonomy=taxonomy)
            relative='prompts/'+new_hash+'.txt';prompts[relative]=prompt
            comparison.update(prompt_path=relative,new_prompt_sha256=sha(prompt),old_prompt_sha256=sha(old_prompt))
        else:
            unchanged.append(old);merged_lines.append(old_lines[old.work_id])
            comparison['unchanged_line_byte_preserved']=True
        comparisons.append(comparison)
    delta_raw=b''.join(delta_lines);merged_raw=b''.join(merged_lines)
    controls={'batch_size':batch_size,'concurrency':concurrency,'timeout':timeout,'max_run_seconds':max_run_seconds,
              'max_observed_tokens':max_observed_tokens,'stop_on_rate_limit':stop_on_rate_limit}
    report={'version':'enrichment-classification-delta-v1','prepared_at':datetime.now(timezone.utc).isoformat(),
            'execution_state':'prepared_not_started','llm_started':False,'web_requests':0,'classification_rebound':False,
            'human_annotations_created':0,'review_packs_rebound':False,'human_quality_verified':False,
            'old_inputs_path':str(old_inputs),'old_inputs_sha256':sha(old_raw),'old_manifest_sha256':sha(old_manifest_path.read_bytes()),
            'enriched_dir':str(enriched_dir),'enriched_manifest_sha256':sha(new_manifest_path.read_bytes()),
            'new_database_path':str(enriched_dir/'dataset.sqlite'),'new_database_sha256':declared['dataset.sqlite'],
            'new_all_inputs_path':str(new_path),'new_all_inputs_sha256':sha(new_raw),
            'canonical_crosswalk_path':'canonical-crosswalk.source.json','canonical_crosswalk_sha256':sha(crosswalk_raw),
            'input_comparison_path':'input-comparison.json','work_count':len(changed_new),'total_work_count':expected_count,
            'changed_count':len(changed_new),'unchanged_count':len(unchanged),
            'identity_changed_count':sum(r['identity_changed'] for r in comparisons),
            'changed_work_ids':[w.work_id for w in changed_new],'unchanged_work_ids':[w.work_id for w in unchanged],
            'ordered_inputs_sha256':sha(delta_raw),'work_inputs_sha256':sha(delta_raw),'all_inputs_sha256':sha(merged_raw),
            'unchanged_rows_byte_preserved':True,'delta_order':'Old ordered-input order through verified one-to-one crosswalk; each changed row uses unmodified new source bytes',
            'source_files_unchanged_before_publish':True,'protected_files_count':len(protected)+len(ARTIFACTS),
            'evidence_comparison':{'all':{'before':coverage(old_works),'after':coverage(new_works)},
                                   'changed':{'before':coverage(changed_old),'after':coverage(changed_new)}},
            'features_enrichment_comparison':'Evidence coverage only; classification labels/features require a separate approved new run, not old-result rebinding',
            'processing_context':old_manifest.get('processing_context'),
            'processing_context_use':'Legacy input provenance only; current execution context is separately frozen below',
            'execution_context':{'run_config':config,'controls':controls,'artifact_count':len(ARTIFACTS),
                                 'artifact_sha256':artifact_sha,'cli_version_verification':'supplied_unverified','code_root':str(ROOT)}}
    comparison_raw=(json.dumps(comparisons,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf8')
    report['input_comparison_sha256']=sha(comparison_raw)
    output.parent.mkdir(parents=True,exist_ok=True);stage=output.with_name(output.name+'.prepare-'+uuid.uuid4().hex);stage.mkdir()
    try:
        copies={'ordered-inputs.jsonl':delta_raw,'all-inputs.jsonl':merged_raw,'input-comparison.json':comparison_raw,
                'canonical-crosswalk.source.json':crosswalk_raw,'old-input-manifest.source.json':old_manifest_path.read_bytes(),
                'enriched-manifest.source.json':new_manifest_path.read_bytes(),**prompts,
                **{'artifacts/'+name:raw for name,raw in artifact_bytes.items()}}
        for name,raw in copies.items():
            target=stage/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        write_json(stage/'protected-inputs-sha.json',protected)
        copies['protected-inputs-sha.json']=(stage/'protected-inputs-sha.json').read_bytes()
        report['output_sha256']={name:sha(raw) for name,raw in copies.items()}
        write_json(stage/'execution-context.json',report['execution_context'])
        report['output_sha256']['execution-context.json']=sha((stage/'execution-context.json').read_bytes())
        write_json(stage/'manifest.json',report)
        if any(sha((stage/name).read_bytes())!=checksum for name,checksum in report['output_sha256'].items()):
            raise ValueError('Staged output changed during preparation')
        if inventory(trees)!=protected or any((ROOT/name).read_bytes()!=raw for name,raw in artifact_bytes.items()):
            raise ValueError('Source/core artifact changed during preparation')
        if build_run_config(model,cli_version,taxonomy,batch_size)!=config:raise ValueError('Run config changed during preparation')
        if output.exists():raise ValueError('Output collision during preparation')
        stage.rename(output)
        return report
    finally:
        if stage.exists():
            resolved=stage.resolve()
            if resolved.parent!=output.parent or not resolved.name.startswith(output.name+'.prepare-'):
                raise ValueError('Unsafe staging cleanup target')
            shutil.rmtree(resolved)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-inputs',type=Path,required=True)
    parser.add_argument('--enriched',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',default='gpt-6.1-sol');parser.add_argument('--cli-version',default='0.162.1')
    parser.add_argument('--batch-size',type=int,default=5);parser.add_argument('--concurrency',type=int,default=8)
    parser.add_argument('--timeout',type=float,default=900)
    parser.add_argument('--max-run-seconds',type=float);parser.add_argument('--max-observed-tokens',type=int)
    args=parser.parse_args(argv)
    report=prepare_enrichment_classification(args.old_inputs,args.enriched,args.output,model=args.model,cli_version=args.cli_version,
        batch_size=args.batch_size,concurrency=args.concurrency,timeout=args.timeout,max_run_seconds=args.max_run_seconds,max_observed_tokens=args.max_observed_tokens)
    print(json.dumps({key:report[key] for key in ('execution_state','total_work_count','changed_count','unchanged_count','identity_changed_count','ordered_inputs_sha256','evidence_comparison')},ensure_ascii=False))


if __name__=='__main__':main()
