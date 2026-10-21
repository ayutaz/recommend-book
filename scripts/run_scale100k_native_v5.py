"""Continue from genuinely adopted49v7 using the repaired review6 coordinator."""
import argparse
from pathlib import Path
from types import ModuleType

from recommend_book import scale100k_native_v1 as state
from recommend_book.scale_content_control_v4 import verify_control_receipt

ROOT,BASE=state.ROOT,state.BASE
V4_SHA='8b794314d776ba0cbf34ff1a81a3650d9ff26e13eac56bb649b74c143ba77d3c'
COORDINATOR_SHA='a9e045dc57d67ea4a700ee6af213c9cce196ea20ce41e97302c297a77e6f9928'
GENERATION_ENTRY_SHA='bf33abdbe75f7450727f4535c0c8e4485b6463828577fe11c9da0f1eb1d81c71'
CONTROL=BASE/'window-controls/scale100k-content-0701-0800-v7.json'


def adapted_source(raw):
    if state.hashlib.sha256(raw).hexdigest()!=V4_SHA:raise ValueError('Frozen v19 driver4 changed')
    recipe=ModuleType('fixed_native_driver4');recipe.__file__=str(ROOT/'scripts/run_scale100k_native_v4.py')
    exec(compile(raw,recipe.__file__,'exec'),recipe.__dict__)
    source=recipe.adapted_source((ROOT/'scripts/run_scale100k_native_v3.py').read_bytes())
    changes=(
        ('scripts/run_scale100k_content_classification_v4.py','scripts/run_scale100k_content_classification_v5.py'),
        ("prior['window_index'] != 48:","prior['window_index'] != 49:"),
        ('        if cohort == starting_cohort:\n            assert_first_source(source)\n',''),
        ('cohort > starting_cohort and clean','(cohort > starting_cohort or first_concurrency == 8) and clean'),
        ('completed_control, completed_control_sha):','completed_control, completed_control_sha, first_concurrency):'),
        ("'generation_entry_receipt': str(BASE / 'generation-v7-entry.json')",
         "'generation_entry_receipt': str(BASE / 'generation-v7-continuation-entry.json')"),
        ("'generation_entry_receipt_sha256': state.sha(BASE / 'generation-v7-entry.json')",
         "'generation_entry_receipt_sha256': state.sha(BASE / 'generation-v7-continuation-entry.json')"),
    )
    for before,after in changes:
        if source.count(before)!=1:raise ValueError('Successful49 continuation anchor differs: '+before)
        source=source.replace(before,after)
    return source


def verify_coordinator():
    if state.sha(ROOT/'scripts/run_scale100k_content_classification_v5.py')!=COORDINATOR_SHA:
        raise ValueError('Fixed repaired coordinator changed')


def load_operations():
    source=adapted_source((ROOT/'scripts/run_scale100k_native_v4.py').read_bytes())
    scope=ModuleType('adopted49_native_continuation');scope.__file__=str(Path(__file__).resolve())
    scope.verify_coordinator=verify_coordinator
    exec(compile(source,scope.__file__,'exec'),scope.__dict__)
    return scope


def start(index_path,index_sha,control_sha,first_concurrency):
    if type(first_concurrency) is not int or first_concurrency not in (4,8):raise ValueError('Four or eight actual slots required')
    state.verify_requirements();state.load_exclusion_index(index_path,index_sha);verify_coordinator()
    if state.sha(BASE/'generation-v7-entry.json')!=GENERATION_ENTRY_SHA:
        raise ValueError('Original exclusive generation entry changed')
    prior=verify_control_receipt(CONTROL,control_sha)
    if prior['window_index']!=49:raise ValueError('Actual adopted49 control required')
    state.save(BASE/'generation-v7-continuation-entry.json',{'version':'scale100k-successful49-continuation-v1',
        'control_path':str(CONTROL),'control_sha256':control_sha,'driver_sha256':state.sha(__file__),
        'generation_entry_sha256':GENERATION_ENTRY_SHA,'coordinator_sha256':COORDINATOR_SHA,
        'first_requested_concurrency':first_concurrency,'original_generation_entry_modified':False})
    load_operations().run(index_path,index_sha,CONTROL,control_sha,first_concurrency)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exclusion-index',type=Path,required=True)
    parser.add_argument('--exclusion-index-sha256',required=True)
    parser.add_argument('--completed-control-sha256',required=True)
    parser.add_argument('--first-concurrency',type=int,choices=(4,8),required=True)
    args=parser.parse_args();start(args.exclusion_index,args.exclusion_index_sha256,args.completed_control_sha256,args.first_concurrency)


if __name__=='__main__':main()
