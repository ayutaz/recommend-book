"""Usage summary for the recovered 20k namespace.

The v2 summarizer remains frozen for native/review token accounting and the
99-row quarantine.  This layer validates and reports model-zero continuation
holds separately; those holds never contribute token, native-failure, or
adopted totals.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V2_PATH = ROOT / "scripts/summarize_scale20k_usage_v2.py"
V2_SHA256 = "ab32e1e5e0a6e388d3ee7130592c63c7ad5d1f6f6d19aec75bc02bde25556834"
HOLD_WINDOWS = {161, 196, 197}


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_v2():
    if sha256(V2_PATH) != V2_SHA256:
        raise ValueError("Frozen v2 usage summarizer changed")
    spec = importlib.util.spec_from_file_location("scale20k_usage_v2_frozen", V2_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("Unable to load frozen v2 usage summarizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_unstarted_windows(audit: dict[str, Any]) -> list[dict[str, Any]]:
    raw = audit.get("excluded_unstarted_windows", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError("excluded_unstarted_windows is missing")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    legacy = audit.get("legacy_recovery_proofs", {}).get("unstarted")
    if isinstance(legacy, dict):
        # Gate161 is stored in the immutable audit3 proof, not in the
        # recovery-gate195 ancestry.  Keep it in the same hold accounting.
        legacy_item = {**legacy, "window_index": 161, "status": "held_unstarted_runtime_import_failure",
                       "run_name": "scale20k-content-6801-6900-v2",
                       "eligible_before": legacy.get("historical_eligible_unchanged", 15952),
                       "eligible_after": legacy.get("historical_eligible_unchanged", 15952),
                       "same_inputs_retried": legacy.get("same_inputs_retried", 0),
                       "native_rows_for_selected": legacy.get("catalogue_rows_remaining", 0),
                       "hold": legacy.get("hold"),
                       "native_failures": legacy.get("native_failures", 0),
                       "model_calls": legacy.get("model_calls", 0)}
        raw = [legacy_item, *raw]
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Unstarted window is not an object")
        index = item.get("window_index")
        selected = item.get("selected")
        if (type(index) is not int or index in seen or index not in HOLD_WINDOWS
                or type(selected) is not int or selected <= 0):
            raise ValueError("Unstarted window identity/count is invalid")
        seen.add(index)
        for key in ("model_calls", "native_failures", "native_rows_for_selected", "same_inputs_retried"):
            if type(item.get(key)) is not int or item[key] != 0:
                raise ValueError("Unstarted window acquired model/native work: " + str(index))
        if item.get("status") not in {"held_unstarted_runtime_import_failure",
                                      "held_unstarted_order_check", "held_unstarted_alias_check"}:
            raise ValueError("Unstarted window status is not a held pre-model state")
        if type(item.get("eligible_before")) is not int or type(item.get("eligible_after")) is not int:
            raise ValueError("Unstarted window catalogue counts are missing")
        if item["eligible_before"] != item["eligible_after"]:
            raise ValueError("Unstarted window changed the catalogue")
        hold = item.get("hold")
        if index == 161:
            if not isinstance(hold, dict) or not isinstance(hold.get("sha256"), str):
                raise ValueError("Legacy unstarted81 hold pin is missing")
            if isinstance(legacy, dict):
                processes = item.get("processes")
                if not isinstance(processes, dict) or set(processes) != {
                        "preflight", "controller_worker", "controller_observer", "sequential_queue"}:
                    raise ValueError("Legacy unstarted81 process witness set differs")
        elif not isinstance(hold, dict) or not isinstance(hold.get("sha256"), str):
            raise ValueError("Unstarted hold pin is missing")
        result.append({"window_index": index, "run_name": item.get("run_name"),
                       "selected": selected, "model_calls": 0, "native_failures": 0,
                       "native_rows_for_selected": 0, "same_inputs_retried": 0,
                       "status": item["status"], "hold": hold,
                       "eligible_before": item["eligible_before"],
                       "eligible_after": item["eligible_after"]})
    if seen != HOLD_WINDOWS:
        raise ValueError("The recovered hold chain is incomplete")
    return result


def summarize(*, audit_path: Path, audit_sha256: str, base_dir: Path,
              output: Path | None = None, minimum_eligible_native: int = 20_000,
              minimum_new_native: int = 10_000) -> dict[str, Any]:
    audit_file = Path(audit_path).resolve(strict=True)
    if sha256(audit_file) != audit_sha256:
        raise ValueError("audit SHA differs")
    audit = json.loads(audit_file.read_text(encoding="utf-8"))
    if not isinstance(audit, dict) or audit.get("version") != "scale20k-catalogue-audit-v4":
        raise ValueError("Current v4 final audit is required")
    holds = validate_unstarted_windows(audit)
    v2 = _load_v2()
    healthy = v2.summarize(audit_path=audit_file, audit_sha256=audit_sha256,
                           base_dir=base_dir, output=None,
                           minimum_eligible_native=minimum_eligible_native,
                           minimum_new_native=minimum_new_native)
    report = dict(healthy)
    report.update({
        "version": "scale20k-usage-summary-v3",
        "excluded_unstarted_windows": holds,
        "excluded_unstarted_totals": {
            "windows": len(holds),
            "selected": sum(item["selected"] for item in holds),
            "model_calls": 0,
            "native_failures": 0,
            "native_rows_for_selected": 0,
            "adopted": None,
            "usage": None,
            "note": "Pre-model holds are reported separately and never enter healthy or failed native totals.",
        },
        "excluded_authority_totals": {
            "legacy_failed_attempt": audit.get("excluded_authority_totals", {}).get("legacy_failed_attempt", 15),
            "legacy_quarantined_failed": audit.get("excluded_authority_totals", {}).get("legacy_quarantined_failed", 14),
            "legacy_unstarted": audit.get("excluded_authority_totals", {}).get("legacy_unstarted", 81),
            "current_failed": audit.get("excluded_authority_totals", {}).get("current_failed", 99),
            "current_unstarted": audit.get("excluded_authority_totals", {}).get("current_unstarted", 185),
            "all_unstarted": sum(item["selected"] for item in holds),
            "cli212_premodel_hold": audit.get("excluded_authority_totals", {}).get("cli212_premodel_hold", 0),
        },
        "cli212_premodel_hold": audit.get("cli212_premodel_hold"),
        "v2_frozen_sha256": V2_SHA256,
    })
    if output is not None:
        destination = Path(output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--audit-sha256", required=True)
    parser.add_argument("--base-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(audit_path=args.audit, audit_sha256=args.audit_sha256,
                       base_dir=args.base_dir, output=args.output)
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "excluded_unstarted_windows": len(report["excluded_unstarted_windows"])},
                     ensure_ascii=True))


if __name__ == "__main__":
    main()
