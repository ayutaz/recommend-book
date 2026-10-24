"""Read-only final audit for the recovered 20k catalogue namespace.

The previous auditors remain immutable.  This adapter authenticates the
gate-195 recovery chain (including the 99 quarantined rows and the two
model-zero holds), then delegates catalogue/baseline/public-row checking to
the frozen v1 auditor.  It adds no database writes, model calls, or network
access.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1"
DATABASE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
AUDIT1 = ROOT / "scripts/audit_scale20k_catalogue_v1.py"
AUDIT1_SHA256 = "3c0c668904259d4784d3ce0d8ec29b13557082b01b0c72b5e0d4efc2d393bbd0"
RECOVERY_ENTRY = BASE / "recovery-gate195-v1/entry-v4.json"
RECOVERY_ENTRY_SHA256 = "db2aa854cba7c248f87e327c2dc77c242e58f54eaadc603eadd6a2f80d3479fe"
RECOVERY_MODULE = "recommend_book.scale20k_recovery195_v3"
EXACT_SHORTFALL_RECEIPT = BASE / "final-shortfall-v2/exact-shortfall-receipt.json"
EXACT_SHORTFALL_RECEIPT_SHA256 = "1895e735963dad9dd83abcd48fc475c4b4fc92ac6e8846c93798ac31f2a02655"
EXACT_SOURCE_MANIFEST = BASE / "classification-sources-final-v2/window-11901-12000-v4/manifest.json"
EXACT_SOURCE_MANIFEST_SHA256 = "b7f8ff6f1eb11fee3b90d5c64128033a939e3cd13213bf5761fa176857a70ef1"
EXACT_SOURCE_INDEX = BASE / "final-shortfall-v2/source-index-exact7-v3.json"
EXACT_SOURCE_INDEX_SHA256 = "37db9e7aacc12d13dd65321c9f5ec36c9443cdc15570e025ae29a7cb8acb55a4"
LEGACY_RECOVERY_ENTRY = BASE / "recovery-gate161-v2/entry-v1.json"
LEGACY_RECOVERY_ENTRY_SHA256 = "f972a2f8e49c2ba7bcb9a4f58b86253d49042f9ff878d77b32b2525f7d969d36"
CURRENT_COORDINATOR = ROOT / "scripts/run_scale20k_content_classification_v5.py"
CURRENT_COORDINATOR_SHA256 = "8ba4ba4dc18c434dedd9e4a0e8fa949e720d8bcfede779c8d3b7c671a0ca6c29"
FINAL_COORDINATOR = ROOT / "scripts/run_scale20k_content_classification_v7.py"
FINAL_COORDINATOR_SHA256 = "983633115024ea7156509e7dc4d18a8bbee86e1eb6d1b03fd9a4e940b1997ed5"
FINAL_COORDINATOR_PARENT = ROOT / "scripts/run_scale20k_content_classification_v6.py"
FINAL_COORDINATOR_PARENT_SHA256 = "41944a867bebb3a62736d852603e4682fa7ac80f59d1374b6202774bf6429f8e"
FINAL_DRIVER = ROOT / "scripts/run_scale20k_final_continuation_v2.py"
FINAL_DRIVER_SHA256 = "5399e646b666fc19aa82b0c34aa8759d1207394bb90770e197e7faed15c00db5"
FINAL_DRIVER_ENTRY = ROOT / "scripts/run_scale20k_final_continuation_v3.py"
FINAL_DRIVER_ENTRY_SHA256 = "97271fa1bd981c1ee684af3cb454bfb65b4ef0fd6f8007e9300e24b67f58cf9b"
FINAL_CHILD_HELPER = ROOT / "scripts/prepare_scale_content_child_v1.py"
FINAL_CHILD_HELPER_SHA256 = "13a4584cfb88c6b67ac028c9506d0c46f0818ec97b9dae530bc00d7e94b78aeb"
FINAL_RESERVATION = BASE / "attempt-reservations/scale20k-content-11901-12000-v4.json"
FINAL_RESERVATION_SHA256 = "fd4bdcc2e74af55d554cdd7eaf4f516dde708a02ab78e458362ab88b1227a69d"
FINAL_FIRST_NATIVE_PROOF = BASE / "final-shortfall-v2/actual-first-native-and-review-v1"
FINAL_FIRST_NATIVE_PROOF_SHA256 = {
    "process-exit.json": "857d30795f7f0f414c43e261afc4a856f10346a3f25de684f75303d2c30caa03",
    "process-start.json": "84f01cafcc14e93b54f261219a926f77bac01b62fe61ee142d4d5605e52e620b",
    "request.json": "42421ad09e5e28e48e1bbe129def7d5567bab76674125321fe64f0fe668812ea",
    "stderr.txt": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "stdout.txt": "49910158fdce10d758d3847443dca9b2908803e317fb1ceb069738b5c805a847",
}
CLI212_HOLD = BASE / "final-shortfall-v1/native-precheck-hold-v1.json"
CLI212_HOLD_SHA256 = "5a3926066d3d841fc710eb41b38d9467cc7126e2d8dd040f0538ec4d381bbedd"

# The public exports retain the logical path and SHA of the CLI that produced
# them.  The current host may have a newer binary, so the final audit projects
# the frozen verifier through this content-addressed archive.  These pins are
# part of the audit implementation context; they do not alter any historical
# control, export, or source artifact.
CLI_ARCHIVE_ROOT = ROOT / "data/autonomous-next-v3/reference/cli-artifacts"
CLI_ARCHIVE_PROJECTION = ROOT / "src/recommend_book/classification/native_cli_archive_projection_v1.py"
CLI_ARCHIVE_PROJECTION_SHA256 = "19e42b4963c1786fed4933e74e50cdc86e8d1499ad8ba5f2ad67593546301c96"
CLI_ARCHIVE_HELPER = ROOT / "src/recommend_book/classification/native_cli_archive_v1.py"
CLI_ARCHIVE_HELPER_SHA256 = "04cd20ad12ecbb7eb47a19eb53d7c3bb2778880a535c78c7dea99382e7ad01af"


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _archive_projection_context() -> dict[str, Any]:
    """Authenticate the verifier adapter used by the current audit only."""
    projection = CLI_ARCHIVE_PROJECTION.resolve(strict=True)
    helper = CLI_ARCHIVE_HELPER.resolve(strict=True)
    archive = CLI_ARCHIVE_ROOT.resolve(strict=True)
    observed = {
        str(projection): sha256(projection),
        str(helper): sha256(helper),
    }
    expected = {
        str(projection): CLI_ARCHIVE_PROJECTION_SHA256,
        str(helper): CLI_ARCHIVE_HELPER_SHA256,
    }
    if observed != expected:
        raise ValueError("CLI archive verifier implementation pin differs")
    if not archive.is_dir() or archive.is_symlink():
        raise ValueError("CLI archive root is not a real directory")
    return {
        "projection": {"path": str(projection), "sha256": CLI_ARCHIVE_PROJECTION_SHA256},
        "helper": {"path": str(helper), "sha256": CLI_ARCHIVE_HELPER_SHA256},
        "archive_root": str(archive),
        "read_only": True,
    }


def _current_manifest_verifier(audit1, *, archive_root: Path = CLI_ARCHIVE_ROOT,
                               archive_verifier: Callable[..., dict[str, Any]] | None = None):
    """Keep v1's coordinator-chain checks and use the current archive adapter.

    ``archive_verifier`` is injectable for offline fixtures.  The frozen v1
    verifier object itself remains untouched, so callers that explicitly use
    it retain the original fixture contract.
    """
    original = audit1.verify_public_manifest

    def verify(path: str | Path, expected: str):
        document = audit1.pinned_json(path, expected)
        from recommend_book.scale_coordinator_proof_v1 import coordinator_chain_pins
        for pin in coordinator_chain_pins(document):
            if audit1.sha(pin["path"]) != pin["sha256"]:
                raise ValueError("Actual coordinator chain pin differs")
        if archive_verifier is None:
            _archive_projection_context()
            adapter = importlib.import_module(
                "recommend_book.classification.native_cli_archive_projection_v1")
            result = adapter.verify_catalogue_classification(path, expected,
                                                              Path(archive_root))
        else:
            result = archive_verifier(path, expected, Path(archive_root))
        if audit1.sha(path) != expected:
            raise ValueError("Public manifest changed during verification")
        return result

    return verify


def _load_frozen_audit():
    path = AUDIT1.resolve(strict=True)
    if sha256(path) != AUDIT1_SHA256:
        raise ValueError("Frozen catalogue auditor changed")
    spec = importlib.util.spec_from_file_location("scale20k_audit_v1_frozen", path)
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load frozen catalogue auditor")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_recovery_verifier():
    return importlib.import_module(RECOVERY_MODULE).verify_entry


def _load_legacy_recovery_audit(database: Path):
    """Load audit3's SHA-fixed RecoveryAudit without changing audit3."""
    audit3 = importlib.import_module("scripts.audit_scale20k_catalogue_v3")
    adapted = audit3.adapted_auditor()
    auditor = adapted.RecoveryAudit(str(LEGACY_RECOVERY_ENTRY),
                                    LEGACY_RECOVERY_ENTRY_SHA256,
                                    database=database)
    # audit3 keeps the gate161 unstarted proof outside its adapted class.
    old_hold = audit3.verify_unstarted_hold(database, auditor.recovery["pins"])
    # Return the in-memory adapted module: its parent source owns the actual
    # ``canonical_name(index, generation='v3')`` API used for adjacent windows.
    # The outer v3 file remains byte-for-byte untouched.
    return adapted, auditor, old_hold


