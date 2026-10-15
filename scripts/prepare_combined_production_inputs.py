"""Append canonical prose inputs without changing any existing production rows."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from recommend_book.classification.validation import input_hash
from recommend_book.models import WorkInput
from recommend_book.normalization import normalize_text


def sha(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(path):return sha(Path(path).read_bytes())
def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write_json(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def input_rows(raw,*,terminated=False):
    lines=raw.splitlines(keepends=True)
    if not lines or any(not line.strip() for line in lines):raise ValueError('input contains empty rows')
    if terminated and any(not line.endswith(b'\n') for line in lines):raise ValueError('input rows must be newline-terminated; bytes are never rewritten')
    rows=[WorkInput.model_validate_json(line) for line in lines]
    if any(row.work_id.startswith('ws:') for row in rows):raise ValueError('legacy ws: work IDs cannot be rebound to canonical production input')
    if len({r.work_id for r in rows})!=len(rows):raise ValueError('duplicate work IDs')
    if any(r.expression_id is not None for r in rows):raise ValueError('production input must be work-level')
    return lines,rows


def verify_context(frozen,repo,ordered_manifest):
    context=read_json(frozen/'processing-context.json')
    if context!=ordered_manifest['processing_context']:raise ValueError('ordered manifest processing context differs from frozen context')
    for relative,proof in context.items():
        if file_sha(frozen/proof['snapshot'])!=proof['sha256']:raise ValueError('frozen context SHA mismatch: '+relative)
        if file_sha(repo/relative)!=proof['sha256']:raise ValueError('context drift: '+relative)
    return context


def verify_review(review_pack,ordered_manifest,main_by_id):
    if review_pack is None:return {}
    pack=read_json(review_pack);payload={k:v for k,v in pack.items() if k!='pack_sha256'}
    if sha(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())!=pack['pack_sha256']:
        raise ValueError('review pack SHA mismatch')
    if pack['pack_sha256']!=ordered_manifest['review_pack_sha256']:raise ValueError('ordered review pack binding differs')
    ids=set()
    for row in pack['works']:
        work=WorkInput.model_validate(row)
        if work.work_id in ids or work.work_id not in main_by_id or input_hash(work)!=input_hash(main_by_id[work.work_id]):
            raise ValueError('review work differs from original main snapshot')
        ids.add(work.work_id)
    split_path=review_pack.parent/'split_manifest.json'
    split=read_json(split_path);hashable={k:v for k,v in split.items() if k!='manifest_sha256'}
    if sha(json.dumps(hashable,ensure_ascii=False,indent=2).encode())!=split['manifest_sha256']:
        raise ValueError('frozen split manifest SHA mismatch')
    return {'review_work_count':len(ids),'review_pack_sha256':file_sha(review_pack),'split_manifest_sha256':file_sha(split_path),'partitions_changed':False}


def prepare(ordered_dir,combined_dir,frozen_dir,output_dir,*,repo=None,
            review_pack=Path('data/evaluation/poc-frozen-v2/400-review-balanced-v1/review_pack.json'),
            expected_main=2000,expected_prose=20):
    ordered_dir,combined_dir,frozen_dir,output_dir=map(Path,(ordered_dir,combined_dir,frozen_dir,output_dir))
    repo=Path(repo) if repo is not None else Path(__file__).resolve().parents[1]
    review_pack=Path(review_pack) if review_pack is not None else None
    if output_dir.exists():raise ValueError('production input output must be new')
    input_trees=[ordered_dir,combined_dir,frozen_dir,repo/'config',repo/'schemas',repo/'src']
    if review_pack is not None:input_trees.append(review_pack.parent)
    if any(output_dir.resolve().is_relative_to(tree.resolve()) for tree in input_trees):
        raise ValueError('output must be outside every input tree')
    ordered_manifest=read_json(ordered_dir/'manifest.json')
    combined_manifest=read_json(combined_dir/'manifest.json')
    context=verify_context(frozen_dir,repo,ordered_manifest)
    for name,checksum in combined_manifest['output_sha256'].items():
        if name.endswith(('-wal','-shm')):raise ValueError('combined durable output manifest contains volatile SQLite sidecar')
        if file_sha(combined_dir/name)!=checksum:raise ValueError('combined durable output SHA mismatch: '+name)
    main_raw=(ordered_dir/'ordered-inputs.jsonl').read_bytes()
    if sha(main_raw)!=ordered_manifest['ordered_inputs_sha256']:raise ValueError('ordered input SHA mismatch')
    main_lines,main=input_rows(main_raw,terminated=True)
    frozen_manifest=read_json(frozen_dir/'manifest.json');frozen_raw=(frozen_dir/'work-inputs.jsonl').read_bytes()
    if sha(frozen_raw)!=frozen_manifest['work_inputs_sha256'] or sha(frozen_raw)!=ordered_manifest['source_sha256']:
        raise ValueError('original frozen input SHA mismatch')
    _,frozen=input_rows(frozen_raw)
    if len(main)!=expected_main or ordered_manifest['work_count']!=expected_main or len(frozen)!=expected_main:
        raise ValueError('fixed main input count differs')
    frozen_by_id={r.work_id:r for r in frozen};main_by_id={r.work_id:r for r in main}
    if set(frozen_by_id)!=set(main_by_id):raise ValueError('ordered main IDs differ from frozen input')
    for row in main:
        if input_hash(row)!=input_hash(frozen_by_id[row.work_id]) or row.source_record_ids!=frozen_by_id[row.work_id].source_record_ids:
            raise ValueError('ordered main work hash or source-version refs differ')
    tail_raw=(combined_dir/'prose-work-inputs.jsonl').read_bytes();tail_lines,tail=input_rows(tail_raw,terminated=True)
    if len(tail)!=expected_prose or set(main_by_id)&{r.work_id for r in tail}:raise ValueError('duplicate main/prose IDs or fixed prose count differs')
    _,combined=input_rows((combined_dir/'work-inputs.jsonl').read_bytes())
    combined_by_id={r.work_id:r for r in combined}
    if combined_manifest['canonical_works']!=expected_main+expected_prose or set(combined_by_id)!=set(main_by_id)|{r.work_id for r in tail}:
        raise ValueError('combined canonical work set differs from main plus prose')
    crosswalk=read_json(combined_dir/'prose-id-crosswalk.json')
    if len(crosswalk)!=expected_prose or len({r['old_style_work_id'] for r in crosswalk})!=expected_prose or len({r['combined_canonical_work_id'] for r in crosswalk})!=expected_prose:
        raise ValueError('crosswalk contains duplicate or missing identities')
    cross_by_id={r['combined_canonical_work_id']:r for r in crosswalk}
    if set(cross_by_id)!={r.work_id for r in tail}:raise ValueError('crosswalk canonical IDs differ from prose inputs')
    old_ids={r['old_style_work_id'] for r in crosswalk}
    if old_ids & set(combined_by_id):raise ValueError('legacy ws: identity reused as canonical work')
    comparison=[]
    for position,row in enumerate([*main,*tail],1):
        canonical=combined_by_id[row.work_id]
        if input_hash(row)!=input_hash(canonical):raise ValueError('production row differs from combined input hash')
        if row.source_record_ids!=canonical.source_record_ids:raise ValueError('production source-version refs differ from combined snapshot')
        if row.work_id in cross_by_id:
            proof=cross_by_id[row.work_id]
            if proof['combined_input_hash']!=input_hash(row):raise ValueError('crosswalk input hash differs from appended work')
            if proof['combined_active_source_version_id'] not in row.source_record_ids:raise ValueError('crosswalk source-version refs differ from appended work')
            if (proof['old_classification_valid_for_new_input'] is not False or proof['human_verified'] is not False or
                proof['independent_identity_confirmation'] is not False or proof['original_source_version_preserved'] is not True):
                raise ValueError('crosswalk classification/provenance flags differ')
        comparison.append({'row':position,'work_id':row.work_id,'input_hash':input_hash(row),'source_version_refs':row.source_record_ids,'scope':'frozen_main' if position<=expected_main else 'prose_supplement','combined_input_hash_matches':True,'combined_source_refs_match':True})
    review=verify_review(review_pack,ordered_manifest,main_by_id)
    stage_size=ordered_manifest['stage_size']
    if not isinstance(stage_size,int) or isinstance(stage_size,bool) or not 0<stage_size<=expected_main:raise ValueError('original stage size invalid')

    protected={ordered_dir/'manifest.json',ordered_dir/'ordered-inputs.jsonl',frozen_dir/'manifest.json',frozen_dir/'work-inputs.jsonl',frozen_dir/'processing-context.json',combined_dir/'manifest.json'}
    protected.update(combined_dir/name for name in combined_manifest['output_sha256'])
    for relative,proof in context.items():protected.update((frozen_dir/proof['snapshot'],repo/relative))
    for relative in ('src/recommend_book/models.py','src/recommend_book/jobs.py'):
        if (repo/relative).exists():protected.add(repo/relative)
    if review_pack is not None:protected.update(p for p in review_pack.parent.iterdir() if p.is_file())
    before={str(p.resolve()):file_sha(p) for p in sorted(protected)}
    original_context_bytes=(frozen_dir/'processing-context.json').read_bytes()
    main_authors={normalize_text(author) for row in main for author in row.authors}
    tail_authors={normalize_text(author) for row in tail for author in row.authors}
    scope={'supplement_work_ids':[w.work_id for w in tail],'main_review_partitions_changed':False,'main_review_and_splits':review,'independent_holdout':False,'observed_display_author_overlap':sorted(main_authors&tail_authors),'author_overlap_method':'normalized display names only; authority aliases or unknown connections are not excluded','human_quality_verified':False,'human_annotations_created':0,'definition':'Additional prose quality evaluation scope; no main partition membership assigned or changed. Author overlap prevents an independent holdout claim.'}
    output_raw=main_raw+tail_raw
    output_dir.mkdir(parents=True,exist_ok=False)
    (output_dir/'ordered-inputs.jsonl').write_bytes(output_raw)
    (output_dir/'processing-context.json').write_bytes(original_context_bytes)
    shutil.copyfile(combined_dir/'prose-id-crosswalk.json',output_dir/'prose-id-crosswalk.source.json')
    write_json(output_dir/'input-comparison.json',comparison)
    write_json(output_dir/'supplement-evaluation-scope.json',scope)
    write_json(output_dir/'protected-inputs-sha.json',before)
    actual=(output_dir/'ordered-inputs.jsonl').read_bytes()
    if actual[:len(main_raw)]!=main_raw or actual[len(main_raw):]!=tail_raw:raise ValueError('output prefix/tail bytes differ')
    if any(file_sha(path)!=checksum for path,checksum in before.items()):raise ValueError('protected production/context/combined/review input changed')
    report={'version':'poc-combined-v1-production-inputs-v1','ordered_inputs_sha256':sha(actual),'work_count':len(main)+len(tail),'main_rows_preserved':len(main),'appended_canonical_prose_rows':len(tail),'main_prefix_bytes':len(main_raw),'original_main_ordered_sha256':sha(main_raw),'appended_prose_bytes_sha256':sha(tail_raw),'original_ordered_manifest_sha256':before[str((ordered_dir/'manifest.json').resolve())],'original_context_sha256':sha(original_context_bytes),'combined_manifest_sha256':before[str((combined_dir/'manifest.json').resolve())],'stage_size':stage_size,'stage_one_prefix_sha256':sha(b''.join(main_lines[:stage_size])),'processing_context':context,'frozen_context_snapshot_directory':str(frozen_dir.resolve()),'processing_context_use':'Legacy input provenance only; stage-two execution version is separate and not authorized by this preparation.','execution_context_created':False,'execution_context_auto_updated':False,'work_ids_hashes_source_refs_verified':len(comparison),'crosswalk_canonical_prose_ids_verified':len(crosswalk),'main_review_and_splits':review,'supplement_independent_holdout':False,'protected_files_unchanged':len(before),'classification_rebound':False,'llm_started':False,'web_requests':0,'human_annotations_created':0,'definition':'Byte-identical ordered main prefix plus byte-identical canonical prose tail. No stage-two run started; main review partitions remain frozen.','output_sha256':{p.name:file_sha(p) for p in output_dir.iterdir() if p.is_file()}}
    write_json(output_dir/'manifest.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ordered',type=Path,default=Path('data/production-inputs/poc-frozen-v2-v1'))
    parser.add_argument('--combined',type=Path,default=Path('data/poc-combined-v1'))
    parser.add_argument('--frozen',type=Path,default=Path('data/poc-frozen-v2'))
    parser.add_argument('--output',type=Path,default=Path('data/production-inputs/poc-combined-v1-v1'))
    args=parser.parse_args();report=prepare(args.ordered,args.combined,args.frozen,args.output)
    print(json.dumps({k:v for k,v in report.items() if k not in ('output_sha256','processing_context')},ensure_ascii=True))
