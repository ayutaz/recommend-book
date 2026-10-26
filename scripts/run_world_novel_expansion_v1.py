"""Create-only, serial-window expansion over the fixed one-work/10M recipe.

`check` validates a separately sealed source index without model calls.
`run` performs mandatory preparation, real Sol classification and independent
full review, then writes export receipts. Catalogue import remains a separate
single-writer operation; these receipts do not claim publication or human gold.
The exclusion inventory must be independently fixed by the source coordinator.
"""
from __future__ import annotations

import argparse
import ast
import builtins
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from recommend_book import scale20k_native_v1 as fixed
from recommend_book.scale_content_control_v3 import actual_gate
from recommend_book.scale_content_control_v4 import display_counts

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/world-21000-delivery-20261026-v1"
INDEX_VERSION = "world-novel-expansion-source-index-v1"
sha, read, save = fixed.sha, fixed.read, fixed.save


def run_name(index):
    if type(index) is not int or not 0 <= index < 100:
        raise ValueError("Expansion window index must be an integer in 0..99")
    return f"world21000-20261026-w{index:03d}-v1"


def checked(path, digest):
    path = Path(path).resolve(strict=True)
    if not isinstance(digest, str) or len(digest) != 64 or sha(path) != digest:
        raise ValueError("Sealed artifact bytes differ: " + str(path))
    return path


def load_sources(index, expected_sha, *, base=BASE):
    """Verify all actual identities before any preparation or attempt reservation."""
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    base = Path(base).resolve()
    index = checked(index, expected_sha)
    document = read(index)
    if document.get("version") != INDEX_VERSION:
        raise ValueError("Dedicated expansion source index required")
    inventory = read(checked(**{"path": document["exclusions"]["path"], "digest": document["exclusions"]["sha256"]}))
    qids, hashes = set(inventory["qids"]), set(inventory["input_hashes"])
    entries = document.get("windows", [])
    indices = [row["window_index"] for row in entries]
    if not entries or indices != list(range(len(entries))) or len(entries) > 100:
        raise ValueError("Contiguous unique expansion indices starting at zero required")
    windows = []
    for row in entries:
        number = row["window_index"]
        name = run_name(number)
        source = Path(row["source_path"]).resolve(strict=True)
        if not source.is_relative_to(base):
            raise ValueError("Expansion sources must remain in the fresh namespace")
        manifest = checked(source / "manifest.json", row["source_sha256"])
        checked(source / "work-mapping.json", row["mapping_sha256"])
        value = read(manifest)
        if (value.get("version") != "scale-content-classification-source-v1"
                or value.get("required_purpose_admissions_allowed") is not True
                or value.get("window_index") != number or len(value.get("chunks", [])) != 1
                or type(value.get("selected")) is not int or not 1 <= value["selected"] <= 100):
            raise ValueError("One bounded required-purpose-admitted expansion window required")
        chunk = value["chunks"][0]
        if chunk["run_name"] != name:
            raise ValueError("Unique expansion run name differs")
        inputs = checked(chunk["inputs_path"], chunk["inputs_sha256"])
        ledger = checked(chunk["usage_ledger_path"], chunk["usage_ledger_sha256"])
        database = checked(value["source_database"], value["source_database_sha256"])
        if not all(path.is_relative_to(base) for path in (inputs, ledger, database)):
            raise ValueError("New input, ledger and source DB must remain in the fresh namespace")
        works = [WorkInput.model_validate_json(line) for line in inputs.read_bytes().splitlines() if line.strip()]
        mapping = read(source / "work-mapping.json")
        _verify_mapping_identity(database, mapping, works)
        new_qids = {item["wikidata_qid"] for item in mapping}
        new_hashes = {model_hash(work) for work in works}
        if not len(new_qids) == len(new_hashes) == len(works) == value["selected"]:
            raise ValueError("Distinct actual work identities required")
        if new_qids & qids or new_hashes & hashes:
            raise ValueError("Old, previously attempted or duplicate new authority/input excluded")
        qids.update(new_qids)
        hashes.update(new_hashes)
        windows.append({**row, "run_name": name, "source_path": str(source),
            "qids": sorted(new_qids), "input_hashes": sorted(new_hashes),
            "selected": len(works), "inputs_path": str(inputs), "ledger_path": str(ledger)})
    return windows


