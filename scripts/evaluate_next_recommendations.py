"""Guarded private owned-observation fixed evaluation; stdout is scalar only."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from recommend_book.usage_guard import UsageGuard
from recommend_book.recommendation.next_evaluation import (
    load_owned_ratings,evaluate_owned_titles,_immutable_json,_sha,_spec_sha)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'data/reference-code/narou-personal-recommender/data/narou.sqlite')
    parser.add_argument('--spec',type=Path,required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--ledger-sha256',required=True)
    parser.add_argument('--origins',type=Path,required=True,help='Private origin inventory with rows/work_id')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    source=load_owned_ratings(args.database);spec=json.loads(args.spec.read_text(encoding='utf-8'))
    if spec['source_database_sha256']!=_sha(args.database):raise ValueError('Spec source differs')
    if _sha(args.spec)!=_spec_sha(spec):raise ValueError('Frozen spec exact bytes differ')
    origin=json.loads(args.origins.read_text(encoding='utf-8'))
    ids=[r['work_id'] for r in origin['rows']]
    if len(ids)!=335:raise ValueError('Complete335 origins required')
    result=evaluate_owned_titles(source['rows'],guard=UsageGuard(args.ledger,expected_ledger_sha256=args.ledger_sha256),origin_ids=ids,spec=spec)
    result['artifacts']={'source_database_sha256':_sha(args.database),'spec_sha256':_sha(args.spec),
        'origin_inventory_sha256':_sha(args.origins)}
    _immutable_json(args.output/'evaluation.json',result)
    print(json.dumps({'rated_n':result['counts']['rated_n'],'methods':result['methods'],
        'quality_verified':False,'output_sha256':_sha(args.output/'evaluation.json')},ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
