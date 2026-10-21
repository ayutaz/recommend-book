"""Build CREATEONLY thin source wrappers for the v6 review recovery.

The builder changes only the source window index and canonical run name.  The
old manifest, input/ledger/database/raw paths and bytes remain the authority;
the new manifest records their recovery lineage and the failed-review receipt.
It does not prepare a model input or perform any network or database work.

The builder excludes the baseline, prior reservations, and already-established
source windows.  The existing native driver's ``reserve_window`` remains the
authority for replaying the old v18 ``native-exclusions-v1`` index immediately
before reservation; this builder deliberately does not replay that driver
state.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-100000-v1"
OLD_OVERRIDE = BASE / "window-source-overrides-v2.json"
OUTPUT_OVERRIDE = BASE / "window-source-overrides-v3.json"
OLD_SOURCES = BASE / "classification-sources-v2"
NEW_SOURCES = BASE / "classification-sources-v3"
DIAGNOSTIC = BASE / "run-summaries/scale100k-content-0701-0800-v6.review-failure-diagnostic-v1.json"
SUCCESSFUL_CONTROL = BASE / "window-controls/scale100k-content-0601-0700-v6.json"
HELPER = Path(__file__).resolve()
BASELINE = BASE / "baseline-native-5000.jsonl"
RESERVATIONS = BASE / "attempt-reservations"
RUNTIME = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
EXCLUSION_RESPONSIBILITY = (
    "existing native driver reserve_window verifies native-exclusions-v1 immediately "
    "before reservation; this wrapper builder does not replay that driver state"
)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(Path(path).read_bytes())


def _write_new(path: Path, raw: bytes) -> None:
    with Path(path).open("xb") as stream:
        stream.write(raw)


def _load_exclusions(baseline: Path, reservations: Path) -> tuple[set[str], set[str]]:
    qids, hashes = set(), set()
    for line in Path(baseline).read_bytes().splitlines():
        if line.strip():
            row = json.loads(line)
            qid = row.get("qid")
            if isinstance(qid, str):
                qids.add(qid)
    if len(qids) != 5000:
        raise ValueError("baseline-native-5000 must contain 5000 distinct QIDs")
    for path in sorted(Path(reservations).glob("*.json")):
        value = read_json(path)
        qids.update(value.get("qids", []))
        hashes.update(value.get("input_hashes", []))
    return qids, hashes


def _validate_receipts(diagnostic: Path, successful_control: Path) -> dict:
    value = read_json(diagnostic)
    if (
        value.get("version") != "scale100k-review-failure-diagnostic-v1"
        or value.get("run_name") != "scale100k-content-0701-0800-v6"
        or value.get("selected_works") != 100
        or value.get("model_calls_added") != 0
        or value.get("original_artifacts_modified") is not False
        or value.get("conservative_all_failed_group_hold", {}).get("passes_unchanged_max_0_05") is not False
    ):
        raise ValueError("failed review diagnostic receipt schema or gate differs")
    if not successful_control.exists():
        raise FileNotFoundError(successful_control)
    return {
        "failed_diagnostic_receipt_path": str(diagnostic.resolve()),
        "failed_diagnostic_receipt_sha256": sha(diagnostic),
        "successful_control_path": str(successful_control.resolve()),
        "successful_control_sha256": sha(successful_control),
    }


def _mapping_rows(path: Path) -> list[dict]:
    value = read_json(path)
    if not isinstance(value, list):
        raise ValueError("work-mapping must be a list")
    rows = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("wikidata_qid"), str) \
                or not isinstance(row.get("input_sha256"), str):
            raise ValueError("work-mapping lacks exact QID/input hash")
        rows.append(row)
    if len({row["wikidata_qid"] for row in rows}) != len(rows):
        raise ValueError("duplicate source QID in work-mapping")
    if len({row["input_sha256"] for row in rows}) != len(rows):
        raise ValueError("duplicate source input hash in work-mapping")
    return rows


def _canonical_name(logical_index: int) -> tuple[str, str]:
    start = (logical_index - 42) * 100 + 1
    end = start + 99
    return f"window-{start:04d}-{end:04d}-v7", f"scale100k-content-{start:04d}-{end:04d}-v7"


def build_wrappers(*, old_override: Path = OLD_OVERRIDE, output_override: Path = OUTPUT_OVERRIDE,
                   output_root: Path = NEW_SOURCES, diagnostic: Path = DIAGNOSTIC,
                   successful_control: Path = SUCCESSFUL_CONTROL, helper: Path | None = None,
                   baseline: Path = BASELINE, reservations: Path = RESERVATIONS,
                   old_logical_start: int = 50, old_logical_end: int = 97,
                   runtime_root: Path = RUNTIME) -> dict:
    """Prepare all wrappers in memory, then publish each artifact exactly once."""
    if old_logical_start < 50 or old_logical_end < old_logical_start:
        raise ValueError("recovery origin must begin after failed v6 logical49")
    if Path(output_override).exists() or Path(output_root).exists():
        raise FileExistsError("recovery v3 output already exists; no overwrite or resume")
    builder_helper = Path(__file__).resolve()
    if helper is not None and Path(helper).resolve() != builder_helper:
        raise ValueError("helper must be the recovery wrapper builder itself")
    helper = builder_helper
    if not helper.exists():
        raise FileNotFoundError(helper)
    old_map = read_json(old_override)
    if not isinstance(old_map, dict):
        raise ValueError("old override is not an object")
    expected_keys = {str(i) for i in range(old_logical_start, old_logical_end + 1)}
    if not expected_keys <= set(old_map):
        raise ValueError("old override lacks the complete unreserved source range")
    excluded_qids, excluded_hashes = _load_exclusions(baseline, reservations)
    # Keep every earlier v6 source (including the failed logical49 window) out
    # of the recovery origin pool even when an external reservation index did
    # not record that old run.
    for prior_logical in range(42, old_logical_start):
        prior_source = old_map.get(str(prior_logical))
        if prior_source:
            prior_rows = _mapping_rows(Path(prior_source).resolve(strict=True) / "work-mapping.json")
            excluded_qids.update(row["wikidata_qid"] for row in prior_rows)
            excluded_hashes.update(row["input_sha256"] for row in prior_rows)
    receipt_lineage = _validate_receipts(diagnostic, successful_control)
    helper_sha = sha(helper)
    all_qids, all_hashes = set(), set()
    planned: list[tuple[int, Path, bytes, bytes, list[str], list[str]]] = []
    for old_logical in range(old_logical_start, old_logical_end + 1):
        old_source = Path(old_map[str(old_logical)]).resolve(strict=True)
        old_manifest = old_source / "manifest.json"
        old_mapping = old_source / "work-mapping.json"
        manifest = read_json(old_manifest)
        rows = _mapping_rows(old_mapping)
        if manifest.get("window_index") != old_logical:
            raise ValueError("old source window index differs from override key")
        if manifest.get("selected") != len(rows) or not manifest.get("chunks") or len(manifest["chunks"]) != 1:
            raise ValueError("old source selected/chunk count differs from mapping")
        old_run_name = manifest["chunks"][0].get("run_name")
        if not isinstance(old_run_name, str) or not old_run_name:
            raise ValueError("old source lacks a run name")
        started_markers = (
            Path(runtime_root) / f"terminal-{old_run_name}",
            Path(runtime_root) / "runtime" / "data" / "autonomous-next-v3" / "classification" / old_run_name,
        )
        if any(marker.exists() for marker in started_markers):
            raise ValueError(f"origin already started: {old_run_name}")
        qids = [row["wikidata_qid"] for row in rows]
        hashes = [row["input_sha256"] for row in rows]
        if set(qids) & excluded_qids or set(hashes) & excluded_hashes:
            raise ValueError("old source overlaps baseline or attempted reservation")
        if set(qids) & all_qids or set(hashes) & all_hashes:
            raise ValueError("recovery wrappers overlap each other")
        all_qids.update(qids)
        all_hashes.update(hashes)
        new_logical = old_logical - 1
        folder_name, run_name = _canonical_name(new_logical)
        derived = copy.deepcopy(manifest)
        derived["window_index"] = new_logical
        derived["chunks"][0]["run_name"] = run_name
        derived["recovery_lineage"] = {
            "version": 1,
            "origin_logical_window": old_logical,
            "new_logical_window": new_logical,
            "origin_manifest_path": str(old_manifest),
            "origin_manifest_sha256": sha(old_manifest),
            "origin_mapping_path": str(old_mapping),
            "origin_mapping_sha256": sha(old_mapping),
            "failed_window_0701_0800_v6_preserved_unadopted": True,
            **receipt_lineage,
            "helper_code_path": str(Path(helper).resolve()),
            "helper_code_sha256": helper_sha,
            "old_v18_exclusion_responsibility": EXCLUSION_RESPONSIBILITY,
            "source_bytes_reused_without_copy": True,
            "source_guard_calls": 0,
            "model_calls": 0,
            "web_requests": 0,
        }
        planned.append((new_logical, output_root / folder_name,
                        (json.dumps(derived, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                        Path(old_mapping).read_bytes(), qids, hashes))
    output_root.mkdir(parents=True, exist_ok=False)
    # Keep already-established logical42..48 pins in the new namespace. The
    # failed logical49 pin is deliberately replaced by the first v7 wrapper.
    override = {
        str(logical): old_map[str(logical)]
        for logical in range(42, old_logical_start)
        if str(logical) in old_map
    }
    for new_logical, folder, manifest_raw, mapping_raw, _, _ in planned:
        folder.mkdir(exist_ok=False)
        _write_new(folder / "manifest.json", manifest_raw)
        _write_new(folder / "work-mapping.json", mapping_raw)
        override[str(new_logical)] = str(folder.resolve())
    _write_new(output_override, (json.dumps(override, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return {
        "version": "scale100k-review-recovery-source-builder-v1",
        "selected_old_logical_windows": [old_logical_start, old_logical_end],
        "selected_new_logical_windows": [old_logical_start - 1, old_logical_end - 1],
        "wrapper_count": len(planned),
        "selected_qids": len(all_qids),
        "selected_input_hashes": len(all_hashes),
        "failed_window_0701_0800_v6_preserved_unadopted": True,
        "old_v18_exclusion_responsibility": EXCLUSION_RESPONSIBILITY,
        "model_calls": 0,
        "web_requests": 0,
        "output_override": str(Path(output_override).resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-override", type=Path, default=OLD_OVERRIDE)
    parser.add_argument("--output-override", type=Path, default=OUTPUT_OVERRIDE)
    parser.add_argument("--output-root", type=Path, default=NEW_SOURCES)
    parser.add_argument("--diagnostic", type=Path, default=DIAGNOSTIC)
    parser.add_argument("--successful-control", type=Path, default=SUCCESSFUL_CONTROL)
    parser.add_argument("--helper", type=Path, default=None)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--reservations", type=Path, default=RESERVATIONS)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    args = parser.parse_args()
    print(json.dumps(build_wrappers(**vars(args)), ensure_ascii=False))


if __name__ == "__main__":
    main()
