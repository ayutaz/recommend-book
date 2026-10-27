"""Start the independent 2026-10-27 world-novel expansion phase.

This is a small namespace adapter around the existing authenticated native
preparer/reviewer.  It owns no frozen recipe bytes and never imports a result
from the 2026-10-26 runs.  ``check`` is offline and read-only; ``execute`` is
the explicit model-running entry point and still leaves catalogue publication
to the existing serialized publisher.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

from recommend_book import scale20k_native_v1 as fixed

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/world-21000-delivery-20261027-v1"
INDEX_VERSION = "world-novel-independent-phase-source-index-v1"
PHASE_VERSION = "world-novel-independent-phase-20261027-v1"
RUN_PREFIX = "world21000-20261027-w"
MAX_WORKS = 15
CLASSIFICATION_CONCURRENCY = 2
REVIEW_CONCURRENCY = 2
DEFAULT_OPERATOR = "world-novel-independent-phase-20261027"
sha, read, save = fixed.sha, fixed.read, fixed.save


def run_name(index: int) -> str:
    if type(index) is not int or not 0 <= index < 100:
        raise ValueError("Independent phase window index must be an integer in 0..99")
    return f"{RUN_PREFIX}{index:03d}-v1"


def checked(path: str | Path, digest: str) -> Path:
    path = Path(path).resolve(strict=True)
    if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest) or sha(path) != digest:
        raise ValueError("Sealed phase artifact bytes differ: " + str(path))
    return path


def _require_phase_document(document: dict[str, Any], base: Path) -> None:
    # The prepared prefix deliberately reuses the frozen source-index schema.
    # The separate prefix-validation receipt binds that legacy schema to this
    # new namespace; a new version is also accepted for synthetic callers.
    if document.get("version") not in (INDEX_VERSION, "world-novel-expansion-source-index-v1"):
        raise ValueError("Independent 2026-10-27 phase source index required")
    if document.get("version") == INDEX_VERSION and document.get("phase") != PHASE_VERSION:
        raise ValueError("Independent phase source index version differs")
    namespace = Path(document.get("namespace", "")).resolve()
    if document.get("version") == INDEX_VERSION and namespace != base:
        raise ValueError("Phase source index namespace differs from the requested fresh base")
    if document.get("version") == INDEX_VERSION:
        operator = document.get("operator")
        if not isinstance(operator, dict) or not operator.get("name") or not operator.get("script"):
            raise ValueError("Phase operator and pinned script are required")
        if not isinstance(operator.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", operator["sha256"]):
            raise ValueError("Phase operator SHA-256 pin is required")
        operator_path = checked(operator["script"], operator["sha256"])
        if operator_path != Path(__file__).resolve():
            raise ValueError("Phase operator pin must name this coordinator")
    if (any(key in document for key in ("catalogue_writes", "model_calls", "network_requests"))
            and (document.get("catalogue_writes") != 0 or document.get("model_calls") != 0
                 or document.get("network_requests") != 0)):
        raise ValueError("Offline phase prefix must contain no model, network, or catalogue writes")


def _verify_prepared_prefix_receipt(base: Path, index_path: Path, expected_sha: str, document: dict[str, Any]) -> None:
    """Bind the frozen source-index schema to the new phase namespace."""
    receipt_path = base / "prefix-validation-v1.json"
    receipt = read(checked(receipt_path, _sha_if_present(receipt_path)))
    if (receipt.get("version") != "world-21000-phase-20261027-prefix-v1"
            or Path(receipt.get("source_index", "")).resolve() != index_path
            or receipt.get("source_index_sha256") != expected_sha
            or receipt.get("window_index") != 0
            or receipt.get("run_name") != run_name(0)
            or receipt.get("required_purpose_admissions_allowed") is not True
            or receipt.get("model_calls") != 0 or receipt.get("network_requests") != 0
            or receipt.get("catalogue_writes") != 0
            or "scoped run_name adapter" not in receipt.get("scoped_runner", "")):
        raise ValueError("Prepared prefix validation receipt is not bound to this phase")
    source_exclusions = document["exclusions"]
    if (Path(receipt.get("source_exclusions", "")).resolve()
            != Path(source_exclusions["path"]).resolve()
            or receipt.get("source_exclusions_sha256") != source_exclusions["sha256"]):
        raise ValueError("Prepared prefix exclusion binding differs")


def _sha_if_present(path: Path) -> str:
    # The receipt is a local operator witness rather than a caller-supplied
    # input. Its own bytes are checked by JSON parsing and its bound pins below.
    if not path.is_file():
        raise FileNotFoundError("Prepared prefix validation receipt is required")
    return sha(path)


def _read_work_inputs(path: Path):
    from recommend_book.models import WorkInput
    return [WorkInput.model_validate_json(line) for line in path.read_bytes().splitlines() if line.strip()]


def load_sources(index: str | Path, expected_sha: str, *, base: str | Path = BASE) -> list[dict[str, Any]]:
    """Verify the complete offline prefix before preparation or reservation.

    The checks intentionally mirror the old expansion source contract, while
    binding every source, input, ledger, and source database to the new phase
    namespace and run-name prefix.
    """
    from recommend_book.scale_content_recovery_v1 import _verify_mapping_identity
    from recommend_book.usage_guard import model_hash

    base = Path(base).resolve()
    index_path = checked(index, expected_sha)
    document = read(index_path)
    _require_phase_document(document, base)
    if document.get("version") == "world-novel-expansion-source-index-v1":
        _verify_prepared_prefix_receipt(base, index_path, expected_sha, document)
    exclusions = document.get("exclusions")
    if not isinstance(exclusions, dict):
        raise ValueError("Pinned prior exclusion inventory is required")
    exclusion_path = checked(exclusions.get("path"), exclusions.get("sha256"))
    inventory = read(exclusion_path)
    if (not isinstance(inventory.get("qids"), list) or not isinstance(inventory.get("input_hashes"), list)
            or len(set(inventory["qids"])) != len(inventory["qids"])
            or len(set(inventory["input_hashes"])) != len(inventory["input_hashes"])):
        raise ValueError("Pinned exclusion inventory is malformed")
    qids, hashes = set(inventory["qids"]), set(inventory["input_hashes"])
    entries = document.get("windows")
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise ValueError("A non-empty bounded phase prefix is required")
    indices = [row.get("window_index") for row in entries]
    if indices != list(range(len(entries))):
        raise ValueError("Phase windows must be contiguous and start at zero")
    windows: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("Malformed phase window")
        number = row["window_index"]
        name = run_name(number)
        if row.get("run_name", name) != name:
            raise ValueError("Phase run name differs from the pinned namespace")
        source = Path(row.get("source_path", "")).resolve(strict=True)
        if not source.is_relative_to(base):
            raise ValueError("Phase sources must remain in the fresh namespace")
        manifest = checked(source / "manifest.json", row.get("source_sha256"))
        checked(source / "work-mapping.json", row.get("mapping_sha256"))
        value = read(manifest)
        selected = value.get("selected")
        if (value.get("version") != "scale-content-classification-source-v1"
                or value.get("required_purpose_admissions_allowed") is not True
                or value.get("window_index") != number
                or len(value.get("chunks", [])) != 1
                or type(selected) is not int or not 1 <= selected <= MAX_WORKS):
            raise ValueError("One bounded required-purpose-admitted phase window is required")
        chunk = value["chunks"][0]
        if chunk.get("run_name") != name:
            raise ValueError("Phase source chunk run name differs")
        inputs = checked(chunk.get("inputs_path"), chunk.get("inputs_sha256"))
        ledger = checked(chunk.get("usage_ledger_path"), chunk.get("usage_ledger_sha256"))
        database = checked(value.get("source_database"), value.get("source_database_sha256"))
        if not all(path.is_relative_to(base) for path in (inputs, ledger, database)):
            raise ValueError("Phase inputs, ledger, and source DB must remain in the fresh namespace")
        works = _read_work_inputs(inputs)
        mapping_path = source / "work-mapping.json"
        mapping = read(mapping_path)
        _verify_mapping_identity(database, mapping, works)
        new_qids = {item["wikidata_qid"] for item in mapping}
        new_hashes = {model_hash(work) for work in works}
        if not len(new_qids) == len(new_hashes) == len(works) == selected:
            raise ValueError("Distinct actual phase work identities required")
        if new_qids & qids or new_hashes & hashes:
            raise ValueError("Old, previously attempted, or duplicate phase identity excluded")
        qids.update(new_qids)
        hashes.update(new_hashes)
        windows.append({**row, "run_name": name, "source_path": str(source),
            "source_manifest_path": str(manifest), "mapping_path": str(mapping_path),
            "qids": sorted(new_qids), "input_hashes": sorted(new_hashes),
            "selected": len(works), "inputs_path": str(inputs), "ledger_path": str(ledger),
            "source_database": str(database)})
    return windows


def _lineage(index: Path, index_sha: str, base: Path, operator: str) -> dict[str, Any]:
    return {
        "version": "world-novel-independent-phase-lineage-v1",
        "phase": PHASE_VERSION,
        "operator": operator,
        "operator_script": str(Path(__file__).resolve()),
        "operator_script_sha256": sha(__file__),
        "namespace": str(base),
        "source_index": str(index),
        "source_index_sha256": index_sha,
        "classification_concurrency": CLASSIFICATION_CONCURRENCY,
        "review_concurrency": REVIEW_CONCURRENCY,
        "max_works_per_window": MAX_WORKS,
        "prior_control_required": False,
        "old_w003_gate_claimed_passed": False,
        "old_w003_publication_reused": False,
        "catalogue_writes": 0,
    }


def run_initial(index: str | Path, expected_sha: str, runtime: str | Path, *, base: str | Path = BASE,
                operator: str = DEFAULT_OPERATOR) -> dict[str, Any]:
    """Execute only phase window zero through the existing two-slot v2 route."""
    base, runtime, index_path = Path(base).resolve(), Path(runtime).resolve(strict=True), Path(index).resolve(strict=True)
    windows = load_sources(index_path, expected_sha, base=base)
    window = windows[0]
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import recover_world_novel_expansion_v2 as recovery
    lineage = _lineage(index_path, expected_sha, base, operator)
    attempt = base / "execution" / window["run_name"]
    # v2._run owns the permanent reservation and the unchanged quality gate.
    # This caller only supplies authenticated phase pins and no prior control.
    pins = [index_path, checked(read(index_path)["exclusions"]["path"], read(index_path)["exclusions"]["sha256"]),
            Path(__file__).resolve(), Path(window["source_manifest_path"]), Path(window["mapping_path"])]
    result = recovery._run(runtime, window, pins, base=base, lineage=None,
        prior_control=None, prior_sha=None, continuation=lineage)
    result["phase_lineage"] = lineage
    result["phase_attempt"] = str(attempt)
    return result


def check(index: str | Path, expected_sha: str, *, base: str | Path = BASE) -> dict[str, Any]:
    base = Path(base).resolve()
    windows = load_sources(index, expected_sha, base=base)
    return {"version": "world-novel-independent-phase-check-v1", "phase": PHASE_VERSION,
        "namespace": str(base), "windows": len(windows), "selected": sum(row["selected"] for row in windows),
        "max_works_per_window": MAX_WORKS, "classification_concurrency": CLASSIFICATION_CONCURRENCY,
        "review_concurrency": REVIEW_CONCURRENCY, "model_calls": 0, "network_requests": 0,
        "catalogue_writes": 0, "checked_at": datetime.now(timezone.utc).isoformat()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "execute"))
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--source-index-sha256", required=True)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--runtime-preparation", type=Path, default=fixed.RUNTIME)
    parser.add_argument("--operator", default=DEFAULT_OPERATOR)
    args = parser.parse_args(argv)
    if args.mode == "check":
        result = check(args.source_index, args.source_index_sha256, base=args.base)
    else:
        result = run_initial(args.source_index, args.source_index_sha256, args.runtime_preparation,
            base=args.base, operator=args.operator)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
