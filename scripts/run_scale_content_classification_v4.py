"""Run one actual content-native window with bounded compact continuation control."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def check_gate(run_manifest, projection):
    selected = run_manifest['selected']
    if type(selected) is not int or not 1 <= selected <= 100:
        raise ValueError('Gate requires one bounded actual native window')
    failed = run_manifest['failed']
    pending = run_manifest['pending']
    adopted = projection['actual_feature_adopted_works']
    if any(type(value) is not int or value < 0 for value in (failed, pending, adopted)) or adopted > selected:
        raise ValueError('Invalid actual gate counts')
    reasons = []
    if pending:
        reasons.append('pending_native_jobs')
    if failed / selected > .05:
        reasons.append('validation_failure_above_5_percent')
    if adopted / selected < .70:
        reasons.append('strict_actual_feature_adoption_below_70_percent')
    if run_manifest.get('stopped') or run_manifest.get('stop_reasons'):
        reasons.append('native_stop_or_quota_signal')
    return {'passed': not reasons, 'selected': selected, 'failed': failed, 'pending': pending,
            'actual_feature_adopted_works': adopted, 'validation_failure_rate': failed / selected,
            'actual_feature_adoption_rate': adopted / selected, 'reasons': reasons,
            'human_quality_verified': False}


def frozen_call(runtime_preparation, code, arguments):
    runtime = Path(runtime_preparation) / 'runtime'
    env = dict(os.environ, PYTHONPATH=str(runtime / 'src'), PYTHONDONTWRITEBYTECODE='1')
    for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_API_BASE'):
        env.pop(name, None)
    code = 'from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); ' + code
    process = subprocess.run([sys.executable, '-c', code, *map(str, arguments)], env=env,
                             cwd=ROOT, capture_output=True, shell=False, timeout=1800)
    if process.returncode:
        raise ValueError('Frozen native adoption/verification failed: ' + process.stderr.decode('utf-8', 'replace')[-3000:])
    return json.loads(process.stdout)


def review_content_window(runtime_preparation, observed, run_name, *, full):
    manifest = json.loads((Path(observed['run_dir']) / 'manifest.json').read_bytes())
    tokens = sum(manifest['usage'].get(key, 0) for key in ('input_tokens', 'output_tokens'))
    remaining_seconds = 3600 - manifest['elapsed_seconds']
    if remaining_seconds <= 0 or tokens >= 2000000 or manifest.get('stop_reasons'):
        raise ValueError('Actual content window budget exhausted; keep outputs without retry')
    output = ROOT / 'data/autonomous-next-v3/scale-30000-v1/separate-review-sharded-v1' / run_name
    selection_path = output.with_name(run_name + '.selection.json')
    code = ('import json,sys; from pathlib import Path; '
            'from recommend_book.models import WorkInput; '
            'from recommend_book.scale_content_classification_v1 import select_review_work_ids; '
            'works=[WorkInput.model_validate_json(l) for l in Path(sys.argv[1]).read_bytes().splitlines() if l.strip()]; '
            'results=[json.loads(p.read_bytes()) for p in (Path(sys.argv[2])/"results").glob("*.json")]; '
            'print(json.dumps(select_review_work_ids(works,results,full=sys.argv[3]=="full")))')
    selected = frozen_call(runtime_preparation, code, [observed['ordered_inputs'], observed['run_dir'], 'full' if full else 'semantic_and_audit'])
    save(selection_path, {'selected_work_ids': selected, 'full': full,
        'completed_native_count': manifest['completed'],
        'rule': 'all first100; thereafter every non-genre/content-only candidate and deterministic genre-only audit'})
    arguments = [runtime_preparation / 'runtime', observed['ordered_inputs'], observed['run_dir'],
        observed['execution_context_manifest'], observed['terminal_proof'], observed['usage_ledger'],
        sha(observed['usage_ledger']), output, min(900, max(1, int(remaining_seconds))), selection_path]
    code = ('import json,sys; from pathlib import Path; from types import SimpleNamespace; '
            'sys.path.insert(0,str(Path.cwd()/"scripts")); from review_scale_content_v3 import execute; '
            'k=dict(zip(("runtime_root","ordered_inputs","run_dir","execution_context_manifest","terminal_proof","ledger"),map(Path,sys.argv[1:7]))); '
            'k.update(ledger_sha256=sys.argv[7],output=Path(sys.argv[8]),timeout=int(sys.argv[9]),selection_path=Path(sys.argv[10])); '
            'print(json.dumps(execute(SimpleNamespace(**k)),ensure_ascii=True))')
    started = time.monotonic()
    result = frozen_call(runtime_preparation, code, arguments)
    result.update(elapsed_seconds=time.monotonic()-started, selection_path=str(selection_path),
                  selection_sha256=sha(selection_path), reviewed_work_count=len(selected), full=full)
    return result


def execute_window(source_preparation, runtime_preparation, route_index, *, control_receipt=None, control_receipt_sha256=None):
    source_preparation = Path(source_preparation).resolve(strict=True)
    runtime_preparation = Path(runtime_preparation).resolve(strict=True)
    source = json.loads((source_preparation / 'manifest.json').read_bytes())
    if source.get('version') != 'scale-content-classification-source-v1' or source.get('required_purpose_admissions_allowed') is not True:
        raise ValueError('New content window has not passed actual required-purpose admission')
    if type(route_index) is not int or route_index < 0 or source['window_index'] != route_index or len(source['chunks']) != 1:
        raise ValueError('One exact current content window required')
    from recommend_book.scale_content_control_v2 import verify_control_receipt
    from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
    prior_control = None
    if route_index:
        if control_receipt is None or control_receipt_sha256 is None:
            raise ValueError('Prior authentic accepted content gate required')
        prior_control = verify_control_receipt(control_receipt, control_receipt_sha256)
        if prior_control['window_index'] != route_index - 1:
            raise ValueError('Content continuation window order differs')
    elif control_receipt is not None:
        raise ValueError('Initial content route cannot borrow another route gate')
    chunk = source['chunks'][0]
    if sha(chunk['inputs_path']) != chunk['inputs_sha256'] or sha(source['source_database']) != source['source_database_sha256']:
        raise ValueError('New fixed source or selected chunk changed')
    preflight_path = runtime_preparation / ('preflight.' + chunk['run_name'] + '.json')
    preflight = json.loads(preflight_path.read_bytes())
    validation_path = runtime_preparation / ('preflight.' + chunk['run_name'] + '.frozen-validation.json')
    validation = json.loads(validation_path.read_bytes())
    if (validation.get('actual_preflight_exit_code') != 0
            or validation.get('actual_preflight_sha256') != sha(preflight_path)
            or validation.get('adapter_sha256') != sha(validation['adapter_path'])
            or validation.get('snapshot_sha256') != sha(runtime_preparation / 'source-snapshot-manifest.json')
            or validation.get('unchanged_chunk_preparer_sha256') != sha(ROOT / 'scripts/prepare_scale_classification_v1.py')):
        raise ValueError('Exact frozen-only content preflight validation differs')
    controls = preflight.get('controls', {})
    concurrency = controls.get('concurrency')
    if concurrency not in ((2,) if route_index == 0 else ((2, 4) if route_index == 1 else (2, 4, 8))) or controls != {
            'batch_size': 5, 'concurrency': concurrency, 'timeout': 900, 'max_run_seconds': 3600, 'max_observed_tokens': 2000000}:
        raise ValueError('Scale window requires frozen 5-work/1h/2m controls; four slots only after actual first-stage gates')
    if concurrency == 8:
        from prepare_scale_content_chunk_v2 import trial_control
        trial_control(control_receipt, control_receipt_sha256)
        if (validation.get('prior_actual_control_receipt') != str(Path(control_receipt).resolve())
                or validation.get('prior_actual_control_sha256') != control_receipt_sha256):
            raise ValueError('Eight-slot preflight differs from actual prior gate')
    if sha(preflight['ordered_inputs_path']) != chunk['inputs_sha256']:
        raise ValueError('Actual preflight input differs from fixed source chunk')
    if preflight['source_ledger_path'] != chunk['usage_ledger_path'] or preflight['source_ledger_sha256'] != chunk['usage_ledger_sha256']:
        raise ValueError('Actual preflight ledger differs from fixed window admission')
    # Reuse the parent PID/exit observer with only its fixed child launcher
    # changed. The request pins this coordinator containing the exact transform.
    terminal_source = (ROOT / 'scripts/native_snapshot_terminal_v1.py').read_text(encoding='utf-8')
    anchor = "runtime/'scripts/run_guarded_classification_v4.py'"
    if terminal_source.count(anchor) != 1:
        raise ValueError('Actual terminal launcher anchor changed')
    terminal_source = terminal_source.replace(anchor, "runtime/'scripts/run_scale_guarded_runtime_v1.py'")
    terminal = ModuleType('scale_actual_terminal')
    terminal.__file__ = str(Path(__file__).resolve())
    exec(compile(terminal_source, terminal.__file__, 'exec'), terminal.__dict__)
    observed = terminal.execute(runtime_preparation, chunk['run_name'], source['source_database'])
    if (sha(validation['adapter_path']) != validation['adapter_sha256']
            or sha(runtime_preparation / 'source-snapshot-manifest.json') != validation['snapshot_sha256']):
        raise ValueError('Exact content preflight adapter/runtime changed during native execution')
    if observed['exit_code']:
        return observed
    output = ROOT / 'data/autonomous-next-v3/scale-30000-v1/classified' / chunk['run_name']
    kwargs = {key: observed[key] for key in ('source_database', 'ordered_inputs', 'run_dir',
              'execution_context_manifest', 'migration_manifest', 'terminal_proof', 'cli_profile')}
    kwargs.update(output_dir=str(output), usage_ledger=observed['usage_ledger'])
    # Every recovery window gets genuine complete-input/native-output review.
    separate_review = review_content_window(runtime_preparation, observed, chunk['run_name'], full=(route_index == 0 or prior_control['next_review_mode'] == 'full'))
    kwargs['quality_adoption_review'] = separate_review['review_path']
    code = ('import json,sys; from recommend_book.next_database_v4 import import_next_classifications; '
            'from recommend_book.usage_guard_v4 import UsageGuard; '
            'k=json.loads(sys.argv[1]); k["guard"]=UsageGuard(k.pop("usage_ledger")); '
            'print(json.dumps(import_next_classifications(**k),ensure_ascii=True))')
    adopted = frozen_call(runtime_preparation, code, [json.dumps(kwargs)])
    mapping = source_preparation / 'work-mapping.json'
    code = ('import json,sys; from recommend_book.scale_content_classification_v1 import authenticated_rows; '
            'print(json.dumps(authenticated_rows(*sys.argv[1:]),ensure_ascii=True,sort_keys=True,allow_nan=False))')
    projection = frozen_call(runtime_preparation, code, [output / 'dataset.sqlite', observed['usage_ledger'], mapping])
    run_manifest_path = Path(observed['run_dir']) / 'manifest.json'
    run_manifest = json.loads(run_manifest_path.read_bytes())
    gate = check_gate(run_manifest, projection)
    if separate_review:
        combined_tokens = {key: run_manifest['usage'].get(key, 0) + separate_review['observed_usage'].get(key, 0)
                           for key in set(run_manifest['usage']) | set(separate_review['observed_usage'])}
        gate['classification_and_separate_review_observed_usage'] = combined_tokens
        if sum(combined_tokens.get(key, 0) for key in ('input_tokens', 'output_tokens')) > 2000000:
            gate['passed'] = False
            gate['reasons'].append('initial_combined_observed_token_budget_exceeded')
        gate['classification_and_separate_review_elapsed_seconds'] = run_manifest['elapsed_seconds'] + separate_review['elapsed_seconds']
        if gate['classification_and_separate_review_elapsed_seconds'] > 3600:
            gate['passed'] = False
            gate['reasons'].append('initial_combined_time_budget_exceeded')
    pins = [output / 'dataset.sqlite', Path(observed['usage_ledger']), mapping,
            source_preparation / 'manifest.json', run_manifest_path, Path(observed['terminal_proof']),
            Path(observed['execution_context_manifest']), runtime_preparation / 'source-snapshot-manifest.json',
            preflight_path, validation_path, Path(validation['adapter_path']),
            Path(verify_control_receipt.__code__.co_filename)]
    current_chain = coordinator_chain_pins({
        'runtime_preparation': str(runtime_preparation), 'run_manifest': str(run_manifest_path),
        'coordinator_sha256': sha(__file__),
        'artifact_sha256': {str(run_manifest_path): sha(run_manifest_path),
                            str(Path(observed['terminal_proof'])): sha(observed['terminal_proof'])}})
    pins.extend([Path(__file__).resolve(), *[Path(item['path']) for item in current_chain]])
    if prior_control:
        # A compact process-control receipt is not old-label/source permission.
        if sha(control_receipt) != control_receipt_sha256:
            raise ValueError('Content control receipt changed during window')
        pins.append(Path(control_receipt))
    if separate_review:
        review = Path(separate_review['review_path'])
        document = json.loads(review.read_bytes())
        pins.extend([review, *[Path(item['path']) for item in document['source_audits']]])
        for audit_binding in document['source_audits']:
            audit_path = Path(audit_binding['path'])
            audit = json.loads(audit_path.read_bytes())
            pins.extend(audit_path.parent / name for name in audit['artifact_sha256'])
    report = {'version': 'scale-content-native-classification-export-v1', 'window_index': route_index,
              'runtime_preparation': str(runtime_preparation), 'source_preparation': str(source_preparation),
              'source_preparation_sha256': sha(source_preparation / 'manifest.json'),
              'classified_database': str(output / 'dataset.sqlite'), 'usage_ledger': observed['usage_ledger'],
              'work_mapping': str(mapping), 'run_manifest': str(run_manifest_path),
              'artifact_sha256': {**projection['artifact_sha256'], **{str(p.resolve()): sha(p) for p in pins}},
              'verified_projection': projection, 'gate': gate,
              'native_adoption_report': adopted, 'old_classifications_modified': False,
              'separate_review': separate_review, 'prior_control_receipt': str(control_receipt) if control_receipt else None,
              'coordinator_sha256': sha(__file__), 'model': 'gpt-6.1-sol', 'execution_spec': 'provenance-v4'}
    export_path = ROOT / 'data/autonomous-next-v3/scale-30000-v1/classification-exports' / chunk['run_name'] / 'manifest.json'
    save(export_path, report)
    return {'stage': 'actual_native_export_verified', 'export_manifest': str(export_path),
            'export_manifest_sha256': sha(export_path), 'gate': gate,
            'selected': run_manifest['selected'], 'completed': run_manifest['completed'],
            'cli_invocations': run_manifest['cli_invocations'], 'usage': run_manifest['usage']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-preparation', type=Path, required=True)
    parser.add_argument('--runtime-preparation', type=Path, required=True)
    parser.add_argument('--route-index', type=int, required=True)
    parser.add_argument('--control-receipt', type=Path)
    parser.add_argument('--control-receipt-sha256')
    args = parser.parse_args()
    print(json.dumps(execute_window(args.source_preparation, args.runtime_preparation,
        args.route_index, control_receipt=args.control_receipt,
        control_receipt_sha256=args.control_receipt_sha256), ensure_ascii=True))


if __name__ == '__main__':
    main()
