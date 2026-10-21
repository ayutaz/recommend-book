"""Read-only audit of current catalogue source bindings.

This checks only each work's current ``source_versions`` row, its pinned raw
artifact, and the current source-conditions receipt.  Historical source rows,
model outputs, rights replay, HTTP, and UI state are outside this scope.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from recommend_book.models import SourceRecord
from recommend_book.rights import RightsRegistry
from recommend_book.scale_catalogue_v1 import ScaleCatalogue


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
PURPOSES = ("acquire", "local_storage", "display", "external_llm", "commercial", "redistribution")
RETAINED_CONDITION_VERSIONS = frozenset({
    "scale-source-conditions-v1",
    "scale-source-conditions-v2",
    "scale-source-conditions-v19-reused-v1",
    "scale-source-conditions-v19-qlever-v1",
})


class _ReceiptHashMismatch(ValueError):
    """A receipt exists but its bytes do not match the immutable pin."""


class _FileCache:
    """Read each pinned artifact once per ``(path, sha256)`` binding."""

    def __init__(self) -> None:
        self._summary: dict[tuple[Path, str | None], dict] = {}
        self._raw: dict[tuple[Path, str], bytes] = {}

    def read_verified(self, path: Path, expected_sha: str) -> bytes:
        path = Path(path).resolve(strict=True)
        key = (path, expected_sha)
        if key in self._raw:
            return self._raw[key]
        raw = path.read_bytes()
        if _sha(raw) != expected_sha:
            raise _ReceiptHashMismatch("pinned artifact hash differs")
        self._raw[key] = raw
        return raw

    def inspect(self, path: Path, parser=None, *, expected_sha: str | None = None) -> dict:
        path = Path(path).resolve(strict=True)
        key = (path, expected_sha)
        if key in self._summary:
            return self._summary[key]
        if expected_sha is not None:
            try:
                raw = self.read_verified(path, expected_sha)
            except ValueError:
                # Keep the observed digest so callers can report tampering
                # without treating a hash mismatch as an auditor crash.
                raw = path.read_bytes()
        else:
            raw = path.read_bytes()
        summary = {"sha256": _sha(raw)}
        if parser is not None:
            summary.update(parser(raw))
        self._summary[key] = summary
        return summary


@contextmanager
def read_snapshot(path: Path):
    """Open one query-only SQLite snapshot and close it explicitly."""
    uri = Path(path).resolve(strict=True).as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    finally:
        if db.in_transaction:
            db.rollback()
        db.close()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_record(raw: str) -> tuple[dict | None, str | None]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, "record_json_invalid"
    if not isinstance(value, dict):
        return None, "record_json_not_object"
    try:
        # Use the shared contract for type/field validation; do not infer any
        # identity from title or other bibliographic text.
        record = SourceRecord.model_validate(value)
    except (TypeError, ValueError):
        return None, "record_schema_invalid"
    return record.model_dump(mode="json"), None


def _raw_summary(raw: bytes) -> dict:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"json_valid": False, "entity_ids": frozenset()}
    entities = payload.get("entities") if isinstance(payload, dict) else None
    valid_entity_ids = frozenset(
        key for key, entity in entities.items()
        if isinstance(entity, dict) and entity.get("id") == key
    ) if isinstance(entities, dict) else frozenset()
    return {
        "json_valid": isinstance(entities, dict),
        "entity_ids": frozenset(entities) if isinstance(entities, dict) else frozenset(),
        "valid_entity_ids": valid_entity_ids,
    }


def _identity_issue(record: dict, qid: str, raw_summary: dict) -> str | None:
    source = record.get("source")
    source_id = record.get("source_id")
    if source_id != qid:
        return "record_source_id_qid_mismatch"
    if record.get("raw_path") is None or record.get("raw_sha256") is None:
        return "record_raw_binding_missing"
    if (source, record.get("provider_id"), record.get("record_kind"), record.get("rights_policy_id")) != (
        "wikidata", "wikidata", "work", "wikidata-cc0"
    ):
        return "unsupported_source_identity"
    if record.get("source_url") != f"https://www.wikidata.org/wiki/{qid}":
        return "record_source_url_identity_mismatch"
    if not raw_summary.get("json_valid"):
        return "raw_json_invalid"
    if qid not in raw_summary.get("valid_entity_ids", ()):
        return "raw_entity_qid_mismatch"
    return None


class _ReadOnlyScaleVerifier:
    """Reuse ScaleCatalogue's current-condition verifier without constructing it."""

    def __init__(self, meta: dict, cache: _FileCache, registry: RightsRegistry) -> None:
        self._meta = meta
        self._registry = registry
        self._cache = cache

    def _artifact(self, path, sha):
        # The primary policy is one document. Return its actual verified bytes
        # to the existing condition checker, then let the caller release them.
        raw = self._cache.read_verified(path, sha)
        if _sha(raw) != sha:
            raise ValueError("primary snapshot hash differs")
        return raw

    def verify(self) -> tuple[dict, dict]:
        return ScaleCatalogue._conditions(self)


