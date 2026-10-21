"""Focused fixtures for the final v19 read-only catalogue audit."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_scale100k_catalogue_v1", ROOT / "scripts/audit_scale100k_catalogue_v1.py"
)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


def _labels(axis="theme", evidence_id="content-1"):
    return [{
        "axis": axis,
        "label_id": f"{axis}.family",
        "decision": "present",
        "scope": "work",
        "evidence": [{"evidence_id": evidence_id, "locator": "revision:1;section:Plot"}],
    }]


def _manifest(path: Path, rows: list[dict]) -> str:
    value = {
        "version": "scale-content-native-classification-export-v3",
        "verified_projection": {"rows": rows, "axis_counts": {}},
    }
    path.write_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _public_row(qid="Q1", labels=None, *, content_ids=None, held=None, attributions=None):
    return {
        "qid": qid,
        "admitted_labels": labels if labels is not None else [],
        "content_evidence_ids": content_ids if content_ids is not None else [],
        "label_admission": {"admitted_labels": labels or [], "held_labels": held or []},
        "source_attributions": attributions or [],
    }


def _native_row(qid, manifest_path, manifest_sha, labels):
    labels_json = json.dumps(labels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "qid": qid,
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha,
        "labels_json": labels_json,
        "provenance_json": "{}",
    }


def _native_row_sha(row):
    canonical = {key: row[key] for key in (
        "qid", "manifest_path", "manifest_sha256", "labels_json", "provenance_json"
    )}
    return hashlib.sha256(audit.canonical_json(canonical)).hexdigest()


def _db(path, *, works, sources, native):
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE works(
                qid TEXT PRIMARY KEY, version_id TEXT, display_allowed INTEGER,
                recommendable INTEGER, language TEXT, year INTEGER
            );
            CREATE TABLE source_versions(
                version_id TEXT PRIMARY KEY, qid TEXT, record_sha256 TEXT,
                record_json TEXT, raw_path TEXT, raw_sha256 TEXT,
                retrieved_at TEXT, conditions_sha256 TEXT
            );
            CREATE TABLE native_classifications(
                qid TEXT PRIMARY KEY, manifest_path TEXT, manifest_sha256 TEXT,
                labels_json TEXT, provenance_json TEXT
            );
            """
        )
        db.executemany("INSERT INTO works VALUES(?,?,?,?,?,?)", works)
        db.executemany("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)", sources)
        db.executemany(
            "INSERT INTO native_classifications VALUES(?,?,?,?,?)",
            [
                (row["qid"], row["manifest_path"], row["manifest_sha256"],
                 row["labels_json"], row["provenance_json"])
                for row in native
            ],
        )


