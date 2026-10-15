"""Run a read-only real dataset audit, then write JSON and local HTML."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from recommend_book.evaluation.audit import audit_dataset, write_audit_report
from recommend_book.rights import RightsRegistry


def load(path):
    path=Path(path)
    if path.is_dir():
        path=path/'results.jsonl'
    text=path.read_text('utf-8-sig')
    return [json.loads(line) for line in text.splitlines() if line.strip()] if path.suffix=='.jsonl' else json.loads(text)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--inputs',type=Path,help='Fixed WorkInput JSONL; absent: reconstruct current DB inputs read-only')
    parser.add_argument('--results',type=Path,help='Validated results.jsonl or its run directory')
    parser.add_argument('--gold',type=Path,help='Explicit provenance/truth/partition human or reference annotations')
    parser.add_argument('--rights',type=Path,help='Rights registry; default: repository registry')
    parser.add_argument('--root',type=Path,default=Path.cwd(),help='Explicit allowed root for source raw-file integrity checks')
    parser.add_argument('--skip-raw-hash',action='store_true',help='Mark raw integrity not_checked')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        report=audit_dataset(args.database,inputs=load(args.inputs) if args.inputs else None,results=load(args.results) if args.results else None,
            gold=load(args.gold) if args.gold else None,rights_registry=RightsRegistry.from_file(args.rights) if args.rights else None,root_dir=args.root,verify_raw=not args.skip_raw_hash)
        artifacts=write_audit_report(report,args.output)
        print(json.dumps({'counts':report['counts'],'rights_provenance_100_percent':report['provenance']['all_required_references_100_percent'],
                          'quality_status':report['quality']['status'],'findings':len(report['findings']),'artifacts':artifacts},ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error) as error:
        print(f'Audit error: {error}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
