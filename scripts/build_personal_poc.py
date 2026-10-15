"""Build the pinned local personal recommendation PoC without any LLM calls."""
import argparse
import json
from pathlib import Path

from recommend_book.recommendation.personal_poc import build_personal_poc

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build_personal_poc(Path(__file__).resolve().parents[1], args.output)
    print(json.dumps({'version': report['version'], 'ranking': report['ranking'],
                      'comparison_counts': report['comparison']['counts']}, ensure_ascii=False))
