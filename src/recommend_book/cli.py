"""Explicit, resumable commands for the four dataset stages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable

from .models import SourceRecord, WorkInput


def read_jsonl(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error


def output(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Collect, resolve, classify, and evaluate a worldwide fiction dataset.")
    commands = root.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="Ingest provenance-preserving source JSONL into a work database")
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--database", type=Path, default=Path("data/dataset.sqlite"))
    for name in ("stats", "snapshot", "review-queue"):
        command = commands.add_parser(name)
        command.add_argument("--database", type=Path, default=Path("data/dataset.sqlite"))
        if name == "snapshot":
            command.add_argument("--output", type=Path, required=True)
    classify = commands.add_parser("classify", help="Run parallel Sol CLI workers with evidence-grounded structured outputs")
    classify.add_argument("--database", type=Path, default=Path("data/dataset.sqlite"))
    classify.add_argument("--input", type=Path, help="Optional fixed WorkInput JSONL snapshot")
    classify.add_argument("--output", type=Path, required=True)
    classify.add_argument("--concurrency", type=int, default=2)
    classify.add_argument("--model", default="gpt-6.1-sol")
    classify.add_argument("--executable")
    classify.add_argument("--limit", type=int)
    classify.add_argument("--batch-size", type=int, default=1, help="Works per CLI invocation (1-10)")
    classify.add_argument("--timeout", type=float, default=900, help="Maximum seconds per CLI invocation")
    classify.add_argument("--max-run-seconds", type=float, help="Stop new submissions after elapsed seconds; in-flight jobs finish")
    classify.add_argument("--max-observed-tokens", type=int, help="Stop new submissions after CLI-observed input + output tokens")
    classify.add_argument("--stop-on-rate-limit", action="store_true", help="Preserve pending jobs and stop new submissions at first rate limit")
    review = commands.add_parser("review", help="Build a local human annotation pack")
    review.add_argument("--database", type=Path, default=Path("data/dataset.sqlite"))
    review.add_argument("--results", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--limit", type=int)
    evaluate = commands.add_parser("evaluate", help="Evaluate against independently sourced, provenance-labelled annotations")
    evaluate.add_argument("--results", type=Path, required=True)
    evaluate.add_argument("--gold", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    annotations = commands.add_parser("import-annotations", help="Validate exported human review annotations")
    annotations.add_argument("--input", type=Path, required=True)
    annotations.add_argument("--review-pack", type=Path, required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command in {"ingest", "stats", "snapshot", "review-queue"}:
            from .database import DatasetDB
            database = DatasetDB(arguments.database)
            if arguments.command == "ingest":
                result = database.ingest(SourceRecord.model_validate(item) for item in read_jsonl(arguments.input))
            elif arguments.command == "stats":
                result = database.stats()
            elif arguments.command == "snapshot":
                result = database.export(arguments.output)
            else:
                result = database.review_queue()
            output(result)
        elif arguments.command == "classify":
            from .classification import run_classification
            from .classification.runner import validate_controls
            validate_controls(concurrency=arguments.concurrency,timeout=arguments.timeout,limit=arguments.limit,
                batch_size=arguments.batch_size,max_run_seconds=arguments.max_run_seconds,max_observed_tokens=arguments.max_observed_tokens)
            if arguments.input:
                inputs = [WorkInput.model_validate(item) for item in read_jsonl(arguments.input)]
            else:
                from .database import DatasetDB
                inputs = DatasetDB(arguments.database).work_inputs()
            output(run_classification(inputs, arguments.output, concurrency=arguments.concurrency, model=arguments.model,
                executable=arguments.executable, limit=arguments.limit,batch_size=arguments.batch_size,timeout=arguments.timeout,
                max_run_seconds=arguments.max_run_seconds,max_observed_tokens=arguments.max_observed_tokens,
                stop_on_rate_limit=arguments.stop_on_rate_limit))
        elif arguments.command == "review":
            from .database import DatasetDB
            from .classification import read_results
            from .evaluation import create_review_pack
            inputs = DatasetDB(arguments.database).work_inputs()
            if arguments.limit:
                inputs = inputs[:arguments.limit]
            output(create_review_pack(inputs, read_results(arguments.results), arguments.output))
        elif arguments.command == "evaluate":
            from .classification import read_results, load_taxonomy
            from .evaluation import evaluate_results, write_report
            gold = list(read_jsonl(arguments.gold)) if arguments.gold else []
            report = evaluate_results(read_results(arguments.results), gold, load_taxonomy())
            write_report(report, arguments.output)
            output(report)
        elif arguments.command == "import-annotations":
            from .evaluation import import_annotations
            output(import_annotations(arguments.input, arguments.review_pack))
        return 0
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