def _adapt_coordinator(tree, *, base):
    """Replace only old orchestration identity/scheduling; validators stay fixed."""
    counts = dict(namespace=0, reservation=0, continuation=0, scheduling=0, eight=0)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("data/autonomous-next-v3/scale-20000-classifications-v1/"):
            suffix = node.value.removeprefix("data/autonomous-next-v3/scale-20000-classifications-v1/")
            node.value = str(Path(base).resolve() / suffix)
            counts["namespace"] += 1
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "execute_window")
    body = []
    for node in function.body:
        if isinstance(node, ast.ImportFrom) and node.module == "recommend_book.scale20k_native_v1":
            node = ast.parse("V19_RUNTIME = EXPANSION_RUNTIME").body[0]
            counts["reservation"] += 1
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "route_index":
            # New index+attempt receipts replace the historic import-control route.
            node = ast.Pass()
            counts["continuation"] += 1
        elif isinstance(node, ast.If) and "concurrency" in ast.unparse(node.test):
            comparison = node.test.values[0] if isinstance(node.test, ast.BoolOp) else node.test
            if isinstance(comparison, ast.Compare) and isinstance(comparison.ops[0], ast.NotIn):
                comparison.comparators[0] = ast.parse("(2, 4, 8)", mode="eval").body
                counts["scheduling"] += 1
            elif isinstance(comparison, ast.Compare) and isinstance(comparison.ops[0], ast.Eq) and isinstance(comparison.comparators[0], ast.Constant) and comparison.comparators[0].value == 8:
                node = ast.Pass()
                counts["eight"] += 1
        body.append(node)
    function.body = body
    if counts != dict(namespace=3, reservation=1, continuation=1, scheduling=1, eight=1):
        raise ValueError("Fixed expansion adapter boundaries differ: " + str(counts))
    return ast.fix_missing_locations(tree)


def load_coordinator(runtime, verify_reservation, *, base=BASE):
    # The loader authenticates every fixed recipe before expanding its AST.
    # Its compile override is restored immediately, before starting any child.
    missing = object()
    old = fixed.__dict__.get("compile", missing)
    captured = []
    def compile_expansion(source, filename, mode, *args, **kwargs):
        if isinstance(source, ast.AST) and Path(filename).resolve() == Path(__file__).resolve():
            source = _adapt_coordinator(source, base=base)
            captured.append(ast.unparse(source))
        return builtins.compile(source, filename, mode, *args, **kwargs)
    fixed.compile = compile_expansion
    try:
        recipe = fixed.load_recipe("coordinator", __file__)
    finally:
        if old is missing:
            fixed.__dict__.pop("compile", None)
        else:
            fixed.compile = old
    if len(captured) != 1:
        raise ValueError("Exactly one immutable coordinator expansion required")
    recipe.EXPANSION_RUNTIME = Path(runtime)
    recipe.verify_reservation = verify_reservation
    recipe._expansion_source = captured[0]
    return recipe


def prepare_window(runtime, window, concurrency, *, prior_control=None, prior_control_sha256=None):
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import prepare_world_novel_chunk_v1 as preparer
    return preparer.prepare(runtime, window["inputs_path"], window["ledger_path"], window["run_name"], concurrency,
        prior_control=prior_control, prior_control_sha256=prior_control_sha256)


def prepare_source_window(records, catalogue, output, official_conditions, window_index, *, base=BASE):
    """Explicit root-only source preparation; no collection, models or catalogue writes.

    The existing v2 adapter requires display=1 and no native classification but
    allows unknown source genre (recommendable=0). It preserves all source rows
    and performs the unchanged required-purpose admissions, without any of the
    later scale20k recovery route/name/lineage machinery.
    """
    output = Path(output).resolve()
    if not output.is_relative_to(Path(base).resolve()) or output.exists():
        raise ValueError("Fresh expansion source output required")
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import prepare_scale20k_content_sources_v2 as source
    return source.prepare_native_window(records, catalogue, output, official_conditions,
        window_index=window_index, run_name=run_name(window_index))


