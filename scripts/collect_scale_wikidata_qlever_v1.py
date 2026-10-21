"""Discover Wikidata novel candidates through the public QLever index.

QLever is used only as a read-only candidate index.  Every candidate is then
fetched from the Wikidata Action API and passed through the existing
``WikidataAdapter`` and catalogue projection.  Consequently a QLever result
never becomes a novel assertion, evidence text, or model input by itself.

This is a separate v19 frame.  The WDQS frame and its held pages are never
rewritten, and a failed page or entity batch is held rather than retried.
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
import sqlite3
import time
from typing import Iterable

from recommend_book.ingestion.core import HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.wikidata import ENTITY_URL, LANGUAGE_CODES, WikidataAdapter
from recommend_book.models import SourceRecord
from recommend_book.scale_catalogue_v1 import ScaleCatalogue


QLEVER_URL = "https://qlever.dev/api/wikidata"
WIKIDATA_SOURCE = "wikidata"
V2_ROOT = Path("data/autonomous-next-v3/scale-30000-v1/acquisition")
CATALOGUE = Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite")
BASELINE_WORKS = Path("data/autonomous-next-v3/scale-100000-v1/baseline-works-32130.jsonl")
BASELINE_WORKS_SHA256 = "aebcc125910a868227ea3d8156d764efc9e43209d3b3daaffbb4b9b149efa62b"
LANGUAGES = tuple(LANGUAGE_CODES.values())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qid(value: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", str(value)):
        raise ValueError(f"invalid Wikidata QID: {value}")
    return str(value)


def source_declared_query(*, limit: int, offset: int, descending: bool = False) -> str:
    """Return a QLever-compatible, work-only candidate query.

    QLever's public Wikidata endpoint documents a default maximum of 100 rows,
    so the collector deliberately keeps pages at or below that limit.
    """
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("QLever pages require limit 1..100 and nonnegative offset")
    return (
        "PREFIX wd: <http://www.wikidata.org/entity/> "
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
        "SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } "
        "{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } "
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY {'DESC(?work)' if descending else '?work'} LIMIT {limit} OFFSET {offset}"
    )


def parse_bindings(content: bytes) -> list[str]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), dict):
        raise ValueError("QLever response has no results object")
    rows = payload["results"].get("bindings")
    if not isinstance(rows, list):
        raise ValueError("QLever response has no bindings list")
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        value = row.get("work", {}).get("value") if isinstance(row, dict) else None
        candidate = qid(str(value).rsplit("/", 1)[-1])
        if candidate not in seen:
            seen.add(candidate)
            result.append(candidate)
    return result


def old_exclusions(*, catalogue: Path, v2_root: Path) -> tuple[set[str], dict]:
    """Reconstruct the fixed v18 exclusion boundary without cloning the DB."""
    if not BASELINE_WORKS.exists() or sha_file(BASELINE_WORKS) != BASELINE_WORKS_SHA256:
        raise ValueError("fixed v19 baseline works snapshot changed or is missing")
    ids = {qid(json.loads(line)["qid"]) for line in BASELINE_WORKS.read_text(encoding="utf-8").splitlines() if line.strip()}
    with sqlite3.connect(f"file:{catalogue.resolve().as_posix()}?mode=ro", uri=True) as db:
        ids.update(qid(row[0]) for row in db.execute("SELECT DISTINCT qid FROM ingestion_failures") if row[0])
    discovery = v2_root / "discovery.json"
    if discovery.exists():
        frame = load(discovery)
        ids.update(qid(value) for value in frame.get("ids", []))
        ids.update(qid(value) for value in frame.get("held_ids", []))
    entity = v2_root / "entity-state.json"
    if entity.exists():
        frame = load(entity)
        for key in ("attempted", "completed", "held"):
            ids.update(qid(value) for value in frame.get(key, []))
    detail = {"version": "scale-100000-qlever-exclusion-v1", "count": len(ids),
              "baseline_works": str(BASELINE_WORKS.resolve()),
              "baseline_works_sha256": BASELINE_WORKS_SHA256,
              "v2_discovery": str(discovery.resolve()), "v2_entity_state": str(entity.resolve()),
              "created_at": now(),
              "rule": "fixed v19 baseline, v18 discovered/attempted/completed/held IDs and recorded ingest failures"}
    return ids, detail


@contextmanager
def catalogue_writer_lock(catalogue: Path, *, wait_seconds: float = 120.0,
                          poll_seconds: float = 1.0):
    lock = catalogue.parent.parent / "catalogue-writer.lock"
    deadline = time.monotonic() + wait_seconds
    stream = None
    while stream is None:
        try:
            stream = lock.open("x", encoding="utf-8")
        except (FileExistsError, PermissionError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"catalogue writer lock wait expired: {lock}") from exc
            time.sleep(min(poll_seconds, deadline - time.monotonic()))
    with stream:
        json.dump({"pid": os.getpid(), "started_at": now(), "owner": "scale-100000-qlever-v1",
                   "catalogue": str(catalogue.resolve())}, stream)
        stream.write("\n")
    try:
        yield lock
    finally:
        lock.unlink(missing_ok=True)


def reuse_conditions(output: Path, v2_root: Path) -> Path:
    source = v2_root / "source-conditions-v2.json"
    if not source.exists():
        raise FileNotFoundError(source)
    receipt = dict(load(source))
    receipt.update(version="scale-source-conditions-v19-qlever-v1", reused_from=str(source.resolve()))
    path = output / "source-conditions-v19-qlever.json"
    if path.exists():
        existing = load(path)
        stable_keys = ("source", "policy_id", "policy_snapshot", "usages",
                       "metadataonly_scope", "scope", "request")
        if any(existing.get(key) != receipt.get(key) for key in stable_keys):
            raise ValueError("existing QLever conditions receipt differs from current Wikidata snapshot")
        return path
    save(path, receipt)
    return path


def make_fetcher(output: Path, v2_root: Path) -> HTTPFetcher:
    fetcher = HTTPFetcher(
        RawStore(output / "raw"),
        user_agent="recommend-book/0.1 (https://github.com/ayutaz/recommend-book) qlever discovery v19",
        min_interval=1.1,
        max_attempts=1,
        host_intervals={"qlever.dev": 10.0},
    )
    gate = v2_root / "raw" / "http-rate-gate.sqlite3"
    gate.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(gate) as db:
        db.execute("CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, last_request REAL NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS cooldowns (host TEXT PRIMARY KEY, not_before REAL NOT NULL)")
    fetcher.gate_path = gate
    return fetcher


class AllLanguagesEntityFetcher:
    """Remove the language filter for new entity batches only.

    Wikibase's ``languages=*`` is not relied on: the request is sent without
    the parameter, while the adapter still prefers the established language
    order when selecting a title.  QLever discovery and all other requests
    continue through the wrapped fetcher unchanged.
    """

    def __init__(self, inner: HTTPFetcher):
        self.inner = inner

    def get(self, url: str, *, source: str, params: dict | None = None,
            headers: dict | None = None):
        if url == ENTITY_URL and params and "languages" in params:
            params = dict(params)
            params.pop("languages", None)
        return self.inner.get(url, source=source, params=params, headers=headers)


def ingest_records(*, catalogue_path: Path, records: list[SourceRecord], conditions: Path) -> dict:
    if not records:
        return {"inserted": 0, "skipped_existing": 0, "failed": 0}
    result = {"inserted": 0, "skipped_existing": 0, "failed": 0, "failures": []}
    with catalogue_writer_lock(catalogue_path):
        # ScaleCatalogue may create/upgrade WAL metadata; it must be opened
        # only after the shared lock is held so native import cannot overlap.
        catalogue = ScaleCatalogue(catalogue_path)
        base_conditions, rights = catalogue._conditions()
        with sqlite3.connect(catalogue_path, timeout=120) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            for record in records:
                db.execute("SAVEPOINT qlever_record")
                try:
                    if db.execute("SELECT 1 FROM works WHERE qid=?", (record.source_id,)).fetchone():
                        result["skipped_existing"] += 1
                        db.execute("RELEASE qlever_record")
                        continue
                    normalized, row = catalogue._projection(record, base_conditions, rights)
                    row["provenance"]["source_conditions_path"] = str(conditions.resolve())
                    row["provenance"]["source_conditions_sha256"] = sha_file(conditions)
                    record_json = normalized.model_dump_json()
                    version_id = f"wd:{record.source_id}:{hashlib.sha256(record_json.encode()).hexdigest()}"
                    for artifact in row["provenance"]["artifacts"]:
                        bound = db.execute("SELECT sha256 FROM raw_artifacts WHERE path=?", (artifact["raw_path"],)).fetchone()
                        if bound and bound[0] != artifact["raw_sha256"]:
                            raise ValueError("raw snapshot path reused with different content")
                        db.execute("INSERT OR IGNORE INTO raw_artifacts VALUES (?,?)", (artifact["raw_path"], artifact["raw_sha256"]))
                    search = " ".join([row["title"], *row["aliases"], *[a["name"] or a["qid"] for a in row["authors"]]]).casefold()
                    db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", (version_id, record.source_id, hashlib.sha256(record_json.encode()).hexdigest(), record_json, normalized.raw_path, normalized.raw_sha256, normalized.retrieved_at, sha_file(conditions)))
                    db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)", (record.source_id, version_id, row["title"], search, row["original_language"], row["year"], int(row["recommendable"]), int(rights["display"] == "allowed"), json.dumps(row, ensure_ascii=False)))
                    db.executemany("INSERT INTO work_genres VALUES (?,?,?)", [(record.source_id, genre["qid"], genre["label"]) for genre in row["genres"]])
                    result["inserted"] += 1
                    db.execute("RELEASE qlever_record")
                except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
                    db.execute("ROLLBACK TO qlever_record")
                    db.execute("RELEASE qlever_record")
                    result["failed"] += 1
                    result["failures"].append({"qid": record.source_id, "error": str(exc)})
            db.commit()
    return result


def acquire(*, output: Path, catalogue: Path, v2_root: Path, target_display: int = 100000,
            max_offset: int = 100000, page_size: int = 100, descending: bool = False,
            all_languages: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    exclusions, exclusion_meta = old_exclusions(catalogue=catalogue, v2_root=v2_root)
    with sqlite3.connect(f"file:{catalogue.resolve().as_posix()}?mode=ro", uri=True) as db:
        runtime_exclusions = set(exclusions)
        runtime_exclusions.update(qid(row[0]) for row in db.execute("SELECT qid FROM works"))
    conditions = reuse_conditions(output, v2_root)
    fetcher = make_fetcher(output, v2_root)
    state_path = output / "discovery.json"
    expected_exclusions = hashlib.sha256(json.dumps(sorted(exclusions), separators=(",", ":")).encode()).hexdigest()
    state = load(state_path) if state_path.exists() else {
        "frame": {"version": "scale-discovery-qlever-v1", "page_size": page_size,
                   "exclude_sha256": expected_exclusions, "endpoint": QLEVER_URL,
                   "order": "desc" if descending else "asc",
                   "scope": "QLever candidate discovery only; Wikidata API owns scope and rights"},
        "pages": {}, "ids": [], "exclusion": exclusion_meta,
    }
    if state.get("frame", {}).get("exclude_sha256") != expected_exclusions:
        raise ValueError("QLever exclusion ledger changed")
    if state.get("frame", {}).get("order", "asc") != ("desc" if descending else "asc"):
        raise ValueError("QLever discovery order changed; use a new output frame")
    historical_pages = set(state.get("pages", {}))
    entity_path = output / "entity-state.json"
    entity = load(entity_path) if entity_path.exists() else {"attempted": [], "completed": [], "held": [], "errors": []}
    if entity.get("in_flight_batch"):
        entity.setdefault("interrupted_batches", []).append({
            "batch": entity["in_flight_batch"].get("ids", []),
            "started_at": entity["in_flight_batch"].get("started_at"),
            "reason": "interrupted_before_entity_response; IDs remain held and are never resent",
            "observed_at": now(),
        })
        entity["in_flight_batch"] = None
        save(entity_path, entity)
    attempted, completed, held = set(entity.get("attempted", [])), set(entity.get("completed", [])), set(entity.get("held", []))
    pending_path = output / "pending-records.jsonl"
    pending = list(read_jsonl(pending_path)) if pending_path.exists() else []
    adapter = WikidataAdapter(AllLanguagesEntityFetcher(fetcher) if all_languages else fetcher)
    entity_languages = LANGUAGES
    discovery_blocked = False
    entity_blocked = False

    def searchable() -> int:
        with sqlite3.connect(catalogue) as db:
            return db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]

    def flush(force: bool = False):
        nonlocal pending
        if not pending or (not force and len(pending) < 1000):
            return
        batch, pending = pending[:1000], pending[1000:]
        report = ingest_records(catalogue_path=catalogue, records=batch, conditions=conditions)
        write_jsonl(pending, pending_path)
        state.setdefault("ingest", []).append({"at": now(), "records": len(batch), **report})
        save(state_path, state)
        print(json.dumps({"ingested_batch": len(batch), **report}), flush=True)

    def process(ids: Iterable[str]) -> bool:
        nonlocal pending, entity_blocked
        candidates = [v for v in ids if v not in attempted and v not in held and v not in runtime_exclusions]
        for start in range(0, len(candidates), 50):
            batch = candidates[start:start + 50]
            attempted.update(batch)
            held.update(batch)
            entity.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held), last_batch=batch, updated_at=now())
            entity["in_flight_batch"] = {"ids": batch, "started_at": now()}
            save(entity_path, entity)
            result = adapter.fetch_entities(batch, languages=entity_languages)
            successes = {r.source_id for r in result.records}
            pending.extend(result.records)
            # Durable pending records must precede completion/in-flight
            # bookkeeping.  If the process dies after this atomic write,
            # restart can ingest the records without issuing another request.
            write_jsonl(pending, pending_path)
            completed.update(successes)
            held.difference_update(successes)
            entity["errors"].extend({"batch": batch, "error": error} for error in result.errors)
            entity.update(attempted=sorted(attempted), completed=sorted(completed), held=sorted(held), updated_at=now())
            entity["in_flight_batch"] = None
            save(entity_path, entity)
            print(json.dumps({"entity_completed": len(completed), "entity_held": len(held), "pending": len(pending)}), flush=True)
            flush()
            if result.status == "failed":
                entity_blocked = True
                return False
        return True

    # Resume only durable candidate IDs that were not attempted/held.
    if pending:
        flush(force=True)
    resume = [v for v in state.get("ids", []) if v not in attempted and v not in held and v not in runtime_exclusions]
    if resume and not process(resume):
        entity_blocked = True
    chosen = set(state.get("ids", []))
    for offset in range(0, max_offset, page_size):
        if discovery_blocked or entity_blocked or searchable() >= target_display:
            break
        key = f"qlever-{'desc' if descending else 'asc'}:{offset}"
        if key in state["pages"]:
            page = state["pages"][key]
            if page.get("status") == "success":
                continue
            # A held/started page from an earlier invocation is immutable:
            # never resend it, but do continue to later never-requested pages.
            # Only a failure created during this invocation blocks subsequent
            # pages, which avoids turning an old outage into a permanent stop.
            if key in historical_pages:
                if page.get("status") == "started":
                    page.update(status="held", error="interrupted_before_response", completed_at=now())
                    save(state_path, state)
                continue
            discovery_blocked = True
            break
        query = source_declared_query(limit=page_size, offset=offset, descending=descending)
        state["pages"][key] = {"status": "started", "query": query, "started_at": now()}
        save(state_path, state)
        response = fetcher.get(QLEVER_URL, source=WIKIDATA_SOURCE, params={"query": query, "format": "json"}, headers={"Accept": "application/sparql-results+json"})
        page = state["pages"][key]
        if response.status != "success":
            page.update(status="held", error=response.error, status_code=response.status_code, raw_path=response.artifact.path if response.artifact else None, raw_sha256=response.artifact.sha256 if response.artifact else None, completed_at=now())
            save(state_path, state)
            discovery_blocked = True
            break
        try:
            ids = parse_bindings(response.content)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            page.update(status="held", error=f"invalid QLever response: {exc}", raw_path=response.artifact.path, raw_sha256=response.artifact.sha256, completed_at=now())
            save(state_path, state)
            discovery_blocked = True
            break
        fresh = [v for v in ids if v not in runtime_exclusions and v not in chosen]
        chosen.update(fresh)
        state["ids"] = sorted(chosen, key=lambda v: int(v[1:]))
        page.update(status="success", returned=len(ids), fresh_count=len(fresh), raw_path=response.artifact.path, raw_sha256=response.artifact.sha256, completed_at=now())
        save(state_path, state)
        if not process(fresh):
            break
        if len(ids) < page_size:
            break
    flush(force=True)
    state["ids"] = sorted(chosen, key=lambda v: int(v[1:]))
    save(state_path, state)
    result = {"version": "scale-100000-qlever-acquisition-v1", "discovery": len(chosen),
              "entity_completed": len(completed), "entity_held": len(held),
              "searchable": searchable(), "target_display": target_display,
              "target_met": searchable() >= target_display, "endpoint": QLEVER_URL,
              "completed_at": now()}
    save(output / "manifest.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/autonomous-next-v3/scale-100000-v1/acquisition-qlever-v1"))
    parser.add_argument("--v2-root", type=Path, default=V2_ROOT)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--target-display", type=int, default=100000)
    parser.add_argument("--max-offset", type=int, default=100000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--order", choices=("asc", "desc"), default="asc")
    parser.add_argument("--all-languages", action="store_true",
                        help="request all Wikidata labels for never-attempted IDs; held IDs are never retried")
    args = parser.parse_args()
    if not 1 <= args.page_size <= 100 or args.target_display < 32126:
        parser.error("invalid QLever collection bounds")
    print(json.dumps(acquire(output=args.output.resolve(), catalogue=args.catalogue.resolve(), v2_root=args.v2_root.resolve(), target_display=args.target_display, max_offset=args.max_offset, page_size=args.page_size, descending=args.order == "desc", all_languages=args.all_languages), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
