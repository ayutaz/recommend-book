"""Prepare/run the fixed blind AI silver experiment; preserve all prior artifacts."""
import argparse
import json
import hashlib
from pathlib import Path

from recommend_book.autonomous_silver import (
    prepare_blind_inputs,run_blind_lanes,compare_silver,adjudicate_silver,
    latest_original_results,_prepared,
)
from recommend_book.classification import read_results
from recommend_book.classification.runner import run_classification
from recommend_book.jobs import atomic_json


def scalar(report):
    return {'selected':report['selected'],'successful':report['successful'],
        'lanes':{k:{field:v.get(field) for field in ('selected','completed','failed','pending','reused','cli_invocations','usage','stopped','stop_reasons','run_id')}
        for k,v in report['lanes'].items()}}


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--namespace',type=Path,required=True)
    prep.add_argument('--review-pack',type=Path,default=Path('data/evaluation/poc-frozen-v2/400-review-balanced-v1/review_pack.json'))
    prep.add_argument('--latest-inputs',type=Path,default=Path('data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/all-inputs.jsonl'))
    prep.add_argument('--crosswalk',type=Path,default=Path('data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/canonical-crosswalk.source.json'))
    prep.add_argument('--database',type=Path,default=Path('data/poc-openlibrary-enriched-v1/dataset.sqlite'))
    prep.add_argument('--baseline',type=Path,default=Path('data/autonomous-poc-v1/baseline-manifest.json'))
    run=sub.add_parser('run');run.add_argument('--namespace',type=Path,required=True);run.add_argument('--pilot-count',type=int,default=20)
    run.add_argument('--continue-full',action='store_true');run.add_argument('--timeout',type=float,default=900)
    run.add_argument('--max-run-seconds',type=float,default=7200);run.add_argument('--max-observed-tokens',type=int,default=5000000)
    run.add_argument('--executable');run.add_argument('--adjudicate',action='store_true')
    run.add_argument('--usage-ledger',type=Path,required=True)
    run.add_argument('--execution-spec',choices=('guarded-v3-baseline','definitions-v3','context-v3'),default='guarded-v3-baseline')
    args=p.parse_args()
    if args.command=='prepare':
        report=prepare_blind_inputs(args.review_pack,args.latest_inputs,args.crosswalk,args.database,args.namespace,baseline_manifest=args.baseline)
        print(json.dumps({k:report[k] for k in ('membership_count','input_changed_count','identity_changed_count','blind_inputs_sha256','source_protected_before_after')},ensure_ascii=False),flush=True);return 0
    namespace,works,prepared=_prepared(args.namespace)
    print(json.dumps({'phase':'pilot_starting','input_sha256':prepared['blind_inputs_sha256'],'selected':args.pilot_count,'maximum_cli_concurrency':4}),flush=True)
    controls={k:getattr(args,k) for k in ('timeout','max_run_seconds','max_observed_tokens','executable','usage_ledger','execution_spec')}
    controls['expected_usage_ledger_sha256']=hashlib.sha256(args.usage_ledger.read_bytes()).hexdigest()
    report=run_blind_lanes(namespace,pilot_count=args.pilot_count,**controls)
    print(json.dumps({'phase':'pilot_terminal',**scalar(report)}),flush=True)
    if not report['successful']:return 2
    if not args.continue_full:return 0
    print(json.dumps({'phase':'full_starting','selected':len(works),'maximum_cli_concurrency':4}),flush=True)
    report=run_blind_lanes(namespace,**controls)
    print(json.dumps({'phase':'full_terminal',**scalar(report)}),flush=True)
    if not report['successful']:return 2
    a,b=read_results(namespace/'lane-a'),read_results(namespace/'lane-b')
    original=latest_original_results(namespace,Path('data/poc-combined-classified-v2-v1/dataset.sqlite'),Path('data/runs/poc-openlibrary-enriched-v1-delta-v1'))
    comparison=compare_silver(works,a,b,original);atomic_json(namespace/'comparison-report.json',comparison)
    third=[]
    if args.adjudicate:
        disagreement=set(comparison['disagreement_work_ids']);selected=[w for w in works if w.work_id in disagreement]
        print(json.dumps({'phase':'adjudication_starting','selected':len(selected),'maximum_cli_concurrency':2,'blind':True}),flush=True)
        m=run_classification(selected,namespace/'adjudication',concurrency=2,batch_size=5,model='gpt-6.1-sol',stop_on_rate_limit=True,**controls)
        third=read_results(namespace/'adjudication');atomic_json(namespace/'adjudication-run-report.json',m)
        print(json.dumps({'phase':'adjudication_terminal',**{k:m.get(k) for k in ('selected','completed','failed','pending','cli_invocations','usage','stopped','stop_reasons')}}),flush=True)
    silver=adjudicate_silver(works,a,b,third);atomic_json(namespace/'ai-silver-report.json',silver)
    _prepared(namespace)
    print(json.dumps({'phase':'complete','known_union':comparison['known_union'],'relevance':comparison['relevance'],'adjudication':silver['status_counts'],'human_gold_created':False}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
