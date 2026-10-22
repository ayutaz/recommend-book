"""Incrementally extend the Wikidata novel catalogue for the v19 100k stage.

The v18 acquisition tree is an input snapshot and is never rewritten.  This
collector has its own discovery and entity checkpoints, carries the old IDs
and old held/failed IDs as an exclusion ledger, and writes completed entity
records to the catalogue in small transactions.  A failed HTTP request is
held permanently in this stage; resuming only continues with IDs that have
not been attempted.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Iterable

from recommend_book.ingestion.core import HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.wikidata import ENTITY_URL, QUERY_URL, LANGUAGE_CODES, WikidataAdapter
from recommend_book.models import SourceRecord
from recommend_book.scale_catalogue_v1 import ScaleCatalogue, _json, _sha


LANGUAGES = tuple(LANGUAGE_CODES.values())
NOVEL_FORMS = ("Q8261", "Q747381")
V2_ROOT = Path("data/autonomous-next-v3/scale-30000-v1/acquisition")
CATALOGUE = Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite")
BASELINE_WORKS = Path("data/autonomous-next-v3/scale-100000-v1/baseline-works-32130.jsonl")
BASELINE_WORKS_SHA256 = "aebcc125910a868227ea3d8156d764efc9e43209d3b3daaffbb4b9b149efa62b"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save(path: str | Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load(path: str | Path) -> object:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def qid(value: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", str(value)):
        raise ValueError(f"invalid Wikidata QID: {value}")
    return str(value)


def sha_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_declared_query(*, limit: int, offset: int, start_year: int | None = None,
                          end_year: int | None = None) -> str:
    """A disjoint continuation query, preserving work-only scope.

    Period routes make progress observable when the WDQS global ordering is
    exhausted.  P577 is only a partitioning predicate; it does not turn an
    edition or short story into a work.  The P629 and Q3331189 exclusions are
    kept from the v18 route.
    """
    if not 1 <= limit <= 500 or offset < 0:
        raise ValueError("invalid discovery page")
    period = ""
    if start_year is not None or end_year is not None:
        if start_year is None or end_year is None or end_year <= start_year:
            raise ValueError("invalid year period")
        period = (
            f" ?work wdt:P577 ?publicationDate."
            f" FILTER(?publicationDate >= \"{start_year}-01-01T00:00:00Z\"^^xsd:dateTime"
            f" && ?publicationDate < \"{end_year}-01-01T00:00:00Z\"^^xsd:dateTime)"
        )
    return (
        "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#> "
        "SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } "
        "{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } "
        f"{period} "
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY ?work LIMIT {limit} OFFSET {offset}"
    )


def _old_exclusions(*, catalogue: Path, v2_root: Path,
                    baseline_works: Path = BASELINE_WORKS) -> tuple[set[str], dict]:
    """Read all v18 IDs, including IDs that were held before ingest."""
    ids: set[str] = set()
    if baseline_works.exists():
        if sha_file(baseline_works) != BASELINE_WORKS_SHA256:
            raise ValueError("fixed v19 baseline works snapshot changed")
        for line in baseline_works.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(qid(json.loads(line)["qid"]))
    else:
        raise FileNotFoundError(baseline_works)
    with sqlite3.connect(f"file:{catalogue.resolve().as_posix()}?mode=ro", uri=True) as db:
        ids.update(qid(row[0]) for row in db.execute("SELECT DISTINCT qid FROM ingestion_failures") if row[0])
    discovery_path = v2_root / "discovery.json"
    if discovery_path.exists():
        discovery = load(discovery_path)
        ids.update(qid(v) for v in discovery.get("ids", []))
        ids.update(qid(v) for v in discovery.get("held_ids", []))
    entity_path = v2_root / "entity-state.json"
    if entity_path.exists():
        entity = load(entity_path)
        ids.update(qid(v) for v in entity.get("attempted", []))
        ids.update(qid(v) for v in entity.get("completed", []))
        ids.update(qid(v) for v in entity.get("held", []))
    detail = {
        "version": "scale-100000-v19-exclusion-ledger-v1",
        "catalogue": str(catalogue.resolve()),
        "v2_discovery": str(discovery_path.resolve()),
        "v2_entity_state": str(entity_path.resolve()),
        "baseline_works": str(baseline_works.resolve()),
        "baseline_works_sha256": BASELINE_WORKS_SHA256,
        "count": len(ids),
        "created_at": now(),
        "rule": "catalogue works, all v18 discovered/attempted/completed/held IDs and all recorded ingest failures",
    }
    return ids, detail


def _parse_bindings(content: bytes) -> list[str]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("SPARQL response has no results object")
    rows = payload["results"].get("bindings")
    if not isinstance(rows, list):
        raise ValueError("SPARQL response has no bindings list")
    return [qid(row["work"]["value"].rsplit("/", 1)[-1]) for row in rows]


def resume_candidates(selected: Iterable[str], *, attempted: set[str], held: set[str],
                      exclusions: set[str]) -> list[str]:
    """Return selected IDs persisted before an interrupted entity step."""
    return [value for value in selected
            if value not in attempted and value not in held and value not in exclusions]


def load_pending_records(path: Path) -> list[SourceRecord]:
    if not path.exists():
        return []
    return [record if isinstance(record, SourceRecord) else SourceRecord.model_validate(record)
            for record in read_jsonl(path)]


def discover_page(*, output: Path, fetcher: HTTPFetcher, state: dict,
                  key: str, query: str, exclusions: set[str]) -> tuple[list[str], bool]:
    """Fetch one never-before-requested page and persist it before returning."""
    pages = state.setdefault("pages", {})
    if key in pages:
        page = pages[key]
        if page.get("status") == "started":
            # An interrupted request has no durable response artifact.  Keep
            # it as a held page instead of guessing whether the server saw it
            # and sending the same query again on resume.
            page.update(status="held", error="interrupted_before_response", completed_at=now())
            save(output / "discovery.json", state)
            return [], False
        return [], page.get("status") == "success"
    page = {"key": key, "query": query, "status": "started", "started_at": now()}
    pages[key] = page
    save(output / "discovery.json", state)
    response = fetcher.get(QUERY_URL, source="wikidata",
                           params={"query": query, "format": "json"},
                           headers={"Accept": "application/sparql-results+json"})
    if response.status != "success":
        page.update(status="held", error=response.error, status_code=response.status_code,
                    attempts=response.attempts, completed_at=now(),
                    raw_path=response.artifact.path if response.artifact else None,
                    raw_sha256=response.artifact.sha256 if response.artifact else None)
        save(output / "discovery.json", state)
        print(json.dumps({"discovery_page": key, "status": "held", "error": response.error}), flush=True)
        return [], False
    try:
        ids = _parse_bindings(response.content)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        page.update(status="held", error=f"invalid SPARQL response: {exc}", completed_at=now(),
                    raw_path=response.artifact.path, raw_sha256=response.artifact.sha256)
        save(output / "discovery.json", state)
        return [], False
    chosen = set(state.get("ids", []))
    fresh = [value for value in ids if value not in exclusions and value not in chosen]
    chosen.update(fresh)
    page.update(status="success", count=len(ids), fresh_count=len(fresh),
                raw_path=response.artifact.path, raw_sha256=response.artifact.sha256,
                completed_at=now())
    state["ids"] = sorted(chosen, key=lambda value: int(value[1:]))
    save(output / "discovery.json", state)
    print(json.dumps({"discovery_page": key, "returned": len(ids),
                      "new": len(fresh), "new_total": len(chosen)}), flush=True)
    return fresh, True


@contextmanager
def catalogue_writer_lock(catalogue_path: Path, *, wait_seconds: float = 120.0,
                          poll_seconds: float = 1.0):
    """Acquire the shared writer lock without unlinking another owner's file."""
    lock_path = catalogue_path.parent.parent / "catalogue-writer.lock"
    payload = {"pid": os.getpid(), "started_at": now(),
               "catalogue": str(catalogue_path.resolve()), "owner": "scale-100000-metadata-v3"}
    deadline = time.monotonic() + wait_seconds
    stream = None
    while stream is None:
        try:
            stream = lock_path.open("x", encoding="utf-8", newline="\n")
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"catalogue writer lock wait expired; inspect owner: {lock_path}") from exc
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
    with stream:
        json.dump(payload, stream, ensure_ascii=False)
        stream.write("\n")
    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)


