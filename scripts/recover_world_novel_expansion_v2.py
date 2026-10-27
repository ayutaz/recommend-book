"""Explicit two-slot fresh scheduling and a failed-original recovery checkpoint.

This adapter leaves every frozen recipe/runtime byte intact. Classification is
one work/call, two slots, one hour/10M observed tokens. Independent review uses
the unchanged five-work recipe with only its scheduling guard set to two slots.
No failed or completed original input can enter the fresh-unstarted route.
Publication remains an explicit serialized root operation through publisher-v1.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
from pathlib import Path
import json
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import recover_world_novel_expansion_v1 as v1
import run_world_novel_expansion_v1 as runner

sha, read, save, checked = v1.sha, v1.read, v1.save, v1.checked
V1_SHA = "6e5a3569526d4b6ca1aa47b4327945361ef8e1f92c084e94eefd125a724ecfe7"
CONTROL_V4_SHA = "d9d8a6dcf56396f6db9eabfe642c5105b7f3cba2809ef83202ab289ddca2741d"
CHECKPOINT = "world-novel-expansion-recovery-checkpoint-v1"
SERVICE_MANIFEST = ROOT / "data/light-novels-v1/continuation-20261026-next-v1/classification-v1/run/manifest.json"
SERVICE_MANIFEST_SHA = "9b77286a7a97a133d1a6cd7b051e1c192bdd8181e5cc7277b65a7d9e0c23f965"
SERVICE_EXIT = ROOT / "data/light-novels-v1/continuation-20261026-next-v1/classification-process-v1/process-exit.json"
SERVICE_EXIT_SHA = "3a366f12196fbc72f5f970d71eb437d35bae33b1d6e1a3789357a3c9797a4f6a"


def service_recovery_evidence():
    """Connection evidence only; LN adoption2/3 remains a failed quality gate."""
    document = read(checked(SERVICE_MANIFEST, SERVICE_MANIFEST_SHA))
    observed = read(checked(SERVICE_EXIT, SERVICE_EXIT_SHA))
    for path, digest in document["artifact_sha256"].items():
        checked(path, digest)
    if (document.get("version") != "light-novel-series-feature-export-v1"
            or len(document.get("classification_attempts", [])) != 3
            or len(document.get("review_attempts", [])) != 1 or document.get("failures") != []
            or document["gate"]["failed"] != 0 or document["gate"]["pending"] != 0
            or document["gate"]["selected"] != 3 or document["gate"]["passed"] is not False
            or observed.get("actual_exit_code") != 0 or observed.get("process_exit_observed") is not True
            or observed.get("timed_out") is not False):
        raise ValueError("Fixed actual service-recovery observation differs")
    return {"version": "world-novel-service-connection-recovery-observation-v1",
        "manifest": str(SERVICE_MANIFEST.resolve()), "manifest_sha256": SERVICE_MANIFEST_SHA,
        "terminal_exit": str(SERVICE_EXIT.resolve()), "terminal_exit_sha256": SERVICE_EXIT_SHA,
        "classification_calls_succeeded": 3, "review_calls_succeeded": 1,
        "service_connection_only": True, "world_quality_gate_used": False,
        "light_novel_quality_gate_passed": False, "light_novel_feature_adoption": "2/3",
        "artifact_sha256": document["artifact_sha256"]}


def source_window(source, expected_sha):
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    from recommend_book.usage_guard import model_hash
    source = Path(source).resolve(strict=True)
    parent = read(checked(source / "manifest.json", expected_sha))
    if (parent.get("version") != "scale-content-classification-source-v1"
            or parent.get("required_purpose_admissions_allowed") is not True
            or len(parent.get("chunks", [])) != 1 or not 1 <= parent.get("selected", 0) <= 100):
        raise ValueError("One bounded actually admitted source window required")
    chunk = parent["chunks"][0]
    inputs = checked(chunk["inputs_path"], chunk["inputs_sha256"])
    ledger = checked(chunk["usage_ledger_path"], chunk["usage_ledger_sha256"])
    database = checked(parent["source_database"], parent["source_database_sha256"])
    works = v1._works(inputs)
    mapping = read(source / "work-mapping.json")
    _verify_mapping_identity(database, mapping, works)
    hashes, qids = {model_hash(work) for work in works}, {row["wikidata_qid"] for row in mapping}
    if not len(works) == len(hashes) == len(qids) == len(mapping) == parent["selected"]:
        raise ValueError("Exact distinct admitted source identities required")
    return {"source_path": str(source), "source_sha256": expected_sha,
        "mapping_sha256": sha(source / "work-mapping.json"), "selected": len(works),
        "window_index": parent["window_index"], "run_name": chunk["run_name"],
        "inputs_path": str(inputs), "ledger_path": str(ledger),
        "input_hashes": sorted(hashes), "qids": sorted(qids), "work_ids": sorted(work.work_id for work in works)}


def check_unstarted(plan, expected_sha, source, source_sha):
    value = v1.verify_recovery_plan(plan, expected_sha)
    window = source_window(source, source_sha)
    parent = read(Path(source) / "manifest.json")
    lineage = parent.get("recovery_lineage", {})
    expected = value["unstarted"]
    if (lineage.get("plan_sha256") != expected_sha
            or Path(lineage.get("plan", "")).resolve() != Path(plan).resolve()
            or lineage.get("original_counts") != value["original_counts"]
            or lineage.get("original_gate_failed") is not True
            or lineage.get("prior_reservations_unchanged") is not True
            or lineage.get("max_classification_concurrency") != 2
            or lineage.get("completed_reclassified") != 0 or lineage.get("failed_retried") != 0
            or set(lineage.get("unsubmitted_work_ids", [])) != {row["work_id"] for row in expected}
            or set(window["input_hashes"]) != {row["input_sha256"] for row in expected}
            or set(window["qids"]) != {row["qid"] for row in expected}
            or set(window["work_ids"]) != {row["work_id"] for row in expected}
            or window["selected"] != len(expected)):
        raise ValueError("Fresh source differs from exact original unsubmitted lineage")
    if window["run_name"] == value.get("run_name"):
        raise ValueError("Original run cannot be retried")
    return window, lineage


def require_fresh_execution(runtime, window, base):
    runtime, base = Path(runtime), Path(base)
    name = window["run_name"]
    if ((runtime / ("terminal-" + name)).exists()
            or (runtime / "runtime/data/autonomous-next-v3/classification" / name).exists()):
        raise FileExistsError("Actual native run already exists; never reenter or resend")
    if (base / "execution" / name).exists():
        raise FileExistsError("Permanent fresh reservation already exists; preserve without retry")


def low_concurrency_review(base, frozen_call, *, verify, lineage):
    """Return the full-review callback; limits/semantic recipe remain unchanged."""
    base = Path(base)
    def review(runtime, actual, name, *, full):
        verify()
        manifest = read(Path(actual["run_dir"]) / "manifest.json")
        remaining = 3600 - manifest["elapsed_seconds"]
        tokens = sum(manifest["usage"].get(key, 0) for key in ("input_tokens", "output_tokens"))
        if remaining <= 0 or tokens >= 10000000 or manifest.get("stop_reasons"):
            raise ValueError("Actual fresh window budget exhausted; preserve outputs without retry")
        selected = sorted(state["work_id"] for state in manifest["jobs"].values() if state["status"] == "completed")
        if not selected or len(selected) != len(set(selected)) or len(selected) != manifest["completed"]:
            raise ValueError("Exact completed native work scope required")
        output = base / "separate-review-two-slots" / name
        selection = output.with_name(name + ".selection.json")
        save(selection, {"selected_work_ids": selected, "full": True, "completed_native_count": len(selected),
            "max_review_concurrency": 2, "max_group_works": 5, "recovery_lineage": lineage})
        args = {"runtime_root": str(Path(runtime) / "runtime"),
            **{field: actual[field] for field in ("ordered_inputs", "run_dir", "execution_context_manifest", "terminal_proof")},
            "ledger": actual["usage_ledger"], "ledger_sha256": sha(actual["usage_ledger"]),
            "output": str(output), "selection_path": str(selection),
            "timeout": min(900, max(1, int(remaining))), "max_review_concurrency": 2}
        code = ('import json,sys; from pathlib import Path; sys.path.insert(0,str(Path.cwd()/"scripts")); '
            'from recover_world_novel_expansion_v1 import _review_child; '
            'print(json.dumps(_review_child(json.loads(sys.argv[1])),ensure_ascii=True))')
        result = frozen_call(runtime, code, [json.dumps(args)])
        verify()
        result.update(selection_path=str(selection), selection_sha256=sha(selection), full=True,
            reviewed_work_count=len(selected), max_review_concurrency=2)
        return result
    return review


def execute_fresh(runtime, window, pins, *, base, lineage=None):
    """Explicit model entry; the caller creates/binds a permanent reservation."""
    checked(v1.__file__, V1_SHA)
    pins = [Path(path).resolve(strict=True) for path in pins]
    pins.extend((Path(__file__).resolve(), Path(v1.__file__).resolve(), Path(runner.__file__).resolve()))
    pins.extend(Path(__file__).with_name("review_scale_content_v" + str(i) + ".py") for i in range(1, 7))
    hashes = {str(path): sha(path) for path in pins}
    def verify():
        for path, digest in hashes.items():
            checked(path, digest)
        checked(Path(window["source_path"]) / "manifest.json", window["source_sha256"])
        checked(Path(window["source_path"]) / "work-mapping.json", window["mapping_sha256"])
        if lineage:
            check_unstarted(lineage["plan"], lineage["plan_sha256"], window["source_path"], window["source_sha256"])
    def reservation(source, number):
        verify()
        if Path(source).resolve() != Path(window["source_path"]).resolve() or number != window["window_index"]:
            raise ValueError("Actual source differs from fresh reserved recovery/normal scope")
        return pins
    recipe = runner.load_coordinator(runtime, reservation, base=base)
    sequence = 0
    def frozen(runtime, code, arguments):
        nonlocal sequence
        sequence += 1
        return v1._observed_call(runtime, code, arguments,
            Path(base) / "execution" / window["run_name"] / "stages" / f"{sequence:02d}", verify=verify)
    recipe.frozen_call = frozen
    recipe.review_content_window = low_concurrency_review(base, frozen, verify=verify, lineage=lineage)
    stages = Path(base) / "execution" / window["run_name"] / "stages"
    recipe.stage_process_pins = lambda: [*pins, *sorted(path for path in stages.glob("*/*") if path.is_file())]
    original_save = recipe.save
    def export_save(path, document):
        if document.get("version") == "scale-content-native-classification-export-v1":
            document.update(explicit_scheduler={"path": str(Path(__file__).resolve()), "sha256": sha(__file__),
                "classification_concurrency": 2, "review_concurrency": 2, "frozen_recipe_bytes_changed": False})
            if lineage:
                document["recovery_lineage"] = lineage
        return original_save(path, document)
    recipe.save = export_save
    result = recipe.execute_window(window["source_path"], runtime, window["window_index"])
    verify()
    return result


def _run(runtime, window, pins, *, base, lineage=None, prior_control=None, prior_sha=None, continuation=None):
    base, runtime = Path(base).resolve(), Path(runtime).resolve(strict=True)
    require_fresh_execution(runtime, window, base)
    if not Path(window["source_path"]).resolve().is_relative_to(base):
        raise ValueError("Source must stay inside explicit world namespace")
    attempt = base / "execution" / window["run_name"]
    attempt.mkdir(parents=True)
    request = attempt / "request.json"
    save(request, {"version": "world-novel-explicit-two-slot-attempt-v1", "source": window["source_path"],
        "source_sha256": window["source_sha256"], "classification_concurrency": 2, "review_concurrency": 2,
        "scheduler": str(Path(__file__).resolve()), "scheduler_sha256": sha(__file__),
        "original_native_coordinator": str(Path(runner.__file__).resolve()), "original_native_coordinator_sha256": sha(runner.__file__),
        "recovery_lineage": lineage, "explicit_finite_continuation": continuation,
        "started_at": datetime.now(timezone.utc).isoformat(), "catalogue_writes": 0})
    reservation = attempt / "reservation.json"
    save(reservation, {**window, "recovery_lineage": lineage})
    pins = [*pins, request, reservation, Path(window["source_path"]) / "manifest.json", Path(window["source_path"]) / "work-mapping.json"]
    runner.prepare_window(runtime, window, 2, prior_control=prior_control, prior_control_sha256=prior_sha)
    result = execute_fresh(runtime, window, pins, base=base, lineage=lineage)
    save(attempt / "result.json", result)
    if result.get("stage") != "actual_native_export_verified":
        raise ValueError("Native window did not reach verified export; preserve without retry")
    public = read(checked(result["export_manifest"], result["export_manifest_sha256"]))
    gate = runner.export_gate(public, read(public["run_manifest"]))
    receipt = {"version": "world-novel-expansion-window-v1", "export_path": result["export_manifest"],
        "export_sha256": result["export_manifest_sha256"], "gate": gate, "recovery_lineage": lineage,
        "catalogue_publication_pending": True, "human_quality_verified": False, "old_classifications_modified": False}
    save(attempt / "gate.json", receipt)
    if not gate["passed"]:
        raise ValueError("Fresh scope actual gate failed; never retry or relabel original54")
    return receipt


def run_unstarted(plan, plan_sha, source, source_sha, runtime, *, base=runner.BASE):
    window, lineage = check_unstarted(plan, plan_sha, source, source_sha)
    service = service_recovery_evidence()
    pins = [Path(plan).resolve(), SERVICE_MANIFEST, SERVICE_EXIT, *map(Path, service["artifact_sha256"])]
    continuation = {"root_authorized_finite_continuation": True, "original_gate_failed": True,
        "plan": str(Path(plan).resolve()), "plan_sha256": plan_sha,
        "original_counts": lineage["original_counts"], "service_recovery": service,
        "salvage24_status": "separate_review_complete_adoption_held_original_pending22",
        "salvage_adoption_claimed": False, "max_fresh_classification_calls": window["selected"],
        "failed_inputs_retry_allowed": False, "quality_gate_waived": False}
    return _run(runtime, window, pins, base=base, lineage=lineage, continuation=continuation)


def run_window(index, index_sha, runtime, number, prior_control, prior_sha, *, base=runner.BASE,
               recovery_checkpoint=None, recovery_checkpoint_sha=None):
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    windows = runner.load_sources(index, index_sha, base=base)
    if type(number) is not int or not 1 <= number < len(windows):
        raise ValueError("Explicit continuation requires a present later source window")
    prior_path = Path(prior_control).resolve(strict=True)
    prior = verify_control_receipt(prior_path, prior_sha)
    if not prior_path.is_relative_to(Path(base).resolve()) or prior["window_index"] != number - 1:
        raise ValueError("Prior actual passed continuation scope/order differs")
    public = read(prior["control_artifacts"]["export"]["path"])
    pins = [Path(index).resolve(), prior_path]
    if public.get("recovery_lineage"):
        if recovery_checkpoint is None or recovery_checkpoint_sha is None:
            raise ValueError("Fresh recovery continuation requires explicit full recovery checkpoint")
        checkpoint = verify_checkpoint(recovery_checkpoint, recovery_checkpoint_sha)
        if checkpoint["fresh_control_sha256"] != prior_sha or Path(checkpoint["fresh_control"]).resolve() != prior_path:
            raise ValueError("Recovery checkpoint differs from actual prior fresh control")
        pins.append(Path(recovery_checkpoint).resolve())
    exclusions = read(index)["exclusions"]
    pins.append(checked(exclusions["path"], exclusions["sha256"]))
    return _run(runtime, windows[number], pins, base=base, prior_control=prior_path, prior_sha=prior_sha)


def failed_control_source(source):
    """Keep every v4 binding check; explicitly require a FAILED original gate."""
    tree = ast.parse(source)
    matches = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and any(isinstance(item, ast.Raise)
                and "Actual content continuation gate failed or differs" in ast.unparse(item) for item in node.body)):
            matches.append(node)
    if len(matches) != 1 or ast.unparse(matches[0].test) != "gate != receipt['gate'] or not gate['passed'] or (not public['gate']['passed'])":
        raise ValueError("Exact v4 failed-original verification boundary differs")
    matches[0].test = ast.parse("gate != receipt['gate'] or gate['passed'] or public['gate']['passed']", mode="eval").body
    return ast.unparse(ast.fix_missing_locations(tree))


def verify_failed_control(path, digest):
    from recommend_book import scale_content_control_v4 as original
    source_path = checked(original.__file__, CONTROL_V4_SHA)
    if read(checked(path, digest)).get("version") != "scale-content-window-control-v4":
        raise ValueError("Actual v4 failed-original control required")
    module = ModuleType("explicit_failed_original_control_only")
    module.__file__ = str(source_path)
    exec(compile(failed_control_source(source_path.read_bytes()), str(source_path), "exec"), module.__dict__)
    return module.verify_control_receipt(path, digest)


def _checkpoint_evidence(plan, plan_sha, salvage_control, salvage_sha, fresh_control, fresh_sha):
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    import publish_world_novel_expansion_v1 as publisher
    value = v1.verify_recovery_plan(plan, plan_sha)
    salvage = verify_failed_control(salvage_control, salvage_sha)
    fresh = verify_control_receipt(fresh_control, fresh_sha)
    sal_export = checked(**{ "path": salvage["control_artifacts"]["export"]["path"],
                            "digest": salvage["control_artifacts"]["export"]["sha256"]})
    new_export = checked(**{ "path": fresh["control_artifacts"]["export"]["path"],
                            "digest": fresh["control_artifacts"]["export"]["sha256"]})
    sal_public, new_public = read(sal_export), read(new_export)
    publisher.verify_policy_control(salvage, sal_public, sha(sal_export))
    publisher.verify_policy_control(fresh, new_public, sha(new_export))
    completed = {(row["qid"], row["work_id"], row["input_sha256"]) for row in value["completed"]}
    unstarted = {(row["qid"], row["work_id"], row["input_sha256"]) for row in value["unstarted"]}
    sal_rows = {(row["qid"], row["input_work_id"], row["input_hash"]) for row in sal_public["verified_projection"]["rows"]}
    new_rows = {(row["qid"], row["input_work_id"], row["input_hash"]) for row in new_public["verified_projection"]["rows"]}
    original = read(Path(value["run"]) / "manifest.json")
    fresh_run = read(fresh["control_artifacts"]["run"]["path"])
    if (sal_rows != completed or not new_rows or not new_rows <= unstarted
            or sal_public.get("recovery", {}).get("plan_sha256") != plan_sha
            or new_public.get("recovery_lineage", {}).get("plan_sha256") != plan_sha
            or salvage["run_id"] != original["run_id"] or fresh["run_id"] == original["run_id"]
            or fresh_run["selected"] != len(unstarted)
            or fresh["window_index"] != salvage["window_index"]
            or sal_public["gate"].get("passed") is not False or new_public["gate"].get("passed") is not True):
        raise ValueError("Recovery checkpoint cannot merge, retry, or relabel distinct original/fresh scopes")
    pins = {str(Path(path).resolve()): sha(path) for path in (plan, salvage_control, fresh_control, __file__, v1.__file__, publisher.__file__)}
    for control in (salvage, fresh):
        pins.update({pin["path"]: pin["sha256"] for pin in control["control_artifacts"].values()})
    return {"version": CHECKPOINT, "plan": str(Path(plan).resolve()), "plan_sha256": plan_sha,
        "original_counts": value["original_counts"], "original_gate_failed": True,
        "salvaged_completed_count": len(sal_rows), "fresh_selected": len(unstarted), "fresh_accepted": len(new_rows),
        "failed_original_never_retried": len(value["failed"]), "fresh_gate": fresh["gate"],
        "salvage_control": str(Path(salvage_control).resolve()), "salvage_control_sha256": salvage_sha,
        "fresh_control": str(Path(fresh_control).resolve()), "fresh_control_sha256": fresh_sha,
        "next_window_index": fresh["window_index"] + 1, "max_next_classification_concurrency": 2,
        "max_next_review_concurrency": 2, "next_review_mode": "full", "artifact_sha256": pins,
        "original54_success_claimed": False, "human_quality_verified": False}


def create_checkpoint(plan, plan_sha, salvage_control, salvage_sha, fresh_control, fresh_sha, output):
    result = _checkpoint_evidence(plan, plan_sha, salvage_control, salvage_sha, fresh_control, fresh_sha)
    save(output, result)
    return Path(output).resolve()


def verify_checkpoint(path, digest):
    value = read(checked(path, digest))
    if value.get("version") != CHECKPOINT:
        raise ValueError("Explicit recovery checkpoint required")
    for artifact, expected in value["artifact_sha256"].items():
        checked(artifact, expected)
    expected = _checkpoint_evidence(value["plan"], value["plan_sha256"], value["salvage_control"],
        value["salvage_control_sha256"], value["fresh_control"], value["fresh_control_sha256"])
    if expected != value:
        raise ValueError("Recovery checkpoint differs from actual failed-original and fresh-scope evidence")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("check-unstarted", "run-unstarted"):
        child = sub.add_parser(command)
        for flag in ("plan", "source"):
            child.add_argument("--" + flag, type=Path, required=True)
            child.add_argument("--" + flag + "-sha256", required=True)
        if command == "run-unstarted":
            child.add_argument("--runtime", type=Path, default=runner.fixed.RUNTIME)
            child.add_argument("--base", type=Path, default=runner.BASE)
    child = sub.add_parser("run-window")
    child.add_argument("--source-index", type=Path, required=True)
    child.add_argument("--source-index-sha256", required=True)
    child.add_argument("--window-index", type=int, required=True)
    child.add_argument("--runtime", type=Path, default=runner.fixed.RUNTIME)
    child.add_argument("--base", type=Path, default=runner.BASE)
    child.add_argument("--prior-control", type=Path, required=True)
    child.add_argument("--prior-control-sha256", required=True)
    child.add_argument("--recovery-checkpoint", type=Path)
    child.add_argument("--recovery-checkpoint-sha256")
    child = sub.add_parser("checkpoint")
    for flag in ("plan", "salvage-control", "fresh-control"):
        child.add_argument("--" + flag, type=Path, required=True)
        child.add_argument("--" + flag + "-sha256", required=True)
    child.add_argument("--output", type=Path, required=True)
    child = sub.add_parser("check-checkpoint")
    child.add_argument("--checkpoint", type=Path, required=True)
    child.add_argument("--checkpoint-sha256", required=True)
    args = parser.parse_args()
    if args.command == "check-unstarted":
        result, _ = check_unstarted(args.plan, args.plan_sha256, args.source, args.source_sha256)
    elif args.command == "run-unstarted":
        result = run_unstarted(args.plan, args.plan_sha256, args.source, args.source_sha256, args.runtime, base=args.base)
    elif args.command == "run-window":
        result = run_window(args.source_index, args.source_index_sha256, args.runtime, args.window_index,
            args.prior_control, args.prior_control_sha256, base=args.base,
            recovery_checkpoint=args.recovery_checkpoint, recovery_checkpoint_sha=args.recovery_checkpoint_sha256)
    elif args.command == "checkpoint":
        path = create_checkpoint(args.plan, args.plan_sha256, args.salvage_control, args.salvage_control_sha256,
            args.fresh_control, args.fresh_control_sha256, args.output)
        result = {"path": str(path), "sha256": sha(path)}
    else:
        result = verify_checkpoint(args.checkpoint, args.checkpoint_sha256)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
