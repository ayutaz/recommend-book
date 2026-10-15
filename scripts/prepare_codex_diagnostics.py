"""Local reference-only STEP3 packet preparation. No model runner is imported."""
import argparse
import json
from pathlib import Path

from recommend_book.diagnostic_preparation import OUTPUT, prepare, verify_preparation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--manifest-sha256", help="Original externally retained receipt digest; required for --verify")
    args = parser.parse_args()
    if args.verify:
        if not args.manifest_sha256:
            parser.error("--verify requires --manifest-sha256 from the original trusted receipt")
        result = verify_preparation(output=args.output, manifest_sha256=args.manifest_sha256)
    else:
        report = prepare(output=args.output)
        result = {k: report[k] for k in ["version", "status", "classification_work_n", "identity_pair_n", "new_model_calls"]}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
