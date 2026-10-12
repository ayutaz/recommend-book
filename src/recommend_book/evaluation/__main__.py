"""Offline evaluation helpers: python -m recommend_book.evaluation --help."""
import argparse
import json
from pathlib import Path
from . import create_review_pack, create_split_manifest, evaluate_results, import_annotations, write_report


def load(path):
    file=Path(path)
    text=file.read_text('utf-8-sig')
    return [json.loads(line) for line in text.splitlines() if line.strip()] if file.suffix=='.jsonl' else json.loads(text)


def main():
    parser=argparse.ArgumentParser(description='Frozen review pack / annotation / evaluation tools')
    sub=parser.add_subparsers(dest='command',required=True)
    review=sub.add_parser('review')
    review.add_argument('--inputs',required=True)
    review.add_argument('--results')
    review.add_argument('--output',required=True)
    review.add_argument('--split',action='store_true',help='Freeze dev150/calibration100/holdout150; requires at least400 works')
    imp=sub.add_parser('import')
    imp.add_argument('--annotations',required=True)
    imp.add_argument('--pack',required=True)
    imp.add_argument('--output',required=True)
    evaluation=sub.add_parser('evaluate')
    evaluation.add_argument('--results',required=True)
    evaluation.add_argument('--gold')
    evaluation.add_argument('--taxonomy',default='config/taxonomy.json')
    evaluation.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='review':
        inputs=load(args.inputs)
        if args.split:
            manifest=create_split_manifest(inputs,args.output)
            chosen={w for values in manifest['partitions'].values() for w in values}
            inputs=[work for work in inputs if work['work_id'] in chosen]
        results=load(args.results) if args.results else []
        chosen={work['work_id'] for work in inputs}
        result=create_review_pack(inputs,[r for r in results if r['work_id'] in chosen],args.output)
    elif args.command=='import':
        result=import_annotations(args.annotations,args.pack)
        target=Path(args.output)
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in result),encoding='utf-8')
        result={'validated_annotation_records':len(result),'output':str(target),'verification_scope':'schema, snapshot and declarations; not verification of real human identity/knowledge'}
    else:
        report=evaluate_results(load(args.results),load(args.gold) if args.gold else [],load(args.taxonomy))
        result=write_report(report,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
