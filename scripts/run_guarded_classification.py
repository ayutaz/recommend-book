"""Freeze a guarded v3 snapshot, then run only after the root's independent audit."""
import argparse
import hashlib
import json
from pathlib import Path

from recommend_book.classification.runner import build_run_config,immutable_bytes,immutable_json,run_classification,validate_controls
from recommend_book.classification.specs import ALL_SPEC_NAMES,get_spec
from recommend_book.classification import input_hash,load_taxonomy
from recommend_book.jobs import canonical_json
from recommend_book.models import WorkInput
from recommend_book.usage_guard import UsageGuard

ROOT=Path(__file__).resolve().parents[1]
OUTPUT_ROOT=ROOT/'data/autonomous-next-v3/classification'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(raw):
    works=[WorkInput.model_validate_json(line)for line in raw.splitlines()if line.strip()]
    ids=[(w.work_id,w.expression_id)for w in works]
    if not works or len(set(ids))!=len(ids):raise ValueError('Empty or duplicate work/expression snapshot')
    return works


def namespace_path(namespace):
    path=Path(namespace).resolve()
    if path==OUTPUT_ROOT.resolve() or not path.is_relative_to(OUTPUT_ROOT.resolve()):
        raise ValueError('New guarded namespace must be inside data/autonomous-next-v3/classification')
    return path


def prepare(inputs_path,usage_ledger,namespace,*,cli_version,execution_spec='guarded-v3-baseline',
        model='gpt-6.1-sol',batch_size=5,concurrency=2,timeout=900,max_run_seconds=1800,max_observed_tokens=500000):
    validate_controls(batch_size=batch_size,concurrency=concurrency,timeout=timeout,
        max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens)
    spec=get_spec(execution_spec);namespace=namespace_path(namespace)
    if namespace.exists():raise ValueError('Namespace exists; preparation never overwrites')
    source=Path(inputs_path).resolve(strict=True);ledger=Path(usage_ledger).resolve(strict=True)
    if source.is_relative_to(namespace) or ledger.is_relative_to(namespace):raise ValueError('Input/output trees overlap')
    input_sha=sha(source);ledger_sha=sha(ledger);works=rows(source.read_bytes())
    guard=UsageGuard(ledger,expected_ledger_sha256=ledger_sha)
    decisions=guard.assert_inputs_allowed(works,'external_llm')
    taxonomy=load_taxonomy();config=build_run_config(model,cli_version,taxonomy,batch_size,execution_spec=execution_spec)
    config_sha=hashlib.sha256(canonical_json(config).encode()).hexdigest()
    execution=guard.assert_execution_allowed(config_sha,'external_llm')
    artifacts={**config['dependency_sha256'],
        'src/recommend_book/classification/runner.py':config['runner_hash'],
        'src/recommend_book/classification/specs.py':config['spec_hash'],
        'src/recommend_book/usage_guard.py':config['usage_guard_hash'],
        spec.prompt_path.relative_to(ROOT).as_posix():config['prompt_hash'],
        spec.schema_path.relative_to(ROOT).as_posix():config['schema_hash'],
        'src/recommend_book/classification/validation.py':config['validation_hash'],
        **config.get('native_codebook_sha256',{})}
    root_context={p:sha(ROOT/p)for p in ('config/autonomous-next-v3.json','docs/source-rights-review-scope-v3.md')}
    artifacts.update(root_context)
    previews=[]
    for work in works:
        prompt=spec.expected_prompt([work],model=model,cli_version=cli_version,taxonomy=taxonomy).encode()
        receipt=guard.assert_prompt_allowed(prompt,[work],'external_llm',expected_prompt_sha256=hashlib.sha256(prompt).hexdigest())
        previews.append((input_hash(work)+'.utf8',prompt,receipt.model_dump(mode='json')))
    if sha(source)!=input_sha or sha(ledger)!=ledger_sha:raise ValueError('Preparation source changed during verification')
    namespace.mkdir(parents=True);(namespace/'prompt-previews').mkdir();(namespace/'artifacts').mkdir()
    immutable_bytes(namespace/'inputs.jsonl',source.read_bytes())
    immutable_bytes(namespace/'usage-ledger.source.json',ledger.read_bytes())
    files={}
    for relative,expected in artifacts.items():
        source_artifact=ROOT/relative
        if sha(source_artifact)!=expected:raise ValueError('Execution artifact changed during preparation: '+relative)
        copied=namespace/'artifacts'/relative;copied.parent.mkdir(parents=True,exist_ok=True)
        immutable_bytes(copied,source_artifact.read_bytes());files[str(copied.relative_to(namespace))]=expected
    for name,prompt,receipt in previews:
        path=namespace/'prompt-previews'/name;files[str(path.relative_to(namespace))]=immutable_bytes(path,prompt)
        proof=namespace/'prompt-previews'/(name+'.receipt.json');files[str(proof.relative_to(namespace))]=immutable_json(proof,receipt)
    files['inputs.jsonl']=input_sha;files['usage-ledger.source.json']=ledger_sha
    report={'version':'guarded-classification-preparation-v3','stage':'prepared_not_started',
        'input_source_path':str(source),'input_sha256':input_sha,'usage_ledger_path':str(ledger),
        'usage_ledger_sha256':ledger_sha,'config':config,'config_sha256':config_sha,'selected':len(works),
        'controls':{'concurrency':concurrency,'batch_size':batch_size,'timeout':timeout,
            'max_run_seconds':max_run_seconds,'max_observed_tokens':max_observed_tokens,'stop_on_rate_limit':True},
        'input_identities':[{'work_id':w.work_id,'expression_id':w.expression_id,'input_hash':input_hash(w)}for w in works],
        'input_usage_receipts':[d.model_dump(mode='json')for d in decisions],
        'execution_usage_receipt':execution.model_dump(mode='json'),'artifact_sha256':artifacts,'output_sha256':files,
        'root_context_artifacts':root_context,
        'preview_scope':'single input preview; not evidence of actual batch stdin',
        'cli_version_scope':'supplied exact prior observation; prepare never starts/version-queries CLI',
        'independent_audit_approved':False,'llm_started':False,'cli_invocations':0,'human_verified':False}
    if sha(source)!=input_sha or sha(ledger)!=ledger_sha:raise ValueError('Preparation protected source changed')
    immutable_json(namespace/'preparation-manifest.json',report)
    return report


