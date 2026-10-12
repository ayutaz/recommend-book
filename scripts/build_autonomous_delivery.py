"""Build a new local recommendation delivery; no new personal input requested."""
import argparse
import json

from recommend_book.autonomous_delivery import run_delivery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world-database', required=True)
    parser.add_argument('--narou-database', required=True)
    parser.add_argument('--annotations', required=True)
    parser.add_argument('--selection', required=True, dest='selection_path')
    parser.add_argument('--baseline-manifest', default='data/autonomous-poc-v1/baseline-manifest.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--limit', type=int, default=100)
    print(json.dumps(run_delivery(**vars(parser.parse_args())), ensure_ascii=False))


if __name__ == '__main__':
    main()
