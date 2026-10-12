"""Read classification job snapshots; process liveness is checked separately."""
import argparse
from collections import Counter
import json
from pathlib import Path


def progress(directory):
    phases = sorted(directory.glob('concurrency-*')) or [directory]
    output = {'directory': str(directory), 'process_liveness': 'not_checked', 'runs': {}}
    for phase in phases:
        states, unreadable = [], []
        for path in (phase / 'jobs').glob('*.json'):
            try:
                states.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                unreadable.append(path.name)
        output['runs'][phase.name] = {
            'created_jobs': len(states),
            'states': dict(Counter(state.get('status', 'unknown') for state in states)),
            'job_attempts': sum(state.get('attempts', 0) for state in states),
            'error_categories': dict(Counter(state['error_category'] for state in states if state.get('error_category'))),
            'manifest_exists': (phase / 'manifest.json').is_file(),
            'unreadable_jobs': unreadable,
        }
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('Run directory does not exist')
    print(json.dumps(progress(args.directory), ensure_ascii=False, indent=2))
