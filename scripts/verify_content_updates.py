"""Bounded synthetic content/update/resume verification. No model/network."""
import argparse
import json

from recommend_book.content_update_verification import verify_content_updates, run_fixture_worker, run_fixed_shards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--output')
    modes.add_argument('--fixture-worker')
    modes.add_argument('--fixture-coordinator')
    parser.add_argument('--pause-after-first', action='store_true')
    parser.add_argument('--pause-before-adoption-of', type=int)
    parser.add_argument('--run-directory')
    args = parser.parse_args()
    if args.fixture_worker:
        run_fixture_worker(args.fixture_worker, run_directory=args.run_directory)
    elif args.fixture_coordinator:
        run_fixed_shards(args.fixture_coordinator, pause_after_first=args.pause_after_first,
            pause_before_adoption_of=args.pause_before_adoption_of)
    else:
        report = verify_content_updates(args.output)
        print(json.dumps({'output': args.output, 'synthetic_fixture': True, 'model_started': False,
            'resumed_shards': report['interruption_resume']['reused_shards'],
            'updated_features': report['content_update']['new_features'],
            'withdrawn_display_count': report['permission_withdrawal']['after_display_count']}))


if __name__ == '__main__':
    main()
