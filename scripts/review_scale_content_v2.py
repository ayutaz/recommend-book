"""Disjoint <=50-work genuine reviews after an observed transport-size failure."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write('\n')


def execute(args):
    if str(ROOT/'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT/'scripts'))
    import review_scale_content_v1 as original
    import review_world_pilot_v2 as semantics
    selected = json.loads(args.selection_path.read_bytes())['selected_work_ids']
    if not selected or len(selected) != len(set(selected)):
        raise ValueError('Distinct actual review selection required')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    groups = [selected[i:i+50] for i in range(0, len(selected), 50)]
    plan = output/'review-plan.json'
    save(plan, {'version':'scale-content-disjoint-review-plan-v1', 'selected_work_ids':selected,
        'groups':groups, 'max_group_works':50, 'max_concurrent_review_cli':2,
        'reason':'Observed CLI transport maximum1048576chars rejected original1181666char request before model turn',
        'failed_original_request_preserved':True, 'unchanged_large_request_retried':False})

    def one(index, ids):
        selection = output/f'group-{index+1:02d}.selection.json'
        save(selection, {'selected_work_ids':ids, 'parent_selection_path':str(args.selection_path),
                        'parent_selection_sha256':sha(args.selection_path), 'review_plan_sha256':sha(plan)})
        child = copy(args)
        child.output = output/f'group-{index+1:02d}'
        child.selection_path = selection
        started = time.monotonic()
        result = original.load_review(ids).execute(child,
            instructions=semantics.INSTRUCTIONS+original.CONTENT_RULES,
            extra_source_paths=(Path(original.__file__),Path(semantics.__file__),Path(__file__),selection,plan,args.selection_path),
            protocol=original.PROTOCOL)
        result['elapsed_seconds']=time.monotonic()-started
        return result

    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures=[workers.submit(one,index,ids) for index,ids in enumerate(groups)]
        results=[future.result() for future in futures]
    documents=[json.loads(Path(result['review_path']).read_bytes()) for result in results]
    merged={**documents[0], 'source_audits':[], 'cases':[]}
    identities=('run_id','ordered_inputs_sha256','run_manifest_sha256','results_sha256','execution_context_manifest_sha256')
    for document in documents:
        if any(document[key]!=merged[key] for key in identities):
            raise ValueError('Disjoint genuine review parent bindings differ')
        merged['source_audits'].extend(document['source_audits'])
        merged['cases'].extend(document['cases'])
    covered=[]
    for binding in merged['source_audits']:
        audit=json.loads(Path(binding['path']).read_bytes())
        covered.extend(row['work_id'] for row in audit['reviews'])
    if len(covered)!=len(set(covered)) or set(covered)!=set(selected):
        raise ValueError('Merged genuine reviews omit or repeat actual selected works')
    review=output/'quality-adoption-review.json'
    save(review,merged)
    from recommend_book.next_database_v4 import _quality_document
    _quality_document(review.resolve())
    usage={key:sum(result['observed_usage'].get(key,0) for result in results)
           for key in set().union(*(r['observed_usage'] for r in results))}
    return {'review_path':str(review.resolve()),'reviewed_n':len(selected),'reviewed_work_count':len(selected),
        'case_n':len(merged['cases']),'held_n':sum(c['status']=='held' for c in merged['cases']),
        'uncertain_n':sum(c['status']=='uncertain' for c in merged['cases']),
        'observed_usage':usage,'elapsed_seconds':time.monotonic()-started,
        'groups':results,'actual_review_model_request_count':len(results),
        'source_plan_path':str(plan),'source_plan_sha256':sha(plan)}
