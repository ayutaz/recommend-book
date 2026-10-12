"""Read completed raw run; write a new audit namespace, never rewrite results."""
import argparse
from collections import Counter
from pathlib import Path
from recommend_book.autonomous_resolution import canonical,digest
from recommend_book.autonomous_resolution_audit import audit_run,strict_json,validate_output_boundary


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--role-audit',type=Path,required=True);args=p.parse_args()
    if args.output.exists():p.error('Existing audit namespace refused')
    manifest=strict_json((args.run/'manifest.json').read_bytes())
    protected=[args.run/'inputs.jsonl',args.run/'schema.json',*manifest['source_sha256'],*manifest['execution_artifact_sha256']]
    args.output=validate_output_boundary(args.output,args.run,args.role_audit,protected)
    report=audit_run(args.run,args.role_audit)
    report['role_audit_sha256']=digest(args.role_audit.read_bytes())
    args.output.mkdir(parents=True)
    (args.output/'audit.json').write_text(canonical(report)+'\n',encoding='utf8')
    (args.output/'accepted-authority-decisions.json').write_text('[]\n',encoding='utf8')
    (args.output/'audited-proposals.jsonl').write_text(''.join(canonical(c)+'\n' for c in report['cases']),encoding='utf8')
    (args.output/'retry-subset-case-ids.json').write_text(canonical(report['retry_subset_case_ids'])+'\n',encoding='utf8')
    print(canonical({'case_count':report['case_count'],'attempt_count':report['attempt_count'],'decision_counts':dict(Counter(c['audited_decision'] for c in report['cases'])),'held_count':sum(c['held'] for c in report['cases']),'strict_issue_count':sum(bool(c['issues']) for c in report['cases']),'retry_subset_count':len(report['retry_subset_case_ids']),'missing':0,'human_gold':0,'authority_accepted':0,'run_files_unchanged':True}))


if __name__=='__main__':main()