def verify_current_gap(*, entry_path: Path = RECOVERY_ENTRY,
                       entry_sha256: str = RECOVERY_ENTRY_SHA256,
                       database: Path = DATABASE) -> dict[str, Any]:
    """Verify the 195/196/197 chain and the genuine 194 -> 198 gap."""
    helper = importlib.import_module(RECOVERY_MODULE)
    entry = helper.verify_entry(str(Path(entry_path).resolve(strict=True)),
                                entry_sha256, database=Path(database).resolve(strict=True))
    prior = entry["prior_control"]
    helper.verify_resume_route(198, prior["path"], prior["sha256"],
                               str(Path(entry_path).resolve(strict=True)), entry_sha256)
    coordinator = CURRENT_COORDINATOR.resolve(strict=True)
    if sha256(coordinator) != CURRENT_COORDINATOR_SHA256:
        raise ValueError("Coordinator5 code pin differs")
    if entry.get("code_pins", {}).get("scripts/run_scale20k_content_classification_v5.py") != CURRENT_COORDINATOR_SHA256:
        raise ValueError("Recovery entry does not pin coordinator5")
    return {"entry": {"path": str(Path(entry_path).resolve()), "sha256": entry_sha256},
            "prior_control": prior, "first_resume_index": 198,
            "coordinator": {"path": str(coordinator), "sha256": CURRENT_COORDINATOR_SHA256},
            "verified_current_native": entry.get("verified_current_native"),
            "verified_failed_qids": len(entry.get("verified_failed_qids", [])),
            "verified_unstarted_qids": len(entry.get("verified_unstarted_qids", []))}


def verify_cli212_hold(*, hold_path: Path = CLI212_HOLD,
                       hold_sha256: str = CLI212_HOLD_SHA256,
                       database: Path = DATABASE) -> dict[str, Any]:
    """Authenticate the old CLI-mismatch stop as a separate pre-model hold."""
    hold_file = Path(hold_path).resolve(strict=True)
    if sha256(hold_file) != hold_sha256:
        raise ValueError("CLI212 hold SHA differs")
    hold = json.loads(hold_file.read_text(encoding="utf-8"))
    if (hold.get("version") != "scale20k-unstarted-cli-precheck-hold-v1"
            or hold.get("status") != "held_unstarted_cli_context_mismatch"
            or hold.get("window_index") != 212
            or hold.get("run_name") != "scale20k-content-11901-12000-v3"
            or hold.get("selected") != 7
            or hold.get("expected_cli_version") != "codex-cli 0.163.0"
            or hold.get("observed_cli_version") != "codex-cli 0.164.0"
            or any(hold.get(key) != 0 for key in ("model_calls", "native_failures",
                                                   "native_rows_for_selected", "same_inputs_retried"))):
        raise ValueError("CLI212 pre-model hold identity or zero-work facts differ")
    qids, hashes = hold.get("qids"), hold.get("input_hashes")
    if (not isinstance(qids, list) or not isinstance(hashes, list) or len(qids) != 7
            or len(set(qids)) != 7 or len(hashes) != 7 or len(set(hashes)) != 7):
        raise ValueError("CLI212 hold selected identities differ")
    for key in ("reservation", "source_manifest", "source_index", "prepared_context"):
        pin = hold.get(key)
        if not isinstance(pin, dict) or sha256(Path(pin["path"]).resolve(strict=True)) != pin["sha256"]:
            raise ValueError("CLI212 hold artifact pin changed: " + key)
    reservation = json.loads(Path(hold["reservation"]["path"]).read_text(encoding="utf-8"))
    if (reservation.get("global_index") != 212 or reservation.get("run_name") != hold["run_name"]
            or set(reservation.get("qids", [])) != set(qids)
            or set(reservation.get("input_hashes", [])) != set(hashes)
            or reservation.get("actual_overlap_count") != 0):
        raise ValueError("CLI212 reservation identity differs")
    processes = hold.get("processes")
    expected_codes = {"preflight": 0, "coordinator": 0, "native": 1, "worker": 1, "observer": 1}
    if not isinstance(processes, dict) or set(processes) != set(expected_codes):
        raise ValueError("CLI212 process witness set differs")
    for name, pair in processes.items():
        docs = {}
        for side in ("start", "exit"):
            pin = pair.get(side)
            path = Path(pin["path"]).resolve(strict=True)
            if sha256(path) != pin["sha256"]:
                raise ValueError("CLI212 process pin changed: " + name)
            docs[side] = json.loads(path.read_text(encoding="utf-8"))
        start, end = docs["start"], docs["exit"]
        same_process = ((end.get("process_pid", end.get("pid")) == start.get("pid")) if name == "native"
                        else end.get("pid") == start.get("pid")
                        and end.get("process_created_at") == start.get("process_created_at"))
        if (type(start.get("pid")) is not int or start["pid"] <= 0
                or not same_process
                or end.get("actual_exit_code") != expected_codes[name]
                or end.get("process_exit_observed") is not True):
            raise ValueError("CLI212 process witness differs: " + name)
    for key in ("native_stderr", "native_stdout"):
        pin = hold.get(key)
        path = Path(pin["path"]).resolve(strict=True)
        if sha256(path) != pin["sha256"]:
            raise ValueError("CLI212 stream pin changed: " + key)
        if key == "native_stdout" and path.read_bytes() != b"":
            raise ValueError("CLI212 native stdout must be empty")
    if not hold.get("confirmed_absent_artifacts") or any(Path(path).exists() for path in hold["confirmed_absent_artifacts"]):
        raise ValueError("CLI212 acquired a run or public artifact")
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        marks = ",".join("?" for _ in qids)
        remaining = db.execute("SELECT count(*) FROM native_classifications WHERE qid IN (" + marks + ")", sorted(qids)).fetchone()[0]
    if remaining:
        raise ValueError("CLI212 hold authorities acquired native rows")
    return {"window_index": 212, "run_name": hold["run_name"], "selected": 7,
            "qids": sorted(qids), "input_hashes": sorted(hashes), "model_calls": 0,
            "native_failures": 0, "native_rows_for_selected": 0,
            "same_inputs_retried": 0, "catalogue_rows_remaining": remaining,
            "hold": {"path": str(hold_file), "sha256": hold_sha256},
            "classification": "pre_model_cli_context_mismatch"}


