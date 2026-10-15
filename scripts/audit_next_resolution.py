import argparse
from pathlib import Path
from recommend_book.next_resolution import audit_next_resolution
from recommend_book.autonomous_resolution import canonical


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database',type=Path,default=Path('data/autonomous-poc-v1/database/final-v1/dataset.sqlite'))
    p.add_argument('--scope',type=Path,default=Path('data/autonomous-poc-v1/database/final-v1/work-scope-quarantine.source.json'))
    p.add_argument('--aggregate',type=Path,default=Path('data/evaluation/autonomous-resolution-v3-aggregate-v1/audit.json'))
    p.add_argument('--inputs',type=Path,default=Path('data/autonomous-poc-v1/resolution/v3/inputs.jsonl'))
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();result=audit_next_resolution(a.database,a.scope,a.aggregate,a.inputs,a.output)
    print(canonical({k:v for k,v in result.items() if k not in ('source_sha256','output_sha256')}))


if __name__=='__main__':main()
