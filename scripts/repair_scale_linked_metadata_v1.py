"""Rebuild unsent scale SourceRecords with proven linked Wikidata labels.

This lane consumes the contributor-repaired records, reuses saved raw/cache
proofs, fetches only the remaining linked IDs once, and writes a new source
record version.  Existing snapshots, ledgers, catalogue rows, and native
classification inputs are immutable.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse

from recommend_book.ingestion.core import HTTPFetcher, RawStore
from recommend_book.ingestion.wikidata import WikidataAdapter


ROOT = Path("data/autonomous-next-v3/scale-30000-v1")
INPUT = ROOT / "author-label-repair-v1/repaired-source-records.jsonl"
RAW_ROOT = ROOT / "acquisition/raw"
OUTPUT = ROOT / "linked-metadata-repair-v1"
ENTITY_URL = "https://www.wikidata.org/w/api.php"
LANGUAGES = ("en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn")
PROPS = ("P50", "P655", "P98", "P110", "P136", "P921", "P495", "P179", "P674", "P840")
CACHE_PATHS = (
    ROOT.parent / "scale-v1/acquisition/linked-entities.json",
    Path("data/wikidata-poc-v2/linked-entities.json"),
    Path("data/wikidata-poc/linked-entities.json"),
)
SAVED_BATCHES = tuple(Path("data/autonomous-next-v3/evaluation").rglob("*linked-entities-batch.json"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def claim_ids(claims: dict, prop: str) -> list[str]:
    result = []
    for statement in claims.get(prop, []):
        value = statement.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            result.append(value["id"])
    return result


def records() -> list[dict]:
    return [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines()]


def required_ids(record: dict) -> set[str]:
    claims = record.get("extra", {}).get("claims", {})
    locators = {item.get("locator") for item in record.get("evidence", [])}
    props = ["P50", "P655", "P98", "P110"]
    if "claims.P136;claims.P921" in locators:
        props.extend(("P136", "P921"))
    if record.get("region"):
        props.append("P495")
    if record.get("series"):
        props.append("P179")
    if "claims.P674" in locators:
        props.append("P674")
    if "claims.P840" in locators:
        props.append("P840")
    return {qid for prop in props for qid in claim_ids(claims, prop)}


def preferred_label(entity: dict) -> tuple[str, str] | None:
    labels = entity.get("labels", {})
    for language in LANGUAGES:
        item = labels.get(language)
        if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"]:
            return item["value"], language
    for language, item in labels.items():
        if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"]:
            return item["value"], language
    return None


def add_entity(proofs: dict[str, dict], qid: str, entity: dict, raw_path: Path, raw_sha: str) -> None:
    if entity.get("id") != qid:
        return
    label = preferred_label(entity)
    if label is None or qid in proofs:
        return
    value, language = label
    proofs[qid] = {
        "label": value,
        "raw_path": str(raw_path.resolve()),
        "raw_sha256": raw_sha,
        "locator": f"entities.{qid}.labels.{language}",
    }


def load_saved_proofs(targets: set[str]) -> dict[str, dict]:
    """Load only target entities, validating each raw object once per path."""
    proofs: dict[str, dict] = {}
    raw_cache: dict[Path, tuple[str, object] | None] = {}

    def use_raw(path: Path, expected_sha: str, qids: set[str]) -> None:
        if not path.is_file() or not isinstance(expected_sha, str):
            return
        cached = raw_cache.get(path)
        if cached is None and path not in raw_cache:
            try:
                actual = sha256(path)
                raw_cache[path] = (actual, load_json(path)) if actual == expected_sha else None
            except (OSError, ValueError, json.JSONDecodeError):
                raw_cache[path] = None
            cached = raw_cache[path]
        if cached is None:
            return
        actual, payload = cached
        if not isinstance(payload, dict):
            return
        for qid in qids:
            entity = payload.get("entities", {}).get(qid)
            if isinstance(entity, dict):
                add_entity(proofs, qid, entity, path, actual)

    for cache_path in CACHE_PATHS:
        if not cache_path.exists():
            continue
        try:
            cache = load_json(cache_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for qid, item in cache.items():
            if qid not in targets or qid in proofs or not isinstance(item, dict):
                continue
            raw_path = Path(item.get("raw_path", ""))
            use_raw(raw_path, item.get("raw_sha256", ""), {qid})

    # The scale acquisition manifest contains the author-label response
    # batches.  Its URLs identify the batch IDs, so do not parse unrelated raw
    # objects.
    manifest = RAW_ROOT / "manifest.jsonl"
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(item.get("source_url", "")).query)
            if query.get("props") != ["labels"]:
                continue
            batch = {qid for value in query.get("ids", []) for qid in urllib.parse.unquote(value).split("|")}
            wanted = targets & batch - proofs.keys()
            if wanted:
                use_raw(Path(item["path"]), item["sha256"], wanted)

    # Older saved evaluation batches are valid raw Wikidata proof objects and
    # are checked by SHA before being reused.
    for path in SAVED_BATCHES:
        wanted = targets - proofs.keys()
        if not wanted:
            break
        try:
            payload = load_json(path)
            actual = sha256(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for qid in wanted:
            entity = payload.get("entities", {}).get(qid) if isinstance(payload, dict) else None
            if isinstance(entity, dict):
                add_entity(proofs, qid, entity, path, actual)
    return proofs


def load_state(agent_ids: list[str]) -> dict:
    path = OUTPUT / "fetch-state.json"
    if path.exists():
        state = load_json(path)
        if state.get("agent_ids") != agent_ids:
            raise ValueError("linked metadata repair ID set changed")
        return state
    state = {"version": "scale-content-linked-metadata-fetch-v1", "agent_ids": agent_ids,
             "completed": [], "held": [], "errors": [], "fetched_proofs": [],
             "request_batches": 0, "created_at": now()}
    save_json(path, state)
    return state


def fetch_missing(agent_ids: list[str], saved: dict[str, dict]) -> tuple[dict, dict[str, dict]]:
    state = load_state(agent_ids)
    completed = set(state.get("completed", []))
    held = set(state.get("held", []))
    fetched = {item["qid"]: item["proof"] for item in state.get("fetched_proofs", [])}
    if state.get("stopped_reason"):
        return state, fetched
    pending = [qid for qid in agent_ids if qid not in saved and qid not in completed and qid not in held]
    fetcher = HTTPFetcher(RawStore(RAW_ROOT), user_agent="recommend-book/0.1 linked metadata repair", min_interval=1.1, max_attempts=1)
    for start in range(0, len(pending), 50):
        batch = pending[start:start + 50]
        held.update(batch)
        state["held"] = sorted(held)
        save_json(OUTPUT / "fetch-state.json", state)
        result = fetcher.get(ENTITY_URL, source="wikidata", params={"action": "wbgetentities", "ids": "|".join(batch), "props": "labels", "languages": "|".join(LANGUAGES), "format": "json", "maxlag": 5})
        state["request_batches"] = int(state.get("request_batches", 0)) + 1
        if result.status != "success":
            state["errors"].append({"batch": batch, "error": result.error, "status_code": result.status_code, "at": now()})
            state["stopped_reason"] = result.error or f"HTTP {result.status_code}"
            save_json(OUTPUT / "fetch-state.json", state)
            break
        try:
            payload = json.loads(result.content)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            state["errors"].append({
                "batch": batch,
                "error": f"malformed Wikidata JSON: {exc}",
                "status_code": result.status_code,
                "at": now(),
            })
            state["stopped_reason"] = "malformed Wikidata JSON"
            save_json(OUTPUT / "fetch-state.json", state)
            break
        # Wikidata can return HTTP 200 with an API-level maxlag/error object.
        # Keep the whole batch held and record the API error; these IDs must
        # never be misreported as individual entities without labels.
        if not isinstance(payload, dict):
            state["errors"].append({
                "batch": batch,
                "error": "Wikidata response must be a JSON object",
                "status_code": result.status_code,
                "at": now(),
            })
            state["stopped_reason"] = "Wikidata response must be a JSON object"
            save_json(OUTPUT / "fetch-state.json", state)
            break
        api_error = payload.get("error")
        if isinstance(api_error, dict):
            state["errors"].append({
                "batch": batch,
                "error": f"Wikidata API {api_error.get('code', 'error')}: {api_error.get('info', '')}",
                "status_code": result.status_code,
                "api_error": api_error,
                "at": now(),
            })
            state["stopped_reason"] = f"Wikidata API {api_error.get('code', 'error')}"
            save_json(OUTPUT / "fetch-state.json", state)
            break
        if not isinstance(payload.get("entities"), dict):
            state["errors"].append({
                "batch": batch,
                "error": "Wikidata response missing entities",
                "status_code": result.status_code,
                "at": now(),
            })
            state["stopped_reason"] = "Wikidata response missing entities"
            save_json(OUTPUT / "fetch-state.json", state)
            break
        got = set()
        for qid in batch:
            entity = payload.get("entities", {}).get(qid)
            label = preferred_label(entity) if isinstance(entity, dict) else None
            if not isinstance(entity, dict) or entity.get("id") != qid or label is None:
                state["errors"].append({"qid": qid, "error": "entity has no usable label", "at": now()})
                continue
            value, language = label
            proof = {"label": value, "raw_path": str(Path(result.artifact.path).resolve()), "raw_sha256": result.artifact.sha256, "locator": f"entities.{qid}.labels.{language}"}
            fetched[qid] = proof
            got.add(qid)
        completed.update(got)
        held.difference_update(got)
        state["completed"] = sorted(completed)
        state["held"] = sorted(held)
        state["fetched_proofs"] = [{"qid": qid, "proof": fetched[qid]} for qid in sorted(fetched)]
        save_json(OUTPUT / "fetch-state.json", state)
        print(json.dumps({"batch": start // 50 + 1, "completed": len(completed), "held": len(held), "errors": len(state["errors"])}, ensure_ascii=False), flush=True)
    return state, fetched


def merge_normalized(original: dict, normalized: dict, proof_map: dict[str, dict]) -> dict:
    merged = dict(original)
    for field in ("titles", "contributors", "identifiers", "work_refs", "original_language", "language", "first_publication_year", "publication_year", "publisher", "series", "volume", "work_kind", "region", "subjects", "raw_sha256", "raw_path", "rights_policy_id"):
        if field in normalized:
            merged[field] = normalized[field]
    old_evidence = [item for item in original.get("evidence", []) if not str(item.get("evidence_id", "")).startswith("wd:")]
    seen = {item.get("evidence_id") for item in normalized.get("evidence", [])}
    merged["evidence"] = normalized.get("evidence", []) + [item for item in old_evidence if item.get("evidence_id") not in seen]
    extra = dict(original.get("extra", {}))
    extra.update(normalized.get("extra", {}))
    extra["linked_label_provenance"] = {qid: proof_map[qid] for qid in sorted(proof_map)}
    extra["linked_label_qids"] = sorted(proof_map)
    extra["source_record_version"] = "scale-content-linked-metadata-repair-v1"
    extra["metadata_repair_scope"] = "canonical Wikidata linked labels and evidence; original content evidence retained"
    merged["extra"] = extra
    return merged


def build(records_in: list[dict], proofs: dict[str, dict], state: dict) -> dict:
    ready, held = [], []
    for row in records_in:
        missing = sorted(required_ids(row) - set(proofs))
        if missing:
            held.append({"work_qid": row["source_id"], "reason": "linked_metadata_label_unresolved", "missing_agent_qids": missing})
            continue
        ready.append(row)
    by_path: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    for row in ready:
        by_path.setdefault((row["raw_path"], row["raw_sha256"]), []).append(row)
    normalized_by_qid = {}
    for (raw_path, raw_sha), rows in by_path.items():
        path = Path(raw_path)
        if sha256(path) != raw_sha:
            raise ValueError(f"source raw hash changed: {path}")
        content = path.read_bytes()

        class SavedFetcher:
            def get(self, *args, **kwargs):
                from types import SimpleNamespace
                return SimpleNamespace(status="success", content=content, artifact=SimpleNamespace(path=str(path), sha256=raw_sha, retrieved_at=rows[0]["retrieved_at"]))

        result = WikidataAdapter(SavedFetcher()).fetch_entities([row["source_id"] for row in rows], languages=LANGUAGES, linked_labels={qid: proof["label"] for qid, proof in proofs.items()})
        if result.errors:
            raise ValueError("saved Wikidata normalization failed: " + "; ".join(result.errors))
        for normalized in result.records:
            normalized_by_qid[normalized.source_id] = normalized.model_dump(mode="json")
    output_path = OUTPUT / "repaired-source-records.jsonl"
    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        for row in ready:
            f.write(json.dumps(merge_normalized(row, normalized_by_qid[row["source_id"]], {qid: proofs[qid] for qid in required_ids(row)}), ensure_ascii=False, separators=(",", ":")) + "\n")
    report = {"version": "scale-content-linked-metadata-repair-v1", "input_records": len(records_in), "repaired_work_count": len(ready), "held_work_count": len(held), "held": held, "required_linked_qids": len(set().union(*(required_ids(r) for r in records_in))), "proven_proof_qids": len(proofs), "fetch_completed": len(state.get("completed", [])), "fetch_held": len(state.get("held", [])), "fetch_errors": len(state.get("errors", [])), "network_requests": state.get("request_batches", 0), "records_sha256": sha256(output_path), "completed_at": now(), "source_scope": "new SourceRecord versions only; prior source snapshots, ledgers, catalogue, and native inputs unchanged"}
    save_json(OUTPUT / "repair-manifest.json", report)
    return report


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source_records = records()
    required = set().union(*(required_ids(row) for row in source_records))
    saved = load_saved_proofs(required)
    agent_ids = sorted(required)
    save_json(OUTPUT / "repair-input-manifest.json", {"version": "scale-content-linked-metadata-repair-input-v1", "input_sha256": sha256(INPUT), "input_records": len(source_records), "required_linked_qids": len(required), "saved_proof_qids": len(saved), "missing_before_fetch": sorted(required - set(saved)), "batch_size": 50, "max_attempts": 1, "created_at": now()})
    state, fetched = fetch_missing(agent_ids, saved)
    proofs = dict(saved)
    proofs.update(fetched)
    report = build(source_records, proofs, state)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
