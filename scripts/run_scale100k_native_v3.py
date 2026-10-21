"""Finish genuine saved native results, then continue with max-five reviews."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

from recommend_book import scale100k_native_v1 as state

ROOT, BASE, RUNTIME = state.ROOT, state.BASE, state.RUNTIME
V2_SHA = 'bd76cd842446ceb78f61837060a45e5638d3c079afe93f79bab8d4b0272a84e4'
FIRST_NAME = 'scale100k-content-0001-0100-v6'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V2_SHA:
        raise ValueError('Frozen v19 operational driver-v2 changed')
    text = raw.decode('utf-8').replace('\r\n','\n')
    boundary = '\nexec(compile(adapted_source('
    if text.count(boundary) != 1:
        raise ValueError('V19 operational driver-v2 recipe boundary differs')
    recipe = ModuleType('frozen_v19_driver_v2_recipe')
    recipe.__file__ = str(Path(__file__).resolve())
    exec(compile(text.split(boundary)[0],'<frozen-v19-driver-v2-recipe>','exec'),recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_native_v1.py').read_bytes())
    changes = (
        ('scripts/run_scale100k_content_classification_v2.py','scripts/run_scale100k_content_classification_v3.py'),
        ('def run(index_path, expected_index_sha):',
         'def run(index_path, expected_index_sha, completed_control, completed_control_sha):'),
        ('    previous_control, previous_sha = state.PRIOR_CONTROL, state.PRIOR_CONTROL_SHA',
         '    previous_control, previous_sha = Path(completed_control), completed_control_sha'),
        ("    if prior['window_index'] != 41:", "    if prior['window_index'] != 42:"),
        ('    cohort = 0\n','    cohort = 1\n'),
    )
    for before,after in changes:
        if source.count(before) != 1:
            raise ValueError('Five-work review operational adapter anchor differs: '+before)
        source=source.replace(before,after)
    return source


def load_operations():
    source=adapted_source(Path(__file__).with_name('run_scale100k_native_v2.py').read_bytes())
    scope=ModuleType('v19_five_work_review_operations')
    scope.__file__=str(Path(__file__).resolve())
    exec(compile(source,str(Path(__file__).resolve()),'exec'),scope.__dict__)
    return scope


def finish_first(operation):
    from recommend_book.scale_content_classification_v3 import create_reviewed_export
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    name=FIRST_NAME
    operation.observed_command([sys.executable,'scripts/resume_scale100k_adoption_v1.py'],
        BASE/'operational-processes'/name/'post-native-five-work-review')
    parent=BASE/'classification-exports'/name/'manifest.json'
    native=state.read(parent)
    terminal=RUNTIME/('terminal-'+name)
    started,exited=state.read(terminal/'process-start.json'),state.read(terminal/'process-exit.json')
    pid_seconds=(datetime.fromisoformat(exited['observed_at'])-datetime.fromisoformat(started['started_at'])).total_seconds()
    model_seconds=pid_seconds+native['separate_review']['elapsed_seconds']
    if model_seconds>3600:
        raise ValueError('Actual PID-inclusive model hour exhausted; preserve completed output')
    public=BASE/'classification-exports-v3'/name/'manifest.json'
    operation.emit(create_reviewed_export(parent,state.sha(parent),public.parent))
    prior=operation.load_operations().finalize(public,state.sha(public))
    control=BASE/'window-controls'/(name+'.json')
    control_sha=state.sha(control)
    document=state.read(public); run=state.read(document['run_manifest'])
    summary={'run_name':name,'cohort_index':0,'global_index':42,'pipeline_generation':3,
        'requirements_sha256':state.REQUIREMENTS_SHA,
        'native_manifest':str(parent),'native_manifest_sha256':state.sha(parent),
        'public_manifest':str(public),'public_manifest_sha256':state.sha(public),
        'native_selected':run['selected'],'native_completed':run['completed'],'native_failed':run['failed'],
        'native_usage':run['usage'],'native_elapsed_seconds':run['elapsed_seconds'],
        'native_terminal_pid_interval_seconds':pid_seconds,'pid_inclusive_model_stage_seconds':model_seconds,
        'native_launch_through_public_import_wall_seconds':(datetime.now(timezone.utc)-datetime.fromisoformat(started['started_at'])).total_seconds(),
        'gate':prior['gate'],'separate_review':document['separate_review'],
        'actual_display_labels_by_axis':prior['actual_display_labels_by_axis'],
        'actual_public_eligible_cumulative':state.eligible_count(),
        'native_reclassified':False,'native_batch_size':1,'max_review_works_per_call':5,
        'original_coordinator_pre_review_operator_hold':True,'observed_at':datetime.now(timezone.utc).isoformat()}
    state.save(BASE/'run-summaries'/(name+'.json'),summary)
    operation.emit({'stage':'actual_window_publicly_imported','summary':summary})
    verify_control_receipt(control,control_sha)
    return control,control_sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exclusion-index',type=Path,required=True)
    parser.add_argument('--exclusion-index-sha256',required=True)
    parser.add_argument('--finish-first-native',action='store_true')
    parser.add_argument('--completed-control',type=Path)
    parser.add_argument('--completed-control-sha256')
    args=parser.parse_args()
    state.verify_requirements();state.load_exclusion_index(args.exclusion_index,args.exclusion_index_sha256)
    operation=load_operations()
    if args.finish_first_native:
        if args.completed_control is not None:
            parser.error('Finish-first cannot borrow a completed control')
        completed,digest=finish_first(operation)
    else:
        if args.completed_control is None or args.completed_control_sha256 is None:
            parser.error('An exact successful first control is required before continuation')
        completed,digest=args.completed_control,args.completed_control_sha256
    operation.run(args.exclusion_index,args.exclusion_index_sha256,completed,digest)


if __name__=='__main__':
    main()
