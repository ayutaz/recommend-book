"""Read-only partial recovery verification for a stopped world window.

This module deliberately sits beside the normal terminal importer.  A stopped
54-input run is not turned into a terminal run by filtering its manifest.  The
module verifies the immutable 54/24/8/22 partition, rechecks the completed
native outputs through the frozen v4 verifier and returns a separately named
public projection.  It never writes a database, retries a model call, or
changes a source artifact.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Iterator


EXPORT_VERSION = "world-novel-partial-recovery-export-v1"
PLAN_VERSION = "world-novel-expansion-recovery-plan-v1"
QUALITY_VERSION = "next-quality-adoption-review-v1"


class PartialRecoveryError(ValueError):
    """Raised when a recovery artifact is not an exact immutable projection."""


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def value_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialRecoveryError(f"Invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PartialRecoveryError(f"JSON object required: {path}")
    return value


def checked(path: str | Path, digest: str) -> Path:
    path = Path(path).resolve(strict=True)
    if not isinstance(digest, str) or len(digest) != 64 or sha256(path) != digest:
        raise PartialRecoveryError("Immutable artifact SHA differs: " + str(path))
    return path


def _bool(value: Any, name: str) -> None:
    if type(value) is not bool:
        raise PartialRecoveryError(f"{name} must be a boolean")


def _count(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise PartialRecoveryError(f"{name} must be a nonnegative integer")
    return value


def _inventory(root: Path) -> dict[str, str]:
    root = root.resolve(strict=True)
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise PartialRecoveryError("Review artifact escapes its namespace")
            result[str(resolved)] = sha256(resolved)
    return result


def _artifact_by_suffix(plan: dict[str, Any], suffix: str) -> tuple[Path, str]:
    matches = [(Path(path), digest) for path, digest in plan["artifact_sha256"].items()
               if Path(path).as_posix().endswith(suffix.replace("\\", "/"))]
    if len(matches) != 1:
        raise PartialRecoveryError(f"Expected one pinned artifact ending in {suffix}: {len(matches)}")
    return matches[0]


def validate_partition(plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete original partition without declaring it terminal."""
    if plan.get("version") != PLAN_VERSION:
        raise PartialRecoveryError("World recovery plan version differs")
    if plan.get("original_gate_failed") is not True:
        raise PartialRecoveryError("Partial recovery requires the original failed gate")
    if plan.get("never_retry_failed") is not True or plan.get("never_reclassify_completed") is not True:
        raise PartialRecoveryError("Recovery retry/reclassification prohibition is absent")
    counts = plan.get("original_counts")
    if not isinstance(counts, dict):
        raise PartialRecoveryError("Original run counts are required")
    selected = _count(counts.get("selected"), "selected")
    completed_n = _count(counts.get("completed"), "completed")
    failed_n = _count(counts.get("failed"), "failed")
    pending_n = _count(counts.get("pending"), "pending")
    if selected != completed_n + failed_n + pending_n or not completed_n:
        raise PartialRecoveryError("Original 54-way partition counts are inconsistent")
    if _count(counts.get("cli_invocations"), "cli_invocations") != completed_n + failed_n:
        raise PartialRecoveryError("Original CLI invocation count differs from attempted partition")

    groups = {"completed": plan.get("completed"), "failed": plan.get("failed"), "unstarted": plan.get("unstarted")}
    if any(not isinstance(rows, list) for rows in groups.values()):
        raise PartialRecoveryError("All original partition rows are required")
    by_work: dict[str, str] = {}
    by_input: dict[str, str] = {}
    by_job: dict[str, str] = {}
    expected_status = {"completed": "completed", "failed": "held", "unstarted": "not_submitted"}
    for group, rows in groups.items():
        for row in rows:
            required = {"job_key", "work_id", "input_sha256", "qid", "original_status"}
            if not required <= set(row) or any(not isinstance(row[key], str) or not row[key] for key in required):
                raise PartialRecoveryError("Partition row identity is incomplete")
            if row["original_status"] != expected_status[group]:
                raise PartialRecoveryError("Partition row status differs from original manifest")
            for index, table in (("work_id", by_work), ("input_sha256", by_input), ("job_key", by_job)):
                if row[index] in table:
                    raise PartialRecoveryError("Duplicate original partition identity: " + index)
                table[row[index]] = group
    if len(groups["completed"]) != completed_n or len(groups["failed"]) != failed_n or len(groups["unstarted"]) != pending_n:
        raise PartialRecoveryError("Original partition rows do not match counts")
    if len(by_work) != selected or len(by_input) != selected or len(by_job) != selected:
        raise PartialRecoveryError("Original partition is not a complete disjoint selection")
    return {"counts": {"selected": selected, "completed": completed_n, "failed": failed_n, "pending": pending_n,
                       "cli_invocations": counts["cli_invocations"]},
            "groups": groups, "by_work": by_work, "by_input": by_input, "by_job": by_job}


