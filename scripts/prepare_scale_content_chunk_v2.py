"""Measured eight-slot continuation over the unchanged native runtime."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType

ROOT=Path(__file__).resolve().parents[1]


def trial_control(path,expected_sha256):
    from recommend_book.scale_content_control_v2 import verify_control_receipt
    receipt=verify_control_receipt(path,expected_sha256)
    run=json.loads(Path(receipt['control_artifacts']['run']['path']).read_bytes())
    if (run['initial_concurrency'] not in (4,8) or run['final_concurrency']!=run['initial_concurrency']
            or run['failed'] or run['pending'] or run.get('stopped') or run.get('stop_reasons')):
        raise ValueError('Eight slots require an actual clean completed four/eight-slot gate')
    return receipt


def load_bounded_preparer():
    original=ROOT/'scripts/prepare_scale_classification_v1.py'
    source=original.read_text(encoding='utf-8').replace('\r\n','\n')
    before="    if concurrency not in (2, 4):\n        raise ValueError('Scale concurrency is bounded to two or four')"
    after="    if concurrency not in (2, 4, 8):\n        raise ValueError('Measured content concurrency must be two, four or eight')"
    if source.count(before)!=1:
        raise ValueError('Immutable scale concurrency anchor drift')
    module=ModuleType('measured_content_preparation')
    module.__file__=str(original)
    exec(compile(source.replace(before,after),str(original),'exec'),module.__dict__)
    return module


def frozen_prepare(preparation,inputs,ledger,name,concurrency):
    from recommend_book.scale_guard_context_v1 import enable_process_cache
    cache=enable_process_cache()
    return load_bounded_preparer().prepare_chunk(preparation,inputs,ledger,
        cli_version='codex-cli 0.163.0',run_name=name,concurrency=concurrency)


def prepare(preparation,inputs,ledger,name,concurrency,*,prior_control=None,prior_control_sha256=None):
    sys.path.insert(0,str(ROOT/'scripts'))
    from prepare_scale_content_chunk_v1 import verify_frozen_runtime,sha
    if concurrency not in (2,4,8):
        raise ValueError('Bounded concurrency required')
    if concurrency==8:
        if prior_control is None or prior_control_sha256 is None:
            raise ValueError('Measured authentic control receipt required before eight slots')
        trial_control(prior_control,prior_control_sha256)
    preparation=Path(preparation).resolve(strict=True)
    frozen=verify_frozen_runtime(preparation)
    code=('import json,sys;from pathlib import Path;sys.path.insert(0,str(Path.cwd()/"scripts"));'
          'from prepare_scale_content_chunk_v2 import frozen_prepare;'
          'print(json.dumps(frozen_prepare(*map(Path,sys.argv[1:4]),sys.argv[4],int(sys.argv[5]))))')
    env=dict(os.environ,PYTHONPATH=str(Path(frozen['runtime'])/'src'),PYTHONDONTWRITEBYTECODE='1')
    argv=[sys.executable,'-c',code,str(preparation),str(Path(inputs).resolve(strict=True)),
          str(Path(ledger).resolve(strict=True)),name,str(concurrency)]
    process=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,shell=False,timeout=900)
    prefix=preparation/('preflight.'+name)
    Path(str(prefix)+'.stdout.txt').write_bytes(process.stdout)
    Path(str(prefix)+'.stderr.txt').write_bytes(process.stderr)
    if process.returncode:
        raise ValueError('Actual measured-content preflight failed: '+process.stderr.decode('utf-8','replace')[-2000:])
    if verify_frozen_runtime(preparation)!=frozen:
        raise ValueError('Frozen runtime changed during measured preflight')
    receipt={**frozen,'adapter_path':str(Path(__file__).resolve()),'adapter_sha256':sha(__file__),
        'unchanged_chunk_preparer_sha256':sha(ROOT/'scripts/prepare_scale_classification_v1.py'),
        'actual_preflight_exit_code':0,'actual_preflight_sha256':sha(Path(str(prefix)+'.json')),
        'concurrency':concurrency,'model_calls':0,
        'prior_actual_control_receipt':str(Path(prior_control).resolve()) if prior_control else None,
        'prior_actual_control_sha256':prior_control_sha256}
    with Path(str(prefix)+'.frozen-validation.json').open('x',encoding='utf-8') as stream:
        json.dump(receipt,stream,ensure_ascii=True,indent=2);stream.write('\n')
    return json.loads(process.stdout)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-preparation',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,required=True)
    parser.add_argument('--usage-ledger',type=Path,required=True)
    parser.add_argument('--run-name',required=True)
    parser.add_argument('--concurrency',type=int,choices=(2,4,8),required=True)
    parser.add_argument('--prior-control',type=Path)
    parser.add_argument('--prior-control-sha256')
    args=parser.parse_args()
    print(json.dumps(prepare(args.runtime_preparation,args.inputs,args.usage_ledger,args.run_name,args.concurrency,
        prior_control=args.prior_control,prior_control_sha256=args.prior_control_sha256)))


if __name__=='__main__':main()
