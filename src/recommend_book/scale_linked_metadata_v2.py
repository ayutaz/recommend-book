"""Incremental, source-bound linked-label repair for the QLever catalogue.

The worker in this module operates on saved primary Wikidata responses.  It
never rediscovers works, refetches a primary work, calls a model, or edits an
older source version.  Authority labels are accepted only from a SHA-bound
saved entity or from a one-shot ``props=labels`` request recorded in the
worker checkpoint.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from types import SimpleNamespace
from typing import Iterable

from .ingestion.core import HTTPFetcher, RawStore
from .ingestion.wikidata import ENTITY_URL, LANGUAGE_CODES, WikidataAdapter, claim_values
from .models import SourceRecord
from .scale_catalogue_v1 import ScaleCatalogue, _json, _qid, _sha


LANGUAGES = tuple(LANGUAGE_CODES.values())
LABEL_PROPERTIES = ("P50", "P655", "P98", "P110", "P136", "P921", "P495", "P179", "P674", "P840")
QID_RE = re.compile(r"Q[1-9][0-9]*\Z")
DEFAULT_CACHE = Path("data/autonomous-next-v3/scale-v1/acquisition/linked-entities.json")
DEFAULT_LABEL_RAW = Path("data/autonomous-next-v3/scale-100000-v1/linked-metadata-v2/raw")
DEFAULT_CHECKPOINT = Path("data/autonomous-next-v3/scale-100000-v1/linked-metadata-v2/checkpoint.json")
DEFAULT_GATE = Path("data/autonomous-next-v3/scale-30000-v1/acquisition/raw/http-rate-gate.sqlite3")


class LinkedMetadataHold(ValueError):
    """A row or authority cannot be safely repaired and must remain held."""

    def __init__(self, reason: str, *, missing: Iterable[str] = ()):
        super().__init__(reason)
        self.reason = reason
        self.missing = sorted(set(missing))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{__import__('os').getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def preferred_label(entity: dict) -> tuple[str, str] | None:
    labels = entity.get("labels", {}) if isinstance(entity, dict) else {}
    for language in LANGUAGES:
        item = labels.get(language)
        if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"]:
            return item["value"], language
    for language, item in labels.items():
        if isinstance(item, dict) and isinstance(item.get("value"), str) and item["value"]:
            return item["value"], language
    return None


def claim_ids(claims: dict, prop: str) -> set[str]:
    return {
        value["id"] for value in claim_values({"claims": claims}, prop)
        if isinstance(value, dict) and QID_RE.fullmatch(str(value.get("id", "")))
    }


def referenced_ids(record: SourceRecord | dict) -> set[str]:
    payload = record.model_dump(mode="json") if isinstance(record, SourceRecord) else record
    # ``extra.claims`` is a denormalized convenience field and is never an
    # authority for repair.  Verify and read the saved primary bytes first so
    # a changed raw file cannot trigger any linked-label request.
    path = Path(payload.get("raw_path", ""))
    expected = payload.get("raw_sha256")
    if not path.is_file() or not isinstance(expected, str) or sha256(path) != expected:
        raise LinkedMetadataHold("source raw hash changed")
    raw = json.loads(path.read_bytes())
    entity = raw.get("entities", {}).get(payload.get("source_id"), {})
    if not isinstance(entity, dict):
        raise LinkedMetadataHold("source entity missing from saved raw")
    claims = entity.get("claims", {})
    return set().union(*(claim_ids(claims, prop) for prop in LABEL_PROPERTIES))


def _proof_from_entity(qid: str, entity: dict, raw_path: Path, raw_sha: str) -> dict | None:
    if entity.get("id") != qid:
        return None
    label = preferred_label(entity)
    if label is None:
        return None
    value, language = label
    return {"label": value, "raw_path": str(raw_path.resolve()), "raw_sha256": raw_sha,
            "locator": f"entities.{qid}.labels.{language}"}


def load_cached_proofs(targets: set[str], cache_paths: Iterable[Path] = (DEFAULT_CACHE,)) -> dict[str, dict]:
    """Load only requested authorities and validate every referenced raw file."""
    proofs: dict[str, dict] = {}
    raw_cache: dict[Path, tuple[str, object] | None] = {}
    for cache_path in cache_paths:
        cache_path = Path(cache_path)
        if not cache_path.is_file():
            continue
        try:
            cache = load_json(cache_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(cache, dict):
            continue
        for qid, item in cache.items():
            if qid not in targets or qid in proofs or not isinstance(item, dict):
                continue
            raw_path = Path(item.get("raw_path", ""))
            expected = item.get("raw_sha256")
            if not raw_path.is_file() or not isinstance(expected, str):
                continue
            cached = raw_cache.get(raw_path)
            if cached is None and raw_path not in raw_cache:
                try:
                    actual = sha256(raw_path)
                    raw_cache[raw_path] = (actual, load_json(raw_path)) if actual == expected else None
                except (OSError, ValueError, json.JSONDecodeError):
                    raw_cache[raw_path] = None
                cached = raw_cache[raw_path]
            if cached is None:
                continue
            actual, payload = cached
            entity = payload.get("entities", {}).get(qid) if isinstance(payload, dict) else None
            if isinstance(entity, dict):
                proof = _proof_from_entity(qid, entity, raw_path, actual)
                if proof:
                    proofs[qid] = proof
    return proofs


def checkpoint_default() -> dict:
    return {"version": "scale-linked-metadata-worker-v2", "processed_versions": {},
            "held_versions": {}, "reference_attempted": [], "reference_held": {},
            "reference_proofs": {}, "updated_at": now()}


def load_checkpoint(path: Path = DEFAULT_CHECKPOINT) -> dict:
    if not path.exists():
        return checkpoint_default()
    state = load_json(path)
    if state.get("version") != "scale-linked-metadata-worker-v2":
        raise ValueError("linked metadata checkpoint version differs")
    for key, default in checkpoint_default().items():
        state.setdefault(key, default)
    return state


def _checkpoint_proofs(state: dict) -> dict[str, dict]:
    return {qid: item for qid, item in state.get("reference_proofs", {}).items() if isinstance(item, dict)}


def _record_payload(record: SourceRecord) -> dict:
    path = Path(record.raw_path)
    if not path.is_file() or sha256(path) != record.raw_sha256:
        raise LinkedMetadataHold("source raw hash changed")
    return json.loads(path.read_bytes())


class _SavedFetcher:
    def __init__(self, path: Path, raw: bytes, raw_sha: str, retrieved_at: str):
        self.path, self.raw, self.raw_sha, self.retrieved_at = path, raw, raw_sha, retrieved_at

    def get(self, *args, **kwargs):
        return SimpleNamespace(status="success", content=self.raw,
                               artifact=SimpleNamespace(path=str(self.path), sha256=self.raw_sha,
                                                        retrieved_at=self.retrieved_at))


def normalize_saved_record(record: SourceRecord, proofs: dict[str, dict]) -> SourceRecord:
    """Re-run the existing Wikidata adapter against one saved primary response."""
    targets = referenced_ids(record)
    missing = targets - set(proofs)
    if missing:
        raise LinkedMetadataHold("linked labels unavailable", missing=missing)
    path = Path(record.raw_path)
    raw = path.read_bytes()
    result = WikidataAdapter(_SavedFetcher(path, raw, record.raw_sha256, record.retrieved_at)).fetch_entities(
        [record.source_id], languages=LANGUAGES,
        linked_labels={qid: proofs[qid]["label"] for qid in targets})
    if result.errors or len(result.records) != 1:
        raise LinkedMetadataHold("saved Wikidata normalization failed: " + "; ".join(result.errors))
    normalized = result.records[0]
    old_evidence = [item for item in record.evidence if not item.evidence_id.startswith("wd:")]
    evidence = list(normalized.evidence)
    seen = {item.evidence_id for item in evidence}
    evidence.extend(item for item in old_evidence if item.evidence_id not in seen)
    extra = dict(normalized.extra)
    original_extra = dict(record.extra)
    original_extra.update(extra)
    original_extra["linked_label_provenance"] = {qid: proofs[qid] for qid in sorted(targets)}
    original_extra["linked_label_qids"] = sorted(targets)
    original_extra["source_record_version"] = "scale-linked-metadata-worker-v2"
    original_extra["metadata_repair_scope"] = "QLever primary raw linked labels; no primary refetch"
    return normalized.model_copy(update={"evidence": evidence, "extra": original_extra,
                                         "raw_path": record.raw_path, "raw_sha256": record.raw_sha256})


def _is_qid(value: object) -> bool:
    return isinstance(value, str) and bool(QID_RE.fullmatch(value))


def needs_repair(record: SourceRecord, row: dict) -> bool:
    authors = row.get("authors", [])
    if any(_is_qid(item.get("name")) or item.get("name") is None for item in authors if isinstance(item, dict)):
        return True
    genres = row.get("genres", [])
    if any(item.get("label") is None or _is_qid(item.get("label")) for item in genres if isinstance(item, dict)):
        return True
    return any(_is_qid(value) for value in row.get("subjects", [])) or _is_qid(row.get("series"))


def _under(root: Path, path: str) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def scan_candidates(catalogue_path: Path, *, qlever_roots: Iterable[Path], baseline_ids: set[str],
                    limit: int | None = 1000, excluded_versions: dict[str, str] | None = None) -> list[dict]:
    roots = [Path(root) for root in qlever_roots]
    result = []
    with closing(sqlite3.connect(f"file:{Path(catalogue_path).resolve().as_posix()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT w.qid,w.version_id,w.row_json,s.record_json,s.raw_path,s.raw_sha256 "
                          "FROM works w JOIN source_versions s ON s.version_id=w.version_id "
                          "ORDER BY w.qid")
        native = {row[0] for row in db.execute("SELECT qid FROM native_classifications")}
        # Stream within the explicitly closed read-only connection.
        for stored in rows:
            qid = stored["qid"]
            if (qid in baseline_ids or qid in native or
                    (excluded_versions and excluded_versions.get(qid) == stored["version_id"]) or
                    not any(_under(root, stored["raw_path"]) for root in roots)):
                continue
            try:
                record = SourceRecord.model_validate_json(stored["record_json"])
                row = json.loads(stored["row_json"])
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
            if needs_repair(record, row):
                result.append({"qid": qid, "version_id": stored["version_id"], "record": record, "row": row})
                if limit is not None and len(result) >= limit:
                    break
    return result


@contextmanager
def catalogue_writer_lock(catalogue_path: Path, *, wait_seconds: float = 120.0, poll_seconds: float = 1.0):
    lock_path = Path(catalogue_path).resolve().parent.parent / "catalogue-writer.lock"
    deadline = time.monotonic() + wait_seconds
    stream = None
    while stream is None:
        try:
            stream = lock_path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"catalogue writer lock wait expired: {lock_path}") from exc
            time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
    try:
        stream.write(json.dumps({"pid": __import__('os').getpid(), "owner": "scale-linked-metadata-worker-v2",
                                 "started_at": now(), "catalogue": str(Path(catalogue_path).resolve())}) + "\n")
        stream.close()
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)


def commit_normalized(catalogue_path: Path, candidate: dict, normalized: SourceRecord) -> dict:
    """Append a version and update only the current projection under the lock."""
    with catalogue_writer_lock(catalogue_path):
        catalogue = ScaleCatalogue(catalogue_path)
        conditions, rights = catalogue._conditions()
        with catalogue._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM works WHERE qid=?", (candidate["qid"],)).fetchone()
            if current is None or current["version_id"] != candidate["version_id"]:
                return {"status": "stale_version"}
            if db.execute("SELECT 1 FROM native_classifications WHERE qid=?", (candidate["qid"],)).fetchone():
                return {"status": "native_present"}
            try:
                checked, row = catalogue._projection(normalized, conditions, rights)
                for artifact in row["provenance"]["artifacts"]:
                    bound = db.execute("SELECT sha256 FROM raw_artifacts WHERE path=?", (artifact["raw_path"],)).fetchone()
                    if bound and bound[0] != artifact["raw_sha256"]:
                        raise ValueError("raw snapshot path reused with different content")
                    db.execute("INSERT OR IGNORE INTO raw_artifacts VALUES(?,?)", (artifact["raw_path"], artifact["raw_sha256"]))
                record_json = _json(checked.model_dump(mode="json"))
                record_sha = _sha(record_json.encode("utf-8"))
                version_id = f"wd:{candidate['qid']}:{record_sha}"
                db.execute("INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                           (version_id, candidate["qid"], record_sha, record_json, checked.raw_path,
                            checked.raw_sha256, checked.retrieved_at, catalogue._meta["source_conditions_sha256"]))
                search = " ".join([row["title"], *row["aliases"], *[item["name"] or item["qid"] for item in row["authors"]]]).casefold()
                db.execute("UPDATE works SET version_id=?,title=?,search_text=?,language=?,year=?,recommendable=?,display_allowed=?,row_json=? WHERE qid=?",
                           (version_id, row["title"], search, row["original_language"], row["year"],
                            int(row["recommendable"]), int(rights["display"] == "allowed"), _json(row), candidate["qid"]))
                db.execute("DELETE FROM work_genres WHERE qid=?", (candidate["qid"],))
                db.executemany("INSERT INTO work_genres VALUES(?,?,?)",
                               [(candidate["qid"], genre["qid"], genre["label"]) for genre in row["genres"]])
                return {"status": "updated", "version_id": version_id}
            except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
                raise


def fetch_missing_labels(targets: set[str], state: dict, *, checkpoint_path: Path | None = None,
                         raw_root: Path = DEFAULT_LABEL_RAW, gate_path: Path = DEFAULT_GATE,
                         fetcher: HTTPFetcher | None = None) -> dict[str, dict]:
    proofs = _checkpoint_proofs(state)
    attempted = set(state.get("reference_attempted", []))
    held = state.setdefault("reference_held", {})
    pending = sorted(targets - set(proofs) - set(held) - attempted)
    if not pending:
        return proofs
    if fetcher is None:
        raw_root.mkdir(parents=True, exist_ok=True)
        fetcher = HTTPFetcher(RawStore(raw_root), user_agent="recommend-book/0.1 qlever linked metadata v2",
                              min_interval=1.1, max_attempts=1)
        fetcher.gate_path = gate_path
    for start in range(0, len(pending), 50):
        batch = pending[start:start + 50]
        attempted.update(batch)
        state["reference_attempted"] = sorted(attempted)
        state["updated_at"] = now()
        if checkpoint_path is not None:
            save_json(checkpoint_path, state)
        response = fetcher.get(ENTITY_URL, source="wikidata",
                               params={"action": "wbgetentities", "ids": "|".join(batch),
                                       "props": "labels", "format": "json"})
        if response.status != "success":
            for qid in batch:
                held[qid] = {"reason": response.error or "label request failed", "status_code": response.status_code}
            continue
        try:
            payload = json.loads(response.content)
            entities = payload["entities"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            entities = {}
        for qid in batch:
            proof = _proof_from_entity(qid, entities.get(qid, {}) if isinstance(entities, dict) else {},
                                       Path(response.artifact.path), response.artifact.sha256)
            if proof:
                proofs[qid] = proof
            else:
                held[qid] = {"reason": "entity has no usable label"}
        state["reference_proofs"] = proofs
        state["updated_at"] = now()
        if checkpoint_path is not None:
            save_json(checkpoint_path, state)
    state["reference_proofs"] = proofs
    return proofs


def process_batch(catalogue_path: Path, candidates: list[dict], state: dict, *, checkpoint_path: Path,
                  cache_paths: Iterable[Path] = (DEFAULT_CACHE,), fetcher: HTTPFetcher | None = None,
                  raw_root: Path = DEFAULT_LABEL_RAW, gate_path: Path = DEFAULT_GATE) -> dict:
    if not candidates:
        return {"selected": 0, "updated": 0, "held": 0}
    valid_candidates = []
    result = {"selected": len(candidates), "updated": 0, "held": 0, "stale": 0, "native": 0}
    for candidate in candidates:
        try:
            referenced_ids(candidate["record"])
        except LinkedMetadataHold as exc:
            state.setdefault("held_versions", {})[candidate["qid"]] = {
                "version_id": candidate["version_id"], "missing": exc.missing, "reason": exc.reason}
            result["held"] += 1
            continue
        valid_candidates.append(candidate)
    if not valid_candidates:
        state["updated_at"] = now()
        save_json(checkpoint_path, state)
        return result
    targets = set().union(*(referenced_ids(item["record"]) for item in valid_candidates))
    proofs = load_cached_proofs(targets, cache_paths)
    proofs.update(_checkpoint_proofs(state))
    state["reference_proofs"] = proofs
    save_json(checkpoint_path, state)
    if targets - set(proofs):
        proofs = fetch_missing_labels(targets, state, checkpoint_path=checkpoint_path,
                                     raw_root=raw_root, gate_path=gate_path, fetcher=fetcher)
        save_json(checkpoint_path, state)
    for candidate in valid_candidates:
        version = candidate["version_id"]
        missing = referenced_ids(candidate["record"]) - set(proofs)
        if missing:
            state.setdefault("held_versions", {})[candidate["qid"]] = {"version_id": version, "missing": sorted(missing), "reason": "linked labels unavailable"}
            result["held"] += 1
            continue
        try:
            normalized = normalize_saved_record(candidate["record"], {qid: proofs[qid] for qid in referenced_ids(candidate["record"])})
            outcome = commit_normalized(catalogue_path, candidate, normalized)
        except LinkedMetadataHold as exc:
            state.setdefault("held_versions", {})[candidate["qid"]] = {"version_id": version, "missing": exc.missing, "reason": exc.reason}
            result["held"] += 1
            continue
        except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
            state.setdefault("held_versions", {})[candidate["qid"]] = {"version_id": version, "missing": [], "reason": str(exc)}
            result["held"] += 1
            continue
        if outcome["status"] == "updated":
            state.setdefault("processed_versions", {})[candidate["qid"]] = {"from": version, "to": outcome["version_id"]}
            result["updated"] += 1
        elif outcome["status"] == "stale_version":
            result["stale"] += 1
        elif outcome["status"] == "native_present":
            result["native"] += 1
    state["updated_at"] = now()
    save_json(checkpoint_path, state)
    return result