def _condition_summary(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {"condition_data": None}
    return {"condition_data": value if isinstance(value, dict) else None}


def _absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("proof path must be absolute")
    return path.resolve(strict=True)


def _verify_retained_conditions(
    path: Path, expected_sha: str, cache: _FileCache, registry: RightsRegistry
) -> tuple[dict, dict]:
    """Verify a row-pinned receipt without rebinding it to catalogue_meta."""
    summary = cache.inspect(path, _condition_summary, expected_sha=expected_sha)
    if summary["sha256"] != expected_sha:
        raise _ReceiptHashMismatch("retained source conditions hash differs")
    data = summary.get("condition_data")
    if not isinstance(data, dict):
        raise ValueError("retained source conditions are not an object")
    if (data.get("version"), data.get("source"), data.get("policy_id"), data.get("metadataonly_scope")) not in {
        (version, "wikidata", "wikidata-cc0", True) for version in RETAINED_CONDITION_VERSIONS
    }:
        raise ValueError("retained source conditions metadata scope differs")
    try:
        from datetime import datetime, timezone
        date = datetime.fromisoformat(data["retrieved_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("retained source conditions timestamp invalid") from exc
    now = datetime.now(timezone.utc)
    if date.tzinfo is None or date > now or (now - date).days > 7:
        raise ValueError("retained source conditions receipt is not current")
    primary = data.get("policy_snapshot")
    if not isinstance(primary, dict):
        raise ValueError("retained source conditions primary snapshot missing")
    usages_data = data.get("usages")
    if not isinstance(usages_data, dict):
        raise ValueError("retained source conditions usages invalid")
    try:
        primary_path = _absolute_path(primary["path"])
        primary_raw = cache.read_verified(primary_path, primary["sha256"])
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ValueError("retained source conditions primary snapshot invalid") from exc
    if "cc0" not in primary_raw.decode("utf-8").lower():
        raise ValueError("retained primary CC0 condition clause absent")
    policy = registry.policies["wikidata-cc0"]
    if policy.source != "wikidata" or policy.provider_id != "wikidata" or policy.license != "CC0-1.0":
        raise ValueError("source registry metadata policy differs")
    usages = {
        usage: ("allowed" if state == "allowed" and policy.usages.get(usage) == "allowed" else "hold")
        for usage, state in usages_data.items()
    }
    for usage in PURPOSES:
        usages.setdefault(usage, "hold")
    return data, usages


def _assert_absolute_condition_paths(meta: dict, cache: _FileCache) -> None:
    """Reject cwd-dependent global and primary receipt proof paths."""
    receipt_path = _absolute_path(meta["source_conditions_path"])
    summary = cache.inspect(receipt_path, _condition_summary, expected_sha=meta["source_conditions_sha256"])
    if summary["sha256"] != meta["source_conditions_sha256"]:
        raise _ReceiptHashMismatch("global source conditions hash differs")
    data = summary.get("condition_data")
    if not isinstance(data, dict) or not isinstance(data.get("policy_snapshot"), dict):
        raise ValueError("global source conditions primary snapshot missing")
    _absolute_path(data["policy_snapshot"]["path"])


def _row_conditions_binding(row_json: str) -> tuple[str, str] | None:
    try:
        row = json.loads(row_json)
    except (TypeError, ValueError):
        return None
    provenance = row.get("provenance") if isinstance(row, dict) else None
    if not isinstance(provenance, dict):
        return None
    path = provenance.get("source_conditions_path")
    sha = provenance.get("source_conditions_sha256")
    if not isinstance(path, str) or not isinstance(sha, str):
        return None
    return path, sha


def audit_sources(database: Path = DATABASE, *, output: Path | None = None) -> dict:
    """Audit current source rows without writing to the database or source files."""
    cache = _FileCache()
    issues: list[str] = []
    counts = Counter()
    source_condition_sha = None
    condition_data: dict = {}
    raw_by_path: dict[Path, str] = {}
    registry = RightsRegistry.default()
    retained_condition_cache: dict[tuple[Path, str], tuple[str, tuple[dict, dict] | None]] = {}
    output_handle = None
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve the result before opening the database. Existing results are
        # never replaced by a later audit run.
        output_handle = output.open("xb")

    try:
        with read_snapshot(database) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"catalogue_meta", "works", "source_versions"}
            missing = sorted(required - tables)
            if missing:
                issues.append("missing_tables:" + ",".join(missing))
            else:
                meta = dict(db.execute("SELECT key,value FROM catalogue_meta"))
                source_condition_sha = meta.get("source_conditions_sha256")
                try:
                    _assert_absolute_condition_paths(meta, cache)
                    condition_data, rights = _ReadOnlyScaleVerifier(meta, cache, registry).verify()
                except (OSError, TypeError, ValueError, KeyError):
                    condition_data, rights = {}, {}
                    issues.append("source_conditions_invalid")
                if rights:
                    for purpose in PURPOSES:
                        if rights.get(purpose) != "allowed":
                            issues.append(f"source_usage_hold:{purpose}")
                counts["works_current"] = db.execute("SELECT count(*) FROM works").fetchone()[0]
                current_sql = """
                    SELECT w.qid AS work_qid, w.version_id,
                           s.qid AS source_qid, s.record_sha256, s.record_json,
                           s.raw_path, s.raw_sha256, s.conditions_sha256, w.row_json
                    FROM works AS w
                    LEFT JOIN source_versions AS s ON s.version_id = w.version_id
                """
                source_condition_bindings: set[tuple[Path, str]] = set()
                for row in db.execute(current_sql):
                    counts["current_rows_checked"] += 1
                    qid = row["work_qid"]
                    if row["source_qid"] is None:
                        issues.append("current_source_version_missing")
                        continue
                    counts["current_source_versions"] += 1
                    if row["source_qid"] != qid:
                        issues.append("current_source_qid_mismatch")
                    binding = _row_conditions_binding(row["row_json"])
                    row_condition_data = condition_data
                    row_rights = rights
                    if binding is None:
                        issues.append("current_source_conditions_provenance_missing")
                    else:
                        receipt_path_text, receipt_sha = binding
                        if receipt_sha != row["conditions_sha256"]:
                            issues.append("current_source_conditions_provenance_mismatch")
                        else:
                            try:
                                receipt_path_value = Path(receipt_path_text)
                            except (OSError, TypeError, ValueError):
                                issues.append("current_source_conditions_receipt_missing")
                            else:
                                if not receipt_path_value.is_absolute():
                                    issues.append("current_source_conditions_receipt_relative")
                                else:
                                    try:
                                        receipt_path = receipt_path_value.resolve(strict=True)
                                    except (OSError, TypeError, ValueError):
                                        issues.append("current_source_conditions_receipt_missing")
                                    else:
                                        receipt_key = (receipt_path, receipt_sha)
                                        if receipt_key in source_condition_bindings:
                                            counts["shared_source_conditions_reused"] += 1
                                        else:
                                            source_condition_bindings.add(receipt_key)
                                            counts["unique_source_conditions_checked"] += 1
                                        if receipt_key not in retained_condition_cache:
                                            try:
                                                retained_condition_cache[receipt_key] = (
                                                    "valid",
                                                    _verify_retained_conditions(
                                                        receipt_path, receipt_sha, cache, registry
                                                    ),
                                                )
                                            except FileNotFoundError:
                                                retained_condition_cache[receipt_key] = ("missing", None)
                                            except _ReceiptHashMismatch:
                                                retained_condition_cache[receipt_key] = ("sha_mismatch", None)
                                            except (OSError, TypeError, ValueError, KeyError, UnicodeError):
                                                retained_condition_cache[receipt_key] = ("invalid", None)
                                        receipt_status, receipt_result = retained_condition_cache[receipt_key]
                                        if receipt_status == "missing":
                                            issues.append("current_source_conditions_receipt_missing")
                                        elif receipt_status == "sha_mismatch":
                                            issues.append("current_source_conditions_receipt_sha_mismatch")
                                        elif receipt_status == "invalid":
                                            issues.append("current_source_conditions_receipt_invalid")
                                        else:
                                            assert receipt_result is not None
                                            row_condition_data, row_rights = receipt_result
                                            for purpose in PURPOSES:
                                                if row_rights.get(purpose) != "allowed":
                                                    issues.append(f"current_source_usage_hold:{purpose}")
                                            if (
                                                row_condition_data.get("source") != condition_data.get("source")
                                                or row_condition_data.get("policy_id") != condition_data.get("policy_id")
                                            ):
                                                issues.append("current_source_conditions_policy_mismatch")
                    if row["version_id"] != f"wd:{qid}:{row['record_sha256']}":
                        issues.append("current_version_id_binding_mismatch")
                    record, record_issue = _json_record(row["record_json"])
                    if record_issue:
                        issues.append(record_issue)
                        continue
                    assert record is not None
                    if _sha(row["record_json"].encode("utf-8")) != row["record_sha256"]:
                        issues.append("current_record_sha_mismatch")
                    if record.get("source_id") != row["source_qid"]:
                        issues.append("record_source_id_qid_mismatch")
                    if record.get("raw_path") != row["raw_path"] or record.get("raw_sha256") != row["raw_sha256"]:
                        issues.append("record_raw_binding_mismatch")
                    if record.get("rights_policy_id") != condition_data.get("policy_id"):
                        issues.append("current_rights_policy_mismatch")
                    if record.get("source") != condition_data.get("source"):
                        issues.append("current_source_policy_mismatch")
                    source_hostname = urlparse(record.get("source_url", "")).hostname
                    allowed_domains = row_condition_data.get("allowed_domains")
                    if not isinstance(allowed_domains, list) or source_hostname not in allowed_domains:
                        issues.append("current_source_allowed_domain_mismatch")
                    try:
                        raw_path = Path(row["raw_path"]).resolve(strict=True)
                    except (OSError, TypeError, ValueError):
                        issues.append("current_raw_missing")
                        continue
                    expected_raw_sha = row["raw_sha256"]
                    prior_sha = raw_by_path.get(raw_path)
                    if prior_sha is not None and prior_sha != expected_raw_sha:
                        issues.append("shared_raw_expected_sha_conflict")
                    if prior_sha is not None:
                        counts["shared_raw_reused"] += 1
                    try:
                        summary = cache.inspect(raw_path, _raw_summary)
                    except (OSError, ValueError):
                        issues.append("current_raw_missing")
                        continue
                    if prior_sha is None:
                        raw_by_path[raw_path] = expected_raw_sha
                        counts["unique_raw_checked"] += 1
                    if summary["sha256"] != expected_raw_sha:
                        issues.append("current_raw_sha_mismatch")
                    identity_issue = _identity_issue(record, qid, summary)
                    if identity_issue:
                        issues.append(identity_issue)
                    else:
                        counts["identity_verified"] += 1
    except (OSError, sqlite3.Error):
        issues.append("database_read_failed")

    status = "violations" if issues else "complete"
    result = {
        "version": "scale100k-sources-audit-v1",
        "scope": "current works.version_id -> source_versions only; raw SHA/entity identity and current source conditions; history/model/rights replay/UI excluded",
        "replayed": {"model": False, "http": False, "rights": False, "usage_guard": False, "ui": False},
        "status": status,
        "counts": dict(counts),
        "source_conditions_sha256": source_condition_sha,
        "issues": sorted(set(issues)),
    }
    if output_handle is not None:
        try:
            output_handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
            output_handle.flush()
        finally:
            output_handle.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit_sources(args.database, output=args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "complete" else 2)


if __name__ == "__main__":
    main()