def _public_pins(path: Path, sha: str, qid: str, labels) -> dict:
    labels_json = json.dumps(labels, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {str(path.resolve()): {"sha256": sha, "rows": {qid: labels_json}}}


def test_baseline_current_reference_mismatch_is_a_violation(tmp_path):
    manifest = tmp_path / "export.json"
    labels = []
    sha = _manifest(manifest, [_public_row(labels=labels)])
    row = _native_row("Q1", manifest, sha, labels)
    db = tmp_path / "catalogue.sqlite"
    _db(
        db,
        works=[("Q1", "v2", 1, 1, "en", 2000)],
        sources=[("v1", "Q1", "r", "{}", "raw", "r", "now", "c")],
        native=[row],
    )
    report = audit.audit_database(
        db,
        [{"qid": "Q1", "version_id": "v1"}],
        [{"qid": "Q1", "row_sha256": _native_row_sha(row)}],
        limits={"searchable": 1, "recommendable": 1, "native_join": 1, "new_eligible_native": 0},
    )
    assert report["status"] == "violations"
    assert "baseline_current_version_mismatch" in report["issues"]


def test_missing_baseline_native_row_is_a_violation(tmp_path):
    db = tmp_path / "catalogue.sqlite"
    _db(
        db,
        works=[("Q1", "v1", 1, 1, "en", 2000)],
        sources=[("v1", "Q1", "r", "{}", "raw", "r", "now", "c")],
        native=[],
    )
    report = audit.audit_database(
        db, [{"qid": "Q1", "version_id": "v1"}], [{"qid": "Q1", "row_sha256": "a" * 64}],
        limits={"searchable": 1, "recommendable": 1, "native_join": 0, "new_eligible_native": 0},
    )
    assert report["status"] == "violations"
    assert report["baseline_native_rows_missing"] == 1
    assert "baseline_native_rows_missing" in report["issues"]


def test_baseline_native_hash_mismatch_is_a_violation(tmp_path):
    manifest = tmp_path / "export.json"
    sha = _manifest(manifest, [_public_row(labels=[])])
    row = _native_row("Q1", manifest, sha, [])
    db = tmp_path / "catalogue.sqlite"
    _db(
        db,
        works=[("Q1", "v1", 1, 1, "en", 2000)],
        sources=[("v1", "Q1", "r", "{}", "raw", "r", "now", "c")],
        native=[row],
    )
    report = audit.audit_database(
        db, [{"qid": "Q1", "version_id": "v1"}], [{"qid": "Q1", "row_sha256": "b" * 64}],
        limits={"searchable": 1, "recommendable": 1, "native_join": 1, "new_eligible_native": 0},
    )
    assert report["status"] == "violations"
    assert "baseline_native_canonical_row_hash_mismatch" in report["issues"]


def test_uneligible_new_native_does_not_satisfy_threshold(tmp_path):
    manifest = tmp_path / "export.json"
    labels = _labels()
    sha = _manifest(manifest, [_public_row(qid="Qnew", labels=labels, content_ids=["content-1"])])
    row = _native_row("Qnew", manifest, sha, labels)
    db = tmp_path / "catalogue.sqlite"
    _db(
        db,
        works=[("Qnew", "v1", 0, 0, "en", 2020)],
        sources=[("v1", "Qnew", "r", "{}", "raw", "r", "now", "c")],
        native=[row],
    )
    report = audit.audit_database(
        db, [], [],
        limits={"searchable": 0, "recommendable": 0, "native_join": 0, "new_eligible_native": 1},
    )
    assert report["new_native"] == 1
    assert report["new_eligible_native"] == 0
    assert "threshold_not_met" in report["issues"]


def test_sql_labels_must_match_authenticated_v3_projection(tmp_path):
    manifest = tmp_path / "export.json"
    public_labels = _labels("theme", "content-1")
    sha = _manifest(manifest, [_public_row(labels=public_labels, content_ids=["content-1"])])
    sql_labels = _labels("theme", "different-content")
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", sql_labels), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "violations"
    assert "sql_labels_mismatch:Q1" in report["issues"]


def test_same_qid_with_different_admitted_labels_is_a_violation(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first_labels = _labels("genre", "genre-evidence")
    second_labels = _labels("theme", "theme-evidence")
    first_sha = _manifest(first, [_public_row(qid="Q1", labels=first_labels)])
    second_sha = _manifest(second, [_public_row(qid="Q1", labels=second_labels)])
    pins = _public_pins(first, first_sha, "Q1", first_labels)
    pins.update(_public_pins(second, second_sha, "Q1", second_labels))
    report = audit.audit_public_manifests(pins, audit._FileCache(), set(), set())
    assert report["status"] == "violations"
    assert "public_same_qid_labels_mismatch:Q1" in report["issues"]


def test_unknown_axes_are_retained_and_content_binding_is_authenticated(tmp_path):
    manifest = tmp_path / "export.json"
    labels = _labels("theme", "content-1")
    row = _public_row(
        labels=labels,
        content_ids=["content-1"],
        attributions=[{"evidence_ids": ["content-1"], "license": "CC BY-SA 4.0"}],
    )
    sha = _manifest(manifest, [row])
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", labels), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "complete"
    assert report["axis_counts"]["theme"] == {"adopted": 1, "held": 0, "unknown": 0}
    for axis in ("genre", "structure", "protagonist", "style", "atmosphere"):
        assert report["axis_counts"][axis]["unknown"] == 1
    assert report["content_nongenre_qids"] == ["Q1"]


def test_authenticated_v3_projection_can_pass(tmp_path):
    manifest = tmp_path / "export.json"
    labels = _labels("theme", "content-1")
    row = _public_row(
        labels=labels,
        content_ids=["content-1"],
        attributions=[{"evidence_ids": ["content-1"], "license": "CC BY-SA 4.0"}],
    )
    sha = _manifest(manifest, [row])
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", labels), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "complete"
    assert report["rows"] == 1


def test_display_hold_and_native_unknown_are_preserved(tmp_path):
    manifest = tmp_path / "export.json"
    labels = [{"axis": "genre", "label_id": "genre.romance", "decision": "present",
               "evidence": [{"evidence_id": "content-1", "quote": "romantic fiction"}]}]
    row = _public_row(labels=labels, content_ids=["content-1"],
                      attributions=[{"evidence_ids": ["content-1"]}])
    row["native_result"] = {"missing_axes": [{"axis": "genre"}]}
    sha = _manifest(manifest, [row])
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", labels), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "complete"
    assert report["actual_display_feature_works"] == 0
    assert report["axis_counts"]["genre"] == {"adopted": 0, "held": 1, "unknown": 1}


def test_modified_public_bytes_fail_the_database_pin(tmp_path):
    manifest = tmp_path / "export.json"
    sha = _manifest(manifest, [_public_row()])
    manifest.write_bytes(manifest.read_bytes() + b" ")
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", []), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "violations"
    assert any("pinned_manifest_sha_mismatch" in issue for issue in report["issues"])


def test_content_ids_require_actual_source_attributions(tmp_path):
    manifest = tmp_path / "export.json"
    labels = _labels()
    row = _public_row(labels=labels, content_ids=["content-1"])
    row["source_attributions"] = None
    sha = _manifest(manifest, [row])
    report = audit.audit_public_manifests(
        _public_pins(manifest, sha, "Q1", labels), audit._FileCache(), set(), {"Q1"}
    )
    assert report["status"] == "violations"
    assert "public_content_attribution_binding:Q1" in report["issues"]


def test_baseline_native_exports_are_not_reinterpreted(tmp_path):
    row = _native_row("Q1", tmp_path / "historic-metadata-only.json", "a" * 64, [])
    db = tmp_path / "catalogue.sqlite"
    _db(db, works=[("Q1", "v1", 1, 1, "en", 2000)],
        sources=[("v1", "Q1", "r", "{}", "raw", "r", "now", "c")], native=[row])
    report = audit.audit_database(
        db, [{"qid": "Q1", "version_id": "v1"}],
        [{"qid": "Q1", "row_sha256": _native_row_sha(row)}],
        limits={"searchable": 1, "recommendable": 1, "native_join": 1, "new_eligible_native": 0},
    )
    assert report["status"] == "complete"
    assert report["manifest_pins"] == {}
