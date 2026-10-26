"""Synthetic contract tests for the world-novel publication adapter.

No production catalogue, model process, network, or frozen publication driver
is executed by these tests.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module():
    path = ROOT / "scripts" / "publish_world_novel_expansion_v1.py"
    spec = importlib.util.spec_from_file_location("publish_world_novel_expansion_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_db(path, *, qid="Q1", genre=None, recommendable=0, native=None):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT,
                display_allowed INTEGER, recommendable INTEGER);
            CREATE TABLE work_genres(qid TEXT, genre_qid TEXT, label TEXT);
            CREATE TABLE native_classifications(qid TEXT PRIMARY KEY,
                manifest_path TEXT, manifest_sha256 TEXT, labels_json TEXT,
                provenance_json TEXT);
        """)
        db.execute("INSERT INTO works VALUES(?,?,?,?)", (qid, "v1", 1, recommendable))
        if genre:
            db.execute("INSERT INTO work_genres VALUES(?,?,?)", (qid, genre, "comedy"))
        if native:
            db.execute("INSERT INTO native_classifications VALUES(?,?,?,?,?)",
                       (qid, native["manifest_path"], native["manifest_sha256"],
                        json.dumps(native["labels"]), "{}"))


def write_export(path, *, qid="Q1", version="scale-content-native-classification-export-v3"):
    value = {"version": version, "verified_projection": {"rows": [
        {"qid": qid, "admitted_labels": [{"axis": "content", "label_id": "L1",
                                             "decision": "present", "scope": "work", "evidence": []}]}]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return value


def test_unknown_source_genre_is_admitted_when_display_authorized(tmp_path):
    module = load_module()
    db = tmp_path / "catalogue.sqlite"
    make_db(db)
    before = module.catalogue_snapshot(db, ["Q1"])
    module.require_new_display_works(before)
    assert module.genre_counts(before) == {"source_genre_known": 0, "source_genre_unknown": 1}


def test_known_genre_is_kept_in_source_snapshot(tmp_path):
    module = load_module()
    db = tmp_path / "catalogue.sqlite"
    make_db(db, genre="Q8253", recommendable=0)
    before = module.catalogue_snapshot(db, ["Q1"])
    module.require_new_display_works(before)
    assert before["Q1"]["genres"] == [["Q8253", "comedy"]]


def test_mutated_metadata_and_native_overwrite_are_rejected(tmp_path):
    module = load_module()
    db = tmp_path / "catalogue.sqlite"
    make_db(db)
    before = module.catalogue_snapshot(db, ["Q1"])
    after = json.loads(json.dumps(before))
    after["Q1"]["recommendable"] = 1
    row = {"qid": "Q1", "admitted_labels": []}
    with pytest.raises(ValueError, match="metadata changed"):
        module.validate_import_observation(before, after, [row], tmp_path / "manifest.json", "a" * 64)

    existing = {"manifest_path": "old", "manifest_sha256": "b" * 64, "labels": []}
    db2 = tmp_path / "existing.sqlite"
    make_db(db2, native=existing)
    with pytest.raises(ValueError, match="display-authorized"):
        module.require_new_display_works(module.catalogue_snapshot(db2, ["Q1"]))


def test_observed_import_records_policy_and_exact_new_rows(tmp_path, monkeypatch):
    module = load_module()
    db = tmp_path / "catalogue.sqlite"
    make_db(db)
    export = tmp_path / "public" / "manifest.json"
    write_export(export)
    digest = module.state.sha(export)
    base = tmp_path / "delivery"
    monkeypatch.setattr(module.state, "writer_lock", lambda **_: nullcontext())
    policy_sha = "f" * 64

    def fake_command(argv, directory, **kwargs):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        module.state.save(directory / "request.json", {"argv": list(map(str, argv)), "shell": False})
        module.state.save(directory / "process-start.json", {"pid": 123, "process_created_at": None})
        result = {"raw_classified": 1, "adopted": 1, "failed": 0,
                  "display_policy_sha256": policy_sha}
        module.state.save(directory / "stdout.json", result)
        process = {"pid": 123, "actual_exit_code": 0,
                   "process_exit_observed": True, "process_created_at": None,
                   "elapsed_seconds": 0, "timed_out": False}
        module.state.save(directory / "process-exit.json", process)
        with sqlite3.connect(db) as writable:
            writable.execute("INSERT INTO native_classifications VALUES(?,?,?,?,?)",
                             ("Q1", str(export.resolve()), digest, json.dumps(
                                 [{"axis": "content", "label_id": "L1", "decision": "present",
                                   "scope": "work", "evidence": []}]), "{}"))
        return process

    monkeypatch.setattr(module, "observed_command", fake_command)
    report, value = module.observed_import(export, digest, database=db, base=base)
    assert value["stage"] == "actual_catalogue_import_complete"
    assert value["display_policy_sha256"] == policy_sha
    policy = module.state.read(value["admission_policy"])
    assert policy["source_genre_counts"] == {"source_genre_known": 0, "source_genre_unknown": 1}
    assert policy["before_import"]["Q1"]["native"] is None
    assert policy["after_import"]["Q1"]["native"]["manifest_sha256"] == digest
    assert module.state.read(report)["after_import"]["Q1"]["recommendable"] == 0

    publisher = Path(module.__file__).resolve()
    receipt = {"control_artifacts": {
        "export": {"path": str(export.resolve()), "sha256": digest},
        "native_eligibility_policy": {"path": str(Path(value["admission_policy"]).resolve()),
                                       "sha256": module.state.sha(value["admission_policy"])},
        "native_eligibility_import_report": {"path": str(report.resolve()),
                                              "sha256": module.state.sha(report)},
        "native_eligibility_publisher": {"path": str(publisher), "sha256": module.state.sha(publisher)}}}
    module.verify_policy_control(receipt, module.state.read(export), digest)
    Path(value["admission_policy"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Pinned admission policy changed"):
        module.verify_policy_control(receipt, module.state.read(export), digest)


def test_exactly_one_frozen_sql_predicate_is_adapted():
    module = load_module()
    import ast

    tree = ast.parse("def finalize():\n  query = ' AND w.display_allowed=1 AND w.recommendable=1'\n")
    fn = tree.body[0]
    module._replace_sql(fn, " AND w.display_allowed=1 AND w.recommendable=1", " AND w.display_allowed=1")
    assert ast.unparse(tree).count("w.recommendable") == 0
    with pytest.raises(ValueError, match="Exactly one"):
        module._replace_sql(fn, " AND w.display_allowed=1 AND w.recommendable=1", " AND w.display_allowed=1")


def test_frozen_publication_tail_loads_only_after_operation_sha_check(tmp_path):
    module = load_module()
    db = tmp_path / "db.sqlite"
    db.touch()
    callback = object()
    operation = tmp_path / "data" / "autonomous-next-v3" / "scale-30000-v1" / "content-operational-driver-v2.py"
    operation.parent.mkdir(parents=True)
    operation.write_text(
        "def save(path, value): pass\n"
        "def finalize(export, expected_sha):\n"
        "    return ' AND w.display_allowed=1 AND w.recommendable=1'\n",
        encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(module.state, "ROOT", tmp_path)
    monkeypatch.setattr(module.state, "OPERATION_SHA", module.state.sha(operation))
    try:
        publication = module.load_publication_tail(db, callback, base=tmp_path)
    finally:
        monkeypatch.undo()
    assert publication.observed_import is callback
    assert publication.DATABASE == db.resolve()
    assert publication.finalize(None, None) == " AND w.display_allowed=1"


def test_publish_reviews_native_v1_then_calls_patched_publication(tmp_path, monkeypatch):
    module = load_module()
    export = tmp_path / "native" / "manifest.json"
    write_export(export, version="scale-content-native-classification-export-v1")
    digest = module.state.sha(export)
    output = tmp_path / "v3"
    calls = []

    def review(parent, expected, destination):
        calls.append((parent, expected, destination))
        destination.mkdir(parents=True)
        public = destination / "manifest.json"
        write_export(public)
        return {"manifest": str(public), "sha256": module.state.sha(public)}

    class FakePublication:
        def finalize(self, public, expected):
            calls.append(("finalize", public, expected))
            return {"version": "scale-content-window-control-v4"}

    db = tmp_path / "db.sqlite"
    db.touch()
    monkeypatch.setattr(module, "create_reviewed_export", review)
    monkeypatch.setattr(module, "load_publication_tail", lambda database, **kwargs: FakePublication())
    monkeypatch.setattr(module, "verify_policy_control", lambda receipt, public, digest: None)
    result = module.publish(export, digest, output, database=tmp_path / "db.sqlite", base=tmp_path)
    assert result["version"] == "scale-content-window-control-v4"
    assert calls[0][0] == export.resolve()
    assert calls[1][0] == "finalize"
