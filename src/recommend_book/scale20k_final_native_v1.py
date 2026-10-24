"""Fresh final-shortfall authority for the last seven 20k works.

The recovery v3 state and all of its historical receipts remain immutable.  This
facade gives the final seven-work handoff a new run namespace and a new
reservation ledger while reusing the already verified source and recipe
boundaries.  It deliberately rejects every earlier reservation and the
model-zero 212 hold; it never retries an old input.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

from . import scale20k_native_v3 as _v3

ROOT, BASE, RUNTIME, DATABASE, WRITER_LOCK = (
    _v3.ROOT, _v3.BASE, _v3.RUNTIME, _v3.DATABASE, _v3.WRITER_LOCK)
REQUIREMENTS_SHA = _v3.REQUIREMENTS_SHA
BASELINE_SHA = _v3.BASELINE_SHA
WORKS_BASELINE_SHA = _v3.WORKS_BASELINE_SHA
PLAN_SHA = _v3.PLAN_SHA
PRIOR_CONTROL, PRIOR_CONTROL_SHA = _v3.PRIOR_CONTROL, _v3.PRIOR_CONTROL_SHA
FAILED_CONTROL_SHA = _v3.FAILED_CONTROL_SHA
ACTIVE_RECOVERY_ENTRY = None
RECOVERY_GENERATION = "scale20k-final-shortfall-v1"
BUILDER_NAME = "prepare_scale20k_content_sources_v5.py"
FINAL_WINDOW = 212
FINAL_SELECTED = 7
FINAL_RUN_SUFFIX = "-v4"
FINAL_POLICY_NAME = "native-final-policy-v1.json"
FINAL_HOLD = BASE / "final-shortfall-v1" / "native-precheck-hold-v1.json"
FINAL_HOLD_SHA256 = "5a3926066d3d841fc710eb41b38d9467cc7126e2d8dd040f0538ec4d381bbedd"
OLD_212_HELPER_SHA256 = "9c817259f430de90f7117c59fe520a246d2f58bd016f842c16ed50ff8c0e42b5"
OLD_212_ENTRY_SHA256 = "f972a2f8e49c2ba7bcb9a4f58b86253d49042f9ff878d77b32b2525f7d969d36"

SourceSupplyExhausted = _v3.SourceSupplyExhausted
_BASE_CANONICAL_RUN_NAME = _v3.canonical_run_name
sha, read, save = _v3.sha, _v3.read, _v3.save
writer_lock, process_created_at = _v3.writer_lock, _v3.process_created_at
verify_requirements, load_inventory = _v3.verify_requirements, _v3.load_inventory
validate_baseline_membership = _v3.validate_baseline_membership


def eligible_count() -> int:
    """Count display-authorized native works, including source-genre unknowns."""
    with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(
            "SELECT count(*) FROM native_classifications n JOIN works w USING(qid) "
            "WHERE w.display_allowed=1").fetchone()[0]


def canonical_run_name(global_index: int) -> str:
    if type(global_index) is not int or global_index != FINAL_WINDOW:
        raise ValueError("Final shortfall has one fresh global-212 namespace")
    return _BASE_CANONICAL_RUN_NAME(global_index).removesuffix("-v3") + FINAL_RUN_SUFFIX


def _source_state():
    """Temporarily bind the frozen v3 source validator to the new run name."""
    old = _v3.canonical_run_name
    _v3.canonical_run_name = canonical_run_name
    return old


def source_entry(path: Path | str, expected_sha: str, global_index: int):
    if global_index != FINAL_WINDOW or sha(path) != expected_sha:
        raise ValueError("Final source-index bytes or global window differ")
    value = read(path)
    if value.get("requirements_sha256") != REQUIREMENTS_SHA:
        raise ValueError("Final source-index requirements differ")
    for row in value.get("windows", []):
        if row.get("window_index") == global_index:
            if row.get("run_name") != canonical_run_name(global_index):
                raise ValueError("Final source-index run namespace differs")
            if row.get("upstream_window_index") != 214:
                raise ValueError("Final source must retain source-index-v55 origin214")
            if not isinstance(row.get("source_path"), str) or not isinstance(row.get("source_sha256"), str):
                raise ValueError("Final source-index row must pin source bytes")
            return row
    raise SourceSupplyExhausted("Final source handoff does not contain global212")


def current_source(source: Path | str, global_index: int, expected_sha: str, inventory: dict):
    if global_index != FINAL_WINDOW:
        raise ValueError("Final source validator accepts global212 only")
    old = _source_state()
    try:
        fixed, hashes, qids = _v3.current_source(source, global_index, expected_sha, inventory)
    finally:
        _v3.canonical_run_name = old
    policy_path = Path(source).resolve(strict=True) / FINAL_POLICY_NAME
    if not policy_path.is_file():
        raise ValueError("Final source must carry its fresh policy receipt")
    policy = read(policy_path)
    if (policy.get("version") != "scale20k-final-source-policy-v1"
            or policy.get("generation") != RECOVERY_GENERATION
            or policy.get("new_window_index") != FINAL_WINDOW
            or policy.get("new_run_name") != canonical_run_name(FINAL_WINDOW)
            or policy.get("selected") != FINAL_SELECTED
            or policy.get("cli_version") != "codex-cli 0.164.0"
            or policy.get("model_calls") != 0
            or policy.get("network_requests") != 0):
        raise ValueError("Final source policy or current CLI identity differs")
    if len(qids) != FINAL_SELECTED or len(hashes) != FINAL_SELECTED:
        raise ValueError("Final source must contain exactly seven distinct works")
    return fixed, hashes, qids


def _all_attempted() -> tuple[set[str], set[str]]:
    qids: set[str] = set()
    hashes: set[str] = set()
    reservations = BASE / "attempt-reservations"
    if reservations.is_dir():
        for path in reservations.glob("*.json"):
            value = read(path)
            qids.update(value.get("qids", []))
            hashes.update(value.get("input_hashes", []))
    return qids, hashes


def _assert_final_hold_is_excluded() -> None:
    if not FINAL_HOLD.is_file():
        raise ValueError("Final model-zero precheck hold is required")
    if sha(FINAL_HOLD) != FINAL_HOLD_SHA256:
        raise ValueError("Final model-zero hold bytes differ")
    hold = read(FINAL_HOLD)
    if (hold.get("version") != "scale20k-unstarted-cli-precheck-hold-v1"
            or hold.get("status") != "held_unstarted_cli_context_mismatch"
            or hold.get("window_index") != FINAL_WINDOW
            or hold.get("run_name") != "scale20k-content-11901-12000-v3"
            or hold.get("model_calls") != 0
            or hold.get("same_inputs_retried") != 0
            or hold.get("native_rows_for_selected") != 0):
        raise ValueError("Final model-zero hold is not authentic")
    if (hold.get("selected") != FINAL_SELECTED
            or len(hold.get("qids", [])) != FINAL_SELECTED
            or len(set(hold.get("qids", []))) != FINAL_SELECTED
            or len(hold.get("input_hashes", [])) != FINAL_SELECTED
            or len(set(hold.get("input_hashes", []))) != FINAL_SELECTED):
        raise ValueError("Final hold selected count differs")
    for name in ("reservation", "source_manifest", "source_index",
                 "native_stderr", "native_stdout", "prepared_context"):
        pin = hold.get(name)
        if (not isinstance(pin, dict) or not isinstance(pin.get("path"), str)
                or sha(pin["path"]) != pin.get("sha256")):
            raise ValueError("Final hold artifact pin differs: " + name)
    reservation = read(hold["reservation"]["path"])
    if (reservation.get("helper_sha256") != OLD_212_HELPER_SHA256
            or reservation.get("recovery_entry", {}).get("sha256") != OLD_212_ENTRY_SHA256
            or reservation.get("qids") != sorted(hold["qids"])
            or reservation.get("input_hashes") != sorted(hold["input_hashes"])):
        raise ValueError("Old global212 reservation chain differs")
    process_codes = {"observer": 1, "worker": 1, "preflight": 0,
                     "coordinator": 0, "native": 1}
    for role, expected_code in process_codes.items():
        process = hold.get("processes", {}).get(role, {})
        starts, exits = process.get("start"), process.get("exit")
        if not starts or not exits:
            raise ValueError("Final hold process witness is incomplete: " + role)
        if sha(starts["path"]) != starts.get("sha256") or sha(exits["path"]) != exits.get("sha256"):
            raise ValueError("Final hold process witness pin differs: " + role)
        start_doc, exit_doc = read(starts["path"]), read(exits["path"])
        pid_match = not exit_doc.get("pid") or start_doc.get("pid") == exit_doc.get("pid")
        birth_match = (not exit_doc.get("process_created_at")
                       or start_doc.get("process_created_at") == exit_doc.get("process_created_at"))
        if (exit_doc.get("actual_exit_code") != expected_code
                or exit_doc.get("process_exit_observed") is not True
                or exit_doc.get("timed_out") is True
                or not pid_match or not birth_match):
            raise ValueError("Final hold process witness values differ: " + role)
    for raw in hold.get("confirmed_absent_artifacts", []):
        if Path(raw).exists():
            raise ValueError("Final hold absent artifact now exists: " + raw)
    with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True) as db:
        marks = ",".join("?" for _ in hold["qids"])
        if db.execute("SELECT count(*) FROM native_classifications WHERE qid IN (" + marks + ")",
                      sorted(hold["qids"])).fetchone()[0] != 0:
            raise ValueError("Final hold selected QID already has native data")


def reserve_window(source, global_index, source_sha, inventory_path, inventory_sha,
                   source_index_path, source_index_sha):
    inventory = load_inventory(inventory_path, inventory_sha)
    indexed = source_entry(source_index_path, source_index_sha, global_index)
    if (Path(indexed["source_path"]).resolve(strict=True) != Path(source).resolve()
            or indexed["source_sha256"] != source_sha):
        raise ValueError("Final source differs from its fixed source-index row")
    fixed, hashes, qids = current_source(source, global_index, source_sha, inventory)
    _assert_final_hold_is_excluded()
    old_qids, old_hashes = _all_attempted()
    # The immutable inventory is the baseline/exclusion authority; it is not
    # sufficient to inspect only the newer reservation ledgers.
    old_qids.update(inventory.get("qids", []))
    old_hashes.update(inventory.get("input_hashes", []))
    if qids & old_qids or hashes & old_hashes:
        raise ValueError("Final source overlaps an existing attempt; do not retry")
    with sqlite3.connect(DATABASE.resolve().as_uri() + "?mode=ro", uri=True) as db:
        marks = ",".join("?" for _ in qids)
        if qids and db.execute("SELECT count(*) FROM native_classifications WHERE qid IN (" + marks + ")", sorted(qids)).fetchone()[0]:
            raise ValueError("Final source already has native classification")
    path = BASE / "attempt-reservations" / (canonical_run_name(global_index) + ".json")
    if path.exists():
        raise ValueError("Final reservation already exists")
    value = {
        "version": "scale20k-final-attempt-reservation-v1",
        "generation": RECOVERY_GENERATION,
        "global_index": global_index,
        "run_name": canonical_run_name(global_index),
        "source_path": str(Path(source).resolve()),
        "source_sha256": source_sha,
        "inventory_path": str(Path(inventory_path).resolve()),
        "inventory_sha256": inventory_sha,
        "source_index_path": str(Path(source_index_path).resolve()),
        "source_index_sha256": source_index_sha,
        "input_hashes": sorted(hashes), "qids": sorted(qids),
        "actual_overlap_count": 0, "batch_size": 1,
        "final_hold_path": str(FINAL_HOLD.resolve()),
        "final_hold_sha256": sha(FINAL_HOLD),
        "helper_sha256": sha(__file__),
        "requirements_sha256": REQUIREMENTS_SHA,
    }
    with writer_lock(purpose="v20_final_shortfall_attempt_reservation"):
        current_qids, current_hashes = _all_attempted()
        if (current_qids | set(inventory.get("qids", []))) & qids or \
                (current_hashes | set(inventory.get("input_hashes", []))) & hashes:
            raise ValueError("Final source became reserved while waiting for writer")
        save(path, value)
    return path


def verify_reservation(source: Path | str, global_index: int):
    """Verify the create-only final reservation before model execution."""
    if global_index != FINAL_WINDOW:
        raise ValueError("Final reservation global window differs")
    source = Path(source).resolve(strict=True)
    fixed = read(source / "manifest.json")
    chunks = fixed.get("chunks", [])
    if len(chunks) != 1 or chunks[0].get("run_name") != canonical_run_name(global_index):
        raise ValueError("Final source manifest namespace differs")
    reservation_path = BASE / "attempt-reservations" / (canonical_run_name(global_index) + ".json")
    reservation = read(reservation_path)
    inventory = load_inventory(reservation["inventory_path"], reservation["inventory_sha256"])
    indexed = source_entry(reservation["source_index_path"], reservation["source_index_sha256"], global_index)
    if (reservation.get("version") != "scale20k-final-attempt-reservation-v1"
            or reservation.get("generation") != RECOVERY_GENERATION
            or reservation.get("global_index") != global_index
            or reservation.get("source_path") != str(source)
            or reservation.get("run_name") != canonical_run_name(global_index)
            or Path(indexed["source_path"]).resolve(strict=True) != source
            or indexed["source_sha256"] != reservation.get("source_sha256")
            or reservation.get("helper_sha256") != sha(__file__)
            or reservation.get("requirements_sha256") != REQUIREMENTS_SHA
            or reservation.get("batch_size") != 1
            or reservation.get("actual_overlap_count") != 0
            or reservation.get("final_hold_path") != str(FINAL_HOLD.resolve())
            or reservation.get("final_hold_sha256") != sha(FINAL_HOLD)):
        raise ValueError("Final reservation binding differs")
    fixed, hashes, qids = current_source(source, global_index,
                                          reservation["source_sha256"], inventory)
    if reservation.get("input_hashes") != sorted(hashes) or reservation.get("qids") != sorted(qids):
        raise ValueError("Final reservation selected authorities differ")
    _assert_final_hold_is_excluded()
    return [reservation_path, Path(reservation["inventory_path"]),
            Path(reservation["source_index_path"]), FINAL_HOLD,
            BASE / "requirements.json", BASE / "baseline-native-10000.jsonl",
            *code_paths()]


def load_recipe(kind: str, owner_path: Path | str):
    """Load the frozen recipe with a process-local final-state alias."""
    old = sys.modules.get("recommend_book.scale20k_native_v1")
    sys.modules["recommend_book.scale20k_native_v1"] = sys.modules[__name__]
    try:
        # Calling v3.load_recipe would rebind its own v1 alias back to v3.
        # Enter at the frozen v1 boundary so generated code resolves the
        # final facade for this process only.
        recipe = _v3._v1.load_recipe(kind, owner_path)
    finally:
        if old is None:
            sys.modules.pop("recommend_book.scale20k_native_v1", None)
        else:
            sys.modules["recommend_book.scale20k_native_v1"] = old
    return recipe


def code_paths():
    return [*_v3.code_paths(), Path(__file__).resolve(), ROOT / "scripts" / BUILDER_NAME,
            ROOT / "scripts" / "run_scale20k_content_classification_v6.py",
            ROOT / "scripts" / "run_scale20k_final_continuation_v2.py",
            ROOT / "scripts" / "prepare_scale_content_chunk_v5.py"]


def code_snapshot():
    paths = [*code_paths(), FINAL_HOLD]
    unique = []
    seen = set()
    for path in paths:
        path = Path(path).resolve()
        if path not in seen:
            seen.add(path)
            unique.append(path)
    missing = [str(path) for path in unique if not path.is_file()]
    if missing:
        raise FileNotFoundError("Final code pin is missing: " + ", ".join(missing))
    return {str(path): sha(path) for path in unique}


def verify_code_snapshot(snapshot):
    for path, expected in snapshot.items():
        if sha(path) != expected:
            raise ValueError("Final shortfall code changed during execution: " + path)
