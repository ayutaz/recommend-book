"""Freeze references for bounded repair diagnostics; never run a diagnostic/model.

Only saved review inventories are traversed. SQLite reads use selected primary keys;
neither a corpus export nor a whole-database digest is produced.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3

VERSION = "codex-diagnostic-preparation-v1"
SELECTION = "held-repair-priority-lexical-v1"
BASE = Path("data/autonomous-next-v3")
ADOPTION = BASE / "evaluation/world-main-quality-review-v1/adoption-review.json"
OBSERVATIONS = BASE / "evaluation/world-main-semantic-observations-v1/observations.json"
EXPANSION = BASE / "evaluation/phase3-cached-synopsis-expansion-review-v1/report.json"
IDENTITY = BASE / "resolution/v2/audit.json"
SUPPLEMENT = BASE / "resolution/v3/supplementary-type-audit.json"
OUTPUT = BASE / "evaluation/phase5-codex-diagnostic-preparation-v3"
REPAIR_CODES = ("evidence_subject_scope_mismatch", "unresolved_version_scope_conflict")
OUTPUT_FILES = frozenset({"classification-task-packets.json", "identity-task-packets.json",
                          "report.json", "source-ledger.json"})
# Ledger content never becomes an SQL identifier. Only these static queries exist.
KEYED_QUERIES = {
    ("next_classification_inputs", "job_key"): 'SELECT * FROM "next_classification_inputs" WHERE "job_key"=?',
    ("next_classification_source_refs", "job_key"): 'SELECT * FROM "next_classification_source_refs" WHERE "job_key"=?',
    ("source_records", "version_id"): 'SELECT * FROM "source_records" WHERE "version_id"=?',
    ("works", "source_version_id"): 'SELECT * FROM "works" WHERE "source_version_id"=?',
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _workspace_path(root, path, label="Source"):
    path = (root / Path(path)).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"{label} outside workspace")
    return path


def _output_file(root, out, name):
    path = _workspace_path(root, out / name, "Output file")
    if not path.is_relative_to(out):
        raise ValueError("Output file outside output directory")
    return path


def _is_sha256(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


class ArtifactLedger:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files = {}

    def add(self, path, expected_sha256=None):
        path = _workspace_path(self.root, path)
        name = path.relative_to(self.root).as_posix()
        sha = file_hash(path)
        if expected_sha256 is not None and sha != expected_sha256:
            raise ValueError(f"Source hash mismatch: {name}")
        if name in self.files and self.files[name] != sha:
            raise ValueError(f"Source changed: {name}")
        self.files[name] = sha
        return name

    def verify(self):
        for path, sha in self.files.items():
            if file_hash(_workspace_path(self.root, path)) != sha:
                raise ValueError(f"Source changed: {path}")


def diagnostic_contract():
    return {"status": "notrun", "execution_allowed": False,
            "provenance": "llm_or_silver", "human_gold": False,
            "authority_merge_approved": False, "confidence_calibrated": False,
            "unknown_is_negative": False, "new_model_calls": 0,
            "external_acquisitions": 0, "database_mutations": 0,
            "user_annotation_required": 0, "private_history_exported": False,
            "source_text_copied": False, "new_results": []}


def select_cases(classification, identity):
    classified = [(i, x) for i, x in enumerate(classification)
                  if x.get("status") == "held" and set(x.get("reason_codes", [])) & set(REPAIR_CODES)]
    ids = [x["work_id"] for _, x in classified]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate classification work")
    classified.sort(key=lambda item: (
        min(REPAIR_CODES.index(c) for c in item[1]["reason_codes"] if c in REPAIR_CODES),
        item[1]["work_id"], item[1]["job_key"]))
    pairs = [(i, x) for i, x in enumerate(identity) if x.get("status") == "held"]
    ids = [tuple(sorted(x["side_versions"])) for _, x in pairs]
    if len(ids) != len(set(ids)) or len({x["case_id"] for _, x in pairs}) != len(pairs):
        raise ValueError("Duplicate identity pair")
    pairs.sort(key=lambda item: item[1]["case_id"])
    return classified[:20], pairs[:40]


def _load(root, ledger, path):
    name = ledger.add(path)
    return json.loads((root / name).read_text(encoding="utf-8"))


def _reference(path, pointer, value):
    return {"path": str(path).replace("\\", "/"), "json_pointer": pointer,
            "value_sha256": canonical_hash(value)}


def _database(root, relative):
    path = _workspace_path(root, relative, "Database")
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _bind_rows(db, row_ledger, table, key_column, key):
    rows = _keyed_rows(db, table, key_column, key)
    binding = {"table": table, "key_column": key_column, "key": key,
               "row_count": len(rows), "rows_sha256": canonical_hash(rows)}
    if binding not in row_ledger:
        row_ledger.append(binding)
    return rows


def _keyed_rows(db, table, key_column, key):
    if (not isinstance(table, str) or not isinstance(key_column, str)
            or (table, key_column) not in KEYED_QUERIES or not isinstance(key, str)):
        raise ValueError("Unsupported database binding")
    return [dict(row) for row in db.execute(KEYED_QUERIES[(table, key_column)], (key,))]


def _source(db, row_ledger, ledger, version):
    rows = _bind_rows(db, row_ledger, "source_records", "version_id", version)
    if len(rows) != 1:
        raise ValueError("Exact source version absent")
    row = rows[0]
    raw = ledger.add(row["raw_path"], row["raw_sha256"])
    works = _bind_rows(db, row_ledger, "works", "source_version_id", version)
    return {key: row[key] for key in ["version_id", "source", "source_id", "payload_sha256",
                                    "raw_sha256", "rights_policy_id"]} | {
        "raw_path": raw,
        "current_work_nodes": [{k: work[k] for k in ["work_id", "canonical_id", "resolved", "work_kind"]}
                               for work in works],
        "expression_scope_verified": False, "new_usage_permission_inferred": False}


def prepare(root=Path.cwd(), output=OUTPUT):
    root = Path(root).resolve()
    out = (root / output).resolve()
    if not out.is_relative_to(root) or out.exists():
        raise ValueError("Output must be a new workspace directory")
    ledger = ArtifactLedger(root)
    policy = _load(root, ledger, "config/quality-evaluation-v1.json")
    taxonomy_path = policy["taxonomy"]["path"]
    ledger.add(taxonomy_path, policy["taxonomy"]["sha256"])
    taxonomy = _load(root, ledger, taxonomy_path)
    definitions = {x["label_id"]: x for x in taxonomy["labels"]}
    adoption = _load(root, ledger, ADOPTION)
    observations = _load(root, ledger, OBSERVATIONS)
    expansion = _load(root, ledger, EXPANSION)
    identity = _load(root, ledger, IDENTITY)
    supplement = _load(root, ledger, SUPPLEMENT)
    for item in adoption["source_audits"]:
        ledger.add(item["path"], item["sha256"])
    selected, pairs = select_cases(adoption["cases"], identity["same_candidates"])
    observation_index = {x["job_key"]: (i, x) for i, x in enumerate(observations["observations"])}
    row_ledger, classification_packets, identity_packets = [], [], []
    database_path = policy["candidate_inventory"]["database_path"]
    db = _database(root, database_path)
    try:
        for index, finding in selected:
            rows = _bind_rows(db, row_ledger, "next_classification_inputs", "job_key", finding["job_key"])
            if len(rows) != 1:
                raise ValueError("Exact current classification job absent")
            row = rows[0]
            inp, result = json.loads(row["input_json"]), json.loads(row["result_json"])
            oi, observation = observation_index[finding["job_key"]]
            if (row["work_id"] != finding["work_id"] or row["input_hash"] != finding["input_hash"]
                    or canonical_hash(inp) != finding["input_hash"]
                    or canonical_hash(result) != finding["native_result_value_sha256"]
                    or observation["input_hash"] != finding["input_hash"]
                    or observation["native_result_value_sha256"] != finding["native_result_value_sha256"]):
                raise ValueError("Old finding/current row binding mismatch")
            refs = _bind_rows(db, row_ledger, "next_classification_source_refs", "job_key", finding["job_key"])
            sources = []
            for ref in refs:
                source = _source(db, row_ledger, ledger, ref["source_version_id"])
                for field in ["payload_sha256", "raw_sha256"]:
                    if field in ref and ref[field] != source[field]:
                        raise ValueError("Classification source-ref hash mismatch")
                source["original_classification_source_scope"] = ref.get("source_scope")
                source["original_classification_work_nodes"] = json.loads(ref.get("work_nodes_json", "[]"))
                source["original_classification_edition_ids"] = json.loads(ref.get("edition_ids_json", "[]"))
                sources.append(source)
            evidence = []
            for i, e in enumerate(inp["evidence"]):
                attribution = e.get("attribution") or {}
                raw = attribution.get("raw_path")
                if raw:
                    raw = ledger.add(raw, attribution["raw_sha256"])
                evidence.append({k: e.get(k) for k in ["evidence_id", "kind", "locator", "expression_ref",
                                                       "rights_policy_id", "llm_input_allowed"]} | {
                    "input_pointer": f"/evidence/{i}", "evidence_value_sha256": canonical_hash(e),
                    "raw_path": raw, "raw_sha256": attribution.get("raw_sha256"),
                    "execution_allowed": False, "identity_and_usage_revalidation_required": True})
            labels = [{k: label.get(k) for k in ["label_id", "scope", "expression_id", "decision", "missing_reason"]} | {
                "definition": definitions[label["label_id"]],
                "original_label_value_sha256": canonical_hash(label),
                "evidence_refs": [{k: ref.get(k) for k in ["evidence_id", "locator", "directness"]}
                                  for ref in label.get("evidence", [])]}
                for label in result["labels"] if label["label_id"] in finding["label_ids"]]
            prior_expansion = [_reference(EXPANSION, f"/work_reviews/{i}", x)
                               for i, x in enumerate(expansion["work_reviews"]) if x["work_id"] == finding["work_id"]]
            classification_packets.append(diagnostic_contract() | {
                "case_id": "classification:" + finding["work_id"], "work_id": finding["work_id"],
                "expression_id": inp.get("expression_id"), "job_key": finding["job_key"],
                "input_hash": finding["input_hash"], "run_id": row["run_id"],
                "old_finding": _reference(ADOPTION, f"/cases/{index}", finding),
                "old_observation": _reference(OBSERVATIONS, f"/observations/{oi}", observation),
                "cached_expansion_reviews": prior_expansion,
                "old_status": finding["status"], "reason_codes": finding["reason_codes"],
                "source_bindings": sources, "evidence_refs": evidence, "labels": labels,
                "missing_axes_ref": {"value_sha256": canonical_hash(result["missing_axes"]),
                                     "preserve_all_unknown": True},
                "task": "Prepare minimal scope/subject proof and repair acceptance criteria for the saved hold.",
                "new_step4_question": (
                    "Which cited evidence IDs refer to a different subject, and which exact source-version/work mapping must be corrected before the held label may be considered? Propose an evidence-subject regression fixture; do not decide the label again."
                    if "evidence_subject_scope_mismatch" in finding["reason_codes"] else
                    "Which exact original-work, translation, adaptation, or edition assertion is missing for each cited evidence ID? Specify its required authority locator and a test that prevents work-level inheritance while that assertion is missing; preserve the held label."),
                "external_llm_blockers": [
                    "This packet is local_storage metadata references, not a purpose-specific external_llm UsageGuard admission.",
                    "Exact cited subject/source scope is unresolved under reason codes: " + ",".join(finding["reason_codes"]),
                    "Evidence refs bind saved bytes only: page/revision/authority identity and inherited restrictions must be validated for each proposed new input.",
                    "The unchanged classification input has already been semantically reviewed; only the distinct repair-prerequisite question is eligible for future admission, not repeat classification."],
                "why_not_duplicate": "Reuse existing semantic verdict. The new task is repair prerequisites and regression-case design, not a second label review of the unchanged input.",
                "step4_prerequisite": "A distinct repair question and verified usage/identity scope; reclassification additionally requires changed evidence or corrected inputs. Unchanged semantic re-review is forbidden.",
            })
        for index, finding in pairs:
            sources = [_source(db, row_ledger, ledger, version) for version in finding["side_versions"]]
            claims = []
            for mi, metadata in enumerate(finding["bibliographic_metadata"]):
                ledger.add(metadata["raw_path"], metadata["raw_sha256"])
                claims.append({"source_version_id": metadata["source_version_id"], "scope": metadata["scope"],
                               "assertion_refs": [{"property": prop, "locator": claim["locator"],
                                                   "value_sha256": canonical_hash(claim)}
                                                  for prop, values in metadata["claims"].items() for claim in values],
                               "saved_metadata_ref": _reference(IDENTITY, f"/same_candidates/{index}/bibliographic_metadata/{mi}", metadata)})
            identity_packets.append(diagnostic_contract() | {
                "case_id": finding["case_id"], "side_versions": finding["side_versions"],
                "old_finding": _reference(IDENTITY, f"/same_candidates/{index}", finding),
                "old_status": "held", "old_reason": finding["reason"], "proposed_same_is_authority": False,
                "source_bindings": sources, "scope_claim_refs": claims,
                "identity_levels": ["work", "expression", "edition"],
                "expression_identity": "unverified", "original_work_identity_verified": False,
                "task": "Prepare minimal missing identity/scope proof and regression fixtures; preserve the existing hold.",
                "new_step4_question": "Which explicit authority relation is missing between these exact source versions, at work versus expression/edition level? Describe the minimum permitted proof and a regression fixture rejecting title/author/AI-same equivalence; do not re-adjudicate the pair from unchanged sources.",
                "external_llm_blockers": [
                    "No purpose-specific external_llm UsageGuard admission exists for this new repair question.",
                    finding["reason"],
                    "Source identity, work versus expression/edition scope, and applicable operation permissions must be bound to any proposed new input; cached raw files are references only."],
                "why_not_duplicate": "The old exact-ID audit already rejected adoption. This packet plans repair prerequisites, not another same/different verdict from unchanged titles or metadata.",
                "step4_prerequisite": "A distinct repair question with verified permitted inputs; an identity verdict requires new explicit authority proof. No new authority is invented from the saved AI same proposal.",
            })
        # Read-only keyed recheck: no DB full hash or corpus scan.
        _verify_rows(db, row_ledger)
    finally:
        db.close()
    for path in ["src/recommend_book/diagnostic_preparation.py", "scripts/prepare_codex_diagnostics.py",
                 "tests/test_diagnostic_preparation.py"]:
        ledger.add(path)
    ledger.verify()
    report = diagnostic_contract() | {
        "version": VERSION, "selection_algorithm": SELECTION,
        "classification_work_n": len(classification_packets), "identity_pair_n": len(identity_packets),
        "classification_cap": 20, "identity_cap": 40,
        "classification_held_eligible_n": sum(x.get("status") == "held" and bool(set(x.get("reason_codes", [])) & set(REPAIR_CODES)) for x in adoption["cases"]),
        "uncertainty_only_not_re_reviewed": sum(x.get("status") == "uncertain" for x in adoption["cases"]),
        "identity_verified_bindings_not_re_reviewed": sum(x.get("status") == "verified_existing_binding" for x in identity["same_candidates"]),
        "identity_supplement_cases_reused_as_context_only": len(supplement["cases"]),
        "nonpair_ndl_holds_not_fabricated_as_pairs": len(identity["unresolved_holds"]),
        "database_path": database_path, "database_full_hash_computed": False,
        "database_snapshot_scope": "Selected keyed rows only; not a whole-database integrity or corpus freshness claim",
        "selection_rule": "Held scope mismatch first, held version conflict second, then work_id/job_key; first 20 unique works. Held identity candidates ordered by case_id; first 40 unique unordered source-version pairs. Never fill a cap with already-reviewed nonrepair cases.",
        "limits": ["References only; no article text, title, private history, or new model input exported.",
                   "No quality score, calibration, new classification, identity decision, repair, or adoption executed.",
                   "Prior observations are LLM findings; mechanical identity audit findings remain mechanical, never human gold.",
                   "UsageGuard is unchanged. All packets stay execution_allowed=false pending purpose-specific admission."]}
    out.mkdir(parents=True)
    artifacts = {"classification-task-packets.json": classification_packets,
                 "identity-task-packets.json": identity_packets, "report.json": report,
                 "source-ledger.json": {"version": VERSION, "files_sha256": ledger.files,
                                        "database_path": database_path, "database_rows": row_ledger}}
    for name, value in artifacts.items():
        (out / name).write_text(canonical(value) + "\n", encoding="utf-8")
    manifest = {"version": VERSION, "status": "notrun", "source_ledger_sha256": file_hash(out / "source-ledger.json"),
                "output_sha256": {name: file_hash(out / name) for name in artifacts}}
    (out / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    return report


def _verify_rows(db, rows):
    for binding in rows:
        actual = _keyed_rows(db, binding["table"], binding["key_column"], binding["key"])
        if len(actual) != binding["row_count"] or canonical_hash(actual) != binding["rows_sha256"]:
            raise ValueError("Selected database row changed")


def verify_preparation(root=Path.cwd(), output=OUTPUT, *, manifest_sha256=None):
    """Verify against a digest from a trusted receipt outside the mutable bundle.

    Recomputing a digest from the manifest being checked is not authentication.
    The caller must supply the original receipt's digest, never a freshly accepted
    digest from the candidate output directory.
    """
    root = Path(root).resolve()
    out = _workspace_path(root, output, "Output")
    if not _is_sha256(manifest_sha256):
        raise ValueError("An externally pinned manifest SHA256 is required")
    manifest_path = _output_file(root, out, "manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise ValueError("manifest external pin mismatch")
    manifest = json.loads(manifest_bytes)
    if set(manifest["output_sha256"]) != OUTPUT_FILES:
        raise ValueError("Unexpected output file set")
    if manifest.get("version") != VERSION or manifest.get("status") != "notrun":
        raise ValueError("Unexpected manifest contract")
    ledger_path = _output_file(root, out, "source-ledger.json")
    ledger_bytes = ledger_path.read_bytes()
    if (not _is_sha256(manifest.get("source_ledger_sha256"))
            or hashlib.sha256(ledger_bytes).hexdigest() != manifest["source_ledger_sha256"]):
        raise ValueError("Source ledger digest mismatch")
    output_paths = {name: _output_file(root, out, name) for name in OUTPUT_FILES}
    frozen_values = {}
    for name, sha in manifest["output_sha256"].items():
        blob = ledger_bytes if name == "source-ledger.json" else output_paths[name].read_bytes()
        if not _is_sha256(sha) or hashlib.sha256(blob).hexdigest() != sha:
            raise ValueError("Frozen output changed")
        frozen_values[name] = json.loads(blob)
    saved = frozen_values["source-ledger.json"]
    ledger = ArtifactLedger(root)
    for path, sha in saved["files_sha256"].items():
        if not _is_sha256(sha):
            raise ValueError("Invalid source hash")
        ledger.add(path, expected_sha256=sha)
    ledger.verify()
    report = frozen_values["report.json"]
    classifications = frozen_values["classification-task-packets.json"]
    identity = frozen_values["identity-task-packets.json"]
    for value in [report, *classifications, *identity]:
        if any(value.get(key) != expected for key, expected in diagnostic_contract().items()):
            raise ValueError("Diagnostic contract changed")
    if (len(classifications) != report["classification_work_n"] or len(classifications) > 20
            or len(identity) != report["identity_pair_n"] or len(identity) > 40):
        raise ValueError("Diagnostic packet counts changed")
    db = _database(root, saved["database_path"])
    try:
        _verify_rows(db, saved["database_rows"])
    finally:
        db.close()
    return {"verified": True, "status": "notrun", "manifest_sha256": manifest_sha256,
            "files": len(ledger.files), "row_bindings": len(saved["database_rows"])}
