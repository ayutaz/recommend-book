"""Run the one-shot v2 review recipe with explicit shared-batch provenance rules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import review_world_pilot_v1 as v1


INSTRUCTIONS = v1.INSTRUCTIONS + '''
When multiple works share one content-addressed raw_path and raw_sha256, do not
treat that reuse alone as a wrong-subject conflict. Distinguish each work by its
source page identity, page_id, revision_id, section, locator, selected-text
digest, and identity proof. Use unresolved_version_scope_conflict only when
those bound fields or the quoted evidence actually conflict; otherwise review
the work-specific page and selected text independently.
'''
PROTOCOL = 'complete-input-native-output-internal-review-v2'


def execute(args):
    return v1.execute(args, instructions=INSTRUCTIONS,
                      extra_source_paths=(Path(__file__),), protocol=PROTOCOL)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('runtime-root', 'ordered-inputs', 'run-dir',
                 'execution-context-manifest', 'terminal-proof', 'ledger', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--ledger-sha256', required=True)
    parser.add_argument('--timeout', type=int, default=900)
    print(json.dumps(execute(parser.parse_args()), ensure_ascii=False))
