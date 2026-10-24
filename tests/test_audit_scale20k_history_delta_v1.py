"""Offline boundary tests for the bounded v20 history delta audit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_scale20k_history_delta_v1.py"


def module():
    spec = importlib.util.spec_from_file_location("audit20k_history_delta_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_record(qid, *, identifiers=None):
    return {
        "source": "wikidata", "source_id": qid, "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved_at": "2026-10-22T00:00:00Z", "provider_id": "wikidata", "record_kind": "work",
        "titles": [{"value": f"Fixture {qid}"}], "contributors": [],
        "identifiers": identifiers if identifiers is not None else [{"source": "wikidata", "value": qid, "scope": "work"}],
        "work_refs": [], "original_language": "en", "language": "en", "first_publication_year": 1900,
        "publication_year": None, "publisher": None, "series": None, "volume": None, "work_kind": "novel",
        "region": [], "subjects": [], "evidence": [], "contents": [], "raw_sha256": "a" * 64,
        "raw_path": f"/fixture/{qid}.json", "rights_policy_id": "wikidata-cc0", "extra": {},
    }


def fixture(tmp_path):
    baseline = tmp_path / "baseline-native-10000.jsonl"
    baseline_raw = encoded({"qid": "Q1", "row_sha256": "f" * 64}) + b"\n"
    baseline.write_bytes(baseline_raw)
    database = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT, display_allowed INTEGER,
                recommendable INTEGER, language TEXT, year INTEGER, row_json TEXT);
            CREATE TABLE source_versions(qid TEXT, version_id TEXT, record_sha256 TEXT, record_json TEXT);
            CREATE TABLE native_classifications(qid TEXT PRIMARY KEY, manifest_path TEXT,
                manifest_sha256 TEXT, labels_json TEXT, provenance_json TEXT);
        """)
        for qid in ("Q1", "Q2", "Q3", "Q4", "Q5"):
            record = source_record(qid)
            db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?)",
                       (qid, "v" + qid, 1, 0, "en", 1900, json.dumps({"title": f"Fixture {qid}"})))
            record_json = json.dumps(record)
            db.execute("INSERT INTO source_versions VALUES(?,?,?,?)",
                       (qid, "v" + qid, digest(record_json.encode()), record_json))
            db.execute("INSERT INTO native_classifications VALUES(?,?,?,?,?)",
                       (qid, "/fixture/manifest.json", "c" * 64, "[]", json.dumps({"native_result": {}})))
    owners = [
        {"ncode": "n0001aa", "record_kind": "work", "identity_scope_verified": True,
         "provider_work_ids": [{"source": "wikidata", "value": "Q2", "scope": "work"}]},
        {"ncode": "n0002aa", "record_kind": "work", "identity_scope_verified": True,
         "provider_work_ids": [{"source": "wikidata", "value": "Q2", "scope": "work"},
                                {"source": "wikidata", "value": "Q3", "scope": "work"}]},
        # An edition-scoped identifier must not become a same-work gold link.
        {"ncode": "n0003aa", "record_kind": "work", "identity_scope_verified": True,
         "provider_work_ids": [{"source": "wikidata", "value": "Q4", "scope": "edition"}]},
        # Matching title-like data is intentionally ignored by the delta audit.
        {"ncode": "n0004aa", "title": "Fixture Q5"},
    ]
    return database, baseline, owners