def _strict_quality_shape(document: dict[str, Any]) -> None:
    required = {"schema_version", "actor", "run_id", "ordered_inputs_sha256", "run_manifest_sha256",
                "results_sha256", "execution_context_manifest_sha256", "source_audits", "cases",
                "human_gold", "authority_merge_approved", "confidence_calibrated"}
    if set(document) != required or document["schema_version"] != QUALITY_VERSION or document["actor"] != "ai_internal_review":
        raise PartialRecoveryError("Quality review schema differs")
    for key in ("human_gold", "authority_merge_approved", "confidence_calibrated"):
        if document[key] is not False:
            raise PartialRecoveryError("Quality review authority flag must remain false: " + key)
    audits = document["source_audits"]
    if not isinstance(audits, list) or not audits:
        raise PartialRecoveryError("Quality source audits are required")
    audit_hashes = set()
    for audit in audits:
        if set(audit) != {"path", "sha256"} or not Path(audit["path"]).is_absolute():
            raise PartialRecoveryError("Quality source audit binding is invalid")
        checked(audit["path"], audit["sha256"])
        if audit["sha256"] in audit_hashes:
            raise PartialRecoveryError("Duplicate quality source audit")
        audit_hashes.add(audit["sha256"])
    if not isinstance(document["cases"], list):
        raise PartialRecoveryError("Quality cases list is required")
    seen = set()
    fields = {"work_id", "input_hash", "job_key", "native_result_value_sha256", "status", "reason_codes",
              "reasons", "label_ids", "source_audit_sha256", "human_gold", "authority_merge_approved",
              "confidence_calibrated"}
    allowed_reasons = {"evidence_subject_scope_mismatch", "unresolved_version_scope_conflict", "semantic_entailment_uncertain"}
    for case in document["cases"]:
        if set(case) != fields or any(case[key] is not False for key in ("human_gold", "authority_merge_approved", "confidence_calibrated")):
            raise PartialRecoveryError("Quality case schema/authority flags differ")
        for key in ("work_id", "input_hash", "job_key", "native_result_value_sha256", "status"):
            if not isinstance(case[key], str) or not case[key]:
                raise PartialRecoveryError("Quality case identity is invalid")
        if case["job_key"] in seen:
            raise PartialRecoveryError("Duplicate quality case")
        seen.add(case["job_key"])
        for key in ("reason_codes", "reasons", "label_ids", "source_audit_sha256"):
            values = case[key]
            if not isinstance(values, list) or not values or len(values) != len(set(values)) or any(not isinstance(v, str) or not v for v in values):
                raise PartialRecoveryError("Quality case list is invalid")
        if not set(case["reason_codes"]) <= allowed_reasons or not set(case["source_audit_sha256"]) <= audit_hashes:
            raise PartialRecoveryError("Quality case audit/reason binding differs")
        expected_status = "held" if set(case["reason_codes"]) - {"semantic_entailment_uncertain"} else "uncertain"
        if case["status"] != expected_status:
            raise PartialRecoveryError("Quality case status differs from reason codes")


