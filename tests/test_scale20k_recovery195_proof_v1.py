import importlib
import json
import sqlite3

import pytest


def test_process_pair_rejects_wrong_pid_birth_and_nonzero_exit(tmp_path):
    m = importlib.import_module("recommend_book.scale20k_recovery195_v1")
    start = {"pid": 12, "process_created_at": "2026-10-24T03:00:00+00:00"}
    end = {**start, "actual_exit_code": 0, "process_exit_observed": True}
    m.check_process_pair(start, end, 0)
    for changed in ({"pid": 13}, {"process_created_at": "2026-10-24T03:00:01+00:00"},
                    {"actual_exit_code": 1}, {"process_exit_observed": False}):
        with pytest.raises(ValueError):
            m.check_process_pair(start, {**end, **changed}, 0)


def test_pinned_bytes_reject_tampering(tmp_path):
    m = importlib.import_module("recommend_book.scale20k_recovery195_v1")
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps({"value": 1}), encoding="utf-8")
    pin = {"path": str(p), "sha256": m.sha(p)}
    assert m.read_pin(pin) == {"value": 1}
    p.write_text(json.dumps({"value": 2}), encoding="utf-8")
    with pytest.raises(ValueError):
        m.read_pin(pin)


def test_failed_authorities_must_be_absent_from_current_catalogue(tmp_path):
    m = importlib.import_module("recommend_book.scale20k_recovery195_v1")
    database = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE works(qid TEXT PRIMARY KEY, display_allowed INTEGER)")
        db.execute("CREATE TABLE native_classifications(qid TEXT PRIMARY KEY)")
        db.execute("INSERT INTO works VALUES ('Q1',1)")
        db.execute("INSERT INTO native_classifications VALUES ('Q1')")
    assert m.check_catalogue(database, {"Q2"}, minimum=1) == 1
    with pytest.raises(ValueError):
        m.check_catalogue(database, {"Q1"}, minimum=1)
    with pytest.raises(ValueError):
        m.check_catalogue(database, {"Q2"}, minimum=2)
