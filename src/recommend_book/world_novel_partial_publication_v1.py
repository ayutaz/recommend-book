"""Read-only publication review for the world partial-recovery projection.

This contract authenticates a separate display/derivative/output-rights review
over the exact 24 completed rows.  It creates no database and never changes the
nonterminal source gate.  The returned document only grants the production
writer permission to consider the reviewed projection after all pins replay.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


PUBLICATION_VERSION = "world-novel-partial-reviewed-export-v1"
PARTIAL_EXPORT_VERSION = "world-novel-partial-recovery-export-v1"
OUTPUT_REVIEW_VERSION = "scale-content-output-rights-review-v2"
OUTPUT_LICENSE = "CC-BY-SA-4.0"
OUTPUT_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


class PartialPublicationError(ValueError):
    """Raised when a partial reviewed export cannot be authenticated."""


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def value_sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialPublicationError(f"Invalid publication artifact: {path}") from exc
    if not isinstance(value, dict):
        raise PartialPublicationError(f"Publication JSON object required: {path}")
    return value


def checked(path: str | Path, digest: str) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not isinstance(digest, str) or len(digest) != 64 or sha256(resolved) != digest:
        raise PartialPublicationError("Publication artifact SHA differs: " + str(resolved))
    return resolved


def _load_partial_module() -> Any:
    path = Path(__file__).with_name("world_novel_partial_recovery_v1.py").resolve(strict=True)
    name = "world_novel_partial_recovery_contract"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PartialPublicationError("Partial recovery verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_output_review_module() -> Any:
    path = Path(__file__).with_name("scale_content_classification_v3.py").resolve(strict=True)
    name = "recommend_book._partial_output_review_v3"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PartialPublicationError("Existing v3 output review cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _partial_contract(partial: dict[str, Any]) -> None:
    if partial.get("version") != PARTIAL_EXPORT_VERSION or partial.get("state") != "partial_recovery_projection":
        raise PartialPublicationError("Partial recovery export version/state differs")
    if partial.get("public_db_adoption_allowed") is not False:
        raise PartialPublicationError("Only an explicitly nonpublic partial projection may enter review")
    projection = partial.get("verified_projection")
    if not isinstance(projection, dict) or projection.get("publishable") is not False:
        raise PartialPublicationError("Partial projection must remain nonpublishable before review")
    pins = projection.get("coordinator_chain_pins")
    if not isinstance(pins, list) or not pins:
        raise PartialPublicationError("Coordinator chain pins are required")
    rows = projection.get("rows")
    if not isinstance(rows, list) or len(rows) != partial.get("recovery", {}).get("adopted_completed"):
        raise PartialPublicationError("Partial public row scope differs")
    if partial.get("gate", {}).get("passed") is not False or partial.get("recovery", {}).get("source_terminal") is not False:
        raise PartialPublicationError("Partial source gate/terminal state was promoted")
    artifacts = partial.get("recovery", {}).get("protected_artifact_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        raise PartialPublicationError("Partial protected artifact inventory is required")
    for path, digest in artifacts.items():
        checked(path, digest)


def _load_works_and_taxonomy(partial: dict[str, Any], partial_module: Any, runtime_root: str | Path | None) -> tuple[Any, list[Any], dict[str, Any], Path, Path, Path]:
    plan_path = checked(partial["recovery"]["recovery_plan_path"], partial["recovery"]["recovery_plan_sha256"])
    plan = read_json(plan_path)
    terminal = Path(plan["terminal"]).resolve(strict=True)
    arguments = read_json(terminal / "adoption-arguments.json")
    ordered_inputs = Path(arguments["ordered_inputs"]).resolve(strict=True)
    runtime = Path(runtime_root or plan["runtime"]).resolve(strict=True)
    with partial_module.runtime_imports(runtime) as modules:
        model_module = __import__("recommend_book.models", fromlist=["WorkInput"])
        works = [model_module.WorkInput.model_validate(json.loads(line))
                 for line in ordered_inputs.read_bytes().splitlines() if line.strip()]
        taxonomy_path = runtime / "runtime" / "config" / "taxonomy.json" if (runtime / "runtime").is_dir() else runtime / "config" / "taxonomy.json"
        taxonomy_path = taxonomy_path.resolve(strict=True)
        taxonomy = read_json(taxonomy_path)
        ledger_path = Path(arguments["usage_ledger"]).resolve(strict=True)
        return modules, works, taxonomy, ordered_inputs, taxonomy_path, ledger_path


def _review_rows(partial: dict[str, Any], modules: dict[str, Any], works: list[Any], taxonomy: dict[str, Any],
                 ledger_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    output_review = _load_output_review_module()
    by_id = {work.work_id: work for work in works}
    rows = []
    for row in partial["verified_rows"]:
        work = by_id.get(row["work_id"])
        if work is None:
            raise PartialPublicationError("Reviewed row input identity is absent")
        content_ids = sorted(item.evidence_id for item in work.evidence
                             if item.kind in ("synopsis", "excerpt", "note")
                             and item.rights_policy_id != "wikidata-cc0"
                             and item.llm_input_allowed and item.text.strip())
        rows.append({"qid": row["qid"], "input_work_id": work.work_id,
                     "input_hash": row["input_hash"], "admitted_labels": row["admitted_labels"],
                     "label_admission": row["label_admission"],
                     "content_evidence_ids": content_ids})
    guard_module = modules["usage"]
    guard = guard_module.UsageGuard(ledger_path, expected_ledger_sha256=sha256(ledger_path))
    purpose_rows = []
    with guard.native_scopes.validation() as scope:
        for source_row, row in zip(partial["verified_rows"], rows, strict=True):
            work = by_id[row["input_work_id"]]
            input_display = guard.check_input(work, "display")
            input_redistribution = guard.check_input(work, "redistribution")
            result = read_json(source_row["raw_result_path"])
            descriptor = read_json(source_row["native_descriptor_path"])
            origins = descriptor.get("originating_input_hashes")
            derived_display = guard.check_derived(result, origins, "display", source_manifest=descriptor)
            derived_redistribution = guard.check_derived(result, origins, "redistribution", source_manifest=descriptor)
            decisions = {"display_input": input_display.model_dump(mode="json"),
                         "redistribution_input": input_redistribution.model_dump(mode="json"),
                         "display_derived": derived_display.model_dump(mode="json"),
                         "redistribution_derived": derived_redistribution.model_dump(mode="json")}
            if any(not decision.allowed for decision in (input_display, input_redistribution,
                                                          derived_display, derived_redistribution)):
                raise PartialPublicationError("Display or derivative purpose admission denied")
            purpose_rows.append({"qid": row["qid"], "input_hash": row["input_hash"], "decisions": decisions})
    reviewed, review = output_review.review_public_rows(rows, works, taxonomy)
    if (review.get("version") != OUTPUT_REVIEW_VERSION or review.get("human_verified") is not False
            or review.get("commercial_cleared") is not False
            or review.get("legal_clearance_claimed") is not False
            or review.get("output_license") != OUTPUT_LICENSE
            or review.get("output_license_url") != OUTPUT_LICENSE_URL):
        raise PartialPublicationError("Existing v3 output review authority/version differs")
    review["partial_purpose_review"] = purpose_rows
    review["partial_purpose_review_sha256"] = value_sha(purpose_rows)
    return reviewed, review, purpose_rows


def review_partial_export(partial: dict[str, Any], *, runtime_root: str | Path | None = None) -> dict[str, Any]:
    """Reverify a partial projection and return a separately versioned review."""
    _partial_contract(partial)
    partial_module = _load_partial_module()
    plan_path = Path(partial["recovery"]["recovery_plan_path"]).resolve(strict=True)
    review_path = (plan_path.parent / "review.result.json").resolve(strict=True)
    expected_partial = partial_module.verify_partial_recovery(
        plan_path, partial["recovery"]["recovery_plan_sha256"], review_path, runtime_root=runtime_root)
    if expected_partial != partial:
        raise PartialPublicationError("Partial recovery verifier projection differs")
    modules, works, taxonomy, ordered_inputs, taxonomy_path, ledger_path = _load_works_and_taxonomy(
        partial, partial_module, runtime_root)
    reviewed_rows, output_review, purpose_rows = _review_rows(partial, modules, works, taxonomy, ledger_path)
    module_path = Path(__file__).resolve(strict=True)
    output_review_path = Path("src/recommend_book/scale_content_classification_v3.py").resolve(strict=True)
    artifacts = dict(partial["recovery"]["protected_artifact_sha256"])
    for path in (module_path, output_review_path, ordered_inputs, taxonomy_path, ledger_path):
        artifacts[str(path)] = sha256(path)
    public_rows = [{"qid": row["qid"], "work_id": "wd:" + row["qid"],
                    "admitted_labels": row["admitted_labels"],
                    "source_attributions": row.get("source_attributions", [])}
                   for row in reviewed_rows]
    partial_sha = value_sha(partial)
    review_sha = value_sha(output_review)
    purpose_sha = value_sha(purpose_rows)
    return {"version": PUBLICATION_VERSION, "state": "partial_reviewed_projection",
            "partial_recovery_export_sha256": partial_sha,
            "partial_recovery": {"version": partial["version"], "source_run_id": partial["source_run_id"],
                                  "recovery_plan_path": partial["recovery"]["recovery_plan_path"],
                                  "review_result_path": str((plan_path.parent / "review.result.json").resolve()),
                                  "verified_rows_sha256": partial["verified_rows_sha256"],
                                  "recovery_plan_sha256": partial["recovery"]["recovery_plan_sha256"],
                                  "protected_artifact_sha256": partial["recovery"]["protected_artifact_sha256"]},
            "verified_projection": {"rows": public_rows,
                                    "coordinator_chain_pins": partial["verified_projection"]["coordinator_chain_pins"],
                                    "publishable": True},
            "public_db_adoption_allowed": True,
            "public_db_adoption_basis": "partial_source_gate_preserved_plus_exact_display_derivative_output_rights_review",
            "source_run_gate_passed": False, "source_run_terminal": False,
            "review": {"output_rights": output_review, "output_rights_sha256": review_sha,
                        "purpose_rows": purpose_rows, "purpose_rows_sha256": purpose_sha},
            "artifact_sha256": artifacts,
            "reviewer_sha256": sha256(module_path),
            "output_review_verifier_sha256": sha256(output_review_path),
            "output_license": OUTPUT_LICENSE, "output_license_url": OUTPUT_LICENSE_URL,
            "human_verified": False, "commercial_cleared": False, "legal_clearance_claimed": False}


def _verify_partial_reviewed_export_in_process(document: dict[str, Any], *, runtime_root: str | Path | None = None) -> dict[str, Any]:
    """Replay every partial, purpose, and output-review binding without writes."""
    if document.get("version") != PUBLICATION_VERSION or document.get("state") != "partial_reviewed_projection":
        raise PartialPublicationError("Partial reviewed export version/state differs")
    if document.get("public_db_adoption_allowed") is not True or document.get("source_run_gate_passed") is not False or document.get("source_run_terminal") is not False:
        raise PartialPublicationError("Reviewed export changed the source gate/terminal contract")
    for path, digest in document.get("artifact_sha256", {}).items():
        checked(path, digest)
    module_path = Path(__file__).resolve(strict=True)
    if document.get("reviewer_sha256") != sha256(module_path):
        raise PartialPublicationError("Publication verifier implementation changed")
    partial_module = _load_partial_module()
    plan_path = Path(document["partial_recovery"]["recovery_plan_path"]).resolve(strict=True)
    plan_sha = document["partial_recovery"]["recovery_plan_sha256"]
    review_path = Path(document["partial_recovery"]["review_result_path"]).resolve(strict=True)
    partial = partial_module.verify_partial_recovery(plan_path, plan_sha, review_path, runtime_root=runtime_root)
    if value_sha(partial) != document.get("partial_recovery_export_sha256"):
        raise PartialPublicationError("Partial recovery replay differs from reviewed export")
    replay = review_partial_export(partial, runtime_root=runtime_root)
    if replay != document:
        raise PartialPublicationError("Display/derivative/output review replay differs")
    return document


def _runtime_root(document: dict[str, Any], runtime_root: str | Path | None) -> Path:
    if runtime_root is not None:
        return Path(runtime_root).resolve(strict=True)
    plan = read_json(document["partial_recovery"]["recovery_plan_path"])
    return Path(plan["runtime"]).resolve(strict=True)


def _needs_fresh_runtime(document: dict[str, Any], runtime_root: str | Path | None) -> bool:
    loaded = sys.modules.get("recommend_book")
    if loaded is None or not getattr(loaded, "__file__", None):
        return False
    runtime = _runtime_root(document, runtime_root)
    frozen_src = runtime / "runtime" / "src" if (runtime / "runtime").is_dir() else runtime / "src"
    return not Path(loaded.__file__).resolve().is_relative_to(frozen_src.resolve())


_SUBPROCESS_CODE = r'''
import hashlib, importlib.util, json, sys
from pathlib import Path
module_path, document_path, document_sha, runtime_root = sys.argv[1:]
module_path, document_path, runtime_root = map(Path, (module_path, document_path, runtime_root))
raw = document_path.read_bytes()
if hashlib.sha256(raw).hexdigest() != document_sha:
    raise SystemExit("input document SHA differs")
spec = importlib.util.spec_from_file_location("partial_publication_subprocess", module_path)
if spec is None or spec.loader is None:
    raise SystemExit("publication module cannot be loaded by spec")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
document = json.loads(raw)
verified = module._verify_partial_reviewed_export_in_process(document, runtime_root=runtime_root)
payload = {"version": verified["version"], "verified_sha256": module.value_sha(verified),
           "verified_projection": verified["verified_projection"]}
sys.stdout.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
'''


def _verify_via_fresh_subprocess(document: dict[str, Any], runtime_root: str | Path | None) -> dict[str, Any]:
    module_path = Path(__file__).resolve(strict=True)
    runtime = _runtime_root(document, runtime_root)
    expected_value_sha = value_sha(document)
    with tempfile.TemporaryDirectory(prefix="partial-publication-verify-") as directory:
        document_path = Path(directory) / "document.json"
        raw = (canonical_json(document) + "\n").encode("utf-8")
        document_path.write_bytes(raw)
        document_sha = hashlib.sha256(raw).hexdigest()
        argv = [sys.executable, "-c", _SUBPROCESS_CODE, str(module_path), str(document_path),
                document_sha, str(runtime)]
        try:
            completed = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd=Path(__file__).resolve().parents[2],
                                       shell=False, check=False, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise PartialPublicationError("Fresh frozen publication verifier timed out") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace")[-2000:]
            raise PartialPublicationError("Fresh frozen publication verifier failed: " + detail)
        try:
            observed = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise PartialPublicationError("Fresh frozen publication verifier returned invalid JSON") from exc
        expected = {"version": PUBLICATION_VERSION, "verified_sha256": expected_value_sha,
                    "verified_projection": document["verified_projection"]}
        if observed != expected:
            raise PartialPublicationError("Fresh verifier replay did not match the expected raw projection")
    return document


def verify_partial_reviewed_export(document: dict[str, Any], *, runtime_root: str | Path | None = None) -> dict[str, Any]:
    """Replay review directly or isolate it when the root package is loaded."""
    if _needs_fresh_runtime(document, runtime_root):
        return _verify_via_fresh_subprocess(document, runtime_root)
    return _verify_partial_reviewed_export_in_process(document, runtime_root=runtime_root)