def load_publication_tail(database, observed_import, *, base=BASE):
    """Load the fixed tail for an explicit caller; this does not publish anything.

    Root supplies observed_import(export, sha), retaining request/start/exit/
    stdout evidence and holding the existing writer_lock around the real DB
    import. After create_reviewed_export(v1, sha, fresh_v3_dir), call the returned
    module.finalize(v3_manifest, sha) to import, observe actual display, create a
    v4 control and verify its gate. No scale20k target/controller is started.
    """
    from types import ModuleType
    path = ROOT / "data/autonomous-next-v3/scale-30000-v1/content-operational-driver-v2.py"
    checked(path, fixed.OPERATION_SHA)
    module = ModuleType("world_expansion_explicit_publication")
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    module.ROOT, module.BASE, module.DATABASE, module.LOCK = ROOT, Path(base), Path(database), fixed.WRITER_LOCK
    module.observed_import = observed_import
    return module


def execute_window(runtime, window, pins, *, base):
    original_pins = {str(path): sha(path) for path in pins}
    def verify_pins():
        for path, digest in original_pins.items():
            checked(path, digest)
    def verify_reservation(source, number):
        verify_pins()
        if Path(source).resolve() != Path(window["source_path"]).resolve() or number != window["window_index"]:
            raise ValueError("Actual execution differs from reserved fresh source")
        checked(Path(source) / "manifest.json", window["source_sha256"])
        return pins
    recipe = load_coordinator(runtime, verify_reservation, base=base)
    stages = Path(base) / "execution" / window["run_name"] / "stages"
    sequence = 0
    def frozen_call(preparation, code, arguments):
        nonlocal sequence
        verify_pins()
        sequence += 1
        output = stages / f"{sequence:02d}"
        output.mkdir(parents=True)
        env = dict(os.environ, PYTHONPATH=str(Path(preparation) / "runtime/src"), PYTHONDONTWRITEBYTECODE="1")
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
            env.pop(key, None)
        code = "from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); " + code
        argv = [sys.executable, "-c", code, *map(str, arguments)]
        save(output / "request.json", {"argv": argv, "shell": False})
        with (output / "stdout.txt").open("xb") as stdout, (output / "stderr.txt").open("xb") as stderr:
            with subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, shell=False) as process:
                save(output / "process-start.json", {"pid": process.pid, "process_created_at": fixed.process_created_at(process.pid)})
                timed_out = False
                try:
                    process.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.kill()
                    process.wait()
                save(output / "process-exit.json", {"pid": process.pid, "actual_exit_code": process.returncode,
                     "process_exit_observed": True, "timed_out": timed_out})
        if timed_out or process.returncode != 0:
            raise ValueError("Actual frozen stage failed; preserve raw evidence without retry: " + str(output))
        return json.loads((output / "stdout.txt").read_bytes())
    recipe.frozen_call = frozen_call
    def stage_pins():
        verify_pins()
        return [*pins, *sorted(path for path in stages.glob("*/*") if path.is_file())]
    recipe.stage_process_pins = stage_pins
    return recipe.execute_window(window["source_path"], runtime, window["window_index"])


def export_gate(public, run):
    rows = public["verified_projection"]["rows"]
    qids = [row["qid"] for row in rows]
    if len(set(qids)) != len(qids):
        raise ValueError("Duplicate exported authority")
    counts = display_counts(rows)
    result = actual_gate(run["selected"], len(rows), counts["actual_display_feature_works"],
        counts["actual_content_non_genre_feature_works"],
        stopped=bool(run.get("pending") or run.get("stopped") or run.get("stop_reasons")))
    if public.get("gate", {}).get("passed") is not True:
        result["passed"] = False
        result["reasons"].append("fixed_coordinator_gate_failed")
    return result


