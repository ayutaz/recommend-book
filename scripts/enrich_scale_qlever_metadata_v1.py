"""Repair linked labels on saved QLever catalogue rows.

This worker is intentionally separate from collection.  It follows rows after
the collector has committed them, so a collector crash or a held label request
does not cause a primary work request to be repeated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import hashlib

from recommend_book.scale_linked_metadata_v2 import (
    DEFAULT_CACHE,
    DEFAULT_CHECKPOINT,
    DEFAULT_GATE,
    DEFAULT_LABEL_RAW,
    checkpoint_default,
    load_checkpoint,
    process_batch,
    save_json,
    scan_candidates,
)


ROOT = Path("data/autonomous-next-v3/scale-100000-v1")
CATALOGUE = Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite")
BASELINE = ROOT / "baseline-works-32130.jsonl"
BASELINE_SHA256 = "aebcc125910a868227ea3d8156d764efc9e43209d3b3daaffbb4b9b149efa62b"
QLEVER_ROOTS = (ROOT / "acquisition-qlever-v1/raw", ROOT / "acquisition-qlever-desc-v1/raw")
PENDING = (ROOT / "acquisition-qlever-v1/pending-records.jsonl",
           ROOT / "acquisition-qlever-desc-v1/pending-records.jsonl")


def baseline_ids(path: Path = BASELINE) -> set[str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError("hard-pinned baseline works snapshot changed")
    return {json.loads(line)["qid"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def pending_count(paths=PENDING) -> int:
    count = 0
    for path in paths:
        if path.exists():
            count += sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return count


def run_worker(*, catalogue: Path = CATALOGUE, qlever_roots=QLEVER_ROOTS,
               baseline: Path = BASELINE, checkpoint: Path = DEFAULT_CHECKPOINT,
               cache_paths=(DEFAULT_CACHE,), limit: int = 1000,
               once: bool = False, max_idle: int = 6, poll_seconds: float = 5.0,
               label_raw: Path = DEFAULT_LABEL_RAW, gate: Path = DEFAULT_GATE,
               cache_only: bool = False) -> dict:
    if limit < 1 or max_idle < 1 or poll_seconds < 0:
        raise ValueError("invalid worker bounds")
    state = load_checkpoint(checkpoint) if checkpoint.exists() else checkpoint_default()
    baseline = baseline_ids(baseline)
    totals = {"selected": 0, "updated": 0, "unchanged": 0, "held": 0, "stale": 0, "native": 0, "polls": 0}
    idle = 0
    while True:
        totals["polls"] += 1
        excluded_versions = {
            qid: item.get("to") or item.get("from", "")
            for qid, item in state.get("processed_versions", {}).items()
            if isinstance(item, dict)
        }
        excluded_versions.update({
            qid: item.get("version_id", "")
            for qid, item in state.get("held_versions", {}).items()
            if isinstance(item, dict)
        })
        # Exclude checkpointed versions while streaming the catalogue, then
        # stop at the batch limit.  This keeps held/processed prefixes from
        # stranding later rows without materializing the whole 100k catalogue.
        candidates = scan_candidates(catalogue, qlever_roots=qlever_roots, baseline_ids=baseline,
                                     limit=limit, excluded_versions=excluded_versions)
        candidates = [item for item in candidates
                      if state.get("processed_versions", {}).get(item["qid"], {}).get("from") != item["version_id"]
                      and state.get("held_versions", {}).get(item["qid"], {}).get("version_id") != item["version_id"]]
        candidates = candidates[:limit]
        if candidates:
            result = process_batch(catalogue, candidates, state, checkpoint_path=checkpoint,
                                   cache_paths=cache_paths, raw_root=label_raw, gate_path=gate,
                                   cache_only=cache_only)
            for key, value in result.items():
                if key in totals:
                    totals[key] += value
            idle = 0
            if once:
                break
        else:
            idle += 1
            save_json(checkpoint, state)
            if once or (idle >= max_idle and (cache_only or pending_count() == 0)):
                break
            if poll_seconds:
                time.sleep(poll_seconds)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=CATALOGUE)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--cache", type=Path, action="append", default=None)
    parser.add_argument("--qlever-root", type=Path, action="append", default=None)
    parser.add_argument("--label-raw", type=Path, default=DEFAULT_LABEL_RAW)
    parser.add_argument("--gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-idle", type=int, default=6)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--cache-only", action="store_true",
                        help="use saved cache/checkpoint proofs only; never issue HTTP requests")
    args = parser.parse_args()
    result = run_worker(catalogue=args.catalogue, baseline=args.baseline, checkpoint=args.checkpoint,
                        cache_paths=tuple(args.cache or (DEFAULT_CACHE,)),
                        qlever_roots=tuple(args.qlever_root or QLEVER_ROOTS),
                        label_raw=args.label_raw, gate=args.gate, limit=args.limit,
                        once=args.once, max_idle=args.max_idle, poll_seconds=args.poll_seconds,
                        cache_only=args.cache_only)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
