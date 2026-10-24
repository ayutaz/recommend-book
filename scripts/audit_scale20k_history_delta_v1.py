"""Bounded, read-only audit of new native history connections for v20.

The audit compares only the native QIDs added after the frozen 10,000-row
native baseline.  It deliberately does not infer a connection from titles,
authors, prose, editions, translations, volumes or series.  A successful
structural match is not a completed recommendation evaluation: the latter
also needs an admitted content feature and a common eligible comparison pool.

The production CLI is intended to be run once, after the v20 native target is
reached.  Tests use a tiny SQLite fixture and never touch the production DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable

from recommend_book.models import SourceRecord

try:  # Keep the existing pure, structured-only helpers as the single policy.
    from scripts.audit_history_connection_v2 import serial_value
except ImportError:  # pragma: no cover - supports direct script execution.
    _HELPER_PATH = Path(__file__).with_name("audit_history_connection_v2.py")
    _HELPER_SPEC = importlib.util.spec_from_file_location("audit_history_connection_v2", _HELPER_PATH)
    if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
        raise ImportError("existing structured serial helper is unavailable")
    _HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
    _HELPER_SPEC.loader.exec_module(_HELPER)
    serial_value = _HELPER.serial_value


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "autonomous-next-v3" / "scale-20000-classifications-v1"
DEFAULT_DATABASE = ROOT / "data" / "autonomous-next-v3" / "scale-v1" / "catalogue.sqlite"
DEFAULT_BASELINE = BASE / "baseline-native-10000.jsonl"
FROZEN_BASELINE_SHA256 = "5734eda48467012f4c257a57027e655cb829ba309730a5dc2cce2a01c47ddf36"
DEFAULT_OWNER_COUNT = 335
DEFAULT_EXISTING_SERIAL_RELATIONS = 10
DEFAULT_EXISTING_CONTENT_LINKS = 2
DEFAULT_NATIVE_TARGET = 20000
_QID = re.compile(r"Q[1-9][0-9]*$")


class AuditInputError(ValueError):
    """A frozen or structured input cannot be safely audited."""


class NativeRow:
    __slots__ = ("qid", "version_id", "display_allowed", "record", "provenance")

    def __init__(self, qid: str, version_id: str, display_allowed: bool,
                 record: dict[str, Any], provenance: dict[str, Any]):
        self.qid = qid
        self.version_id = version_id
        self.display_allowed = display_allowed
        self.record = record
        self.provenance = provenance


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_object(raw: str | bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AuditInputError(f"{label} is not JSON") from exc
    if not isinstance(value, dict):
        raise AuditInputError(f"{label} is not an object")
    return value


def load_frozen_baseline(path: Path = DEFAULT_BASELINE, *, expected_sha256: str | None = None) -> set[str]:
    """Load only baseline QIDs after checking its frozen byte hash."""
    path = Path(path).resolve(strict=True)
    expected = FROZEN_BASELINE_SHA256 if expected_sha256 is None and path == DEFAULT_BASELINE.resolve() else expected_sha256
    observed = file_sha256(path)
    if expected is not None and observed != expected:
        raise AuditInputError("frozen native baseline SHA256 differs")
    qids: set[str] = set()
    for line in path.read_bytes().splitlines():
        if not line.strip():
            continue
        row = _json_object(line, "baseline row")
        qid = row.get("qid")
        if not isinstance(qid, str) or not _QID.fullmatch(qid) or qid in qids:
            raise AuditInputError("baseline contains duplicate or invalid QID")
        qids.add(qid)
    if not qids:
        raise AuditInputError("frozen native baseline is empty")
    return qids


def _sqlite_uri(path: Path) -> str:
    # mode=ro permits SQLite's normal WAL handling.  The explicit transaction
    # below supplies one consistent snapshot without bypassing WAL.
    return path.resolve(strict=True).as_uri() + "?mode=ro"


def read_native_delta(database: Path, baseline: set[str], *, minimum_current_native: int | None = None):
    """Read only the new native/source rows in one read-only transaction.

    The first query obtains identities and display flags only.  SourceRecord and
    native provenance JSON are decoded only for QIDs outside the frozen
    baseline, so a final run never replays 10,000 historical source records.
    """
    database = Path(database).resolve(strict=True)
    try:
        connection = sqlite3.connect(_sqlite_uri(database), uri=True)
    except (OSError, sqlite3.Error) as exc:
        raise AuditInputError("cannot open catalogue in read-only mode") from exc
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"works", "source_versions", "native_classifications"}
        if not required <= tables:
            raise AuditInputError("catalogue is missing the required native/source tables")
        identity_rows = connection.execute(
            """
            SELECT n.qid, w.display_allowed
            FROM native_classifications AS n
            LEFT JOIN works AS w ON w.qid = n.qid
            ORDER BY n.qid
            """
        ).fetchall()
        native_qids = {row[0] for row in identity_rows}
        if any(row[1] is None for row in identity_rows):
            raise AuditInputError("native row lacks a current works join")
        if len(native_qids) != len(identity_rows):
            raise AuditInputError("native identity listing contains duplicate QIDs")
        missing = baseline - native_qids
        if missing:
            raise AuditInputError("frozen native baseline row is missing from current native snapshot")
        if minimum_current_native is not None and len(native_qids) < minimum_current_native:
            raise AuditInputError(
                f"native target not reached: expected at least {minimum_current_native}, got {len(native_qids)}"
            )
        current_display = {row[0] for row in identity_rows if bool(row[1])}
        new_all_qids = native_qids - baseline
        new_display_qids = current_display - baseline
        if not new_all_qids:
            return {}, native_qids, current_display, new_all_qids, new_display_qids
        placeholders = ",".join("?" for _ in new_all_qids)
        rows = connection.execute(
            f"""
            SELECT n.qid, w.version_id, w.display_allowed,
                   s.record_json, s.record_sha256, n.provenance_json
            FROM native_classifications AS n
            JOIN works AS w ON w.qid = n.qid
            JOIN source_versions AS s
              ON s.qid = w.qid AND s.version_id = w.version_id
            WHERE n.qid IN ({placeholders})
            ORDER BY n.qid
            """, tuple(sorted(new_all_qids))
        ).fetchall()
        joined_qids = {row[0] for row in rows}
        if new_all_qids != joined_qids:
            missing = sorted(new_all_qids - joined_qids)
            raise AuditInputError("native row lacks a current works/source_versions join: " + ",".join(missing[:3]))
        result: dict[str, NativeRow] = {}
        for qid, version_id, display_allowed, record_json, record_sha256, provenance_json in rows:
            if not isinstance(qid, str) or not _QID.fullmatch(qid):
                raise AuditInputError("native row has an invalid public QID")
            if qid in result:
                raise AuditInputError("native/source join contains duplicate QID")
            record = _json_object(record_json, f"source record {qid}")
            if not isinstance(record_json, str) or hashlib.sha256(record_json.encode("utf-8")).hexdigest() != record_sha256:
                raise AuditInputError(f"source record {qid} record_sha256 pin differs")
            try:
                # This checks SourceRecord.identifiers against the real schema;
                # no free-text field is inspected for an identity.
                SourceRecord.model_validate(record)
            except Exception as exc:  # pydantic version-independent boundary
                raise AuditInputError(f"source record {qid} does not satisfy SourceRecord") from exc
            if record.get("record_kind") != "work":
                raise AuditInputError(f"native source record {qid} is not work-scoped")
            if ("wikidata", qid) not in _record_work_ids(record):
                raise AuditInputError(f"native source record {qid} lacks its scoped Wikidata work ID")
            provenance = _json_object(provenance_json, f"native provenance {qid}")
            result[qid] = NativeRow(qid, str(version_id), bool(display_allowed), record, provenance)
        return result, native_qids, current_display, new_all_qids, new_display_qids
    except (sqlite3.Error, OSError) as exc:
        raise AuditInputError("read-only native/source query failed") from exc
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def _scoped_identifiers(value: Any) -> set[tuple[str, str]]:
    """Extract only explicit work-scoped provider identifiers."""
    if not isinstance(value, list):
        return set()
    result: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or item.get("scope") != "work":
            continue
        source, identifier = item.get("source"), item.get("value")
        if isinstance(source, str) and source and isinstance(identifier, str) and identifier:
            result.add((source.casefold(), identifier))
    return result


def _owner_work_ids(owner: dict[str, Any]) -> set[tuple[str, str]]:
    values: set[tuple[str, str]] = set()
    for key in ("provider_work_ids", "identifiers", "external_ids"):
        values.update(_scoped_identifiers(owner.get(key)))
    return values


def _record_work_ids(record: dict[str, Any]) -> set[tuple[str, str]]:
    # SourceRecord.identifiers is the only authoritative provider-ID field.
    return _scoped_identifiers(record.get("identifiers"))


def _record_serial_ids(record: dict[str, Any]) -> set[str]:
    """Return explicit work-scoped serial identifiers, never prose matches."""
    result: set[str] = set()
    for item in record.get("identifiers", []):
        if not isinstance(item, dict) or item.get("scope") != "work":
            continue
        if str(item.get("source", "")).casefold() not in {"narou", "syosetu", "ncode"}:
            continue
        value = item.get("value")
        if isinstance(value, str) and (normalized := serial_value(value)):
            result.add(normalized)
    return result


def _owner_serial_id(owner: dict[str, Any]) -> str:
    value = owner.get("ncode")
    if not isinstance(value, str) or serial_value(value) is None:
        raise AuditInputError("owner row has no exact serialization ID")
    return serial_value(value)  # type: ignore[return-value]


def _valid_saved_receipts(owner: dict[str, Any], qid: str, owner_serial: str,
                          cache: dict[tuple[str, str, str, str], str], receipt_verifier=None) -> str:
    """Return verified, held or none for a saved receipt.

    A receipt's own JSON and hash establish integrity only.  It becomes a
    same-work relation only when the caller dispatches it to an existing
    genuine verifier.  Without that dispatch it remains a held candidate.
    """
    values = owner.get("same_work_receipts", owner.get("relation_receipts", []))
    if not isinstance(values, list):
        return "none"
    held = False
    for receipt in values:
        if not isinstance(receipt, dict):
            continue
        if (receipt.get("catalogue_qid") == qid
                and receipt.get("owner_serial_id") == owner_serial
                and receipt.get("relation") == "same_work"
                and receipt.get("scope") == "work"
                and receipt.get("verified") is True
                and isinstance(receipt.get("receipt_path"), str)
                and isinstance(receipt.get("receipt_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", receipt["receipt_sha256"])):
            path = Path(receipt["receipt_path"])
            if not path.is_absolute():
                continue
            try:
                resolved = str(path.resolve(strict=True))
                key = (resolved, receipt["receipt_sha256"], owner_serial, qid)
                if key not in cache:
                    raw = Path(resolved).read_bytes()
                    payload = _json_object(raw, "saved relation receipt")
                    valid = (hashlib.sha256(raw).hexdigest() == receipt["receipt_sha256"]
                             and payload.get("owner_serial_id") == owner_serial
                             and payload.get("catalogue_qid") == qid
                             and payload.get("relation") == "same_work"
                             and payload.get("scope") == "work"
                             and payload.get("verified") is True)
                    if not valid:
                        cache[key] = "none"
                    elif receipt_verifier is None:
                        cache[key] = "held"
                    else:
                        cache[key] = "verified" if receipt_verifier(receipt, payload, raw) is True else "held"
                if cache[key] == "verified":
                    return "verified"
                held |= cache[key] == "held"
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                cache[(str(path), receipt["receipt_sha256"], owner_serial, qid)] = "none"
    return "held" if held else "none"


def _owner_digest(owner: dict[str, Any]) -> str:
    return _digest(_owner_serial_id(owner))


def _match_owner(owner: dict[str, Any], rows: dict[str, NativeRow],
                 receipt_cache: dict[tuple[str, str, str, str], str], receipt_verifier=None) -> tuple[str, list[str], str | None]:
    """Return (kind, candidate QIDs, basis) without exposing owner values."""
    if owner.get("record_kind") != "work" or owner.get("identity_scope_verified") is not True:
        return "scope_held", [], None
    owner_ids = _owner_work_ids(owner)
    owner_serial = _owner_serial_id(owner)
    candidates: set[str] = set()
    held_candidates: set[str] = set()
    bases: dict[str, str] = {}
    for qid, row in rows.items():
        common = owner_ids & _record_work_ids(row.record)
        if common:
            candidates.add(qid)
            bases[qid] = "explicit_work_provider_id"
        elif owner_serial in _record_serial_ids(row.record):
            candidates.add(qid)
            bases[qid] = "explicit_work_serial_id"
        else:
            receipt_state = _valid_saved_receipts(owner, qid, owner_serial, receipt_cache, receipt_verifier)
            if receipt_state == "verified":
                candidates.add(qid)
                bases[qid] = "saved_same_work_receipt"
            elif receipt_state == "held":
                held_candidates.add(qid)
    if held_candidates - candidates:
        if candidates:
            return "ambiguous", sorted(candidates | held_candidates), None
        return "receipt_held", sorted(held_candidates), None
    if len(candidates) == 1:
        qid = next(iter(candidates))
        return "new_same_work", [qid], bases[qid]
    if candidates:
        return "ambiguous", sorted(candidates), None
    return "unmatched", [], None


def _owner_rows(history: Iterable[dict[str, Any]], expected_count: int) -> list[dict[str, Any]]:
    rows = list(history)
    if len(rows) != expected_count:
        raise AuditInputError(f"owner roster count differs: expected {expected_count}, got {len(rows)}")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise AuditInputError("owner roster row is not an object")
        code = _owner_serial_id(row)
        if code in seen:
            raise AuditInputError("owner roster contains duplicate serialization ID")
        seen.add(code)
    return rows


def audit_delta(
    *,
    database: Path,
    baseline_native: Path,
    owner_history: Iterable[dict[str, Any]],
    baseline_sha256: str | None = None,
    expected_owner_count: int = DEFAULT_OWNER_COUNT,
    existing_serial_relations: int = DEFAULT_EXISTING_SERIAL_RELATIONS,
    existing_content_links: int = DEFAULT_EXISTING_CONTENT_LINKS,
    minimum_current_native: int | None = None,
    owner_history_path: Path | None = None,
    owner_history_sha256: str | None = None,
    historical_reference_refs: Iterable[str] = (),
    receipt_verifier=None,
) -> dict[str, Any]:
    """Run the bounded native-delta audit and return aggregate-only data."""
    report: dict[str, Any] = {
        "version": "scale20k-history-delta-audit-v1",
        "status": "violations",
        "comparison": {
            "status": "unavailable",
            "complete": False,
            "relation_eligible_n": 0,
            "comparison_eligible_n": 0,
        },
        "issues": [],
        "model_calls": 0,
        "network_calls": 0,
        "database_writes": 0,
        "source_prose_scanned": False,
        "database_read_scope": "mode=ro; BEGIN snapshot; baseline source JSON not decoded",
        "private_fields_exported": ["none"],
        "owner_roster_pin": {
            "path": str(Path(owner_history_path).resolve()) if owner_history_path else None,
            "sha256": owner_history_sha256,
            "status": "not_supplied" if owner_history_path is None else "supplied_by_caller",
        },
        "historical_reference": {
            "owner_roster_n": expected_owner_count,
            "serialization_relation_n": existing_serial_relations,
            "content_link_n": existing_content_links,
            "status": "not_reverified_in_this_delta_audit",
            "source_refs": list(historical_reference_refs),
        },
    }
    try:
        baseline = load_frozen_baseline(baseline_native, expected_sha256=baseline_sha256)
        owners = _owner_rows(owner_history, expected_owner_count)
        native, current_all, current, new_all_qids, new_qids = read_native_delta(
            database, baseline, minimum_current_native=minimum_current_native
        )
        new_rows = {qid: native[qid] for qid in sorted(new_qids)}
        counts = Counter()
        matches: list[dict[str, Any]] = []
        held_candidate_qids: set[str] = set()
        receipt_cache: dict[tuple[str, str, str, str], str] = {}
        for owner in owners:
            kind, candidates, basis = _match_owner(owner, new_rows, receipt_cache, receipt_verifier)
            counts[kind] += 1
            if kind == "new_same_work":
                qid = candidates[0]
                matches.append({"owner_digest": _owner_digest(owner), "catalogue_qid": qid, "basis": basis})
            elif kind == "receipt_held":
                held_candidate_qids.update(candidates)
        relation_n = counts["new_same_work"]
        report["counts"] = {
            "baseline_native_n": len(baseline),
            "current_native_n": len(current_all),
            "current_display_native_n": len(current),
            "new_native_delta_n": len(new_all_qids),
            "new_display_native_delta_n": len(new_qids),
            "baseline_source_rows_decoded_n": 0,
            "new_source_rows_decoded_n": len(native),
            "owner_rows_scanned_n": len(owners),
            "new_same_work_relation_n": relation_n,
            "new_same_work_relation_owner_rows_n": relation_n,
            "new_same_work_relation_catalogue_qids_n": len({item["catalogue_qid"] for item in matches}),
            "new_relation_candidate_owner_rows_n": relation_n + counts["receipt_held"],
            "new_relation_candidate_catalogue_qids_n": len(
                held_candidate_qids | {item["catalogue_qid"] for item in matches}
            ),
            "ambiguous_owner_rows_n": counts["ambiguous"],
            "receipt_held_owner_rows_n": counts["receipt_held"],
            "scope_held_owner_rows_n": counts["scope_held"],
            "owner_scope_eligible_n": len(owners) - counts["scope_held"],
            "unmatched_owner_rows_n": counts["unmatched"],
        }
        report["new_relation_matches"] = matches
        report["comparison"]["relation_eligible_n"] = relation_n
        report["comparison"]["comparison_eligible_n"] = 0 if relation_n == 0 else None
        report["comparison"]["status"] = "unavailable" if relation_n == 0 else "unassessed"
        report["comparison"]["next_steps"] = (
            [
                "check feature admission and source purpose for each relation; this delta auditor did not assess them",
                "build the same-candidate comparison pool and grouped leakage split",
                "measure recommendation values separately; independent accuracy/calibration remains a separate evaluation",
            ] if relation_n else ["no exact new relation; comparison denominator is zero"]
        )
        report["comparison"]["reason"] = (
            "feature admission, common candidates, grouped leakage and evaluation values were not assessed by this delta audit"
            if relation_n else "no new exact same-work relation"
        )
        report["status"] = "complete"
        report["audit_scope"] = "final native QID delta only; baseline rows were not re-matched"
    except (AuditInputError, OSError, sqlite3.Error, TypeError, KeyError) as exc:
        report["issues"].append(type(exc).__name__ + ": " + str(exc))
    return report


def _load_history(path: Path, expected_sha256: str) -> tuple[list[dict[str, Any]], str]:
    raw = Path(path).resolve(strict=True).read_bytes()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected_sha256:
        raise AuditInputError("owner roster SHA256 differs")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if line.strip():
            rows.append(_json_object(line, "owner history row"))
    return rows, observed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--baseline-native", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--owner-history", type=Path, required=True)
    parser.add_argument("--owner-history-sha256", required=True)
    parser.add_argument("--baseline-sha256", default=None)
    parser.add_argument("--minimum-current-native", type=int, default=DEFAULT_NATIVE_TARGET)
    parser.add_argument("--historical-reference-ref", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        parser.error("output already exists; use a new path")
    try:
        owner_history, owner_history_sha256 = _load_history(args.owner_history, args.owner_history_sha256)
    except AuditInputError as exc:
        parser.error(str(exc))
    result = audit_delta(
        database=args.database,
        baseline_native=args.baseline_native,
        owner_history=owner_history,
        baseline_sha256=args.baseline_sha256,
        minimum_current_native=args.minimum_current_native,
        owner_history_path=args.owner_history,
        owner_history_sha256=owner_history_sha256,
        historical_reference_refs=args.historical_reference_ref,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "counts": result.get("counts")}, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
