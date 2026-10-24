"""Withdraw the gate195 display-adoption failure using the frozen transaction.

Only the independently pinned failed control changes. The previous quarantine
implementation and all input, classification, review and publication proofs
remain immutable. No model is invoked.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1"
HELPER = ROOT / "scripts/quarantine_scale20k_failed_window_v1.py"
HELPER_SHA = "25a0f2560b2c4eb4564667d9b79a02b7cca8e572abf7c110b883fc6626bd7d93"
CONTROL = BASE / "window-controls/scale20k-content-10201-10300-v3.json"
CONTROL_SHA = "e688d5d3cef1396104bdf36f046bed85be3cd3c59a3a99763f927f9c57a3625d"
BASELINE_SHA = "5734eda48467012f4c257a57027e655cb829ba309730a5dc2cce2a01c47ddf36"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError("Frozen quarantine transaction differs")
    spec = importlib.util.spec_from_file_location("_gate195_quarantine", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if hashlib.sha256(HELPER.read_bytes()).hexdigest() != HELPER_SHA:
        raise ValueError("Frozen quarantine transaction changed during import")
    result = helper.quarantine(
        database=ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite",
        control=CONTROL,
        control_sha=CONTROL_SHA,
        baseline=BASE / "baseline-native-10000.jsonl",
        baseline_sha=BASELINE_SHA,
        output=args.output,
    )
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
