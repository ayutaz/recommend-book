"""Frozen scale CLI launcher; exact-byte parse reuse never caches admission."""
import importlib.util
from pathlib import Path
import sys

from recommend_book.classification.validation import ROOT
from recommend_book.scale_guard_context_v1 import guard_json_cache


def main():
    path = ROOT / 'scripts/run_guarded_classification_v4.py'
    spec = importlib.util.spec_from_file_location('frozen_scale_guarded_cli', path)
    helper = importlib.util.module_from_spec(spec)
    with guard_json_cache():
        spec.loader.exec_module(helper)
        return helper.main(sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())
