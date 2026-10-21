"""Audit one-time content-window selections, outcomes, URLs, and duplicates."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _qids_from_record(path: Path) -> set[str]:
    qids: set[str] = set()
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return qids
    for row in rows:
        qid = row.get("qid")
        if isinstance(qid, str) and qid.startswith("Q"):
            qids.add(qid)
        for identifier in row.get("identifiers", []):
            value = identifier.get("value")
            if identifier.get("source") == "wikidata" and identifier.get("scope") == "work" and isinstance(value, str):
                qids.add(value)
    return qids


def audit(root: Path) -> dict:
    windows = []
    outcome_qids: list[str] = []
    selected_qids: list[str] = []
    accepted_qids: list[str] = []
    urls: set[str] = set()
    http_requests = 0
    gaps = []
    for manifest_path in sorted(root.glob("content-window-*/manifest.json")):
        window = manifest_path.parent
        manifest = _read_json(manifest_path) or {}
        outcomes = _read_json(window / "outcomes.json") or []
        if not isinstance(outcomes, list):
            outcomes = []
        selections = _read_json(window / "selections.json") or []
        if not isinstance(selections, list):
            selections = []
        outcome_rows = [row for row in outcomes if isinstance(row, dict)]
        selection_rows = [row for row in selections if isinstance(row, dict)]
        outcome_qids.extend(row["qid"] for row in outcome_rows if isinstance(row.get("qid"), str))
        selected_qids.extend(row["qid"] for row in selection_rows if isinstance(row.get("qid"), str))
        accepted = {row["qid"] for row in outcome_rows if row.get("status") == "accepted" and isinstance(row.get("qid"), str)}
        accepted_qids.extend(sorted(accepted))
        for pin_path in (window / "source-pins.jsonl",):
            try:
                for line in pin_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        pin = json.loads(line)
                        source_url = pin.get("content", {}).get("source_url")
                        if source_url:
                            urls.add(source_url)
            except (OSError, ValueError):
                pass
        selected_count = int(manifest.get("selected_authorities", 0))
        outcome_count = len(outcome_rows)
        gap = selected_count - outcome_count
        if gap:
            gaps.append({"window": window.name, "selected": selected_count, "outcomes": outcome_count, "unrecorded_selection_count": gap})
        http_requests += int(manifest.get("network_requests", 0))
        supported_path = window / "supported" / "records.jsonl"
        source_pins_path = window / "source-pins.jsonl"
        windows.append({
            "window": window.name,
            "manifest_path": str(manifest_path.resolve()),
            "supported_records_path": str(supported_path.resolve()) if supported_path.exists() else None,
            "supported_records_sha256": hashlib.sha256(supported_path.read_bytes()).hexdigest() if supported_path.exists() else None,
            "source_pins_path": str(source_pins_path.resolve()) if source_pins_path.exists() else None,
            "source_pins_sha256": hashlib.sha256(source_pins_path.read_bytes()).hexdigest() if source_pins_path.exists() else None,
            "selected_authorities": selected_count,
            "outcome_rows": outcome_count,
            "accepted_records": int(manifest.get("accepted_records", 0)),
            "network_requests": int(manifest.get("network_requests", 0)),
            "selection_artifact": (window / "selections.json").exists(),
        })

    outcome_counts = Counter(outcome_qids)
    accepted_counts = Counter(accepted_qids)
    window_records = set()
    for record_path in root.glob("content-window-*/records.jsonl"):
        window_records.update(_qids_from_record(record_path))
    supported_records = set()
    # v7 is the latest validated content projection and contains the 231
    # first-stage records (including the 31 replacements).  Earlier v1-v6
    # projections are superseded versions and must not inflate the count.
    for record_path in root.glob("content-v7/**/content-source-records.jsonl"):
        supported_records.update(_qids_from_record(record_path))
    supported_records.update(_qids_from_record(root / "supported-content-v2" / "records.jsonl"))
    multilingual = root / "content-multilingual-v1" / "adapted-v1" / "records.jsonl"
    supported_records.update(_qids_from_record(multilingual))
    distinct_success = window_records | supported_records
    result = {
        "version": "scale-content-window-audit-v1",
        "window_count": len(windows),
        "windows": windows,
        "outcome_rows": len(outcome_qids),
        "outcome_distinct_qids": len(outcome_counts),
        "outcome_duplicate_qids": sum(1 for count in outcome_counts.values() if count > 1),
        "outcome_max_repeat": max(outcome_counts.values(), default=0),
        "accepted_outcome_rows": len(accepted_qids),
        "accepted_distinct_qids": len(accepted_counts),
        "window_record_distinct_qids": len(window_records),
        "supported_distinct_qids": len(supported_records),
        "distinct_success_qids": len(distinct_success),
        "source_pin_distinct_urls": len(urls),
        "manifest_network_requests": http_requests,
        "selection_artifact_gaps": gaps,
        "selection_rows_observed": len(selected_qids),
        "selection_distinct_qids_observed": len(set(selected_qids)),
        "selection_artifacts_missing": sum(1 for window in windows if not window["selection_artifact"]),
        "model_calls": 0,
        "network_calls_for_audit": 0,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/autonomous-next-v3/scale-30000-v1"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    output = args.output or args.root / "content-window-audit-v1.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["output"] = str(output.resolve())
    result["output_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
