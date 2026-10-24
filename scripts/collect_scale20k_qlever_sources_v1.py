"""Collect a bounded, genre-and-Wikipedia-sitelink QLever supplement.

QLever remains a candidate index only.  The existing v19 collector performs
the Wikidata entity fetch, source projection, rights checks, writer locking,
checkpointing, and resume handling; this wrapper supplies only a new query and
an isolated discovery frame.  The old 100k frame is never opened for writing.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

try:
    import collect_scale_wikidata_qlever_v1 as _BASE_MODULE
    qid = _BASE_MODULE.qid
except ModuleNotFoundError:
    _BASE = Path(__file__).with_name("collect_scale_wikidata_qlever_v1.py")
    if not _BASE.exists():
        _BASE = Path(__file__).parents[3] / "scripts" / "collect_scale_wikidata_qlever_v1.py"
    _SPEC = importlib.util.spec_from_file_location("collect_scale_wikidata_qlever_v1", _BASE)
    if not _SPEC or not _SPEC.loader:
        raise
    _MODULE = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = _MODULE
    _SPEC.loader.exec_module(_MODULE)
    _BASE_MODULE = _MODULE
    qid = _MODULE.qid


CATALOGUE = Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite")
V2_ROOT = Path("data/autonomous-next-v3/scale-30000-v1/acquisition")
OUTPUT = Path(
    "data/autonomous-next-v3/scale-20000-classifications-v1/"
    "acquisition-qlever-genre-sitelink-v1"
)
OLD_ENTITY_STATE = Path(
    "data/autonomous-next-v3/scale-100000-v1/acquisition-qlever-desc-v1/entity-state.json"
)
NATIVE_EXCLUSIONS = Path(
    "data/autonomous-next-v3/scale-20000-classifications-v1/native-exclusions-v3.json"
)
OLD_FINAL_RAW_SHA256 = "7ac68d3ac918e9f15a582901001803ee29ae3ce6f51abd35d1fd84a3db7d5aa4"
OLD_FINAL_BOUNDARY_QID = "Q134459058"
OLD_FINAL_BOUNDARY_URI = f"http://www.wikidata.org/entity/{OLD_FINAL_BOUNDARY_QID}"
ROUTE = "qlever-genre-sitelink-desc"
FRAME_VERSION = "scale-discovery-qlever-genre-sitelink-v1"
FRAME_SCOPE = (
    "QLever candidate discovery only; explicit P136 and Wikipedia sitelink "
    "are candidate filters; Wikidata API owns scope and rights"
)
WIKIPEDIA_LANGUAGES = (
    "en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn"
)
WIKIPEDIA_HOST_VALUES = " ".join(
    f"<https://{language}.wikipedia.org/>" for language in WIKIPEDIA_LANGUAGES
)


def genre_sitelink_query(*, limit: int, offset: int, descending: bool = False) -> str:
    """Return the bounded supplement query without asserting genre truth.

    The four fixed work patterns and edition/collection exclusions are kept
    identical to the v19 route.  P136 and a Wikipedia sitelink only narrow the
    candidate index; entity projection still validates the record.
    """
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("QLever pages require limit 1..100 and nonnegative offset")
    order = "DESC(?work)" if descending else "?work"
    return (
        "PREFIX wd: <http://www.wikidata.org/entity/> "
        "PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
        "PREFIX schema: <http://schema.org/> "
        "SELECT DISTINCT ?work WHERE { "
        "{ ?work wdt:P7937 wd:Q8261. } "
        "UNION { ?work wdt:P7937 wd:Q747381. } "
        "UNION { ?work wdt:P31 wd:Q8261. } "
        "UNION { ?work wdt:P31 wd:Q747381. } "
        "?work wdt:P136 ?genre . "
        "?article schema:about ?work ; schema:isPartOf ?wiki . "
        f"VALUES ?wiki {{ {WIKIPEDIA_HOST_VALUES} }} "
        f'FILTER(STR(?work) < "{OLD_FINAL_BOUNDARY_URI}") '
        "FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } "
        "FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } "
        f"}} ORDER BY {order} LIMIT {limit} OFFSET {offset}"
    )


def filter_unrecorded_ids(ids: list[str], excluded: set[str], chosen: set[str]) -> list[str]:
    """Keep QLever IDs once, without requesting old or already chosen works."""
    fresh: list[str] = []
    for value in ids:
        value = qid(value)
        if value in excluded or value in chosen:
            continue
        chosen.add(value)
        fresh.append(value)
    return fresh


def _read_json_object(path: Path) -> dict:
    """Read a pinned object, tolerating historical trailing JSON data."""
    value, _ = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def prior_attempt_ids(path: Path = OLD_ENTITY_STATE,
                      native_exclusions: Path = NATIVE_EXCLUSIONS) -> set[str]:
    """Read old entity/native ledgers so no prior QID is fetched again."""
    result: set[str] = set()
    if path.exists():
        state = _read_json_object(path)
        for key in ("attempted", "completed", "held"):
            result.update(qid(value) for value in state.get(key, []))
    if native_exclusions.exists():
        state = _read_json_object(native_exclusions)
        result.update(qid(value) for value in state.get("qids", []))
    return result


def acquire(*, output: Path = OUTPUT, catalogue: Path = CATALOGUE,
            v2_root: Path = V2_ROOT, max_offset: int = 1000,
            page_size: int = 100, target_display: int | None = None,
            all_languages: bool = True, old_entity_state: Path = OLD_ENTITY_STATE,
            native_exclusions: Path = NATIVE_EXCLUSIONS) -> dict:
    """Run at most ``max_offset`` candidate rows in a new checkpoint frame.

    This is deliberately route-local.  It imports the v19 fetcher, adapter,
    raw store, projection, rights, and ingest helpers without modifying the
    frozen collector module or its checkpoints.
    """
    if max_offset < page_size or max_offset > 1000:
        raise ValueError("supplement max_offset must be between one page and 1000")
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    if target_display is None:
        with sqlite3.connect(catalogue) as db:
            current_display = db.execute(
                "SELECT count(*) FROM works WHERE display_allowed=1"
            ).fetchone()[0]
        target_display = current_display + max_offset
    if target_display < 1:
        raise ValueError("target_display must be positive")
    base = _BASE_MODULE
    output.mkdir(parents=True, exist_ok=True)
    exclusions, exclusion_meta = base.old_exclusions(catalogue=catalogue, v2_root=v2_root)
    exclusions.update(prior_attempt_ids(old_entity_state, native_exclusions))
    with sqlite3.connect(f"file:{catalogue.resolve().as_posix()}?mode=ro", uri=True) as db:
        runtime_exclusions = set(exclusions)
        runtime_exclusions.update(qid(row[0]) for row in db.execute("SELECT qid FROM works"))
    conditions = base.reuse_conditions(output, v2_root)
    fetcher = base.make_fetcher(output, v2_root)
    state_path = output / "discovery.json"
    expected_exclusions = base.hashlib.sha256(
        json.dumps(sorted(exclusions), separators=(",", ":")).encode()
    ).hexdigest()
    frame_scope = (
        f"{FRAME_SCOPE}; old v19 final boundary {OLD_FINAL_BOUNDARY_QID}; "
        f"boundary_raw_sha256={OLD_FINAL_RAW_SHA256}"
    )
    state = _read_json_object(state_path) if state_path.exists() else {
        "frame": {
            "version": FRAME_VERSION, "page_size": page_size,
            "exclude_sha256": expected_exclusions, "endpoint": base.QLEVER_URL,
            "order": "desc", "route": ROUTE, "scope": frame_scope,
        },
        "pages": {}, "ids": [], "exclusion": exclusion_meta,
    }
    frame = state.get("frame", {})
    if frame.get("exclude_sha256") != expected_exclusions:
        raise ValueError("supplement exclusion ledger changed")
    if frame.get("order", "desc") != "desc" or frame.get("route", ROUTE) != ROUTE:
        raise ValueError("supplement discovery frame changed; use a new output")
    historical_pages = set(state.get("pages", {}))
    entity_path = output / "entity-state.json"
    entity = _read_json_object(entity_path) if entity_path.exists() else {
        "attempted": [], "completed": [], "held": [], "errors": []
    }
    if entity.get("in_flight_batch"):
        entity.setdefault("interrupted_batches", []).append({
            "batch": entity["in_flight_batch"].get("ids", []),
            "started_at": entity["in_flight_batch"].get("started_at"),
            "reason": "interrupted_before_entity_response; IDs remain held and are never resent",
            "observed_at": base.now(),
        })
        entity["in_flight_batch"] = None
        base.save(entity_path, entity)
    attempted = set(entity.get("attempted", []))
    completed = set(entity.get("completed", []))
    held = set(entity.get("held", []))
    pending_path = output / "pending-records.jsonl"
    pending = list(base.read_jsonl(pending_path)) if pending_path.exists() else []
    adapter = base.WikidataAdapter(
        base.AllLanguagesEntityFetcher(fetcher) if all_languages else fetcher
    )
    blocked = False

    def searchable() -> int:
        with sqlite3.connect(catalogue) as db:
            return db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]

    def flush(force: bool = False) -> None:
        nonlocal pending
        if not pending or (not force and len(pending) < 1000):
            return
        batch, pending = pending[:1000], pending[1000:]
        report = base.ingest_records(
            catalogue_path=catalogue, records=batch, conditions=conditions
        )
        base.write_jsonl(pending, pending_path)
        state.setdefault("ingest", []).append({"at": base.now(), "records": len(batch), **report})
        base.save(state_path, state)

    def process(ids: list[str]) -> bool:
        nonlocal pending, blocked
        candidates = [v for v in ids if v not in attempted and v not in held and v not in runtime_exclusions]
        for start in range(0, len(candidates), 50):
            batch = candidates[start:start + 50]
            attempted.update(batch)
            held.update(batch)
            entity.update(attempted=sorted(attempted), completed=sorted(completed),
                          held=sorted(held), updated_at=base.now())
            entity["in_flight_batch"] = {"ids": batch, "started_at": base.now()}
            base.save(entity_path, entity)
            result = adapter.fetch_entities(batch, languages=base.LANGUAGES)
            successes = {record.source_id for record in result.records}
            pending.extend(result.records)
            base.write_jsonl(pending, pending_path)
            completed.update(successes)
            held.difference_update(successes)
            entity.setdefault("errors", []).extend(
                {"batch": batch, "error": error} for error in result.errors
            )
            entity.update(attempted=sorted(attempted), completed=sorted(completed),
                          held=sorted(held), updated_at=base.now(), in_flight_batch=None)
            base.save(entity_path, entity)
            flush()
            if result.status == "failed":
                blocked = True
                return False
        return True

    if pending:
        flush(force=True)
    chosen = set(state.get("ids", []))
    resume = [value for value in state.get("ids", [])
              if value not in attempted and value not in held and value not in runtime_exclusions]
    if resume and not process(resume):
        blocked = True
    for offset in range(0, max_offset, page_size):
        if blocked:
            break
        key = f"{ROUTE}:{offset}"
        if key in state["pages"]:
            if state["pages"][key].get("status") == "success":
                continue
            if key in historical_pages:
                state["pages"][key].update(
                    status="held", error="interrupted_before_response", completed_at=base.now()
                )
                base.save(state_path, state)
                continue
            blocked = True
            break
        if searchable() >= target_display:
            break
        query = genre_sitelink_query(limit=page_size, offset=offset, descending=True)
        state["pages"][key] = {"status": "started", "query": query, "started_at": base.now()}
        base.save(state_path, state)
        response = fetcher.get(
            base.QLEVER_URL, source=base.WIKIDATA_SOURCE,
            params={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        page = state["pages"][key]
        if response.status != "success":
            artifact = response.artifact
            page.update(status="held", error=response.error,
                        status_code=response.status_code,
                        raw_path=artifact.path if artifact else None,
                        raw_sha256=artifact.sha256 if artifact else None,
                        completed_at=base.now())
            base.save(state_path, state)
            break
        try:
            ids = base.parse_bindings(response.content)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            artifact = response.artifact
            page.update(status="held", error=f"invalid QLever response: {exc}",
                        raw_path=artifact.path if artifact else None,
                        raw_sha256=artifact.sha256 if artifact else None,
                        completed_at=base.now())
            base.save(state_path, state)
            break
        fresh = [value for value in ids if value not in runtime_exclusions and value not in chosen]
        chosen.update(fresh)
        state["ids"] = sorted(chosen, key=lambda value: int(value[1:]))
        artifact = response.artifact
        page.update(status="success", returned=len(ids), fresh_count=len(fresh),
                    raw_path=artifact.path if artifact else None,
                    raw_sha256=artifact.sha256 if artifact else None,
                    completed_at=base.now())
        base.save(state_path, state)
        if not process(fresh):
            break
        if len(ids) < page_size:
            break
    flush(force=True)
    state["ids"] = sorted(chosen, key=lambda value: int(value[1:]))
    base.save(state_path, state)
    result = {
        "version": "scale-20000-qlever-genre-sitelink-supplement-v1",
        "discovery": len(chosen), "entity_completed": len(completed),
        "entity_held": len(held), "searchable": searchable(),
        "candidate_limit": max_offset, "target_display": target_display,
        "endpoint": base.QLEVER_URL, "completed_at": base.now(),
    }
    base.save(output / "manifest.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--v2-root", type=Path, default=V2_ROOT)
    parser.add_argument("--old-entity-state", type=Path, default=OLD_ENTITY_STATE)
    parser.add_argument("--native-exclusions", type=Path, default=NATIVE_EXCLUSIONS)
    parser.add_argument("--max-offset", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--target-display", type=int, default=None)
    parser.add_argument("--limited-languages", action="store_false", dest="all_languages",
                        help="use the established language list instead of all labels")
    parser.set_defaults(all_languages=True)
    args = parser.parse_args()
    print(acquire(output=args.output.resolve(), catalogue=args.catalogue.resolve(),
                  v2_root=args.v2_root.resolve(), max_offset=args.max_offset,
                  page_size=args.page_size, target_display=args.target_display,
                  all_languages=args.all_languages,
                  old_entity_state=args.old_entity_state.resolve(),
                  native_exclusions=args.native_exclusions.resolve()), flush=True)


if __name__ == "__main__":
    main()
