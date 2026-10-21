"""Offline withholding of unproven optional metadata for the v2 held pool.

No raw source, source record, catalogue, or network checkpoint is changed.  A
new SourceRecord namespace clears only region/series/subject values whose
linked labels were unavailable, while retaining all contributors and WP
narrative evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path("data/autonomous-next-v3/scale-30000-v1")
OUTPUT = ROOT / "linked-metadata-repair-v3-withheld-v2"
SCRIPT = Path(__file__).with_name("repair_scale_linked_metadata_v2.py")
spec = importlib.util.spec_from_file_location("repair_scale_linked_metadata_v2", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load v2 scope implementation")
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)
from recommend_book.ingestion.wikidata import claim_values


def value_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def adopted_ids(claims: dict, prop: str) -> set[str]:
    """Match UsageGuard/WikidataAdapter rank and deprecated-claim semantics."""
    return {
        value["id"] for value in claim_values({"claims": claims}, prop)
        if isinstance(value, dict) and isinstance(value.get("id"), str)
    }


def required_ids(row: dict) -> set[str]:
    claims = row.get("extra", {}).get("claims", {})
    result = {
        identifier.get("value")
        for contributor in row.get("contributors", [])
        for identifier in contributor.get("identifiers", [])
        if identifier.get("scope") == "agent" and isinstance(identifier.get("value"), str)
    }
    if row.get("region"):
        result.update(adopted_ids(claims, "P495"))
    if row.get("series"):
        result.update(adopted_ids(claims, "P179"))
    if any(item.get("locator") == "claims.P136;claims.P921" for item in v2._selected_evidence(row)):
        result.update(adopted_ids(claims, "P136"))
        result.update(adopted_ids(claims, "P921"))
    return result


def canonicalize(row: dict, proofs: dict[str, dict]) -> dict:
    path = Path(row["raw_path"])
    content = path.read_bytes()
    if v2.base.sha256(path) != row["raw_sha256"]:
        raise ValueError(f"source raw hash changed: {path}")
    labels = {
        qid: item["label"]
        for qid, item in row.get("extra", {}).get("linked_label_provenance", {}).items()
        if isinstance(item, dict) and isinstance(item.get("label"), str)
    }
    labels.update({qid: proofs[qid]["label"] for qid in required_ids(row) if qid in proofs})

    class SavedFetcher:
        def get(self, *args, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(
                status="success", content=content,
                artifact=SimpleNamespace(path=str(path), sha256=row["raw_sha256"], retrieved_at=row["retrieved_at"]),
            )

    result = v2.base.WikidataAdapter(SavedFetcher()).fetch_entities(
        [row["source_id"]], languages=v2.base.LANGUAGES,
        linked_labels=labels,
    )
    if result.errors or not result.records:
        raise ValueError("saved Wikidata normalization failed: " + "; ".join(result.errors))
    proof_map = dict(row.get("extra", {}).get("linked_label_provenance", {}))
    proof_map.update({qid: proofs[qid] for qid in required_ids(row) if qid in proofs})
    return v2.base.merge_normalized(row, result.records[0].model_dump(mode="json"), proof_map)


def strip_optional(row: dict, missing: set[str], proofs: dict[str, dict]) -> tuple[dict, list[dict], list[str]]:
    candidate = json.loads(json.dumps(row, ensure_ascii=False))
    withheld = []
    subject_evidence_ids = [
        item.get("evidence_id") for item in row.get("evidence", [])
        if item.get("llm_input_allowed")
        and item.get("kind") == "subject"
        and item.get("locator") == "claims.P136;claims.P921"
        and item.get("rights_policy_id") == "wikidata-cc0"
    ]
    claims = row.get("extra", {}).get("claims", {})
    region_ids = adopted_ids(claims, "P495") if row.get("region") else set()
    series_ids = adopted_ids(claims, "P179") if row.get("series") else set()
    subject_ids = set()
    if any(item.get("locator") == "claims.P136;claims.P921" for item in v2._selected_evidence(row)):
        subject_ids = adopted_ids(claims, "P136") | adopted_ids(claims, "P921")

    def hold_field(field: str, old_value: object, ids: set[str], reason: str) -> None:
        withheld.append({
            "field": field,
            "original_value_sha256": value_sha(old_value),
            "original_value": old_value,
            "linked_qids": sorted(ids),
            "unproven_linked_qids": sorted(ids - set(proofs)),
            "reason": reason,
        })

    if region_ids - set(proofs):
        old = candidate.get("region", [])
        candidate["region"] = []
        hold_field("region", old, region_ids, "optional region label unavailable; empty region is permitted by current guard")
    if series_ids - set(proofs):
        old = candidate.get("series")
        candidate["series"] = None
        hold_field("series", old, series_ids, "optional series label unavailable; null series is permitted by current guard")
    if subject_ids - set(proofs):
        old = candidate.get("subjects", [])
        candidate["subjects"] = []
        candidate["evidence"] = [item for item in candidate.get("evidence", []) if item.get("evidence_id") not in subject_evidence_ids]
        hold_field("subjects", old, subject_ids, "unproven Wikidata subject evidence withheld; retained descriptions remain available")
        withheld[-1]["evidence_ids"] = subject_evidence_ids

    extra = dict(candidate.get("extra", {}))
    if region_ids - set(proofs):
        extra["region_basis"] = None
    if series_ids - set(proofs):
        extra["series_qids"] = []
    if subject_ids - set(proofs):
        extra["subject_qids"] = []
    extra["source_record_version"] = "scale-content-linked-metadata-repair-v3-withheld-v2"
    extra["metadata_repair_scope"] = "optional unproven region, series, and Wikidata subject values withheld; contributors and WP narrative retained"
    candidate["extra"] = extra
    return candidate, withheld, subject_evidence_ids


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=False)
    input_sha = v2.base.sha256(v2.base.INPUT)
    rows = v2.base.records()
    input_line_sha = {
        line_number: hashlib.sha256(raw_line).hexdigest()
        for line_number, raw_line in enumerate(v2.base.INPUT.read_bytes().splitlines(), 1)
    }
    rows_with_lines = list(enumerate(rows, 1))
    v2_report = v2.base.load_json(ROOT / "linked-metadata-repair-v2/repair-manifest.json")
    held_qids = {item["work_qid"] for item in v2_report["held"]}
    rows_with_lines = [(line, row) for line, row in rows_with_lines if row["source_id"] in held_qids]
    rows = [row for _, row in rows_with_lines]
    required = set().union(*(required_ids(row) for row in rows))
    proofs = v2.base.load_saved_proofs(required)
    held_rows = []
    for line_number, row in rows_with_lines:
        missing = required_ids(row) - set(proofs)
        held_rows.append((line_number, row, missing))

    candidates = []
    held = []
    manifest_rows = []
    for line_number, original_row, missing in held_rows:
        row = canonicalize(original_row, proofs)
        candidate, withheld, subject_evidence_ids = strip_optional(row, missing, proofs)
        descriptions = [
            item for item in candidate.get("evidence", [])
            if item.get("llm_input_allowed") and item.get("kind") == "description"
        ]
        narrative = [
            item for item in candidate.get("evidence", [])
            if item.get("llm_input_allowed") and item.get("rights_policy_id") == "wikipedia-screened-prose"
            and item.get("kind") in {"note", "synopsis", "excerpt"}
        ]
        if not descriptions:
            held.append({"work_qid": row["source_id"], "reason": "no_allowed_description_after_optional_withholding"})
            continue
        if not narrative:
            held.append({"work_qid": row["source_id"], "reason": "WP_narrative_evidence_absent"})
            continue
        for item in withheld:
            field = item["field"]
            source_value = original_row.get(field, [])
            item["canonical_value_before_withholding"] = item.pop("original_value")
            item["canonical_value_before_withholding_sha256"] = item.pop("original_value_sha256")
            item["source_original_value"] = source_value
            item["source_original_value_sha256"] = value_sha(source_value)
        candidates.append(candidate)
        manifest_rows.append({
            "work_qid": row["source_id"],
            "input_path": str(v2.base.INPUT.resolve()),
            "input_line": line_number,
            "input_sha256": input_sha,
            "input_line_sha256": input_line_sha[line_number],
            "original_source_record_version": original_row.get("extra", {}).get("source_record_version"),
            "raw_path": original_row.get("raw_path"),
            "raw_sha256": original_row.get("raw_sha256"),
            "withheld": withheld,
            "retained_description_evidence_ids": [item.get("evidence_id") for item in descriptions],
            "retained_wp_narrative_evidence_ids": [item.get("evidence_id") for item in narrative],
            "original_missing_linked_qids": sorted(missing),
            "v2_missing_linked_qids": next(item["missing_agent_qids"] for item in v2_report["held"] if item["work_qid"] == original_row["source_id"]),
        })

    output_path = OUTPUT / "withheld-source-records.jsonl"
    output_path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in candidates), encoding="utf-8", newline="\n")
    report = {
        "version": "scale-content-linked-metadata-repair-v3-withheld-v2",
        "input_records": len(v2.base.records()),
        "v2_held_records": len(held_qids),
        "adopted_scope_required_linked_qids": len(required),
        "candidate_records": len(candidates),
        "held_after_withholding": len(held),
        "network_requests": 0,
        "input_path": str(v2.base.INPUT.resolve()),
        "input_sha256": input_sha,
        "records_sha256": v2.base.sha256(output_path),
        "scope": "known labels canonicalized from saved raw; optional unproven region/series/WD subject withheld; contributors, descriptions, WP narrative, raw paths and raw hashes retained",
        "manifest": "withheld-manifest.jsonl",
        "held": held,
        "created_at": v2.base.now(),
    }
    v2.base.save_json(OUTPUT / "withheld-report.json", report)
    (OUTPUT / "withheld-manifest.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in manifest_rows), encoding="utf-8", newline="\n")
    print(json.dumps({key: report[key] for key in ("v2_held_records", "candidate_records", "held_after_withholding", "network_requests", "records_sha256")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
