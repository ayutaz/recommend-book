"""Continue from control 48 with fresh v7 sources; quarantine failed v6 intact."""
import argparse
import hashlib
from pathlib import Path
import sqlite3
from types import ModuleType

from recommend_book import scale100k_native_v1 as state

ROOT, BASE, RUNTIME = state.ROOT, state.BASE, state.RUNTIME
V3_SHA = '39b2010e64baf783649d9040f9feadb35ca6b58456c982219c6b6badce95c7e8'
COORDINATOR_SHA = '19064d826984e215a340e261ac566f622bc380c16af2abad4a5a1e7e08606daf'
FAILED_NAME = 'scale100k-content-0701-0800-v6'
PRIOR_NAME = 'scale100k-content-0601-0700-v6'
PRIOR_SHA = 'fdb637f1db4da85e3ef99055958752e81e17683d8e6e3cb52d8c2e8b63d43c76'
DIAGNOSTIC_SHA = '94b97ec309e8eeba293c0d730d55b7c21ad5d92beae9f54fb441dab1e96f9225'
STOP_SHA = '5fcc8c8dd459f8811d7c9a07eda1c85ee38c5d6f0ca9e1cc13b8e34bf83fb3be'
RESERVATION_SHA = 'fd1cece1ab11efa0ebb406b913eba711aa0f2d88c9221a070e6f33eafb1399a8'
SOURCE_SHA = '4930130fb0f961b64c592ade7a550b07e2c29b55ef926534b8d3e54c46caa625'
NATIVE_SHA = 'ea1ea713b4334fb5e74856424ebccc22f6db1b92de1dea38792d883f17001ebc'
ARGUMENTS_SHA = '0bab7d05fd95e67d3b5375ece0d55a43913e4b395dfdc5a56ea4ec2c6290a716'
NATIVE_EXIT_SHA = '5fe6d88a83fbcdc16ba198935df21c5a8350e5a231bcfc278b710ea18a0533c0'
TERMINAL_SHA = 'ebc82fd823316021c4eeb8cc967a1a6dd82111bd27f7b9dd8c3687b18b50bfed'


def pinned(path, expected, pins):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve(strict=True)
    if not path.is_relative_to(ROOT.resolve()) or state.sha(path) != expected:
        raise ValueError('Fresh-generation fixed artifact pin differs: ' + str(path))
    pins[str(path)] = expected
    return path


def exact_exit(value, expected):
    if (type(value.get('actual_exit_code')) is not int
            or value['actual_exit_code'] != expected or value.get('process_exit_observed') is not True):
        raise ValueError('Exact actual exit observation required')


def validate_failure_facts(native, native_exit, coordinator_exit, stop, groups, whole):
    for key, expected in (('selected',100),('completed',100),('failed',0),('pending',0)):
        if type(native.get(key)) is not int or native[key] != expected:
            raise ValueError('Only the complete original native 100 is quarantined here')
    if native.get('stopped') is not False or native.get('stop_reasons'):
        raise ValueError('Another native stop cannot use this generation entry')
    exact_exit(native_exit,0); exact_exit(coordinator_exit,1); exact_exit(stop,1)
    if (type(stop.get('session_id')) is not int or stop['session_id'] != 19712
            or type(stop.get('tool_result',{}).get('exit_code')) is not int
            or stop['tool_result']['exit_code'] != 1):
        raise ValueError('Original owner terminal exit 1 required')
    if len(groups) != 20 or [g['group'] for g in groups] != list(range(1,21)):
        raise ValueError('All twenty actual review groups required')
    for group in groups:
        expected, returned = group['expected_work_ids'], group['returned_work_ids']
        if (type(group['actual_exit_code']) is not int or group['actual_exit_code'] != 0
                or len(expected) != 5 or len(set(expected)) != 5
                or len(returned) != 5 or len(set(returned)) != 5):
            raise ValueError('Actual five-work review group differs')
        if group['group'] == 14:
            if (len(set(expected)-set(returned)) != 1 or len(set(returned)-set(expected)) != 1
                    or group['quality_adoption_review_saved'] is not False
                    or group['source_audit_saved'] is not False):
                raise ValueError('Only the original foreign-work group is quarantined')
        elif (set(expected) != set(returned) or group['quality_adoption_review_saved'] is not True
              or group['source_audit_saved'] is not True):
            raise ValueError('Another missing or invalid review cannot use this entry')
    if (len(whole) != 1 or whole[0]['group'] != 8 or whole[0]['qid'] != 'Q5466562'
            or whole[0]['status'] != 'held'
            or 'unresolved_version_scope_conflict' not in whole[0]['reason_codes']):
        raise ValueError('Original additional whole-work scope hold required')
    return {'unadopted_native_works':100,'mechanically_unreviewed_works':5,
            'separate_review_whole_holds':1,'conservative_failure_rate':0.06,
            'failed_window_accepted':False}