def validate_review(plan: dict[str, Any], plan_path: Path, review_result_path: Path) -> dict[str, Any]:
    review_result_path = review_result_path.resolve(strict=True)
    review = read_json(review_result_path)
    expected_sha = sha256(plan_path)
    if review.get("recovery_plan_sha256") != expected_sha or review.get("full") is not True or review.get("original_gate_failed") is not True:
        raise PartialRecoveryError("Review is not bound to this failed recovery plan")
    completed = {row["work_id"] for row in plan["completed"]}
    if review.get("reviewed_work_count") != len(completed) or review.get("reviewed_n") != len(completed):
        raise PartialRecoveryError("Review does not cover every completed native output")
    selection_path = checked(review["selection_path"], review["selection_sha256"])
    selection = read_json(selection_path)
    if set(selection.get("selected_work_ids", [])) != completed or len(selection.get("selected_work_ids", [])) != len(completed):
        raise PartialRecoveryError("Review selection differs from completed partition")
    quality_path = Path(review["review_path"]).resolve(strict=True)
    quality = read_json(quality_path)
    _strict_quality_shape(quality)
    if quality.get("run_id") != plan["original_run_id"]:
        raise PartialRecoveryError("Quality review run identity differs")
    completed_jobs = {row["job_key"] for row in plan["completed"]}
    if any(case["job_key"] not in completed_jobs for case in quality["cases"]):
        raise PartialRecoveryError("Quality case belongs outside the completed partition")
    ordered_path = Path(plan["source"]) / "ordered-inputs.jsonl"
    source_manifest = read_json(Path(plan["source"]) / "manifest.json")
    ordered_path = Path(source_manifest["chunks"][0]["inputs_path"]).resolve(strict=True)
    run = Path(plan["run"])
    if quality.get("ordered_inputs_sha256") != sha256(ordered_path) or quality.get("run_manifest_sha256") != sha256(run / "manifest.json") or quality.get("results_sha256") != sha256(run / "results.jsonl"):
        raise PartialRecoveryError("Quality review source hashes differ from original run")
    terminal = Path(plan["terminal"])
    arguments = read_json(terminal / "adoption-arguments.json")
    context = Path(arguments["execution_context_manifest"]).resolve(strict=True)
    if quality.get("execution_context_manifest_sha256") != sha256(context):
        raise PartialRecoveryError("Quality review context hash differs")
    return {"result": review, "selection": selection, "quality": quality,
            "review_result_path": str(review_result_path), "review_result_sha256": sha256(review_result_path),
            "selection_path": str(selection_path), "selection_sha256": sha256(selection_path),
            "quality_path": str(quality_path), "quality_sha256": sha256(quality_path)}


@contextmanager
def runtime_imports(runtime_root: str | Path) -> Iterator[dict[str, Any]]:
    """Import the frozen runtime in a clean module path for native verification."""
    root = Path(runtime_root).resolve(strict=True)
    runtime = root / "runtime" if (root / "runtime").is_dir() else root
    source = runtime / "src"
    scripts = runtime / "scripts"
    if not source.is_dir() or not scripts.is_dir():
        raise PartialRecoveryError("Frozen runtime src/scripts directories are required")
    loaded = sys.modules.get("recommend_book")
    if loaded is not None and getattr(loaded, "__file__", "") and not str(loaded.__file__).startswith(str(source)):
        raise PartialRecoveryError("Native verification requires a clean frozen-runtime interpreter")
    old_path = list(sys.path)
    sys.path[:0] = [str(scripts), str(source)]
    try:
        helper_spec = importlib.util.spec_from_file_location("partial_recovery_import_helpers", scripts / "import_classification_database.py")
        if helper_spec is None or helper_spec.loader is None:
            raise PartialRecoveryError("Frozen import helper cannot be loaded")
        helper = importlib.util.module_from_spec(helper_spec)
        helper_spec.loader.exec_module(helper)
        next_db = importlib.import_module("recommend_book.next_database_v4")
        usage = importlib.import_module("recommend_book.usage_guard_v4")
        native = importlib.import_module("recommend_book.classification.native_attempt_v4")
        yield {"helper": helper, "next_db": next_db, "usage": usage, "native": native, "runtime": runtime}
    finally:
        sys.path[:] = old_path


