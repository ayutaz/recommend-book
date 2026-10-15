#!/usr/bin/env python3
"""Discover bounded P629 version/edition/translation records for a fixed work frame.

API calls are serialized. The edition cap is not an increase in distinct works.
Run only after other Wikidata collection using a different raw root has finished.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from recommend_book.ingestion import HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.wikidata import WikidataAdapter


def collect(input_path: str | Path, output_dir: str | Path, *, limit: int = 300,
            work_batch_size: int = 100, query_page_size: int = 500,
            adapter: WikidataAdapter | None = None, linked_labels: dict | None = None) -> dict:
    if not 1 <= limit <= 500 or not 30 <= work_batch_size <= 100 or not 1 <= query_page_size <= 500:
        raise ValueError("edition limit 1..500, work batch 30..100, query page 1..500 required")
    input_path, output_dir = Path(input_path), Path(output_dir)
    input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    frame = list(read_jsonl(input_path))
    work_ids = list(dict.fromkeys(i.value for r in frame if r.record_kind == "work" for i in r.identifiers if i.source == "wikidata" and i.scope == "work"))
    if not work_ids:
        raise ValueError("fixed input contains no scoped Wikidata work identifiers")
    frame_set = set(work_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = adapter or WikidataAdapter(HTTPFetcher(RawStore(output_dir / "raw"), min_interval=1,
        host_intervals={"query.wikidata.org": 61}))
    edition_ids = {}
    expected_relations = {}
    pages = []
    errors = []
    requested_works = set()
    cap_reached = False
    discovery_complete = False
    for start in range(0, len(work_ids), work_batch_size):
        batch = work_ids[start:start + work_batch_size]
        offset = 0
        requested_works.update(batch)
        while len(edition_ids) < limit:
            page_limit = min(query_page_size, limit - len(edition_ids))
            ids, result = adapter.query_edition_ids(batch, limit=page_limit, offset=offset)
            page = {"work_ids": batch, "offset": offset, "limit": page_limit, "status": result.status, "edition_ids": ids, "metadata": result.metadata, "errors": result.errors}
            pages.append(page)
            with (output_dir / "query-pages.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(page, ensure_ascii=False) + "\n")
            if result.status == "failed":
                errors.extend(result.errors)
                break
            for relation in result.metadata.get("relations", []):
                expected_relations.setdefault(relation["edition"], set()).add(relation["work"])
            for qid in ids:
                if len(edition_ids) < limit:
                    edition_ids.setdefault(qid, None)
            print(json.dumps({"phase": "discover", "work_batch_start": start, "works_requested": len(requested_works), "candidate_editions": len(edition_ids), "page_status": result.status}, ensure_ascii=True), flush=True)
            if len(edition_ids) >= limit:
                cap_reached = True
                break
            if result.status == "complete":
                break
            next_offset = result.metadata.get("next_offset", offset + result.metadata.get("relation_rows", 0))
            if next_offset <= offset:
                errors.append("truncated edition page did not advance offset")
                break
            offset = next_offset
        if errors or cap_reached:
            break
        if start + len(batch) >= len(work_ids):
            discovery_complete = True
    records = []
    quarantined = []
    ids = list(edition_ids)
    for start in range(0, len(ids), 50):
        result = adapter.fetch_entities(ids[start:start + 50], kind="edition", linked_labels=linked_labels)
        if result.status == "failed":
            errors.extend(result.errors)
        for record in result.records:
            refs = {i.value for i in record.work_refs if i.source == "wikidata" and i.scope == "work"}
            expected = expected_relations.get(record.source_id, set())
            reasons = []
            if not refs or not refs.intersection(expected):
                reasons.append("entity_P629_does_not_confirm_discovered_relation")
            if refs - frame_set:
                reasons.append("entity_has_out_of_frame_work_reference")
            if record.source_id in frame_set:
                reasons.append("edition_ID_conflicts_with_fixed_work_scope")
            if reasons:
                quarantined.append({"source_id": record.source_id, "reasons": reasons, "record": record.model_dump(mode="json")})
                continue
            record.extra["edition_discovery"] = {"frame_sha256": input_hash, "frame_path": str(input_path.resolve()), "work_refs": sorted(refs), "expected_query_work_refs": sorted(expected), "query_raw_provenance": [{"path": p["metadata"].get("raw_path"), "sha256": p["metadata"].get("raw_sha256")} for p in pages if record.source_id in p["edition_ids"]]}
            records.append(record)
        # Flush valid records after each batch. A later failure leaves reviewable partial data.
        write_jsonl(records, output_dir / "records.jsonl")
        print(json.dumps({"phase": "entities", "entity_candidates_processed": min(start + 50, len(ids)), "accepted_editions": len(records), "quarantined": len(quarantined)}, ensure_ascii=True), flush=True)
        if result.status == "failed":
            break
    write_jsonl(records, output_dir / "records.jsonl")
    with (output_dir / "quarantined.jsonl").open("w", encoding="utf-8", newline="\n") as log:
        for value in quarantined:
            log.write(json.dumps(value, ensure_ascii=False) + "\n")
    referenced = {i.value for r in records for i in r.work_refs if i.source == "wikidata"}
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "failed" if errors else "truncated" if cap_reached or not discovery_complete else "complete", "source": "wikidata", "rights_policy_id": "wikidata-cc0", "frame_path": str(input_path.resolve()), "frame_sha256": input_hash, "frame_work_count": len(work_ids), "works_requested": len(requested_works), "candidate_edition_cap": limit, "candidate_editions": len(ids), "accepted_edition_records": len(records), "quarantined_edition_records": len(quarantined), "added_work_count": 0, "existing_works_referenced": len(referenced), "discovery_complete": discovery_complete, "cap_reached": cap_reached, "edition_relation_rows": sum(p["metadata"].get("relation_rows", 0) for p in pages), "query_pages": len(pages), "work_batch_size": work_batch_size, "query_page_size": query_page_size, "edition_language_distribution": dict(Counter(r.language or "unknown" for r in records)), "editions_without_author_claim": sum(not any(c.role == "author" for c in r.contributors) for r in records), "editions_with_translator_claim": sum(any(c.role == "translator" for c in r.contributors) for r in records), "editions_with_isbn": sum(any(i.source == "isbn" for i in r.identifiers) for r in records), "editions_with_publication_year": sum(r.publication_year is not None for r in records), "author_metadata_not_copied_from_works": True, "errors": errors, "records_path": str((output_dir / "records.jsonl").resolve()), "limitations": ["P629 means version, edition or translation of; not every relation is a separately published manifestation", "Additional entity-type/ISBN/publication metadata remains available for review; no independent corroboration fabricated by copying work authors", "This is an edition relation convenience sample of the fixed work frame, not all editions or a representative edition population", "Cap and source coverage limit discovery; records are not added to distinct-work counts"]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/wikipedia-frame-v2-input.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/wikidata-editions-poc"))
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--work-batch-size", type=int, default=100)
    parser.add_argument("--query-page-size", type=int, default=500)
    parser.add_argument("--linked-labels", type=Path, help="Optional already verified flat QID-to-label JSON; no work author copying")
    args = parser.parse_args()
    labels = json.loads(args.linked_labels.read_text(encoding="utf-8")) if args.linked_labels else None
    manifest = collect(args.input, args.output_dir, limit=args.limit, work_batch_size=args.work_batch_size, query_page_size=args.query_page_size, linked_labels=labels)
    print(json.dumps(manifest, ensure_ascii=True), flush=True)
    return 1 if manifest["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
