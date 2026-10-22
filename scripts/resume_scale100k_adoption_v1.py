"""Review and adopt the complete genuine v6 native run; never call its runner."""
import importlib.util
import json
from pathlib import Path
from types import ModuleType

from recommend_book import scale100k_native_v1 as state
from recommend_book.scale_content_control_v4 import verify_control_receipt
from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins

ROOT, BASE, RUNTIME = state.ROOT, state.BASE, state.RUNTIME
NAME = 'scale100k-content-0001-0100-v6'
SOURCE = BASE / 'classification-sources-v2/window-0001-0100-v6'
SOURCE_SHA = '5284caacb26081572e4d8dc4170fb76d82ab1d51ee7a390ad08e8b43ecff8fb8'
ORIGINAL_COORDINATOR = ROOT / 'scripts/run_scale100k_content_classification_v2.py'
ORIGINAL_COORDINATOR_SHA = '82fb38f1e9b271b2a1f15fd0d9da02c2bf268bd39cdcd72fd8d8609996a83169'
HOLD_SHA = '38f1ce6c0032e6e14369bf1d5a8cdd2beae777f6f6e8673b60b327f50ee7cf60'


def validate_finished_native():
    """A complete authentic terminal and the exact pre-review stop are required."""
    state.verify_requirements()
    if state.sha(SOURCE/'manifest.json') != SOURCE_SHA or state.sha(ORIGINAL_COORDINATOR) != ORIGINAL_COORDINATOR_SHA:
        raise ValueError('Original fixed native source/coordinator changed')
    fixed, hashes, qids = state._current_source(SOURCE, 42)
    reservation = BASE/'attempt-reservations'/(NAME+'.json')
    reserved = state.read(reservation)
    if (reserved['source_sha256'] != SOURCE_SHA or reserved['source_path'] != str(SOURCE)
            or reserved['input_hashes'] != sorted(hashes) or reserved['qids'] != sorted(qids)
            or reserved['global_index'] != 42 or reserved['actual_overlap_count'] != 0):
        raise ValueError('Original one-time native reservation differs')
    state.load_exclusion_index(reserved['index_path'], reserved['index_sha256'])
    hold = BASE/'separate-review-sharded-v1'/(NAME+'.selection.json')
    if state.sha(hold) != HOLD_SHA or state.read(hold)['stage'] != 'operator_hold_not_review_evidence':
        raise ValueError('Original pre-review operator hold differs')
    operational = BASE/'operational-processes'/NAME/'native-and-review'
    stopped = state.read(operational/'process-exit.json')
    error = (operational/'stderr.txt').read_text(encoding='utf-8')
    if (type(stopped['actual_exit_code']) is not int or stopped['actual_exit_code'] != 1
            or stopped['process_exit_observed'] is not True or 'FileExistsError' not in error
            or (str(hold) not in error and repr(str(hold)) not in error)):
        raise ValueError('Only the genuine pre-review exclusive operator stop may resume')
    terminal = RUNTIME/('terminal-'+NAME)
    observed = state.read(terminal/'adoption-arguments.json')
    exited, request = state.read(terminal/'process-exit.json'), state.read(terminal/'request.json')
    run = Path(observed['run_dir'])/'manifest.json'
    document = state.read(run)
    if (type(exited['actual_exit_code']) is not int or exited['actual_exit_code'] != 0
            or exited['process_exit_observed'] is not True or observed['exit_code'] != 0
            or document['selected'] != 100 or document['completed'] != 100
            or document['failed'] or document['pending'] or document.get('stopped') or document.get('stop_reasons')
            or request['coordinator_path'] != str(ORIGINAL_COORDINATOR)
            or request['coordinator_sha256'] != ORIGINAL_COORDINATOR_SHA
            or observed['source_database'] != fixed['source_database']
            or state.sha(observed['ordered_inputs']) != fixed['chunks'][0]['inputs_sha256']):
        raise ValueError('Only the exact fully completed original native run may be adopted')
    chain = coordinator_chain_pins({'runtime_preparation':str(RUNTIME),'run_manifest':str(run),
        'coordinator_sha256':ORIGINAL_COORDINATOR_SHA,
        'artifact_sha256':{str(run):state.sha(run),str(terminal/'terminal-proof.json'):state.sha(terminal/'terminal-proof.json')}})
    pins = [reservation, Path(reserved['index_path']), hold, ORIGINAL_COORDINATOR, Path(__file__).resolve(),
            operational/'request.json',operational/'process-start.json',operational/'process-exit.json',
            operational/'stderr.txt',terminal/'adoption-arguments.json',
            *[Path(row['path']) for row in chain], BASE/'requirements.json',BASE/'baseline-native-5000.jsonl',
            ROOT/'src/recommend_book/scale100k_native_v1.py',ROOT/'scripts/run_scale100k_native_v3.py',
            ROOT/'scripts/run_scale100k_content_classification_v3.py',ROOT/'scripts/review_scale_content_v4.py']
    return observed, pins