def _read_only_exclusion_check(database: Path, recovery: dict[str, Any]) -> dict[str, int]:
    failed = set(recovery.get("verified_failed_qids", ()))
    unstarted = set(recovery.get("verified_unstarted_qids", ()))
    if not failed or not unstarted:
        raise ValueError("Recovery verifier returned no failed or unstarted authorities")
    overlap = failed & unstarted
    if overlap:
        raise ValueError("Failed and unstarted authority sets overlap")
    excluded = sorted(failed | unstarted)
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + "?mode=ro", uri=True) as db:
        remaining = db.execute(
            "SELECT count(*) FROM native_classifications WHERE qid IN ("
            + ",".join("?" for _ in excluded) + ")", excluded).fetchone()[0]
    if remaining:
        raise ValueError("Excluded recovery authorities reappeared in the catalogue")
    return {"failed_qids": len(failed), "unstarted_qids": len(unstarted), "native_rows": remaining}


def verify_exact_shortfall_receipt(*, receipt_path: Path = EXACT_SHORTFALL_RECEIPT,
                                   receipt_sha256: str = EXACT_SHORTFALL_RECEIPT_SHA256,
                                   source_manifest: Path = EXACT_SOURCE_MANIFEST,
                                   source_manifest_sha256: str = EXACT_SOURCE_MANIFEST_SHA256,
                                   source_index: Path | None = EXACT_SOURCE_INDEX,
                                   source_index_sha256: str | None = EXACT_SOURCE_INDEX_SHA256,
                                   reservation_path: Path | None = FINAL_RESERVATION,
                                   reservation_sha256: str | None = FINAL_RESERVATION_SHA256,
                                   expected_run_name: str | None = None,
                                   expected_origin_index_sha256: str = "ecafd3a14d1718d95f4596c1edbc910dc9cef8b08a71e5ec71e98b207d5d26da") -> dict[str, Any]:
    """Authenticate the final 7-row subset instead of assuming a full window."""
    receipt_path = Path(receipt_path).resolve(strict=True)
    if sha256(receipt_path) != receipt_sha256:
        raise ValueError("Exact shortfall receipt SHA differs")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_run_name = expected_run_name or receipt.get("run_name")
    selected = receipt.get("selected")
    if (receipt.get("version") != "scale20k-exact-shortfall-v1"
            or receipt.get("run_name") != expected_run_name
            or receipt.get("window_index") != 212
            or receipt.get("target_eligible_native") != 20000
            or receipt.get("eligible_native_count") != 19993
            or receipt.get("remaining") != 7
            or not isinstance(selected, list) or len(selected) != 7):
        raise ValueError("Exact shortfall receipt target/count differs")
    qids = [row.get("qid") for row in selected if isinstance(row, dict)]
    hashes = [row.get("input_sha256") for row in selected if isinstance(row, dict)]
    if (len(qids) != 7 or len(set(qids)) != 7 or any(not isinstance(qid, str) or not qid for qid in qids)
            or len(hashes) != 7 or len(set(hashes)) != 7
            or any(not isinstance(value, str) or len(value) != 64 for value in hashes)
            or receipt.get("origin_index_sha256") != expected_origin_index_sha256):
        raise ValueError("Exact shortfall selected identities differ")
    source_manifest = Path(source_manifest).resolve(strict=True)
    if sha256(source_manifest) != source_manifest_sha256:
        raise ValueError("Exact shortfall source manifest SHA differs")
    source = json.loads(source_manifest.read_text(encoding="utf-8"))
    if (source.get("window_index") != 212 or source.get("selected") != 7
            or source.get("run_name") not in (None, expected_run_name)
            or (source_index is not None and source.get("source_builder_sha256") != sha256(
                ROOT / "scripts/prepare_scale20k_content_sources_v5.py"))):
        raise ValueError("Exact shortfall source is not a seven-row source")
    source_qids = source.get("qids")
    source_identity = source.get("input_identity")
    if (isinstance(source_qids, list) and set(source_qids) != set(qids)) or (
            isinstance(source_identity, dict)
            and {value for key, value in source_identity.items() if key in set(qids)} != set(hashes)):
        raise ValueError("Exact shortfall receipt/source selected identities differ")
    index_result = None
    if source_index is not None:
        index_path = Path(source_index).resolve(strict=True)
        if source_index_sha256 is None or sha256(index_path) != source_index_sha256:
            raise ValueError("Exact shortfall source index SHA differs")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        rows = [row for row in index.get("windows", [])
                if isinstance(row, dict) and row.get("window_index") == 212]
        if (index.get("selected") != 7 or len(rows) != 1
                or rows[0].get("run_name") != expected_run_name
                or rows[0].get("selected") != 7
                or rows[0].get("source_sha256") != source_manifest_sha256
                or rows[0].get("source_builder_sha256") != source.get("source_builder_sha256")):
            raise ValueError("Exact shortfall source index binding differs")
        receipt_pin = index.get("exact_shortfall_receipt")
        if (not isinstance(receipt_pin, dict)
                or Path(receipt_pin.get("path", "")).resolve() != receipt_path
                or receipt_pin.get("sha256") != receipt_sha256):
            raise ValueError("Exact shortfall source index receipt binding differs")
        additional = index.get("additional_code_pins", {})
        if additional.get(str(FINAL_DRIVER_ENTRY.resolve())) != FINAL_DRIVER_ENTRY_SHA256:
            raise ValueError("Exact shortfall source index final driver pin differs")
        index_result = {"path": str(index_path), "sha256": source_index_sha256,
                        "additional_code_pins": additional}
    reservation_result = None
    if reservation_path is not None:
        reservation_file = Path(reservation_path).resolve(strict=True)
        if reservation_sha256 is None or sha256(reservation_file) != reservation_sha256:
            raise ValueError("Exact shortfall reservation SHA differs")
        reservation = json.loads(reservation_file.read_text(encoding="utf-8"))
        if (reservation.get("global_index") != 212
                or reservation.get("run_name") != expected_run_name
                or Path(reservation.get("source_path", "")).resolve() != source_manifest.parent.resolve()
                or reservation.get("source_sha256") != source_manifest_sha256
                or set(reservation.get("qids", [])) != set(qids)
                or set(reservation.get("input_hashes", [])) != set(hashes)
                or reservation.get("actual_overlap_count") != 0
                or (source_index is not None and (
                    Path(reservation.get("source_index_path", "")).resolve() != Path(source_index).resolve()
                    or reservation.get("source_index_sha256") != source_index_sha256))):
            raise ValueError("Exact shortfall receipt/reservation binding differs")
        reservation_result = {"path": str(reservation_file), "sha256": reservation_sha256}
    return {"receipt": {"path": str(receipt_path), "sha256": receipt_sha256},
            "source_manifest": {"path": str(source_manifest), "sha256": source_manifest_sha256},
            "source_index": index_result,
            "reservation": reservation_result,
            "window_index": 212, "run_name": expected_run_name, "selected": 7, "qids": sorted(qids),
            "unused_source_rows": 60}


