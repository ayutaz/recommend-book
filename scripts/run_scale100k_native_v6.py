"""Continue from genuine control75 with unattempted v9 sources after timeout76."""
import argparse
import ctypes
from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
from types import ModuleType

from recommend_book import scale100k_native_v1 as state
from recommend_book.scale_content_control_v4 import verify_control_receipt

ROOT, BASE = state.ROOT, state.BASE
DRIVER_V5_SHA = 'f1f361af6d9292432c44b55d11645ee71256d15958ff0996ae63e8a8088e2bbf'
COORDINATOR_SHA = 'd2ed9452abdd2751ba0e2eeee38fd00d54406ee409392dda0b69f82cc89f1f7f'
PRIOR_NAME = 'scale100k-content-3301-3400-v7'
PRIOR_SHA = '00e8dc8cf23d08daf9ba00dc6ecd26f34a2b0c72f38c612b097d22df78993099'
FAILED_NAME = 'scale100k-content-3401-3500-v7'
ENTRY = BASE / 'generation-v9-entry.json'


def validate_failure_facts(d):
    if d.get('version') != 'scale100k-review-timeout-diagnostic-v1':
        raise ValueError('Exact timeout diagnostic required')
    integers = {'failed_global_index':76,'prior_global_index':75,'selected_works':100,
                'native_completed':99,'native_failed':1,'mechanically_unreviewed_works':5,
                'review_whole_holds':0,'conservative_failure_count':6,'failed_window_sql_rows':0,
                'eligible_cumulative':8369,'model_calls_added':0}
    if any(type(d.get(k)) is not int or d[k] != v for k,v in integers.items()):
        raise ValueError('Timeout window facts differ')
    if (d.get('failed_window_accepted') is not False or d.get('original_artifacts_modified') is not False
            or d.get('conservative_failure_rate') != .06 or d.get('unchanged_max_failure_rate') != .05
            or d.get('driver_actual_exit_code') is not None):
        raise ValueError('Failed window cannot borrow adoption or an inferred parent exit')
    groups = d.get('groups',[])
    if len(groups) != 20 or [g['group'] for g in groups] != list(range(1,21)):
        raise ValueError('All twenty actual review groups required')
    ids=[]
    for g in groups:
        if (type(g.get('actual_exit_code')) is not int or g['actual_exit_code'] != (1 if g['group']==14 else 0)
                or g.get('timeout') is not (g['group']==14)
                or g.get('quality_adoption_review_saved') is not (g['group']!=14)
                or not 1 <= len(g['expected_work_ids']) <= 5):
            raise ValueError('Different review failure requires separate diagnosis')
        ids.extend(g['expected_work_ids'])
    if len(ids)!=99 or len(set(ids))!=99 or len(groups[13]['expected_work_ids'])!=5:
        raise ValueError('Actual disjoint review selection differs')


def adapted_source(raw):
    if state.hashlib.sha256(raw).hexdigest() != DRIVER_V5_SHA:
        raise ValueError('Frozen driver-v5 changed')
    recipe=importlib.import_module('run_scale100k_native_v5')
    source=recipe.adapted_source((ROOT/'scripts/run_scale100k_native_v4.py').read_bytes())
    changes=(("prior['window_index'] != 49:","prior['window_index'] != 75:"),
             ('scripts/run_scale100k_content_classification_v5.py','scripts/run_scale100k_content_classification_v6.py'),
             ('window-source-overrides-v3.json','window-source-overrides-v9.json'),
             ('classification-sources-v3','classification-sources-v9'),
             ('generation-v7-continuation-entry.json','generation-v9-entry.json'))
    for before,after in changes:
        expected=2 if before=='generation-v7-continuation-entry.json' else 1
        if source.count(before)!=expected:raise ValueError('Timeout continuation anchor differs: '+before)
        source=source.replace(before,after)
    return source


def verify_coordinator():
    if state.sha(ROOT/'scripts/run_scale100k_content_classification_v6.py') != COORDINATOR_SHA:
        raise ValueError('Timeout continuation coordinator pin differs')


