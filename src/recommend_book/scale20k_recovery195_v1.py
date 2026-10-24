"""Read-only authority for skipping the quarantined gate195 window.

The existing v3 runtime, source identities and validators remain unchanged.
This receipt authorizes one explicit gap from passed194 to untouched196;
it never makes failed195 a passed continuation control.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1"
DATABASE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
FIXED = {
    "previous_recovery_entry": ("recovery-gate161-v2/entry-v1.json", "f972a2f8e49c2ba7bcb9a4f58b86253d49042f9ff878d77b32b2525f7d969d36"),
    "prior_control": ("window-controls/scale20k-content-10101-10200-v3.json", "1402b00da4f31f55b64867de8620bd8847c1cb632c602db50f67493736f768f8"),
    "failed_control": ("window-controls/scale20k-content-10201-10300-v3.json", "e688d5d3cef1396104bdf36f046bed85be3cd3c59a3a99763f927f9c57a3625d"),
    "failed_reservation": ("attempt-reservations/scale20k-content-10201-10300-v3.json", "4419f35930bc5de2280c22ea6b7af59f60924d0dbdf07362ad2541364eb9369c"),
    "quarantine": ("recovery-gate195-v1/quarantine/manifest.json", "997860ac65f90001c6c85c9c093c526d0d00e0b478916d4ac0c132e9ff982eef"),
    "quarantine_rows": ("recovery-gate195-v1/quarantine/rows-before.json", "bd72188b851b187605664fd72e24c09bdd57c7989210678d61f123d70362e06f"),
    "source_index": ("recovery-gate161-v2/source-index-v3-012.json", "4226047fed0b631292df25a0e3857db7f56b6134ba57cee173f08d3d1d42b858"),
    "inventory": ("native-exclusions-v3.json", "42e9b536871ced433010f1b881548b8c62845e975b8f95e95f6ce39726db2a7e"),
}
PROCESS_PINS = {
    "failed_worker": ("3ef5deb6d037d8c695d8a0a36a4313efeea9f44ff179d77653c5133b42258770", "daca264bcbb394a8ab47b3e90cd6ad1e946e87b29c1241f5e49a1d777160d055"),
    "failed_observer": ("ed6ded4e16321c953621aa63af4d31a7f8b5f3a1b9ce2bbcd7661c0cc7b7ecaf", "ee5f6f2ecc29ed557d0e9e98e41325f703f92afd909cc3532382211562a20ef0"),
    "queue": ("5d9bf4ce2b1f20a6fb5c270e1d0856c7637bc06e2f1a7c44aa485d115cedf833", "5d13fedf9a993b661a6595f3dbb30934dec0f01ac7aa7c9be321409621044e2c"),
}
NEW_CODE = {
    "src/recommend_book/scale20k_recovery195_v1.py",
    "scripts/run_scale20k_recovery195_v1.py",
    "scripts/quarantine_scale20k_failed_window_v2.py",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_pin(pin):
    path = Path(pin["path"]).resolve(strict=True)
    if not Path(pin["path"]).is_absolute() or sha(path) != pin["sha256"]:
        raise ValueError("Recovery195 pinned bytes differ")
    return json.loads(path.read_bytes())


def check_process_pair(start, end, expected_code):
    birth = start.get("process_created_at")
    if (type(start.get("pid")) is not int or start["pid"] <= 0
            or not isinstance(birth, str) or datetime.fromisoformat(birth).tzinfo is None
            or end.get("pid") != start["pid"] or end.get("process_created_at") != birth
            or type(end.get("actual_exit_code")) is not int
            or end["actual_exit_code"] != expected_code or end.get("process_exit_observed") is not True):
        raise ValueError("Recovery195 actual process PID, birth or exit differs")


def check_catalogue(database, excluded, *, minimum):
    with sqlite3.connect(Path(database).resolve(strict=True).as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        remaining = db.execute("SELECT count(*) FROM native_classifications WHERE qid IN ("
            + ",".join("?" for _ in excluded) + ")", sorted(excluded)).fetchone()[0]
        count = db.execute("SELECT count(*) FROM native_classifications n JOIN works w USING(qid) "
            "WHERE w.display_allowed=1").fetchone()[0]
    if remaining or count < minimum or count > 20000:
        raise ValueError("Quarantined195 authorities remain or preserved count differs")
    return count


def verify_entry(path, expected_sha, *, database=DATABASE, before_launch=False):
    from .scale20k_recovery_v2 import verify_recovery_entry
    from .scale_content_control_v4 import verify_control_receipt

    entry = read_pin({"path": str(Path(path).resolve(strict=True)), "sha256": expected_sha})
    if (entry.get("version") != "scale20k-recovery195-entry-v1"
            or (entry.get("last_passed_index"), entry.get("failed_index"), entry.get("first_index"),
                entry.get("eligible_after")) != (194, 195, 196, 18669)):
        raise ValueError("Only the explicit passed194/quarantined195/untouched196 gap is authorized")
    documents = {}
    for key, (relative, digest) in FIXED.items():
        pin = entry[key]
        if Path(pin["path"]).resolve(strict=True) != (BASE / relative).resolve(strict=True) or pin["sha256"] != digest:
            raise ValueError("Recovery195 independent authority differs: " + key)
        documents[key] = read_pin(pin)
    if set(entry["code_pins"]) != NEW_CODE:
        raise ValueError("Recovery195 named code pins differ")
    for relative, digest in entry["code_pins"].items():
        if sha(ROOT / relative) != digest:
            raise ValueError("Recovery195 code changed: " + relative)
    old = entry["previous_recovery_entry"]
    verify_recovery_entry(old["path"], old["sha256"], database=database)
    prior = entry["prior_control"]
    if verify_control_receipt(prior["path"], prior["sha256"])["window_index"] != 194:
        raise ValueError("Last actual passed control is not194")
    failed = entry["failed_control"]
    try:
        verify_control_receipt(failed["path"], failed["sha256"])
    except ValueError as exc:
        if str(exc) != "Actual content continuation gate failed or differs":
            raise
    else:
        raise ValueError("Failed195 must not authorize continuation")
    control = documents["failed_control"]
    gate = control["gate"]
    if (control["window_index"] != 195 or gate["passed"] is not False
            or (gate["selected"], gate["accepted"], gate["actual_display_feature_works"],
                gate["actual_content_non_genre_feature_works"]) != (99, 99, 65, 54)
            or gate["reasons"] != ["actual_display_feature_adoption_below_70_percent"]):
        raise ValueError("Actual failed195 display gate differs")
    reservation, manifest, rows = (documents[k] for k in ("failed_reservation", "quarantine", "quarantine_rows"))
    excluded = set(reservation["qids"])
    hashes = set(reservation["input_hashes"])
    if (reservation["global_index"] != 195 or len(excluded) != 99 or len(hashes) != 99
            or {row["qid"] for row in rows["rows"]} != excluded or len(rows["rows"]) != 99
            or rows["control_sha256"] != failed["sha256"] or rows["failed_gate"] != gate
            or rows["export_sha256"] != control["control_artifacts"]["export"]["sha256"]
            or any(row["manifest_sha256"] != rows["export_sha256"] for row in rows["rows"])
            or manifest["status"] != "quarantined" or manifest["removed"] != 99
            or manifest["remaining"] != 18669 or manifest["failed_gate"] != gate
            or manifest["rows_before_sha256"] != entry["quarantine_rows"]["sha256"]
            or manifest["model_calls"] != 0 or manifest["failed_inputs_retried"] != 0
            or manifest["original_proofs_modified"] is not False or manifest["baseline_qids_untouched"] is not True):
        raise ValueError("Actual99 quarantine and original failed authority set differ")
    if set(entry["processes"]) != {"failed_worker", "failed_observer", "queue", "quarantine"}:
        raise ValueError("Recovery195 actual process witness set differs")
    for name, pins in entry["processes"].items():
        if name in PROCESS_PINS and (pins["start"]["sha256"], pins["exit"]["sha256"]) != PROCESS_PINS[name]:
            raise ValueError("Original stopped process witness bytes differ")
        check_process_pair(read_pin(pins["start"]), read_pin(pins["exit"]), 0 if name == "quarantine" else 1)
    request = read_pin(entry["processes"]["quarantine"]["request"])
    if (request.get("shell") is not False or request.get("model_calls") != 0
            or request.get("failed_inputs_retried") != 0 or request.get("control_sha256") != failed["sha256"]
            or request.get("quarantine_script_sha256") != entry["code_pins"]["scripts/quarantine_scale20k_failed_window_v2.py"]):
        raise ValueError("Actual quarantine request does not name the pinned script/control")
    first = next((row for row in documents["source_index"]["windows"] if row["window_index"] == 196), None)
    if first is None or first["run_name"] != "scale20k-content-10301-10400-v3":
        raise ValueError("First untouched source196 is absent")
    count = check_catalogue(database, excluded, minimum=18669)
    if before_launch:
        if count != 18669:
            raise ValueError("First recovery launch must start at the actual quarantined count")
        runtime = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
        for p in (BASE / "attempt-reservations" / (first["run_name"] + ".json"),
                  runtime / ("terminal-" + first["run_name"]),
                  runtime / "runtime/data/autonomous-next-v3/classification" / first["run_name"]):
            if p.exists():
                raise ValueError("Untouched196 was already reserved or prepared; never replay")
    return {**entry, "verified_failed_qids": sorted(excluded),
            "verified_failed_input_hashes": sorted(hashes), "verified_current_native": count}
