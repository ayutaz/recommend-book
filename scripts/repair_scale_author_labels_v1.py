"""Repair unresolved Wikidata contributor labels for held scale-content works.

The input pool is frozen by ``author-label-repair-request-v1.json``.  This
script only creates new SourceRecord versions for rows whose unresolved
contributor IDs can be proven from an existing saved entity or one bounded
Wikidata labels request.  It never changes the source records, prepared input
ledgers, or the catalogue database.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.parse

from recommend_book.ingestion.core import HTTPFetcher, RawStore


ROOT = Path("data/autonomous-next-v3/scale-30000-v1")
REQUEST = ROOT / "author-label-repair-request-v1.json"
RAW_ROOT = ROOT / "acquisition/raw"
ENTITY_URL = "https://www.wikidata.org/w/api.php"
LANGUAGES = ("en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn")
AGENT_RE = re.compile(r"Q[1-9][0-9]*\Z")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def request_digest() -> str:
    return sha256(REQUEST)


def output_dir() -> Path:
    return ROOT / "author-label-repair-v1"


def load_request() -> dict:
    request = load_json(REQUEST)
    if request.get("version") != "scale-content-linked-label-repair-request-v1":
        raise ValueError("unexpected repair request version")
    if request.get("no_workinputs_or_usage_ledger_created_for_these_held_sources") is not True:
        raise ValueError("repair request is not isolated from prepared ledgers")
    if request.get("immutable_already_prepared_sources_excluded") is not True:
        raise ValueError("repair request does not exclude immutable prepared sources")
    for row in request.get("rows", []):
        if not re.fullmatch(r"Q[1-9][0-9]*", row.get("work_qid", "")):
            raise ValueError("invalid work QID")
        for item in row.get("unresolved_contributors", []):
            if not AGENT_RE.fullmatch(item.get("agent_qid", "")):
                raise ValueError("invalid contributor QID")
            if not item.get("current_name"):
                raise ValueError("missing current contributor name")
    return request


def load_cache(target_ids: set[str]) -> dict[str, dict]:
    """Load old linked label caches, preferring the pinned scale-v1 cache."""
    result: dict[str, dict] = {}
    raw_cache: dict[Path, object] = {}
    caches = (
        ROOT.parent / "scale-v1/acquisition/linked-entities.json",
        Path("data/wikidata-poc-v2/linked-entities.json"),
    )
    for cache_path in caches:
        if not cache_path.exists():
            continue
        cache = load_json(cache_path)
        for qid, item in cache.items():
            if qid not in target_ids or qid in result or not isinstance(item, dict):
                continue
            entity = item.get("entity", {})
            raw_path = Path(item.get("raw_path", ""))
            raw_sha = item.get("raw_sha256")
            if not raw_path.is_file() or not isinstance(raw_sha, str):
                continue
            if raw_path not in raw_cache:
                try:
                    if sha256(raw_path) != raw_sha:
                        raw_cache[raw_path] = None
                    else:
                        raw_cache[raw_path] = load_json(raw_path)
                except (OSError, ValueError, json.JSONDecodeError):
                    raw_cache[raw_path] = None
            raw = raw_cache[raw_path]
            if not isinstance(raw, dict):
                continue
            raw_entity = raw.get("entities", {}).get(qid)
            if not isinstance(raw_entity, dict) or raw_entity.get("id") != qid:
                continue
            labels = raw_entity.get("labels", {})
            if not isinstance(labels, dict) or not any(isinstance(v, dict) and isinstance(v.get("value"), str) for v in labels.values()):
                continue
            # Keep the exact source entity from the raw response.  The cache
            # itself is only an index; the proof always points at raw bytes.
            result[qid] = {"raw_path": str(raw_path), "raw_sha256": raw_sha, "entity": raw_entity}
    return result


def label_proof(qid: str, item: dict) -> dict | None:
    entity = item.get("entity", {})
    labels = entity.get("labels", {})
    for language in LANGUAGES:
        value = labels.get(language, {}).get("value") if isinstance(labels.get(language), dict) else None
        if isinstance(value, str) and value:
            return {
                "label": value,
                "raw_path": str(Path(item["raw_path"]).resolve()),
                "raw_sha256": item["raw_sha256"],
                "locator": f"entities.{qid}.labels.{language}",
            }
    for language, label in labels.items():
        if isinstance(label, dict) and isinstance(label.get("value"), str) and label["value"]:
            return {
                "label": label["value"],
                "raw_path": str(Path(item["raw_path"]).resolve()),
                "raw_sha256": item["raw_sha256"],
                "locator": f"entities.{qid}.labels.{language}",
            }
    return None


def load_state(out: Path, agent_ids: list[str]) -> dict:
    path = out / "fetch-state.json"
    if path.exists():
        state = load_json(path)
        if state.get("request_sha256") != request_digest() or state.get("agent_ids") != agent_ids:
            raise ValueError("repair request or agent set changed")
        return state
    state = {
        "version": "scale-content-linked-label-repair-fetch-v1",
        "request_sha256": request_digest(),
        "agent_ids": agent_ids,
        "completed": [],
        "held": [],
        "errors": [],
        "request_batches": 0,
        "created_at": now(),
    }
    save_json(path, state)
    return state


def fetch_missing(out: Path, missing: list[str]) -> dict:
    state = load_state(out, missing)
    completed = set(state["completed"])
    held = set(state["held"])
    pending = [qid for qid in missing if qid not in completed and qid not in held]
    # acquisition/raw is the established Wikidata gate and manifest.  Using
    # it here serializes this lane with any other external metadata worker.
    fetcher = HTTPFetcher(
        RawStore(RAW_ROOT),
        user_agent="recommend-book/0.1 (https://github.com/ayutaz/recommend-book) contributor label repair",
        min_interval=1.1,
        max_attempts=1,
    )
    fetched: dict[str, dict] = {}
    for start in range(0, len(pending), 50):
        batch = pending[start : start + 50]
        # A batch is held before the request so an interrupted run cannot
        # resend it.  Successful IDs are removed below; failed IDs remain
        # held and are intentionally never retried.
        held.update(batch)
        state["held"] = sorted(held)
        save_json(out / "fetch-state.json", state)
        result = fetcher.get(
            ENTITY_URL,
            source="wikidata",
            params={
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "labels",
                "languages": "|".join(LANGUAGES),
                "format": "json",
                "maxlag": 5,
            },
        )
        state["request_batches"] = int(state.get("request_batches", 0)) + 1
        if result.status != "success":
            state["errors"].append({"batch": batch, "error": result.error, "status_code": result.status_code, "at": now()})
            # The held batch is deliberately never retried on resume.
            save_json(out / "fetch-state.json", state)
            print(json.dumps({"batch": start // 50 + 1, "held": len(batch), "error": result.error}), flush=True)
            continue
        payload = json.loads(result.content)
        entities = payload.get("entities", {})
        if not isinstance(entities, dict):
            raise ValueError("Wikidata labels response has no entities map")
        got = set()
        for qid in batch:
            entity = entities.get(qid)
            if not isinstance(entity, dict) or entity.get("id") != qid or "missing" in entity:
                state["errors"].append({"qid": qid, "error": "entity missing from successful response", "at": now()})
                continue
            item = {"raw_path": result.artifact.path, "raw_sha256": result.artifact.sha256, "entity": entity}
            if label_proof(qid, item) is None:
                state["errors"].append({"qid": qid, "error": "entity has no label", "at": now()})
                continue
            fetched[qid] = item
            got.add(qid)
        completed |= got
        held.difference_update(got)
        state["completed"] = sorted(completed)
        state["held"] = sorted(held)
        save_json(out / "fetch-state.json", state)
        print(json.dumps({"batch": start // 50 + 1, "completed": len(completed), "held": len(state["held"]), "errors": len(state["errors"])}, ensure_ascii=False), flush=True)
    return state


def load_fetched(out: Path) -> dict[str, dict]:
    state = load_json(out / "fetch-state.json")
    requested = set(state.get("completed", []))
    # Index the manifest once.  One raw response serves up to fifty IDs, so
    # scanning and parsing it once per ID was needlessly quadratic.
    artifact_by_qid: dict[str, tuple[Path, str]] = {}
    manifest_path = RAW_ROOT / "manifest.jsonl"
    if not manifest_path.exists():
        return {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        query_values = urllib.parse.parse_qs(urllib.parse.urlparse(item.get("source_url", "")).query).get("ids", [])
        query_ids = {qid for value in query_values for qid in urllib.parse.unquote(value).split("|")}
        path = Path(item["path"])
        for qid in requested & query_ids:
            # Keep the newest manifest entry, matching the previous reverse
            # scan while still parsing the manifest only once.
            artifact_by_qid[qid] = (path, item["sha256"])
    result: dict[str, dict] = {}
    payload_cache: dict[Path, object] = {}
    for qid, (path, expected_sha) in artifact_by_qid.items():
        if path not in payload_cache:
            try:
                if sha256(path) != expected_sha:
                    payload_cache[path] = None
                else:
                    payload_cache[path] = load_json(path)
            except (OSError, ValueError, json.JSONDecodeError):
                payload_cache[path] = None
        payload = payload_cache[path]
        if not isinstance(payload, dict):
            continue
        entity = payload.get("entities", {}).get(qid)
        if isinstance(entity, dict) and entity.get("id") == qid:
            result[qid] = {"raw_path": str(path.resolve()), "raw_sha256": expected_sha, "entity": entity}
    return result


def build_records(out: Path, request: dict, proofs: dict[str, dict]) -> dict:
    repaired = []
    held = []
    request_sha256 = request_digest()
    # Source rows are grouped across a small number of immutable JSONL files.
    # Keep only a bounded number of complete line arrays in memory and retain
    # the per-row SHA check below.
    source_lines: OrderedDict[Path, list[str]] = OrderedDict()

    def get_source_line(source_path: Path, line_number: int) -> str:
        lines = source_lines.get(source_path)
        if lines is None:
            lines = source_path.read_text(encoding="utf-8").splitlines()
            source_lines[source_path] = lines
            source_lines.move_to_end(source_path)
            if len(source_lines) > 8:
                source_lines.popitem(last=False)
        else:
            source_lines.move_to_end(source_path)
        return lines[line_number - 1]

    for row in request["rows"]:
        source_ref = row["supported_source_ref"]
        source_path = Path(source_ref["path"])
        source_line = get_source_line(source_path, source_ref["line"])
        if hashlib.sha256(source_line.encode("utf-8")).hexdigest() != source_ref["record_sha256"]:
            raise ValueError(f"supported source record changed: {row['work_qid']}")
        record = json.loads(source_line)
        if record.get("source_id") != row["work_qid"]:
            raise ValueError("source identity differs from repair request")
        unresolved = {x["agent_qid"] for x in row["unresolved_contributors"]}
        missing = sorted(unresolved - set(proofs))
        if missing:
            held.append({"work_qid": row["work_qid"], "reason": "linked_contributor_label_unresolved", "missing_agent_qids": missing})
            continue
        extra = dict(record.get("extra", {}))
        provenance = dict(extra.get("linked_label_provenance", {}))
        used = set(extra.get("linked_label_qids", []))
        for contributor in record.get("contributors", []):
            ids = [i.get("value") for i in contributor.get("identifiers", []) if i.get("source") == "wikidata" and i.get("scope") == "agent"]
            for qid in ids:
                if qid in proofs:
                    provenance[qid] = proofs[qid]
                    used.add(qid)
                    if contributor.get("name") == qid and qid in unresolved:
                        contributor["name"] = proofs[qid]["label"]
        extra["linked_label_provenance"] = provenance
        extra["linked_label_qids"] = sorted(used)
        extra["source_record_version"] = "scale-content-linked-label-repair-v1"
        extra["repair_request_sha256"] = request_sha256
        record["extra"] = extra
        repaired.append(record)
    repaired_path = out / "repaired-source-records.jsonl"
    temporary = repaired_path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as f:
        for record in repaired:
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(repaired_path)
    report = {
        "version": "scale-content-linked-label-repair-v1",
        "request_sha256": request_sha256,
        "requested_work_count": len(request["rows"]),
        "distinct_agent_qids": request["distinct_agent_qids"],
        "proof_agent_qids": len(proofs),
        "repaired_work_count": len(repaired),
        "held_work_count": len(held),
        "held": held,
        "records_sha256": sha256(repaired_path),
        "completed_at": now(),
        "network_requests": int(load_json(out / "fetch-state.json").get("request_batches", 0)),
        "source_scope": "new SourceRecord versions only; original supported records and prepared ledgers unchanged",
    }
    save_json(out / "repair-manifest.json", report)
    return report


def run() -> dict:
    request = load_request()
    out = output_dir()
    out.mkdir(parents=True, exist_ok=True)
    unresolved = OrderedDict()
    for row in request["rows"]:
        for contributor in row["unresolved_contributors"]:
            unresolved.setdefault(contributor["agent_qid"], None)
    agent_ids = list(unresolved)
    cache = load_cache(set(agent_ids))
    cached_proofs = {qid: proof for qid, item in cache.items() if (proof := label_proof(qid, item))}
    missing = [qid for qid in agent_ids if qid not in cached_proofs]
    save_json(out / "repair-input-manifest.json", {
        "version": "scale-content-linked-label-repair-input-v1",
        "request_sha256": request_digest(),
        "requested_work_count": len(request["rows"]),
        "distinct_agent_qids": len(agent_ids),
        "cache_proof_agent_qids": len(cached_proofs),
        "missing_agent_qids": missing,
        "network_batch_size": 50,
        "max_attempts": 1,
        "languages": list(LANGUAGES),
        "created_at": now(),
    })
    state = fetch_missing(out, missing) if missing else load_state(out, missing)
    fetched = load_fetched(out) if missing else {}
    proofs = dict(cached_proofs)
    proofs.update({qid: proof for qid, item in fetched.items() if (proof := label_proof(qid, item))})
    report = build_records(out, request, proofs)
    report.update({"cache_proof_agent_qids": len(cached_proofs), "fetched_proof_agent_qids": len(proofs) - len(cached_proofs), "fetch_held_agent_qids": len(state.get("held", [])), "fetch_errors": len(state.get("errors", []))})
    save_json(out / "repair-manifest.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("all",), default="all")
    parser.parse_args()
    run()
