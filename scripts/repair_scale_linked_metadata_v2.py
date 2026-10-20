"""Build the native-input-scoped linked-label repair without network access.

This is deliberately a separate namespace from v1.  It reads v1's immutable
fetch checkpoint and reuses only saved raw/cache proofs.  The required set
matches ``scale_evidence_input``: all contributors represented by the source
record, region/series metadata actually present, and retained CC0 subjects.
Unused P674/P840 claims are not made a prerequisite for native admission.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path("data/autonomous-next-v3/scale-30000-v1")
V1_OUTPUT = ROOT / "linked-metadata-repair-v1"
OUTPUT = ROOT / "linked-metadata-repair-v2"
SCRIPT = Path(__file__).with_name("repair_scale_linked_metadata_v1.py")
_spec = importlib.util.spec_from_file_location("repair_scale_linked_metadata_v1", SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError("cannot load v1 repair implementation")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


def _selected_evidence(row: dict) -> list[dict]:
    evidence = row.get("evidence", [])
    descriptions = [
        item for item in evidence
        if item.get("llm_input_allowed") and item.get("kind") == "description"
    ]

    def language(item: dict) -> str:
        return item.get("language") or (
            item.get("locator", "").split(".", 1)[1]
            if item.get("locator", "").startswith("descriptions.") else ""
        )

    descriptions.sort(key=lambda item: ({"en": 0, "ja": 1}.get(language(item), 2), language(item), item.get("evidence_id", "")))
    selected_ids = {item.get("evidence_id") for item in descriptions[:2]}
    return [
        item for item in evidence
        if item.get("llm_input_allowed")
        and (item.get("kind") == "subject" or item.get("evidence_id") in selected_ids)
    ]


def claim_ids(claims: dict, prop: str) -> set[str]:
    result = set()
    for statement in claims.get(prop, []):
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            result.add(value["id"])
    return result


def required_ids(row: dict) -> set[str]:
    claims = row.get("extra", {}).get("claims", {})
    result = {
        identifier.get("value")
        for contributor in row.get("contributors", [])
        for identifier in contributor.get("identifiers", [])
        if identifier.get("scope") == "agent" and isinstance(identifier.get("value"), str)
    }
    if row.get("region"):
        result.update(claim_ids(claims, "P495"))
    if row.get("series"):
        result.update(claim_ids(claims, "P179"))
    if any(item.get("locator") == "claims.P136;claims.P921" for item in _selected_evidence(row)):
        result.update(claim_ids(claims, "P136"))
        result.update(claim_ids(claims, "P921"))
    return result


def coverage(rows: list[dict], proofs: dict[str, dict]) -> dict:
    categories = {name: set() for name in ("contributors", "region", "series", "subjects")}
    for row in rows:
        claims = row.get("extra", {}).get("claims", {})
        categories["contributors"].update(
            identifier.get("value")
            for contributor in row.get("contributors", [])
            for identifier in contributor.get("identifiers", [])
            if identifier.get("scope") == "agent" and isinstance(identifier.get("value"), str)
        )
        if row.get("region"):
            categories["region"].update(claim_ids(claims, "P495"))
        if row.get("series"):
            categories["series"].update(claim_ids(claims, "P179"))
        if any(item.get("locator") == "claims.P136;claims.P921" for item in _selected_evidence(row)):
            categories["subjects"].update(claim_ids(claims, "P136"))
            categories["subjects"].update(claim_ids(claims, "P921"))
    return {
        name: {
            "required_qids": len(values),
            "saved_proof_qids": len(values & set(proofs)),
            "missing_qids": sorted(values - set(proofs)),
        }
        for name, values in categories.items()
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source_records = base.records()
    required = set().union(*(required_ids(row) for row in source_records))
    saved = base.load_saved_proofs(required)
    v1_state_path = V1_OUTPUT / "fetch-state.json"
    v1_state = base.load_json(v1_state_path)
    fetched = {
        item["qid"]: item["proof"]
        for item in v1_state.get("fetched_proofs", [])
        if item.get("qid") in required
    }
    proofs = dict(saved)
    proofs.update(fetched)
    base.OUTPUT = OUTPUT
    base.required_ids = required_ids
    base.save_json(OUTPUT / "repair-input-manifest.json", {
        "version": "scale-content-linked-metadata-repair-input-v2",
        "input_sha256": base.sha256(base.INPUT),
        "input_records": len(source_records),
        "required_linked_qids": len(required),
        "saved_proof_qids": len(saved),
        "v1_fetched_proof_qids": len(fetched),
        "missing_without_network": sorted(required - set(proofs)),
        "network_requests": 0,
        "v1_fetch_state": str(v1_state_path.resolve()),
        "scope": "actual scale_evidence_input metadata and retained CC0 subjects",
        "created_at": base.now(),
    })
    # build() is reused for its immutable raw SHA checks and Wikidata
    # normalization.  It performs no HTTP when given this proof map.
    state = {
        "completed": sorted(fetched),
        "held": sorted(required - set(proofs)),
        "errors": [],
        "request_batches": 0,
    }
    report = base.build(source_records, proofs, state)

    output_path = OUTPUT / "repaired-source-records.jsonl"
    rewritten = []
    for line in output_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        extra = dict(row.get("extra", {}))
        extra["source_record_version"] = "scale-content-linked-metadata-repair-v2"
        extra["metadata_repair_scope"] = "actual native scale_evidence_input metadata and retained CC0 subjects; original content evidence retained"
        row["extra"] = extra
        rewritten.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    output_path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8", newline="\n")
    report.update({
        "version": "scale-content-linked-metadata-repair-v2",
        "required_linked_qids": len(required),
        "proven_proof_qids": len(proofs),
        "fetch_completed": len(fetched),
        "fetch_held": len(required - set(proofs)),
        "fetch_errors": len(v1_state.get("errors", [])),
        "network_requests": 0,
        "v1_fetch_state": str(v1_state_path.resolve()),
        "records_sha256": base.sha256(output_path),
        "completed_at": base.now(),
        "source_scope": "new SourceRecord versions only; actual native input scope; prior source snapshots, ledgers, catalogue, and v1 output unchanged",
    })
    base.save_json(OUTPUT / "repair-manifest.json", report)
    base.save_json(OUTPUT / "coverage.json", {
        "version": "scale-content-linked-metadata-coverage-v2",
        "required_linked_qids": len(required),
        "saved_proof_qids": len(proofs),
        "categories": coverage(source_records, proofs),
        "network_requests": 0,
        "v1_fetch_state": str(v1_state_path.resolve()),
    })
    base.save_json(OUTPUT / "fetch-state-reference.json", {
        "version": "scale-content-linked-metadata-fetch-reference-v1",
        "path": str(v1_state_path.resolve()),
        "sha256": base.sha256(v1_state_path),
        "network_requests_in_v2": 0,
        "read_only": True,
    })
    print(json.dumps({key: report[key] for key in (
        "input_records", "repaired_work_count", "held_work_count",
        "required_linked_qids", "proven_proof_qids", "network_requests",
        "records_sha256",
    )}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
