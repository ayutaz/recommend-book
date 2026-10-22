"""Resume review/rights/import for saved clean 49v7; never rerun its native CLI."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

from recommend_book import scale100k_native_v1 as state
from recommend_book.scale_content_control_v4 import verify_control_receipt
from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins

ROOT, BASE, RUNTIME = state.ROOT, state.BASE, state.RUNTIME
NAME = 'scale100k-content-0701-0800-v7'
SOURCE = BASE/'classification-sources-v3/window-0701-0800-v7'
SOURCE_SHA = '8fd513539a9764f835422910b3fb54e216daf809d9ae7cc3e7ab1bac516ef584'
DIAGNOSTIC_SHA = '3121846afceba699d768fa1a86474658c71791e17adb01d0cd37fbea9a24245d'
PRIOR = BASE/'window-controls/scale100k-content-0601-0700-v6.json'
PRIOR_SHA = 'fdb637f1db4da85e3ef99055958752e81e17683d8e6e3cb52d8c2e8b63d43c76'
ORIGINAL_COORDINATOR = ROOT/'scripts/run_scale100k_content_classification_v4.py'
ORIGINAL_COORDINATOR_SHA = '19064d826984e215a340e261ac566f622bc380c16af2abad4a5a1e7e08606daf'
COORDINATOR_SHA = 'a9e045dc57d67ea4a700ee6af213c9cce196ea20ce41e97302c297a77e6f9928'
REVIEW_SHA = 'f9b0f7538a7282b6781f54adbb03d8f7cec9324547978f54ac17619ee374ef20'
TAIL_V1_SHA = '920019424ec5dd427023818a7ace70fcffd7983c6930e7e781bb1663d574c773'
DRIVER_V1_SHA = 'ca2c823fa1d822c2d69fda9d3ced266ae9246411783ec951373ade1a9d63c809'


def frozen_module(path, expected):
    path=Path(path); raw=path.read_bytes()
    if state.hashlib.sha256(raw).hexdigest()!=expected:
        raise ValueError('Frozen post-native recipe changed: '+str(path))
    scope=ModuleType('fixed_postnative_recipe_'+path.stem);scope.__file__=str(path.resolve())
    exec(compile(raw,scope.__file__,'exec'),scope.__dict__)
    return scope


def validate_failure_facts(native,exited,coordinator,stop,selection,error,review_output_exists):
    for key,expected in (('selected',100),('completed',100),('failed',0),('pending',0)):
        if type(native.get(key)) is not int or native[key]!=expected:
            raise ValueError('Only the fully completed native100 may use this tail')
    if native.get('stopped') is not False or native.get('stop_reasons'):
        raise ValueError('Another native stop cannot use this tail')
    for value,code in ((exited,0),(coordinator,1),(stop,1)):
        if (type(value.get('actual_exit_code')) is not int or value['actual_exit_code']!=code
                or value.get('process_exit_observed') is not True):
            raise ValueError('Exact actual process exits required')
    if (type(exited.get('process_pid')) is not int or exited['process_pid']!=168352
            or type(coordinator.get('pid')) is not int or coordinator['pid']!=46384
            or type(stop.get('session_id')) is not int or stop['session_id']!=88143
            or type(stop.get('tool_result',{}).get('exit_code')) is not int
            or stop['tool_result']['exit_code']!=1):
        raise ValueError('Actual saved native/coordinator/owner process identity differs')
    ids=selection.get('selected_work_ids',[])
    if (selection.get('full') is not True or type(selection.get('completed_native_count')) is not int
            or selection['completed_native_count']!=100 or len(ids)!=100 or len(set(ids))!=100
            or review_output_exists is not False
            or "NameError: name 'review_concurrency' is not defined" not in error
            or 'review_scale_content_v5.py' not in error):
        raise ValueError('Only the exact pre-model code failure with retained selection may resume')


def validate_finished_native():
    state.verify_requirements()
    diagnostic=BASE/'run-summaries'/f'{NAME}.pre-review-code-failure-diagnostic-v1.json'
    if state.sha(diagnostic)!=DIAGNOSTIC_SHA:
        raise ValueError('Fixed pre-review failure diagnostic changed')
    doc=state.read(diagnostic);pins=[diagnostic]
    for path,expected in doc['artifacts_sha256'].items():
        path=Path(path).resolve(strict=True)
        if not path.is_relative_to(ROOT.resolve()) or state.sha(path)!=expected:
            raise ValueError('Original pre-review saved artifact changed: '+str(path))
        pins.append(path)
    terminal=RUNTIME/('terminal-'+NAME)
    observed=state.read(terminal/'adoption-arguments.json')
    run=Path(observed['run_dir'])/'manifest.json';native=state.read(run)
    operational=BASE/'operational-processes'/NAME/'native-and-review'
    selection=BASE/'separate-review-sharded-v4'/(NAME+'.selection.json')
    old_review=BASE/'separate-review-sharded-v4'/NAME
    validate_failure_facts(native,state.read(terminal/'process-exit.json'),
        state.read(operational/'process-exit.json'),state.read(Path(doc['driver_stop'])),
        state.read(selection),(operational/'stderr.txt').read_text(encoding='utf-8'),old_review.exists())
    fixed=state.read(SOURCE/'manifest.json');reserved=state.read(BASE/'attempt-reservations'/(NAME+'.json'))
    request=state.read(terminal/'request.json')
    if (state.sha(SOURCE/'manifest.json')!=SOURCE_SHA or fixed['window_index']!=49
            or fixed['selected']!=100 or fixed['chunks'][0]['run_name']!=NAME
            or reserved['source_sha256']!=SOURCE_SHA or reserved['source_path']!=str(SOURCE)
            or reserved['global_index']!=49 or len(set(reserved['qids']))!=100
            or len(set(reserved['input_hashes']))!=100 or reserved['actual_overlap_count']!=0
            or type(observed['exit_code']) is not int or observed['exit_code']!=0
            or observed['source_database']!=fixed['source_database']
            or state.sha(observed['ordered_inputs'])!=fixed['chunks'][0]['inputs_sha256']
            or request['coordinator_path']!=str(ORIGINAL_COORDINATOR)
            or request['coordinator_sha256']!=ORIGINAL_COORDINATOR_SHA):
        raise ValueError('Saved original source/reservation/native/coordinator identity differs')
    state.load_exclusion_index(reserved['index_path'],reserved['index_sha256'])
    chain=coordinator_chain_pins({'runtime_preparation':str(RUNTIME),'run_manifest':str(run),
        'coordinator_sha256':ORIGINAL_COORDINATOR_SHA,
        'artifact_sha256':{str(run):state.sha(run),str(terminal/'terminal-proof.json'):state.sha(terminal/'terminal-proof.json')}})
    pins.extend(Path(row['path']) for row in chain)
    for directory in ('classification-exports','classification-exports-v3','catalogue-imports'):
        if (BASE/directory/NAME/'manifest.json').exists():
            raise ValueError('Original window already exported/imported; no automatic resume')
    if (BASE/'window-controls'/(NAME+'.json')).exists():
        raise ValueError('Original window already has a control; no automatic resume')
    pins.extend([Path(__file__).resolve(),ROOT/'scripts/run_scale100k_content_classification_v5.py',
                 ROOT/'scripts/review_scale_content_v6.py',ROOT/'scripts/run_scale100k_native_v5.py'])
    return observed,list(dict.fromkeys(pins))


def tail_recipe_source(raw=None):
    path=ROOT/'scripts/resume_scale100k_adoption_v1.py'
    raw=path.read_bytes() if raw is None else raw
    if state.hashlib.sha256(raw).hexdigest()!=TAIL_V1_SHA:
        raise ValueError('Frozen original complete-native tail recipe changed')
    text=raw.decode('utf-8').replace('\r\n','\n')
    start='def load_tail_recipe():\n';end='\n\ndef execute():'
    if text.count(start)!=1 or text.count(end)!=1:
        raise ValueError('Original complete-native tail recipe boundary differs')
    source=text[text.index(start):text.index(end)]
    changes=(
        ("path = ROOT/'scripts/run_scale100k_content_classification_v3.py'",
         "path = ROOT/'scripts/run_scale100k_content_classification_v5.py'"),
        ("    spec = importlib.util.spec_from_file_location('v19_five_work_coordinator', path)\n"
         "    coordinator = importlib.util.module_from_spec(spec); spec.loader.exec_module(coordinator)",
         '    coordinator = frozen_module(path,COORDINATOR_SHA)'),
        ('source = coordinator.adapted_source(ORIGINAL_COORDINATOR.read_bytes())',
         "source = coordinator.adapted_source((ROOT/'scripts/run_scale100k_content_classification_v4.py').read_bytes())"),
    )
    for before,after in changes:
        if source.count(before)!=1:raise ValueError('Post-native recipe anchor differs: '+before)
        source=source.replace(before,after)
    return source


def load_tail_recipe():
    scope=ModuleType('saved_native100_tail_loader');scope.__dict__.update(globals())
    exec(compile(tail_recipe_source(),str(Path(__file__).resolve()),'exec'),scope.__dict__)
    return scope.load_tail_recipe()


def execute():
    from recommend_book.scale_content_classification_v3 import create_reviewed_export
    observed,pins=validate_finished_native()
    prior=verify_control_receipt(PRIOR,PRIOR_SHA)
    if prior['window_index']!=48:raise ValueError('Actual prior control48 required')
    preflight=RUNTIME/('preflight.'+NAME+'.json')
    validation_path=RUNTIME/('preflight.'+NAME+'.frozen-validation.json');validation=state.read(validation_path)
    if (type(validation['actual_preflight_exit_code']) is not int or validation['actual_preflight_exit_code']!=0
            or validation['actual_preflight_sha256']!=state.sha(preflight)
            or validation['adapter_sha256']!=state.sha(validation['adapter_path'])
            or validation['snapshot_sha256']!=state.sha(RUNTIME/'source-snapshot-manifest.json')):
        raise ValueError('Original genuine preflight differs')
    if state.sha(ROOT/'scripts/review_scale_content_v6.py')!=REVIEW_SHA:
        raise ValueError('Fixed new review code changed')
    scope=load_tail_recipe()
    entry=BASE/'post-native-continuations'/NAME/'entry.json'
    state.save(entry,{'version':'scale100k-complete-native-post-review-entry-v2','run_name':NAME,
        'diagnostic_sha256':DIAGNOSTIC_SHA,'original_coordinator_sha256':ORIGINAL_COORDINATOR_SHA,
        'tail_path':str(Path(__file__).resolve()),'tail_sha256':state.sha(__file__),
        'postnative_coordinator_sha256':COORDINATOR_SHA,'review_sha256':REVIEW_SHA,
        'native_model_calls_added':0,'review_model_calls_before_entry':0,
        'artifact_sha256':{str(p):state.sha(p) for p in pins}})
    result=scope.resume_adoption(SOURCE,RUNTIME,49,PRIOR,PRIOR_SHA,observed,validation,
                                preflight,validation_path,[*pins,entry],prior)
    parent=Path(result['export_manifest']);native=state.read(parent)
    terminal=RUNTIME/('terminal-'+NAME)
    started,exited=state.read(terminal/'process-start.json'),state.read(terminal/'process-exit.json')
    pid_seconds=(datetime.fromisoformat(exited['observed_at'])-datetime.fromisoformat(started['started_at'])).total_seconds()
    model_seconds=pid_seconds+native['separate_review']['elapsed_seconds']
    if model_seconds>3600:raise ValueError('Original native PID plus actual review model hour exceeded')
    public=BASE/'classification-exports-v3'/NAME/'manifest.json'
    rights=create_reviewed_export(parent,state.sha(parent),public.parent)
    original=frozen_module(ROOT/'scripts/run_scale100k_native_v1.py',DRIVER_V1_SHA)
    original.__file__=str(Path(__file__).resolve());operation=original.load_operations()
    imported=operation.finalize(public,state.sha(public))
    control=BASE/'window-controls'/(NAME+'.json');control_sha=state.sha(control)
    run=state.read(observed['run_dir']+'/manifest.json')
    summary={'run_name':NAME,'global_index':49,'cohort_index':7,'pipeline_generation':5,
        'native_manifest':str(parent),'native_manifest_sha256':state.sha(parent),
        'public_manifest':str(public),'public_manifest_sha256':state.sha(public),
        'native_selected':run['selected'],'native_completed':run['completed'],'native_failed':run['failed'],
        'native_usage':run['usage'],'native_elapsed_seconds':run['elapsed_seconds'],
        'native_terminal_pid_interval_seconds':pid_seconds,'pid_inclusive_model_stage_seconds':model_seconds,
        'native_launch_through_public_import_wall_seconds':(datetime.now(timezone.utc)-datetime.fromisoformat(started['started_at'])).total_seconds(),
        'separate_review':native['separate_review'],'gate':imported['gate'],
        'actual_display_labels_by_axis':imported['actual_display_labels_by_axis'],
        'actual_public_eligible_cumulative':state.eligible_count(),'native_reclassified':False,
        'original_coordinator_pre_review_code_failure':True,'post_native_entry_sha256':state.sha(entry),
        'observed_at':datetime.now(timezone.utc).isoformat()}
    state.save(BASE/'run-summaries'/(NAME+'.json'),summary)
    verify_control_receipt(control,control_sha)
    completed={'version':'scale100k-postnative-completion-v2','control_path':str(control),
        'control_sha256':control_sha,'summary':summary,'rights_result':rights,'passed':True}
    state.save(entry.parent/'completed.json',completed)
    return completed


if __name__=='__main__':
    print(json.dumps(execute(),ensure_ascii=True))