def load_tail_recipe():
    path = ROOT/'scripts/run_scale100k_content_classification_v3.py'
    spec = importlib.util.spec_from_file_location('v19_five_work_coordinator', path)
    coordinator = importlib.util.module_from_spec(spec); spec.loader.exec_module(coordinator)
    source = coordinator.adapted_source(ORIGINAL_COORDINATOR.read_bytes())
    start = "    output = ROOT / 'data/autonomous-next-v3/scale-100000-v1/classified'"
    end = '\n\ndef main():'
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('Complete native adoption tail boundary changed')
    tail = source[source.index(start):source.index(end)]
    if tail.count("'coordinator_sha256': sha(__file__)") != 2:
        raise ValueError('Native original-coordinator binding anchors changed')
    tail = tail.replace("'coordinator_sha256': sha(__file__)","'coordinator_sha256': ORIGINAL_COORDINATOR_SHA")
    scope = ModuleType('v19_complete_native_adoption_tail')
    scope.__file__ = str(Path(__file__).resolve())
    scope.REVIEW_SHA = coordinator.REVIEW_SHA
    exec(compile(source[:source.index('\ndef execute_window(')], str(path), 'exec'), scope.__dict__)
    scope.ORIGINAL_COORDINATOR_SHA = ORIGINAL_COORDINATOR_SHA
    scope.verify_control_receipt = verify_control_receipt
    scope.coordinator_chain_pins = coordinator_chain_pins
    signature = ('def resume_adoption(source_preparation,runtime_preparation,route_index,control_receipt,'
                 'control_receipt_sha256,observed,validation,preflight_path,validation_path,v19_pins,prior_control):\n'
                 '    chunk = json.loads((source_preparation / "manifest.json").read_bytes())["chunks"][0]\n')
    exec(compile(signature+tail,str(Path(__file__).resolve()),'exec'),scope.__dict__)
    return scope


def execute():
    observed, pins = validate_finished_native()
    prior = verify_control_receipt(state.PRIOR_CONTROL,state.PRIOR_CONTROL_SHA)
    if prior['window_index'] != 41:
        raise ValueError('Original genuine prior control differs')
    preflight = RUNTIME/('preflight.'+NAME+'.json')
    validation_path = RUNTIME/('preflight.'+NAME+'.frozen-validation.json')
    validation = state.read(validation_path)
    if (validation['actual_preflight_exit_code'] != 0 or validation['actual_preflight_sha256'] != state.sha(preflight)
            or validation['adapter_sha256'] != state.sha(validation['adapter_path'])
            or validation['snapshot_sha256'] != state.sha(RUNTIME/'source-snapshot-manifest.json')):
        raise ValueError('Original genuine preflight differs')
    scope = load_tail_recipe()
    result = scope.resume_adoption(SOURCE,RUNTIME,42,state.PRIOR_CONTROL,state.PRIOR_CONTROL_SHA,
        observed,validation,preflight,validation_path,pins,prior)
    return result


if __name__ == '__main__':
    print(json.dumps(execute(),ensure_ascii=True))
