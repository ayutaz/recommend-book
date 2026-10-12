#!/usr/bin/env python3
"""Bounded NDL fiction-edition PoC; search returns are independently checked.

Run: uv run python scripts/collect_ndl_poc.py --limit-per-query 20
No API parallelism, provider broadening, LLM calls or human-gold generation.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from recommend_book.ingestion import CollectionResult, HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.ndl import NDLAdapter, assess_sru_record
from recommend_book.models import SourceRecord


def query_specs() -> list[dict]:
    specs = []
    for prefix, name, translation in [("913.6", "japanese_fiction_editions", False), ("933.7", "english_fiction_translations", True), ("953.7", "french_fiction_translations", True), ("943.7", "german_fiction_translations", True), ("923.7", "chinese_fiction_translations", True)]:
        specs.append({"name": name + "_2024", "query": f'ndc="{prefix}" AND from="2024" AND until="2024" AND mediatype="books"', "constraints": {"ndc_prefix": prefix, "publication_year": 2024, "language": "jpn", "translation_required": translation}})
    # These are lookup requests, not claims that ISBN automatically identifies a work.
    specs.extend([
        {"name": "isbn_lookup_9784101010137", "query": 'isbn="9784101010137" AND mediatype="books"', "constraints": {"isbn": "9784101010137", "language": "jpn"}},
        {"name": "isbn_lookup_9784151200533", "query": 'isbn="9784151200533" AND mediatype="books"', "constraints": {"isbn": "9784151200533", "language": "jpn", "translation_required": True}},
    ])
    return specs


def collect(output_dir: str | Path, *, limit_per_query: int = 20,
            specs: list[dict] | None = None, adapter: NDLAdapter | None = None) -> dict:
    if not 1 <= limit_per_query <= 100:
        raise ValueError("limit_per_query must be 1..100 for this bounded PoC")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    provider = "iss-ndl-opac"
    specs = specs if specs is not None else query_specs()
    if len(specs) > 20:
        raise ValueError("this PoC supports at most 20 bounded queries")
    adapter = adapter or NDLAdapter(HTTPFetcher(RawStore(output_dir / "raw"), min_interval=1), approved_providers={provider})
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        history = output_dir / "run-manifests"
        history.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (history / (stamp + ".json")).write_bytes(manifest_path.read_bytes())
    accepted = {}
    rejected = []
    queries = []
    for spec in specs:
        result = adapter.search_sru(spec["query"], provider=provider, max_records=limit_per_query)
        decisions = []
        for record in result.records:
            decision = assess_sru_record(record, **spec["constraints"])
            decision["query_name"] = spec["name"]
            decisions.append(decision)
            if decision["accepted"]:
                record.extra.setdefault("poc_selection", []).append({"query_name": spec["name"], "constraints": spec["constraints"], "decision": decision})
                previous = accepted.get(record.source_id)
                if previous:
                    previous.extra.setdefault("poc_selection", []).extend(record.extra["poc_selection"])
                else:
                    accepted[record.source_id] = record
            else:
                rejected.append({"decision": decision, "record": record.model_dump(mode="json")})
        query_result = {"name": spec["name"], "query": spec["query"], "constraints": spec["constraints"], "provider": provider, "status": result.status, "returned": len(result.records), "accepted": sum(d["accepted"] for d in decisions), "rejected": sum(not d["accepted"] for d in decisions), "total_available": result.total_available, "api_position_cutoff": 500, "requested_limit": limit_per_query, "errors": result.errors, "metadata": result.metadata, "decisions": decisions}
        queries.append(query_result)
        with (output_dir / "query-log.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **query_result}, ensure_ascii=False) + "\n")
        print(json.dumps({"query": spec["name"], "status": result.status, "total": result.total_available, "returned": len(result.records), "accepted": sum(d["accepted"] for d in decisions)}, ensure_ascii=True), flush=True)
    records = list(accepted.values())
    write_jsonl(records, output_dir / "records.jsonl")
    with (output_dir / "rejected.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for value in rejected:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    failed = any(q["status"] == "failed" for q in queries)
    truncated = any(q["status"] == "truncated" for q in queries)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "status": "failed" if failed else "truncated" if truncated else "complete", "provider": provider, "source": "NDLサーチ API", "license": "CC-BY-4.0 compatible metadata terms; provider-specific attribution retained", "queries": queries, "accepted_unique_editions": len(records), "returned_records": sum(q["returned"] for q in queries), "rejected_records": len(rejected), "deduplication_unit": "NDL source record ID only; these counts are not resolved works", "language_distribution": dict(Counter(r.language or "unknown" for r in records)), "translation_source_languages": dict(Counter(r.extra.get("translation_source_language") or "unknown" for r in records)), "prose_external_llm_allowed": sum(e.llm_input_allowed and e.kind in {"synopsis", "note", "excerpt", "description"} for r in records for e in r.evidence), "records_path": str((output_dir / "records.jsonl").resolve()), "raw_manifest": str((output_dir / "raw" / "manifest.jsonl").resolve()), "limitations": ["Convenience sample of Japanese-language publications, not a representative Japanese/translated work population", "Query counts are not summed as distinct works; duplicate records across queries retained in selection provenance", "Only returned metadata satisfying explicit constraints admitted; missing fields and server-side false matches quarantined", "SRU searches identified metadata; provider attribution is source/query provenance, not universal field-level rights clearance", "NDL prose remains unresolved and is excluded from external LLM input", "Source record counts do not establish fiction form or precise work identity beyond bibliographic NDC selection"]}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def reassess_saved_snapshot(output_dir: str | Path) -> dict:
    """Recheck recorded candidates with current admission rules, without API calls.

    This recomputes admission only, preserving the stored normalized values, raw hash,
    original retrieval timestamp and response status. It does not claim a fresh scrape.
    """
    output_dir = Path(output_dir)
    previous = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    candidates = list(read_jsonl(output_dir / "records.jsonl"))
    rejected_path = output_dir / "rejected.jsonl"
    rejected = [json.loads(line) for line in rejected_path.read_text(encoding="utf-8").splitlines()] if rejected_path.exists() else []
    by_query = {}
    for record in candidates:
        for selection in record.extra.get("poc_selection", []):
            by_query.setdefault(selection["query_name"], {})[record.source_id] = record
    for value in rejected:
        record = SourceRecord.model_validate(value["record"])
        by_query.setdefault(value["decision"]["query_name"], {})[record.source_id] = record
    query_by_text = {q["query"]: q for q in previous["queries"]}
    class SnapshotAdapter:
        def search_sru(self, query, **kwargs):
            original = query_by_text[query]
            records = []
            for value in by_query.get(original["name"], {}).values():
                record = value.model_copy(deep=True)
                record.extra.pop("poc_selection", None)
                records.append(record)
            return CollectionResult(records=records, status=original["status"], total_available=original["total_available"], errors=original["errors"], metadata=original["metadata"])
    specs = [{"name": q["name"], "query": q["query"], "constraints": q["constraints"]} for q in previous["queries"]]
    result = collect(output_dir, limit_per_query=previous["queries"][0]["requested_limit"], specs=specs, adapter=SnapshotAdapter())
    result["acquisition_mode"] = "reassess_saved_snapshot"
    result["source_snapshot_created_at"] = previous.get("source_snapshot_created_at", previous["created_at"])
    (output_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/ndl-poc"))
    parser.add_argument("--limit-per-query", type=int, default=20)
    parser.add_argument("--reassess-snapshot", action="store_true", help="Recheck saved candidates without HTTP calls; preserves original retrieval/hash")
    args = parser.parse_args()
    manifest = reassess_saved_snapshot(args.output_dir) if args.reassess_snapshot else collect(args.output_dir, limit_per_query=args.limit_per_query)
    print(json.dumps({"status": manifest["status"], "accepted_unique_editions": manifest["accepted_unique_editions"], "manifest": str(args.output_dir / "manifest.json")}, ensure_ascii=True))
    return 1 if manifest["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
