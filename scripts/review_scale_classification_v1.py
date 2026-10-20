"""Reuse the genuine v2 complete-native-output review for the first scale window.

The sole new scope rule reviews every terminal-completed input when a few other
selected jobs were held. Held jobs have no native labels to review. Original
selected inputs, actual terminal proof and complete completed results stay bound.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def load_review():
    path = ROOT / 'scripts/review_world_pilot_v1.py'
    raw = path.read_bytes()
    before = '    if len(records) != len(works) or not records:\n'
    source = raw.decode('utf-8').replace('\r\n', '\n')
    if source.count(before) != 1:
        raise ValueError('Historical review scope anchor drift; refuse new model request')
    source = source.replace(before, "    if len(records) != manifest['completed'] or not records:\n")
    result = ModuleType('scale_completed_native_review')
    result.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), result.__dict__)
    return result


def execute(args):
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    import review_world_pilot_v2 as v2
    return load_review().execute(args, instructions=v2.INSTRUCTIONS,
        extra_source_paths=(Path(v2.__file__), Path(__file__)), protocol=v2.PROTOCOL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('runtime-root', 'ordered-inputs', 'run-dir', 'execution-context-manifest', 'terminal-proof', 'ledger', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--ledger-sha256', required=True)
    parser.add_argument('--timeout', type=int, default=900)
    print(json.dumps(execute(parser.parse_args()), ensure_ascii=True))


if __name__ == '__main__':
    main()