def verify_failed_window():
    """One bounded check of the saved failed window, without guard/model replay."""
    pins = {}
    diagnostic_path = pinned(BASE/'run-summaries'/f'{FAILED_NAME}.review-failure-diagnostic-v1.json',DIAGNOSTIC_SHA,pins)
    diagnostic = state.read(diagnostic_path)
    for path, expected in diagnostic['artifacts_sha256'].items():
        pinned(path,expected,pins)
    stop = state.read(pinned(BASE/'run-summaries'/f'{FAILED_NAME}.driver-stop-observation-v1.json',STOP_SHA,pins))
    source = BASE/'classification-sources-v2/window-0701-0800-v6'
    fixed = state.read(pinned(source/'manifest.json',SOURCE_SHA,pins))
    reservation = state.read(pinned(BASE/'attempt-reservations'/f'{FAILED_NAME}.json',RESERVATION_SHA,pins))
    terminal = RUNTIME/('terminal-'+FAILED_NAME)
    arguments = state.read(pinned(terminal/'adoption-arguments.json',ARGUMENTS_SHA,pins))
    native = state.read(pinned(Path(arguments['run_dir'])/'manifest.json',NATIVE_SHA,pins))
    native_exit = state.read(pinned(terminal/'process-exit.json',NATIVE_EXIT_SHA,pins))
    proof = state.read(pinned(terminal/'terminal-proof.json',TERMINAL_SHA,pins))
    if (proof['manifest_sha256'] != NATIVE_SHA or proof['parent_exit_observation_sha256'] != NATIVE_EXIT_SHA
            or type(arguments['exit_code']) is not int or arguments['exit_code'] != 0):
        raise ValueError('Original native terminal binding differs')
    chunk = fixed['chunks'][0]
    inputs = pinned(chunk['inputs_path'],chunk['inputs_sha256'],pins)
    pinned(arguments['ordered_inputs'],chunk['inputs_sha256'],pins)
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    works = [WorkInput.model_validate_json(line) for line in inputs.read_bytes().splitlines() if line.strip()]
    mapping = state.read(source/'work-mapping.json')
    hashes, qids = {model_hash(w) for w in works}, {row['wikidata_qid'] for row in mapping}
    if (reservation['global_index'] != 49 or reservation['run_name'] != FAILED_NAME
            or reservation['source_sha256'] != SOURCE_SHA or reservation['source_path'] != str(source.resolve())
            or len(works) != 100 or len(hashes) != 100 or len(qids) != 100
            or reservation['input_hashes'] != sorted(hashes) or reservation['qids'] != sorted(qids)
            or reservation['requirements_sha256'] != state.REQUIREMENTS_SHA
            or reservation['actual_overlap_count'] != 0):
        raise ValueError('Original complete attempted authority/hash reservation changed')
    review = BASE/'separate-review-sharded-v4'/FAILED_NAME
    plan = state.read(review/'review-plan.json')
    mapping_by_id = {row['work_id']:row['wikidata_qid'] for row in mapping}
    groups, whole = [], []
    for i, expected in enumerate(plan['groups'],1):
        directory = review/f'group-{i:02d}'
        observed = state.read(directory/'process-exit.json')
        exact_exit(observed,0)
        returned = state.read(directory/'review.final.json')['reviews']
        quality = directory/'quality-adoption-review.json'
        groups.append({'group':i,'actual_exit_code':observed['actual_exit_code'],
                       'expected_work_ids':expected,'returned_work_ids':[r['work_id'] for r in returned],
                       'quality_adoption_review_saved':quality.exists(),
                       'source_audit_saved':(directory/'source-audit.json').exists()})
        if quality.exists():
            for case in state.read(quality)['cases']:
                if case['status']=='held':
                    whole.append({'group':i,'qid':mapping_by_id[case['work_id']],**case})
    coordinator = BASE/'operational-processes'/FAILED_NAME/'native-and-review'
    failure = validate_failure_facts(native,native_exit,state.read(coordinator/'process-exit.json'),stop,groups,whole)
    if (groups != [{k:g[k] for k in groups[0]} for g in diagnostic['groups']]
            or whole != diagnostic['existing_whole_work_holds']
            or "ValueError: Review foreign work" not in (coordinator/'stderr.txt').read_text(encoding='utf-8')):
        raise ValueError('Saved failed review diagnosis differs from actual artifacts')
    for parent in ('classification-exports','classification-exports-v3','catalogue-imports'):
        if (BASE/parent/FAILED_NAME/'manifest.json').exists():
            raise ValueError('Failed window reached export/import; separate investigation required')
    if (BASE/'window-controls'/f'{FAILED_NAME}.json').exists():
        raise ValueError('Original failed coordinator cannot borrow a control')
    with sqlite3.connect(state.DATABASE.resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
        count = db.execute('SELECT count(*) FROM native_classifications WHERE qid IN ('+
                           ','.join('?' for _ in qids)+')',sorted(qids)).fetchone()[0]
    if count or state.eligible_count() != 5679:
        raise ValueError('Expected untouched failed works and actual cumulative 5679')
    pinned(BASE/'window-controls'/f'{PRIOR_NAME}.json',PRIOR_SHA,pins)
    return {'failure':failure,'diagnostic_path':str(diagnostic_path),'diagnostic_sha256':DIAGNOSTIC_SHA,
            'retained_attempted_qids':sorted(qids),'retained_attempted_hashes':sorted(hashes),
            'artifacts_sha256':pins,'old_native_or_review_resubmitted':False}


def verify_entry_source():
    overrides = BASE/'window-source-overrides-v3.json'
    source = Path(state.read(overrides)['49']).resolve(strict=True)
    if not source.is_relative_to((BASE/'classification-sources-v3').resolve()):
        raise ValueError('Fresh v7 wrapper must be in its new namespace')
    fixed = state.read(source/'manifest.json')
    if (type(fixed['window_index']) is not int or fixed['window_index'] != 49
            or type(fixed['selected']) is not int or fixed['selected'] != 100
            or len(fixed['chunks']) != 1
            or fixed['chunks'][0]['run_name'] != 'scale100k-content-0701-0800-v7'):
        raise ValueError('First fresh generation must retain logical slot 49 and select 100')
    # The unchanged reserve_window and mandatory preflight perform exact identity,
    # attempt exclusion, SourceRecord and UsageGuard authorization before any CLI.
    return {'source':str(source),'sha256':state.sha(source/'manifest.json'),
            'overrides_path':str(overrides),'overrides_sha256':state.sha(overrides)}


def assert_first_source(source):
    entry = state.read(BASE/'generation-v7-entry.json')['fresh_source']
    if (Path(source).resolve(strict=True) != Path(entry['source']).resolve(strict=True)
            or state.sha(Path(source)/'manifest.json') != entry['sha256']):
        raise ValueError('Fresh first source changed after the exclusive generation entry')


def verify_coordinator():
    pinned(ROOT/'scripts/run_scale100k_content_classification_v4.py',COORDINATOR_SHA,{})


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V3_SHA:
        raise ValueError('Frozen v19 operational driver-v3 changed')
    recipe = ModuleType('fixed_v19_driver_v3')
    recipe.__file__ = str(Path(__file__).with_name('run_scale100k_native_v3.py').resolve())
    exec(compile(raw,recipe.__file__,'exec'),recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_native_v2.py').read_bytes())
    changes = (
        ('scripts/run_scale100k_content_classification_v3.py',
         'scripts/run_scale100k_content_classification_v4.py'),
        ("'window-source-overrides-v2.json'","'window-source-overrides-v3.json'"),
        ("BASE / 'classification-sources-v2'","BASE / 'classification-sources-v3'"),
        ("    if prior['window_index'] != 42:","    if type(prior['window_index']) is not int or prior['window_index'] != 48:"),
        ('    cohort = 1\n',"    cohort = prior['window_index'] - 41\n    starting_cohort = cohort\n"),
        ('8 if cohort and clean','8 if cohort > starting_cohort and clean'),
        ('        reservation = state.reserve_window(source, global_index, index_path, expected_index_sha)',
         '        if cohort == starting_cohort:\n'
         '            assert_first_source(source)\n'
         '        verify_coordinator()\n'
         '        reservation = state.reserve_window(source, global_index, index_path, expected_index_sha)'),
        ("            'requirements_sha256': state.REQUIREMENTS_SHA, 'exclusion_index_sha256': expected_index_sha,",
         "            'generation_entry_receipt': str(BASE / 'generation-v7-entry.json'),\n"
         "            'generation_entry_receipt_sha256': state.sha(BASE / 'generation-v7-entry.json'),\n"
         "            'requirements_sha256': state.REQUIREMENTS_SHA, 'exclusion_index_sha256': expected_index_sha,"),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Fresh v7 operational adapter anchor differs: '+before)
        source = source.replace(before,after)
    return source


def load_operations():
    source = adapted_source(Path(__file__).with_name('run_scale100k_native_v3.py').read_bytes())
    scope = ModuleType('fresh_v19_generation_v7_operations')
    scope.__file__ = str(Path(__file__).resolve())
    scope.assert_first_source = assert_first_source
    scope.verify_coordinator = verify_coordinator
    exec(compile(source,scope.__file__,'exec'),scope.__dict__)
    return scope


def start(index_path,index_sha):
    state.verify_requirements(); state.load_exclusion_index(index_path,index_sha)
    verify_coordinator()
    failed = verify_failed_window()
    fresh = verify_entry_source()
    # Exclusive entry precedes any reservation/preflight/model. There is no resume
    # branch: all issued v6 authorities remain in their original reservation.
    state.save(BASE/'generation-v7-entry.json',{'version':'scale100k-fresh-generation-entry-v1',
        'driver_path':str(Path(__file__).resolve()),'driver_sha256':state.sha(__file__),
        'coordinator_path':str(ROOT/'scripts/run_scale100k_content_classification_v4.py'),
        'coordinator_sha256':COORDINATOR_SHA,
        'requirements_sha256':state.REQUIREMENTS_SHA,'exclusion_index_sha256':index_sha,
        'prior_control':str(BASE/'window-controls'/f'{PRIOR_NAME}.json'),'prior_control_sha256':PRIOR_SHA,
        'failed_window':failed,'fresh_source':fresh,'first_concurrency':4,
        'prior_failed_window_accepted':False,'new_model_calls_at_entry':0})
    load_operations().run(index_path,index_sha,BASE/'window-controls'/f'{PRIOR_NAME}.json',PRIOR_SHA)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exclusion-index',type=Path,required=True)
    parser.add_argument('--exclusion-index-sha256',required=True)
    args = parser.parse_args()
    start(args.exclusion_index,args.exclusion_index_sha256)


if __name__=='__main__':
    main()
