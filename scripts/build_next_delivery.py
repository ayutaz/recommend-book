"""Build a guarded world bibliography and owned-title reference ranking."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from recommend_book.usage_guard import UsageGuard
from recommend_book.recommendation.next_evaluation import load_owned_ratings,_sha,_spec_sha
from recommend_book.next_delivery import build_next_delivery,write_next_delivery


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['bibliographic-only','lexical-reference','classified-reference'],required=True)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--owned-database',type=Path,default=ROOT/'data/reference-code/narou-personal-recommender/data/narou.sqlite')
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--ledger-sha256',required=True)
    parser.add_argument('--private-ledger',type=Path)
    parser.add_argument('--private-ledger-sha256')
    parser.add_argument('--origins',type=Path)
    parser.add_argument('--spec',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=100)
    args=parser.parse_args()
    from recommend_book.next_database import load_next_world_database
    guard=UsageGuard(args.ledger,expected_ledger_sha256=args.ledger_sha256)
    loaded=load_next_world_database(args.database,guard=guard,usage='display',content_classified=args.mode=='classified-reference')
    if args.mode=='bibliographic-only':
        result=build_next_delivery(loaded['rows'],guard=guard,mode=args.mode,limit=args.limit)
        artifacts={'world_database_sha256':_sha(args.database),'world_ledger_sha256':args.ledger_sha256}
    else:
        if any(v is None for v in (args.private_ledger,args.private_ledger_sha256,args.origins,args.spec)):
            parser.error('Reference mode requires --private-ledger, --private-ledger-sha256, --origins and --spec')
        private_guard=UsageGuard(args.private_ledger,expected_ledger_sha256=args.private_ledger_sha256)
        reference=load_owned_ratings(args.owned_database)
        spec=json.loads(args.spec.read_text(encoding='utf-8'))
        if spec['source_database_sha256']!=_sha(args.owned_database):raise ValueError('Spec owned source differs')
        if _sha(args.spec)!=_spec_sha(spec):raise ValueError('Frozen spec exact bytes differ')
        ids=[r['work_id'] for r in json.loads(args.origins.read_text(encoding='utf-8'))['rows']]
        result=build_next_delivery(loaded['rows'],reference['rows'],guard=guard,private_guard=private_guard,origin_ids=ids,spec=spec,limit=args.limit,mode=args.mode)
        artifacts={'world_database_sha256':_sha(args.database),'owned_database_sha256':_sha(args.owned_database),
            'preregistration_sha256':_sha(args.spec),'origin_inventory_sha256':_sha(args.origins)}
    result['artifacts']=artifacts
    write_next_delivery(result,args.output)
    print(json.dumps({'ranking_n':len(result['ranking']),'audit':result['audit'],
        'output_sha256':_sha(args.output/'delivery.json')},ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
