"""Classify one bounded source-admitted series window without automatic retries."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from recommend_book.light_novel_classification_v1 import SeriesInput, run_window, freeze_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reservations", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=(4, 8), default=4)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--executable")
    parser.add_argument("--frozen", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.frozen:
        args.output.mkdir(parents=True, exist_ok=False)
        runtime = freeze_runtime(args.output / "runtime")
        argv = [sys.executable, str(runtime / "scripts/classify_light_novel_series_v1.py"), "--frozen",
            "--inputs", str(args.inputs.resolve()), "--source-manifest", str(args.source_manifest.resolve()),
            "--source-sha256", args.source_sha256, "--output", str((args.output / "run").resolve()),
            "--reservations", str(args.reservations.resolve()), "--concurrency", str(args.concurrency), "--timeout", str(args.timeout)]
        if args.executable:
            argv.extend(("--executable", args.executable))
        env = dict(os.environ, PYTHONPATH=str(runtime / "src"), PYTHONDONTWRITEBYTECODE="1")
        with subprocess.Popen(argv, env=env, cwd=runtime, shell=False) as child:
            raise SystemExit(child.wait())
    inputs = [SeriesInput.model_validate_json(line) for line in args.inputs.read_bytes().splitlines() if line.strip()]
    print(json.dumps(run_window(inputs, args.output, source_manifest=args.source_manifest,
        source_sha256=args.source_sha256, reservations=args.reservations,
        concurrency=args.concurrency, timeout=args.timeout, executable=args.executable), ensure_ascii=False))


if __name__ == "__main__":
    main()
