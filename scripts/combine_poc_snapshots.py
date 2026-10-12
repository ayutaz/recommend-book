"""Offline metadata-only combination; legacy classifications remain legacy."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3

from recommend_book.classification.validation import input_hash
from recommend_book.database import DatasetDB
from recommend_book.models import WorkInput


_spec=importlib.util.spec_from_file_location('_prose_integration_helpers',Path(__file__).with_name('integrate_prose_poc.py'))
prose_helpers=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prose_helpers)
digest=prose_helpers.digest
write_json=prose_helpers.write_json
read_records=prose_helpers.read_records


def read_inputs(path):
    return [WorkInput.model_validate_json(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def write_inputs(path,rows):
    Path(path).write_text(''.join(row.model_dump_json()+'\n' for row in rows),encoding='utf-8')


def readonly(path):
    """Immutable reads avoid writing a protected snapshot's WAL/shm sidecars."""
    path=Path(path)
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('immutable snapshot has nonempty WAL; checkpoint at its owner first')
    connection=sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    return connection


def source_signatures(connection):
    return {row['version_id']:hashlib.sha256(json.dumps(tuple(row),ensure_ascii=False,separators=(',',':')).encode()).hexdigest() for row in connection.execute('SELECT * FROM source_records ORDER BY version_id')}


def source_heads(connection):
    return {(row['source'],row['source_id']):row['version_id'] for row in connection.execute('SELECT * FROM source_heads')}


def editions(connection,*,source=None):
    query='SELECT e.* FROM editions e JOIN source_records r ON r.version_id=e.source_version_id'
    rows=connection.execute(query+(' WHERE r.source=?' if source else ''),(source,) if source else ())
    return {row['edition_id']:{'edition':dict(row),'contents':[dict(c) for c in connection.execute('SELECT * FROM edition_contents WHERE edition_id=? ORDER BY work_id,position',(row['edition_id'],))]} for row in rows}


def preservation_violations(before,after):
    return [key for key,value in before.items() if after.get(key)!=value]


def compare_main_inputs(frozen,derived):
    by_id={row.work_id:row for row in derived if row.expression_id is None}
    comparisons=[]
    for row in frozen:
        new=by_id.get(row.work_id)
        comparisons.append({'work_id':row.work_id,'original_input_hash':input_hash(row),'combined_input_hash':input_hash(new) if new else None,'input_hash_unchanged':new is not None and input_hash(row)==input_hash(new),'original_source_version_refs':row.source_record_ids,'combined_source_version_refs':new.source_record_ids if new else None,'source_versions_unchanged':new is not None and row.source_record_ids==new.source_record_ids})
    return comparisons