def _validate_partial_manifest(plan: dict[str, Any], partition: dict[str, Any], helper: Any, proof: dict[str, Any], manifest: dict[str, Any], run: Path, works: list[Any], config: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("run_id") != plan["original_run_id"] or proof.get("run_id") != manifest.get("run_id"):
        raise PartialRecoveryError("Original run identity differs")
    counts = partition["counts"]
    for key in ("selected", "completed", "failed", "pending"):
        if manifest.get(key) != counts[key]:
            raise PartialRecoveryError("Original run count differs: " + key)
    if manifest.get("stopped") is not True or manifest.get("human_quality_verified") is not False or manifest.get("pending") == 0:
        raise PartialRecoveryError("Partial recovery requires an actually stopped nonterminal run")
    states = manifest.get("jobs")
    if not isinstance(states, dict) or len(states) != len(works):
        raise PartialRecoveryError("Original durable job inventory differs")
    by_work = {work.work_id: work for work in works}
    job_key_fn = importlib.import_module("recommend_book.jobs").job_key
    durable: dict[str, dict[str, Any]] = {}
    for key, state in states.items():
        work = by_work.get(state.get("work_id"))
        if work is None or job_key_fn(helper.input_hash(work), config) != key:
            raise PartialRecoveryError("Original job identity/config differs")
        group = partition["by_work"].get(work.work_id)
        expected_status = {"completed": "completed", "failed": "held", "unstarted": "not_submitted"}[group]
        if state.get("status") != expected_status:
            raise PartialRecoveryError("Original durable job state differs")
        if group == "unstarted":
            if (run / "jobs" / (key + ".json")).exists() or list((run / "attempts").glob(key + ".*")):
                raise PartialRecoveryError("Unstarted input has an actual request/attempt")
            continue
        if helper.read_json(helper.contained(run, "jobs/" + key + ".json")) != state:
            raise PartialRecoveryError("Original durable job state differs")
        if group == "completed":
            if state.get("config") != config:
                raise PartialRecoveryError("Completed job config differs")
            result_path = helper.contained(run, state.get("result_path"))
            result = helper.read_json(result_path)
            if result.get("input_hash") != helper.input_hash(work) or result.get("work_id") != work.work_id:
                raise PartialRecoveryError("Completed native result identity differs")
            durable[key] = {"work": work, "state": state, "result": result, "result_path": result_path}
        elif state.get("result_path") or (run / "results" / (key + ".json")).exists():
            raise PartialRecoveryError("Noncompleted job has a result artifact")
    results = [helper.strict_json(line) for line in (run / "results.jsonl").read_bytes().splitlines() if line.strip()]
    ordered = [durable[key]["result"] for key in (job_key_fn(helper.input_hash(work), config) for work in works) if key in durable]
    if results != ordered or len(results) != counts["completed"]:
        raise PartialRecoveryError("Completed result index differs from exact completed partition")
    return durable


def _review_case_by_job(quality: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["job_key"]: case for case in quality["cases"]}


def resolve_admitted_labels(result: dict[str, Any], quality_case: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the frozen v4 feature/quality hold policy to a native result.

    A quality ``held`` case suppresses the complete feature packet, matching
    ``next_database_v4``.  An ``uncertain`` case suppresses only its finding
    label IDs; all other mechanical present/work labels remain candidates.
    The native result is never changed.
    """
    labels = result.get("labels")
    conflicts = result.get("conflicts")
    if not isinstance(labels, list) or not isinstance(conflicts, list):
        raise PartialRecoveryError("Native result feature-adoption shape differs")
    candidates = [label for label in labels
                  if isinstance(label, dict) and label.get("decision") == "present" and label.get("scope") == "work"]
    adoption_status = "held_conflict" if conflicts else ("adopted" if candidates else "abstained")
    held_ids: set[str] = set()
    quality_status = None
    if quality_case is not None:
        quality_status = quality_case.get("status")
        if quality_status not in {"held", "uncertain"}:
            raise PartialRecoveryError("Quality case status differs from frozen v4 policy")
        held_ids = set(quality_case.get("label_ids", []))
        result_ids = {label.get("label_id") for label in candidates}
        if not held_ids <= result_ids:
            raise PartialRecoveryError("Quality finding is not bound to an exact native label")
    if adoption_status == "held_conflict" or quality_status == "held":
        admitted = []
        held = [{"label_id": label.get("label_id"),
                 "reason": "feature_conflict" if adoption_status == "held_conflict" else "quality_review_held"}
                for label in candidates]
    else:
        admitted = [label for label in candidates if label.get("label_id") not in held_ids]
        held = [{"label_id": label.get("label_id"), "reason": "quality_review_uncertain"}
                for label in candidates if label.get("label_id") in held_ids]
    return {"policy": "next-v4-feature-and-quality-adoption-v1", "feature_adoption": {
                "status": adoption_status, "reasons": list(conflicts),
                "needs_human_review": result.get("needs_human_review"), "human_verified": False},
            "quality_status": quality_status, "admitted_labels": admitted, "held_labels": held,
            "human_verified": False, "confidence_calibrated": False}


def _public_projection_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"qid": row["qid"], "work_id": "wd:" + row["qid"],
             "admitted_labels": row.get("admitted_labels", [])} for row in rows]


def build_partial_export(*, plan: dict[str, Any], plan_path: Path, review: dict[str, Any],
                         verified_rows: list[dict[str, Any]], protected_artifacts: dict[str, str],
                         coordinator_chain_pins: list[dict[str, str]] | None = None) -> dict[str, Any]:
    partition = validate_partition(plan)
    completed_ids = {row["work_id"] for row in plan["completed"]}
    if len(verified_rows) != len(completed_ids) or {row.get("work_id") for row in verified_rows} != completed_ids:
        raise PartialRecoveryError("Verified projection does not cover exactly completed native outputs")
    if len({row.get("input_hash") for row in verified_rows}) != len(verified_rows):
        raise PartialRecoveryError("Verified projection contains duplicate input hashes")
    rows = sorted(verified_rows, key=lambda row: row["work_id"])
    if any(not isinstance(row.get("qid"), str) or not row["qid"].startswith("Q") for row in rows):
        raise PartialRecoveryError("Verified projection qid identity is invalid")
    coordinator_chain_pins = list(coordinator_chain_pins or [])
    source_counts = partition["counts"]
    scope = {"version": "world-novel-partial-recovery-scope-v1", "source_run_id": plan["original_run_id"],
             "source_run_gate_passed": False, "source_terminal": False, "source_counts": source_counts,
             "adopted_completed": len(rows), "failed_retried": 0, "pending_reclassified": 0,
             "review_scope_complete": True, "original_all_inputs_verified": True,
             "raw_model_outputs_preserved": True, "rights_admission_revalidated": True,
             "recovery_plan_path": str(plan_path.resolve()), "recovery_plan_sha256": sha256(plan_path),
             "review_result_sha256": review["review_result_sha256"], "review_selection_sha256": review["selection_sha256"],
             "quality_review_sha256": review["quality_sha256"], "protected_artifact_sha256": protected_artifacts}
    scope["verified_rows_sha256"] = value_sha(rows)
    public_rows = _public_projection_rows(rows)
    blockers = ["source_run_nonterminal_partial_recovery", "display_review_required",
                "derivative_review_required", "output_rights_review_required"]
    return {"version": EXPORT_VERSION, "state": "partial_recovery_projection", "recovery": scope,
            "source_run_id": plan["original_run_id"], "verified_rows": rows,
            "verified_rows_sha256": scope["verified_rows_sha256"],
            "verified_projection": {"rows": public_rows, "coordinator_chain_pins": coordinator_chain_pins,
                                    "publishable": False, "publication_blockers": blockers},
            "public_db_adoption_allowed": False,
            "public_db_adoption_reason": "partial_recovery_requires_separate_display_derivative_output_rights_review",
            "gate": {"passed": False, "reason": "source_run_nonterminal_partial_recovery", **source_counts,
                      "adopted_completed": len(rows), "human_quality_verified": False},
            "human_quality_verified": False, "semantic_quality_verified": False,
            "new_model_calls": 0, "failed_retried": 0, "pending_reclassified": 0,
            "source_files_unchanged": True}


def verify_partial_recovery(plan: str | Path, plan_sha256: str, review_result: str | Path, *, runtime_root: str | Path | None = None) -> dict[str, Any]:
    """Verify the real completed subset and return a nonterminal public export."""
    plan_path = checked(plan, plan_sha256)
    plan_value = read_json(plan_path)
    partition = validate_partition(plan_value)
    review = validate_review(plan_value, plan_path, Path(review_result))
    for path, digest in plan_value["artifact_sha256"].items():
        checked(path, digest)
    review_root = Path(review["quality_path"]).parent
    review_artifacts = _inventory(review_root)
    review_artifacts[review["review_result_path"]] = review["review_result_sha256"]
    selection_parent = Path(review["selection_path"]).parent
    if selection_parent != review_root:
        review_artifacts.update(_inventory(selection_parent))
    protected = {**plan_value["artifact_sha256"], **review_artifacts}
    terminal = Path(plan_value["terminal"])
    arguments = read_json(terminal / "adoption-arguments.json")
    runtime_root = runtime_root or plan_value["runtime"]
    with runtime_imports(runtime_root) as modules:
        helper, next_db, native, usage = modules["helper"], modules["next_db"], modules["native"], modules["usage"]
        source_db = Path(arguments["source_database"]).resolve(strict=True)
        ordered_inputs = Path(arguments["ordered_inputs"]).resolve(strict=True)
        run = Path(arguments["run_dir"]).resolve(strict=True)
        context_path = Path(arguments["execution_context_manifest"]).resolve(strict=True)
        migration_path = Path(arguments["migration_manifest"]).resolve(strict=True)
        proof_path = Path(arguments["terminal_proof"]).resolve(strict=True)
        profile = Path(arguments["cli_profile"]).resolve(strict=True)
        proof = helper.read_json(proof_path)
        helper.check_proof(proof, source_db=source_db, ordered_inputs=ordered_inputs, run_dir=run,
                           execution_context_manifest=context_path, migration_manifest=migration_path, cli_profile=profile)
        terminal_request = read_json(terminal / "request.json")
        coordinator_doc = {"runtime_preparation": str(Path(runtime_root).resolve()),
                           "run_manifest": str(run / "manifest.json"),
                           "coordinator_sha256": terminal_request["coordinator_sha256"],
                           "artifact_sha256": protected}
        coordinator_module = importlib.import_module("recommend_book.scale_coordinator_proof_v1")
        coordinator_chain_pins = coordinator_module.coordinator_chain_pins(coordinator_doc)
        protected.update({pin["path"]: pin["sha256"] for pin in coordinator_chain_pins})
        context = helper.read_json(context_path)
        spec_name = context["execution_context"]["run_config"].get("execution_spec")
        if not spec_name:
            raise PartialRecoveryError("Explicit next execution spec required")
        spec = importlib.import_module("recommend_book.classification.specs").get_spec(spec_name)
        runner_v4 = importlib.import_module("recommend_book.classification.runner_v4")
        helper.build_run_config = lambda model, version, taxonomy, batch: runner_v4.build_run_config(
            model, version, taxonomy, batch, execution_spec=spec_name)
        helper.validate_result = spec.validate_result
        config, _taxonomy = helper.check_context(context, ordered_inputs)
        works = [importlib.import_module("recommend_book.models").WorkInput.model_validate(
            json.loads(line)) for line in ordered_inputs.read_bytes().splitlines() if line.strip()]
        expected_works = {row["work_id"] for rows in (plan_value["completed"], plan_value["failed"], plan_value["unstarted"]) for row in rows}
        if {work.work_id for work in works} != expected_works or len(works) != partition["counts"]["selected"]:
            raise PartialRecoveryError("Original full input scope differs")
        candidates = {helper.input_hash(work): work for work in next_db.get_next_inputs(source_db)["classification"]}
        for work in works:
            if candidates.get(helper.input_hash(work)) != work or not work.source_record_ids or work.expression_id is not None:
                raise PartialRecoveryError("Original input differs from exact next projection")
        for row in plan_value["completed"] + plan_value["failed"] + plan_value["unstarted"]:
            work = next(work for work in works if work.work_id == row["work_id"])
            if helper.input_hash(work) != row["input_sha256"]:
                raise PartialRecoveryError("Recovery partition input hash differs from immutable input")
        manifest = helper.read_json(run / "manifest.json")
        durable = _validate_partial_manifest(plan_value, partition, helper, proof, manifest, run, works, config)
        guard = usage.UsageGuard(arguments["usage_ledger"], expected_ledger_sha256=sha256(arguments["usage_ledger"]))
        quality_cases = _review_case_by_job(review["quality"])
        rows: list[dict[str, Any]] = []
        with guard.native_scopes.validation() as scope:
            for key, item in durable.items():
                work, result, state = item["work"], item["result"], item["state"]
                histories = [entry for entry in state.get("history", []) if entry.get("source_manifest_path")]
                if not histories:
                    raise PartialRecoveryError("Typed native source manifest missing")
                history = histories[-1]
                descriptor_path = helper.contained(run, history["source_manifest_path"])
                if sha256(descriptor_path) != history.get("source_manifest_sha256"):
                    raise PartialRecoveryError("Native descriptor SHA differs")
                descriptor = helper.read_json(descriptor_path)
                origins = descriptor.get("originating_input_hashes")
                native.verify_native_attempt(descriptor, result, origins,
                                             expected_config_sha256=next_db._value_sha(config), works=[work], artifact_session=scope)
                input_decision = guard.check_input(work, "external_llm")
                derived_decision = guard.check_derived(result, origins, "external_llm", source_manifest=descriptor)
                if not input_decision.allowed or not derived_decision.allowed:
                    raise PartialRecoveryError("Native input or output admission denied")
                quality_case = quality_cases.get(key)
                admission = resolve_admitted_labels(result, quality_case)
                labels = admission["admitted_labels"]
                evidence = [{"evidence_id": ev.evidence_id, "source_url": ev.source_url,
                             "rights_policy_id": ev.rights_policy_id, "locator": ev.locator,
                             "license": ev.license, "license_url": ev.license_url,
                             "attribution": ev.attribution,
                             "inherited_restrictions": list(ev.inherited_restrictions),
                             "source_record_ids": list(work.source_record_ids)} for ev in work.evidence]
                artifact_sha = {name: value["sha256"] for name, value in descriptor.get("artifacts", {}).items()}
                rows.append({"work_id": work.work_id, "input_hash": helper.input_hash(work), "job_key": key,
                             "qid": next(row["qid"] for row in plan_value["completed"] if row["work_id"] == work.work_id),
                             "source_record_ids": list(work.source_record_ids), "source_evidence": evidence,
                             "admitted_labels": labels, "native_result_value_sha256": next_db._value_sha(result),
                             "raw_result_path": str(item["result_path"]), "raw_result_sha256": sha256(item["result_path"]),
                             "native_descriptor_path": str(descriptor_path), "native_artifact_sha256": artifact_sha,
                             "quality_case": quality_case, "label_admission": admission,
                             "input_admission": input_decision.model_dump(mode="json"),
                             "derived_admission": derived_decision.model_dump(mode="json"),
                             "human_verified": False, "confidence_calibrated": False})
        for path, digest in protected.items():
            checked(path, digest)
        # Pin this verifier in the returned artifact inventory so a later
        # publication verifier cannot silently substitute its implementation.
        verifier_path = Path(__file__).resolve(strict=True)
        protected[str(verifier_path)] = sha256(verifier_path)
        return build_partial_export(plan=plan_value, plan_path=plan_path, review=review,
                                    verified_rows=rows, protected_artifacts=protected,
                                    coordinator_chain_pins=coordinator_chain_pins)
