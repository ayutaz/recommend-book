"""Build CREATEONLY v9 wrappers after the bounded timeout at logical 76.

Origins 77..96 are mapped to fresh logical slots 76..95.  The original
manifest, input, usage ledger, source database, raw records, and mapping bytes
remain the authority and are referenced without copying.  The failed logical
76 is excluded by the reservation/exclusion census.  Native
``reserve_window`` and its preflight remain the final admission gate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-100000-v1"
OLD_OVERRIDE = BASE / "window-source-overrides-v3.json"
OUTPUT_OVERRIDE = BASE / "window-source-overrides-v9.json"
OLD_SOURCES = BASE / "classification-sources-v3"
NEW_SOURCES = BASE / "classification-sources-v9"
DIAGNOSTIC = BASE / "run-summaries/scale100k-content-3401-3500-v7.review-timeout-diagnostic-v1.json"
SUCCESSFUL_CONTROL = BASE / "window-controls/scale100k-content-3301-3400-v7.json"
BASELINE = BASE / "baseline-native-5000.jsonl"
RESERVATIONS = BASE / "attempt-reservations"
RUNTIME = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
HELPER = Path(__file__).resolve()


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_bytes())


def _write_new(path: Path, raw: bytes) -> None:
    with Path(path).open("xb") as stream:
        stream.write(raw)


def _canonical_name(logical_index: int) -> tuple[str, str]:
    start = (logical_index - 42) * 100 + 1
    end = start + 99
    return f"window-{start:04d}-{end:04d}-v9", f"scale100k-content-{start:04d}-{end:04d}-v9"


def _mapping_rows(path: Path) -> list[dict]:
    rows = read_json(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("work-mapping must be a non-empty list")
    if any(not isinstance(row, dict) or not isinstance(row.get("wikidata_qid"), str)
           or not isinstance(row.get("input_sha256"), str) for row in rows):
        raise ValueError("work-mapping lacks exact QID/input hash")
    if len({row["wikidata_qid"] for row in rows}) != len(rows):
        raise ValueError("duplicate QID in work-mapping")
    if len({row["input_sha256"] for row in rows}) != len(rows):
        raise ValueError("duplicate input hash in work-mapping")
    return rows


def _load_exclusions(baseline: Path, reservations: Path) -> tuple[set[str], set[str], set[str]]:
    qids, hashes, run_names = set(), set(), set()
    for line in Path(baseline).read_bytes().splitlines():
        if line.strip():
            qid = json.loads(line).get("qid")
            if isinstance(qid, str):
                qids.add(qid)
    if len(qids) != 5000:
        raise ValueError("baseline-native-5000 must contain 5000 distinct QIDs")
    for path in sorted(Path(reservations).glob("*.json")):
        row = read_json(path)
        qids.update(row.get("qids", []))
        hashes.update(row.get("input_hashes", []))
        if isinstance(row.get("run_name"), str):
            run_names.add(row["run_name"])
    return qids, hashes, run_names


def _validate_receipts(*, diagnostic: Path, diagnostic_sha256: str,
                       successful_control: Path, successful_control_sha256: str) -> dict:
    if sha(diagnostic) != diagnostic_sha256:
        raise ValueError("timeout diagnostic SHA differs")
    if sha(successful_control) != successful_control_sha256:
        raise ValueError("successful control SHA differs")
    value = read_json(diagnostic)
    if (
        value.get("version") != "scale100k-review-timeout-diagnostic-v1"
        or value.get("run_name") != "scale100k-content-3401-3500-v7"
        or value.get("failed_global_index") != 76
        or value.get("prior_global_index") != 75
        or value.get("selected_works") != 100
        or value.get("model_calls_added") != 0
        or value.get("original_artifacts_modified") is not False
        or not isinstance(value.get("conservative_failure_rate"), (int, float))
        or value["conservative_failure_rate"] < 0.06
        or value.get("failed_window_accepted") is not False
        or not isinstance(value.get("artifacts_sha256"), dict)
        or not value["artifacts_sha256"]
    ):
        raise ValueError("timeout diagnostic facts or pins differ")
    control = read_json(successful_control)
    gate = control.get("gate", {})
    if (
        control.get("version") != "scale-content-window-control-v4"
        or control.get("window_index") != 75
        or gate.get("passed") is not True
        or gate.get("selected") != 100
    ):
        raise ValueError("successful prior control facts differ")
    return {
        "timeout_diagnostic_path": str(diagnostic.resolve()),
        "timeout_diagnostic_sha256": diagnostic_sha256,
        "successful_control_path": str(successful_control.resolve()),
        "successful_control_sha256": successful_control_sha256,
    }


def _load_source(source: Path, old_logical: int, *, allow_records_mismatch: bool = False):
    source = Path(source).resolve(strict=True)
    manifest_path, mapping_path = source / "manifest.json", source / "work-mapping.json"
    manifest = read_json(manifest_path)
    mapping = _mapping_rows(mapping_path)
    if (manifest.get("version") != "scale-content-classification-source-v1"
            or manifest.get("window_index") != old_logical
            or manifest.get("required_purpose_admissions_allowed") is not True
            or len(manifest.get("chunks", [])) != 1
            or manifest.get("selected") != len(mapping)):
        raise ValueError("origin manifest/mapping contract differs")
    chunk = manifest["chunks"][0]
    inputs = Path(chunk["inputs_path"]).resolve(strict=True)
    ledger = Path(chunk["usage_ledger_path"]).resolve(strict=True)
    database = Path(manifest["source_database"]).resolve(strict=True)
    records = Path(manifest["records_path"]).resolve(strict=True)
    for path, expected in ((inputs, chunk.get("inputs_sha256")),
                           (ledger, chunk.get("usage_ledger_sha256")),
                           (database, manifest.get("source_database_sha256")),
                           (records, manifest.get("records_sha256"))):
        if (path == records and allow_records_mismatch and isinstance(expected, str)
                and sha(path) != expected):
            continue
        if not isinstance(expected, str) or sha(path) != expected:
            raise ValueError(f"origin artifact hash differs: {path.name}")
    if "ordered_inputs_path" in manifest:
        top_path = Path(manifest["ordered_inputs_path"]).resolve(strict=True)
        top_sha = sha(top_path)
        if top_sha != manifest.get("ordered_inputs_sha256") or top_sha != chunk.get("inputs_sha256"):
            raise ValueError("origin top-level ordered inputs binding differs")
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    input_lines = [line for line in inputs.read_bytes().splitlines() if line.strip()]
    works = [WorkInput.model_validate_json(line) for line in input_lines]
    if len(works) != len(mapping) or len(works) > 100 or len({work.work_id for work in works}) != len(works):
        raise ValueError("origin input/mapping count differs")
    by_id = {work.work_id: work for work in works}
    if {row.get("work_id") for row in mapping} != set(by_id):
        raise ValueError("origin mapping identity differs")
    if any(row["input_sha256"] != model_hash(by_id[row["work_id"]]) for row in mapping):
        raise ValueError("origin input hash differs")
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    _verify_mapping_identity(database, mapping, works)
    return source, manifest, mapping, inputs, ledger, database, records


def _validate_source_hold(*, hold: Path, hold_sha256: str, source: Path,
                          manifest: dict) -> dict:
    if sha(hold) != hold_sha256:
        raise ValueError("source hold SHA differs")
    value = read_json(hold)
    manifest_path = (Path(source) / "manifest.json").resolve()
    records_path = Path(manifest["records_path"]).resolve(strict=True)
    observed = sha(records_path)
    expected = manifest.get("records_sha256")
    if (
        value.get("version") != "scale100k-unstarted-source-hold-v1"
        or value.get("origin_logical_window") != 92
        or Path(value.get("origin_manifest_path", "")).resolve() != manifest_path
        or value.get("origin_manifest_sha256") != sha(manifest_path)
        or Path(value.get("records_path", "")).resolve() != records_path
        or value.get("expected_records_sha256") != expected
        or value.get("observed_records_sha256") != observed
        or observed == expected
        or value.get("reason") != "current_records_hash_mismatch"
        or value.get("model_calls_added") != 0
        or value.get("original_artifacts_modified") is not False
    ):
        raise ValueError("source hold facts or binding differs")
    return {
        "source_hold_path": str(hold.resolve()),
        "source_hold_sha256": hold_sha256,
        "source_hold_origin_logical_window": 92,
        "source_hold_records_expected_sha256": expected,
        "source_hold_records_observed_sha256": observed,
    }


def _validate_unstarted(*, run_name: str, runtime: Path, reservations: Path, reserved_run_names: set[str]) -> None:
    markers = (
        runtime / f"terminal-{run_name}",
        runtime / "runtime" / "data" / "autonomous-next-v3" / "classification" / run_name,
    )
    if (any(path.exists() for path in markers)
            or (Path(reservations) / f"{run_name}.json").exists()
            or run_name in reserved_run_names):
        raise ValueError(f"origin already started or reserved: {run_name}")
    if list(runtime.glob(f"preflight.{run_name}.*")):
        raise ValueError(f"origin preflight already exists: {run_name}")


def build_wrappers(*, old_override: Path, old_override_sha256: str,
                   output_override: Path = OUTPUT_OVERRIDE, output_root: Path = NEW_SOURCES,
                   diagnostic: Path = DIAGNOSTIC, diagnostic_sha256: str,
                   successful_control: Path = SUCCESSFUL_CONTROL, successful_control_sha256: str,
                   baseline: Path = BASELINE, reservations: Path = RESERVATIONS,
                   runtime_root: Path = RUNTIME, old_logical_start: int = 77,
                   old_logical_end: int = 96, source_hold: Path | None = None,
                   source_hold_sha256: str | None = None) -> dict:
    if old_logical_start != 77 or old_logical_end != 96:
        raise ValueError("timeout recovery origin range must be 77..96")
    if Path(output_override).exists() or Path(output_root).exists():
        raise FileExistsError("v9 output already exists; no overwrite or resume")
    if sha(old_override) != old_override_sha256:
        raise ValueError("v3 source override SHA differs")
    old_map = read_json(old_override)
    if not isinstance(old_map, dict) or not {str(i) for i in range(42, 97)} <= set(old_map):
        raise ValueError("v3 source override lacks required logical range")
    receipt_lineage = _validate_receipts(
        diagnostic=diagnostic, diagnostic_sha256=diagnostic_sha256,
        successful_control=successful_control, successful_control_sha256=successful_control_sha256)
    if (source_hold is None) != (source_hold_sha256 is None):
        raise ValueError("source hold path and SHA must be supplied together")
    excluded_qids, excluded_hashes, reserved_runs = _load_exclusions(baseline, reservations)
    hold_lineage: dict = {}
    helper_sha = sha(HELPER)
    all_qids, all_hashes = set(), set()
    planned = []
    for old_logical in range(old_logical_start, old_logical_end + 1):
        held = old_logical == 92 and source_hold is not None
        old_source, manifest, mapping, inputs, ledger, database, records = _load_source(
            Path(old_map[str(old_logical)]), old_logical,
            allow_records_mismatch=held)
        old_run = manifest["chunks"][0].get("run_name")
        if not isinstance(old_run, str):
            raise ValueError("origin run name missing")
        _validate_unstarted(run_name=old_run, runtime=Path(runtime_root),
                            reservations=Path(reservations), reserved_run_names=reserved_runs)
        qids = {row["wikidata_qid"] for row in mapping}
        hashes = {row["input_sha256"] for row in mapping}
        if qids & excluded_qids or hashes & excluded_hashes:
            raise ValueError("origin overlaps baseline/reservation/exclusion")
        if held:
            hold_lineage = _validate_source_hold(
                hold=Path(source_hold), hold_sha256=str(source_hold_sha256),
                source=old_source, manifest=manifest)
            excluded_qids.update(qids)
            excluded_hashes.update(hashes)
            continue
        if qids & all_qids or hashes & all_hashes:
            raise ValueError("origin wrappers overlap each other")
        all_qids.update(qids)
        all_hashes.update(hashes)
        new_logical = 76 + len(planned)
        folder_name, run_name = _canonical_name(new_logical)
        derived = copy.deepcopy(manifest)
        derived["window_index"] = new_logical
        derived["chunks"][0]["run_name"] = run_name
        derived["recovery_lineage"] = {
            "version": 1,
            "origin_logical_window": old_logical,
            "new_logical_window": new_logical,
            "origin_manifest_path": str(old_source / "manifest.json"),
            "origin_manifest_sha256": sha(old_source / "manifest.json"),
            "origin_mapping_path": str(old_source / "work-mapping.json"),
            "origin_mapping_sha256": sha(old_source / "work-mapping.json"),
            "origin_inputs_path": str(inputs),
            "origin_inputs_sha256": sha(inputs),
            "origin_usage_ledger_path": str(ledger),
            "origin_usage_ledger_sha256": sha(ledger),
            "origin_source_database": str(database),
            "origin_source_database_sha256": sha(database),
            "origin_records_path": str(records),
            "origin_records_sha256": sha(records),
            **receipt_lineage,
            **hold_lineage,
            "helper_code_path": str(HELPER),
            "helper_code_sha256": helper_sha,
            "failed_window_3401_3500_v7_preserved_unadopted": True,
            "source_bytes_reused_without_copy": True,
            "source_guard_calls": 0,
            "model_calls": 0,
            "web_requests": 0,
            "native_reservation_and_preflight_required": True,
        }
        planned.append((new_logical, output_root / folder_name,
                        (json.dumps(derived, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
                        Path(old_source / "work-mapping.json").read_bytes()))
    output_root.mkdir(parents=True, exist_ok=False)
    override = {str(logical): old_map[str(logical)] for logical in range(42, 76)}
    for new_logical, folder, manifest_raw, mapping_raw in planned:
        folder.mkdir(exist_ok=False)
        _write_new(folder / "manifest.json", manifest_raw)
        _write_new(folder / "work-mapping.json", mapping_raw)
        override[str(new_logical)] = str(folder.resolve())
    _write_new(output_override, (json.dumps(override, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    new_range = [76, 76 + len(planned) - 1] if planned else []
    remaining_works = len(all_qids)
    return {"version": "scale100k-timeout-recovery-source-builder-v1",
            "origin_logical_windows": [77, 96], "new_logical_windows": new_range,
            "wrapper_count": len(planned), "selected_qids": len(all_qids),
            "selected_input_hashes": len(all_hashes), "model_calls": 0, "web_requests": 0,
            "held_origin_windows": [92] if source_hold is not None else [],
            "remaining_source_works": remaining_works,
            "output_override": str(Path(output_override).resolve())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-override", type=Path, default=OLD_OVERRIDE)
    parser.add_argument("--old-override-sha256", required=True)
    parser.add_argument("--output-override", type=Path, default=OUTPUT_OVERRIDE)
    parser.add_argument("--output-root", type=Path, default=NEW_SOURCES)
    parser.add_argument("--diagnostic", type=Path, default=DIAGNOSTIC)
    parser.add_argument("--diagnostic-sha256", required=True)
    parser.add_argument("--successful-control", type=Path, default=SUCCESSFUL_CONTROL)
    parser.add_argument("--successful-control-sha256", required=True)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--reservations", type=Path, default=RESERVATIONS)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    parser.add_argument("--source-hold", type=Path)
    parser.add_argument("--source-hold-sha256")
    print(json.dumps(build_wrappers(**vars(parser.parse_args())), ensure_ascii=False))


if __name__ == "__main__":
    main()
