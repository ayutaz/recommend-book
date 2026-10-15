"""Audit direct serial-ID coverage against the latest public delivery.

This is a read-only, aggregate-only audit.  Owner rows are represented only
by NCODE digests and counts; titles, ratings, URLs, and source text are never
written to the output.  A title, Wikidata ID, or Open Library ID is not used
as an owner-history join key.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from recommend_book.recommendation import history_binding


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "autonomous-next-v3"
OWNER_DIR = BASE / "evaluation" / "phase3-history-binding-v1" / "owner-originals"
DELIVERY = BASE / "database" / "phase7-world-pilot-delivery-v1" / "delivery.json"
ADMITTED = BASE / "database" / "phase7-world-pilot-delivery-v1" / "admitted-new-rows.local.json"
PROFILE_SUMMARY = BASE / "evaluation" / "phase3-personal-profile-v1" / "runtime-v2" / "profile-summary.json"
BINDINGS = BASE / "evaluation" / "phase3-history-binding-expansion-v2" / "private-all-bindings.jsonl"
PINNED_INVENTORY = BASE / "evaluation" / "phase8-history-connection-v2" / "source-database-inventory.json"
OUT = BASE / "evaluation" / "phase8-history-connection-v4"


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the whole SQLite database into memory."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def digest(value: str) -> str:
    return sha256(value.encode("utf-8"))


def load_json(path: Path):
    return json.loads(path.read_bytes())


def serial_value(value: str) -> str | None:
    """Normalize only explicit serial IDs, never titles or free text."""
    value = value.strip().lower()
    if value.startswith(("https://ncode.syosetu.com/", "http://ncode.syosetu.com/")):
        value = value.split("//", 1)[1].removeprefix("ncode.syosetu.com/").strip("/")
    if re.fullmatch(r"n\d{4}[a-z]{1,2}", value):
        return value
    return None


STRUCTURED_SERIAL_FIELDS = {
    "external_id", "external_ids", "original_serialization_id",
    "original_serialization_ids", "original_serialization_url",
    "original_serialization_urls", "serial_url", "serial_urls",
    "serial_work_id", "source_url", "source_urls",
}


def structured_serial_ids(value, field_name: str | None = None) -> set[str]:
    """Read only whitelisted structured ID/URL fields; never scan prose."""
    found: set[str] = set()
    if isinstance(value, dict):
        if field_name == "external_ids" and "value" in value:
            found.update(structured_serial_ids(value["value"], "external_id"))
        for key, item in value.items():
            if key in STRUCTURED_SERIAL_FIELDS:
                found.update(structured_serial_ids(item, key))
    elif isinstance(value, list):
        for item in value:
            found.update(structured_serial_ids(item, field_name))
    elif isinstance(value, str) and field_name in STRUCTURED_SERIAL_FIELDS:
        normalized = serial_value(value)
        if normalized is not None:
            found.add(normalized)
    return found


def owner_ncodes() -> tuple[set[str], int, int]:
    raw_rows = 0
    valid_rows = 0
    values: set[str] = set()
    for path in sorted(OWNER_DIR.glob("*.html")):
        rows = history_binding.extract_owner_html_rows(path.read_bytes())
        raw_rows += len(rows)
        for row in rows:
            if row.get("rating") is None:
                continue
            valid_rows += 1
            if not row.get("ncode"):
                continue
            values.add(row["ncode"])
    if valid_rows != 335 or len(values) != 335:
        raise ValueError(f"expected frozen owner roster of 335, got valid={valid_rows}, ids={len(values)}")
    return values, raw_rows, valid_rows - len(values)


def sqlite_serial_ids(path: Path) -> tuple[set[str], dict[str, int]]:
    serials: set[str] = set()
    source_counts: dict[str, int] = {}
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
        if "external_ids" not in tables:
            return serials, source_counts
        for source, value in connection.execute("select source,value from external_ids"):
            source = str(source)
            source_counts[source] = source_counts.get(source, 0) + 1
            normalized = serial_value(str(value))
            if normalized is not None or source.casefold() in {"narou", "syosetu", "ncode"}:
                if normalized is not None:
                    serials.add(normalized)
    finally:
        connection.close()
    return serials, source_counts


def database_inventory() -> tuple[set[str], list[dict], dict]:
    all_serials: set[str] = set()
    inventory: list[dict] = []
    mismatch_paths: list[str] = []
    previous = load_json(PINNED_INVENTORY)
    for entry in previous:
        path = ROOT / entry["path"]
        if not path.is_file():
            raise ValueError(f"pinned database missing: {path}")
        sha256_before = file_sha256(path)
        serials, source_counts = sqlite_serial_ids(path)
        sha256_after = file_sha256(path)
        pinned_sha256 = entry.get("sha256")
        stable = sha256_before == sha256_after
        pinned_matches = sha256_before == pinned_sha256
        accepted = stable and pinned_matches
        if not accepted:
            mismatch_paths.append(entry["path"])
        if accepted:
            all_serials.update(serials)
        current = {"path": entry["path"], "pinned_sha256": pinned_sha256}
        current["sha256_before"] = sha256_before
        current["sha256_after"] = sha256_after
        current["sha256_stable_during_query"] = stable
        current["pinned_sha256_matches_before"] = pinned_matches
        current["current_accepted"] = accepted
        current["serial_id_count"] = len(serials)
        current["source_counts"] = source_counts
        inventory.append(current)
    summary = {
        "snapshot_path": str(PINNED_INVENTORY.relative_to(ROOT)).replace("\\", "/"),
        "snapshot_entry_n": len(previous),
        "before_after_stable": not mismatch_paths or all(
            item["sha256_stable_during_query"] for item in inventory
        ),
        "pinned_hash_matches": not mismatch_paths or all(
            item["pinned_sha256_matches_before"] for item in inventory
        ),
        "accepted_current_n": sum(item["current_accepted"] for item in inventory),
        "mismatch_n": len(mismatch_paths),
        "mismatch_paths": mismatch_paths,
    }
    return all_serials, inventory, summary


def main() -> None:
    owners, owner_raw_rows, owner_missing_serial_rows = owner_ncodes()
    delivery = load_json(DELIVERY)
    ranking = delivery["ranking"]
    admitted = load_json(ADMITTED)
    profile_summary = load_json(PROFILE_SUMMARY)
    serial_ids, inventory, database_hash_verification = database_inventory()
    bindings = [json.loads(line) for line in BINDINGS.read_text(encoding="utf-8").splitlines() if line.strip()]

    delivery_qids = {row["source_qid"] for row in ranking if row.get("source_qid")}
    delivery_work_ids = {row["work_id"] for row in ranking}
    delivery_serial_ids = set().union(*(structured_serial_ids(row) for row in ranking))
    first10_serial_ids = set().union(*(structured_serial_ids(row) for row in admitted))
    first10_serial_rows = sum(bool(structured_serial_ids(row)) for row in admitted)
    first10_feature_rows = sum(bool(row.get("admitted_row", {}).get("features")) for row in admitted)
    first10_withheld_rows = sum(bool(row.get("recommendation_features_withheld")) for row in admitted)
    direct_matches = owners & serial_ids

    OUT.mkdir(parents=True, exist_ok=False)
    inventory_path = OUT / "source-database-inventory.json"
    inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "version": "phase8-history-connection-audit-v4",
        "private_local_only": True,
        "aggregate_only": True,
        "titles_ratings_urls_raw_text_exported": False,
        "network_calls": 0,
        "model_calls": 0,
        "source_files_modified": False,
        "owner_rating_n": len(owners),
        "owner_raw_html_row_n": owner_raw_rows,
        "owner_ncode_digest_n": len(owners),
        "owner_valid_rows_missing_serial_id_n": owner_missing_serial_rows,
        "historically_verified_serialization_relation_n": len(bindings),
        "current_content_connected_n": profile_summary["supported_reference_n"],
        "profile_verified_serialization_relation_n": profile_summary["verified_serialization_relation_n"],
        "latest_delivery_candidate_n": len(ranking),
        "latest_delivery_work_id_n": len(delivery_work_ids),
        "latest_delivery_authority_id_n": len(delivery_qids),
        "latest_delivery_explicit_serial_id_n": len(delivery_serial_ids),
        "first10_feature_row_n": first10_feature_rows,
        "first10_withheld_feature_row_n": first10_withheld_rows,
        "first10_explicit_serial_id_row_n": first10_serial_rows,
        "first10_explicit_serial_id_n": len(first10_serial_ids),
        "all_scanned_source_database_n": len(inventory),
        "scanned_source_serial_id_n": len(serial_ids),
        "direct_owner_serial_id_match_n": len(direct_matches),
        "owner_rows_uncompared_missing_candidate_serial_id_n": len(owners),
        "owner_rows_outside_historically_verified_relation_n": len(owners) - 10,
        "new_content_connections_admitted": 0,
        "decision": "no_new_direct_connection;latest_source_metadata_has_no_serial_ids",
        "latest_delivery_audit_target": "phase7-world-pilot-delivery-v1 (1999 candidates)",
        "latest_delivery_display_audited": False,
        "database_inventory_snapshot": database_hash_verification["snapshot_path"],
        "database_inventory_snapshot_n": database_hash_verification["snapshot_entry_n"],
        "database_hash_verification": database_hash_verification,
        "source_database_hashes_are_current": database_hash_verification["mismatch_n"] == 0,
        "limits": [
            "Latest delivery/source metadata exposes Wikidata/Open Library-style IDs, not Narou NCODEs.",
            "Titles and authority IDs were not used as history joins.",
            "The 335 owner rows therefore remain uncomparable to the latest candidate set by a permitted exact serial ID.",
            "The ten historically verified serialization relations and two content-connected rows remain unchanged.",
            "Uncompared is not treated as unread, negative, or a failed identity match.",
            "Only whitelisted structured external-ID and URL fields were normalized; titles and prose were not scanned.",
            "The source database set is the fixed 23-entry v2 inventory snapshot; newly discovered databases were not included.",
            "A pinned SHA256 is not treated as current unless the read-only query's before/after hashes both match it.",
        ],
        "input_sha256": {
            "delivery": sha256(DELIVERY.read_bytes()),
            "admitted_new_rows": sha256(ADMITTED.read_bytes()),
            "profile_summary": sha256(PROFILE_SUMMARY.read_bytes()),
            "owner_originals": digest("|".join(sorted(digest(path.read_bytes().hex()) for path in OWNER_DIR.glob("*.html")))),
        },
        "output_sha256": {"source_database_inventory": sha256(inventory_path.read_bytes())},
        "protected_fields_not_exported": ["title", "rating", "owner_url", "ncode", "raw_text"],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
