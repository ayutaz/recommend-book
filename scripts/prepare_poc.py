"""Freeze selected source records and ingest them into a provenance-preserving DB."""
import argparse
import hashlib
import json
from pathlib import Path

from recommend_book.database import DatasetDB
from recommend_book.ingestion.core import read_jsonl, write_jsonl
from recommend_book.poc import render_linked_evidence, select_works


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    records = [render_linked_evidence(r, labels) for r in read_jsonl(args.input)]
    selected, manifest = select_works(records, target=args.target, seed=args.seed)
    selected_path = args.output / "selected-sources.jsonl"
    write_jsonl(selected, selected_path)
    db = DatasetDB(args.output / "dataset.sqlite")
    manifest["ingest"] = db.ingest(selected)
    inputs = [w for w in db.work_inputs() if w.expression_id is None]
    path = args.output / "work-inputs.jsonl"
    path.write_text("".join(w.model_dump_json() + "\n" for w in inputs), encoding="utf-8")
    manifest["input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["source_sha256"] = hashlib.sha256(selected_path.read_bytes()).hexdigest()
    manifest["canonical_work_inputs"] = len(inputs)
    manifest["candidate_input_sha256"] = hashlib.sha256(args.input.read_bytes()).hexdigest()
    manifest["linked_labels_sha256"] = hashlib.sha256(args.labels.read_bytes()).hexdigest()
    manifest["state"] = "metadata_snapshot_not_human_validated"
    (args.output / "selection-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