def select_source(global_index, default, override, expected_sha):
    override=Path(override)
    if state.sha(override)!=expected_sha:raise ValueError('Fresh source map changed')
    value=state.read(override).get(str(global_index))
    if value is None:raise ValueError('No fresh source in sealed map')
    source=Path(value).resolve(strict=True)
    if not source.is_relative_to((BASE/'classification-sources-v9').resolve()):
        raise ValueError('Fresh source leaves v9 namespace')
    return source


def load_operations(override, expected_sha):
    source=adapted_source((ROOT/'scripts/run_scale100k_native_v5.py').read_bytes())
    scope=ModuleType('scale100k_timeout_continuation_v6');scope.__file__=str(Path(__file__).resolve())
    scope.verify_coordinator=verify_coordinator
    exec(compile(source,scope.__file__,'exec'),scope.__dict__)
    scope.selected_source=lambda i,default:select_source(i,default,override,expected_sha)
    return scope


def observe_prior_processes_absent():
    if os.name!='nt':raise ValueError('Actual Windows process absence must be observed')
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    for pid in (143868,53352,131968,167800):
        handle=kernel.OpenProcess(0x1000,False,pid)
        if handle:
            kernel.CloseHandle(handle);raise ValueError('Prior process ID is still live; do not start another driver')
        if ctypes.get_last_error()!=87:raise ValueError('Prior process absence could not be verified')
    return {'queried_pids':[143868,53352,131968,167800],'found_pids':[],
            'observed_at':datetime.now(timezone.utc).isoformat(),'method':'OpenProcess ERROR_INVALID_PARAMETER'}


def start(index_path,index_sha,diagnostic,diagnostic_sha,override,override_sha):
    state.verify_requirements();state.load_exclusion_index(index_path,index_sha);verify_coordinator()
    if state.sha(diagnostic)!=diagnostic_sha:raise ValueError('Timeout diagnostic changed')
    d=state.read(diagnostic);validate_failure_facts(d)
    for path,digest in d['artifacts_sha256'].items():
        if state.sha(path)!=digest:raise ValueError('Retained timeout artifact changed')
    prior=BASE/'window-controls'/(PRIOR_NAME+'.json')
    if state.sha(prior)!=PRIOR_SHA or verify_control_receipt(prior,PRIOR_SHA)['window_index']!=75:
        raise ValueError('Genuine prior control75 required')
    if state.eligible_count()!=8369:raise ValueError('Failed window must remain unadopted')
    for path in (BASE/'classification-exports'/FAILED_NAME/'manifest.json',
                 BASE/'classification-exports-v3'/FAILED_NAME/'manifest.json',
                 BASE/'window-controls'/(FAILED_NAME+'.json')):
        if path.exists():raise ValueError('Failed window unexpectedly reached adoption')
    absence=observe_prior_processes_absent()
    first=select_source(76,None,override,override_sha)
    state.save(ENTRY,{'version':'scale100k-timeout-continuation-v9','failed_diagnostic':str(diagnostic),
        'failed_diagnostic_sha256':diagnostic_sha,'prior_control':str(prior),'prior_control_sha256':PRIOR_SHA,
        'source_override':str(override),'source_override_sha256':override_sha,
        'first_source':str(first),'first_source_sha256':state.sha(first/'manifest.json'),
        'driver_sha256':state.sha(__file__),'coordinator_sha256':COORDINATOR_SHA,
        'prior_process_absence':absence,'driver_actual_exit_code':None,
        'first_requested_concurrency':4,'original_artifacts_modified':False,'failed_inputs_resubmitted':False})
    load_operations(override,override_sha).run(index_path,index_sha,prior,PRIOR_SHA,4)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exclusion-index',type=Path,required=True)
    p.add_argument('--exclusion-index-sha256',required=True)
    p.add_argument('--diagnostic',type=Path,required=True)
    p.add_argument('--diagnostic-sha256',required=True)
    p.add_argument('--source-override',type=Path,required=True)
    p.add_argument('--source-override-sha256',required=True)
    a=p.parse_args();start(a.exclusion_index,a.exclusion_index_sha256,a.diagnostic,a.diagnostic_sha256,
                         a.source_override,a.source_override_sha256)


if __name__=='__main__':main()