def _ingest_records(*, output: Path, catalogue_path: Path, records: list, conditions_path: Path) -> dict:
    if not records:
        return {"inserted": 0, "skipped_existing": 0, "failed": 0, "chunks": 0}
    catalogue = ScaleCatalogue(catalogue_path)
    base_conditions, rights = catalogue._conditions()
    result = {"inserted": 0, "skipped_existing": 0, "failed": 0, "failures": [], "chunks": 0}
    with catalogue_writer_lock(catalogue_path):
        with sqlite3.connect(catalogue_path, timeout=120) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            for record in records:
                db.execute("SAVEPOINT v3_record")
                try:
                    value = record.source_id
                    if db.execute("SELECT 1 FROM works WHERE qid=?", (value,)).fetchone():
                        result["skipped_existing"] += 1
                        db.execute("RELEASE v3_record")
                        continue
                    normalized, row = catalogue._projection(record, base_conditions, rights)
                    row["provenance"]["source_conditions_path"] = str(conditions_path.resolve())
                    row["provenance"]["source_conditions_sha256"] = sha_file(conditions_path)
                    row["provenance"]["conditions_route"] = "reused_v18_wikidata_cc0_receipt"
                    record_json = _json(normalized.model_dump(mode="json"))
                    record_sha = _sha(record_json.encode("utf-8"))
                    version_id = f"wd:{value}:{record_sha}"
                    for artifact in row["provenance"]["artifacts"]:
                        bound = db.execute("SELECT sha256 FROM raw_artifacts WHERE path=?", (artifact["raw_path"],)).fetchone()
                        if bound and bound[0] != artifact["raw_sha256"]:
                            raise ValueError("raw snapshot path reused with different content")
                        db.execute("INSERT OR IGNORE INTO raw_artifacts VALUES(?,?)", (artifact["raw_path"], artifact["raw_sha256"]))
                    search = " ".join([row["title"], *row["aliases"], *[a["name"] or a["qid"] for a in row["authors"]]]).casefold()
                    db.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                               (version_id, value, record_sha, record_json, normalized.raw_path,
                                normalized.raw_sha256, normalized.retrieved_at, sha_file(conditions_path)))
                    db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?)",
                               (value, version_id, row["title"], search, row["original_language"], row["year"],
                                int(row["recommendable"]), int(rights["display"] == "allowed"), _json(row)))
                    db.executemany("INSERT INTO work_genres VALUES(?,?,?)",
                                   [(value, genre["qid"], genre["label"]) for genre in row["genres"]])
                    result["inserted"] += 1
                    db.execute("RELEASE v3_record")
                except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                    db.execute("ROLLBACK TO v3_record")
                    db.execute("RELEASE v3_record")
                    result["failed"] += 1
                    if len(result["failures"]) < 100:
                        result["failures"].append({"qid": record.source_id, "error": str(exc)})
            db.commit()
            result["chunks"] = 1
    return result


