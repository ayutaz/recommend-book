"""Resume only review/adoption/export from an authentic completed native100.

The failed oversized request is retained. No native-run execution is reachable:
the old coordinator's one terminal invocation is replaced by verified saved
arguments, and its original actual native coordinator remains correctly named.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
from types import ModuleType

ROOT=Path(__file__).resolve().parents[1]


def execute(source_preparation,runtime_preparation):
    source_preparation=Path(source_preparation).resolve(strict=True)
    runtime_preparation=Path(runtime_preparation).resolve(strict=True)
    source=json.loads((source_preparation/'manifest.json').read_bytes())
    name=source['chunks'][0]['run_name']
    terminal=runtime_preparation/('terminal-'+name)
    observed=json.loads((terminal/'adoption-arguments.json').read_bytes())
    proof=json.loads((terminal/'terminal-proof.json').read_bytes())
    request=json.loads((terminal/'request.json').read_bytes())
    if observed['exit_code']!=0 or proof['exit_code']!=0 or proof['process_exit_observed'] is not True:
        raise ValueError('Authentic terminal-completed native results required')
    from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
    import hashlib
    sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    coordinator_chain_pins({'runtime_preparation':str(runtime_preparation),
        'run_manifest':str(Path(observed['run_dir'])/'manifest.json'),
        'coordinator_sha256':request['coordinator_sha256'],
        'artifact_sha256':{str(Path(observed['run_dir'])/'manifest.json'):proof['manifest_sha256'],
                           str(terminal/'terminal-proof.json'):sha(terminal/'terminal-proof.json')}})
    original=ROOT/'scripts/run_scale_content_classification_v1.py'
    if sha(original)!=request['coordinator_sha256']:
        raise ValueError('Actual original native coordinator changed')
    failed=ROOT/'data/autonomous-next-v3/scale-30000-v1/separate-review'/name
    failure=json.loads((failed/'process-exit.json').read_bytes())
    if failure['actual_exit_code']!=1 or 'input_too_large' not in (failed/'stderr.txt').read_text():
        raise ValueError('This recovery only handles observed pre-model transport size failure')
    code=original.read_text(encoding='utf-8').replace('\r\n','\n')
    anchor="    observed = terminal.execute(runtime_preparation, chunk['run_name'], source['source_database'])"
    if code.count(anchor)!=1:
        raise ValueError('Immutable terminal recovery anchor drift')
    code=code.replace(anchor,'    observed = RESUME_OBSERVED')
    anchor="        audit = json.loads(review.with_name('source-audit.json').read_bytes())\n        pins.extend(review.parent / name for name in audit['artifact_sha256'])"
    replacement="        for audit_binding in document['source_audits']:\n            audit_path = Path(audit_binding['path'])\n            audit = json.loads(audit_path.read_bytes())\n            pins.extend(audit_path.parent / name for name in audit['artifact_sha256'])"
    if code.count(anchor)!=1:
        raise ValueError('Immutable genuine review export anchor drift')
    code=code.replace(anchor,replacement)
    module=ModuleType('authentic_native_review_tail_resume')
    module.__file__=str(original)
    module.RESUME_OBSERVED=observed
    exec(compile(code,str(original),'exec'),module.__dict__)
    original_save=module.save

    def save(path,value):
        if value.get('version')=='scale-content-native-classification-export-v1':
            value.update(adoption_resume_adapter_path=str(Path(__file__).resolve()),
                adoption_resume_adapter_sha256=sha(__file__),
                failed_original_review_transport_only=True, failed_original_review_model_turn_started=False)
            value['artifact_sha256'].update({str(p.resolve()):sha(p) for p in (
                Path(__file__),ROOT/'scripts/review_scale_content_v2.py',
                *[failed/n for n in ('request.json','process-start.json','process-exit.json','stderr.txt','stdout.jsonl')])})
        return original_save(path,value)
    module.save=save

    def review(runtime,actual,run_name,*,full):
        if not full:
            raise ValueError('Initial recovery requires all actual100 labels reviewed')
        start=datetime.fromisoformat(request['started_at'])
        remaining=3600-(datetime.now(timezone.utc)-start).total_seconds()
        if remaining<=0:
            raise ValueError('Initial actual one-hour route budget exhausted before new review')
        output=ROOT/'data/autonomous-next-v3/scale-30000-v1/separate-review-sharded-v1'/run_name
        selection=output.with_name(run_name+'.selection.json')
        manifest=json.loads((Path(actual['run_dir'])/'manifest.json').read_bytes())
        ids=sorted({job['work_id'] for job in manifest['jobs'].values() if job['status']=='completed'})
        if len(ids)!=100:
            raise ValueError('This first actual recovery requires100 completed distinct works')
        original_save(selection,{'selected_work_ids':ids,'full':True,'completed_native_count':100,
            'failed_oversized_request_preserved':True,'new_disjoint_review_groups':2})
        args=[runtime/'runtime',actual['ordered_inputs'],actual['run_dir'],actual['execution_context_manifest'],
            actual['terminal_proof'],actual['usage_ledger'],sha(actual['usage_ledger']),output,
            min(900,max(1,int(remaining))),selection]
        child_code=('import json,sys; from pathlib import Path; from types import SimpleNamespace; '
            'sys.path.insert(0,str(Path.cwd()/"scripts")); from review_scale_content_v2 import execute; '
            'k=dict(zip(("runtime_root","ordered_inputs","run_dir","execution_context_manifest","terminal_proof","ledger"),map(Path,sys.argv[1:7]))); '
            'k.update(ledger_sha256=sys.argv[7],output=Path(sys.argv[8]),timeout=int(sys.argv[9]),selection_path=Path(sys.argv[10])); '
            'print(json.dumps(execute(SimpleNamespace(**k)),ensure_ascii=True))')
        result=module.frozen_call(runtime,child_code,args)
        # Conservatively include bootstrap, failed-request prep, diagnosis and
        # this review in the original one-hour budget, not only model seconds.
        result.update(elapsed_seconds=(datetime.now(timezone.utc)-start).total_seconds()-manifest['elapsed_seconds'],
            selection_path=str(selection),selection_sha256=sha(selection),full=True,
            failed_original_transport_request_count=1,failed_original_model_turn_count=0)
        return result
    module.review_content_window=review
    return module.execute_window(source_preparation,runtime_preparation,0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-preparation',type=Path,required=True)
    parser.add_argument('--runtime-preparation',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(execute(args.source_preparation,args.runtime_preparation),ensure_ascii=True))


if __name__=='__main__':
    main()
