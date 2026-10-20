"""Disjoint <=50-work genuine reviews after an observed transport-size failure."""
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import hashlib
import json
from pathlib import Path
from types import ModuleType
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MAX_PROMPT_CHARS = 1048576


def bounded_groups(selected,measure):
    groups=[]
    def add(ids):
        if measure(ids)<=MAX_PROMPT_CHARS:
            groups.append(ids);return
        if len(ids)==1:
            raise ValueError('One actual review work exceeds measured transport limit; no request sent')
        middle=len(ids)//2
        add(ids[:middle]);add(ids[middle:])
    for offset in range(0,len(selected),50):add(selected[offset:offset+50])
    return groups


def preview_prompt_chars(args,ids):
    import review_scale_content_v1 as original
    import review_world_pilot_v2 as semantics
    from recommend_book.models import WorkInput
    works={work.work_id:work for work in [WorkInput.model_validate_json(line)
        for line in args.ordered_inputs.read_bytes().splitlines() if line.strip()]}
    manifest=json.loads((args.run_dir/'manifest.json').read_bytes())
    selected=set(ids);records=[]
    for state in manifest['jobs'].values():
        if state['status']=='completed' and state['work_id'] in selected:
            native=json.loads((args.run_dir/state['result_path']).read_bytes())
            records.append((works[native['work_id']],native))
    if {work.work_id for work,_ in records}!=selected or len(records)!=len(selected):
        raise ValueError('Exact actual completed review identities required before size measurement')
    taxonomy=json.loads((args.runtime_root/'config/taxonomy.json').read_bytes())
    payload={'taxonomy':taxonomy,'works':[{'work':work.model_dump(mode='json')} for work,_ in records]}
    prompt=(semantics.INSTRUCTIONS+original.CONTENT_RULES+'\nBEGIN_LOCAL_DATA_JSON\n'+
        json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+
        '\nEND_LOCAL_DATA_JSON\nBEGIN_AUTHENTIC_NATIVE_CLASSIFICATIONS_JSON\n'+
        json.dumps([native for _,native in records],ensure_ascii=False,sort_keys=True,separators=(',',':'))+
        '\nEND_AUTHENTIC_NATIVE_CLASSIFICATIONS_JSON\n')
    return len(prompt)


def load_bounded_review(ids,expected_chars):
    import review_scale_content_v1 as original
    module=original.load_review(ids)
    path=ROOT/'scripts/review_world_pilot_v1.py'
    source=path.read_text(encoding='utf-8').replace('\r\n','\n')
    replacements={
        "            if state['status'] != 'completed':\n":
            "            if state['status'] != 'completed' or state['work_id'] not in SELECTED_WORK_IDS:\n",
        '    if len(records) != len(works) or not records:\n':
            "    if {r['work'].work_id for r in records} != SELECTED_WORK_IDS or not records:\n",
        "    audit = {'version': review_version, 'protocol': protocol, 'run_id': manifest['run_id'],\n":
            "    audit = {'version': review_version, 'protocol': protocol, 'run_id': manifest['run_id'],\n        'selected_work_ids': sorted(SELECTED_WORK_IDS),\n",
        "    (args.output/'review.stdin.utf8').write_bytes(prompt)":
            "    if len(prompt.decode('utf-8')) != EXPECTED_PROMPT_CHARS or len(prompt.decode('utf-8')) > MAX_PROMPT_CHARS:\n        raise ValueError('Actual review prompt size differs/exceeds measured transport bound; model not started')\n    (args.output/'review.stdin.utf8').write_bytes(prompt)",
    }
    for before,after in replacements.items():
        if source.count(before)!=1:raise ValueError('Immutable bounded review anchor drift')
        source=source.replace(before,after)
    module.EXPECTED_PROMPT_CHARS=expected_chars
    module.MAX_PROMPT_CHARS=MAX_PROMPT_CHARS
    exec(compile(source,str(path),'exec'),module.__dict__)
    return module


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
    groups = bounded_groups(selected, lambda ids: preview_prompt_chars(args, ids))
    sizes = [preview_prompt_chars(args, ids) for ids in groups]
    plan = output/'review-plan.json'
    save(plan, {'version':'scale-content-disjoint-review-plan-v1', 'selected_work_ids':selected,
        'groups':groups, 'actual_planned_prompt_chars':sizes, 'max_prompt_chars':MAX_PROMPT_CHARS, 'max_group_works':50, 'max_concurrent_review_cli':2,
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
        result = load_bounded_review(ids, sizes[index]).execute(child,
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