def _reuse_conditions(output: Path, v2_root: Path) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    old = v2_root / "source-conditions-v2.json"
    if not old.exists():
        raise FileNotFoundError(old)
    receipt = load(old)
    receipt["version"] = "scale-source-conditions-v19-reused-v1"
    receipt["reused_from"] = str(old.resolve())
    path = output / "source-conditions-v19.json"
    save(path, receipt)
    return path


def _make_fetcher(output: Path, v2_root: Path) -> HTTPFetcher:
    fetcher = HTTPFetcher(RawStore(output / "raw"),
                          user_agent="recommend-book/0.1 novel catalogue v19",
                          min_interval=1.1, max_attempts=1)
    # Keep one gate for the old and new Wikidata workers.  Raw payloads remain
    # isolated under the new root; only host timing/cooldowns are shared.
    shared_gate = v2_root / "raw" / "http-rate-gate.sqlite3"
    shared_gate.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(shared_gate) as db:
        db.execute("CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, last_request REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS cooldowns (host TEXT PRIMARY KEY, not_before REAL NOT NULL)")
    fetcher.gate_path = shared_gate
    return fetcher


def _load_state(output: Path, *, exclusions: set[str], exclusion_meta: dict,
                target_new: int) -> dict:
    path = output / "discovery.json"
    if path.exists():
        state = load(path)
        if state.get("frame", {}).get("version") != "scale-discovery-v3":
            raise ValueError("v3 discovery frame changed")
        expected = _sha(json.dumps(sorted(exclusions), separators=(",", ":")).encode())
        if state.get("frame", {}).get("exclude_sha256") != expected:
            raise ValueError("v3 exclusion ledger changed")
        return state
    state = {
        "frame": {"version": "scale-discovery-v3", "page_size": 500,
                   "target_new": target_new, "routes": ["broad-continuation", "year-period"],
                   "old_discovery_frame": "data/autonomous-next-v3/scale-30000-v1/acquisition/discovery.json",
                   "exclude_sha256": _sha(json.dumps(sorted(exclusions), separators=(",", ":")).encode()),
                   "scope": "P7937/P31 novel forms, P629 editions and Q3331189 short stories excluded"},
        "pages": {}, "ids": [], "held_ids": [], "exclusion": exclusion_meta,
    }
    save(path, state)
    save(output / "exclusion-ledger.json", {**exclusion_meta, "ids_sha256": _sha("\n".join(sorted(exclusions)).encode())})
    return state