def test_structured_scope_ambiguity_and_unavailable_denominator(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    report = m.audit_delta(database=database, baseline_native=baseline, baseline_sha256=digest(baseline.read_bytes()),
                           owner_history=owners, expected_owner_count=4)
    assert report["status"] == "complete"
    assert report["counts"] == {
        "baseline_native_n": 1, "current_native_n": 5, "current_display_native_n": 5,
        "new_native_delta_n": 4, "new_display_native_delta_n": 4,
        "baseline_source_rows_decoded_n": 0, "new_source_rows_decoded_n": 4,
        "owner_rows_scanned_n": 4, "new_same_work_relation_n": 1,
        "new_same_work_relation_owner_rows_n": 1, "new_same_work_relation_catalogue_qids_n": 1,
        "new_relation_candidate_owner_rows_n": 1, "new_relation_candidate_catalogue_qids_n": 1,
        "ambiguous_owner_rows_n": 1, "receipt_held_owner_rows_n": 0,
        "scope_held_owner_rows_n": 1, "owner_scope_eligible_n": 3, "unmatched_owner_rows_n": 1,
    }
    assert report["comparison"]["status"] == "unassessed"
    assert report["comparison"]["complete"] is False
    assert report["comparison"]["relation_eligible_n"] == 1
    assert report["comparison"]["comparison_eligible_n"] is None
    assert report["new_relation_matches"][0]["catalogue_qid"] == "Q2"
    assert "n0001aa" not in json.dumps(report)
    assert "Fixture Q5" not in json.dumps(report)


def test_baseline_sha_mismatch_fails_closed_without_delta_scan(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    report = m.audit_delta(database=database, baseline_native=baseline, baseline_sha256="0" * 64,
                           owner_history=owners, expected_owner_count=4)
    assert report["status"] == "violations"
    assert "SHA256" in report["issues"][0]
    assert "counts" not in report


def test_missing_source_join_is_a_violation_not_an_inferred_relation(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM source_versions WHERE qid='Q3'")
    report = m.audit_delta(database=database, baseline_native=baseline, baseline_sha256=digest(baseline.read_bytes()),
                           owner_history=owners, expected_owner_count=4)
    # A missing source-version join is a malformed final input, not an
    # opportunity to infer a relation from the remaining rows.
    assert report["status"] == "violations"
    assert "source_versions join" in report["issues"][0]


def test_source_record_sha_pin_tamper_is_a_violation(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE source_versions SET record_json=? WHERE qid='Q2'", ("{}",))
    report = m.audit_delta(database=database, baseline_native=baseline,
                           baseline_sha256=digest(baseline.read_bytes()), owner_history=owners,
                           expected_owner_count=4)
    assert report["status"] == "violations"
    assert "record_sha256 pin differs" in report["issues"][0]


def test_verified_saved_relation_receipt_is_a_candidate_but_not_a_completed_comparison(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    receipt_path = tmp_path / "same-work-receipt.json"
    receipt_raw = encoded({"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                           "relation": "same_work", "scope": "work", "verified": True})
    receipt_path.write_bytes(receipt_raw)
    owners[3].update({"record_kind": "work", "identity_scope_verified": True,
                      "same_work_receipts": [{"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                                               "relation": "same_work", "scope": "work", "verified": True,
                                               "receipt_path": str(receipt_path),
                                               "receipt_sha256": digest(receipt_raw)}]})
    report = m.audit_delta(database=database, baseline_native=baseline,
                           baseline_sha256=digest(baseline.read_bytes()), owner_history=owners,
                           expected_owner_count=4)
    assert report["status"] == "complete"
    assert report["comparison"]["status"] == "unassessed"
    assert report["comparison"]["complete"] is False
    assert report["comparison"]["relation_eligible_n"] == 1
    assert report["counts"]["receipt_held_owner_rows_n"] == 1
    assert report["counts"]["new_relation_candidate_owner_rows_n"] == 2
    assert report["counts"]["new_relation_candidate_catalogue_qids_n"] == 2
    assert {item["catalogue_qid"] for item in report["new_relation_matches"]} == {"Q2"}


def test_fake_receipt_hash_does_not_create_a_gold_relation(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    receipt_path = tmp_path / "same-work-receipt.json"
    receipt_raw = encoded({"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                           "relation": "same_work", "scope": "work", "verified": True})
    receipt_path.write_bytes(receipt_raw)
    owners[3].update({"record_kind": "work", "identity_scope_verified": True,
                      "same_work_receipts": [{"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                                               "relation": "same_work", "scope": "work", "verified": True,
                                               "receipt_path": str(receipt_path),
                                               "receipt_sha256": "d" * 64}]})
    report = m.audit_delta(database=database, baseline_native=baseline,
                           baseline_sha256=digest(baseline.read_bytes()), owner_history=owners,
                           expected_owner_count=4)
    assert report["status"] == "complete"
    assert report["comparison"]["relation_eligible_n"] == 1
    assert {item["catalogue_qid"] for item in report["new_relation_matches"]} == {"Q2"}


def test_receipt_dispatch_is_the_only_path_from_held_to_relation(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    receipt_path = tmp_path / "same-work-receipt.json"
    receipt_raw = encoded({"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                           "relation": "same_work", "scope": "work", "verified": True})
    receipt_path.write_bytes(receipt_raw)
    owners[3].update({"record_kind": "work", "identity_scope_verified": True,
                      "same_work_receipts": [{"owner_serial_id": "n0004aa", "catalogue_qid": "Q5",
                                               "relation": "same_work", "scope": "work", "verified": True,
                                               "receipt_path": str(receipt_path),
                                               "receipt_sha256": digest(receipt_raw)}]})
    calls = []
    def genuine_verifier(receipt, payload, raw):
        calls.append(payload)
        return True
    report = m.audit_delta(database=database, baseline_native=baseline,
                           baseline_sha256=digest(baseline.read_bytes()), owner_history=owners,
                           expected_owner_count=4,
                           receipt_verifier=genuine_verifier)
    assert len(calls) == 1
    assert report["comparison"]["relation_eligible_n"] == 2
    assert {item["catalogue_qid"] for item in report["new_relation_matches"]} == {"Q2", "Q5"}


def test_no_exact_relation_keeps_zero_comparison_denominator(tmp_path):
    m = module()
    database, baseline, owners = fixture(tmp_path)
    owners[0].pop("provider_work_ids")
    report = m.audit_delta(database=database, baseline_native=baseline,
                           baseline_sha256=digest(baseline.read_bytes()), owner_history=owners,
                           expected_owner_count=4)
    assert report["counts"]["new_same_work_relation_n"] == 0
    assert report["comparison"]["status"] == "unavailable"
    assert report["comparison"]["comparison_eligible_n"] == 0


def test_owner_roster_loader_requires_exact_bytes_pin(tmp_path):
    m = module()
    path = tmp_path / "owners.jsonl"
    raw = b'{"ncode":"n0001aa"}\n'
    path.write_bytes(raw)
    rows, observed = m._load_history(path, digest(raw))
    assert rows == [{"ncode": "n0001aa"}]
    assert observed == digest(raw)
    try:
        m._load_history(path, "0" * 64)
    except m.AuditInputError as exc:
        assert "owner roster SHA256" in str(exc)
    else:
        raise AssertionError("owner roster hash mismatch was accepted")