def run_one(index, expected_sha, runtime, window_index, *, concurrency=4,
            prior_control=None, prior_control_sha256=None, base=BASE):
    """Run one fresh window; the caller publishes and verifies its real control.

    Later windows require the prior actual publication control. Eight slots
    additionally require the unchanged preparer's clean four/eight-slot trial.
    No catalogue mutation occurs here. Root owns the serialized publication.
    """
    if type(concurrency) is not int or concurrency not in (2, 4, 8):
        raise ValueError("Concurrency must be 2, 4 or 8")
    base, runtime = Path(base).resolve(), Path(runtime).resolve(strict=True)
    windows = load_sources(index, expected_sha, base=base)
    if type(window_index) is not int or not 0 <= window_index < len(windows):
        raise ValueError("Window not present in fixed independent source index")
    window = windows[window_index]
    if window_index == 0:
        if concurrency == 8 or prior_control is not None:
            raise ValueError("Initial expansion window starts at two/four slots without borrowed control")
    else:
        from recommend_book.scale_content_control_v4 import verify_control_receipt
        if prior_control is None or prior_control_sha256 is None:
            raise ValueError("Prior actual published expansion gate required")
        prior_control = Path(prior_control).resolve(strict=True)
        prior = verify_control_receipt(prior_control, prior_control_sha256)
        if not prior_control.is_relative_to(base) or prior["window_index"] != window_index - 1 or not prior["gate"]["passed"]:
            raise ValueError("Prior actual passed expansion window differs")
    # A permanent create-only reservation retains failure evidence and forbids
    # automatic retry. Do not remove it to resume; prepare a separate handoff.
    invocation = base / "execution"
    invocation.mkdir(exist_ok=True)
    attempt = invocation / window["run_name"]
    attempt.mkdir()
    save(attempt / "request.json", {"source_index": str(Path(index).resolve()), "source_index_sha256": expected_sha,
        "window_index": window_index, "concurrency": concurrency, "batch_size": 1, "max_observed_tokens": 10000000,
        "coordinator": str(Path(__file__).resolve()), "coordinator_sha256": sha(__file__),
        "started_at": datetime.now(timezone.utc).isoformat(), "catalogue_writes": 0})
    save(attempt / "reservation.json", window)
    inventory = read(index)["exclusions"]
    pins = [Path(index).resolve(), checked(inventory["path"], inventory["sha256"]), Path(__file__).resolve(),
            attempt / "request.json", attempt / "reservation.json"]
    if prior_control is not None:
        pins.append(prior_control)
    prepare_window(runtime, window, concurrency, prior_control=prior_control, prior_control_sha256=prior_control_sha256)
    result = execute_window(runtime, window, pins, base=base)
    save(attempt / "result.json", result)
    if result.get("stage") != "actual_native_export_verified":
        raise ValueError("Actual native execution did not produce a verified export; preserve attempt")
    export = checked(result["export_manifest"], result["export_manifest_sha256"])
    public = read(export)
    gate = export_gate(public, read(public["run_manifest"]))
    receipt = {"version": "world-novel-expansion-window-v1", "export_path": str(export),
        "export_sha256": sha(export), "gate": gate, "catalogue_publication_pending": True,
        "human_quality_verified": False, "old_classifications_modified": False}
    save(attempt / "gate.json", receipt)
    if not gate["passed"]:
        raise ValueError("Actual expansion gate failed; stop without retry: " + str(attempt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "run"))
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--source-index-sha256", required=True)
    parser.add_argument("--runtime-preparation", type=Path, default=fixed.RUNTIME)
    parser.add_argument("--window-index", type=int, default=0)
    parser.add_argument("--concurrency", type=int, choices=(2, 4, 8), default=4)
    parser.add_argument("--prior-control", type=Path)
    parser.add_argument("--prior-control-sha256")
    args = parser.parse_args()
    if args.mode == "check":
        windows = load_sources(args.source_index, args.source_index_sha256)
        result = {"selected": sum(row["selected"] for row in windows), "windows": len(windows), "model_calls": 0}
    else:
        result = run_one(args.source_index, args.source_index_sha256, args.runtime_preparation,
            args.window_index, concurrency=args.concurrency, prior_control=args.prior_control,
            prior_control_sha256=args.prior_control_sha256)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
