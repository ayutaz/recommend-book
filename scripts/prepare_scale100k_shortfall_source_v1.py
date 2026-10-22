"""Create a CREATEONLY v8 source for a final shortfall below one hundred works.

This is an offline source-preparation helper.  It selects the first ``N``
works from one already sealed, unreserved v19 source and writes only new
ordered-inputs, mapping, manifest, and a separate override.  The source
database, usage ledger, raw records, and their pins are referenced in place;
they are never copied or rewritten.  The native driver's reservation and
preflight remain the final authority for exclusion and admission.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-100000-v1"
OUTPUT_ROOT = BASE / "classification-sources-v8-shortfall"
OUTPUT_OVERRIDE = BASE / "window-source-overrides-v8-shortfall.json"
EXCLUSION_INDEX = BASE / "native-exclusions-v1.json"
RESERVATIONS = BASE / "attempt-reservations"
HELPER = Path(__file__).resolve()
RUNTIME = ROOT / "data/autonomous-next-v3/preparation/scale30k-content-runtime-v1"
LIVE_DATABASE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"


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
    return f"window-{start:04d}-{end:04d}-v8", f"scale100k-content-{start:04d}-{end:04d}-v8"


def _load_exclusions(index_path: Path, reservations: Path, expected_sha256: str | None) -> tuple[set[str], set[str]]:
    if expected_sha256 is not None and sha(index_path) != expected_sha256:
        raise ValueError("native exclusion index pin differs")
    value = read_json(index_path)
    if value.get("version") != "scale100k-native-exclusions-v1":
        raise ValueError("native exclusion index version differs")
    qids = set(value.get("qids", []))
    hashes = set(value.get("input_hashes", []))
    for path in sorted(Path(reservations).glob("*.json")):
        row = read_json(path)
        qids.update(row.get("qids", []))
        hashes.update(row.get("input_hashes", []))
    return qids, hashes


def _load_source(source: Path, logical_index: int):
    """Validate the same source shape consumed by native ``_current_source``."""
    source = Path(source).resolve(strict=True)
    manifest_path = source / "manifest.json"
    mapping_path = source / "work-mapping.json"
    manifest = read_json(manifest_path)
    mapping = read_json(mapping_path)
    if (manifest.get("version") != "scale-content-classification-source-v1"
            or manifest.get("window_index") != logical_index
            or manifest.get("required_purpose_admissions_allowed") is not True
            or len(manifest.get("chunks", [])) != 1
            or not isinstance(mapping, list)):
        raise ValueError("source does not satisfy the v19 current-source contract")
    chunk = manifest["chunks"][0]
    inputs_path = Path(chunk["inputs_path"]).resolve(strict=True)
    ledger_path = Path(chunk["usage_ledger_path"]).resolve(strict=True)
    database_path = Path(manifest["source_database"]).resolve(strict=True)
    records_path = Path(manifest["records_path"]).resolve(strict=True)
    for path, key in ((inputs_path, "inputs_sha256"), (ledger_path, "usage_ledger_sha256"),
                      (database_path, "source_database_sha256"), (records_path, "records_sha256")):
        if sha(path) != (chunk.get(key) if key in chunk else manifest.get(key)):
            raise ValueError(f"source artifact pin differs: {path.name}")
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    input_lines = [line for line in inputs_path.read_bytes().splitlines() if line.strip()]
    works = [WorkInput.model_validate_json(line) for line in input_lines]
    if (manifest.get("selected") != len(works) or len(mapping) != len(works)
            or len(works) > 100 or len(works) < 1
            or len({work.work_id for work in works}) != len(works)):
        raise ValueError("source selected/input/mapping counts differ")
    by_id = {work.work_id: work for work in works}
    if {row.get("work_id") for row in mapping} != set(by_id):
        raise ValueError("source mapping work identities differ")
    if any(row.get("input_sha256") != model_hash(by_id[row["work_id"]]) for row in mapping):
        raise ValueError("source mapping input hash differs")
    # Reuse the production identity verifier.  It opens the sealed database
    # read-only and binds each QID to the actual current SourceRecord.
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    _verify_mapping_identity(database_path, mapping, works)
    raw_by_id = {work.work_id: line + b"\n" for work, line in zip(works, input_lines)}
    return source, manifest, mapping, works, raw_by_id, inputs_path, ledger_path, database_path, records_path


def _assert_not_attempted(mapping: list[dict], excluded_qids: set[str], excluded_hashes: set[str], database: Path | None):
    qids = {row["wikidata_qid"] for row in mapping}
    hashes = {row["input_sha256"] for row in mapping}
    if qids & excluded_qids or hashes & excluded_hashes:
        raise ValueError("shortfall source overlaps attempted/native input")
    if database is not None:
        with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
            placeholders = ",".join("?" for _ in qids)
            if placeholders and db.execute(
                f"SELECT count(*) FROM native_classifications WHERE qid IN ({placeholders})", sorted(qids)
            ).fetchone()[0]:
                raise ValueError("shortfall source overlaps existing native authority")


def build_shortfall(*, source: Path, logical_index: int, selected: int,
                    output_root: Path = OUTPUT_ROOT, output_override: Path = OUTPUT_OVERRIDE,
                    exclusion_index: Path = EXCLUSION_INDEX, exclusion_index_sha256: str | None = None,
                    reservations: Path = RESERVATIONS, native_database: Path | None = LIVE_DATABASE,
                    existing_override: Path | None = BASE / "window-source-overrides-v3.json",
                    runtime_root: Path = RUNTIME) -> dict:
    if type(logical_index) is not int or logical_index < 42:
        raise ValueError("logical index must be a v19 source window")
    if type(selected) is not int or not 1 <= selected < 100:
        raise ValueError("shortfall selected count must be between 1 and 99")
    if Path(output_root).exists() or Path(output_override).exists():
        raise FileExistsError("v8 shortfall output already exists; no overwrite or resume")
    source = Path(source).resolve(strict=True)
    if existing_override is not None and Path(existing_override).exists():
        assigned = read_json(existing_override).get(str(logical_index))
        if assigned is not None and Path(assigned).resolve() != source:
            raise ValueError("logical source window is assigned to a different source")
    source, manifest, mapping, works, raw_by_id, inputs_path, ledger_path, database_path, records_path = _load_source(source, logical_index)
    if manifest["selected"] < selected:
        raise ValueError("source does not contain enough works for shortfall")
    excluded_qids, excluded_hashes = _load_exclusions(exclusion_index, reservations, exclusion_index_sha256)
    subset_mapping = mapping[:selected]
    by_id = {work.work_id: work for work in works}
    subset_works = [by_id[row["work_id"]] for row in subset_mapping]
    _assert_not_attempted(subset_mapping, excluded_qids, excluded_hashes, native_database)
    # Validate the exact N-work subset against the same sealed DB authority.
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    _verify_mapping_identity(database_path, subset_mapping, subset_works)
    subset_raw = b"".join(raw_by_id[work.work_id] for work in subset_works)
    folder_name, run_name = _canonical_name(logical_index)
    for marker in (
        Path(runtime_root) / f"terminal-{run_name}",
        Path(runtime_root) / "runtime" / "data" / "autonomous-next-v3" / "classification" / run_name,
    ):
        if marker.exists():
            raise ValueError(f"shortfall run already started: {run_name}")
    derived = copy.deepcopy(manifest)
    derived["selected"] = selected
    derived["records_path"] = str(records_path)
    derived["source_database"] = str(database_path)
    if "ordered_inputs_path" in derived:
        derived["ordered_inputs_path"] = str(Path(output_root) / folder_name / "ordered-inputs.jsonl")
    if "ordered_inputs_sha256" in derived:
        derived["ordered_inputs_sha256"] = hashlib.sha256(subset_raw).hexdigest()
    derived["chunks"][0]["selected"] = selected
    derived["chunks"][0]["run_name"] = run_name
    derived["chunks"][0]["inputs_path"] = str(Path(output_root) / folder_name / "ordered-inputs.jsonl")
    derived["chunks"][0]["inputs_sha256"] = hashlib.sha256(subset_raw).hexdigest()
    derived["shortfall_lineage"] = {
        "version": 1,
        "origin_source_path": str(source),
        "origin_manifest_sha256": sha(source / "manifest.json"),
        "origin_mapping_path": str(source / "work-mapping.json"),
        "origin_mapping_sha256": sha(source / "work-mapping.json"),
        "origin_inputs_path": str(inputs_path),
        "origin_inputs_sha256": sha(inputs_path),
        "origin_usage_ledger_path": str(ledger_path),
        "origin_usage_ledger_sha256": sha(ledger_path),
        "origin_source_database": str(database_path),
        "origin_source_database_sha256": sha(database_path),
        "origin_records_path": str(records_path),
        "origin_records_sha256": sha(records_path),
        "selected_count": selected,
        "helper_code_path": str(HELPER),
        "helper_code_sha256": sha(HELPER),
        "source_database_ledger_raw_reused_without_copy": True,
        "model_calls": 0,
        "web_requests": 0,
        "native_reservation_and_preflight_required": True,
    }
    manifest_raw = (json.dumps(derived, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    mapping_raw = (json.dumps(subset_mapping, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    folder = Path(output_root) / folder_name
    folder.mkdir(parents=True, exist_ok=False)
    _write_new(folder / "ordered-inputs.jsonl", subset_raw)
    _write_new(folder / "work-mapping.json", mapping_raw)
    _write_new(folder / "manifest.json", manifest_raw)
    override_raw = (json.dumps({str(logical_index): str(folder.resolve())}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_new(output_override, override_raw)
    return {
        "version": "scale100k-shortfall-source-builder-v1",
        "logical_index": logical_index,
        "run_name": run_name,
        "selected": selected,
        "output_root": str(Path(output_root).resolve()),
        "output_override": str(Path(output_override).resolve()),
        "source_database_ledger_raw_reused_without_copy": True,
        "model_calls": 0,
        "web_requests": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--logical-index", type=int, required=True)
    parser.add_argument("--selected", type=int, required=True)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--output-override", type=Path, default=OUTPUT_OVERRIDE)
    parser.add_argument("--exclusion-index", type=Path, default=EXCLUSION_INDEX)
    parser.add_argument("--exclusion-index-sha256", required=True)
    parser.add_argument("--reservations", type=Path, default=RESERVATIONS)
    parser.add_argument("--native-database", type=Path, default=LIVE_DATABASE)
    parser.add_argument("--existing-override", type=Path, default=BASE / "window-source-overrides-v3.json")
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME)
    print(json.dumps(build_shortfall(**vars(parser.parse_args())), ensure_ascii=False))


if __name__ == "__main__":
    main()
