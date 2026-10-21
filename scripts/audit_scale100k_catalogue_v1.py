"""Read-only final audit for the v19 catalogue and public projections.

The audit deliberately covers the SQLite database and the current public export
artifacts only.  It does not replay model, rights, usage-guard, coordinator,
UI, or CI history.  It never opens the catalogue in a writable mode.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from recommend_book.scale_catalogue_v1 import _display_native_labels, _native_axis_summary


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-100000-v1"
PLAN = ROOT / "docs/autonomous-next-phase-plan-v19.md"
REQUIREMENTS = BASE / "requirements.json"
BASELINE_WORKS = BASE / "baseline-works-32130.jsonl"
BASELINE_NATIVE = BASE / "baseline-native-5000.jsonl"
DATABASE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
EXPECTED = {
    "plan": "9d49f4e5fbd4e9350c129f6e4f190769464d8292b33a28fd3e65941f4a22efb9",
    "requirements": "af2afa4eaaf2129c5b585ca76554a9eaa3f0be51b5a0bf4f0d9b2a09f94db6fc",
    "baseline_works": "aebcc125910a868227ea3d8156d764efc9e43209d3b3daaffbb4b9b149efa62b",
    "baseline_native": "3886229ec2c404f54b816710e9851bda279897e9f86e255d907f961f20a34bcd",
}
AXES = ("genre", "theme", "structure", "protagonist", "style", "atmosphere")
DEFAULT_OUTPUT = BASE / "audit" / "scale100k-catalogue-audit-v1.json"


class _FileCache:
    def __init__(self) -> None:
        self._raw: dict[Path, bytes] = {}

    def raw(self, path: Path) -> bytes:
        path = Path(path).resolve(strict=True)
        if path not in self._raw:
            self._raw[path] = path.read_bytes()
        return self._raw[path]

    def sha(self, path: Path) -> str:
        return hashlib.sha256(self.raw(path)).hexdigest()

    def json(self, path: Path):
        return json.loads(self.raw(path))


def canonical_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _jsonl(raw: bytes) -> Iterable[dict]:
    for line in raw.splitlines():
        if line.strip():
            yield json.loads(line)


@contextmanager
def read_snapshot(path: Path):
    """Yield one query-only read transaction and always close it explicitly."""
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


def frozen_inputs(cache: _FileCache) -> tuple[dict, list[dict], list[dict], list[str]]:
    violations: list[str] = []
    for name, path in (("plan", PLAN), ("requirements", REQUIREMENTS),
                       ("baseline_works", BASELINE_WORKS), ("baseline_native", BASELINE_NATIVE)):
        try:
            observed = cache.sha(path)
        except (OSError, ValueError) as exc:
            violations.append(f"missing_frozen:{name}:{exc}")
            continue
        if observed != EXPECTED[name]:
            violations.append(f"frozen_sha_mismatch:{name}")
    try:
        requirements = cache.json(REQUIREMENTS)
    except (OSError, ValueError):
        requirements = {}
    works = list(_jsonl(cache.raw(BASELINE_WORKS))) if BASELINE_WORKS.exists() else []
    native = list(_jsonl(cache.raw(BASELINE_NATIVE))) if BASELINE_NATIVE.exists() else []
    work_qids = [row.get("qid") for row in works]
    native_qids = [row.get("qid") for row in native]
    if len(works) != 32130 or len(set(work_qids)) != 32130:
        violations.append("baseline_works_count_or_qid_mismatch")
    if len(native) != 5000 or len(set(native_qids)) != 5000:
        violations.append("baseline_native_count_or_qid_mismatch")
    return requirements, works, native, violations


def _labels(value) -> list[dict]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("admitted_labels", "labels", "features"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _held(value) -> list[dict]:
    if not isinstance(value, dict):
        return []
    out = []
    for key in ("held_labels", "llm_held_features", "held_features"):
        if isinstance(value.get(key), list):
            out.extend(item for item in value[key] if isinstance(item, dict))
    admission = value.get("label_admission")
    if isinstance(admission, dict) and isinstance(admission.get("held_labels"), list):
        out.extend(item for item in admission["held_labels"] if isinstance(item, dict))
    return out


def _axis(label: dict) -> str | None:
    axis = label.get("axis")
    if axis in AXES:
        return axis
    label_id = label.get("label_id")
    if isinstance(label_id, str) and "." in label_id and label_id.split(".", 1)[0] in AXES:
        return label_id.split(".", 1)[0]
    return None


def _period(year) -> str:
    if not isinstance(year, int):
        return "unknown"
    if year < 1800:
        return "before-1800"
    if year < 1900:
        return "1800-1899"
    if year < 1950:
        return "1900-1949"
    if year < 2000:
        return "1950-1999"
    return "2000-or-later"


def audit_database(database: Path, baseline_works: list[dict], baseline_native: list[dict],
                   *, limits: dict[str, int] | None = None) -> dict:
    defaults = {"searchable": 100000, "recommendable": 10000,
                "native_join": 10000, "new_eligible_native": 5000}
    if limits:
        defaults.update(limits)
    limits = defaults
    baseline_versions = {row.get("qid"): row.get("version_id") for row in baseline_works}
    baseline_hashes = {row.get("qid"): row.get("row_sha256") for row in baseline_native}
    issues: list[str] = []
    native_stats = {
        "accepted": 0, "display": 0, "display_recommendable": 0,
        "content_nongenre": 0, "feature_adopted": 0,
        "axes": {axis: {"adopted": 0, "held": 0, "unknown": 0} for axis in AXES},
        "languages": Counter(), "periods": Counter(),
    }
    searchable_languages, searchable_periods = Counter(), Counter()
    baseline_current = baseline_source_missing = baseline_hash_mismatch = 0
    baseline_native_seen = 0
    actual_native = total_native = new_eligible_native = 0
    new_native_qids: set[str] = set()
    manifest_pins: dict[str, dict] = {}
    with read_snapshot(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"works", "source_versions", "native_classifications"}
        missing = sorted(required - tables)
        if missing:
            return {"status": "incomplete", "issues": ["missing_tables:" + ",".join(missing)]}
        searchable = db.execute("SELECT count(*) FROM works WHERE display_allowed=1").fetchone()[0]
        stored = db.execute("SELECT count(*) FROM works").fetchone()[0]
        recommendable = db.execute("SELECT count(*) FROM works WHERE display_allowed=1 AND recommendable=1").fetchone()[0]
        native_join = db.execute("SELECT count(*) FROM native_classifications n JOIN works w USING(qid) WHERE w.display_allowed=1 AND w.recommendable=1").fetchone()[0]
        for work in db.execute("SELECT language,year FROM works WHERE display_allowed=1"):
            searchable_languages[work[0] or "unknown"] += 1
            searchable_periods[_period(work[1])] += 1
        source_pairs: set[tuple[str, str]] = set()
        for row in db.execute("SELECT qid,version_id FROM source_versions"):
            if row[0] in baseline_versions:
                source_pairs.add((row[0], row[1]))
        for row in db.execute("SELECT qid,version_id FROM works"):
            qid, version = row[0], row[1]
            if qid in baseline_versions:
                if version == baseline_versions[qid]:
                    baseline_current += 1
                if (qid, version) not in source_pairs:
                    baseline_source_missing += 1
        native_sql = ("SELECT n.qid,n.manifest_path,n.manifest_sha256,n.labels_json,n.provenance_json,"
                      "w.display_allowed,w.recommendable,w.language,w.year "
                      "FROM native_classifications n LEFT JOIN works w USING(qid)")
        for row in db.execute(native_sql):
            qid = row[0]
            total_native += 1
            canonical = {"qid": row[0], "manifest_path": row[1], "manifest_sha256": row[2],
                         "labels_json": row[3], "provenance_json": row[4]}
            if qid in baseline_hashes:
                baseline_native_seen += 1
                observed = hashlib.sha256(canonical_json(canonical)).hexdigest()
                if observed != baseline_hashes[qid]:
                    baseline_hash_mismatch += 1
            else:
                path_key = str(Path(row[1]).resolve())
                pin = manifest_pins.setdefault(path_key, {"sha256": row[2], "rows": {}})
                if pin["sha256"] != row[2]:
                    issues.append(f"native_manifest_pin_mismatch:{path_key}")
                pin["rows"][qid] = row[3]
                actual_native += 1
                new_native_qids.add(qid)
                native_stats["accepted"] += 1
                native_stats["display"] += bool(row[5])
                native_stats["display_recommendable"] += bool(row[5] and row[6])
                new_eligible_native += bool(row[5] and row[6])
                native_stats["languages"][row[7] if row[7] else "unknown"] += 1
                native_stats["periods"][_period(row[8])] += 1
                try:
                    labels = _labels(json.loads(row[3]))
                except (TypeError, ValueError):
                    labels = []
                    issues.append(f"invalid_native_json:{qid}")
                for label in labels:
                    axis = _axis(label)
                    if axis and label.get("decision", "present") == "present":
                        native_stats["axes"][axis]["adopted"] += 1
                if labels:
                    native_stats["feature_adopted"] += 1
        if searchable < limits["searchable"] or recommendable < limits["recommendable"] \
                or native_join < limits["native_join"] or new_eligible_native < limits["new_eligible_native"]:
            issues.append("threshold_not_met")
    if baseline_current != len(baseline_versions):
        issues.append("baseline_current_version_mismatch")
    if baseline_source_missing:
        issues.append("baseline_source_version_reference_missing")
    if baseline_hash_mismatch:
        issues.append("baseline_native_canonical_row_hash_mismatch")
    baseline_native_missing = len(baseline_hashes) - baseline_native_seen
    if baseline_native_missing:
        issues.append("baseline_native_rows_missing")
    if len(set(baseline_versions)) != len(baseline_versions):
        issues.append("baseline_duplicate_qid")
    status = "violations" if any("mismatch" in issue or "reference_missing" in issue or "rows_missing" in issue
                                  or "invalid_" in issue or "manifest_" in issue for issue in issues) else ("incomplete" if issues else "complete")
    return {"status": status, "issues": issues, "stored": stored, "searchable": searchable, "recommendable": recommendable,
            "native_total": total_native,
            "native_join_display_recommendable": native_join, "baseline_current_versions": baseline_current,
            "baseline_source_versions_missing": baseline_source_missing, "baseline_native_hash_mismatches": baseline_hash_mismatch,
            "baseline_native_rows_missing": baseline_native_missing,
            "new_native": actual_native, "new_eligible_native": new_eligible_native,
            "new_native_qids": sorted(new_native_qids), "manifest_pins": manifest_pins,
            "new_native_stats": {**native_stats, "languages": dict(native_stats["languages"]), "periods": dict(native_stats["periods"])},
            "searchable_languages": dict(searchable_languages), "searchable_periods": dict(searchable_periods)}


def audit_public_manifests(manifest_pins: dict[str, dict], cache: _FileCache,
                           baseline_qids: set[str], new_native_qids: set[str]) -> dict:
    """Authenticate only manifests pinned by native_classifications.

    The v3 public rows are read from each distinct manifest exactly once.  SQL
    labels are compared to the authenticated admitted_labels projection; no
    source prefix is treated as content evidence.
    """
    issues: list[str] = []
    files, public_qids = [], set()
    public_signatures: dict[str, str] = {}
    content_nongenre_qids: set[str] = set()
    display_feature_qids: set[str] = set()
    axis_counts = {axis: {"adopted": 0, "held": 0, "unknown": 0} for axis in AXES}
    for path_string, pin in manifest_pins.items():
        path = Path(path_string)
        try:
            raw = cache.raw(path)
            manifest = json.loads(raw)
        except (OSError, ValueError) as exc:
            issues.append(f"missing_or_invalid_pinned_manifest:{path}:{exc}"); continue
        observed_sha = hashlib.sha256(raw).hexdigest()
        if observed_sha != pin["sha256"]:
            issues.append(f"pinned_manifest_sha_mismatch:{path}")
        version = manifest.get("version")
        if version != "scale-content-native-classification-export-v3":
            files.append({"path": str(path), "sha256": observed_sha, "version": version, "skipped": True})
            continue
        projection = manifest.get("verified_projection")
        rows = projection.get("rows") if isinstance(projection, dict) else None
        if not isinstance(rows, list):
            issues.append(f"v3_verified_projection_rows_missing:{path}"); continue
        files.append({"path": str(path), "sha256": observed_sha, "version": version, "rows": len(rows)})
        projection_by_qid = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("qid"), str):
                issues.append(f"v3_row_missing_qid:{path}"); continue
            qid = row["qid"]
            if qid in projection_by_qid:
                issues.append(f"v3_duplicate_qid:{path}:{qid}")
            projection_by_qid[qid] = row
            public_qids.add(qid)
            is_new_native = qid in new_native_qids
            signature = hashlib.sha256(canonical_json(row.get("admitted_labels", []))).hexdigest()
            if qid in public_signatures and public_signatures[qid] != signature:
                issues.append(f"public_same_qid_labels_mismatch:{qid}")
            elif qid in public_signatures:
                issues.append(f"public_duplicate_qid:{qid}")
            public_signatures[qid] = signature
            sql_labels = pin["rows"].get(qid)
            if sql_labels is None:
                issues.append(f"public_row_missing_sql_qid:{qid}")
            else:
                try:
                    if canonical_json(json.loads(sql_labels)) != canonical_json(row.get("admitted_labels", [])):
                        issues.append(f"sql_labels_mismatch:{qid}")
                except (TypeError, ValueError):
                    issues.append(f"invalid_sql_labels:{qid}")
            labels = row.get("admitted_labels") if isinstance(row.get("admitted_labels"), list) else []
            if not isinstance(row.get("admitted_labels"), list):
                issues.append(f"public_admitted_labels_missing:{qid}")
            displayed, display_held = _display_native_labels(labels)
            if is_new_native and displayed:
                display_feature_qids.add(qid)
            content_ids = set(row.get("content_evidence_ids", [])) if isinstance(row.get("content_evidence_ids"), list) else set()
            attributions = row.get("source_attributions")
            if content_ids:
                attribution_ids = {
                    evidence_id
                    for item in (attributions if isinstance(attributions, list) else [])
                    if isinstance(item, dict)
                    for evidence_id in item.get("evidence_ids", [])
                    if isinstance(item.get("evidence_ids"), list)
                }
                if not content_ids <= attribution_ids:
                    issues.append(f"public_content_attribution_binding:{qid}")
            for label in displayed:
                axis = _axis(label)
                for citation in label.get("evidence", []) if isinstance(label.get("evidence"), list) else []:
                    if is_new_native and isinstance(citation, dict) and citation.get("evidence_id") in content_ids and axis and axis != "genre":
                        content_nongenre_qids.add(qid)
            if is_new_native:
                for summary in _native_axis_summary(row, displayed, display_held):
                    counts = axis_counts[summary['axis']]
                    counts['adopted'] += summary['adopted_count']
                    counts['held'] += summary['held_count']
                    counts['unknown'] += bool(summary['unknown'])
        for qid in set(pin["rows"]) - set(projection_by_qid):
            issues.append(f"sql_qid_missing_public_row:{qid}")
    missing_new = new_native_qids - public_qids
    if missing_new:
        issues.append(f"new_native_missing_public_rows:{len(missing_new)}")
    return {"status": "violations" if issues else "complete", "files": files,
            "rows": len(public_qids), "distinct_qids": len(public_qids),
            "content_nongenre_qids": sorted(content_nongenre_qids),
            "actual_display_feature_works": len(display_feature_qids),
            "axis_counts": axis_counts, "issues": sorted(set(issues))}


def audit(*, database: Path = DATABASE, output: Path | None = None) -> dict:
    cache = _FileCache()
    requirements, works, native, frozen_issues = frozen_inputs(cache)
    if database.exists():
        database_report = audit_database(database, works, native)
        manifest_pins = database_report.pop("manifest_pins", {})
        new_native_qids = set(database_report.pop("new_native_qids", []))
        public_report = audit_public_manifests(
            manifest_pins, cache, {row.get("qid") for row in works}, new_native_qids
        )
        database_report["new_native_stats"]["content_nongenre"] = len(public_report["content_nongenre_qids"])
        database_report["new_native_stats"]["actual_display_feature_works"] = public_report["actual_display_feature_works"]
        database_report["new_native_stats"]["axes"] = public_report["axis_counts"]
    else:
        database_report = {"status": "incomplete", "issues": ["missing_database"]}
        public_report = {"status": "incomplete", "issues": ["missing_database"]}
    result = {"version": "scale100k-catalogue-audit-v1",
              "scope": "SQLite DB + current public exports only; baseline current-version references and their source_versions presence are checked, not full historical preservation",
              "replayed": {"model": False, "http": False, "rights": False, "usage_guard": False, "coordinator": False, "ui": False, "ci": False},
              "frozen": {"requirements_sha256": cache.sha(REQUIREMENTS) if REQUIREMENTS.exists() else None,
                         "plan_sha256": cache.sha(PLAN) if PLAN.exists() else None,
                         "baseline_works_sha256": cache.sha(BASELINE_WORKS) if BASELINE_WORKS.exists() else None,
                         "baseline_native_sha256": cache.sha(BASELINE_NATIVE) if BASELINE_NATIVE.exists() else None,
                         "issues": frozen_issues},
              "requirements": requirements,
              "database": database_report, "public_manifests": public_report}
    statuses = {result["database"]["status"], result["public_manifests"]["status"]}
    if frozen_issues or "violations" in statuses:
        result["status"] = "violations"
    elif "incomplete" in statuses:
        result["status"] = "incomplete"
    else:
        result["status"] = "complete"
    if output is not None:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = audit(database=args.database, output=args.output)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve())}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "complete" else 2)


if __name__ == "__main__":
    main()
