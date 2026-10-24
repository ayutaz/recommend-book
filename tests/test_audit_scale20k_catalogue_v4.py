"""Offline tests for the recovered final-audit adapter."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_scale20k_catalogue_v4.py"


def module():
    spec = importlib.util.spec_from_file_location("audit20k_v4_test", SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def pin(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def database(path, qids=()):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE native_classifications(qid TEXT)")
        db.execute("CREATE TABLE works(qid TEXT, display_allowed INTEGER)")
        db.executemany("INSERT INTO native_classifications VALUES (?)", [(qid,) for qid in qids])
        db.executemany("INSERT INTO works VALUES (?, 1)", [(qid,) for qid in qids])


def test_recovery_chain_counts_disjoint_absent_authorities(tmp_path):
    m = module()
    entry = pin(tmp_path / "entry.json", {"version": "fixture"})
    db = tmp_path / "catalogue.sqlite"
    database(db)
    recovery = {
        "verified_failed_qids": ["Qfailed"],
        "verified_unstarted_qids": ["Qheld"],
        "verified_current_native": 20000,
    }
    result = m.verify_recovery_chain(entry_path=Path(entry["path"]), entry_sha256=entry["sha256"],
                                     database=db, recovery_verifier=lambda *a, **k: recovery)
    assert result["counts"] == {"failed_qids": 1, "unstarted_qids": 1, "native_rows": 0}
    assert result["model_zero_windows"] == [196, 197]


def test_exact_shortfall_authenticates_seven_selected_rows(tmp_path):
    m = module()
    selected = [{"qid": f"Q{i}", "input_sha256": f"{i:064x}"} for i in range(7)]
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({
        "version": "scale20k-exact-shortfall-v1",
        "run_name": "scale20k-content-11901-12000-v3", "window_index": 212,
        "target_eligible_native": 20000, "eligible_native_count": 19993,
        "remaining": 7, "selected": selected,
        "origin_index_sha256": "ecafd3a14d1718d95f4596c1edbc910dc9cef8b08a71e5ec71e98b207d5d26da",
    }, sort_keys=True), encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"window_index": 212, "selected": 7}), encoding="utf-8")
    result = m.verify_exact_shortfall_receipt(
        receipt_path=receipt, receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        source_manifest=source, source_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_index=None, source_index_sha256=None,
        reservation_path=None, reservation_sha256=None)
    assert result["selected"] == 7
    assert result["unused_source_rows"] == 60


def test_exact_shortfall_accepts_fresh_v4_caller_pins(tmp_path):
    m = module()
    selected = [{"qid": f"Q{i}", "input_sha256": f"{i + 20:064x}"} for i in range(7)]
    origin_index_sha = "b" * 64
    receipt = tmp_path / "receipt-v4.json"
    receipt.write_text(json.dumps({
        "version": "scale20k-exact-shortfall-v1",
        "run_name": "scale20k-content-11901-12000-v4", "window_index": 212,
        "target_eligible_native": 20000, "eligible_native_count": 19993,
        "remaining": 7, "selected": selected,
        "origin_index_sha256": origin_index_sha,
    }, sort_keys=True), encoding="utf-8")
    source = tmp_path / "source-v4.json"
    source.write_text(json.dumps({"window_index": 212, "selected": 7}), encoding="utf-8")
    result = m.verify_exact_shortfall_receipt(
        receipt_path=receipt, receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest(),
        source_manifest=source, source_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_index=None, source_index_sha256=None,
        reservation_path=None, reservation_sha256=None,
        expected_run_name="scale20k-content-11901-12000-v4",
        expected_origin_index_sha256=origin_index_sha)
    assert result["run_name"] == "scale20k-content-11901-12000-v4"


def test_exact_shortfall_binds_index_and_reservation_identity(tmp_path):
    m = module()
    qids = [f"Q{i}" for i in range(7)]
    hashes = [f"{i + 40:064x}" for i in range(7)]
    origin = "c" * 64
    source_dir = tmp_path / "source-v4"
    source_dir.mkdir()
    source = source_dir / "manifest.json"
    source.write_text(json.dumps({"window_index": 212, "selected": 7,
                                  "run_name": "scale20k-content-11901-12000-v4",
                                  "qids": qids, "input_identity": dict(zip(qids, hashes)),
                                  "source_builder_sha256": m.sha256(
                                      m.ROOT / "scripts/prepare_scale20k_content_sources_v5.py")}), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"version": "scale20k-exact-shortfall-v1",
        "run_name": "scale20k-content-11901-12000-v4", "window_index": 212,
        "target_eligible_native": 20000, "eligible_native_count": 19993,
        "remaining": 7, "selected": [{"qid": q, "input_sha256": h} for q, h in zip(qids, hashes)],
        "origin_index_sha256": origin}), encoding="utf-8")
    receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"selected": 7, "windows": [{
        "window_index": 212, "run_name": "scale20k-content-11901-12000-v4", "selected": 7,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_builder_sha256": source.read_text(encoding="utf-8") and m.sha256(
            m.ROOT / "scripts/prepare_scale20k_content_sources_v5.py")}],
        "exact_shortfall_receipt": {"path": str(receipt), "sha256": receipt_sha},
        "additional_code_pins": {str(m.FINAL_DRIVER_ENTRY.resolve()): m.FINAL_DRIVER_ENTRY_SHA256}}), encoding="utf-8")
    index_sha = hashlib.sha256(index.read_bytes()).hexdigest()
    reservation = tmp_path / "reservation.json"
    reservation.write_text(json.dumps({"global_index": 212,
        "run_name": "scale20k-content-11901-12000-v4", "source_path": str(source_dir),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_index_path": str(index), "source_index_sha256": index_sha,
        "qids": qids, "input_hashes": hashes, "actual_overlap_count": 0}), encoding="utf-8")
    result = m.verify_exact_shortfall_receipt(
        receipt_path=receipt, receipt_sha256=receipt_sha, source_manifest=source,
        source_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_index=index, source_index_sha256=index_sha,
        reservation_path=reservation, reservation_sha256=hashlib.sha256(reservation.read_bytes()).hexdigest(),
        expected_origin_index_sha256=origin)
    assert result["reservation"]["sha256"] == hashlib.sha256(reservation.read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["overlap", "reappeared"])
def test_recovery_chain_rejects_unsafe_authority_state(tmp_path, kind):
    m = module()
    entry = pin(tmp_path / "entry.json", {"version": "fixture"})
    db = tmp_path / "catalogue.sqlite"
    database(db, ["Qfailed"] if kind == "reappeared" else [])
    recovery = {"verified_failed_qids": ["Qfailed"],
                "verified_unstarted_qids": ["Qfailed"] if kind == "overlap" else ["Qheld"]}
    with pytest.raises(ValueError):
        m.verify_recovery_chain(entry_path=Path(entry["path"]), entry_sha256=entry["sha256"],
                                database=db, recovery_verifier=lambda *a, **k: recovery)


def hold_document(tmp_path, selected=2):
    m = module()
    db = tmp_path / "catalogue.sqlite"
    database(db)
    processes = {}
    for name, code in (("preflight", 0), ("coordinator", 1), ("worker", 1), ("observer", 1)):
        birth = "2026-10-24T18:00:00+09:00"
        start = pin(tmp_path / f"{name}-start.json", {"pid": 100 + len(processes), "process_created_at": birth})
        exit_pin = pin(tmp_path / f"{name}-exit.json", {
            "pid": 100 + len(processes), "process_created_at": birth,
            "actual_exit_code": code, "process_exit_observed": True})
        processes[name] = {"start": start, "exit": exit_pin}
    stderr = pin(tmp_path / "stderr.txt", {"stderr": "Content continuation window order differs"})
    return m, db, {
        "window_index": 196, "selected": selected,
        "qids": [f"Q{i}" for i in range(selected)],
        "input_hashes": [f"{i:064x}" for i in range(selected)],
        "model_calls": 0, "native_failures": 0,
        "native_rows_for_selected": 0, "same_inputs_retried": 0,
        "eligible_before": 18669, "eligible_after": 18669,
        "processes": processes, "coordinator_stderr": stderr,
        "confirmed_absent_artifacts": [str(tmp_path / "missing-run"), str(tmp_path / "missing-export")],
    }


def test_current_hold_schema_checks_process_sql_and_absence(tmp_path):
    m, db, hold = hold_document(tmp_path)
    result = m._verify_hold_schema(hold, db)
    assert result["window_index"] == 196
    assert result["selected"] == 2
    assert result["catalogue_rows_remaining"] == 0


@pytest.mark.parametrize("change", ["exit", "stderr", "artifact"])
def test_current_hold_schema_rejects_changed_witnesses(tmp_path, change):
    m, db, hold = hold_document(tmp_path)
    if change == "exit":
        exit_path = Path(hold["processes"]["worker"]["exit"]["path"])
        exit_path.write_text(json.dumps({"actual_exit_code": 0}), encoding="utf-8")
    elif change == "stderr":
        Path(hold["coordinator_stderr"]["path"]).write_text("changed", encoding="utf-8")
    else:
        Path(hold["confirmed_absent_artifacts"][0]).write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError):
        m._verify_hold_schema(hold, db)


def cli_hold_fixture(tmp_path):
    m = module()
    db = tmp_path / "catalogue.sqlite"
    database(db)
    qids = [f"Q{i}" for i in range(7)]
    hashes = [f"{i + 10:064x}" for i in range(7)]
    reservation = pin(tmp_path / "reservation.json", {
        "global_index": 212, "run_name": "scale20k-content-11901-12000-v3",
        "qids": qids, "input_hashes": hashes, "actual_overlap_count": 0})
    source = pin(tmp_path / "source.json", {"window_index": 212, "selected": 7})
    index = pin(tmp_path / "index.json", {"windows": [212]})
    context = pin(tmp_path / "context.json", {"cli": "codex-cli 0.163.0"})
    processes = {}
    codes = {"preflight": 0, "coordinator": 0, "native": 1, "worker": 1, "observer": 1}
    for name, code in codes.items():
        birth = "2026-10-24T18:00:00+09:00"
        start = pin(tmp_path / f"{name}-start.json", {"pid": 500 + len(processes), "process_created_at": birth})
        exit_pin = pin(tmp_path / f"{name}-exit.json", {"pid": 500 + len(processes),
            "process_created_at": birth, "actual_exit_code": code, "process_exit_observed": True})
        processes[name] = {"start": start, "exit": exit_pin}
    stderr = tmp_path / "stderr.txt"
    stderr.write_bytes(b"runner rejected observed CLI/config")
    stdout = tmp_path / "stdout.txt"
    stdout.write_bytes(b"")
    stderr_pin = {"path": str(stderr), "sha256": hashlib.sha256(stderr.read_bytes()).hexdigest()}
    stdout_pin = {"path": str(stdout), "sha256": hashlib.sha256(stdout.read_bytes()).hexdigest()}
    hold = {"version": "scale20k-unstarted-cli-precheck-hold-v1",
            "status": "held_unstarted_cli_context_mismatch", "window_index": 212,
            "run_name": "scale20k-content-11901-12000-v3", "selected": 7,
            "qids": qids, "input_hashes": hashes, "reservation": reservation,
            "source_manifest": source, "source_index": index,
            "prepared_context": context, "processes": processes,
            "native_stderr": stderr_pin, "native_stdout": stdout_pin,
            "expected_cli_version": "codex-cli 0.163.0", "observed_cli_version": "codex-cli 0.164.0",
            "model_calls": 0, "native_failures": 0, "native_rows_for_selected": 0,
            "same_inputs_retried": 0, "confirmed_absent_artifacts": [str(tmp_path / "run")],
            "eligible_before": 19993, "eligible_after": 19993}
    hold_path = tmp_path / "hold.json"
    hold_path.write_text(json.dumps(hold, sort_keys=True), encoding="utf-8")
    return m, db, hold_path, hold


def test_cli212_hold_is_separate_from_native_failures(tmp_path):
    m, db, hold_path, hold = cli_hold_fixture(tmp_path)
    result = m.verify_cli212_hold(hold_path=hold_path,
                                  hold_sha256=hashlib.sha256(hold_path.read_bytes()).hexdigest(),
                                  database=db)
    assert result["selected"] == 7
    assert result["classification"] == "pre_model_cli_context_mismatch"
    assert result["native_failures"] == 0


def test_cli212_hold_rejects_model_or_cli_mutation(tmp_path):
    m, db, hold_path, hold = cli_hold_fixture(tmp_path)
    hold["model_calls"] = 1
    hold_path.write_text(json.dumps(hold, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError):
        m.verify_cli212_hold(hold_path=hold_path,
                             hold_sha256=hashlib.sha256(hold_path.read_bytes()).hexdigest(),
                             database=db)


def test_current_manifest_verifier_keeps_chain_and_delegates_archive(tmp_path, monkeypatch):
    m = module()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": "fixture"}), encoding="utf-8")
    chain = tmp_path / "coordinator.bin"
    chain.write_bytes(b"coordinator-chain")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    chain_sha = hashlib.sha256(chain.read_bytes()).hexdigest()

    class Frozen:
        @staticmethod
        def pinned_json(path, expected):
            assert expected == manifest_sha
            return json.loads(Path(path).read_text(encoding="utf-8"))

        @staticmethod
        def sha(path):
            return hashlib.sha256(Path(path).read_bytes()).hexdigest()

        @staticmethod
        def verify_public_manifest(*args):
            raise AssertionError("the archive adapter should be selected")

    import recommend_book.scale_coordinator_proof_v1 as proof
    monkeypatch.setattr(proof, "coordinator_chain_pins",
                        lambda document: [{"path": str(chain), "sha256": chain_sha}])
    calls = []

    def delegated(path, expected, archive_root):
        calls.append((Path(path), expected, Path(archive_root)))
        return {"adapter": "archive"}

    verify = m._current_manifest_verifier(Frozen(), archive_root=tmp_path,
                                          archive_verifier=delegated)
    assert verify(manifest, manifest_sha) == {"adapter": "archive"}
    assert calls == [(manifest.resolve(), manifest_sha, tmp_path.resolve())]


def test_archive_projection_context_records_current_pins(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "CLI_ARCHIVE_ROOT", tmp_path)
    context = m._archive_projection_context()
    assert context["projection"]["sha256"] == m.CLI_ARCHIVE_PROJECTION_SHA256
    assert context["helper"]["sha256"] == m.CLI_ARCHIVE_HELPER_SHA256
    assert Path(context["archive_root"]).is_dir()


def test_final_display_observation_accepts_nonrecommendable_rows(tmp_path, monkeypatch):
    m = module()
    export = tmp_path / "manifest.json"
    export.write_text(json.dumps({"verified_projection": {
        "rows": [{"qid": "Q1", "admitted_labels": []}]}}, sort_keys=True), encoding="utf-8")
    export_sha = hashlib.sha256(export.read_bytes()).hexdigest()
    database_path = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(database_path) as db:
        db.execute("CREATE TABLE native_classifications(qid TEXT, manifest_sha256 TEXT, labels_json TEXT)")
        db.execute("CREATE TABLE works(qid TEXT, display_allowed INTEGER, recommendable INTEGER)")
        db.execute("INSERT INTO native_classifications VALUES ('Q1', ?, '[]')", (export_sha,))
        db.execute("INSERT INTO works VALUES ('Q1', 1, 0)")

    import recommend_book.scale_content_control_v4 as control
    monkeypatch.setattr(control, "display_policy_sha256", lambda: "policy")
    monkeypatch.setattr(control, "display_counts",
                        lambda rows: {"policy_sha256": "policy", "rows": len(rows)})
    result = m._observe_final_catalogue_display(database_path, export, export_sha)
    assert result["matched_display_works"] == 1
    assert result["recommendable_filter_applied"] is False


def test_final_v4_export_uses_dedicated_control_dispatch(tmp_path, monkeypatch):
    m = module()
    legacy = type("Legacy", (), {"database": tmp_path / "catalogue.sqlite"})()
    called = {}

    def fake(export, expected, database):
        called.update(export=str(export), expected=expected, database=database)
        return {"final_v4": True}

    monkeypatch.setattr(m, "_verify_final_v4_control", fake)
    verify = m._legacy_control_verifier(None, None, legacy, {"prior_control": {}})
    export = tmp_path / "scale20k-content-11901-12000-v4" / "manifest.json"
    result = verify(export, "f" * 64)
    assert result == {"final_v4": True}
    assert called["expected"] == "f" * 64


def test_final_historical_snapshot_requires_v7_parent_and_witnesses(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "BASE", tmp_path)
    run_name = "scale20k-content-11901-12000-v4"
    context_path = tmp_path / "operational-processes" / run_name / "new-code-context.json"
    stopped = context_path.parent / "preflight"
    child = tmp_path / "final-shortfall-v2" / "actual-child-preparation-v1"
    stopped.mkdir(parents=True)
    child.mkdir(parents=True)
    expected = {
        m.FINAL_COORDINATOR: m.FINAL_COORDINATOR_SHA256,
        m.FINAL_COORDINATOR_PARENT: m.FINAL_COORDINATOR_PARENT_SHA256,
        m.FINAL_DRIVER: m.FINAL_DRIVER_SHA256,
        m.FINAL_DRIVER_ENTRY: m.FINAL_DRIVER_ENTRY_SHA256,
        m.FINAL_CHILD_HELPER: m.FINAL_CHILD_HELPER_SHA256,
        m.ROOT / "scripts/prepare_scale20k_content_sources_v5.py":
            m.sha256(m.ROOT / "scripts/prepare_scale20k_content_sources_v5.py"),
    }
    witnesses = [
        (child / "process-start.json", {"pid": 1}),
        (child / "process-exit.json", {"actual_exit_code": 0, "process_exit_observed": True}),
        (child / "request.json", {"helper_sha256": m.FINAL_CHILD_HELPER_SHA256,
                                   "first_model_call_pending": True,
                                   "prior_preparation_model_calls": 0}),
        (child / "stdout.txt", {}), (child / "stderr.txt", {}),
        (stopped / "process-start.json", {"pid": 2}),
        (stopped / "process-exit.json", {"actual_exit_code": 1, "process_exit_observed": True}),
        (stopped / "request.json", {"final_driver_entry_sha256": m.FINAL_DRIVER_ENTRY_SHA256,
                                     "final_continuation_sha256": m.FINAL_DRIVER_SHA256}),
        (stopped / "stdout.txt", {}), (stopped / "stderr.txt", {"error": "preflight"}),
    ]
    for path, value in witnesses:
        path.write_text(json.dumps(value), encoding="utf-8")
    first = tmp_path / "actual-first-native-and-review-v1"
    first.mkdir()
    first_export = tmp_path / run_name / "manifest.json"
    first_export.parent.mkdir()
    first_export.write_text("{}", encoding="utf-8")
    first_request = {
        "argv": ["python", str(m.FINAL_COORDINATOR), "--source-preparation", "source",
                 "--route-index", "212", "--control-receipt-sha256",
                 "c3ea6b531886a34c917df34960a443a98fe8669cb3700e17f13a20afa2aada1d"],
        "coordinator_sha256": m.FINAL_COORDINATOR_SHA256,
        "first_model_calls_for_this_reservation": True,
        "prior_failed_preparation_model_calls": 0,
        "source_manifest_sha256": "m" * 64,
    }
    first_start = {"pid": 8, "process_created_at": "birth"}
    first_exit = {"pid": 8, "process_created_at": "birth", "actual_exit_code": 0,
                  "process_exit_observed": True}
    first_stdout = {"stage": "actual_native_export_verified", "export_manifest": str(first_export),
                    "export_manifest_sha256": hashlib.sha256(first_export.read_bytes()).hexdigest(),
                    "selected": 7, "completed": 7, "failed": 0, "pending": 0, "cli_invocations": 7,
                    "gate": {"passed": True, "selected": 7, "failed": 0, "pending": 0}}
    first_values = {"request.json": first_request, "process-start.json": first_start,
                    "process-exit.json": first_exit, "stdout.txt": first_stdout}
    first_hashes = {}
    for name, value in first_values.items():
        path = first / name
        path.write_text(json.dumps(value), encoding="utf-8")
        first_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (first / "stderr.txt").write_bytes(b"")
    first_hashes["stderr.txt"] = hashlib.sha256((first / "stderr.txt").read_bytes()).hexdigest()
    snapshot = {str(path.resolve()): digest for path, digest in expected.items()}
    for path, _ in witnesses:
        snapshot[str(path.resolve())] = m.sha256(path)
    context_path.parent.mkdir(parents=True, exist_ok=True)
    context_path.write_text(json.dumps({"coordinator_sha256": m.FINAL_COORDINATOR_SHA256,
                                        "artifact_sha256": snapshot}), encoding="utf-8")
    result = m._verify_final_historical_snapshot(
        {"artifact_sha256": snapshot}, run_name, source_manifest_sha256="m" * 64,
        first_native_proof=first, first_native_sha256=first_hashes)
    assert result["parent_coordinator_sha256"] == m.FINAL_COORDINATOR_PARENT_SHA256


def test_final_historical_snapshot_rejects_live_only_public_map(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "BASE", tmp_path)
    run_name = "scale20k-content-11901-12000-v4"
    context = tmp_path / "operational-processes" / run_name / "new-code-context.json"
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"coordinator_sha256": m.FINAL_COORDINATOR_SHA256,
                                   "artifact_sha256": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        m._verify_final_historical_snapshot({"artifact_sha256": {}}, run_name)
