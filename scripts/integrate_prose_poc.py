"""Offline, independent prose identity snapshot; never rebinds classifications.

Only validated work authority records are activated. Edition API records are
retained as support, while the versioned Wikisource record supplies the sampled
edition and text. This avoids authorless API edition nodes becoming extra works.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

from recommend_book.classification.validation import input_hash,validate_result
from recommend_book.database import DatasetDB
from recommend_book.ingestion.core import write_jsonl
from recommend_book.ingestion.wikisource import fulfilled_for_record
from recommend_book.models import Identifier,SourceRecord,WorkInput
from recommend_book.normalization import normalize_text
from recommend_book.resolution import compare_records
from recommend_book.rights import RightsRegistry


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_records(path): return [SourceRecord.model_validate_json(l) for l in Path(path).read_text(encoding='utf-8').splitlines() if l.strip()]
def write_json(path,value): Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def values(entity,property_id):
    return [r.get('mainsnak',{}).get('datavalue',{}).get('value') for r in entity.get('claims',{}).get(property_id,[]) if r.get('rank')!='deprecated' and r.get('mainsnak',{}).get('snaktype')=='value']
def qids(entity,property_id): return sorted({v['id'] for v in values(entity,property_id) if isinstance(v,dict) and 'id' in v})
def years(entity,property_id):
    return sorted({int(re.match(r'\+([0-9]+)-',v['time'])[1]) for v in values(entity,property_id) if isinstance(v,dict) and re.match(r'\+([0-9]+)-',v.get('time',''))})


class Proofs:
    def __init__(self): self.cache={}
    def payload(self,path,sha):
        path=str(Path(path).resolve())
        if path not in self.cache:
            blob=Path(path).read_bytes();self.cache[path]=(hashlib.sha256(blob).hexdigest(),json.loads(blob))
        actual,payload=self.cache[path]
        if actual!=sha: raise ValueError('raw proof SHA mismatch')
        return payload
    def entity(self,qid,row):
        proof=row['raw_evidence'][qid];entity=self.payload(proof['path'],proof['sha256']).get('entities',{}).get(qid)
        if not entity or entity.get('id')!=qid: raise ValueError('raw proof does not contain exact entity ID')
        return entity


def diagnose_holds(held,rows,proofs):
    diagnostics=[];mapping={r['candidate_id']:r for r in rows}
    for hold in held:
        row=mapping[hold['candidate_id']]
        if row.get('status')!='matched':continue
        item={**hold,'work_qid':row.get('work_qid'),'identity_map_original_language_qids':row.get('original_language_qids'),'raw_evidence':row.get('raw_evidence',{}),'normalization_applied':False}
        try:
            work=proofs.entity(row['work_qid'],row)
            claims=[{'statement_id':s.get('id'),'rank':s.get('rank'),'qid':s.get('mainsnak',{}).get('datavalue',{}).get('value',{}).get('id'),'qualifiers':s.get('qualifiers',{})} for s in work.get('claims',{}).get('P407',[]) if s.get('mainsnak',{}).get('snaktype')=='value']
            item.update(nondeprecated_raw_language_qids=qids(work,'P407'),preferred_raw_language_qids=sorted({c['qid'] for c in claims if c['rank']=='preferred'}),language_statements=claims,raw_work_publication_years=years(work,'P577'),raw_edition_publication_years=years(proofs.entity(row['edition_qid'],row),'P577'))
            if item['nondeprecated_raw_language_qids']!=item['identity_map_original_language_qids']:
                item['interpretation']='All nondeprecated raw P407 values differ from the identity-map original-language subset. This is a rank/scope-policy hold, not proof that the sampled English text is a translation. Assertions remain immutable.'
        except (ValueError,KeyError,OSError,TypeError) as exc:item['diagnostic_error']=str(exc)
        diagnostics.append(item)
    return diagnostics


def check_original_text(record):
    fulfilled=fulfilled_for_record(record)
    if not fulfilled: raise ValueError('original per-work rights conditions not fulfilled')
    if digest(record.raw_path)!=record.raw_sha256: raise ValueError('original Wikisource raw SHA mismatch')
    for evidence,capture in zip(record.evidence,record.extra['chapters']):
        if digest(capture['render_raw_path'])!=capture['render_raw_sha256']: raise ValueError('chapter render SHA mismatch')
        if digest(capture['normalized_path'])!=capture['normalized_sha256']: raise ValueError('chapter normalized SHA mismatch')
        text=Path(capture['normalized_path']).read_text(encoding='utf-8')
        match=re.search(r'chars:(\d+)-(\d+)',evidence.locator or '')
        if not match or evidence.text!=text[int(match[1]):int(match[2])]: raise ValueError('excerpt character locator mismatch')
    author=record.extra['author_fact_raw_path']
    if digest(author)!=record.extra['author_fact_raw_sha256']: raise ValueError('author render SHA mismatch')
    return fulfilled


def enrich_verified(record,row,authority,metadata,proofs,parent_version_id):
    if row.get('status')!='matched' or not row.get('integration_ready'):
        raise ValueError('; '.join(row.get('reasons',[])) or 'identity is uncertain')
    if row.get('human_verified') or row.get('text_rights_changed') or row.get('reasons'):
        raise ValueError('unreviewed identity-map provenance flags or reasons')
    if row.get('entity_role')!='edition_or_version' or row.get('P629')!=[row.get('work_qid')]:
        raise ValueError('requires one edition_or_version P629 work target')
    candidate=next(i.value for i in record.identifiers if i.source=='wikisource' and i.scope=='work')
    if (candidate!=row['candidate_id'] or record.source_url!=row['edition_url'] or
        normalize_text(record.titles[0].value)!=normalize_text(row['title']) or
        record.first_publication_year!=row['first_publication_year'] or record.publication_year!=row['edition_year']):
        raise ValueError('original title, dates, candidate or edition locator differs from identity snapshot')
    edition_id=row['edition_qid'];work_id=row['work_qid'];author_id=row['author_wikidata_candidate']
    edition=proofs.entity(edition_id,row);work=proofs.entity(work_id,row);author=proofs.entity(author_id,row)
    for qid,proof in row['raw_evidence'].items(): proofs.payload(proof['path'],proof['sha256'])
    if (row['entity_qid']!=edition_id or qids(edition,'P629')!=[work_id] or
        'Q3331189' not in qids(edition,'P31') or edition.get('sitelinks',{}).get('enwikisource',{}).get('title')!=row['wikisource_title']):
        raise ValueError('raw edition type, P629 or exact Wikisource sitelink mismatch')
    if authority.record_kind!='work' or authority.source_id!=work_id or ('wikidata',work_id,'work') not in {(i.source,i.value,i.scope) for i in authority.identifiers}:
        raise ValueError('missing exact work authority SourceRecord')
    proof=row['raw_evidence'][work_id]
    if Path(authority.raw_path).resolve()!=Path(proof['path']).resolve() or authority.raw_sha256!=proof['sha256']:
        raise ValueError('authority SourceRecord raw proof SHA/path mismatch')
    if qids(work,'P50')!=row['original_author_qids'] or qids(work,'P50')!=[author_id]:
        raise ValueError('raw complete original author attribution mismatch')
    author_name=record.extra['original_rights_review']['author']
    author_page=metadata['pages']['Author:'+author_name]
    if author_page.get('pageprops',{}).get('wikibase_item')!=author_id:
        raise ValueError('saved Author page entity does not match original P50')
    if str(author_page['revisions'][0]['revid'])!=record.extra['original_rights_review']['author_revision_url'].split('=')[-1]:
        raise ValueError('Author page fixed revision differs from rights source')
    if years(author,'P570')!=[record.extra['original_rights_review']['author_death_year']]:
        raise ValueError('nondeprecated author death-year conflict')
    if (qids(work,'P407')!=row['original_language_qids'] or
        years(work,'P577')!=row['work_publication_years'] or years(edition,'P577')!=row['entity_publication_years'] or
        record.first_publication_year not in years(work,'P577') or record.publication_year not in years(edition,'P577')):
        raise ValueError('raw language or publication-year assertions mismatch')
    if authority.original_language!=record.original_language:
        raise ValueError(f'language precision hold: Wikisource {record.original_language} versus Wikidata {authority.original_language}; no normalization applied')
    new=record.model_copy(deep=True)
    new.record_kind='edition'
    new.identifiers.append(Identifier(source='wikidata',value=edition_id,scope='edition'))
    new.work_refs=[Identifier(source='wikidata',value=work_id,scope='work')]
    for contributor in new.contributors:
        if contributor.role=='author': contributor.identifiers.append(Identifier(source='wikidata',value=author_id,scope='agent'))
    new.extra['edition_statement']='Sampled historic English original; Wikidata edition_or_version broad type, specific publication edition type not independently verified'
    new.extra['expression_kind']='original'
    new.extra['prose_identity_integration']={'candidate_id':candidate,'parent_source_version_id':parent_version_id,'work_qid':work_id,'edition_or_version_qid':edition_id,'author_page_qid':author_id,'author_revision_url':record.extra['original_rights_review']['author_revision_url'],'P629':row['P629'],'raw_evidence':row['raw_evidence'],'human_verified':False,'independent_identity_confirmation':False,'text_rights_changed':False,'body_scope':'Two sampled original-English excerpts from the recorded historic edition/version, not all versions or whole-work prose style.','metadata_scope':'Dependent Wikisource/WD source mapping; contributor text retains conditional CC BY-SA and per-work JP/US rights review.'}
    consistency=compare_records(new,authority)
    if consistency.status!='same': raise ValueError('metadata consistency hold: '+consistency.reason)
    return new


def audit_readonly(database,expected,integrated):
    connection=sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        count=lambda query:connection.execute(query).fetchone()[0]
        works=count('SELECT count(DISTINCT canonical_id) FROM works WHERE resolved=1')
        active=connection.execute('SELECT r.version_id,r.record_json FROM source_heads h JOIN source_records r ON r.version_id=h.version_id').fetchall()
        fulfilled=0;original=0;integration_rows=[];verified_links=0;link_violations=[];scope_violations=[]
        for row in active:
            record=SourceRecord.model_validate_json(row['record_json'])
            if record.source!='wikisource':continue
            obligations=check_original_text(record)
            for evidence in record.evidence:
                assert RightsRegistry.default().permits(evidence.rights_policy_id,'excerpt','external_llm',obligations)
                fulfilled+=1
            original+=1
            for scoped in connection.execute('SELECT expression_id,evidence_json FROM evidence WHERE source_version_id=?',(row['version_id'],)):
                evidence=json.loads(scoped['evidence_json'])
                if scoped['expression_id'] is not None or evidence.get('expression_ref') is not None or evidence.get('language')!='en':
                    scope_violations.append({'source_version_id':row['version_id'],'evidence_id':evidence['evidence_id']})
            if 'prose_identity_integration' in record.extra:
                integration=record.extra['prose_identity_integration'];integration_rows.append(integration)
                target=connection.execute("SELECT w.canonical_id FROM works w JOIN source_records r ON r.version_id=w.source_version_id JOIN source_heads h ON h.version_id=r.version_id WHERE r.source='wikidata' AND r.source_id=?",(integration['work_qid'],)).fetchone()
                links=connection.execute('SELECT c.work_id,x.expression_kind,x.language FROM editions e JOIN edition_contents c ON c.edition_id=e.edition_id LEFT JOIN expressions x ON x.expression_id=c.expression_id WHERE e.source_version_id=?',(row['version_id'],)).fetchall()
                reason=None
                if not target or len(links)!=1 or links[0]['work_id']!=target['canonical_id']:
                    reason='edition target does not equal raw-proof work authority canonical ID'
                elif links[0]['expression_kind']!='original' or links[0]['language']!='en':
                    reason='sampled edition expression is not original English'
                elif not connection.execute("SELECT 1 FROM external_ids i JOIN editions e ON e.edition_id=i.entity_id WHERE e.source_version_id=? AND i.source='wikidata' AND i.scope='edition' AND i.value=?",(row['version_id'],integration['edition_or_version_qid'])).fetchone():
                    reason='scoped edition identifier missing from bibliographic edition row'
                if reason:link_violations.append({'source_version_id':row['version_id'],'reason':reason})
                else:verified_links+=1
        return {'audit_connection':'SQLite URI mode=ro; PRAGMA query_only=ON','integrity_check':connection.execute('PRAGMA integrity_check').fetchone()[0],'foreign_key_violations':len(connection.execute('PRAGMA foreign_key_check').fetchall()),'canonical_works':works,'expected_original_candidates':expected,'unexpected_canonical_work_count':abs(works-expected),'work_nodes':count('SELECT count(*) FROM works'),'source_versions':count('SELECT count(*) FROM source_records'),'active_source_records':count('SELECT count(*) FROM source_heads'),'editions':count('SELECT count(*) FROM editions'),'edition_contents':count('SELECT count(*) FROM edition_contents'),'expressions':count('SELECT count(*) FROM expressions'),'active_prose_records':original,'prose_evidence_rights_fulfilled':fulfilled,'invalid_evidence_scopes':count('SELECT count(*) FROM evidence WHERE scope_valid=0'),'human_resolution_decisions':count("SELECT count(*) FROM resolution_decisions WHERE method='human'"),'edition_work_links_verified':verified_links,'edition_relationship_violations':link_violations,'original_prose_scope_violations':scope_violations,'integrated_relations':len(integration_rows),'expected_integrated_relations':integrated,'independent_identity_confirmations':sum(bool(r['independent_identity_confirmation']) for r in integration_rows),'human_verified_identities':sum(bool(r['human_verified']) for r in integration_rows),'body_license_changed':any(r['text_rights_changed'] for r in integration_rows)}
    finally: connection.close()


def integrate(prose_dir,resolution_dir,output_dir,*,old_results=Path('data/runs/prose-style-v1/results.jsonl')):
    prose_dir,resolution_dir,output_dir=map(Path,(prose_dir,resolution_dir,output_dir))
    if output_dir.exists() and any(output_dir.iterdir()): raise ValueError('output directory must be new or empty')
    original_path=prose_dir/'source-records.jsonl';inputs_path=prose_dir/'work-inputs.jsonl';metadata_path=prose_dir/'metadata.json'
    protected=[original_path,inputs_path,metadata_path,resolution_dir/'identity-map.json',resolution_dir/'source-records.jsonl',resolution_dir/'manifest.json']
    if old_results is not None:protected.append(Path(old_results))
    before={str(path.resolve()):digest(path) for path in protected}
    resolution_manifest=json.loads((resolution_dir/'manifest.json').read_text(encoding='utf-8'))
    for name,sha in resolution_manifest['output_sha256'].items():
        if digest(resolution_dir/name)!=sha: raise ValueError('identity snapshot output SHA mismatch: '+name)
    if digest(metadata_path)!=resolution_manifest['metadata_sha256']: raise ValueError('prose metadata SHA mismatch')
    originals=read_records(original_path);authority_records=read_records(resolution_dir/'source-records.jsonl');authorities={r.source_id:r for r in authority_records}
    rows=json.loads((resolution_dir/'identity-map.json').read_text(encoding='utf-8'));metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
    inputs=[WorkInput.model_validate_json(l) for l in inputs_path.read_text(encoding='utf-8').splitlines() if l.strip()]
    old_inputs={w.work_id:w for w in inputs};mapping={r['candidate_id']:r for r in rows}
    if len(originals)!=len(mapping) or len(inputs)!=len(mapping): raise ValueError('unique candidate/input/source-record counts differ')
    if old_results is not None:
        old_classifications=[json.loads(l) for l in Path(old_results).read_text(encoding='utf-8').splitlines() if l.strip()]
        if {r['work_id'] for r in old_classifications}!=set(old_inputs): raise ValueError('old classification identities do not match old snapshot')
        for result in old_classifications:validate_result(result,old_inputs[result['work_id']])
    for record in originals:check_original_text(record)
    output_dir.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(original_path,output_dir/'original-source-records.jsonl')
    shutil.copyfile(resolution_dir/'source-records.jsonl',output_dir/'wikidata-support-source-records.jsonl')
    shutil.copyfile(resolution_dir/'identity-map.json',output_dir/'identity-map.source.json')
    proofs=Proofs();enriched=[];selected_authorities={};held=[];crosswalk=[]
    with DatasetDB(output_dir/'dataset.sqlite') as db:
        db.ingest(originals)
        old_versions={(r['source'],r['source_id']):r['version_id'] for r in db.connection.execute('SELECT * FROM source_heads')}
        for record in originals:
            candidate=next(i.value for i in record.identifiers if i.source=='wikisource' and i.scope=='work')
            if candidate not in old_inputs or old_inputs[candidate].evidence!=record.evidence: raise ValueError('old input is not derived from exact source evidence')
            row=mapping[candidate]
            try:
                authority=authorities.get(row.get('work_qid'))
                if row.get('status')!='matched': raise ValueError('; '.join(row.get('reasons',[])) or 'uncertain identity')
                if authority is None:raise ValueError('work authority SourceRecord absent')
                new=enrich_verified(record,row,authority,metadata,proofs,old_versions[record.source,record.source_id])
                enriched.append(new);selected_authorities[authority.source_id]=authority
            except (ValueError,KeyError,OSError,TypeError) as exc:
                held.append({'candidate_id':candidate,'title':old_inputs[candidate].title,'original_identity_map_status':row.get('status'),'reason':str(exc),'human_verified':False,'independent_identity_confirmation':False})
        db.ingest(selected_authorities.values());db.ingest(enriched)
        scope_inputs=db.work_inputs();by_id={w.work_id:w for w in scope_inputs if w.expression_id is None}
        for record in originals:
            candidate=next(i.value for i in record.identifiers if i.source=='wikisource' and i.scope=='work')
            head=db.connection.execute('SELECT h.version_id,w.work_id,w.canonical_id FROM source_heads h JOIN works w ON w.source_version_id=h.version_id WHERE h.source=? AND h.source_id=?',(record.source,record.source_id)).fetchone()
            scope_input=by_id[head['canonical_id']]
            integrated=next((r for r in enriched if r.source_id==record.source_id),None)
            crosswalk.append({'old_style_work_id':candidate,'original_source':record.source,'original_source_id':record.source_id,'original_source_version_id':old_versions[record.source,record.source_id],'active_source_version_id':head['version_id'],'canonical_work_id':head['canonical_id'],'verified_wikidata_work_qid':integrated.extra['prose_identity_integration']['work_qid'] if integrated else None,'verified_edition_or_version_qid':integrated.extra['prose_identity_integration']['edition_or_version_qid'] if integrated else None,'identity_status':'integrated' if integrated else 'held','old_style_input_hash':input_hash(old_inputs[candidate]),'new_scope_input_hash':input_hash(scope_input),'old_classification_valid_for_new_input':False,'old_classification_snapshot':str(Path(old_results).resolve()) if old_results is not None else None,'human_verified':False,'independent_identity_confirmation':False})
        # Add an explicit dependent-proof event to the existing decision ledger;
        # no human merge or new database schema is used.
        for decision in db.connection.execute("SELECT * FROM resolution_decisions WHERE active=1 AND status='same'").fetchall():
            db.connection.execute("INSERT INTO resolution_events(decision_id,event_type,details_json) VALUES(?,'prose_identity_proof',?)",(decision['decision_id'],json.dumps({'method':'offline_raw_P629_and_Wikisource_source_mapping','human_verified':False,'independent_identity_confirmation':False,'identity_map_sha256':before[str((resolution_dir/'identity-map.json').resolve())]})))
        db.connection.commit()
    write_jsonl(enriched,output_dir/'enriched-prose-source-records.jsonl');write_jsonl(selected_authorities.values(),output_dir/'activated-work-authorities.jsonl')
    (output_dir/'scope-inputs.jsonl').write_text(''.join(w.model_dump_json()+'\n' for w in scope_inputs),encoding='utf-8')
    write_json(output_dir/'id-crosswalk.json',crosswalk);write_json(output_dir/'held.json',held)
    write_json(output_dir/'hold-diagnostics.json',diagnose_holds(held,rows,proofs))
    audit=audit_readonly(output_dir/'dataset.sqlite',len(originals),len(enriched));write_json(output_dir/'audit.json',audit)
    if (audit['unexpected_canonical_work_count'] or audit['foreign_key_violations'] or audit['integrity_check']!='ok' or audit['edition_relationship_violations'] or audit['original_prose_scope_violations'] or audit['edition_work_links_verified']!=len(enriched)): raise ValueError('independent DB audit failed')
    if any(digest(path)!=sha for path,sha in before.items()): raise ValueError('protected source snapshot modified')
    report={'original_candidates':len(originals),'identity_map_matched':sum(r['status']=='matched' for r in rows),'integrated':len(enriched),'held':len(held),'database':str((output_dir/'dataset.sqlite').resolve()),'protected_inputs_sha256':before,'originals_unchanged':True,'classification_rebound':False,'llm_started':False,'web_requests':0,'metadata_authorities_activated':len(selected_authorities),'edition_authority_records_not_activated':'Kept as raw proof/support only; sampled Wikisource edition rows carry scoped IDs and relationships without extra unresolved work nodes.','audit':audit,'output_sha256':{path.name:digest(path) for path in output_dir.iterdir() if path.is_file()}}
    write_json(output_dir/'manifest.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prose',type=Path,default=Path('data/prose-poc'));parser.add_argument('--resolution',type=Path,default=Path('data/prose-poc/wikidata-resolution-v1'));parser.add_argument('--output',type=Path,default=Path('data/prose-poc/database-integrated-v1'));parser.add_argument('--old-results',type=Path,default=Path('data/runs/prose-style-v1/results.jsonl'))
    args=parser.parse_args();result=integrate(args.prose,args.resolution,args.output,old_results=args.old_results)
    print(json.dumps({k:v for k,v in result.items() if k not in ('protected_inputs_sha256','output_sha256')},ensure_ascii=True))