def verify_prepared(namespace):
    namespace=namespace_path(namespace)
    manifest=json.loads((namespace/'preparation-manifest.json').read_bytes())
    if manifest['version']!='guarded-classification-preparation-v3':raise ValueError('Unknown preparation version')
    for relative,expected in manifest['output_sha256'].items():
        target=(namespace/relative).resolve()
        if not target.is_relative_to(namespace) or sha(target)!=expected:raise ValueError('Prepared artifact changed: '+relative)
    if sha(manifest['input_source_path'])!=manifest['input_sha256'] or sha(manifest['usage_ledger_path'])!=manifest['usage_ledger_sha256']:
        raise ValueError('Prepared source or ledger changed')
    config=manifest['config'];fresh=build_run_config(config['model'],config['cli_version'],load_taxonomy(),config['batch_size'],execution_spec=config['execution_spec'])
    for relative,expected in manifest['root_context_artifacts'].items():
        if sha(ROOT/relative)!=expected:raise ValueError('Frozen root operating/rights context changed')
    if fresh!=config:raise ValueError('Current execution context differs from fixed preparation')
    guard=UsageGuard(manifest['usage_ledger_path'],expected_ledger_sha256=manifest['usage_ledger_sha256'])
    guard.assert_inputs_allowed(rows((namespace/'inputs.jsonl').read_bytes()),'external_llm')
    guard.assert_execution_allowed(manifest['config_sha256'],'external_llm')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--inputs',type=Path,required=True);prep.add_argument('--usage-ledger',type=Path,required=True)
    prep.add_argument('--namespace',type=Path,required=True);prep.add_argument('--cli-version',required=True)
    prep.add_argument('--execution-spec',choices=ALL_SPEC_NAMES,default='guarded-v3-baseline');prep.add_argument('--batch-size',type=int,default=5)
    prep.add_argument('--concurrency',type=int,default=2);prep.add_argument('--timeout',type=float,default=900)
    prep.add_argument('--max-run-seconds',type=float,default=1800);prep.add_argument('--max-observed-tokens',type=int,default=500000)
    run=sub.add_parser('run',help='root launches only after independent fixed-context audit');run.add_argument('--namespace',type=Path,required=True)
    run.add_argument('--preparation-sha256',required=True);run.add_argument('--executable')
    args=parser.parse_args(argv)
    if args.command=='prepare':
        result=prepare(args.inputs,args.usage_ledger,args.namespace,cli_version=args.cli_version,
            **{k:getattr(args,k)for k in ('execution_spec','batch_size','concurrency','timeout','max_run_seconds','max_observed_tokens')})
    else:
        namespace=namespace_path(args.namespace)
        if sha(namespace/'preparation-manifest.json')!=args.preparation_sha256:raise ValueError('Prepared manifest differs from root-audited snapshot')
        prepared=verify_prepared(namespace);config=prepared['config']
        result=run_classification(rows((namespace/'inputs.jsonl').read_bytes()),namespace/'run',model=config['model'],
            usage_ledger=Path(prepared['usage_ledger_path']),execution_spec=config['execution_spec'],executable=args.executable,
            expected_run_config=config,expected_usage_ledger_sha256=prepared['usage_ledger_sha256'],**prepared['controls'])
        verify_prepared(namespace)
    print(json.dumps({k:result.get(k)for k in ('version','stage','selected','completed','failed','pending','cli_invocations','llm_started','config_sha256','input_sha256')},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