def acquire(*, output: Path, catalogue: Path, v2_root: Path, target_new: int,
            target_display: int, max_broad_offset: int = 100000) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    exclusions, exclusion_meta = _old_exclusions(catalogue=catalogue, v2_root=v2_root)
    with sqlite3.connect(f"file:{catalogue.resolve().as_posix()}?mode=ro", uri=True) as db:
        runtime_exclusions = set(exclusions)
        runtime_exclusions.update(qid(row[0]) for row in db.execute("SELECT qid FROM works"))
    conditions = _reuse_conditions(output, v2_root)
    fetcher = _make_fetcher(output, v2_root)
    state = _load_state(output, exclusions=exclusions, exclusion_meta=exclusion_meta,
                        target_new=target_new)
    entity_path = output / "entity-state.json"
    entity = load(entity_path) if entity_path.exists() else {"attempted": [], "completed": [], "held": [], "errors": []}
    attempted, completed, held = set(entity.get("attempted", [])), set(entity.get("completed", [])), set(entity.get("held", []))
    pending_path = output / "pending-records.jsonl"
    pending = load_pending_records(pending_path)
    adapter = WikidataAdapter(fetcher)

    def flush_pending(force: bool = False):
        nonlocal pending
        if not pending or (not force and len(pending) < 1000):
            return
        batch = pending[:1000]
        report = _ingest_records(output=output, catalogue_path=catalogue, records=batch, conditions_path=conditions)
        ingested = output / "ingest-ledger.jsonl"
        with ingested.open("a", encoding="utf-8", newline="\n") as stream:
            for record in batch:
                stream.write(json.dumps({"qid": record.source_id, "at": now(), "result": report}, ensure_ascii=False) + "\n")
        pending = pending[len(batch):]
        write_jsonl(pending, pending_path)
        state.setdefault("ingest", []).append({"at": now(), "records": len(batch), **report})
        save(output / "discovery.json", state)
        print(json.dumps({"ingested_batch": len(batch), "inserted": report["inserted"], "failed": report["failed"]}), flush=True)

    def searchable_count() -> int:
        with sqlite3.connect(catalogue) as db:
            return db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]

    def process(ids: Iterable[str]):
        nonlocal pending, attempted, completed, held, entity
        candidates = [value for value in ids if value not in attempted and value not in held and value not in runtime_exclusions]
        for start in range(0, len(candidates), 50):
            batch = candidates[start:start + 50]
            attempted.update(batch)
            held.update(batch)
            entity.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held), last_batch=batch, updated_at=now())
            save(entity_path, entity)
            result = adapter.fetch_entities(batch, languages=tuple(LANGUAGES))
            successes = {record.source_id for record in result.records}
            if result.records:
                shard = output / "record-shards" / f"{batch[0]}.jsonl"
                write_jsonl(result.records, shard)
                pending.extend(result.records)
            completed.update(successes)
            held.difference_update(successes)
            entity["errors"].extend({"batch": batch, "error": error} for error in result.errors)
            entity.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held), last_batch=batch, updated_at=now())
            save(entity_path, entity)
            write_jsonl(pending, pending_path)
            print(json.dumps({"entity_batch": len(attempted) // 50, "completed": len(completed), "held": len(held), "pending": len(pending)}), flush=True)
            flush_pending()
            if result.status == "failed" and any("429" in str(error) or "HTTP 429" in str(error) for error in result.errors):
                return False
        return True

    # Resume records and selected IDs saved immediately before an interrupted
    # process.  Discovery pages are immutable; skipping this step would lose
    # a page whose entity work was interrupted after its checkpoint write.
    flush_pending(force=True)
    resume_ids = resume_candidates(state.get("ids", []), attempted=attempted,
                                   held=held, exclusions=runtime_exclusions)
    if resume_ids:
        process(resume_ids)
    chosen = set(state.get("ids", []))
    # Continue the v18 broad route only after its last successful page.  The
    # old page keys are copied into the new frame as inherited receipts and
    # are never requested again.
    last_broad = 11500
    old_discovery = v2_root / "discovery.json"
    if old_discovery.exists():
        old_pages = load(old_discovery).get("pages", {})
        offsets = [int(k.split(":", 1)[-1]) for k, v in old_pages.items()
                   if v.get("route") == "broad-no-P136" and k.startswith("broad:") and v.get("status") == "success"]
        if offsets:
            last_broad = max(offsets)
    route_blocked = False
    for offset in range(last_broad + 500, max_broad_offset, 500):
        if len(chosen) >= target_new or searchable_count() >= target_display:
            break
        key = f"broad-continuation:{offset}"
        prior = state["pages"].get(key)
        if prior is not None:
            if prior.get("status") == "success" and prior.get("count", 0) < 500:
                break
            # A historical held/started page is not a new outage response.
            # Its IDs are never retried; resume advances to an unattempted page.
            continue
        fresh, ok = discover_page(output=output, fetcher=fetcher, state=state,
                                  key=key,
                                  query=source_declared_query(limit=500, offset=offset),
                                  exclusions=runtime_exclusions)
        chosen.update(fresh)
        if not process(fresh):
            break
        if searchable_count() >= target_display:
            break
        if not ok:
            route_blocked = True
            break
        if len(fresh) == 0 and state["pages"][f"broad-continuation:{offset}"].get("count", 0) < 500:
            break
    # Partitioned year routes are new query frames and therefore still useful
    # after the global result has ended.  Stop only on a held page; no held
    # request is retried on resume.
    periods = [(1800, 1850), (1850, 1900), (1900, 1925), (1925, 1950),
               (1950, 1975), (1975, 2000), (2000, 2010), (2010, 2020), (2020, 2035)]
    for start_year, end_year in periods:
        if route_blocked or len(chosen) >= target_new or searchable_count() >= target_display:
            break
        for offset in range(0, 30000, 500):
            if len(chosen) >= target_new or searchable_count() >= target_display:
                break
            key = f"period:{start_year}-{end_year}:{offset}"
            prior = state["pages"].get(key)
            if prior is not None:
                if prior.get("status") == "success" and prior.get("count", 0) < 500:
                    break
                continue
            fresh, ok = discover_page(output=output, fetcher=fetcher, state=state, key=key,
                                      query=source_declared_query(limit=500, offset=offset,
                                                                   start_year=start_year, end_year=end_year),
                                      exclusions=runtime_exclusions)
            chosen.update(fresh)
            if not process(fresh):
                break
            if searchable_count() >= target_display:
                break
            page = state["pages"][key]
            if not ok or page.get("count", 0) < 500:
                if not ok:
                    route_blocked = True
                break
    flush_pending(force=True)
    state["ids"] = sorted(chosen, key=lambda value: int(value[1:]))
    state["new_count"] = len(chosen)
    state["completed_at"] = now() if len(chosen) >= target_new else None
    save(output / "discovery.json", state)
    with sqlite3.connect(catalogue) as db:
        searchable = db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]
        recommendable = db.execute("SELECT count(*) FROM works WHERE display_allowed=1 AND recommendable=1").fetchone()[0]
    final = {"version": "scale-100000-acquisition-v3", "discovery": len(chosen),
             "entity_completed": len(completed), "entity_held": len(held),
             "searchable": searchable, "recommendable": recommendable,
             "target_display": target_display, "target_met": searchable >= target_display,
             "conditions_sha256": sha_file(conditions), "completed_at": now()}
    save(output / "manifest.json", final)
    return final


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/autonomous-next-v3/scale-100000-v1/acquisition"))
    parser.add_argument("--v2-root", type=Path, default=V2_ROOT)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--target-new", type=int, default=80000)
    parser.add_argument("--target-display", type=int, default=100000)
    parser.add_argument("--max-broad-offset", type=int, default=100000)
    args = parser.parse_args()
    if not 1000 <= args.target_new <= 150000 or args.target_display < 32126:
        parser.error("invalid v19 collection bounds")
    result = acquire(output=args.output.resolve(), catalogue=args.catalogue.resolve(),
                     v2_root=args.v2_root.resolve(), target_new=args.target_new,
                     target_display=args.target_display, max_broad_offset=args.max_broad_offset)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
