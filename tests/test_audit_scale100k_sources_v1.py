"""Small offline fixtures for the current-source audit."""

import hashlib
import importlib.util
import json
import pytest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_scale100k_sources_v1", ROOT / "scripts/audit_scale100k_sources_v1.py"
)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _conditions(path: Path, *, commercial="allowed") -> str:
    primary = path.parent / "wikidata.html"
    primary.write_bytes(b"Official CC0-1.0 terms")
    value = {
        "version": "scale-source-conditions-v1",
        "source": "wikidata",
        "policy_id": "wikidata-cc0",
        "policy_snapshot": {"path": str(primary.resolve()), "sha256": _sha(primary.read_bytes())},
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "usages": {purpose: "allowed" for purpose in audit.PURPOSES},
        "allowed_domains": ["www.wikidata.org", "query.wikidata.org"],
        "metadataonly_scope": True,
        "scope": "Wikidata structured entity data only; no linked text rights inherited",
    }
    value["usages"]["commercial"] = commercial
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return _sha(raw)


def _record(raw_path: Path, raw_sha: str, qid="Q1") -> dict:
    return {
        "source": "wikidata",
        "source_id": qid,
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved_at": "2026-10-21T00:00:00+00:00",
        "provider_id": "wikidata",
        "record_kind": "work",
        "raw_sha256": raw_sha,
        "raw_path": str(raw_path.resolve()),
        "rights_policy_id": "wikidata-cc0",
    }


def _db(path: Path, *, conditions: Path, conditions_sha: str, work_qid="Q1",
        source_qid="Q1", current_version=None, record_qid="Q1",
        raw_path: Path | None = None, raw_sha: str | None = None,
        record_override: dict | None = None, history=True):
    raw_path = raw_path or conditions.parent / "raw.json"
    if raw_sha is None and raw_path.exists():
        raw_sha = _sha(raw_path.read_bytes())
    record = record_override or _record(raw_path, raw_sha or "0" * 64, record_qid)
    record_json = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record_sha = _sha(record_json.encode("utf-8"))
    current_version = current_version or f"wd:{work_qid}:{record_sha}"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE catalogue_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT NOT NULL, title TEXT NOT NULL,
                               search_text TEXT NOT NULL, language TEXT, year INTEGER,
                               recommendable INTEGER NOT NULL, display_allowed INTEGER NOT NULL,
                               row_json TEXT NOT NULL);
            CREATE TABLE source_versions(
                version_id TEXT PRIMARY KEY,qid TEXT NOT NULL,record_sha256 TEXT NOT NULL,
                record_json TEXT NOT NULL,raw_path TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,conditions_sha256 TEXT NOT NULL);
        """)
        db.executemany("INSERT INTO catalogue_meta VALUES(?,?)", [
            ("source_conditions_path", str(conditions.resolve())),
            ("source_conditions_sha256", conditions_sha),
        ])
        db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?)",
                   (work_qid, current_version, "A", "a", "en", 2000, 1, 1, "{}"))
        db.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                   (current_version, source_qid, record_sha, record_json,
                    str(raw_path.resolve()), raw_sha or "0" * 64,
                    "2026-10-21T00:00:00+00:00", conditions_sha))
        if history:
            db.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                       ("old-version", "Qold", "old", "{}", str(conditions.parent / "missing-old.raw"),
                        "f" * 64, "2026-01-01T00:00:00+00:00", "old-conditions"))


def test_current_source_identity_and_raw_pass_while_history_is_out_of_scope(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "complete"
    assert report["counts"]["current_source_versions"] == 1
    assert report["counts"]["identity_verified"] == 1
    assert "current_raw_missing" not in report["issues"]


def test_tampered_current_raw_fails_closed(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    expected = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(expected)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(expected))
    raw.write_bytes(b'{"entities":{"Q1":{"id":"Q999"}}}')
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "current_raw_sha_mismatch" in report["issues"]


def test_missing_current_raw_fails_closed(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "missing.json"
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha="a" * 64)
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "current_raw_missing" in report["issues"]


def test_current_source_qid_mismatch_is_rejected(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q2":{"id":"Q2"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        source_qid="Q2", record_qid="Q2", raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "current_source_qid_mismatch" in report["issues"]


def test_current_usage_hold_is_rejected(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions, commercial="hold")
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "source_usage_hold:commercial" in report["issues"]


@pytest.mark.parametrize("second_sha,issue", [
    (None, "raw_entity_qid_mismatch"),
    ("f" * 64, "shared_raw_expected_sha_conflict"),
])
def test_shared_raw_still_checks_each_current_entity_id(tmp_path, second_sha, issue):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    second_sha = second_sha or _sha(raw_bytes)
    record2 = _record(raw, second_sha, "Q2")
    record2_json = json.dumps(record2, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record2_sha = _sha(record2_json.encode("utf-8"))
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?)",
                     ("Q2", f"wd:Q2:{record2_sha}", "B", "b", "en", 2001, 1, 1, "{}"))
        conn.execute("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)",
                     (f"wd:Q2:{record2_sha}", "Q2", record2_sha, record2_json,
                      str(raw.resolve()), second_sha, "2026-10-21T00:00:00+00:00", conditions_sha))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert issue in report["issues"]
    assert report["counts"]["shared_raw_reused"] == 1


def test_record_hash_must_remain_bound_to_current_version_id(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw.write_bytes(b'{"entities":{"Q1":{"id":"Q1"}}}')
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, current_version="wd:Q1:" + "e" * 64)
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "current_version_id_binding_mismatch" in report["issues"]


def test_stale_conditions_receipt_fails_closed(tmp_path):
    conditions = tmp_path / "conditions.json"
    _conditions(conditions)
    value = json.loads(conditions.read_text(encoding="utf-8"))
    value["retrieved_at"] = "2020-01-01T00:00:00+00:00"
    conditions.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    conditions_sha = _sha(conditions.read_bytes())
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "source_conditions_invalid" in report["issues"]


def test_tampered_primary_conditions_snapshot_fails_closed(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    primary = Path(json.loads(conditions.read_text(encoding="utf-8"))["policy_snapshot"]["path"])
    primary.write_bytes(b"tampered terms")
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "source_conditions_invalid" in report["issues"]


def test_metadata_only_scope_is_required(tmp_path):
    conditions = tmp_path / "conditions.json"
    _conditions(conditions)
    value = json.loads(conditions.read_text(encoding="utf-8"))
    value["metadataonly_scope"] = False
    conditions.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    conditions_sha = _sha(conditions.read_bytes())
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    report = audit.audit_sources(db)
    assert report["status"] == "violations"
    assert "source_conditions_invalid" in report["issues"]


def test_output_is_create_only(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    output = tmp_path / "audit.json"
    output.write_text("sentinel", encoding="utf-8")
    with pytest.raises(FileExistsError):
        audit.audit_sources(db, output=output)
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_public_output_does_not_include_raw_paths_or_body(tmp_path):
    conditions = tmp_path / "conditions.json"
    conditions_sha = _conditions(conditions)
    raw = tmp_path / "raw.json"
    raw_bytes = b'{"entities":{"Q1":{"id":"Q1"}}}'
    raw.write_bytes(raw_bytes)
    db = tmp_path / "catalogue.sqlite"
    _db(db, conditions=conditions, conditions_sha=conditions_sha,
        raw_path=raw, raw_sha=_sha(raw_bytes))
    output = tmp_path / "audit.json"
    audit.audit_sources(db, output=output)
    rendered = output.read_text(encoding="utf-8")
    assert str(raw) not in rendered
    assert raw_bytes.decode("utf-8") not in rendered
