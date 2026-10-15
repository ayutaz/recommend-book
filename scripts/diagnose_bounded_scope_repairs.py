"""Inspect the fixed 20+3 cached metadata cases; never send text to a model."""
import argparse
import json
from pathlib import Path

from recommend_book.bounded_scope_repair import OUTPUT, diagnose


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = diagnose(output=args.output)
    print(json.dumps({k: report[k] for k in (
        "status", "classification_work_n", "classification_label_n", "identity_pair_n",
        "accepted_substantive_repairs", "new_model_calls", "database_mutations")}))


if __name__ == "__main__":
    main()
