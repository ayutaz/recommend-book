"""Extend the scale novel catalogue with new Wikidata work IDs.

This is an additive v2 route.  The v1 acquisition snapshot and helpers remain
immutable.  Discovery starts after the v1 global range, filters the restored
v1 IDs locally, and stores every request, result, and held input under a new
checkpoint root.  Entity requests are one-shot: a failed or scope-conflicting
batch is held permanently so a resume cannot silently retry it.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace

from recommend_book.ingestion.core import HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.wikidata import ENTITY_URL, QUERY_URL, WikidataAdapter
from recommend_book.scale_catalogue_v1 import ScaleCatalogue, _json, _sha


NOVEL_FORMS = ("Q8261", "Q747381")
LANGUAGES = ("en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn")
CONDITIONS_VERSION = "scale-source-conditions-v2"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: str | Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{__import__('os').getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load(path: str | Path) -> object:
    return json.loads(Path(path).read_bytes())


def _qid(value: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", str(value)):
        raise ValueError("invalid Wikidata QID")
    return str(value)


def sha_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextmanager
def catalogue_writer_lock(catalogue_path: Path):
    """Serialize metadata/native catalogue writers without deleting stale locks."""
    lock_path = catalogue_path.parent.parent / "catalogue-writer.lock"
    payload = {"pid": __import__("os").getpid(), "started_at": now(),
               "catalogue": str(catalogue_path.resolve()), "owner": "scale-30000-metadata-v2"}
    try:
        with lock_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise RuntimeError(f"catalogue writer lock exists; inspect live owner before removal: {lock_path}") from exc
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)


def discovery_query(*, limit: int, offset: int) -> str:
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid discovery page")
    return (
        "SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } "
        "{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } "
        "?work wdt:P136 ?genre. "
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY ?work LIMIT {limit} OFFSET {offset}"
    )


def language_discovery_query(language: str, *, limit: int, offset: int) -> str:
    language = _qid(language)
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid language discovery page")
    return (
        "SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } "
        "{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } "
        f"VALUES ?language {{ wd:{language} }} ?work wdt:P407 ?language. "
        "?work wdt:P136 ?genre. "
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY ?work LIMIT {limit} OFFSET {offset}"
    )


def broad_discovery_query(*, limit: int, offset: int) -> str:
    """Find direct novel/light-novel declarations without requiring P136."""
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid broad discovery page")
    return (
        "SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } "
        "{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } "
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY ?work LIMIT {limit} OFFSET {offset}"
    )


def load_existing_ids(*, baseline_selection: str | Path, catalogue: str | Path) -> set[str]:
    selection = load(baseline_selection)
    ids = {_qid(value) for value in selection.get("ids", [])}
    with sqlite3.connect(Path(catalogue).resolve().as_uri() + "?mode=ro", uri=True) as db:
        ids.update(_qid(row[0]) for row in db.execute("SELECT qid FROM works"))
    return ids


def fresh_conditions(output: Path, fetcher: HTTPFetcher) -> dict:
    """Fetch and pin the current Wikidata licensing page once per output root."""
    path = output / "conditions" / "wikidata-licensing.bin"
    receipt_path = output / "source-conditions-v2.json"
    if receipt_path.exists() and path.exists():
        receipt = load(receipt_path)
        raw = path.read_bytes()
        if _sha(raw) != receipt["policy_snapshot"]["sha256"] or b"cc0" not in raw.lower():
            raise ValueError("saved v2 conditions receipt does not reproduce policy bytes")
        return receipt
    result = fetcher.get(
        "https://www.wikidata.org/wiki/Wikidata:Licensing",
        source="wikidata",
        headers={"Accept": "text/html"},
    )
    if result.status != "success":
        raise RuntimeError(f"Wikidata licensing conditions unavailable: {result.error}")
    raw = Path(result.artifact.path).read_bytes()
    if b"cc0" not in raw.lower():
        raise ValueError("Wikidata licensing snapshot does not contain CC0")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    receipt = {
        "version": CONDITIONS_VERSION,
        "source": "wikidata",
        "policy_id": "wikidata-cc0",
        "policy_snapshot": {"path": str(path.resolve()), "sha256": _sha(raw)},
        "retrieved_at": result.artifact.retrieved_at,
        "usages": {u: "allowed" for u in ("acquire", "local_storage", "external_llm", "display", "redistribution", "commercial")},
        "allowed_domains": ["www.wikidata.org", "query.wikidata.org"],
        "metadataonly_scope": True,
        "scope": "Wikidata structured entity data only; no linked Wikipedia or external text rights inherited",
        "request": {"url": "https://www.wikidata.org/wiki/Wikidata:Licensing", "raw_sha256": result.artifact.sha256},
    }
    save(receipt_path, receipt)
    return receipt


def discover(output: Path, fetcher: HTTPFetcher, *, existing_ids: set[str], target_new: int,
             start_offset: int = 12000, max_offset: int = 100000) -> dict:
    path = output / "discovery.json"
    context = {"version": "scale-discovery-v2", "start_offset": start_offset,
               "page_size": 500, "target_new": target_new, "max_offset": max_offset,
               "forms": list(NOVEL_FORMS), "requires_P136": True,
               "excludes_P629": True, "excludes_edition_P31": True}
    state = load(path) if path.exists() else {"frame": context, "pages": {}, "ids": [], "held_ids": []}
    if state.get("frame") != context:
        raise ValueError("discovery frame changed")
    chosen = set(state["ids"])
    global_pages = [page for key, page in state["pages"].items() if key.isdigit()]
    global_success = any(page.get("status") == "success" for page in global_pages)
    global_complete = any(page.get("status") == "success" and page.get("count", 0) < 500 for page in global_pages)
    if not global_complete:
        for offset in range(start_offset, max_offset, 500):
            if len(chosen) >= target_new:
                break
            key = str(offset)
            page = state["pages"].get(key)
            if page is not None:
                continue
            query = discovery_query(limit=500, offset=offset)
            page = {"offset": offset, "query": query, "started_at": now(), "status": "started", "count": 0}
            state["pages"][key] = page
            save(path, state)
            response = fetcher.get(QUERY_URL, source="wikidata",
                                   params={"query": query, "format": "json"},
                                   headers={"Accept": "application/sparql-results+json"})
            if response.status != "success":
                page.update(status="held", error=response.error, status_code=response.status_code,
                            completed_at=now())
                save(path, state)
                print(json.dumps({"discovery_page": offset, "status": "held", "error": response.error}), flush=True)
                if response.status_code == 429 or "429" in str(response.error):
                    break
                continue
            try:
                bindings = json.loads(response.content)["results"]["bindings"]
                ids = [_qid(row["work"]["value"].rsplit("/", 1)[-1]) for row in bindings]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                page.update(status="held", error=f"invalid SPARQL response: {exc}", completed_at=now())
                save(path, state)
                continue
            fresh = [qid for qid in ids if qid not in existing_ids and qid not in chosen]
            chosen.update(fresh)
            page.update(status="success", count=len(ids), fresh_count=len(fresh),
                        raw_path=response.artifact.path, raw_sha256=response.artifact.sha256,
                        completed_at=now())
            state["ids"] = sorted(chosen, key=lambda q: int(q[1:]))
            save(path, state)
            print(json.dumps({"discovery_page": offset, "returned": len(ids),
                              "new": len(fresh), "new_total": len(chosen)}), flush=True)
            if len(ids) < 500:
                global_complete = True
                break

    # The global ordered result currently ends before the requested expansion.
    # Use a second, disjoint discovery frame by original language.  It shares
    # the same WDQS host gate and local old-ID exclusion, but has independent
    # page keys and receipts so a held global page is never retried.
    languages = ("Q1860", "Q5287", "Q150", "Q188", "Q1321", "Q7737", "Q7850", "Q652", "Q5146", "Q9176", "Q9610", "Q1568", "Q13955")
    for language in languages if global_success or chosen else ():
        if len(chosen) >= target_new:
            break
        for offset in range(0, 20000, 500):
            if len(chosen) >= target_new:
                break
            key = f"lang:{language}:{offset}"
            if key in state["pages"]:
                continue
            query = language_discovery_query(language, limit=500, offset=offset)
            page = {"route": "language", "language": language, "offset": offset,
                    "query": query, "started_at": now(), "status": "started", "count": 0}
            state["pages"][key] = page
            save(path, state)
            response = fetcher.get(QUERY_URL, source="wikidata",
                                   params={"query": query, "format": "json"},
                                   headers={"Accept": "application/sparql-results+json"})
            if response.status != "success":
                page.update(status="held", error=response.error, status_code=response.status_code,
                            completed_at=now())
                save(path, state)
                print(json.dumps({"discovery_page": key, "status": "held", "error": response.error}), flush=True)
                if response.status_code == 429 or "429" in str(response.error):
                    break
                # Do not walk thousands of pages while WDQS is returning the
                # same transport failure. The page is held and the route ends.
                break
            try:
                bindings = json.loads(response.content)["results"]["bindings"]
                ids = [_qid(row["work"]["value"].rsplit("/", 1)[-1]) for row in bindings]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                page.update(status="held", error=f"invalid SPARQL response: {exc}", completed_at=now())
                save(path, state)
                continue
            fresh = [qid for qid in ids if qid not in existing_ids and qid not in chosen]
            chosen.update(fresh)
            page.update(status="success", count=len(ids), fresh_count=len(fresh),
                        raw_path=response.artifact.path, raw_sha256=response.artifact.sha256,
                        completed_at=now())
            state["ids"] = sorted(chosen, key=lambda q: int(q[1:]))
            save(path, state)
            print(json.dumps({"discovery_page": key, "returned": len(ids),
                              "new": len(fresh), "new_total": len(chosen)}), flush=True)
            if len(ids) < 500:
                break

    # P136 is intentionally not required for the searchable catalogue target.
    # Rows without P136 remain displayable source-declared novels but are not
    # counted as source-backed recommendation candidates.
    broad_pages = [page for key, page in state["pages"].items() if key.startswith("broad:")]
    broad_complete = any(page.get("status") == "success" and page.get("count", 0) < 500 for page in broad_pages)
    broad_blocked = any(page.get("status") == "held" for page in broad_pages)
    if not broad_complete and not broad_blocked and (global_success or chosen):
        for offset in range(0, 100000, 500):
            if len(chosen) >= target_new:
                break
            key = f"broad:{offset}"
            if key in state["pages"]:
                continue
            query = broad_discovery_query(limit=500, offset=offset)
            page = {"route": "broad-no-P136", "offset": offset, "query": query,
                    "started_at": now(), "status": "started", "count": 0}
            state["pages"][key] = page
            save(path, state)
            response = fetcher.get(QUERY_URL, source="wikidata",
                                   params={"query": query, "format": "json"},
                                   headers={"Accept": "application/sparql-results+json"})
            if response.status != "success":
                page.update(status="held", error=response.error, status_code=response.status_code,
                            completed_at=now())
                save(path, state)
                print(json.dumps({"discovery_page": key, "status": "held", "error": response.error}), flush=True)
                if response.status_code == 429 or "429" in str(response.error):
                    break
                # Stop this fallback frame after one transport failure; the
                # exact failed page is held and will never be retried.
                break
            try:
                bindings = json.loads(response.content)["results"]["bindings"]
                ids = [_qid(row["work"]["value"].rsplit("/", 1)[-1]) for row in bindings]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                page.update(status="held", error=f"invalid SPARQL response: {exc}", completed_at=now())
                save(path, state)
                continue
            fresh = [qid for qid in ids if qid not in existing_ids and qid not in chosen]
            chosen.update(fresh)
            page.update(status="success", count=len(ids), fresh_count=len(fresh),
                        raw_path=response.artifact.path, raw_sha256=response.artifact.sha256,
                        completed_at=now())
            state["ids"] = sorted(chosen, key=lambda q: int(q[1:]))
            save(path, state)
            print(json.dumps({"discovery_page": key, "returned": len(ids),
                              "new": len(fresh), "new_total": len(chosen)}), flush=True)
            if len(ids) < 500:
                broad_complete = True
                break
    state["completed_at"] = now() if len(chosen) >= target_new else None
    state["existing_ids_count"] = len(existing_ids)
    state["new_count"] = len(chosen)
    save(path, state)
    return state


def enrich(output: Path, *, selected_ids: list[str], fetcher: HTTPFetcher) -> dict:
    state_path = output / "entity-state.json"
    state = load(state_path) if state_path.exists() else {"attempted": [], "completed": [], "held": [], "errors": []}
    attempted, completed, held = set(state["attempted"]), set(state["completed"]), set(state["held"])
    shard_root = output / "record-shards"
    shard_root.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(shard_root.glob("*.jsonl")):
        records.extend(read_jsonl(path))
    completed.update(record.source_id for record in records)
    attempted.update(completed)
    pending = [qid for qid in selected_ids if qid not in attempted and qid not in held]
    adapter = WikidataAdapter(fetcher)
    for start in range(0, len(pending), 50):
        batch = pending[start:start + 50]
        attempted.update(batch)
        held.update(batch)
        state.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held))
        save(state_path, state)
        result = adapter.fetch_entities(batch, languages=LANGUAGES)
        successes = {record.source_id for record in result.records}
        if result.records:
            write_jsonl(result.records, shard_root / f"{batch[0]}.jsonl")
            records.extend(result.records)
        completed.update(successes)
        held.difference_update(successes)
        errors = list(result.errors)
        state["errors"].extend({"batch": batch, "error": error} for error in errors)
        for hold in result.metadata.get("scope_holds", []):
            state.setdefault("scope_holds", []).append(hold)
        state.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held), last_batch=batch, updated_at=now())
        save(state_path, state)
        print(json.dumps({"entity_batches": start // 50 + 1, "completed": len(completed),
                          "held": len(held), "errors": len(state["errors"])}), flush=True)
        if any("429" in str(error) for error in errors):
            break
    write_jsonl(records, output / "works-enriched.jsonl")
    report = {"version": "scale-acquisition-v2", "selected": len(selected_ids),
              "records": len(records), "completed": len(completed), "held": len(held),
              "errors": len(state["errors"]), "records_sha256": sha_file(output / "works-enriched.jsonl"),
              "completed_at": now(), "rights_scope": "structuredWikidataMetadataOnly"}
    save(output / "entity-manifest.json", report)
    return report


def materialize_records(output: Path, *, limit: int | None = None) -> dict:
    """Write an ordered, bounded JSONL view from completed immutable shards."""
    discovery = load(output / "discovery.json")
    by_qid = {}
    shard_root = output / "record-shards"
    for path in sorted(shard_root.glob("*.jsonl")):
        for record in read_jsonl(path):
            by_qid.setdefault(record.source_id, record)
    ordered = [by_qid[qid] for qid in discovery["ids"] if qid in by_qid]
    if limit is not None:
        ordered = ordered[:limit]
    path = output / "works-enriched.jsonl"
    write_jsonl(ordered, path)
    report = {"version": "scale-acquisition-v2-materialized", "records": len(ordered),
              "available_completed": len(by_qid), "selected_limit": limit,
              "records_sha256": sha_file(path), "completed_at": now()}
    save(output / "materialize-report.json", report)
    return report


def ingest_existing(*, output: Path, catalogue_path: Path, conditions: dict, chunk_size: int = 500) -> dict:
    """Project and append v2 records to the existing catalogue, isolating rows."""
    records = list(read_jsonl(output / "works-enriched.jsonl"))
    catalogue = ScaleCatalogue(catalogue_path)
    base_conditions, rights = catalogue._conditions()
    result = {"inserted": 0, "skipped_existing": 0, "failed": 0, "failures": [], "chunks": 0}
    with catalogue_writer_lock(catalogue_path):
        with sqlite3.connect(catalogue_path, timeout=120) as db:
            db.row_factory = sqlite3.Row
            for start in range(0, len(records), chunk_size):
                batch = records[start:start + chunk_size]
                db.execute("BEGIN IMMEDIATE")
                for record in batch:
                    qid = record.source_id
                    db.execute("SAVEPOINT v2_record")
                    try:
                        if db.execute("SELECT 1 FROM works WHERE qid=?", (qid,)).fetchone():
                            result["skipped_existing"] += 1
                            db.execute("RELEASE v2_record")
                            continue
                        normalized, row = catalogue._projection(record, base_conditions, rights)
                        row["provenance"]["source_conditions_path"] = str((output / "source-conditions-v2.json").resolve())
                        row["provenance"]["source_conditions_sha256"] = sha_file(output / "source-conditions-v2.json")
                        row["provenance"]["conditions_route"] = "fresh_v2_wikidata_cc0_receipt"
                        record_json = _json(normalized.model_dump(mode="json"))
                        record_sha = _sha(record_json.encode("utf-8"))
                        version_id = f"wd:{qid}:{record_sha}"
                        for artifact in row["provenance"]["artifacts"]:
                            bound = db.execute("SELECT sha256 FROM raw_artifacts WHERE path=?", (artifact["raw_path"],)).fetchone()
                            if bound and bound[0] != artifact["raw_sha256"]:
                                raise ValueError("raw snapshot path reused with different content")
                            db.execute("INSERT OR IGNORE INTO raw_artifacts VALUES(?,?)", (artifact["raw_path"], artifact["raw_sha256"]))
                        search = " ".join([row["title"], *row["aliases"], *[a["name"] or a["qid"] for a in row["authors"]]]).casefold()
                        db.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                                   (version_id, qid, record_sha, record_json, normalized.raw_path,
                                    normalized.raw_sha256, normalized.retrieved_at, sha_file(output / "source-conditions-v2.json")))
                        db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?)",
                                   (qid, version_id, row["title"], search, row["original_language"], row["year"],
                                    int(row["recommendable"]), int(rights["display"] == "allowed"), _json(row)))
                        db.executemany("INSERT INTO work_genres VALUES(?,?,?)",
                                       [(qid, genre["qid"], genre["label"]) for genre in row["genres"]])
                        result["inserted"] += 1
                        db.execute("RELEASE v2_record")
                    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                        db.execute("ROLLBACK TO v2_record")
                        db.execute("RELEASE v2_record")
                        result["failed"] += 1
                        if len(result["failures"]) < 100:
                            result["failures"].append({"qid": qid, "error": str(exc)})
                        db.execute("INSERT INTO ingestion_failures(qid,error,observed_at) VALUES(?,?,?)",
                                   (qid, f"scale-v2: {exc}", now()))
                db.commit()
                result["chunks"] += 1
                print(json.dumps({"ingest_chunks": result["chunks"], "inserted": result["inserted"],
                                  "failed": result["failed"]}), flush=True)
        with sqlite3.connect(catalogue_path) as db:
            result["final_works"] = db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]
            result["final_recommendable"] = db.execute("SELECT count(*) FROM works WHERE display_allowed=1 AND recommendable=1").fetchone()[0]
    result["conditions_sha256"] = sha_file(output / "source-conditions-v2.json")
    result["completed_at"] = now()
    save(output / "ingest-report.json", result)
    return result


def run(args: argparse.Namespace) -> dict:
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fetcher = HTTPFetcher(RawStore(output / "raw"),
                          user_agent="recommend-book/0.1 (https://github.com/ayutaz/recommend-book) novel catalogue v2",
                          min_interval=1.1, max_attempts=1)
    conditions = fresh_conditions(output, fetcher)
    if args.phase == "materialize":
        result = materialize_records(output, limit=args.limit_records)
        save(output / "manifest-materialize.json", result)
        return result
    if args.phase == "ingest":
        result = ingest_existing(output=output, catalogue_path=Path(args.catalogue).resolve(), conditions=conditions)
        save(output / "manifest-ingest.json", result)
        return result
    existing = load_existing_ids(baseline_selection=args.baseline_selection, catalogue=args.catalogue)
    discovery_state = discover(output, fetcher, existing_ids=existing, target_new=args.target_new,
                               start_offset=args.start_offset, max_offset=args.max_offset)
    if len(discovery_state["ids"]) < args.target_new:
        raise RuntimeError(f"discovery stopped at {len(discovery_state['ids'])}/{args.target_new}; held pages remain isolated")
    entity = enrich(output, selected_ids=discovery_state["ids"], fetcher=fetcher)
    if entity["records"] < args.min_records:
        raise RuntimeError(f"entity acquisition produced {entity['records']} records, below {args.min_records}")
    ingest = ingest_existing(output=output, catalogue_path=Path(args.catalogue).resolve(), conditions=conditions)
    final = {"version": "scale-30000-acquisition-v2", "baseline_ids": len(existing),
             "discovery": discovery_state["new_count"], "entity": entity, "ingest": ingest,
             "target_display_works": args.target_display_works, "target_met": ingest.get("final_works", 0) >= args.target_display_works,
             "conditions": conditions, "completed_at": now()}
    save(output / "manifest.json", final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/autonomous-next-v3/scale-30000-v1/acquisition"))
    parser.add_argument("--baseline-selection", type=Path, default=Path("data/autonomous-next-v3/scale-v1/acquisition/selection.json"))
    parser.add_argument("--catalogue", type=Path, default=Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite"))
    parser.add_argument("--target-new", type=int, default=22000)
    parser.add_argument("--min-records", type=int, default=20000)
    parser.add_argument("--target-display-works", type=int, default=30000)
    parser.add_argument("--start-offset", type=int, default=12000)
    parser.add_argument("--max-offset", type=int, default=100000)
    parser.add_argument("--phase", choices=["all", "materialize", "ingest"], default="all")
    parser.add_argument("--limit-records", type=int, default=None)
    args = parser.parse_args()
    if not 1000 <= args.target_new <= 100000 or args.start_offset < 0 or args.max_offset <= args.start_offset:
        parser.error("invalid collection bounds")
    print(json.dumps(run(args), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
