"""Authorize fresh198 after two proven model-zero continuation holds."""
from __future__ import annotations

from pathlib import Path

from . import scale20k_recovery195_v1 as util
from . import scale20k_recovery195_v2 as previous

ROOT, BASE, DATABASE = util.ROOT, util.BASE, util.DATABASE
PREVIOUS_ENTRY_SHA = "5f6e6a8b2db36fa36e3cb19ea5c8992a0737e33599dd6dc5cbcaafb669281fb0"
HOLD_SHA = "79a2edf14cc9914e47f029e454b7cb6cae0bcacc1c782724d1f03af7c6cfc351"
RESERVATION_SHA = "7cb6af01894fd291f803f34098cd5314d6613469fd03feaf8c4129b9b9a9e520"
NEW_CODE = {"src/recommend_book/scale20k_recovery195_v3.py",
            "scripts/run_scale20k_recovery195_v3.py",
            "scripts/run_scale20k_content_classification_v5.py"}


def verify_entry(path, expected_sha, *, database=DATABASE, before_launch=False):
    entry = util.read_pin({"path": str(Path(path).resolve(strict=True)), "sha256": expected_sha})
    if (entry.get("version") != "scale20k-recovery195-entry-v3"
            or (entry.get("last_passed_index"), entry.get("failed_index"), entry.get("first_index")) != (194, 195, 198)
            or entry.get("unstarted_indices") != [196, 197]):
        raise ValueError("Only failed195, unstarted196/197 and fresh198 are authorized")
    pin = entry["previous_entry"]
    if pin["sha256"] != PREVIOUS_ENTRY_SHA or Path(pin["path"]).resolve(strict=True) != (BASE / "recovery-gate195-v1/entry-v3.json").resolve(strict=True):
        raise ValueError("Prior immutable195/196 entry differs")
    base = previous.verify_entry(pin["path"], pin["sha256"], database=database)
    hold_pin = entry["unstarted_hold"]
    if hold_pin["sha256"] != HOLD_SHA or Path(hold_pin["path"]).resolve(strict=True) != (BASE / "recovery-gate195-v1/unstarted197-hold-v1.json").resolve(strict=True):
        raise ValueError("Actual unstarted197 hold differs")
    hold = util.read_pin(hold_pin)
    reservation = util.read_pin(hold["reservation"])
    qids, hashes = set(reservation["qids"]), set(reservation["input_hashes"])
    if (hold["version"] != "scale20k-unstarted-alias-hold-v1" or hold["status"] != "held_unstarted_alias_check"
            or hold["window_index"] != 197 or hold["selected"] != 92 or len(qids) != 92 or len(hashes) != 92
            or hold["reservation"]["sha256"] != RESERVATION_SHA or reservation["global_index"] != 197
            or set(hold["qids"]) != qids or set(hold["input_hashes"]) != hashes
            or qids & set(base["verified_failed_qids"] + base["verified_unstarted_qids"])
            or hashes & set(base["verified_failed_input_hashes"] + base["verified_unstarted_input_hashes"])
            or any(type(hold[k]) is not int or hold[k] != 0 for k in
                   ("model_calls", "native_failures", "native_rows_for_selected", "same_inputs_retried"))
            or hold["eligible_before"] != 18669 or hold["eligible_after"] != 18669):
        raise ValueError("Actual unstarted197 identities and zero-model facts differ")
    if set(hold["processes"]) != {"preflight", "coordinator", "worker", "observer"}:
        raise ValueError("Actual unstarted197 process set differs")
    for name, pair in hold["processes"].items():
        util.check_process_pair(util.read_pin(pair["start"]), util.read_pin(pair["exit"]), 0 if name == "preflight" else 1)
    error = hold["coordinator_stderr"]
    if util.sha(error["path"]) != error["sha256"] or "V20 source index authority differs" not in Path(error["path"]).read_text(encoding="utf-8"):
        raise ValueError("Actual unstarted197 reason differs")
    if not hold["confirmed_absent_artifacts"] or any(Path(p).exists() for p in hold["confirmed_absent_artifacts"]):
        raise ValueError("Unstarted197 acquired native/public artifacts")
    if set(entry["code_pins"]) != NEW_CODE or any(util.sha(ROOT / p) != digest for p, digest in entry["code_pins"].items()):
        raise ValueError("Fresh198 code pins differ")
    for key in ("prior_control", "failed_control", "source_index", "inventory", "previous_recovery_entry"):
        if key in entry and entry[key] != base[key]:
            raise ValueError("Inherited recovery authority differs: " + key)
    index = util.read_pin(entry["continuation_source_index"])
    original = util.read_pin(base["source_index"])
    if (index.get("requirements_sha256") != original["requirements_sha256"]
            or index.get("version") != original["version"]
            or index["windows"] != [r for r in original["windows"] if r["window_index"] >= 198]):
        raise ValueError("Fresh continuation index must be the exact untouched original suffix")
    excluded = qids | set(base["verified_failed_qids"] + base["verified_unstarted_qids"])
    count = util.check_catalogue(database, excluded, minimum=18669)
    if before_launch:
        runtime = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
        name = "scale20k-content-10501-10600-v3"
        if count != 18669 or any(p.exists() for p in (BASE / "attempt-reservations" / (name + ".json"),
                runtime / ("terminal-" + name), runtime / "runtime/data/autonomous-next-v3/classification" / name)):
            raise ValueError("Fresh198 must not have been reserved or prepared")
    return {**base, **entry, "verified_unstarted_qids": sorted(qids | set(base["verified_unstarted_qids"])),
            "verified_unstarted_input_hashes": sorted(hashes | set(base["verified_unstarted_input_hashes"])),
            "verified_current_native": count}


def verify_resume_route(route_index, prior_control_path, prior_control_sha, entry_path, entry_sha):
    from .scale_content_control_v4 import verify_control_receipt
    entry = verify_entry(entry_path, entry_sha)
    control = verify_control_receipt(prior_control_path, prior_control_sha)
    if route_index == 198 and control["window_index"] == 194:
        pin = entry["prior_control"]
        if Path(prior_control_path).resolve(strict=True) != Path(pin["path"]).resolve(strict=True) or prior_control_sha != pin["sha256"]:
            raise ValueError("First198 must name the genuine passed194 control")
    elif route_index < 199 or route_index != control["window_index"] + 1:
        raise ValueError("Content continuation order differs without the complete hold chain")
