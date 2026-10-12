"""Prepare a new local execution namespace without starting classification."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import uuid

from recommend_book.classification.runner import build_run_config, validate_controls
from recommend_book.classification.validation import ROOT
from recommend_book.models import WorkInput

ARTIFACTS = (
    "config/sources.json", "config/taxonomy.json", "schemas/classification.schema.json",
    "src/recommend_book/classification/prompt.py", "src/recommend_book/classification/validation.py",
    "src/recommend_book/classification/runner.py", "src/recommend_book/models.py",
    "src/recommend_book/jobs.py", "pyproject.toml", "uv.lock",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON property: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    def floating(value):
        number = float(value)
        return number if math.isfinite(number) else nonfinite(value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite, parse_float=floating)


def freeze_execution(input_path, output_dir, *, model, cli_version, batch_size=1,
                     concurrency=2, timeout=900, max_run_seconds=None,
                     max_observed_tokens=None, stop_on_rate_limit=False):
    validate_controls(batch_size=batch_size, concurrency=concurrency, timeout=timeout,
                      max_run_seconds=max_run_seconds, max_observed_tokens=max_observed_tokens)
    if not isinstance(model, str) or not model.strip() or not isinstance(cli_version, str) or not cli_version.strip():
        raise ValueError("Explicit nonempty model and CLI version required")
    if not isinstance(stop_on_rate_limit, bool):
        raise ValueError("stop_on_rate_limit must be boolean")
    input_path = Path(input_path).resolve(strict=True)
    input_manifest = input_path.with_name("manifest.json").resolve(strict=True)
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Output already exists")
    if output.is_relative_to(input_path.parent):
        raise ValueError("Output cannot be inside the input directory")
    paths = {"input": input_path, "input_manifest": input_manifest,
             **{name: (ROOT / name).resolve(strict=True) for name in ARTIFACTS}}
    initial = {name: path.read_bytes() for name, path in paths.items()}
    manifest = strict_json(initial["input_manifest"])
    if not isinstance(manifest, dict):
        raise ValueError("Sibling input manifest must be a JSON object")
    declared = [manifest[key] for key in ("ordered_inputs_sha256", "work_inputs_sha256") if key in manifest]
    if not declared or any(value != sha(initial["input"]) for value in declared):
        raise ValueError("Declared input SHA does not match exact input bytes")
    identities = []
    for line in initial["input"].decode("utf-8").splitlines():
        if line.strip():
            work = WorkInput.model_validate(strict_json(line))
            identities.append((work.work_id, work.expression_id))
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate work/expression identity")
    taxonomy = strict_json(initial["config/taxonomy.json"])
    config = build_run_config(model, cli_version, taxonomy, batch_size)
    for key, artifact in (("schema_hash", "schemas/classification.schema.json"),
                          ("prompt_hash", "src/recommend_book/classification/prompt.py"),
                          ("validation_hash", "src/recommend_book/classification/validation.py")):
        if config[key] != sha(initial[artifact]):
            raise ValueError("Config artifact changed during preparation")
    controls = {"batch_size": batch_size, "concurrency": concurrency, "timeout": timeout,
                "max_run_seconds": max_run_seconds, "max_observed_tokens": max_observed_tokens,
                "stop_on_rate_limit": stop_on_rate_limit}
    report = {
        "version": "classification-execution-freeze-v1",
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "execution_state": "prepared_not_started", "model_run_started": False,
        "cli_version_verification": "supplied_unverified", "network_requests": 0,
        "input_provenance": {"source_path": str(input_path), "manifest_path": str(input_manifest),
                             "input_sha256": sha(initial["input"]),
                             "manifest_sha256": sha(initial["input_manifest"]),
                             "processing_context": manifest.get("processing_context"),
                             "work_expression_count": len(identities)},
        "execution_context": {"run_config": config, "controls": controls,
                              "artifact_count": len(ARTIFACTS),
                              "artifact_sha256": {name: sha(initial[name]) for name in ARTIFACTS},
                              "code_root": str(ROOT.resolve())},
        "source_files_unchanged_before_publish": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".{output.name}.prepare-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        copies = {"inputs/ordered.jsonl": initial["input"], "inputs/manifest.json": initial["input_manifest"],
                  **{f"artifacts/{name}": initial[name] for name in ARTIFACTS}}
        for relative, raw in copies.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        report["output_sha256"] = {name: sha(raw) for name, raw in copies.items()}
        manifest_bytes = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        (stage / "manifest.json").write_bytes(manifest_bytes)
        for relative, expected in report["output_sha256"].items():
            if sha((stage / relative).read_bytes()) != expected:
                raise ValueError("Staged copy changed during preparation")
        if (stage / "manifest.json").read_bytes() != manifest_bytes:
            raise ValueError("Staged manifest changed during preparation")
        final_config = build_run_config(model, cli_version, taxonomy, batch_size)
        final = {name: path.read_bytes() for name, path in paths.items()}
        if final != initial or final_config != config:
            raise ValueError("Source or config changed during preparation")
        if output.exists():
            raise ValueError("Output already exists")
        stage.rename(output)
        return report
    finally:
        # Remove only the exact UUID stage owned by this invocation. Never
        # delete an existing output or another preparer's staging directory.
        if stage.exists():
            resolved = stage.resolve()
            if resolved.parent != output.parent.resolve() or resolved == output.parent.resolve():
                raise ValueError("Unsafe stage cleanup target")
            shutil.rmtree(resolved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--cli-version", required=True, help="Explicit supplied value; not measured here")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-run-seconds", type=float)
    parser.add_argument("--max-observed-tokens", type=int)
    parser.add_argument("--stop-on-rate-limit", action="store_true")
    args = parser.parse_args(argv)
    report = freeze_execution(args.input, args.output, model=args.model, cli_version=args.cli_version,
                              batch_size=args.batch_size, concurrency=args.concurrency, timeout=args.timeout,
                              max_run_seconds=args.max_run_seconds, max_observed_tokens=args.max_observed_tokens,
                              stop_on_rate_limit=args.stop_on_rate_limit)
    print(json.dumps({"execution_state": report["execution_state"], "output": str(args.output),
                      "work_expression_count": report["input_provenance"]["work_expression_count"]}))


if __name__ == "__main__":
    main()
