"""Freeze work-level classification inputs and an identical 100-work pilot."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path

from recommend_book.database import DatasetDB
from recommend_book.poc import year_group


def pilot_inputs(inputs, size=100, seed=1729):
    if len(inputs) < size or len({w.work_id for w in inputs}) != len(inputs):
        raise ValueError("Pilot needs enough distinct work-level inputs")
    quotas = {"ja": round(size * .35), "en": round(size * .35)}
    quotas["other"] = size - sum(quotas.values())
    pools = defaultdict(lambda: defaultdict(list))
    def order(work):
        return hashlib.sha256(f"{seed}:{work.work_id}".encode()).hexdigest()
    for work in inputs:
        if work.expression_id is not None:
            raise ValueError("Work-level pilot excludes expression-specific input rows")
        language = work.original_language or "unknown"
        group = language if language in {"ja", "en"} else "other"
        kinds = {e.kind for e in work.evidence if e.llm_input_allowed}
        depth = next((k for k in ("excerpt", "synopsis", "note") if k in kinds), "facts")
        pools[group][(language, year_group(work.first_publication_year), depth)].append(work)
    selected, remaining, deficits = [], [], {}
    for group, quota in quotas.items():
        cells = {key: deque(sorted(rows, key=order)) for key, rows in sorted(pools[group].items())}
        picked = []
        while len(picked) < quota and any(cells.values()):
            for queue in cells.values():
                if queue and len(picked) < quota:
                    picked.append(queue.popleft())
        deficits[group] = quota - len(picked)
        selected.extend(picked)
        remaining.extend(w for queue in cells.values() for w in queue)
    selected.extend(sorted(remaining, key=order)[:size - len(selected)])
    selected.sort(key=order)
    return selected, deficits


def write_inputs(path, inputs):
    content = "".join(w.model_dump_json() + "\n" for w in inputs).encode()
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Frozen snapshot output must be new; existing snapshots are immutable")
    with DatasetDB(args.database) as db:
        inputs = [w for w in db.work_inputs() if w.expression_id is None]
        pilot, deficits = pilot_inputs(inputs, args.pilot_size, args.seed)
        snapshot = {"database": str(args.database.resolve()), "stats": db.stats(), "selected_work_ids": [w.work_id for w in inputs]}
        args.output.mkdir(parents=True)
        manifest = {"seed": args.seed, "work_count": len(inputs), "pilot_count": len(pilot), "work_inputs_sha256": write_inputs(args.output / "work-inputs.jsonl", inputs), "pilot_inputs_sha256": write_inputs(args.output / "pilot100.jsonl", pilot), "pilot_original_languages": dict(Counter(w.original_language or "unknown" for w in pilot)), "pilot_year_groups": dict(Counter(year_group(w.first_publication_year) for w in pilot)), "pilot_evidence_kinds": dict(Counter(e.kind for w in pilot for e in w.evidence)), "pilot_stratum_deficits": deficits, "sampling": "within-frame language, year, and evidence-depth cells for operational pilot; no world representativeness claim", "snapshot": snapshot, "human_quality_verified": False}
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "snapshot"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