def combine(frozen_dir,prose_dir,integrated_dir,output_dir,*,expected_main=2000,expected_prose=20,
            old_results=Path('data/runs/prose-style-v1/results.jsonl'),extra_protected=None,progress=None):
    frozen_dir,prose_dir,integrated_dir,output_dir=map(Path,(frozen_dir,prose_dir,integrated_dir,output_dir))
    if output_dir.exists():raise ValueError('combined output must be new')
    for source in (frozen_dir,prose_dir,integrated_dir):
        if output_dir.resolve().is_relative_to(source.resolve()):raise ValueError('combined output must be outside protected input trees')
    if extra_protected is None:
        extra_protected=[p for p in (Path('data/poc-v1/dataset.sqlite'),Path('data/poc-v1/work-inputs.jsonl')) if p.exists()]
    protected=set(Path(p).resolve() for p in extra_protected)
    for root in (frozen_dir,prose_dir,integrated_dir):
        protected.update(p.resolve() for p in root.rglob('*') if p.is_file())
    if old_results is not None:protected.add(Path(old_results).resolve())
    before={str(path):digest(path) for path in sorted(protected)}
    emit=lambda stage,**kwargs:progress({'stage':stage,**kwargs}) if progress else None
    emit('protected',files=len(before))

    frozen_manifest=json.loads((frozen_dir/'manifest.json').read_text(encoding='utf-8'))
    if digest(frozen_dir/'work-inputs.jsonl')!=frozen_manifest['work_inputs_sha256']:raise ValueError('frozen input SHA mismatch')
    snapshot_path=frozen_dir/'database-snapshot.json'
    if snapshot_path.exists() and digest(frozen_dir/'dataset.sqlite')!=json.loads(snapshot_path.read_text(encoding='utf-8'))['sha256']:
        raise ValueError('frozen database SHA mismatch')
    imported_manifest=json.loads((integrated_dir/'manifest.json').read_text(encoding='utf-8'))
    for name,sha in imported_manifest['output_sha256'].items():
        if digest(integrated_dir/name)!=sha:raise ValueError('import output SHA mismatch: '+name)
    if digest(prose_dir/'source-records.jsonl')!=digest(integrated_dir/'original-source-records.jsonl'):
        raise ValueError('original prose import differs from immutable originals')

    frozen_inputs=read_inputs(frozen_dir/'work-inputs.jsonl')
    original_inputs=read_inputs(prose_dir/'work-inputs.jsonl')
    if (len(frozen_inputs)!=expected_main or frozen_manifest['work_count']!=expected_main or
        len({w.work_id for w in frozen_inputs})!=expected_main or any(w.expression_id for w in frozen_inputs)):
        raise ValueError('fixed main input count or work-level identities differ')
    originals=read_records(integrated_dir/'original-source-records.jsonl')
    authorities=read_records(integrated_dir/'activated-work-authorities.jsonl')
    enriched=read_records(integrated_dir/'enriched-prose-source-records.jsonl')
    if len(originals)!=expected_prose or len(original_inputs)!=expected_prose or len(enriched)!=imported_manifest['integrated']:
        raise ValueError('fixed prose import counts differ')
    old_by_id={w.work_id:w for w in original_inputs}
    originals_by_source={(r.source,r.source_id):r for r in originals}
    for record in originals:
        prose_helpers.check_original_text(record)
        candidate=next(i.value for i in record.identifiers if i.source=='wikisource' and i.scope=='work')
        if candidate not in old_by_id or old_by_id[candidate].evidence!=record.evidence:raise ValueError('legacy prose input is not exact source evidence')
    for record in enriched:
        original=originals_by_source.get((record.source,record.source_id))
        if original is None or original.evidence!=record.evidence or original.rights_policy_id!=record.rights_policy_id:
            raise ValueError('enriched import changes original body evidence or rights')
        integration=record.extra['prose_identity_integration']
        if integration['human_verified'] or integration['independent_identity_confirmation'] or integration['text_rights_changed']:
            raise ValueError('import identity or rights provenance flags differ')
    if old_results is not None:
        legacy=[json.loads(line) for line in Path(old_results).read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(legacy)!=expected_prose or {r['work_id'] for r in legacy}!=set(old_by_id):raise ValueError('legacy classification IDs differ')
        for result in legacy:prose_helpers.validate_result(result,old_by_id[result['work_id']])

    imported_connection=readonly(integrated_dir/'dataset.sqlite')
    try:imported_versions=source_signatures(imported_connection)
    finally:imported_connection.close()
    original_crosswalk=json.loads((integrated_dir/'id-crosswalk.json').read_text(encoding='utf-8'))
    standalone_inputs={w.work_id:w for w in read_inputs(integrated_dir/'scope-inputs.jsonl') if w.expression_id is None}
    source_connection=readonly(frozen_dir/'dataset.sqlite')
    try:
        for authority in authorities:
            if source_connection.execute("SELECT 1 FROM external_ids WHERE source='wikidata' AND scope='work' AND value=?",(authority.source_id,)).fetchone():
                raise ValueError('prose authority work identifier already present in frozen DB: '+authority.source_id)
        existing_pairs=set(source_heads(source_connection))
        if existing_pairs & {(r.source,r.source_id) for r in [*originals,*authorities]}:
            raise ValueError('prose source identity already present in frozen DB')
        output_dir.mkdir(parents=True)
        destination=sqlite3.connect(output_dir/'dataset.sqlite')
        try:source_connection.backup(destination)
        finally:destination.close()
    finally:source_connection.close()
    emit('sqlite_backup_completed')

    with DatasetDB(output_dir/'dataset.sqlite') as db:
        baseline_versions=source_signatures(db.connection);baseline_heads=source_heads(db.connection)
        baseline_editions=editions(db.connection);baseline_ndl=editions(db.connection,source='ndl')
        baseline_stats=db.stats()
        baseline_inputs=db.work_inputs()
        before_comparisons=compare_main_inputs(frozen_inputs,baseline_inputs)
        if any(not r['input_hash_unchanged'] or not r['source_versions_unchanged'] for r in before_comparisons):
            raise ValueError('frozen input differs from read-only backup reconstruction')
        if baseline_stats['works']!=expected_main:raise ValueError('fixed main DB work count differs')
        imports=[]
        for name,records in [('original_prose',originals),('activated_work_authorities',authorities),('enriched_prose',enriched)]:
            emit('ingest_started',import_group=name,records=len(records))
            stats=db.ingest(records);imports.append({'group':name,'records':len(records),'inserted':stats['inserted'],'updated':stats['updated'],'unchanged':stats['unchanged']})
            emit('ingest_completed',import_group=name,canonical_works=stats['works'])
        stats=db.stats()
        if stats['works']!=expected_main+expected_prose:raise ValueError('unexpected canonical work count; no count adjustment permitted')
        inputs=db.work_inputs();work_level=[w for w in inputs if w.expression_id is None]
        comparisons=compare_main_inputs(frozen_inputs,work_level)
        if any(not r['input_hash_unchanged'] or not r['source_versions_unchanged'] for r in comparisons):
            raise ValueError('main input hash or immutable source-version refs changed')
        all_versions=source_signatures(db.connection);all_heads=source_heads(db.connection)
        if preservation_violations(baseline_versions,all_versions) or preservation_violations(baseline_heads,all_heads):
            raise ValueError('original main source version/head changed')
        if preservation_violations(imported_versions,all_versions):raise ValueError('immutable prose import source-version provenance changed')
        if preservation_violations(baseline_editions,editions(db.connection)):raise ValueError('original edition relationship changed')
        new_by_id={w.work_id:w for w in work_level};crosswalk=[];prose_inputs=[]
        for previous in original_crosswalk:
            source=previous['original_source'];source_id=previous['original_source_id']
            head=db.connection.execute('SELECT h.version_id,w.canonical_id FROM source_heads h JOIN works w ON w.source_version_id=h.version_id WHERE h.source=? AND h.source_id=?',(source,source_id)).fetchone()
            current=new_by_id[head['canonical_id']]
            original=originals_by_source[source,source_id]
            current_evidence={e.evidence_id:e for e in current.evidence}
            for evidence in original.evidence:
                if current_evidence.get(evidence.evidence_id)!=evidence:raise ValueError('combined prose evidence, rights or locator differs')
            if current.expression_id is not None or current.original_language!='en':raise ValueError('combined prose input scope differs')
            if head['version_id'] not in current.source_record_ids:raise ValueError('active prose source version not bound to new work input')
            standalone=standalone_inputs[previous['canonical_work_id']]
            crosswalk.append({**previous,'standalone_v2_canonical_work_id':previous['canonical_work_id'],'combined_canonical_work_id':head['canonical_id'],'combined_active_source_version_id':head['version_id'],'original_source_version_preserved':previous['original_source_version_id'] in all_versions,'standalone_v2_input_hash':input_hash(standalone),'combined_input_hash':input_hash(current),'standalone_v2_input_hash_unchanged':input_hash(standalone)==input_hash(current),'old_classification_valid_for_new_input':False})
            prose_inputs.append(current)
        if len({w.work_id for w in prose_inputs})!=expected_prose or {w.work_id for w in prose_inputs}&{w.work_id for w in frozen_inputs}:
            raise ValueError('prose canonical IDs unexpectedly overlap or duplicate')
        if set(new_by_id)!={w.work_id for w in [*frozen_inputs,*prose_inputs]}:raise ValueError('combined work input set differs from fixed main plus prose')
        db.connection.commit()

    audit=prose_helpers.audit_readonly(output_dir/'dataset.sqlite',expected_main+expected_prose,len(enriched))
    audit.update(main_source_versions_preserved=len(baseline_versions),imported_prose_source_versions_preserved=len(imported_versions),main_edition_relationships_preserved=len(baseline_editions),ndl_editions_preserved=len(baseline_ndl),main_inputs_unchanged=len(comparisons),main_input_or_source_ref_changes=sum(not r['input_hash_unchanged'] or not r['source_versions_unchanged'] for r in comparisons),expected_total_editions=len(baseline_editions)+len(enriched))
    if (audit['foreign_key_violations'] or audit['integrity_check']!='ok' or audit['unexpected_canonical_work_count'] or
        audit['edition_relationship_violations'] or audit['original_prose_scope_violations'] or
        audit['edition_work_links_verified']!=len(enriched) or audit['editions']!=audit['expected_total_editions']):
        raise ValueError('combined read-only audit failed')
    write_inputs(output_dir/'work-inputs.jsonl',work_level)
    write_inputs(output_dir/'expression-inputs.jsonl',[w for w in inputs if w.expression_id is not None])
    write_inputs(output_dir/'prose-work-inputs.jsonl',prose_inputs)
    shutil.copyfile(frozen_dir/'work-inputs.jsonl',output_dir/'legacy-main-inputs.jsonl')
    shutil.copyfile(prose_dir/'work-inputs.jsonl',output_dir/'legacy-prose-style-inputs.jsonl')
    if old_results is not None:shutil.copyfile(old_results,output_dir/'legacy-prose-style-results.jsonl')
    write_json(output_dir/'main-input-comparison.json',comparisons)
    write_json(output_dir/'prose-id-crosswalk.json',crosswalk)
    write_json(output_dir/'audit.json',audit)
    write_json(output_dir/'import-provenance.json',{'copy_method':'SQLite backup from immutable query-only frozen source','frozen_database_sha256':before[str((frozen_dir/'dataset.sqlite').resolve())],'standalone_prose_database_sha256':before[str((integrated_dir/'dataset.sqlite').resolve())],'imports_in_order':imports,'main_immutable_source_versions':sorted(baseline_versions),'prose_immutable_source_versions':sorted(imported_versions),'human_verified':False,'independent_identity_confirmation':False,'classification_rebound':False})
    if any(digest(path)!=sha for path,sha in before.items()):raise ValueError('protected main/frozen/prose/legacy snapshot changed')
    write_json(output_dir/'protected-inputs-sha.json',before)
    report={'database':str((output_dir/'dataset.sqlite').resolve()),'canonical_works':stats['works'],'expected_canonical_works':expected_main+expected_prose,'main_inputs_unchanged':len(comparisons),'main_source_versions_unchanged':True,'prose_source_versions_preserved':len(imported_versions),'ndl_editions_preserved':len(baseline_ndl),'prose_candidates':expected_prose,'prose_integrated':len(enriched),'prose_held':expected_prose-len(enriched),'protected_files_unchanged':len(before),'standalone_prose_input_hashes_unchanged':sum(r['standalone_v2_input_hash_unchanged'] for r in crosswalk),'classification_rebound':False,'human_quality_verified':False,'llm_started':False,'web_requests':0,'legacy_classification_note':'Legacy files retain their original IDs/input hashes. Ongoing main classifications stay under their frozen input snapshot; no classification rows are imported or IDs rewritten.','stats':stats,'audit':audit,'output_sha256':{p.name:digest(p) for p in output_dir.iterdir() if p.is_file() and not p.name.endswith(('-shm','-wal'))}}
    write_json(output_dir/'manifest.json',report)
    emit('completed',canonical_works=stats['works'])
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--frozen',type=Path,default=Path('data/poc-frozen-v2'))
    parser.add_argument('--prose',type=Path,default=Path('data/prose-poc'))
    parser.add_argument('--integrated',type=Path,default=Path('data/prose-poc/database-integrated-v2'))
    parser.add_argument('--output',type=Path,default=Path('data/poc-combined-v1'))
    args=parser.parse_args()
    result=combine(args.frozen,args.prose,args.integrated,args.output,progress=lambda event:print(json.dumps(event),flush=True))
    print(json.dumps({k:v for k,v in result.items() if k not in ('output_sha256','stats','audit')},ensure_ascii=True))
