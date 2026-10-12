"""Offline builder/finalizer for a new autonomous PoC database namespace."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from recommend_book.autonomous_database import build_autonomous_database, finalize_autonomous_database


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build')
    for name in ('base-database', 'enriched-database', 'delta-inputs', 'delta-manifest',
                 'delta-results', 'delta-run-manifest', 'delta-terminal-proof', 'output'):
        build.add_argument('--' + name, type=Path, required=True)
    for name in ('accepted-source-records', 'accepted-inputs', 'accepted-ledger', 'author-role-audit'):
        build.add_argument('--' + name, type=Path)
    final = commands.add_parser('finalize')
    final.add_argument('--namespace', type=Path, required=True)
    final.add_argument('--output', type=Path, required=True)
    for name in ('run-dir', 'execution-context-manifest', 'migration-manifest', 'terminal-proof', 'cli-profile', 'scope-quarantine'):
        final.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'build':
        result = build_autonomous_database(baseline_overlay={
            'base_database': args.base_database, 'enriched_database': args.enriched_database,
            'delta_inputs': args.delta_inputs, 'delta_manifest': args.delta_manifest,
            'results': args.delta_results, 'run_manifest': args.delta_run_manifest,
            'terminal_proof': args.delta_terminal_proof}, output_dir=args.output,
            accepted_source_records=args.accepted_source_records,
            accepted_inputs=args.accepted_inputs, accepted_ledger=args.accepted_ledger, author_role_audit=args.author_role_audit)
    else:
        result = finalize_autonomous_database(namespace=args.namespace, output_dir=args.output,
            run_dir=args.run_dir, execution_context_manifest=args.execution_context_manifest,
            migration_manifest=args.migration_manifest, terminal_proof=args.terminal_proof, cli_profile=args.cli_profile,
            scope_quarantine=args.scope_quarantine)
    print(json.dumps({'state': result['state'], 'work_count': result['work_count'],
                      'changed_count': result['changed_count'], 'output': str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
