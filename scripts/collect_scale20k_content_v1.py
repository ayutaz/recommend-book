"""Plan or collect bounded v20 Wikipedia prose windows.

The default command is a read-only dry preflight.  HTTP requires the explicit
``--execute-http`` switch and always uses one HTTP attempt, a shared raw store,
and the persisted selection ledger.  Old attempts, holds and no-sitelink works
are excluded before selection and are never retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from recommend_book.models import SourceRecord
except ModuleNotFoundError:  # dry preflight can run without optional model deps
    SourceRecord = None  # type: ignore[assignment,misc]
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
V19 = ROOT / "data/autonomous-next-v3/scale-100000-v1"
V20 = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1"
INVENTORY = V20 / "inventory-v1/eligible-supply-v1.jsonl"
CONDITIONS = ROOT / "data/autonomous-next-v3/scale-30000-v1/native-source-conditions-v1.json"
REGISTRY = ROOT / "data/autonomous-next-v3/scale-100000-v1/native-source-registry-v1.json"
DEFAULT_OUTPUT = V20 / "content-acquisition-v1/window-0001-0100"
SHARED_RAW_STORE = ROOT / "data/autonomous-next-v3/scale-30000-v1/shared-content-fetch-v1"
UNKNOWN_CONTENT_INVENTORY = V20 / "inventory-v2/unknown-source-genre-content-potential-v1.jsonl"
LANGUAGES = ("en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_bytes())


def choose_sitelink(record: Any, supported: dict[str, Any] | None = None) -> tuple[str, str] | None:
    links = record.extra.get("sitelinks", {})
    preferred = list(dict.fromkeys([getattr(record, "original_language", None), *LANGUAGES]))
    for language in preferred:
        if language not in LANGUAGES or language + "wiki" not in links:
            continue
        title = links[language + "wiki"]["title"]
        if supported is not None:
            candidate = supported.get(language, supported.get(language + "wiki"))
            candidate = candidate.get("title") if isinstance(candidate, dict) else candidate
            if candidate != title:
                continue
        return language, title
    return None


def _qid_from_row(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in ("qid", "source_id", "wikidata_qid", "work_id", "input_work_id"):
        value = row.get(key)
        if isinstance(value, str):
            if value.startswith("wd:"):
                value = value.split(":", 2)[1]
            if value.startswith("Q"):
                return value
    identifiers = row.get("identifiers")
    if isinstance(identifiers, list):
        for identifier in identifiers:
            if isinstance(identifier, dict) and identifier.get("source") == "wikidata" and identifier.get("scope") == "work":
                value = identifier.get("value")
                if isinstance(value, str) and value.startswith("Q"):
                    return value
    return None


def _qids_from_compact(path: Path) -> set[str]:
    """Read a known compact JSON/JSONL ledger, including pretty JSON objects."""
    qids: set[str] = set()
    try:
        if path.suffix == ".jsonl":
            values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        else:
            value = _json(path)
            if isinstance(value, list):
                values = value
            elif isinstance(value, dict):
                values = []
                for key in ("rows", "selections", "outcomes", "records", "pins", "qids", "selected_qids"):
                    candidate = value.get(key)
                    if isinstance(candidate, list):
                        values.extend(candidate if key != "qids" and key != "selected_qids" else [{"qid": x} for x in candidate])
                if not values:
                    values = [value]
            else:
                values = []
    except (OSError, ValueError, TypeError):
        return qids
    for row in values:
        qid = _qid_from_row(row)
        if qid:
            qids.add(qid)
    return qids


def _scan_old_attempts() -> set[str]:
    """Union bounded attempt/exclusion ledgers across v18, v19 and v20.

    Selections are read before HTTP, so selected, held, failed and no-sitelink
    rows remain excluded after an interrupted run.  This deliberately names
    compact ledger locations instead of walking raw/history trees.
    """
    qids: set[str] = set()
    for root in (V19, ROOT / "data/autonomous-next-v3/scale-30000-v1", ROOT / "data/autonomous-next-v3/scale-100000-v1", V20):
        for name in ("native-exclusions-v1.json", "native-exclusions-v2.json", "native-exclusions-v3.json"):
            path = root / name
            if path.exists():
                qids.update(_qids_from_compact(path))
        reservations = root / "attempt-reservations"
        if reservations.exists():
            for path in sorted(reservations.glob("*.json")):
                qids.update(_qids_from_compact(path))
    roots = [V19 / "content", ROOT / "data/autonomous-next-v3/scale-30000-v1", ROOT / "data/autonomous-next-v3/scale-100000-v1", ROOT / "data/autonomous-next-v3/scale-100000-v1/content", V20 / "content-acquisition-v1"]
    for root in roots:
        if not root.exists():
            continue
        patterns = ("content-v*/manifest.json", "content-v*/content-inputs.jsonl", "content-window-*/manifest.json",
                    "content-window-*/selections.json", "content-window-*/outcomes.json", "content-window-*/source-pins.jsonl",
                    "content-window-*/records.jsonl", "window-*/manifest.json", "window-*/selections.json",
                    "window-*/outcomes.json", "window-*/source-pins.jsonl", "window-*/records.jsonl")
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                qids.update(_qids_from_compact(path))
    for source_root in (V19 / "classification-sources", V19 / "classification-sources-v2", V19 / "classification-sources-v3", V20 / "classification-sources-v1"):
        for path in sorted(source_root.glob("window-*/work-mapping.json")):
            qids.update(_qids_from_compact(path))
    return qids


def _old_attempts() -> set[str]:
    """Load the one-time old union, then only incremental v20 ledgers."""
    fixed_attempt_index = V20 / "content-acquisition-v1/old-attempt-index-v1.json"
    if not fixed_attempt_index.exists():
        return _scan_old_attempts()
    qids = _qids_from_compact(fixed_attempt_index)
    for name in ("native-exclusions-v1.json", "native-exclusions-v2.json", "native-exclusions-v3.json"):
        path = V20 / name
        if path.exists():
            qids.update(_qids_from_compact(path))
    reservations = V20 / "attempt-reservations"
    if reservations.exists():
        for path in sorted(reservations.glob("*.json")):
            qids.update(_qids_from_compact(path))
    root = V20 / "content-acquisition-v1"
    for pattern in ("window-*/selections.json",):
        for path in sorted(root.glob(pattern)):
            qids.update(_qids_from_compact(path))
    return qids


def _rights_gate(conditions: Path, now: datetime, registry: Path | None = None) -> dict[str, Any]:
    registry = registry or REGISTRY
    value = _json(conditions)
    policies = {p.get("policy_id"): p for p in _json(registry).get("rights_policies", []) if isinstance(p, dict)}
    required = ("wikidata-cc0", "wikipedia-screened-prose")
    required_urls = {
        "wikidata-cc0": {"https://www.wikidata.org/wiki/Wikidata:Licensing"},
        "wikipedia-screened-prose": {"https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use", "https://creativecommons.org/licenses/by-sa/4.0/"},
    }
    receipts: list[dict[str, Any]] = []
    for policy_id in required:
        rows = value.get(policy_id)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"missing official rights receipts for {policy_id}")
        policy = policies.get(policy_id)
        if not policy:
            raise ValueError(f"missing rights registry policy {policy_id}")
        usages = policy.get("usages", {})
        if any(usages.get(purpose) not in {"allowed", "conditional"} for purpose in ("acquire", "local_storage", "external_llm")):
            raise ValueError(f"registry does not permit route purposes for {policy_id}")
        seen_urls: set[str] = set()
        for receipt in rows:
            if not isinstance(receipt, dict):
                raise ValueError(f"invalid receipt for {policy_id}")
            raw_path = Path(str(receipt.get("raw_path", "")))
            expected = receipt.get("raw_sha256")
            if not raw_path.is_file() or not isinstance(expected, str) or sha(raw_path) != expected:
                raise ValueError(f"official receipt snapshot mismatch for {policy_id}")
            stamp = receipt.get("verified_at")
            if not isinstance(stamp, str):
                raise ValueError(f"receipt missing verified_at for {policy_id}")
            verified_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            age_days = (now - verified_at).total_seconds() / 86400
            if age_days > 7:
                raise ValueError(f"{policy_id} rights receipt is stale: {age_days:.2f} days")
            host = (str(receipt.get("final_url") or receipt.get("source_url"))).split("/", 3)[2].lower()
            allowed_hosts = {"wikidata.org", "www.wikidata.org", "foundation.wikimedia.org", "creativecommons.org", "www.creativecommons.org"}
            if host not in allowed_hosts:
                raise ValueError(f"unregistered official rights host for {policy_id}: {host}")
            url = str(receipt.get("final_url") or receipt.get("source_url"))
            if receipt.get("status_code") not in (None, 200):
                raise ValueError(f"official rights receipt is not HTTP 200 for {policy_id}")
            seen_urls.add(url)
            receipts.append({"policy_id": policy_id, "verified_at": verified_at.isoformat(), "age_days": round(age_days, 3), "raw_sha256": expected, "source_url": receipt.get("source_url")})
        if not required_urls[policy_id].issubset(seen_urls):
            raise ValueError(f"required official URLs missing for {policy_id}")
    return {"path": str(conditions.resolve()), "sha256": sha(conditions), "registry_path": str(registry.resolve()), "registry_sha256": sha(registry), "receipts": receipts, "max_age_days": 7}


def select_authorities(*, catalogue: Path, inventory: Path, limit: int, excluded: set[str],
                       no_sitelink_qids: set[str] | None = None,
                       selected_sitelinks: dict[str, tuple[str, str]] | None = None,
                       inventory_mode: str = "known-genre") -> tuple[list[SourceRecord], dict[str, int]]:
    if inventory_mode not in {"known-genre", "unknown-source-genre-content"}:
        raise ValueError(f"unsupported inventory mode: {inventory_mode}")
    rows = []
    for line in inventory.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("qid") not in excluded:
                rows.append(row)
    authorities: list[SourceRecord] = []
    counts = Counter(inventory_rows=len(rows))
    db = sqlite3.connect(catalogue.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        for row in rows:
            if len(authorities) >= limit:
                break
            if inventory_mode == "unknown-source-genre-content":
                if row.get("source_genre") != "unknown" or row.get("source_genre_recommendable") is not False:
                    counts["unknown_policy_rejected"] += 1
                    continue
                hit = db.execute("SELECT w.qid,w.version_id,s.record_json,s.record_sha256,s.raw_path,s.raw_sha256 FROM works w JOIN source_versions s ON s.version_id=w.version_id WHERE w.qid=? AND w.display_allowed=1", (row["qid"],)).fetchone()
            else:
                hit = db.execute("SELECT w.qid,w.version_id,s.record_json,s.record_sha256,s.raw_path,s.raw_sha256 FROM works w JOIN source_versions s ON s.version_id=w.version_id WHERE w.qid=? AND w.recommendable=1 AND w.display_allowed=1", (row["qid"],)).fetchone()
            if hit is None:
                counts["not_current_display_authority" if inventory_mode == "unknown-source-genre-content" else "not_current_recommendable"] += 1; continue
            qid, version_id, payload, record_sha, raw_path, raw_sha = hit
            if (hashlib.sha256(payload.encode("utf-8")).hexdigest() != record_sha
                    or row.get("record_sha256") != record_sha or row.get("raw_sha256") != raw_sha
                    or (inventory_mode == "unknown-source-genre-content" and row.get("source_version_id") != version_id)):
                counts["source_pin_rejected"] += 1; continue
            if SourceRecord is None:
                value = json.loads(payload)
                authority = SimpleNamespace(**value)
            else:
                authority = SourceRecord.model_validate_json(payload)
            if authority.source != "wikidata" or authority.source_id != qid or authority.raw_sha256 != raw_sha or authority.raw_path != raw_path:
                counts["identity_rejected"] += 1; continue
            if inventory_mode == "unknown-source-genre-content" and authority.work_kind not in {"novel", "light_novel"}:
                counts["not_explicit_novel_or_light_novel"] += 1; continue
            supported = row.get("supported_sitelinks") if inventory_mode == "unknown-source-genre-content" else None
            selected = choose_sitelink(authority, supported)
            if selected is None:
                if no_sitelink_qids is not None:
                    no_sitelink_qids.add(qid)
                counts["no_sitelink"] += 1; continue
            if selected_sitelinks is not None:
                selected_sitelinks[qid] = selected
            authorities.append(authority)
    finally:
        db.close()
    counts["selected"] = len(authorities)
    if len(authorities) < limit:
        raise ValueError(f"only {len(authorities)} eligible unattempted authorities, requested {limit}")
    return authorities, dict(counts)


def build_preflight(*, catalogue: Path = CATALOGUE, inventory: Path = INVENTORY,
                    conditions: Path = CONDITIONS, limit: int = 100,
                    output: Path = DEFAULT_OUTPUT, now: datetime | None = None,
                    inventory_mode: str = "known-genre") -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"v20 acquisition output already exists: {output}")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    now = now or datetime.now(timezone.utc)
    excluded = _old_attempts()
    rights = _rights_gate(conditions, now)
    authorities, counts = select_authorities(catalogue=catalogue, inventory=inventory, limit=limit, excluded=excluded,
                                              inventory_mode=inventory_mode)
    selections = [{"qid": authority.source_id, "status": "selected", "language": choose_sitelink(authority)[0], "title": choose_sitelink(authority)[1]} for authority in authorities]
    return {
        "version": "scale20k-content-acquisition-preflight-v1", "status": "dry_run_only",
        "created_at": now.isoformat(), "limit": limit, "selected_qids": [a.source_id for a in authorities],
        "selection_counts": counts, "excluded_qids": len(excluded), "selections": selections,
        "source_route": ("current Wikidata authority -> exact Wikipedia sitelink -> pageprops/revision screened prose"
                         if inventory_mode == "known-genre" else
                         "current display Wikidata authority (explicit novel/light_novel, source genre unknown) -> exact supported Wikipedia sitelink -> pageprops/revision screened prose"),
        "inventory_mode": inventory_mode,
        "rights": rights, "http_policy": {"max_attempts": 1, "min_interval_seconds": 1.1, "batch_titles": 50, "shared_raw_store": _rel(SHARED_RAW_STORE)},
        "output": _rel(output), "model_calls": 0, "network_requests": 0,
        "retry_policy": "selected/started/held/failed/no-sitelink QIDs remain excluded globally; no same-page retry",
    }


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def write_preflight(document: dict[str, Any], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=False, indent=2); stream.write("\n")


def _write_json_createonly(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_jsonl_createonly(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _content_qid(record: Any) -> str:
    identifiers = getattr(record, "identifiers", [])
    qids = {item.value for item in identifiers if item.source == "wikidata" and item.scope == "work"}
    if len(qids) != 1:
        raise ValueError("content record must bind exactly one Wikidata work QID")
    return next(iter(qids))


def build_native_records(*, content_paths: list[Path], catalogue: Path,
                         output_dir: Path, limit: int = 100, allowed_qids: set[str] | None = None) -> dict[str, Any]:
    """Project saved Wikipedia records onto current WD authorities.

    This reads only the selected content rows and exact QID joins.  It never
    scans the catalogue and preserves the authority raw/version pin and the
    content evidence/ledger in each enriched WD SourceRecord.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    from recommend_book.models import SourceRecord as ModelSourceRecord
    candidates: list[tuple[str, Any, dict[str, Any] | None]] = []
    seen: set[str] = set()
    for records_path in content_paths:
        path = Path(records_path).resolve(strict=True)
        pins_path = path.with_name("source-pins.jsonl")
        pins = {}
        if pins_path.exists():
            for line in pins_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    pin = json.loads(line)
                    if isinstance(pin, dict) and isinstance(pin.get("qid"), str):
                        pins[pin["qid"]] = pin
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = ModelSourceRecord.model_validate_json(line)
            qid = _content_qid(record)
            if allowed_qids is not None and qid not in allowed_qids:
                continue
            if qid in seen:
                continue
            seen.add(qid)
            candidates.append((qid, record, pins.get(qid)))
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break
    if not candidates:
        raise ValueError("no saved supported content records available")
    qids = [qid for qid, _, _ in candidates]
    enriched: list[dict[str, Any]] = []
    pins_out: list[dict[str, Any]] = []
    db = sqlite3.connect(Path(catalogue).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON"); db.execute("BEGIN")
        placeholders = ",".join("?" for _ in qids)
        rows = {row[0]: row for row in db.execute(
            f"SELECT w.qid,w.version_id,s.record_sha256,s.record_json,s.raw_path,s.raw_sha256 FROM works w JOIN source_versions s ON s.version_id=w.version_id WHERE w.qid IN ({placeholders})", qids)}
        for qid, content, pin in candidates:
            hit = rows.get(qid)
            if hit is None:
                raise ValueError(f"current authority missing for {qid}")
            _, version_id, authority_sha, authority_payload, authority_raw_path, authority_raw_sha = hit
            if hashlib.sha256(authority_payload.encode("utf-8")).hexdigest() != authority_sha:
                raise ValueError(f"current authority record SHA mismatch for {qid}")
            authority = ModelSourceRecord.model_validate_json(authority_payload)
            if authority.source != "wikidata" or authority.source_id != qid or authority.raw_path != authority_raw_path or authority.raw_sha256 != authority_raw_sha:
                raise ValueError(f"current authority identity/raw pin mismatch for {qid}")
            content_evidence = list(content.evidence)
            extra = {**authority.extra, "scale_content_supported_v1": {
                "content_record_source_id": content.source_id,
                "content_raw_path": content.raw_path,
                "content_raw_sha256": content.raw_sha256,
                "content_source_url": content.source_url,
                "content_evidence_ids": [item.evidence_id for item in content_evidence],
                "content_ledger": (pin or {}).get("ledger"),
                "authority_version_id": version_id,
                "authority_record_sha256": authority_sha,
                "authority_raw_path": authority_raw_path,
                "authority_raw_sha256": authority_raw_sha,
                "recipe": "screened-paragraphs-v1",
            }}
            projected = authority.model_copy(update={"evidence": [*authority.evidence, *content_evidence], "extra": extra}, deep=True)
            enriched.append(projected.model_dump(mode="json"))
            pins_out.append({"qid": qid, "authority_version_id": version_id, "authority_record_sha256": authority_sha,
                             "authority_raw_path": authority_raw_path, "authority_raw_sha256": authority_raw_sha,
                             "content_record_source_id": content.source_id, "content_raw_path": content.raw_path,
                             "content_raw_sha256": content.raw_sha256, "content_evidence_ids": [item.evidence_id for item in content_evidence],
                             "content_ledger": (pin or {}).get("ledger")})
    finally:
        db.close()
    output_dir = Path(output_dir)
    _write_jsonl_createonly(output_dir / "native-records.jsonl", enriched)
    _write_jsonl_createonly(output_dir / "native-source-pins.jsonl", pins_out)
    handoff = {"version": "scale20k-native-content-handoff-v1", "selected": len(enriched),
               "records_path": _rel(output_dir / "native-records.jsonl"),
               "records_sha256": sha(output_dir / "native-records.jsonl"),
               "pins_path": _rel(output_dir / "native-source-pins.jsonl"),
               "pins_sha256": sha(output_dir / "native-source-pins.jsonl"),
               "model_calls": 0, "web_requests": 0, "catalogue_writes": 0,
               "authority_binding": "current exact QID/version_id/record_sha/raw_sha",
               "native_guard_required_before_model": True}
    _write_json_createonly(output_dir / "native-handoff.json", handoff)
    return handoff


def prepare_native_handoff(*, native_records: Path, catalogue: Path, native_output: Path,
                           conditions: Path, window_index: int, run_name: str) -> dict[str, Any]:
    """Call the existing canonical native SourceRecord preparation API."""
    if not 1 <= sum(1 for line in Path(native_records).read_text(encoding="utf-8").splitlines() if line.strip()) <= 100:
        raise ValueError("native handoff must contain 1..100 records")
    import importlib.util
    helper_path = ROOT / "scripts" / "prepare_scale_content_sources_v1.py"
    spec = importlib.util.spec_from_file_location("prepare_scale_content_sources_v1", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical native preparation helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.prepare_native_window(native_records, catalogue, native_output, conditions,
                                          window_index=window_index, run_name=run_name)
    manifest = Path(result["source_preparation"]) / "manifest.json"
    result = {**result, "manifest_path": str(manifest.resolve()), "manifest_sha256": sha(manifest),
              "model_calls": 0, "web_requests": 0}
    return result


def execute_window(*, catalogue: Path = CATALOGUE, inventory: Path = INVENTORY,
                   conditions: Path = CONDITIONS, output: Path = DEFAULT_OUTPUT,
                   limit: int = 100, now: datetime | None = None,
                   inventory_mode: str = "known-genre") -> dict[str, Any]:
    """Acquire one bounded window using the established one-attempt route.

    Selection is sealed before the first HTTP request.  A terminal 403/429 is
    recorded and stops the window; rerunning the same output is forbidden.
    """
    if output.exists():
        raise FileExistsError(f"v20 acquisition output already exists: {output}")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    now = now or datetime.now(timezone.utc)
    rights = _rights_gate(conditions, now)
    excluded = _old_attempts()
    no_sitelink_qids: set[str] = set()
    selected_sitelinks: dict[str, tuple[str, str]] = {}
    authorities, counts = select_authorities(catalogue=catalogue, inventory=inventory, limit=limit, excluded=excluded,
                                              no_sitelink_qids=no_sitelink_qids, selected_sitelinks=selected_sitelinks,
                                              inventory_mode=inventory_mode)
    output.mkdir(parents=True, exist_ok=False)
    selections = [{"qid": qid, "status": "held", "reason": "no_supported_sitelink"} for qid in no_sitelink_qids]
    groups: dict[str, list[tuple[str, Any]]] = {}
    for authority in authorities:
        selected = selected_sitelinks[authority.source_id]
        if selected is None:
            selections.append({"qid": authority.source_id, "status": "held", "reason": "no_supported_sitelink"})
            continue
        language, title = selected
        selections.append({"qid": authority.source_id, "language": language, "title": title, "status": "selected"})
        groups.setdefault(language, []).append((title, authority))
    selection_path = output / "selections.json"
    _write_json_createonly(selection_path, selections)
    # Reuse the v18 shared gate/raw objects.  A new v20 path would refetch old
    # pages and defeat the global no-duplicate acquisition ledger.
    from recommend_book.ingestion.core import HTTPFetcher, RawStore
    from recommend_book.wikipedia_paragraphs import build_evidence
    from recommend_book.models import SourceRecord as ModelSourceRecord
    shared_store = RawStore(SHARED_RAW_STORE)
    fetcher = HTTPFetcher(shared_store, min_interval=1.1, max_attempts=1,
                          user_agent="recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)")
    records: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    network_requests = 0
    terminal_stop: dict[str, Any] | None = None
    for language, candidates in groups.items():
        for start in range(0, len(candidates), 50):
            batch = candidates[start:start + 50]
            response = fetcher.get(f"https://{language}.wikipedia.org/w/api.php", source="wikipedia", params={
                "action": "query", "titles": "|".join(title for title, _ in batch),
                "prop": "pageprops|revisions", "ppprop": "wikibase_item", "rvprop": "ids|timestamp|content",
                "rvslots": "main", "format": "json", "formatversion": 2, "maxlag": 5})
            network_requests += response.attempts
            if response.status != "success":
                status_code = response.status_code
                reason = str(response.error)
                outcomes.extend({"qid": authority.source_id, "status": "held", "reason": reason, "status_code": status_code} for _, authority in batch)
                if status_code in {403, 429}:
                    terminal_stop = {"status_code": status_code, "reason": reason, "retry_after_preserved": True}
                    break
                continue
            pages = json.loads(response.content).get("query", {}).get("pages", [])
            by_qid = {page.get("pageprops", {}).get("wikibase_item"): page for page in pages}
            for title, authority in batch:
                page = by_qid.get(authority.source_id)
                if not page or not page.get("revisions"):
                    outcomes.append({"qid": authority.source_id, "status": "held", "reason": "missing_exact_page_or_revision"})
                    continue
                try:
                    evidence, ledger, sections = build_evidence(authority, page, response.artifact, language)
                    narrative = [e for e in evidence if e.kind in ("synopsis", "excerpt")]
                    if not narrative:
                        outcomes.append({"qid": authority.source_id, "status": "held", "reason": "no_screened_narrative", "sections": sections})
                        continue
                    revision = page["revisions"][0]
                    revid = revision["revid"]
                    url = f"https://{language}.wikipedia.org/w/index.php?oldid={revid}"
                    extra = {"revision_id": revid, "revision_timestamp": revision.get("timestamp"),
                             "page_id": page["pageid"], "article_title": page["title"], "article_language": language,
                             "wikibase_item_verified": authority.source_id,
                             "imported_bibliography_from": {"source_id": authority.source_id, "raw_sha256": authority.raw_sha256, "raw_path": authority.raw_path},
                             "human_reviewed": False, "text_license": "CC-BY-SA-4.0",
                             "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                             "attribution": {"credit": f"Wikipedia contributors: {page['title']}", "permalink": url,
                                             "changes": "selected complete screened paragraphs; rejected paragraphs omitted; references removed; wiki markup converted to plain text"},
                             "license_scope": "article prose and adaptations, not CC0; quoted/imported/fair-use content excluded"}
                    record = ModelSourceRecord(source="wikipedia", source_id=f"{language}:{revid}:screened-paragraphs-v1",
                        source_url=url, retrieved_at=response.artifact.retrieved_at, provider_id="wikipedia", record_kind="work",
                        titles=[*authority.titles], contributors=authority.contributors,
                        identifiers=[{"source": "wikidata", "value": authority.source_id, "scope": "work"}],
                        original_language=authority.original_language, first_publication_year=authority.first_publication_year,
                        work_kind=authority.work_kind, region=authority.region, series=authority.series, volume=authority.volume,
                        evidence=evidence, raw_sha256=response.artifact.sha256, raw_path=response.artifact.path,
                        rights_policy_id="wikipedia-screened-prose", extra=extra)
                    records.append(record.model_dump(mode="json"))
                    pins.append({"qid": authority.source_id, "work_id": "wd:" + authority.source_id,
                                 "authority": {"source_id": authority.source_id, "raw_path": authority.raw_path, "raw_sha256": authority.raw_sha256},
                                 "content": {"raw_path": response.artifact.path, "raw_sha256": response.artifact.sha256, "source_url": url, "source_id": record.source_id},
                                 "evidence_ids": [e.evidence_id for e in evidence], "ledger": ledger})
                    outcomes.append({"qid": authority.source_id, "status": "accepted", "evidence_count": len(evidence), "narrative_count": len(narrative), "content_raw_sha256": response.artifact.sha256})
                except (ValueError, KeyError, TypeError) as exc:
                    outcomes.append({"qid": authority.source_id, "status": "held", "reason": str(exc)})
            if terminal_stop:
                break
        if terminal_stop:
            break
    _write_jsonl_createonly(output / "records.jsonl", records)
    _write_jsonl_createonly(output / "source-pins.jsonl", pins)
    _write_json_createonly(output / "outcomes.json", outcomes)
    manifest = {"version": "scale20k-content-acquisition-window-v1", "created_at": now.isoformat(),
                "status": "terminal_hold" if terminal_stop else "complete", "requested": limit,
                "selected_authorities": len(authorities), "accepted_records": len(records),
                "persisted_no_sitelink": len(no_sitelink_qids),
                "status_counts": dict(Counter(x["status"] for x in outcomes)), "network_requests": network_requests,
                "model_calls": 0, "catalogue_path": str(catalogue.resolve()),
                "selection_path": str(selection_path.resolve()), "selection_sha256": sha(selection_path),
                "shared_raw_store": str(shared_store.root), "http_max_attempts": 1,
                "rights": rights, "inventory_mode": inventory_mode, "terminal_stop": terminal_stop, "old_sources_modified": False}
    _write_json_createonly(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--conditions", type=Path, default=CONDITIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--inventory-mode", choices=("known-genre", "unknown-source-genre-content"), default="known-genre")
    parser.add_argument("--execute-http", action="store_true", help="execute one bounded, preflighted window")
    parser.add_argument("--build-native-records", action="store_true", help="project saved content rows onto current WD authorities")
    parser.add_argument("--content-path", action="append", type=Path, default=[])
    parser.add_argument("--native-output-dir", type=Path)
    parser.add_argument("--allowed-qids-file", type=Path)
    parser.add_argument("--prepare-native", action="store_true", help="run canonical native source preparation")
    parser.add_argument("--native-records", type=Path)
    parser.add_argument("--native-window-index", type=int)
    parser.add_argument("--native-run-name")
    args = parser.parse_args()
    if args.build_native_records:
        if not args.content_path or args.native_output_dir is None:
            parser.error("--build-native-records requires --content-path and --native-output-dir")
        allowed = None
        if args.allowed_qids_file:
            value = _json(args.allowed_qids_file)
            allowed_values = value.get("qids", []) if isinstance(value, dict) else value if isinstance(value, list) else []
            allowed = {item for item in allowed_values if isinstance(item, str)}
        print(json.dumps(build_native_records(content_paths=args.content_path, catalogue=args.catalogue,
                                              output_dir=args.native_output_dir, limit=args.limit,
                                              allowed_qids=allowed), ensure_ascii=False))
        return
    if args.prepare_native:
        if args.native_records is None or args.native_output_dir is None or args.native_window_index is None or args.native_run_name is None:
            parser.error("--prepare-native requires native records/output/window index/run name")
        print(json.dumps(prepare_native_handoff(native_records=args.native_records, catalogue=args.catalogue,
                                                native_output=args.native_output_dir, conditions=args.conditions,
                                                window_index=args.native_window_index, run_name=args.native_run_name), ensure_ascii=False))
        return
    kwargs = {key: value for key, value in vars(args).items() if key not in {"execute_http", "build_native_records", "content_path", "native_output_dir", "allowed_qids_file", "prepare_native", "native_records", "native_window_index", "native_run_name"}}
    if args.execute_http:
        document = execute_window(**kwargs)
        target = args.output / "manifest.json"
    else:
        document = build_preflight(**kwargs)
        target = V20 / "acquisition-preflight-v1.json"
        write_preflight(document, target)
    print(json.dumps(document, ensure_ascii=False))


if __name__ == "__main__":
    main()