def verify_recovery_chain(*, entry_path: Path = RECOVERY_ENTRY,
                          entry_sha256: str = RECOVERY_ENTRY_SHA256,
                          database: Path = DATABASE,
                          recovery_verifier: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Verify the immutable recovery entry and absence of all held rows."""
    entry = Path(entry_path).resolve(strict=True)
    if sha256(entry) != entry_sha256:
        raise ValueError("Recovery entry SHA differs")
    verifier = recovery_verifier or _load_recovery_verifier()
    recovery = verifier(str(entry), entry_sha256, database=Path(database).resolve(strict=True))
    if not isinstance(recovery, dict):
        raise ValueError("Recovery verifier did not return an object")
    counts = _read_only_exclusion_check(Path(database), recovery)
    return {
        "entry": {"path": str(entry), "sha256": entry_sha256},
        "counts": counts,
        "current_eligible_native": recovery.get("verified_current_native"),
        "model_zero_windows": [196, 197],
        "quarantined_failed_window": 195,
        "verified": recovery,
    }


def _pin(path: Path) -> dict[str, str]:
    path = path.resolve(strict=True)
    return {"path": str(path), "sha256": sha256(path)}


def _recovery_hold_pins(entry_path: Path) -> list[dict[str, Any]]:
    """Collect the immutable hold receipts in the recovery entry ancestry."""
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def visit(pin: Any) -> None:
        if not isinstance(pin, dict) or not pin.get("path") or not pin.get("sha256"):
            return
        path = Path(pin["path"]).resolve(strict=True)
        key = (str(path), pin["sha256"])
        if key in seen:
            return
        seen.add(key)
        document = json.loads(path.read_bytes())
        hold = document.get("unstarted_hold")
        if hold is None:
            hold = document.get("unstarted_preflight_hold")
        if isinstance(hold, dict) and hold.get("path"):
            hold_path = Path(hold["path"]).resolve(strict=True)
            hold_doc = json.loads(hold_path.read_bytes())
            result.append({"hold": _pin(hold_path), "document": hold_doc,
                           "entry": {"path": str(path), "sha256": sha256(path)}})
        for key_name in ("previous_entry", "base_entry", "previous_recovery_entry"):
            child = document.get(key_name)
            if isinstance(child, dict):
                visit(child)

    visit({"path": str(Path(entry_path).resolve(strict=True)), "sha256": sha256(entry_path)})
    return sorted(result, key=lambda item: item["document"].get("window_index", -1))


def _verify_hold_schema(document: dict[str, Any], database: Path) -> dict[str, Any]:
    """Recheck current hold witnesses independently of the recovery helper."""
    index, selected = document.get("window_index"), document.get("selected")
    if type(index) is not int or type(selected) is not int or selected <= 0:
        raise ValueError("Current hold identity/count is invalid")
    if (document.get("model_calls") != 0 or document.get("native_failures") != 0
            or document.get("native_rows_for_selected") != 0
            or document.get("same_inputs_retried") != 0
            or document.get("eligible_before") != document.get("eligible_after")):
        raise ValueError("Current hold is not a zero-model unchanged-catalogue hold")
    qids, hashes = document.get("qids"), document.get("input_hashes")
    if (not isinstance(qids, list) or not isinstance(hashes, list)
            or len(qids) != selected or len(set(qids)) != selected
            or len(hashes) != selected or len(set(hashes)) != selected
            or any(not isinstance(qid, str) or not qid for qid in qids)
            or any(not isinstance(value, str) or len(value) != 64 for value in hashes)):
        raise ValueError("Current hold identities are invalid")
    expected_processes = {"preflight", "coordinator", "worker", "observer"}
    processes = document.get("processes")
    if not isinstance(processes, dict) or set(processes) != expected_processes:
        raise ValueError("Current hold process witness set differs")
    for name, pair in processes.items():
        if not isinstance(pair, dict) or not isinstance(pair.get("start"), dict) or not isinstance(pair.get("exit"), dict):
            raise ValueError("Current hold process pair is incomplete: " + name)
        documents = {}
        for side in ("start", "exit"):
            pin = pair[side]
            path = Path(pin["path"]).resolve(strict=True)
            if sha256(path) != pin["sha256"]:
                raise ValueError("Current hold process pin changed: " + name)
            documents[side] = json.loads(path.read_text(encoding="utf-8"))
        start, end = documents["start"], documents["exit"]
        if (type(start.get("pid")) is not int or start["pid"] <= 0
                or end.get("pid") != start["pid"]
                or end.get("process_created_at") != start.get("process_created_at")
                or end.get("process_exit_observed") is not True
                or type(end.get("actual_exit_code")) is not int
                or end["actual_exit_code"] != (0 if name == "preflight" else 1)):
            raise ValueError("Current hold process witness differs: " + name)
    stderr = document.get("coordinator_stderr")
    if not isinstance(stderr, dict) or sha256(Path(stderr["path"]).resolve(strict=True)) != stderr["sha256"]:
        raise ValueError("Current hold coordinator stderr pin changed")
    absent = document.get("confirmed_absent_artifacts")
    if not isinstance(absent, list) or not absent or any(Path(path).exists() for path in absent):
        raise ValueError("Current hold has model/public artifacts")
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        marks = ",".join("?" for _ in qids)
        remaining = db.execute("SELECT count(*) FROM native_classifications WHERE qid IN (" + marks + ")", sorted(qids)).fetchone()[0]
    if remaining:
        raise ValueError("Current hold authorities acquired native rows")
    return {"window_index": index, "selected": selected, "qids": sorted(qids),
            "input_hashes": sorted(hashes), "processes": processes,
            "catalogue_rows_remaining": remaining, "model_calls": 0,
            "native_failures": 0, "native_rows_for_selected": 0,
            "same_inputs_retried": 0}


def _failed_window(audit1, recovery: dict[str, Any]) -> dict[str, Any]:
    """Build the v2-compatible failed-window pins for the usage adapter."""
    verified = recovery["verified"]
    failed_control = verified["failed_control"]
    control_path = Path(failed_control["path"]).resolve(strict=True)
    control = json.loads(control_path.read_bytes())
    artifacts = control["control_artifacts"]
    summary_path = BASE / "run-summaries" / "scale20k-content-10201-10300-v3.json"
    export = artifacts["export"]
    quarantine = verified["quarantine"]
    rows = verified["quarantine_rows"]
    return {
        "summary": _pin(summary_path),
        "control": {"path": str(control_path), "sha256": failed_control["sha256"]},
        "export": export,
        "quarantine": quarantine,
        "quarantine_rows": rows,
        "removed": 99,
        "catalogue_rows_remaining": 0,
        "failed_gate": control["gate"],
    }


def _legacy_control_verifier(audit1, audit3, legacy, current_gap: dict[str, Any]):
    """Use audit3's source/reservation/current-SQL verifier for v3 exports.

    The only special route is global198: its preceding passed control is 194,
    authenticated by recovery195-v3, because 195--197 are excluded holds.
    ``legacy._source`` remains the single implementation of the detailed
    source, reservation, metadata and catalogue-gap checks.
    """
    # audit3.py exposes this function through its SHA-fixed adapted module;
    # _load_legacy_recovery_audit returns that module directly.
    canonical_name = getattr(audit3, "canonical_name", None) if audit3 is not None else None

    def verify(path: str | Path, expected: str):
        export = Path(path).resolve()
        if export.parent.name.endswith("-v4"):
            return _verify_final_v4_control(export, expected, legacy.database)
        if export.parent.name.endswith("-v3") and export.is_relative_to((BASE / "classification-exports-v3").resolve()):
            match = __import__("re").fullmatch(r"scale20k-content-(\d{4,})-(\d{4,})-v3", export.parent.name)
            if match is None:
                raise ValueError("Unsupported v3 recovery public namespace")
            start, end = map(int, match.groups())
            index = 93 + (start - 1) // 100
            if (start - 1) % 100 or end != start + 99 or not 161 <= index <= 213:
                raise ValueError("Recovery window range differs")
            # The legacy adapter already authenticates the ordinary 161--194
            # chain, including the genuine passed160 predecessor of 161.
            if index <= 194:
                return legacy.dispatch(export, expected)
            public = audit3.pinned({"path": str(export), "sha256": expected})
            control_path = BASE / "window-controls" / (export.parent.name + ".json")
            control_sha = audit3.sha(control_path)
            receipt = legacy.driver.verify_policy_control(control_path, control_sha)
            bound = receipt["control_artifacts"]["export"]
            if (receipt["window_index"] != index or receipt["gate"].get("passed") is not True
                    or Path(bound["path"]).resolve() != export or bound["sha256"] != expected):
                raise ValueError("Passed recovery control/public binding differs")
            for name, relative in (("native_eligibility_controller", "scripts/run_scale20k_native_v4.py"),
                                   ("native_eligibility_source_builder", "scripts/prepare_scale20k_content_sources_v4.py")):
                pin = receipt["control_artifacts"][name]
                expected_pin = legacy.code[str((ROOT / relative).resolve())]
                if Path(pin["path"]).resolve() != (ROOT / relative).resolve() or pin["sha256"] != expected_pin:
                    raise ValueError("Passed recovery policy code pin differs")
            if index == 198:
                prior = current_gap["prior_control"]
            else:
                if not callable(canonical_name):
                    raise ValueError("Audit3 adapted RecoveryAudit has no canonical_name API")
                prior_path = BASE / "window-controls" / (
                    canonical_name(index - 1, generation="v3") + ".json")
                prior = {"path": str(prior_path), "sha256": audit3.sha(prior_path)}
            prior_document = audit3.pinned(prior)
            expected_prior_index = 194 if index == 198 else index - 1
            if (prior_document.get("window_index") != expected_prior_index
                    or prior_document.get("gate", {}).get("passed") is not True
                    or Path(public["prior_control_receipt"]).resolve() != Path(prior["path"]).resolve()
                    or public.get("artifact_sha256", {}).get(str(Path(prior["path"]).resolve())) != prior["sha256"]):
                raise ValueError("Recovery prior must be the preceding passed control or passed194 gap")
            legacy._source(receipt, public, index, export.parent.name)
            legacy.verified.add(index)
            legacy.control_counts[index] = receipt["gate"]["accepted"]
            return {**receipt, "audit_control_pin": {"path": str(control_path), "sha256": control_sha}}
        return audit1.verify_window_control(export, expected, database=legacy.database)
    return verify


def _verify_actual_first_native_witness(source_manifest_sha256: str, run_name: str,
                                        proof_dir: Path = FINAL_FIRST_NATIVE_PROOF,
                                        expected_artifacts: dict[str, str] = FINAL_FIRST_NATIVE_PROOF_SHA256) -> dict[str, Any]:
    """Authenticate the separate first-native process witness.

    The terminal chain proves the final publication path.  This retained
    witness proves that the fresh reservation actually reached the first
    native/export stage under coordinator7 before the restore tail ran.
    """
    proof_dir = Path(proof_dir).resolve(strict=True)
    for name, expected in expected_artifacts.items():
        path = proof_dir / name
        if sha256(path) != expected:
            raise ValueError("Final first-native witness SHA differs: " + name)
    request = json.loads((proof_dir / "request.json").read_text(encoding="utf-8"))
    start = json.loads((proof_dir / "process-start.json").read_text(encoding="utf-8"))
    end = json.loads((proof_dir / "process-exit.json").read_text(encoding="utf-8"))
    argv = request.get("argv")
    if not isinstance(argv, list):
        raise ValueError("Final first-native witness argv is missing")
    pairs = dict(zip(argv, argv[1:]))
    if (Path(argv[1]).resolve() != FINAL_COORDINATOR.resolve()
            or pairs.get("--route-index") != "212"
            or pairs.get("--control-receipt-sha256") != "c3ea6b531886a34c917df34960a443a98fe8669cb3700e17f13a20afa2aada1d"
            or request.get("coordinator_sha256") != FINAL_COORDINATOR_SHA256
            or request.get("first_model_calls_for_this_reservation") is not True
            or request.get("prior_failed_preparation_model_calls") != 0
            or request.get("source_manifest_sha256") != source_manifest_sha256):
        raise ValueError("Final first-native request binding differs")
    if (type(start.get("pid")) is not int or start["pid"] <= 0
            or end.get("pid") != start["pid"]
            or end.get("process_created_at") != start.get("process_created_at")
            or end.get("actual_exit_code") != 0
            or end.get("process_exit_observed") is not True):
        raise ValueError("Final first-native process witness differs")
    output = json.loads((proof_dir / "stdout.txt").read_text(encoding="utf-8"))
    gate = output.get("gate", {})
    export_path = Path(output.get("export_manifest", "")).resolve(strict=True)
    if (output.get("stage") != "actual_native_export_verified"
            or output.get("selected") != 7
            or output.get("completed") != 7
            or output.get("failed", gate.get("failed")) != 0
            or output.get("pending", gate.get("pending")) != 0
            or output.get("cli_invocations") != 7
            or output.get("export_manifest_sha256") != sha256(export_path)
            or not export_path.is_relative_to(BASE)
            or export_path.parent.name != run_name
            or gate.get("passed") is not True
            or gate.get("selected") != 7
            or gate.get("failed") != 0
            or gate.get("pending") != 0):
        raise ValueError("Final first-native export witness differs")
    return {"path": str(proof_dir),
            "artifacts": {name: {"path": str((proof_dir / name).resolve()), "sha256": digest}
                          for name, digest in expected_artifacts.items()},
            "coordinator_sha256": FINAL_COORDINATOR_SHA256,
            "export_manifest": {"path": str(export_path),
                                "sha256": output["export_manifest_sha256"]},
            "selected": 7, "completed": 7, "failed": 0}


def _verify_final_historical_snapshot(public: dict[str, Any], run_name: str,
                                      source_manifest_sha256: str | None = None,
                                      first_native_proof: Path = FINAL_FIRST_NATIVE_PROOF,
                                      first_native_sha256: dict[str, str] = FINAL_FIRST_NATIVE_PROOF_SHA256) -> dict[str, Any]:
    """Require the snapshot emitted by the actual v7 coordinator.

    ``scale20k_final_native_v1.code_snapshot()`` is a live view of the
    checkout.  The coordinator's ``new-code-context.json`` and the public
    export's artifact map are the historical execution witnesses; both must
    retain the v7 wrapper, its v6 parent, the v3 driver entry, and the
    successful child preparation plus the stopped preflight evidence.
    """
    context_path = (BASE / "operational-processes" / run_name / "new-code-context.json").resolve(strict=True)
    context = json.loads(context_path.read_text(encoding="utf-8"))
    snapshot = context.get("artifact_sha256")
    if not isinstance(snapshot, dict) or context.get("coordinator_sha256") != FINAL_COORDINATOR_SHA256:
        raise ValueError("Final v4 historical coordinator context is incomplete")
    expected = {
        FINAL_COORDINATOR: FINAL_COORDINATOR_SHA256,
        FINAL_COORDINATOR_PARENT: FINAL_COORDINATOR_PARENT_SHA256,
        FINAL_DRIVER: FINAL_DRIVER_SHA256,
        FINAL_CHILD_HELPER: FINAL_CHILD_HELPER_SHA256,
        ROOT / "scripts/prepare_scale20k_content_sources_v5.py":
            sha256(ROOT / "scripts/prepare_scale20k_content_sources_v5.py"),
    }
    public_snapshot = public.get("artifact_sha256")
    if not isinstance(public_snapshot, dict):
        raise ValueError("Final v4 public export has no historical artifact snapshot")
    for path, digest in expected.items():
        key = str(path.resolve())
        if (snapshot.get(key) != digest or public_snapshot.get(key) != digest
                or sha256(path) != digest):
            raise ValueError("Final v4 historical code snapshot pin differs: " + key)

    child_dir = BASE / "final-shortfall-v2/actual-child-preparation-v1"
    stopped_dir = BASE / "operational-processes" / run_name / "preflight"
    first_native = _verify_actual_first_native_witness(
        source_manifest_sha256 or sha256(BASE / "classification-sources-final-v2/window-11901-12000-v4/manifest.json"),
        run_name, proof_dir=first_native_proof, expected_artifacts=first_native_sha256)
    for directory in (child_dir, stopped_dir):
        for path in sorted(directory.glob("*")):
            # These preparation/preflight witnesses belong to the execution
            # context.  The sealed public export predates their addition and
            # must remain byte-for-byte untouched, so it is not treated as a
            # second witness index here.
            if path.is_file() and snapshot.get(str(path.resolve())) != sha256(path):
                raise ValueError("Final v4 historical witness is absent from context: " + str(path))
    child_request = json.loads((child_dir / "request.json").read_text(encoding="utf-8"))
    child_exit = json.loads((child_dir / "process-exit.json").read_text(encoding="utf-8"))
    if (child_request.get("helper_sha256") != FINAL_CHILD_HELPER_SHA256
            or child_request.get("first_model_call_pending") is not True
            or child_request.get("prior_preparation_model_calls") != 0
            or child_exit.get("actual_exit_code") != 0
            or child_exit.get("process_exit_observed") is not True):
        raise ValueError("Final v4 child preparation witness differs")
    preflight_request = json.loads((stopped_dir / "request.json").read_text(encoding="utf-8"))
    preflight_exit = json.loads((stopped_dir / "process-exit.json").read_text(encoding="utf-8"))
    if (preflight_request.get("final_driver_entry_sha256") != FINAL_DRIVER_ENTRY_SHA256
            or preflight_request.get("final_continuation_sha256") != FINAL_DRIVER_SHA256
            or preflight_exit.get("actual_exit_code") != 1
            or preflight_exit.get("process_exit_observed") is not True
            or not (stopped_dir / "stderr.txt").read_bytes()):
        raise ValueError("Final v4 stopped preflight witness differs")

    return {"path": str(context_path), "sha256": sha256(context_path),
            "coordinator_sha256": FINAL_COORDINATOR_SHA256,
            "parent_coordinator_sha256": FINAL_COORDINATOR_PARENT_SHA256,
            "required_code_pins": {str(path.resolve()): digest for path, digest in expected.items()},
            "child_preparation": {"path": str(child_dir), "selected": 7, "model_calls": 0},
            "stopped_preflight": {"path": str(stopped_dir), "exit_code": 1},
            "first_native": first_native}


def _observe_final_catalogue_display(database: Path, export: Path,
                                     expected: str) -> dict[str, Any]:
    """Observe final rows without applying the pre-classification gate.

    The final source policy intentionally leaves these seven works at
    ``recommendable=0`` while their native classification is adopted for the
    content catalogue.  The normal v4 observer filters that flag and cannot
    represent this final subset.  This read-only observer compares persisted
    labels against the public projection while retaining the display policy
    check.
    """
    from recommend_book.scale_content_control_v4 import display_counts, display_policy_sha256

    raw = Path(export).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Final display observation export SHA differs")
    public = json.loads(raw)
    rows = public["verified_projection"]["rows"]
    before = display_policy_sha256()
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        actual = {qid: json.loads(labels) for qid, labels in db.execute(
            "SELECT n.qid,n.labels_json FROM native_classifications n JOIN works w USING(qid) "
            "WHERE n.manifest_sha256=? AND w.display_allowed=1", (expected,))}
        expected_rows = {row["qid"]: row["admitted_labels"] for row in rows}
        if actual != expected_rows:
            raise ValueError("Final catalogue labels or display identity set differs")
        counts = display_counts(rows)
    after = display_policy_sha256()
    if before != counts["policy_sha256"] or before != after:
        raise ValueError("Display policy changed during final observation")
    return {
        "classification_manifest_sha256": expected,
        "display_projection": counts,
        "matched_display_works": len(actual),
        "recommendable_filter_applied": False,
        "database_path": str(database),
        "persisted_rows": [{"qid": qid, "admitted_labels": labels}
                           for qid, labels in sorted(actual.items())],
        "policy_sha256_before": before,
        "policy_sha256_after": after,
        "observer_code_path": str(Path(__file__).resolve()),
        "observer_code_sha256": sha256(Path(__file__).resolve()),
        "read_only": True,
        "model_calls": 0,
    }


def _verify_final_v4_control(export: Path, expected: str, database: Path) -> dict[str, Any]:
    """Verify the adopted fresh-v4 export through its real control receipt."""
    from recommend_book import scale20k_final_native_v1 as final_state
    from recommend_book.scale_content_control_v4 import verify_control_receipt

    run_name = export.parent.name
    if run_name != final_state.canonical_run_name(212):
        raise ValueError("Final export namespace differs")
    control_path = BASE / "window-controls" / (run_name + ".json")
    control_sha = sha256(control_path)
    receipt = verify_control_receipt(control_path, control_sha)
    bound = receipt.get("control_artifacts", {}).get("export", {})
    if (receipt.get("window_index") != 212
            or receipt.get("gate", {}).get("passed") is not True
            or Path(bound.get("path", "")).resolve() != export
            or bound.get("sha256") != expected
            or receipt.get("coordinator_sha256") != FINAL_COORDINATOR_SHA256):
        raise ValueError("Final v4 control/export/coordinator binding differs")
    current_sql_observation = _observe_final_catalogue_display(database, export, expected)
    artifacts = receipt["control_artifacts"]
    builder = ROOT / "scripts/prepare_scale20k_content_sources_v5.py"
    driver = ROOT / "scripts/run_scale20k_final_continuation_v2.py"
    source_builder = artifacts.get("native_eligibility_source_builder")
    controller = artifacts.get("native_eligibility_controller")
    if (not isinstance(source_builder, dict) or Path(source_builder.get("path", "")).resolve() != builder.resolve()
            or source_builder.get("sha256") != sha256(builder)
            or not isinstance(controller, dict) or Path(controller.get("path", "")).resolve() != driver.resolve()
            or controller.get("sha256") != sha256(driver)):
        raise ValueError("Final v4 native source/controller code pins differ")
    reservation_path = BASE / "attempt-reservations" / (run_name + ".json")
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    source = Path(reservation.get("source_path", "")).resolve(strict=True)
    verified_paths = final_state.verify_reservation(source, 212)
    policy_path = source / final_state.FINAL_POLICY_NAME
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    manifest_path = source / "manifest.json"
    if (policy.get("version") != "scale20k-final-source-policy-v1"
            or policy.get("new_run_name") != run_name
            or policy.get("selected") != 7
            or policy.get("cli_version") != "codex-cli 0.164.0"
            or policy.get("model_calls") != 0 or policy.get("network_requests") != 0
            or policy.get("source_manifest_sha256") != sha256(manifest_path)
            or policy.get("source_builder_sha256") != sha256(builder)):
        raise ValueError("Final v4 source policy/current CLI binding differs")
    if (policy.get("observer_path") != str(driver.resolve())
            or policy.get("observer_sha256") != FINAL_DRIVER_SHA256):
        raise ValueError("Final v4 source observer pin differs")
    snapshot = final_state.code_snapshot()
    if not snapshot or any(not isinstance(digest, str) or len(digest) != 64 for digest in snapshot.values()):
        raise ValueError("Final v4 code snapshot is incomplete")
    public = json.loads(export.read_text(encoding="utf-8"))
    historical = _verify_final_historical_snapshot(public, run_name,
                                                    source_manifest_sha256=sha256(manifest_path))
    parent_source = FINAL_COORDINATOR.read_text(encoding="utf-8")
    if (f'PARENT_SHA = "{FINAL_COORDINATOR_PARENT_SHA256}"' not in parent_source
            and f"PARENT_SHA = '{FINAL_COORDINATOR_PARENT_SHA256}'" not in parent_source):
        raise ValueError("Final v4 coordinator parent pin differs")
    return {**receipt,
            "audit_control_pin": {"path": str(control_path), "sha256": control_sha},
            "final_v4": {"reservation": {"path": str(reservation_path), "sha256": sha256(reservation_path)},
                         "source": {"path": str(source), "manifest_sha256": sha256(manifest_path)},
                         "verified_paths": [str(Path(path).resolve()) for path in verified_paths],
                         "code_snapshot": snapshot,
                         "historical_snapshot": historical,
                         "current_sql_observation": current_sql_observation,
                         "catalogue_eligible_native": final_state.eligible_count()}}


def _current_control_verifier(audit1, database: Path, *, legacy=None,
                              audit3=None, current_gap: dict[str, Any] | None = None):
    if legacy is not None and audit3 is not None and current_gap is not None:
        return _legacy_control_verifier(audit1, audit3, legacy, current_gap)
    def verify(path: str | Path, expected: str):
        export = Path(path).resolve(strict=True)
        if export.parent.name.endswith("-v3") and export.is_relative_to((BASE / "classification-exports-v3").resolve()):
            control_path = BASE / "window-controls" / (export.parent.name + ".json")
            control_sha = sha256(control_path)
            module = importlib.import_module("recommend_book.scale_content_control_v4")
            receipt = module.verify_control_receipt(control_path, control_sha)
            bound = receipt.get("control_artifacts", {}).get("export", {})
            if (Path(bound.get("path", "")).resolve() != export or bound.get("sha256") != expected
                    or receipt.get("gate", {}).get("passed") is not True):
                raise ValueError("Current control/export/gate binding differs")
            return {**receipt, "audit_control_pin": {"path": str(control_path), "sha256": control_sha}}
        return audit1.verify_window_control(export, expected, database=database)
    return verify


def audit(*, database: Path = DATABASE, entry_path: Path = RECOVERY_ENTRY,
          entry_sha256: str = RECOVERY_ENTRY_SHA256,
          exact_shortfall_receipt: Path | None = EXACT_SHORTFALL_RECEIPT,
          exact_shortfall_receipt_sha256: str = EXACT_SHORTFALL_RECEIPT_SHA256,
          exact_source_manifest: Path = EXACT_SOURCE_MANIFEST,
          exact_source_manifest_sha256: str = EXACT_SOURCE_MANIFEST_SHA256,
          exact_source_index: Path | None = EXACT_SOURCE_INDEX,
          exact_source_index_sha256: str | None = EXACT_SOURCE_INDEX_SHA256,
          exact_run_name: str = "scale20k-content-11901-12000-v4",
          exact_origin_index_sha256: str = "ecafd3a14d1718d95f4596c1edbc910dc9cef8b08a71e5ec71e98b207d5d26da",
           cli212_hold: Path | None = CLI212_HOLD,
           cli212_hold_sha256: str = CLI212_HOLD_SHA256,
           recovery_verifier: Callable[..., dict[str, Any]] | None = None,
           manifest_verifier: Callable[..., dict[str, Any]] | None = None,
           catalogue_auditor: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run recovery checks and the frozen catalogue audit without mutation."""
    recovery = verify_recovery_chain(entry_path=entry_path, entry_sha256=entry_sha256,
                                     database=database, recovery_verifier=recovery_verifier)
    current_gap = verify_current_gap(entry_path=entry_path, entry_sha256=entry_sha256,
                                     database=database)
    cli212 = (verify_cli212_hold(hold_path=cli212_hold, hold_sha256=cli212_hold_sha256,
                                 database=database) if cli212_hold is not None else None)
    legacy_audit3, legacy, old_unstarted = _load_legacy_recovery_audit(Path(database).resolve(strict=True))
    audit1 = _load_frozen_audit()
    runner = catalogue_auditor or audit1.audit
    implementation_context = _archive_projection_context()
    result = runner(database=Path(database),
                    manifest_verifier=(manifest_verifier or _current_manifest_verifier(audit1)),
                    control_verifier=_current_control_verifier(
                        audit1, Path(database), legacy=legacy, audit3=legacy_audit3,
                        current_gap=current_gap))
    if not isinstance(result, dict):
        raise ValueError("Catalogue auditor did not return an object")
    result = dict(result)
    holds = _recovery_hold_pins(Path(entry_path))
    verified = recovery["verified"]
    if exact_shortfall_receipt is not None:
        result["exact_shortfall"] = verify_exact_shortfall_receipt(
            receipt_path=exact_shortfall_receipt,
            receipt_sha256=exact_shortfall_receipt_sha256,
            source_manifest=exact_source_manifest,
            source_manifest_sha256=exact_source_manifest_sha256,
            source_index=exact_source_index,
            source_index_sha256=exact_source_index_sha256,
            expected_run_name=exact_run_name,
            expected_origin_index_sha256=exact_origin_index_sha256)
    old_failed = dict(legacy.excluded)
    old_failed.update({"attempted": 15, "quarantined": 14,
                       "catalogue_rows_remaining": 0})
    result["excluded_failed_windows"] = [old_failed, _failed_window(audit1, recovery)]
    result["excluded_unstarted_windows"] = [
        {"window_index": item["document"].get("window_index"),
         "run_name": item["document"].get("run_name"),
         "selected": item["document"].get("selected"),
         "model_calls": item["document"].get("model_calls"),
         "native_failures": item["document"].get("native_failures"),
         "native_rows_for_selected": item["document"].get("native_rows_for_selected"),
         "same_inputs_retried": item["document"].get("same_inputs_retried"),
         "hold": item["hold"],
         "status": item["document"].get("status"),
         "eligible_before": item["document"].get("eligible_before"),
         "eligible_after": item["document"].get("eligible_after"),
         "independent_schema": _verify_hold_schema(item["document"], Path(database))}
        for item in holds
    ]
    expected_verified = set(range(161, 195))
    if legacy.verified:
        maximum = max(legacy.verified)
        expected_verified |= set(range(198, maximum + 1))
    if legacy.verified != expected_verified:
        raise ValueError("Passed v3 recovery controls are not a contiguous chain")
    result["legacy_recovery_proofs"] = {
        "entry": {"path": str(LEGACY_RECOVERY_ENTRY.resolve()),
                  "sha256": LEGACY_RECOVERY_ENTRY_SHA256},
        "failed_attempt": 15,
        "quarantined_failed": 14,
        "unstarted": old_unstarted,
        "catalogue_rows_remaining": 0,
    }
    result["current_recovery_gap"] = current_gap
    result["current_recovery_gap"]["passed_v3_windows"] = sorted(legacy.verified)
    result["current_recovery_gap"]["passed_v3_control_count"] = len(legacy.verified)
    result["excluded_authority_totals"] = {
        "legacy_failed_attempt": 15,
        "legacy_quarantined_failed": 14,
        "legacy_unstarted": 81,
        "current_failed": recovery["counts"]["failed_qids"],
        "current_unstarted": recovery["counts"]["unstarted_qids"],
        "current_total_unstarted": recovery["counts"]["unstarted_qids"],
        "all_unstarted_including_legacy": 81 + recovery["counts"]["unstarted_qids"],
        "cli212_premodel_hold": 0,
    }
    if cli212 is not None:
        result["cli212_premodel_hold"] = cli212
        result["excluded_authority_totals"]["cli212_premodel_hold"] = cli212["selected"]
    result.update({
        "version": "scale20k-catalogue-audit-v4",
        "recovery_chain": recovery,
        "model_zero_windows": [196, 197],
        "quarantined_failed_window": 195,
        "independent_accuracy": "unavailable",
        "calibrated_confidence": "unavailable",
        "model_calls": 0,
        "database_writes": 0,
        "source_raw_rescanned": False,
        "implementation_context": {
            "manifest_verifier": "native_cli_archive_projection_v1.verify_catalogue_classification",
            "cli_archive_projection": implementation_context["projection"],
            "cli_archive_helper": implementation_context["helper"],
            "cli_archive_root": implementation_context["archive_root"],
            "read_only": True,
        },
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--recovery-entry", type=Path, default=RECOVERY_ENTRY)
    parser.add_argument("--recovery-entry-sha256", default=RECOVERY_ENTRY_SHA256)
    parser.add_argument("--exact-shortfall-receipt", type=Path, default=EXACT_SHORTFALL_RECEIPT)
    parser.add_argument("--exact-shortfall-receipt-sha256", default=EXACT_SHORTFALL_RECEIPT_SHA256)
    parser.add_argument("--exact-source-manifest", type=Path, default=EXACT_SOURCE_MANIFEST)
    parser.add_argument("--exact-source-manifest-sha256", default=EXACT_SOURCE_MANIFEST_SHA256)
    parser.add_argument("--exact-source-index", type=Path, default=EXACT_SOURCE_INDEX)
    parser.add_argument("--exact-source-index-sha256", default=EXACT_SOURCE_INDEX_SHA256)
    parser.add_argument("--exact-run-name", default="scale20k-content-11901-12000-v4")
    parser.add_argument("--exact-origin-index-sha256",
                        default="ecafd3a14d1718d95f4596c1edbc910dc9cef8b08a71e5ec71e98b207d5d26da")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = audit(database=args.database, entry_path=args.recovery_entry,
                       entry_sha256=args.recovery_entry_sha256,
                       exact_shortfall_receipt=args.exact_shortfall_receipt,
                       exact_shortfall_receipt_sha256=args.exact_shortfall_receipt_sha256,
                       exact_source_manifest=args.exact_source_manifest,
                       exact_source_manifest_sha256=args.exact_source_manifest_sha256,
                       exact_source_index=args.exact_source_index,
                       exact_source_index_sha256=args.exact_source_index_sha256,
                       exact_run_name=args.exact_run_name,
                       exact_origin_index_sha256=args.exact_origin_index_sha256)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.DatabaseError) as exc:
        result = {"version": "scale20k-catalogue-audit-v4", "status": "violations",
                  "issues": [type(exc).__name__ + ": " + str(exc)], "model_calls": 0,
                  "database_writes": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=True))
    raise SystemExit(0 if result.get("status") == "complete" else 1)


if __name__ == "__main__":
    main()
