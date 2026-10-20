"""Execute a new native window with durable prior-gate replay receipts.

No historical result is retried or rewritten. Subsequent windows require every
earlier window's actual validation and strict feature-adoption gate to pass.
"""
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


def review_initial_window(runtime_preparation, observed, run_name='scale-0001-0100'):
    """One genuine separate v2 model review; same first-window aggregate budget."""
    manifest = json.loads((Path(observed['run_dir']) / 'manifest.json').read_bytes())
    tokens = sum(value for key, value in manifest['usage'].items() if key in ('input_tokens', 'output_tokens') and isinstance(value, (int, float)))
    remaining_seconds = 3600 - manifest['elapsed_seconds']
    if remaining_seconds <= 0 or tokens >= 2000000 or manifest.get('stop_reasons'):
        raise ValueError('Initial classification exhausted its actual window budget; retain results and hold separate review')
    output = ROOT / 'data/autonomous-next-v3/scale-v1/separate-review' / run_name
    arguments = [runtime_preparation / 'runtime', observed['ordered_inputs'], observed['run_dir'],
        observed['execution_context_manifest'], observed['terminal_proof'], observed['usage_ledger'],
        sha(observed['usage_ledger']), output, min(900, max(1, int(remaining_seconds)))]
    code = ('import json,sys; from pathlib import Path; from types import SimpleNamespace; '
            'sys.path.insert(0,str(Path.cwd()/"scripts")); from review_scale_classification_v1 import execute; '
            'k=dict(zip(("runtime_root","ordered_inputs","run_dir","execution_context_manifest","terminal_proof","ledger"),map(Path,sys.argv[1:7]))); '
            'k.update(ledger_sha256=sys.argv[7],output=Path(sys.argv[8]),timeout=int(sys.argv[9])); '
            'print(json.dumps(execute(SimpleNamespace(**k)),ensure_ascii=True))')
    started = time.monotonic()
    result = frozen_call(runtime_preparation, code, arguments)
    result['elapsed_seconds'] = time.monotonic() - started
    return result


