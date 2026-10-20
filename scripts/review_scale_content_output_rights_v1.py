"""Observe actual output-rights review and seal a public v2 content export."""
import argparse
import json
from pathlib import Path

from recommend_book.scale_content_classification_v2 import create_reviewed_export


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-parent', type=Path, required=True)
    parser.add_argument('--native-parent-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(create_reviewed_export(args.native_parent, args.native_parent_sha256, args.output), ensure_ascii=True))


if __name__ == '__main__':
    main()
