"""Run the bounded synthetic update proof without accessing production data."""
import argparse
import json

from recommend_book.dataset_update_verification import verify_dataset_updates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New output directory for synthetic fixture artifacts')
    args = parser.parse_args()
    report = verify_dataset_updates(args.output)
    print(json.dumps({'synthetic_fixture': True, 'output': args.output,
        'candidate_counts': {key: value['candidate_count'] for key, value in report['phases'].items()},
        'protected_snapshots_unchanged': report['protected_snapshots_unchanged']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