def execute_window(source_preparation, runtime_preparation, chunk_index, *, previous_exports=()):
    source_preparation = Path(source_preparation).resolve(strict=True)
    runtime_preparation = Path(runtime_preparation).resolve(strict=True)
    source = json.loads((source_preparation / 'manifest.json').read_bytes())
    if source.get('version') != 'scale-classification-source-v1' or source.get('all_six_purpose_admissions_allowed') is not True:
        raise ValueError('New fixed source pool has not passed six-purpose admission')
    if type(chunk_index) is not int or not 0 <= chunk_index < len(source['chunks']):
        raise ValueError('Unknown fixed chunk index')
    if len(previous_exports) != chunk_index:
        raise ValueError('Each earlier fixed window requires one independent verified export gate')
    from recommend_book.scale_gate_replay_v1 import verify_prior_export
    prior_gate_replays = []
    for index, export in enumerate(previous_exports):
        path = Path(export).resolve(strict=True)
        previous = json.loads(path.read_bytes())
        prior_gate_replays.append(verify_prior_export(path, sha(path),
            ROOT / "data/autonomous-next-v3/scale-v1/gate-replay-cache-v1"))
        if previous['chunk_index'] != index or previous['source_preparation_sha256'] != sha(source_preparation / 'manifest.json'):
            raise ValueError('Previous export does not bind the same ordered fixed pool')
        actual = json.loads(Path(previous['run_manifest']).read_bytes())
        if not previous.get('gate', {}).get('passed') or not check_gate(actual, previous['verified_projection'])['passed']:
            raise ValueError('Previous actual native window failed expansion gate')
    chunk = source['chunks'][chunk_index]
    if sha(chunk['inputs_path']) != chunk['inputs_sha256'] or sha(source['source_database']) != source['source_database_sha256']:
        raise ValueError('New fixed source or selected chunk changed')
    preflight_path = runtime_preparation / ('preflight.' + chunk['run_name'] + '.json')
    preflight = json.loads(preflight_path.read_bytes())
    controls = preflight.get('controls', {})
    concurrency = controls.get('concurrency')
    if concurrency not in ((2,) if chunk_index == 0 else (2, 4)) or controls != {
            'batch_size': 5, 'concurrency': concurrency, 'timeout': 900, 'max_run_seconds': 3600, 'max_observed_tokens': 2000000}:
        raise ValueError('Scale window requires frozen 5-work/1h/2m controls; four slots only after actual first-stage gates')
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
    if observed['exit_code']:
        return observed
    output = ROOT / 'data/autonomous-next-v3/scale-v1/classified' / chunk['run_name']
    kwargs = {key: observed[key] for key in ('source_database', 'ordered_inputs', 'run_dir',
              'execution_context_manifest', 'migration_manifest', 'terminal_proof', 'cli_profile')}
    kwargs.update(output_dir=str(output), usage_ledger=observed['usage_ledger'])
    separate_review = None
    actual_results = [json.loads(line) for line in (Path(observed['run_dir']) / 'results.jsonl').read_bytes().splitlines() if line.strip()]
    has_non_genre_candidates = any(label['decision'] == 'present' and label['axis'] != 'genre'
                                   for result in actual_results for label in result['labels'])
    if chunk_index == 0 or has_non_genre_candidates:
        separate_review = review_initial_window(runtime_preparation, observed, chunk['run_name'])
        kwargs['quality_adoption_review'] = separate_review['review_path']
    code = ('import json,sys; from recommend_book.next_database_v4 import import_next_classifications; '
            'from recommend_book.usage_guard_v4 import UsageGuard; '
            'k=json.loads(sys.argv[1]); k["guard"]=UsageGuard(k.pop("usage_ledger")); '
            'print(json.dumps(import_next_classifications(**k),ensure_ascii=True))')
    adopted = frozen_call(runtime_preparation, code, [json.dumps(kwargs)])
    mapping = source_preparation / 'work-mapping.json'
    code = ('import json,sys; from recommend_book.scale_classification_v1 import authenticated_rows; '
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
            Path(observed['execution_context_manifest']), runtime_preparation / 'source-snapshot-manifest.json']
    for replay in prior_gate_replays:
        receipt_path = Path(replay['receipt_path'])
        if sha(receipt_path) != replay['receipt_sha256']:
            raise ValueError('Prior actual gate replay receipt changed during window')
        receipt = json.loads(receipt_path.read_bytes())
        for path, expected in receipt['protected_artifacts'].items():
            if sha(path) != expected:
                raise ValueError('Prior actual gate dependency changed during window: ' + path)
        pins.extend([receipt_path, *map(Path, replay['replay_artifacts']),
                     *map(Path, receipt['protected_artifacts'])])
    if separate_review:
        review = Path(separate_review['review_path'])
        document = json.loads(review.read_bytes())
        pins.extend([review, *[Path(item['path']) for item in document['source_audits']]])
        audit = json.loads(review.with_name('source-audit.json').read_bytes())
        pins.extend(review.parent / name for name in audit['artifact_sha256'])
    report = {'version': 'scale-native-classification-export-v1', 'chunk_index': chunk_index,
              'runtime_preparation': str(runtime_preparation), 'source_preparation': str(source_preparation),
              'source_preparation_sha256': sha(source_preparation / 'manifest.json'),
              'classified_database': str(output / 'dataset.sqlite'), 'usage_ledger': observed['usage_ledger'],
              'work_mapping': str(mapping), 'run_manifest': str(run_manifest_path),
              'artifact_sha256': {**projection['artifact_sha256'], **{str(p.resolve()): sha(p) for p in pins}},
              'verified_projection': projection, 'gate': gate,
              'native_adoption_report': adopted, 'old_classifications_modified': False,
              'separate_review': separate_review, 'prior_gate_replays': prior_gate_replays,
              'coordinator_sha256': sha(__file__), 'model': 'gpt-6.1-sol', 'execution_spec': 'provenance-v4'}
    export_path = ROOT / 'data/autonomous-next-v3/scale-v1/classification-exports' / chunk['run_name'] / 'manifest.json'
    save(export_path, report)
    return {'stage': 'actual_native_export_verified', 'export_manifest': str(export_path),
            'export_manifest_sha256': sha(export_path), 'gate': gate,
            'selected': run_manifest['selected'], 'completed': run_manifest['completed'],
            'cli_invocations': run_manifest['cli_invocations'], 'usage': run_manifest['usage']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-preparation', type=Path, required=True)
    parser.add_argument('--runtime-preparation', type=Path, required=True)
    parser.add_argument('--chunk-index', type=int, required=True)
    parser.add_argument('--previous-export', type=Path, action='append', default=[])
    args = parser.parse_args()
    result = execute_window(args.source_preparation, args.runtime_preparation, args.chunk_index,
                            previous_exports=args.previous_export)
    print(json.dumps(result, ensure_ascii=True))
    return result.get('exit_code', 0)


if __name__ == '__main__':
    raise SystemExit(main())
