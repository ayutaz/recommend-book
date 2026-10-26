"""Thin continuation after a passed fresh22 gate while original24 adoption waits.

This is process continuity, not permission to publish any original stopped-run
output. It authenticates the saved24 review and failed adoption boundary, keeps
the original54 gate false, and delegates every new model/guard step to v2.
After this next normal window passes, ordinary v2 continuation is sufficient.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import recover_world_novel_expansion_v2 as v2
from recommend_book.scale_content_control_v4 import verify_control_receipt as verify_control

sha, read, save, checked = v2.sha, v2.read, v2.save, v2.checked
V2_SHA = "89c431e7e06bcbb46608906e79d5cee565abc24d29b4140c6aa27158882162ef"
VERSION = "world-novel-pending-salvage-continuation-v1"


def pending_review_evidence(value, plan_sha, review_receipt, review_sha, failed_tail, exit_sha):
    receipt = read(checked(review_receipt, review_sha))
    selected = read(checked(receipt["selection_path"], receipt["selection_sha256"]))["selected_work_ids"]
    wanted = {row["work_id"] for row in value["completed"]}
    document_path = Path(receipt["review_path"])
    document = read(document_path)
    proof = read(Path(value["terminal"]) / "terminal-proof.json")
    bindings = {"run_id": value["original_run_id"], **{key: proof[key] for key in (
        "ordered_inputs_sha256", "results_sha256", "execution_context_manifest_sha256")},
        "run_manifest_sha256": proof["manifest_sha256"]}
    if (receipt.get("recovery_plan_sha256") != plan_sha or receipt.get("full") is not True
            or receipt.get("reviewed_work_count") != len(wanted) or len(selected) != len(wanted)
            or set(selected) != wanted or any(document[key] != expected for key, expected in bindings.items())):
        raise ValueError("Exact saved original-completed review evidence required")
    pins = {str(Path(path).resolve()): sha(path) for path in (review_receipt, receipt["selection_path"], document_path)}
    covered = []
    for binding in document["source_audits"]:
        audit_path = checked(binding["path"], binding["sha256"])
        pins[str(audit_path)] = binding["sha256"]
        audit = read(audit_path)
        if audit.get("run_id") != value["original_run_id"] or audit.get("exit_code") != 0:
            raise ValueError("Saved independent review belongs to another or failed native run")
        covered.extend(row["work_id"] for row in audit["reviews"])
        for path, digest in audit["source_sha256"].items():
            pins[str(checked(path, digest))] = digest
        for relative, digest in audit["artifact_sha256"].items():
            pins[str(checked(audit_path.parent / relative, digest))] = digest
    if len(covered) != len(wanted) or set(covered) != wanted:
        raise ValueError("Saved24 review has duplicate or missing actual completed work")
    process = Path(review_receipt).with_name(Path(review_receipt).name.removesuffix(".result.json") + ".process")
    review_exit = read(process / "process-exit.json")
    failed_tail = Path(failed_tail)
    failure = read(checked(failed_tail / "process-exit.json", exit_sha))
    if (review_exit.get("actual_exit_code") != 0 or review_exit.get("process_exit_observed") is not True
            or failure.get("actual_exit_code") != 1 or failure.get("process_exit_observed") is not True
            or "Run is not terminal for exact selected input/config" not in (failed_tail / "stderr.txt").read_text(encoding="utf-8")):
        raise ValueError("Actual saved review success and original pending-run adoption rejection required")
    for directory in (process, failed_tail):
        pins.update({str(path.resolve()): sha(path) for path in directory.iterdir() if path.is_file()})
    return pins


def checkpoint_evidence(plan, plan_sha, review_receipt, review_sha, failed_tail, exit_sha, fresh_control, fresh_sha):
    checked(v2.__file__, V2_SHA)
    value = v2.v1.verify_recovery_plan(plan, plan_sha)
    pins = pending_review_evidence(value, plan_sha, review_receipt, review_sha, failed_tail, exit_sha)
    control = verify_control(fresh_control, fresh_sha)
    public = read(checked(control["control_artifacts"]["export"]["path"], control["control_artifacts"]["export"]["sha256"]))
    run = read(checked(control["control_artifacts"]["run"]["path"], control["control_artifacts"]["run"]["sha256"]))
    original_source = read(Path(value["source"]) / "manifest.json")
    wanted = {(row["qid"], row["work_id"], row["input_sha256"]) for row in value["unstarted"]}
    accepted = {(row["qid"], row["input_work_id"], row["input_hash"]) for row in public["verified_projection"]["rows"]}
    identities = {(row["work_id"], row["input_hash"]) for row in run["input_identities"]}
    if (public.get("recovery_lineage", {}).get("plan_sha256") != plan_sha or not accepted or not accepted <= wanted
            or run["selected"] != len(wanted) or identities != {(row[1], row[2]) for row in wanted}
            or control["gate"].get("passed") is not True
            or control["window_index"] != original_source["window_index"]
            or control.get("run_id") == value["original_run_id"]):
        raise ValueError("Actual passed fresh scope must contain only original never-submitted inputs")
    pins.update(value["artifact_sha256"])
    pins.update({pin["path"]: pin["sha256"] for pin in control["control_artifacts"].values()})
    pins.update({str(Path(path).resolve()): sha(path) for path in (plan, review_receipt, fresh_control, __file__, v2.__file__, v2.v1.__file__)})
    return {"version": VERSION, "plan": str(Path(plan).resolve()), "plan_sha256": plan_sha,
        "review_receipt": str(Path(review_receipt).resolve()), "review_sha256": review_sha,
        "failed_tail": str(Path(failed_tail).resolve()), "failed_tail_exit_sha256": exit_sha,
        "fresh_control": str(Path(fresh_control).resolve()), "fresh_control_sha256": fresh_sha,
        "original_counts": value["original_counts"], "original_gate_failed": True,
        "salvage_completed_separately_reviewed": len(value["completed"]),
        "salvage_adoption_claimed": False, "salvage_status": "held_original_pending_run_import_boundary",
        "failed_original_never_retried": len(value["failed"]), "fresh_gate": control["gate"],
        "next_window_index": control["window_index"] + 1, "next_review_mode": "full",
        "max_next_classification_concurrency": 2, "max_next_review_concurrency": 2,
        "artifact_sha256": pins, "human_quality_verified": False}


def create_checkpoint(plan, plan_sha, review_receipt, review_sha, failed_tail, exit_sha, fresh_control, fresh_sha, output):
    value = checkpoint_evidence(plan, plan_sha, review_receipt, review_sha, failed_tail, exit_sha, fresh_control, fresh_sha)
    save(output, value)
    return Path(output).resolve()


def verify_checkpoint(path, digest):
    value = read(checked(path, digest))
    if value.get("version") != VERSION:
        raise ValueError("Pending-salvage process checkpoint required")
    for artifact, expected in value["artifact_sha256"].items():
        checked(artifact, expected)
    expected = checkpoint_evidence(value["plan"], value["plan_sha256"], value["review_receipt"], value["review_sha256"],
        value["failed_tail"], value["failed_tail_exit_sha256"], value["fresh_control"], value["fresh_control_sha256"])
    if expected != value:
        raise ValueError("Pending salvage checkpoint differs from actual failed/successful scopes")
    return value


def run_next(checkpoint, checkpoint_sha, index, index_sha, runtime, *, base=v2.runner.BASE):
    value = verify_checkpoint(checkpoint, checkpoint_sha)
    windows = v2.runner.load_sources(index, index_sha, base=base)
    number = value["next_window_index"]
    if not 1 <= number < len(windows):
        raise ValueError("Next actual source window is absent from sealed independent index")
    exclusions = read(index)["exclusions"]
    pins = [Path(checkpoint).resolve(), Path(index).resolve(), Path(__file__).resolve(),
            checked(exclusions["path"], exclusions["sha256"]), *map(Path, value["artifact_sha256"])]
    return v2._run(runtime, windows[number], pins, base=base,
        prior_control=Path(value["fresh_control"]), prior_sha=value["fresh_control_sha256"],
        continuation={"pending_salvage_checkpoint": str(Path(checkpoint).resolve()), "checkpoint_sha256": checkpoint_sha,
            "original_gate_failed": True, "salvage_adoption_claimed": False, "quality_gate_waived": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    child = sub.add_parser("checkpoint")
    for flag in ("plan", "review-receipt", "fresh-control"):
        child.add_argument("--" + flag, type=Path, required=True)
        child.add_argument("--" + flag + "-sha256", required=True)
    child.add_argument("--failed-tail", type=Path, required=True)
    child.add_argument("--failed-tail-exit-sha256", required=True)
    child.add_argument("--output", type=Path, required=True)
    for command in ("check", "run-next"):
        child = sub.add_parser(command)
        child.add_argument("--checkpoint", type=Path, required=True)
        child.add_argument("--checkpoint-sha256", required=True)
        if command == "run-next":
            child.add_argument("--source-index", type=Path, required=True)
            child.add_argument("--source-index-sha256", required=True)
            child.add_argument("--runtime", type=Path, default=v2.runner.fixed.RUNTIME)
            child.add_argument("--base", type=Path, default=v2.runner.BASE)
    args = parser.parse_args()
    if args.command == "checkpoint":
        path = create_checkpoint(args.plan, args.plan_sha256, args.review_receipt, args.review_receipt_sha256,
            args.failed_tail, args.failed_tail_exit_sha256, args.fresh_control, args.fresh_control_sha256, args.output)
        result = {"path": str(path), "sha256": sha(path)}
    elif args.command == "check":
        result = verify_checkpoint(args.checkpoint, args.checkpoint_sha256)
    else:
        result = run_next(args.checkpoint, args.checkpoint_sha256, args.source_index, args.source_index_sha256, args.runtime, base=args.base)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
