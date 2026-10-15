"""Offline next-generation work projections; no model or network invocation."""
from __future__ import annotations

import argparse
import json

from recommend_book.next_database import (
    build_next_database, import_next_classifications, inspect_rating_world_binding,
    write_next_inspection,
)
from recommend_book.usage_guard import UsageGuard
from recommend_book.sealed_projection import seal_next_projections


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    inspect=commands.add_parser('inspect')
    build=commands.add_parser('build')
    for command in (inspect,build):
        command.add_argument('--source-database',required=True)
        command.add_argument('--output-dir',required=True)
    build.add_argument('--authority-metadata-relations')
    build.add_argument('--supplementary-type-audit')
    ratings=commands.add_parser('rating-binding')
    ratings.add_argument('--world-database',required=True)
    ratings.add_argument('--owned-rating-database',required=True)
    ratings.add_argument('--output-dir',required=True)
    adopt=commands.add_parser('import-classification')
    for flag in ('source-database','ordered-inputs','run-dir','execution-context-manifest',
                 'migration-manifest','terminal-proof','cli-profile','output-dir','usage-ledger'):
        adopt.add_argument('--'+flag,required=True)
    adopt.add_argument('--quality-adoption-review')
    seal=commands.add_parser('seal-projections')
    for flag in ('base-database','bibliography-inputs','classification-inputs','alias-inputs',
                 'projection-manifest','usage-ledger','output-dir'):
        seal.add_argument('--'+flag,required=True)
    args=vars(parser.parse_args())
    operation=args.pop('command')
    if operation=='inspect':
        report=write_next_inspection(**args)
    elif operation=='build':
        report=build_next_database(**args)
    elif operation=='rating-binding':
        report=inspect_rating_world_binding(args['world_database'],args['owned_rating_database'],args['output_dir'])
    elif operation=='seal-projections':
        report=seal_next_projections(**args)
    else:
        args['guard']=UsageGuard(args.pop('usage_ledger'))
        report=import_next_classifications(**args)
    # Aggregate-only stdout, especially for private owned-rating diagnostics.
    counts={key:value for key,value in report.items() if key in (
        'version','state','historical_work_count','historical_world_work_count','bibliographic_work_count',
        'scope_quarantined_count','classification_candidate_count','classification_adopted_count',
        'alias_input_count','relationship_assertion_count','supplementary_scope_annotation_count',
        'owned_ratings_count','verified_binding_count','explicit_ncode_candidate_count','held_candidate_count',
        'raw_classification_saved_count','feature_adopted_work_count','conflict_held_count','abstained_work_count',
        'quality_held_count','quality_uncertain_count',
        'selected','completed','held','legacy_features_adopted','llm_started')}
    print(json.dumps(counts,ensure_ascii=True))


if __name__=='__main__':
    main()
