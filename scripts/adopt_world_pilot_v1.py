"""Adopt one observed real pilot run into a new immutable supplement."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def adopt(args):
    runtime = args.runtime_root.resolve(strict=True)
    terminal = args.terminal_dir.resolve(strict=True)
    arguments = json.loads((terminal / 'adoption-arguments.json').read_bytes())
    proof = json.loads((terminal / 'terminal-proof.json').read_bytes())
    observed = json.loads((terminal / 'process-exit.json').read_bytes())
    if (arguments['stage'] != 'actual_run_observed' or arguments['exit_code'] != 0
            or arguments['failed'] or arguments['pending']
            or arguments['completed'] != arguments['selected']
            or observed['actual_exit_code'] != 0
            or observed['process_exit_observed'] is not True
            or observed['process_pid'] != arguments['process_pid']
            or proof['process_pid'] != observed['process_pid']
            or proof['parent_request_sha256'] != sha(terminal / 'request.json')
            or proof['parent_exit_observation_sha256'] != sha(terminal / 'process-exit.json')):
        raise ValueError('Actual terminal process or complete-run proof differs')
    sys.path.insert(0, str(runtime / 'src'))
    from recommend_book.next_database_v4 import import_next_classifications
    from recommend_book.usage_guard_v4 import UsageGuard
    guard = UsageGuard(arguments['usage_ledger'], expected_ledger_sha256=args.ledger_sha256)
    names = ('source_database', 'ordered_inputs', 'run_dir', 'execution_context_manifest',
             'migration_manifest', 'terminal_proof', 'cli_profile')
    started = time.perf_counter()
    report = import_next_classifications(**{key: arguments[key] for key in names},
        output_dir=args.output, guard=guard, quality_adoption_review=args.review)
    observation = {'version': 'world-pilot-adoption-observation-v1',
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': time.perf_counter() - started,
        'terminal_arguments_sha256': sha(terminal / 'adoption-arguments.json'),
        'review_sha256': sha(args.review), 'coordinator_sha256': sha(__file__),
        'database_sha256': sha(args.output / 'dataset.sqlite'),
        'new_model_calls': 0, 'native_validation_scopes': guard.native_scopes.reports,
        'adoption_report': report}
    with (args.output / 'adoption-observation.json').open('x', encoding='utf-8') as stream:
        json.dump(observation, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return observation


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('runtime-root', 'terminal-dir', 'review', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--ledger-sha256', required=True)
    print(json.dumps(adopt(parser.parse_args()), ensure_ascii=False))
