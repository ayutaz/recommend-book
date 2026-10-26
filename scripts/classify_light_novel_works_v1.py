"""Freeze and run exact fresh LN WORK inputs; no series/world writer."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from recommend_book.light_novel_work_sources_v1 import WorkInput,validate_source_admission
from recommend_book.light_novel_work_history_v1 import verify,recheck_current
from recommend_book.light_novel_work_classification_v1 import run_window,freeze_runtime,ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','source-manifest','source-sha256','history-audit','history-sha256','reservations','output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--concurrency',type=int,choices=(4,8),default=4)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--executable')
    parser.add_argument('--frozen-child',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    for field in ('inputs','source_manifest','history_audit','reservations','output'):
        setattr(args,field,str(Path(getattr(args,field)).resolve()))
    inputs=[WorkInput.model_validate_json(line) for line in Path(args.inputs).read_bytes().splitlines() if line.strip()]
    validate_source_admission(inputs,args.source_manifest,args.source_sha256)
    audit=verify(args.history_audit,args.history_sha256,[w.work_id for w in inputs]);recheck_current(audit)
    if not args.frozen_child:
        output=Path(args.output)
        if output.exists():raise ValueError('Work output already exists; no retries')
        runtime=freeze_runtime(output.parent/(output.name+'-runtime'))
        argv=[sys.executable,str(runtime/'scripts/classify_light_novel_works_v1.py'),'--frozen-child']
        for field in ('inputs','source_manifest','source_sha256','history_audit','history_sha256','reservations','output','concurrency','timeout'):
            argv.extend(['--'+field.replace('_','-'),str(getattr(args,field))])
        if args.executable:argv.extend(['--executable',args.executable])
        env=dict(os.environ,PYTHONPATH=str(runtime/'src'),PYTHONDONTWRITEBYTECODE='1')
        result=subprocess.run(argv,cwd=runtime,env=env,shell=False,check=True,capture_output=True)
        print(result.stdout.decode('utf8').strip());return
    print(json.dumps(run_window(inputs,args.output,source_manifest=args.source_manifest,
        source_sha256=args.source_sha256,reservations=args.reservations,
        history_manifest=args.history_audit,history_sha256=args.history_sha256,
        concurrency=args.concurrency,timeout=args.timeout,executable=args.executable),ensure_ascii=True))


if __name__=='__main__':main()
