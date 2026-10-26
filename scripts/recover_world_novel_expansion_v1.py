"""Explicit recovery of an immutable stopped window; never retry its attempts.

Seal/check are offline. Review sends ONLY the completed native outputs to a
separate reviewer, in five-work groups and at most two concurrent calls. Tail
adopts into a fresh private staging DB and exports the original failed scope.
Production publication and an explicit recovery continuation decision belong
to the root caller. None of these APIs manufactures a passed original gate.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
VERSION = "world-novel-expansion-recovery-plan-v1"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write("\n")


def checked(path, digest):
    path = Path(path).resolve(strict=True)
    if sha(path) != digest:
        raise ValueError("Sealed recovery artifact bytes differ: " + str(path))
    return path


def _works(path):
    from recommend_book.models import WorkInput
    return [WorkInput.model_validate_json(line) for line in Path(path).read_bytes().splitlines() if line.strip()]


def _unsubmitted(run, job):
    # A status flag alone is insufficient: even an orphan request disqualifies
    # an input from this fresh, never-submitted lineage.
    if (run / "jobs" / (job + ".json")).exists() or list((run / "attempts").glob(job + ".*")):
        raise ValueError("Actual unsubmitted proof absent: " + job)


def seal_recovery_plan(source, runtime, reservation, output):
    from recommend_book.usage_guard import model_hash
    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)
    reservation = Path(reservation).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Recovery output is create-only")
    parent = read(source / "manifest.json")
    reserved = read(reservation)
    checked(source / "manifest.json", reserved["source_sha256"])
    if Path(reserved["source_path"]).resolve() != source or parent.get("required_purpose_admissions_allowed") is not True:
        raise ValueError("Original admitted reservation required")
    if len(parent["chunks"]) != 1:
        raise ValueError("One original reserved native chunk required")
    chunk = parent["chunks"][0]
    name = chunk["run_name"]
    if reserved["run_name"] != name:
        raise ValueError("Original reserved run differs")
    terminal = runtime / ("terminal-" + name)
    observed = read(terminal / "adoption-arguments.json")
    run = Path(observed["run_dir"]).resolve(strict=True)
    expected_run = runtime / "runtime/data/autonomous-next-v3/classification" / name / "run"
    if run != expected_run:
        raise ValueError("Actual native run namespace differs")
    manifest = read(run / "manifest.json")
    proof = read(terminal / "terminal-proof.json")
    request = read(terminal / "request.json")
    exit_observation = read(terminal / "process-exit.json")
    if (observed.get("exit_code") != 0 or proof.get("exit_code") != 0
            or proof.get("process_exit_observed") is not True
            or exit_observation.get("actual_exit_code") != 0
            or exit_observation.get("process_exit_observed") is not True):
        raise ValueError("Actual observed terminal completion required")
    pins = {}

    def pin(path, digest=None):
        path = Path(path).resolve(strict=True)
        if digest is not None:
            checked(path, digest)
        pins[str(path)] = sha(path)
        return path

    pin(run / "manifest.json", proof["manifest_sha256"])
    pin(run / ("manifest." + manifest["run_id"] + ".json"), proof["manifest_sha256"])
    pin(run / "results.jsonl", proof["results_sha256"])
    if proof.get("run_id") != manifest["run_id"]:
        raise ValueError("Native terminal run identity differs")
    pin(terminal / "request.json", proof["parent_request_sha256"])
    pin(terminal / "process-exit.json", proof["parent_exit_observation_sha256"])
    pin(request["coordinator_path"], request["coordinator_sha256"])
    for path in terminal.iterdir():
        if path.is_file():
            pin(path)
    pin(source / "manifest.json")
    pin(source / "work-mapping.json", reserved.get("mapping_sha256"))
    pin(reservation)
    pin(parent["source_database"], parent["source_database_sha256"])
    pin(chunk["inputs_path"], chunk["inputs_sha256"])
    pin(chunk["usage_ledger_path"], chunk["usage_ledger_sha256"])
    for field, proof_field in (("ordered_inputs", "ordered_inputs_sha256"),
                               ("execution_context_manifest", "execution_context_manifest_sha256"),
                               ("migration_manifest", "migration_manifest_sha256"),
                               ("usage_ledger", None)):
        if field in observed:
            pin(observed[field], proof.get(proof_field) if proof_field else None)
    for path in (run / "jobs").glob("*.json"):
        pin(path)
    for path in (run / "attempts").iterdir():
        if path.is_file():
            pin(path)
    inputs = _works(chunk["inputs_path"])
    by_id = {work.work_id: work for work in inputs}
    identities = {row["work_id"]: row["input_hash"] for row in manifest["input_identities"]}
    mapping = {row["input_work_id"]: row for row in read(source / "work-mapping.json")}
    hashes = {model_hash(work) for work in inputs}
    if (len(by_id) != len(inputs) or len(mapping) != len(inputs) or set(mapping) != set(by_id)
            or identities != {work.work_id: model_hash(work) for work in inputs}
            or hashes != set(reserved["input_hashes"])):
        raise ValueError("Original source/reservation/native input identity differs")
    if "ordered_inputs" in observed and {work.work_id: model_hash(work) for work in _works(observed["ordered_inputs"])} != identities:
        raise ValueError("Actual ordered native inputs differ")
    partitions = {"completed": [], "failed": [], "unstarted": []}
    seen = set()
    for job, state in sorted(manifest["jobs"].items()):
        work_id, status = state["work_id"], state["status"]
        if work_id in seen or work_id not in by_id or status not in ("completed", "held", "not_submitted"):
            raise ValueError("Exact disjoint terminal job partition required")
        seen.add(work_id)
        row = {"job_key": job, "work_id": work_id, "input_sha256": identities[work_id],
               "qid": mapping[work_id]["wikidata_qid"], "original_status": status}
        if mapping[work_id]["input_sha256"] != identities[work_id]:
            raise ValueError("Source mapping input hash differs")
        if status == "not_submitted":
            _unsubmitted(run, job)
            partitions["unstarted"].append(row)
        else:
            saved = read(run / "jobs" / (job + ".json"))
            if saved.get("status") != status or saved.get("work_id") != work_id or saved.get("attempts") != 1:
                raise ValueError("Original single native attempt state differs")
            if not list((run / "attempts").glob(job + ".*.request.json")):
                raise ValueError("Original native request proof missing")
            row["error_kind"] = state.get("error_kind", saved.get("error_kind"))
            if status == "completed":
                pin(run / state.get("result_path", "results/" + job + ".json"))
            partitions["completed" if status == "completed" else "failed"].append(row)
    counts = {key: manifest[key] for key in ("selected", "completed", "failed", "pending", "cli_invocations")}
    if (seen != set(by_id) or counts != {"selected": len(inputs), "completed": len(partitions["completed"]),
            "failed": len(partitions["failed"]), "pending": len(partitions["unstarted"]),
            "cli_invocations": len(partitions["completed"]) + len(partitions["failed"])}
            or not partitions["completed"] or not partitions["failed"]):
        raise ValueError("Stopped failed native window count partition differs")
    plan = {"version": VERSION, "source": str(source), "runtime": str(runtime), "run": str(run),
        "terminal": str(terminal), "run_name": name, "original_run_id": manifest["run_id"],
        "original_counts": counts, "original_gate_failed": True,
        "original_failure_rate": counts["failed"] / counts["selected"],
        "original_stop_reasons": manifest.get("stop_reasons", []),
        "never_retry_failed": True, "never_reclassify_completed": True,
        "artifact_sha256": pins, **partitions}
    output.mkdir(parents=True)
    save(output / "plan.json", plan)
    return output / "plan.json"


def verify_recovery_plan(plan, expected_sha):
    plan = checked(plan, expected_sha)
    value = read(plan)
    if value.get("version") != VERSION or value.get("original_gate_failed") is not True:
        raise ValueError("Explicit failed-original recovery plan required")
    for path, digest in value["artifact_sha256"].items():
        checked(path, digest)
    run = Path(value["run"])
    for row in value["unstarted"]:
        _unsubmitted(run, row["job_key"])
    return value


def review_only_source(source):
    tree = ast.parse(source)
    matches = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "execute"
                and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "terminal"):
            matches.append(node)
    if len(matches) != 1:
        raise ValueError("Exactly one terminal execution recovery boundary required")
    matches[0].value = ast.Name(id="RECOVERY_OBSERVED", ctx=ast.Load())
    return ast.unparse(ast.fix_missing_locations(tree))


def _scripts():
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))


def _chain(value):
    # The census is independently usable on synthetic fixtures. Before any real
    # reviewer/adopter, authenticate the full existing native coordinator chain.
    from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
    terminal, run = Path(value["terminal"]), Path(value["run"])
    request = read(terminal / "request.json")
    return coordinator_chain_pins({"runtime_preparation": value["runtime"], "run_manifest": str(run / "manifest.json"),
        "coordinator_sha256": request["coordinator_sha256"], "artifact_sha256": value["artifact_sha256"]})


def _observed_call(runtime, code, arguments, output, *, verify):
    from recommend_book.scale20k_native_v1 import process_created_at
    verify()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(Path(runtime) / "runtime/src"), PYTHONDONTWRITEBYTECODE="1")
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
        env.pop(key, None)
    code = "from recommend_book.scale_guard_context_v1 import enable_process_cache; _cache=enable_process_cache(); " + code
    argv = [sys.executable, "-c", code, *map(str, arguments)]
    save(output / "request.json", {"argv": argv, "shell": False, "recovery_adapter_sha256": sha(__file__)})
    with (output / "stdout.txt").open("xb") as stdout, (output / "stderr.txt").open("xb") as stderr:
        with subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, shell=False) as process:
            save(output / "process-start.json", {"pid": process.pid, "process_created_at": process_created_at(process.pid)})
            # Up to five 900s groups, in serial if root chooses one slot.
            timed_out = False
            try:
                process.wait(timeout=5400)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait()
            save(output / "process-exit.json", {"pid": process.pid, "actual_exit_code": process.returncode,
                "process_exit_observed": True, "timed_out": timed_out})
    verify()
    if timed_out or process.returncode:
        raise ValueError("Recovery stage failed; preserve evidence without automatic resend: " + str(output))
    return json.loads((output / "stdout.txt").read_bytes())


def review_request(plan, expected_sha, output, *, concurrency=2):
    """Offline preparation of exact completed-scope review arguments."""
    value = verify_recovery_plan(plan, expected_sha)
    if type(concurrency) is not int or concurrency not in (1, 2):
        raise ValueError("Recovery review concurrency must be one or two")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Completed review output is create-only")
    actual = read(Path(value["terminal"]) / "adoption-arguments.json")
    selection = output.with_name(output.name + ".selection.json")
    save(selection, {"selected_work_ids": sorted(row["work_id"] for row in value["completed"]),
        "recovery_plan": str(Path(plan).resolve()), "recovery_plan_sha256": expected_sha,
        "original_counts": value["original_counts"], "original_gate_failed": True,
        "classification_requests": 0, "max_review_concurrency": concurrency})
    return {"runtime_root": str(Path(value["runtime"]) / "runtime"),
        **{key: actual[key] for key in ("ordered_inputs", "run_dir", "execution_context_manifest", "terminal_proof")},
        "ledger": actual["usage_ledger"], "ledger_sha256": sha(actual["usage_ledger"]),
        "output": str(output), "selection_path": str(selection), "timeout": 900,
        "max_review_concurrency": concurrency}


def _review_child(arguments):
    """Run inside the frozen interpreter; only scheduling differs from review6."""
    _scripts()
    import review_scale_content_v6 as review
    slots = arguments["max_review_concurrency"]
    if type(slots) is not int or slots not in (1, 2):
        raise ValueError("Recovery requires one or two actual reviewer slots")
    review.review_concurrency = lambda args: slots
    fields = ("runtime_root", "ordered_inputs", "run_dir", "execution_context_manifest", "terminal_proof", "ledger", "output", "selection_path")
    args = SimpleNamespace(**{**arguments, **{key: Path(arguments[key]) for key in fields}})
    return review.execute(args)


def review_completed(plan, expected_sha, output, *, concurrency=2):
    value = verify_recovery_plan(plan, expected_sha)
    _chain(value)
    args = review_request(plan, expected_sha, output, concurrency=concurrency)
    adapter_sha = sha(__file__)
    def verify():
        verify_recovery_plan(plan, expected_sha)
        checked(__file__, adapter_sha)
    code = ('import json,sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd()/"scripts")); '
            'from recover_world_novel_expansion_v1 import _review_child; '
            'print(json.dumps(_review_child(json.loads(sys.argv[1])),ensure_ascii=True))')
    result = _observed_call(value["runtime"], code, [json.dumps(args)],
        Path(output).with_name(Path(output).name + ".process"), verify=verify)
    result.update(selection_path=args["selection_path"], selection_sha256=sha(args["selection_path"]),
        full=True, original_gate_failed=True, recovery_plan=str(Path(plan).resolve()), recovery_plan_sha256=expected_sha)
    receipt = Path(output).with_name(Path(output).name + ".result.json")
    save(receipt, result)
    return receipt


def recover_completed(plan, expected_sha, review_result, output):
    """No model calls: use an observed completed review to create native v1 export.

    The fixed adopter writes a NEW staging database only. Its full54 failed gate
    and native chain are retained. Root separately performs output review and
    decides the explicit production adoption of the authenticated24 projection.
    """
    value = verify_recovery_plan(plan, expected_sha)
    _chain(value)
    review_result = Path(review_result).resolve(strict=True)
    review = read(review_result)
    if (review.get("recovery_plan_sha256") != expected_sha or review.get("full") is not True
            or review.get("reviewed_work_count") != len(value["completed"])):
        raise ValueError("Exact independent completed-scope recovery review required")
    selection = read(checked(review["selection_path"], review["selection_sha256"]))
    if set(selection["selected_work_ids"]) != {row["work_id"] for row in value["completed"]}:
        raise ValueError("Review scope differs from original completed native outputs")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    _scripts()
    import run_world_novel_expansion_v1 as runner
    original = read(Path(value["terminal"]) / "request.json")
    pins = [Path(plan).resolve(), Path(__file__).resolve(), review_result]
    pins.extend(Path(path) for path in value["artifact_sha256"])
    review_parent = review_result.with_name(review_result.name.removesuffix(".result.json") + ".process")
    pins.extend(path for path in review_parent.glob("*") if path.is_file())
    pin_hashes = {str(path): sha(path) for path in pins}
    def verify():
        verify_recovery_plan(plan, expected_sha)
        for path, digest in pin_hashes.items():
            checked(path, digest)
    def reservation(source, number):
        verify()
        if Path(source).resolve() != Path(value["source"]) or number != read(Path(source) / "manifest.json")["window_index"]:
            raise ValueError("Recovery original source scope differs")
        return pins
    coordinator = runner.load_coordinator(Path(value["runtime"]), reservation, base=output)
    module = ModuleType("world_native_completed_recovery_tail")
    module.__dict__.update(coordinator.__dict__)
    module.__file__ = original["coordinator_path"]
    module.RECOVERY_OBSERVED = read(Path(value["terminal"]) / "adoption-arguments.json")
    exec(compile(review_only_source(coordinator._expansion_source), module.__file__, "exec"), module.__dict__)
    sequence = 0
    def call(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        return _observed_call(runtime, code, arguments, output / "stages" / f"{sequence:02d}", verify=verify)
    module.frozen_call = call
    module.stage_process_pins = lambda: [*pins, *sorted(path for path in (output / "stages").glob("*/*") if path.is_file())]
    module.review_content_window = lambda *args, **kwargs: review
    original_save = module.save
    def export_save(path, document):
        if document.get("version") == "scale-content-native-classification-export-v1":
            if document["gate"]["passed"] is not False:
                raise ValueError("Recovery must retain the original failed full-window gate")
            document["recovery"] = {"plan": str(Path(plan).resolve()), "plan_sha256": expected_sha,
                "original_counts": value["original_counts"], "original_gate_failed": True,
                "completed_reclassified": 0, "failed_retried": 0, "fresh_unstarted_count": len(value["unstarted"])}
        return original_save(path, document)
    module.save = export_save
    result = module.execute_window(value["source"], value["runtime"], read(Path(value["source"]) / "manifest.json")["window_index"])
    verify()
    save(output / "result.json", result)
    return result


def prepare_unstarted(plan, expected_sha, output, run_name):
    """Offline exact never-submitted subset; immutable original source DB reused.

    Old exclusions/reservations remain unchanged. This manifest carries the
    explicit exception proof for these never-sent inputs, not a retry waiver.
    Root must launch it through a fresh reservation with concurrency<=2.
    """
    from recommend_book.usage_guard import UsageGuard, model_hash
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    value = verify_recovery_plan(plan, expected_sha)
    if not run_name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in run_name) or run_name == value["run_name"]:
        raise ValueError("Distinct safe fresh recovery run name required")
    source, output = Path(value["source"]), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    parent = read(source / "manifest.json")
    original = parent["chunks"][0]
    wanted = {row["work_id"] for row in value["unstarted"]}
    if not wanted:
        raise ValueError("No genuinely unsubmitted work to prepare")
    works = [work for work in _works(original["inputs_path"]) if work.work_id in wanted]
    mapping = [row for row in read(source / "work-mapping.json") if row["input_work_id"] in wanted]
    if len(works) != len(wanted) or {model_hash(work) for work in works} != {row["input_sha256"] for row in value["unstarted"]}:
        raise ValueError("Fresh subset differs from actual unsubmitted proof")
    _verify_mapping_identity(parent["source_database"], mapping, works)
    inputs = output / "ordered-inputs.jsonl"
    inputs.write_text("".join(work.model_dump_json() + "\n" for work in works), encoding="utf-8", newline="\n")
    ledger = deepcopy(read(original["usage_ledger_path"]))
    retained = {}
    for digest, entry in ledger["inputs"].items():
        ref = entry["input_ref"]
        from recommend_book.models import WorkInput
        work = WorkInput.model_validate_json(Path(ref["path"]).read_bytes().splitlines()[ref["line_number"] - 1])
        if work.work_id in wanted:
            retained[digest] = entry
    ledger["inputs"] = retained
    for number, work in enumerate(works, 1):
        ledger["inputs"][model_hash(work)]["input_ref"] = {"path": str(inputs), "sha256": sha(inputs), "line_number": number}
    ledger["derived_artifacts"] = {key: row for key, row in ledger["derived_artifacts"].items()
                                   if set(row["originating_input_hashes"]) <= set(retained)}
    ledger_path = output / "usage-ledger.json"
    save(ledger_path, ledger)
    guard = UsageGuard(ledger_path, expected_ledger_sha256=sha(ledger_path))
    receipts = {usage: [guard.check_input(work, usage).model_dump(mode="json") for work in works]
                for usage in parent["required_input_usages"]}
    save(output / "admission-receipts.json", receipts)
    if any(not row["allowed"] for rows in receipts.values() for row in rows):
        raise ValueError("Fresh unsubmitted subset required-purpose admission held")
    save(output / "work-mapping.json", mapping)
    qids = {row["wikidata_qid"] for row in mapping}
    records_path = output / "records.jsonl"
    records = [json.loads(line) for line in Path(parent["records_path"]).read_bytes().splitlines() if line.strip()]
    records_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records if row["source_id"] in qids), encoding="utf-8", newline="\n")
    manifest = {**parent, "selected": len(works), "records_path": str(records_path), "records_sha256": sha(records_path),
        "ordered_inputs_path": str(inputs), "ordered_inputs_sha256": sha(inputs),
        "usage_ledger_path": str(ledger_path), "usage_ledger_sha256": sha(ledger_path),
        "chunks": [{**original, "run_name": run_name, "selected": len(works), "inputs_path": str(inputs),
            "inputs_sha256": sha(inputs), "usage_ledger_path": str(ledger_path), "usage_ledger_sha256": sha(ledger_path)}],
        "original_languages": dict(Counter(work.original_language or "unknown" for work in works)),
        "with_explicit_genre_claims": sum(bool(row.get("genre_qids")) for row in mapping),
        "source_database_reused_read_only": True, "new_source_records_ingested": 0,
        "recovery_lineage": {"plan": str(Path(plan).resolve()), "plan_sha256": expected_sha,
            "original_gate_failed": True, "original_counts": value["original_counts"],
            "unsubmitted_work_ids": sorted(wanted), "max_classification_concurrency": 2,
            "prior_reservations_unchanged": True, "completed_reclassified": 0, "failed_retried": 0},
        "recovery_adapter_sha256": sha(__file__)}
    verify_recovery_plan(plan, expected_sha)
    save(output / "manifest.json", manifest)
    return output / "manifest.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    seal = sub.add_parser("seal")
    for field in ("source", "runtime", "reservation", "output"):
        seal.add_argument("--" + field, type=Path, required=True)
    for command in ("check", "review", "tail", "prepare-unstarted"):
        child = sub.add_parser(command)
        child.add_argument("--plan", type=Path, required=True)
        child.add_argument("--plan-sha256", required=True)
        if command != "check":
            child.add_argument("--output", type=Path, required=True)
        if command == "review":
            child.add_argument("--concurrency", type=int, choices=(1, 2), default=2)
        if command == "tail":
            child.add_argument("--review-result", type=Path, required=True)
        if command == "prepare-unstarted":
            child.add_argument("--run-name", required=True)
    args = parser.parse_args()
    if args.command == "seal":
        result = seal_recovery_plan(args.source, args.runtime, args.reservation, args.output)
    elif args.command == "check":
        result = verify_recovery_plan(args.plan, args.plan_sha256)["original_counts"]
    elif args.command == "review":
        result = review_completed(args.plan, args.plan_sha256, args.output, concurrency=args.concurrency)
    elif args.command == "tail":
        result = recover_completed(args.plan, args.plan_sha256, args.review_result, args.output)
    else:
        result = prepare_unstarted(args.plan, args.plan_sha256, args.output, args.run_name)
    print(json.dumps(result if isinstance(result, dict) else {"path": str(result), "sha256": sha(result)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
