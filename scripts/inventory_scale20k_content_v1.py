"""Create a bounded, read-only inventory for the v20 classification supply.

The inventory reads the current ``works``/``source_versions`` snapshot once and
keeps only compact source pins.  It never reads source bytes, calls a model or
network, and never mutates the catalogue or an earlier v19 artifact.  The
result is a new CREATEONLY v20 scope containing a summary and a JSONL list of
unprocessed, recommendation-eligible work IDs.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
V19_ROOT = ROOT / "data/autonomous-next-v3/scale-100000-v1"
OUTPUT_ROOT = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1/inventory-v1"
SUPPLY_NAME = "eligible-supply-v1.jsonl"
SUMMARY_NAME = "inventory-v1.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    return json.loads(Path(path).read_bytes())


def _repo_path(path: str | Path) -> str:
    value = Path(path)
    try:
        return value.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(value)


def _load_old_exclusions(v19_root: Path) -> tuple[set[str], set[str], dict[str, int]]:
    """Load compact old exclusion/attempt indexes, never source payloads."""
    qids: set[str] = set()
    input_hashes: set[str] = set()
    counts = {"reservation_files": 0, "reserved_qids": 0, "reserved_input_hashes": 0}
    exclusion = v19_root / "native-exclusions-v1.json"
    if exclusion.exists():
        value = _json(exclusion)
        qids.update(x for x in value.get("qids", []) if isinstance(x, str))
        input_hashes.update(x for x in value.get("input_hashes", []) if isinstance(x, str))
    reservations = v19_root / "attempt-reservations"
    if reservations.exists():
        for path in sorted(reservations.glob("*.json")):
            value = _json(path)
            counts["reservation_files"] += 1
            rows = {x for x in value.get("qids", []) if isinstance(x, str)}
            hashes = {x for x in value.get("input_hashes", []) if isinstance(x, str)}
            counts["reserved_qids"] += len(rows)
            counts["reserved_input_hashes"] += len(hashes)
            qids.update(rows)
            input_hashes.update(hashes)
    return qids, input_hashes, counts


def _artifact_inventory(v19_root: Path) -> dict[str, int]:
    """Count known compact ledger artifacts without walking old raw/history trees."""
    result = collections.Counter()
    result["reservation_files"] = len(list((v19_root / "attempt-reservations").glob("*.json")))
    result["checkpoint_files"] = len(list((v19_root / "window-controls").glob("*checkpoint*.json")))
    result["failure_or_hold_receipts"] = len([
        p for p in (v19_root / "run-summaries").glob("*.json")
        if any(token in p.name.lower() for token in ("failure", "failed", "hold", "held"))
    ])
    source_dirs = (
        "classification-sources", "classification-sources-v2",
        "classification-sources-v2-prepared", "classification-sources-v3",
        "classification-sources-v8-shortfall", "classification-sources-v9",
    )
    result["manifest_files"] = sum(len(list((v19_root / name).glob("window-*/manifest.json"))) for name in source_dirs)
    result["selection_outcome_ledger_files"] = sum(
        len(list((v19_root / name).glob("window-*/*ledger*.json")))
        + len(list((v19_root / name).glob("window-*/*outcome*.json")))
        + len(list((v19_root / name).glob("window-*/*selection*.json")))
        for name in source_dirs
    )
    return dict(sorted(result.items()))


def _period(year: int | None) -> str:
    if year is None:
        return "unknown"
    if year < 1900:
        return "before-1900"
    if year <= 1945:
        return "1900-1945"
    if year <= 1989:
        return "1946-1989"
    if year <= 2009:
        return "1990-2009"
    if year <= 2019:
        return "2010-2019"
    return "2020-plus"


def _dimensions(labels: Iterable[str], language: str | None, year: int | None) -> list[str]:
    normalized = {str(label).casefold().strip() for label in labels if label}
    dimensions: list[str] = []
    if (language or "").casefold() in {"ja", "jpn", "ja-jp"}:
        dimensions.append("japanese")
    if any("mystery" in label or "detective" in label or label == "crime fiction" for label in normalized):
        dimensions.append("mystery")
    if any("fantasy" in label for label in normalized):
        dimensions.append("fantasy")
    if any(label in {"light novel", "ライトノベル"} or "light novel" in label for label in normalized):
        dimensions.append("light_novel")
    period = _period(year)
    if period != "unknown":
        dimensions.append(f"period:{period}")
    return dimensions


def _priority(dimensions: list[str]) -> int:
    weights = {"japanese": 5, "mystery": 4, "fantasy": 4, "light_novel": 4}
    return sum(weights.get(item, 1) for item in dimensions)


def _condition_summary(v19_root: Path, now: datetime) -> dict[str, Any]:
    candidates = sorted((v19_root / "acquisition").glob("source-conditions*.json"))
    rows: list[dict[str, Any]] = []
    for path in candidates:
        value = _json(path)
        retrieved = value.get("retrieved_at")
        age_days = None
        if isinstance(retrieved, str):
            try:
                age_days = round((now - datetime.fromisoformat(retrieved.replace("Z", "+00:00"))).total_seconds() / 86400, 3)
            except ValueError:
                pass
        rows.append({
            "path": _repo_path(path),
            "sha256": _sha(path),
            "version": value.get("version"),
            "source": value.get("source"),
            "policy_id": value.get("policy_id"),
            "retrieved_at": retrieved,
            "age_days_at_inventory": age_days,
            "usages": value.get("usages", {}),
            "metadataonly_scope": value.get("metadataonly_scope"),
            "scope": value.get("scope"),
        })
    rows.sort(key=lambda row: row.get("retrieved_at") or "", reverse=True)
    latest = rows[0] if rows else None
    return {
        "receipts": rows,
        "latest": latest,
        "earliest_legal_continuation": {
            "route": "existing-current-wikidata-structured-metadata",
            "condition_receipt": latest["path"] if latest else None,
            "allowed_purposes": latest.get("usages", {}) if latest else {},
            "scope": latest.get("scope") if latest else None,
            "next_action": "reuse current pinned SourceRecord metadata; refresh only before any new network/narrative route",
            "linked_text_inherited": False,
        },
    }


def _write_createonly(path: Path, payload: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(payload)


def build_inventory(*, db_path: Path = DB_PATH, v19_root: Path = V19_ROOT,
                    output_root: Path = OUTPUT_ROOT, now: datetime | None = None) -> dict[str, Any]:
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(f"v20 inventory output already exists: {output_root}")
    now = now or datetime.now(timezone.utc)
    old_qids, old_hashes, reservation_counts = _load_old_exclusions(Path(v19_root))
    artifact_counts = _artifact_inventory(Path(v19_root))
    labels_by_qid: dict[str, set[str]] = collections.defaultdict(set)
    native_qids: set[str] = set()
    rows: list[dict[str, Any]] = []
    all_counts = collections.Counter()
    eligible_counts = collections.Counter()
    all_language_counts = collections.Counter()
    eligible_language_counts = collections.Counter()
    condition_hash_counts = collections.Counter()
    source_counts = collections.Counter()
    raw_exists: dict[str, bool] = {}
    native_display_recommendable = 0

    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        native_qids.update(row[0] for row in db.execute("SELECT qid FROM native_classifications"))
        for qid, label in db.execute("SELECT qid, label FROM work_genres"):
            if label:
                labels_by_qid[qid].add(label)
        query = """
            SELECT w.qid,w.version_id,w.title,w.language,w.year,w.recommendable,w.display_allowed,
                   s.record_sha256,s.raw_path,s.raw_sha256,s.retrieved_at,s.conditions_sha256,s.record_json
            FROM works AS w JOIN source_versions AS s ON s.version_id=w.version_id
            ORDER BY w.qid
        """
        for (qid, version_id, title, language, year, recommendable, display_allowed,
             record_sha, raw_path, raw_sha, retrieved_at, conditions_sha, record_raw) in db.execute(query):
            dims = _dimensions(labels_by_qid.get(qid, ()), language, year)
            language_key = (language or "unknown").casefold()
            all_language_counts[language_key] += 1
            period_key = f"period:{_period(year)}"
            if period_key not in dims:
                all_counts[period_key] += 1
            condition_hash_counts[conditions_sha] += 1
            all_counts["works"] += 1
            if recommendable:
                all_counts["recommendable"] += 1
            if display_allowed:
                all_counts["display_allowed"] += 1
            if qid in native_qids and recommendable and display_allowed:
                native_display_recommendable += 1
            for dim in dims:
                all_counts[dim] += 1
            record = json.loads(record_raw)
            evidence = record.get("evidence") or []
            source_counts[record.get("source") or "unknown"] += 1
            raw_path_str = str(raw_path)
            if raw_path_str not in raw_exists:
                raw_exists[raw_path_str] = Path(raw_path_str).is_file()
            identity_ok = record.get("source_id") == qid and record.get("source") == "wikidata"
            pin_ok = (
                record.get("raw_path") == raw_path_str
                and record.get("raw_sha256") == raw_sha
                and bool(record_sha) and bool(raw_sha) and raw_exists[raw_path_str]
            )
            allowed_evidence = [e for e in evidence if e.get("llm_input_allowed") is True]
            rights_ok = bool(record.get("rights_policy_id")) and bool(allowed_evidence)
            source_ok = identity_ok and pin_ok and rights_ok
            excluded = qid in native_qids or qid in old_qids
            if source_ok and not excluded:
                eligible_counts["source"] += 1
                if recommendable:
                    eligible_counts["recommendable"] += 1
                if display_allowed:
                    eligible_counts["display_allowed"] += 1
                for dim in dims:
                    eligible_counts[dim] += 1
                eligible_language_counts[language_key] += 1
                if period_key not in dims:
                    eligible_counts[period_key] += 1
                if recommendable:
                    rows.append({
                        "qid": qid, "title": title, "language": language, "year": year,
                        "period": _period(year), "recommendable": bool(recommendable),
                        "display_allowed": bool(display_allowed), "priority_dimensions": dims,
                        "priority_score": _priority(dims), "genre_labels": sorted(labels_by_qid.get(qid, ())),
                        "source_version_id": version_id, "record_sha256": record_sha,
                        "raw_path": _repo_path(raw_path_str), "raw_sha256": raw_sha,
                        "retrieved_at": retrieved_at, "conditions_sha256": conditions_sha,
                        "source": record.get("source"), "source_id": record.get("source_id"),
                        "rights_policy_id": record.get("rights_policy_id"),
                        "evidence_kinds": sorted({e.get("kind") for e in allowed_evidence if e.get("kind")}),
                        "evidence_count": len(allowed_evidence),
                        "eligibility_basis": ["current_source_version", "exact_source_id", "raw_pin_present", "llm_input_allowed"],
                        "content_scope": "metadata_only_structured_wikidata; linked_text_not_inherited",
                    })
    finally:
        db.close()

    rows.sort(key=lambda row: (-row["priority_score"], row["qid"]))
    recommendation_counts = collections.Counter(source=len(rows), recommendable=len(rows))
    recommendation_languages = collections.Counter()
    for row in rows:
        recommendation_counts.update(row["priority_dimensions"])
        period_key = f"period:{row['period']}"
        if period_key not in row["priority_dimensions"]:
            recommendation_counts[period_key] += 1
        recommendation_counts["display_allowed"] += int(row["display_allowed"])
        recommendation_languages[(row["language"] or "unknown").casefold()] += 1
    supply_raw = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)
    conditions = _condition_summary(Path(v19_root), now)
    output_root.mkdir(parents=True, exist_ok=False)
    supply_path = output_root / SUPPLY_NAME
    _write_createonly(supply_path, supply_raw)
    summary = {
        "version": "scale20k-content-inventory-v1",
        "observed_at": now.isoformat(),
        "scope": "v20 stage1 inventory; current catalogue/source pins only; no model/network/raw-byte scan",
        "catalogue": {
            "database_path": _repo_path(db_path), "works_observed": all_counts["works"],
            "plan_searchable_target": 100368, "recommendable_observed": all_counts["recommendable"],
            "native_classifications": len(native_qids), "native_display_recommendable": native_display_recommendable,
        },
        "exclusions": {
            "native_qids": len(native_qids), "old_exclusion_qids": len(old_qids),
            "old_exclusion_input_hashes": len(old_hashes), "union_excluded_qids": len(native_qids | old_qids),
            **reservation_counts,
        },
        "artifact_inventory": artifact_counts,
        "source_interface": {
            "current_join": "works.version_id = source_versions.version_id",
            "identity": "source_versions.record_json.source == wikidata and source_id == works.qid",
            "raw_binding": "source_versions.raw_path/raw_sha256 matched by SourceRecord pins; bytes not read in this stage",
            "rights_binding": "source_versions.conditions_sha256 plus source-conditions receipt",
            "source_counts": dict(sorted(source_counts.items())),
            "eligible_supply_path": _repo_path(supply_path),
        },
        "gap_counts": {
            "all_current_works": dict(sorted(all_counts.items())),
            "eligible_unprocessed_authorities": dict(sorted(eligible_counts.items())),
            "eligible_unprocessed_recommendable": dict(sorted(recommendation_counts.items())),
            "language_counts": {
                "all_current_works": dict(sorted(all_language_counts.items())),
                "eligible_unprocessed_authorities": dict(sorted(eligible_language_counts.items())),
                "eligible_unprocessed_recommendable": dict(sorted(recommendation_languages.items())),
            },
            "definition": "dimensions come only from works.language/year and stored work_genres labels; unknown is retained",
        },
        "available_supply": {
            "eligible_unprocessed_source_records": eligible_counts["source"],
            "eligible_unprocessed_recommendable": eligible_counts["recommendable"],
            "candidate_rows_written": len(rows), "priority_order": "Japanese, mystery, fantasy, light novel, known period, QID",
            "supply_sha256": hashlib.sha256(supply_raw).hexdigest(),
        },
        "rights": conditions,
        "current_source_condition_hashes": dict(sorted(condition_hash_counts.items())),
        "outputs": {"summary": _repo_path(output_root / SUMMARY_NAME), "supply": _repo_path(supply_path)},
    }
    _write_createonly(output_root / SUMMARY_NAME, (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return summary


def build_inventory_v2_from_v1(*, source_root: Path = OUTPUT_ROOT,
                               output_root: Path = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1/inventory-v2",
                               now: datetime | None = None) -> dict[str, Any]:
    """Correct recommendation-gap dimensions from the compact v1 candidate list.

    v1's authority dimensions were measured before the recommendation filter.
    This correction does not reopen SQLite or raw records: it streams the
    already sealed recommendation-eligible JSONL once and keeps v1 immutable.
    """
    source_root = Path(source_root)
    output_root = Path(output_root)
    if output_root.exists():
        raise FileExistsError(f"v2 inventory output already exists: {output_root}")
    summary_v1 = _json(source_root / SUMMARY_NAME)
    supply_path = source_root / SUPPLY_NAME
    rows = [json.loads(line) for line in supply_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    dims = collections.Counter()
    languages = collections.Counter()
    periods = collections.Counter()
    source_count = 0
    for row in rows:
        source_count += 1
        language_value = (row.get("language") or "unknown").casefold()
        language = language_value if language_value in {"ar", "bn", "de", "en", "es", "fr", "hi", "it", "ja", "ko", "pt", "ru", "zh"} else "unknown"
        languages[language] += 1
        period = f"period:{row.get('period') or 'unknown'}"
        periods[period] += 1
        for label in row.get("priority_dimensions", []):
            dims[label] += 1
    output_root.mkdir(parents=True, exist_ok=False)
    corrected_supply = output_root / "eligible-supply-v2.jsonl"
    _write_createonly(corrected_supply, supply_path.read_bytes())
    summary = {
        "version": "scale20k-content-inventory-v2",
        "observed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "scope": "correction from immutable v1 compact recommendable candidate export; no SQLite/raw/history reread",
        "source_v1": {"summary": _repo_path(source_root / SUMMARY_NAME), "summary_sha256": _sha(source_root / SUMMARY_NAME),
                      "supply": _repo_path(supply_path), "supply_sha256": _sha(supply_path)},
        "catalogue": summary_v1.get("catalogue", {}),
        "exclusions": summary_v1.get("exclusions", {}),
        "source_interface": summary_v1.get("source_interface", {}),
        "gap_counts": {
            "all_current_works": summary_v1.get("gap_counts", {}).get("all_current_works", {}),
            "eligible_unprocessed_authorities": summary_v1.get("available_supply", {}).get("eligible_unprocessed_source_records", 0),
            "eligible_unprocessed_recommendable": {"source": source_count, "recommendable": source_count, **dict(sorted(dims.items()))},
            "language_counts": {
                "all_current_works": summary_v1.get("gap_counts", {}).get("language_counts", {}).get("all_current_works", {}),
                "eligible_unprocessed_authorities": summary_v1.get("gap_counts", {}).get("language_counts", {}).get("eligible_unprocessed_authorities", summary_v1.get("gap_counts", {}).get("language_counts", {}).get("eligible_unprocessed_recommendable", {})),
                "eligible_unprocessed_recommendable": dict(sorted(languages.items())),
            },
            "period_counts": dict(sorted(periods.items())),
            "country_region": {"scope": "not present in v1 compact candidate rows; requires stored-region projection before counting unknown", "unassessed": source_count},
            "definition": "recommendable dimensions are counted only from rows in eligible-supply-v1.jsonl; unknown is retained",
        },
        "available_supply": {"eligible_unprocessed_source_records": summary_v1.get("available_supply", {}).get("eligible_unprocessed_source_records"),
                             "eligible_unprocessed_recommendable": source_count, "candidate_rows_written": source_count,
                             "supply_sha256": _sha(corrected_supply)},
        "rights": summary_v1.get("rights", {}),
        "outputs": {"summary": _repo_path(output_root / "inventory-v2.json"), "supply": _repo_path(corrected_supply)},
    }
    _write_createonly(output_root / "inventory-v2.json", (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    parser.add_argument("--v19-root", type=Path, default=V19_ROOT)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--correct-from-v1", action="store_true")
    parser.add_argument("--source-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args()
    if args.correct_from_v1:
        print(json.dumps(build_inventory_v2_from_v1(source_root=args.source_root, output_root=args.output_root), ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(build_inventory(db_path=args.db_path, v19_root=args.v19_root, output_root=args.output_root), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
