"""Explicit gate195 plus pre-model196 hold; retain both original receipts."""
from __future__ import annotations

from pathlib import Path

from . import scale20k_recovery195_v1 as prior

ROOT, BASE, DATABASE = prior.ROOT, prior.BASE, prior.DATABASE
BASE_ENTRY_SHA = "818aeff393577bae58f70102979897a9000abaefb9702a8c562e586d028e7f18"
HOLD_SHA = "4f9385988f6db7334a501085fc8f07562419e4388bafd84f874945865907c29c"
RESERVATION_SHA = "8dd9cd45b785120d91a76fc008f5787f1242625a754296985f8bc348d3cea832"
NEW_CODE = {"src/recommend_book/scale20k_recovery195_v2.py",
            "scripts/run_scale20k_recovery195_v2.py",
            "scripts/run_scale20k_content_classification_v4.py"}


def verify_entry(path, expected_sha, *, database=DATABASE, before_launch=False):
    entry = prior.read_pin({"path": str(Path(path).resolve(strict=True)), "sha256": expected_sha})
    if (entry.get("version") != "scale20k-recovery195-entry-v2"
            or (entry.get("last_passed_index"), entry.get("failed_index"), entry.get("unstarted_index"),
                entry.get("first_index")) != (194, 195, 196, 197)):
        raise ValueError("Only failed195 and genuinely unstarted196 may be skipped")
    base_pin = entry["base_entry"]
    if (base_pin["sha256"] != BASE_ENTRY_SHA
            or Path(base_pin["path"]).resolve(strict=True) != (BASE / "recovery-gate195-v1/entry-v1.json").resolve(strict=True)):
        raise ValueError("Original gate195 entry authority differs")
    base = prior.verify_entry(base_pin["path"], base_pin["sha256"], database=database)
    for key in ("prior_control", "failed_control", "failed_reservation", "previous_recovery_entry",
                "source_index", "inventory", "quarantine", "quarantine_rows"):
        if key in entry and entry[key] != base[key]:
            raise ValueError("Inherited immutable recovery authority differs: " + key)
    hold_pin = entry["unstarted_hold"]
    if (hold_pin["sha256"] != HOLD_SHA
            or Path(hold_pin["path"]).resolve(strict=True) != (BASE / "recovery-gate195-v1/unstarted196-hold-v1.json").resolve(strict=True)):
        raise ValueError("Actual unstarted196 hold differs")
    hold = prior.read_pin(hold_pin)
    reservation = prior.read_pin(hold["reservation"])
    qids, hashes = set(reservation["qids"]), set(reservation["input_hashes"])
    if (hold["version"] != "scale20k-unstarted-order-hold-v1"
            or hold["status"] != "held_unstarted_order_check" or hold["window_index"] != 196
            or hold["selected"] != 93 or len(qids) != 93 or len(hashes) != 93
            or hold["reservation"]["sha256"] != RESERVATION_SHA or reservation["global_index"] != 196
            or set(hold["qids"]) != qids or set(hold["input_hashes"]) != hashes
            or qids & set(base["verified_failed_qids"])
            or hashes & set(base["verified_failed_input_hashes"])
            or any(type(hold[k]) is not int or hold[k] != 0 for k in
                   ("model_calls", "native_failures", "native_rows_for_selected", "same_inputs_retried"))
            or hold["eligible_before"] != 18669 or hold["eligible_after"] != 18669):
        raise ValueError("Unstarted93 identities, model-zero or database facts differ")
    if set(hold["processes"]) != {"preflight", "coordinator", "worker", "observer"}:
        raise ValueError("Unstarted196 actual process set differs")
    for name, pins in hold["processes"].items():
        prior.check_process_pair(prior.read_pin(pins["start"]), prior.read_pin(pins["exit"]),
                                 0 if name == "preflight" else 1)
    stderr_pin = hold["coordinator_stderr"]
    if prior.sha(stderr_pin["path"]) != stderr_pin["sha256"] or "Content continuation window order differs" not in Path(stderr_pin["path"]).read_text(encoding="utf-8"):
        raise ValueError("Unstarted196 actual failure reason differs")
    if not hold["confirmed_absent_artifacts"] or any(Path(p).exists() for p in hold["confirmed_absent_artifacts"]):
        raise ValueError("Unstarted196 acquired native/public artifacts")
    if set(entry["code_pins"]) != NEW_CODE:
        raise ValueError("Recovery195 v2 code pin names differ")
    for relative, digest in entry["code_pins"].items():
        if prior.sha(ROOT / relative) != digest:
            raise ValueError("Recovery195 v2 code bytes differ: " + relative)
    count = prior.check_catalogue(database, qids | set(base["verified_failed_qids"]), minimum=18669)
    if before_launch:
        runtime = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
        name = "scale20k-content-10401-10500-v3"
        if count != 18669 or any(p.exists() for p in (
                BASE / "attempt-reservations" / (name + ".json"), runtime / ("terminal-" + name),
                runtime / "runtime/data/autonomous-next-v3/classification" / name)):
            raise ValueError("First197 launch must be genuinely new at the quarantined count")
    return {**base, **entry, "verified_unstarted_qids": sorted(qids),
            "verified_unstarted_input_hashes": sorted(hashes), "verified_current_native": count}


def verify_resume_route(route_index, prior_control_path, prior_control_sha, entry_path, entry_sha):
    from .scale_content_control_v4 import verify_control_receipt
    entry = verify_entry(entry_path, entry_sha)
    control = verify_control_receipt(prior_control_path, prior_control_sha)
    if route_index == 197 and control["window_index"] == 194:
        pin = entry["prior_control"]
        if (Path(prior_control_path).resolve(strict=True) != Path(pin["path"]).resolve(strict=True)
                or prior_control_sha != pin["sha256"]):
            raise ValueError("First197 must name the genuinely passed194 control")
    elif route_index < 198 or route_index != control["window_index"] + 1:
        raise ValueError("Content continuation window order differs without an authorized hold")
