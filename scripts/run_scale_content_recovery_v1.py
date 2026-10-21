"""Run the one reserved window-24 recovery at two slots with full review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
ORDINARY_COORDINATOR_SHA = '454f157102a2ce398a76052bd8daca4fcb3ed7fd08360230c601f3c5fe36cea2'


def recovery_source(raw):
    if hashlib.sha256(raw).hexdigest() != ORDINARY_COORDINATOR_SHA:
        raise ValueError('Frozen ordinary content coordinator changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = {
        '    from recommend_book.scale_content_control_v4 import verify_control_receipt':
        '    from recommend_book.scale_content_recovery_v1 import verify_recovery_checkpoint as verify_control_receipt, recovery_artifact_paths',
        "full=(route_index == 0 or prior_control['next_review_mode'] == 'full')": 'full=True',
        '        pins.append(Path(control_receipt))':
        '        pins.extend(recovery_artifact_paths(control_receipt))',
        "'old_classifications_modified': False,":
        "'old_classifications_modified': False, 'recovery_checkpoint': str(control_receipt), 'recovery_kind': 'single_quarantined_failed_window_checkpoint',",
    }
    for before, after in changes.items():
        if source.count(before) != 1:
            raise ValueError('Frozen recovery coordinator anchor drift: ' + before)
        source = source.replace(before, after)
    return source


def load_recovery_coordinator():
    path = ROOT / 'scripts/run_scale_content_classification_v6.py'
    source = recovery_source(path.read_bytes())
    result = ModuleType('single_content_recovery_coordinator')
    result.__file__ = str(Path(__file__).resolve())
    exec(compile(source, result.__file__, 'exec'), result.__dict__)
    return result


def execute(checkpoint, expected_sha256):
    from recommend_book.scale_content_recovery_v1 import enter_recovery_process, RUN_NAME
    document = enter_recovery_process(checkpoint, expected_sha256)
    sys.path.insert(0, str(ROOT / 'scripts'))
    from prepare_scale_content_chunk_v4 import prepare
    source = json.loads((Path(document['source_preparation']) / 'manifest.json').read_bytes())
    chunk = source['chunks'][0]
    # This is a new preflight, never reuse a partial preflight or a native run.
    prepare(document['runtime_preparation'], chunk['inputs_path'], chunk['usage_ledger_path'], RUN_NAME, 2)
    preflight = json.loads((Path(document['runtime_preparation']) /
                           ('preflight.' + RUN_NAME + '.json')).read_bytes())
    if preflight['controls'] != {'batch_size': 5, 'concurrency': 2, 'timeout': 900,
                                 'max_run_seconds': 3600, 'max_observed_tokens': 2000000}:
        raise ValueError('Recovery preflight changed the fixed bounded controls')
    return load_recovery_coordinator().execute_window(document['source_preparation'],
        document['runtime_preparation'], 23, control_receipt=checkpoint,
        control_receipt_sha256=expected_sha256)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(execute(args.checkpoint, args.checkpoint_sha256), ensure_ascii=True))


if __name__ == '__main__':
    main()
