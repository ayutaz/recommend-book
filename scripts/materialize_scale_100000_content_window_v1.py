"""Materialize new v19 content rows into immutable per-window native inputs.

This only joins saved Wikipedia prose rows to the exact read-only Wikidata
authority record.  It does not fetch, call a model, or modify the catalogue.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V19 = ROOT / "data/autonomous-next-v3/scale-100000-v1/content"
CATALOGUE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def qid_of(row: dict) -> str:
    qids = {
        item["value"]
        for item in row.get("identifiers", [])
        if item.get("source") == "wikidata" and item.get("scope") == "work"
    }
    if len(qids) != 1:
        raise ValueError(f"expected one Wikidata work QID, got {sorted(qids)}")
    return next(iter(qids))


def already_materialized() -> set[str]:
    qids: set[str] = set()
    for path in (V19 / "windows").glob("global-*/minimal-native-records.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                qids.add(qid_of(json.loads(line)))
    return qids


def source_rows() -> list[tuple[Path, int, dict]]:
    rows: list[tuple[Path, int, dict]] = []
    for path in sorted(V19.glob("content-window-*/records.jsonl")):
        digest = sha(path)
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            narrative = [e for e in row.get("evidence", []) if e.get("kind") in {"synopsis", "excerpt"}]
            if not narrative:
                continue
            qid_of(row)
            row["_source_file_sha256"] = digest
            rows.append((path, line_no, row))
    return rows


def select_new_rows(rows: list[tuple[Path, int, dict]], excluded: set[str], take: int) -> list[tuple[Path, int, dict]]:
    """Select each work once across all prior sealed windows."""
    selected: list[tuple[Path, int, dict]] = []
    seen = set(excluded)
    for path, line_no, row in rows:
        qid = qid_of(row)
        if qid in seen:
            continue
        seen.add(qid)
        selected.append((path, line_no, row))
        if len(selected) == take:
            return selected
    raise ValueError(f"only {len(selected)} new narrative rows available")


def authority_rows(qids: set[str]) -> dict[str, tuple[str, str, dict]]:
    result: dict[str, tuple[str, str, dict]] = {}
    with sqlite3.connect(f"file:{CATALOGUE.resolve().as_posix()}?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        placeholders = ",".join("?" for _ in qids)
        query = (
            "SELECT w.qid,w.version_id,v.record_sha256,v.record_json "
            "FROM works w JOIN source_versions v USING(version_id) "
            f"WHERE w.qid IN ({placeholders})"
        )
        for qid, version, record_sha, payload in db.execute(query, tuple(sorted(qids))):
            authority = json.loads(payload)
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != record_sha:
                raise ValueError(f"authority record SHA mismatch: {qid}")
            result[qid] = (version, record_sha, authority)
    return result


def enrich(authority: dict, candidate: dict, source_path: Path, line_no: int, version: str, record_sha: str) -> tuple[dict, dict]:
    qid = qid_of(candidate)
    proof = {}
    for evidence in candidate.get("evidence", []):
        proof = evidence.get("identity_proof") or evidence.get("attribution", {}).get("identity_proof") or {}
        if proof:
            break
    if candidate.get("extra", {}).get("wikibase_item_verified") != qid:
        raise ValueError(f"missing exact pageprops proof: {qid}")
    if proof.get("wikidata_work_id") not in {None, qid} or proof.get("pageprops_wikibase_item") not in {None, qid}:
        raise ValueError(f"content identity proof mismatch: {qid}")
    raw_path = Path(candidate["raw_path"])
    if hashlib.sha256(raw_path.read_bytes()).hexdigest() != candidate["raw_sha256"]:
        raise ValueError(f"content raw changed: {qid}")
    narrative = [copy.deepcopy(e) for e in candidate.get("evidence", []) if e.get("kind") in {"synopsis", "excerpt"}]
    authority_evidence = [copy.deepcopy(e) for e in authority.get("evidence", []) if e.get("kind") == "description"]
    provenance = {
        "content_record_path": rel(source_path),
        "content_record_file_sha256": candidate.pop("_source_file_sha256"),
        "content_record_line": line_no,
        "content_source_id": candidate["source_id"],
        "content_source_url": candidate.get("source_url"),
        "content_raw_path": rel(raw_path),
        "content_raw_sha256": candidate["raw_sha256"],
        "article_language": candidate.get("extra", {}).get("article_language"),
        "page_id": candidate.get("extra", {}).get("page_id"),
        "revision_id": candidate.get("extra", {}).get("revision_id"),
        "wikibase_item_verified": qid,
        "identity_proof": proof,
        "rights_route": "wikipedia-screened-prose",
        "rights_policy_id": "wikipedia-screened-prose",
        "authority_version_id": version,
        "authority_record_sha256": record_sha,
    }
    full = copy.deepcopy(authority)
    full["evidence"] = [*copy.deepcopy(authority.get("evidence", [])), *narrative]
    full.setdefault("extra", {})["scale_100000_content_v1"] = provenance
    minimal = copy.deepcopy(authority)
    minimal["contributors"] = []
    minimal["subjects"] = []
    minimal["region"] = []
    minimal["work_refs"] = []
    minimal["language"] = None
    minimal["original_language"] = None
    minimal["first_publication_year"] = None
    minimal["publication_year"] = None
    minimal["publisher"] = None
    minimal["series"] = None
    minimal["volume"] = None
    minimal["evidence"] = [*authority_evidence, *narrative]
    minimal["extra"] = {
        "claims": {},
        "linked_label_provenance": {},
        "scale_100000_content_v1": provenance,
        "authority_raw_path": authority.get("raw_path"),
        "authority_raw_sha256": authority.get("raw_sha256"),
    }
    ordered_evidence = [copy.deepcopy(e) for e in narrative]
    ordered = {
        "schema_version": "scale-100000-content-input-v1",
        "cohort": 0,
        "window_index": 0,
        "work_id": f"wd:{qid}",
        "qid": qid,
        "title": next((t["value"] for t in minimal.get("titles", []) if t.get("kind") == "preferred"), qid),
        "source_record_path": provenance["content_record_path"],
        "source_record_file_sha256": provenance["content_record_file_sha256"],
        "source_record_line": line_no,
        "source_id": candidate["source_id"],
        "evidence": ordered_evidence,
        "rights_policy_id": "wikipedia-screened-prose",
        "source_url": candidate.get("source_url"),
    }
    return full, (minimal, ordered)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--global-index", type=int, required=True)
    parser.add_argument("--take", type=int, default=100)
    args = parser.parse_args()
    if args.take < 1 or args.take > 100:
        raise ValueError("take must be 1..100")
    existing = already_materialized()
    candidates = select_new_rows(source_rows(), existing, args.take)
    authorities = authority_rows({qid_of(row) for _, _, row in candidates})
    window = V19 / "windows" / f"global-{args.global_index}"
    window.mkdir(parents=True, exist_ok=False)
    full_path = window / "native-records.jsonl"
    minimal_path = window / "minimal-native-records.jsonl"
    ordered_path = window / "ordered-inputs.jsonl"
    inventory_path = V19 / "inventory" / f"worker-v1-accepted-global{args.global_index}.jsonl"
    full_rows, minimal_rows, ordered_rows, inventory_rows = [], [], [], []
    for path, line_no, candidate in candidates:
        qid = qid_of(candidate)
        if qid not in authorities:
            raise ValueError(f"missing current authority: {qid}")
        version, record_sha, authority = authorities[qid]
        full, (minimal, ordered) = enrich(authority, candidate, path, line_no, version, record_sha)
        ordered["window_index"] = args.global_index
        full_rows.append(full)
        minimal_rows.append(minimal)
        ordered_rows.append(ordered)
        inventory_rows.append(minimal["extra"]["scale_100000_content_v1"] | {
            "qid": qid,
            "title": ordered["title"],
            "source_record_path": ordered["source_record_path"],
            "source_record_file_sha256": ordered["source_record_file_sha256"],
            "source_record_line": line_no,
            "source_id": ordered["source_id"],
            "source_url": ordered["source_url"],
            "raw_path": minimal["raw_path"],
            "raw_sha256": minimal["raw_sha256"],
            "evidence": ordered["evidence"],
            "rights_policy_id": ordered["rights_policy_id"],
        })
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    for path, rows in ((full_path, full_rows), (minimal_path, minimal_rows), (ordered_path, ordered_rows), (inventory_path, inventory_rows)):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    manifest = {
        "schema_version": "scale-100000-content-window-v1",
        "window_index": args.global_index,
        "cohort": 0,
        "run_name": f"scale100k-content-global{args.global_index}",
        "status": "MATERIALIZED_FOR_NATIVE_SOURCE_PREP",
        "selected_count": len(minimal_rows),
        "unique_qids": len({qid_of(row) for row in minimal_rows}),
        "qids": [qid_of(row) for row in minimal_rows],
        "ordered_inputs_path": rel(ordered_path),
        "ordered_inputs_sha256": sha(ordered_path),
        "source_inventory_path": rel(inventory_path),
        "source_inventory_sha256": sha(inventory_path),
        "native_records_path": rel(full_path),
        "native_records_sha256": sha(full_path),
        "minimal_native_records_path": rel(minimal_path),
        "minimal_native_records_sha256": sha(minimal_path),
        "model_calls": 0,
        "web_requests": 0,
        "raw_copied": False,
        "old_outputs_modified": False,
    }
    (window / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"window": args.global_index, "selected": len(minimal_rows), "qids": manifest["qids"], "manifest": rel(window / "manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
