"""Collect a fixed, language-stratified Wikidata candidate frame with resumable pages."""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import time
from datetime import datetime, timezone

from recommend_book.ingestion.core import HTTPFetcher, RawStore, write_jsonl, read_jsonl
from recommend_book.ingestion.wikidata import WikidataAdapter, claim_values
from recommend_book.models import Identifier


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/wikidata-poc"))
    parser.add_argument("--target", type=int, default=2400, help="Candidate frame sizing reference; final distinct-work target is frozen by prepare_poc.py")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    fetcher = HTTPFetcher(RawStore(args.root / "raw"), user_agent="recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)", min_interval=1.1)
    adapter = WikidataAdapter(fetcher)
    # Oversample original-language groups to permit later age/author/genre balancing.
    if args.target < 1:
        raise ValueError("target must be positive")
    base_groups = [("ja", ["Q5287"], 1000), ("en", ["Q1860"], 1000), ("fr", ["Q150"], 200), ("de", ["Q188"], 160), ("es", ["Q1321"], 160), ("ru", ["Q7737"], 140), ("zh", ["Q7850"], 120), ("ko", ["Q9176"], 100), ("it", ["Q652"], 100), ("pt", ["Q5146"], 80), ("other", ["Q9610", "Q1568", "Q13955"], 100)]
    groups = [(group, languages, max(1, round(count * args.target / 2400))) for group, languages, count in base_groups]
    selection_path = args.root / "selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else {"created_at": datetime.now(timezone.utc).isoformat(), "pages": [], "ids": [], "queries": [], "limitations": ["Selected Wikidata instance coverage; not the world fiction population", "Metadata language is not inferred from translated titles"]}
    frame_context = {"groups": groups, "forms": ["Q8261", "Q747381", "Q49084"], "discovery": "P7937_or_P31_excluding_P629"}
    # Do not reuse the earlier sparse P31-only frame after discovery changes.
    if selection.get("queries") and any("wdt:P7937" not in q.get("query", "") for q in selection["queries"]):
        raise ValueError("Legacy P31-only selection; use a new --root for the corrected discovery")
    previous_context = selection.get("frame_context")
    if previous_context is not None and json.dumps(previous_context, sort_keys=True) != json.dumps(frame_context, sort_keys=True):
        raise ValueError("Discovery groups/configuration changed; use a new --root")
    selection["frame_context"] = frame_context
    seen = set(selection["ids"])
    for group, language_ids, count in groups:
        for offset in range(0, count, 500):
            page_key = f"{group}:{offset}"
            if page_key in selection["pages"]:
                continue
            kwargs = {"limit": min(500, count - offset), "offset": offset, "language_qids": language_ids}
            if "instance_qids" in inspect.signature(adapter.query_work_ids).parameters:
                kwargs["instance_qids"] = ["Q8261", "Q747381", "Q49084"]
            ids, result = adapter.query_work_ids(**kwargs)
            if result.status == "failed":
                selection.setdefault("errors", []).append({"page": page_key, "errors": result.errors})
                save_json(selection_path, selection)
                print(json.dumps({"page": page_key, "status": "failed", "errors": result.errors}), flush=True)
                return 1
            selection["ids"].extend(value for value in ids if value not in seen)
            seen.update(ids)
            selection["queries"].append({"group": group, "count": len(ids), **result.metadata})
            selection["pages"].append(page_key)
            save_json(selection_path, selection)
            print(json.dumps({"page": page_key, "status": result.status, "new_ids": len(ids), "total_unique": len(seen)}), flush=True)
            if len(ids) < kwargs["limit"]:
                break
    ids = selection["ids"]
    records_path = args.root / "works.jsonl"
    records = list(read_jsonl(records_path)) if records_path.exists() else []
    done = {record.source_id for record in records}
    for index in range(0, len(ids), 50):
        batch = [qid for qid in ids[index:index + 50] if qid not in done]
        if not batch:
            continue
        started = time.monotonic()
        result = adapter.fetch_entities(batch)
        records.extend(result.records)
        done.update(record.source_id for record in result.records)
        write_jsonl(records, records_path)
        print(json.dumps({"fetched_works": len(records), "frame_size": len(ids), "status": result.status, "seconds": round(time.monotonic() - started, 1), "errors": result.errors}), flush=True)
        if result.status == "failed":
            return 1
    references = set()
    authors = set()
    for record in records:
        for prop in ("P50", "P136", "P495", "P179", "P921", "P674", "P840", "P31"):
            for value in claim_values({"claims": record.extra.get("claims", {})}, prop):
                if isinstance(value, dict) and "id" in value:
                    references.add(value["id"])
                    if prop == "P50":
                        authors.add(value["id"])
    labels_path = args.root / "linked-labels.json"
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else {}
    reference_entities_path = args.root / "linked-entities.json"
    linked = json.loads(reference_entities_path.read_text(encoding="utf-8")) if reference_entities_path.exists() else {}
    remaining = sorted(references - set(labels))
    for index in range(0, len(remaining), 50):
        batch = remaining[index:index + 50]
        response = fetcher.get("https://www.wikidata.org/w/api.php", source="wikidata", params={"action": "wbgetentities", "ids": "|".join(batch), "props": "labels|claims", "languages": "en|ja|fr|de|es|ru|zh|ko|it|pt|ar|hi|bn", "format": "json"})
        if response.status != "success":
            print(json.dumps({"linked_status": "failed", "error": response.error}), flush=True)
            return 1
        payload = json.loads(response.content)
        if "error" in payload:
            raise ValueError(payload["error"])
        for qid, entity in payload["entities"].items():
            entity_labels = entity.get("labels", {})
            preferred = entity_labels.get("en") or entity_labels.get("ja") or next(iter(entity_labels.values()), {"value": qid})
            labels[qid] = preferred["value"]
            linked[qid] = {"entity": entity, "raw_sha256": response.artifact.sha256, "raw_path": response.artifact.path}
        save_json(labels_path, labels)
        save_json(reference_entities_path, linked)
        print(json.dumps({"linked_entities": len(labels), "remaining_total": len(references)}), flush=True)
    # Reparse the original saved entity payloads with verified labels; no refetch needed.
    for record in records:
        for contributor in record.contributors:
            qids = [identifier.value for identifier in contributor.identifiers if identifier.source == "wikidata"]
            if qids:
                qid = qids[0]
                contributor.name = labels.get(qid, contributor.name)
                author_entity = linked.get(qid, {}).get("entity", {})
                for ol_id in claim_values(author_entity, "P648"):
                    if isinstance(ol_id, str) and ol_id.startswith("OL") and ol_id.endswith("A"):
                        contributor.identifiers.append(Identifier(source="openlibrary", value="/authors/" + ol_id, scope="agent"))
        for evidence in record.evidence:
            if evidence.locator == "claims.P136":
                evidence.text = "; ".join(labels.get(value, value) for value in record.subjects)
        record.subjects = [labels.get(value, value) for value in record.subjects]
        record.region = [labels.get(value, value) for value in record.region]
        series = claim_values({"claims": record.extra.get("claims", {})}, "P179")
        if series:
            record.series = "; ".join(labels.get(v.get("id"), v.get("id", "")) for v in series if isinstance(v, dict)) or None
        record.extra["linked_label_provenance"] = {qid: {"label": labels[qid], "raw_path": linked[qid]["raw_path"], "raw_sha256": linked[qid]["raw_sha256"]} for prop in ("P50", "P136", "P495", "P179", "P921", "P674", "P840", "P31") for val in claim_values({"claims": record.extra.get("claims", {})}, prop) if isinstance(val, dict) and (qid := val.get("id")) in linked}
    write_jsonl(records, args.root / "works-enriched.jsonl")
    print(json.dumps({"status": "complete", "works": len(records), "linked_entities": len(linked), "output": str(args.root / "works-enriched.jsonl")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
