"""Bibliographic authority metadata audit; AI proposals never authorize merges."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET
import re
from datetime import datetime,timezone
from recommend_book.autonomous_resolution import canonical,digest
from recommend_book.autonomous_resolution_audit import strict_json
from recommend_book.normalization import normalize_isbn

PROPERTIES=('P31','P7937','P629','P747','P179','P361','P527','P50','P655','P123','P577','P407','P212','P957')
D='http://purl.org/dc/terms/';N='http://ndl.go.jp/dcndl/terms/';R='http://www.w3.org/1999/02/22-rdf-syntax-ns#';F='http://xmlns.com/foaf/0.1/';C='http://purl.org/dc/elements/1.1/'


def wd_metadata(raw,qid):
    entity=raw.get('entities',{}).get(qid)
    if not isinstance(entity,dict) or entity.get('id')!=qid:raise ValueError('Exact Wikidata entity absent')
    properties={}
    for prop in PROPERTIES:
        properties[prop]=[{'index':i,'value':s['mainsnak']['datavalue']['value'],'statement':s,'locator':'entities.'+qid+'.claims.'+prop+'['+str(i)+']'} for i,s in enumerate(entity.get('claims',{}).get(prop,[])) if s.get('rank')!='deprecated' and s.get('mainsnak',{}).get('snaktype')=='value' and 'datavalue' in s['mainsnak']]
    types={x['value'].get('id') for x in properties['P31'] if isinstance(x['value'],dict)}
    return {'qid':qid,'scope':'edition_or_translation' if 'Q3331189' in types else ('work' if 'Q7725634' in types else 'unverified_work_scope'),'relations':properties,'label_metadata':entity.get('labels',{})}


def exact_wikipedia_page(raw,record):
    extra=record.get('extra',{});page_id=extra.get('page_id');revision_id=extra.get('revision_id')
    if type(page_id) is not int or type(revision_id) is not int or record['source_id'].split(':')[1]!=str(revision_id):raise ValueError('Page/revision identity absent or differs from source id')
    pages=raw.get('query',{}).get('pages',[]);items=pages.items() if isinstance(pages,dict) else enumerate(pages)
    matches=[(i,p) for i,p in items if p.get('pageid')==page_id and any(x.get('revid')==revision_id for x in p.get('revisions',[]))]
    if len(matches)!=1:raise ValueError('Exact page/revision boundary absent or ambiguous')
    index,page=matches[0];qid=page.get('pageprops',{}).get('wikibase_item')
    if not isinstance(qid,str) or not re.fullmatch('Q[1-9][0-9]*',qid):raise ValueError('Exact page lacks Wikidata binding')
    return {'qid':qid,'page_id':page_id,'revision_id':revision_id,'assertion_locator':'query.pages['+canonical(index)+'].pageprops.wikibase_item','assertion_json':{'pageid':page_id,'revision_id':revision_id,'wikibase_item':qid}}


def ndl_bibliography(raw,source_id):
    root=ET.fromstring(raw);needle=source_id.split('-I')[-1]
    resources=[e for e in root.iter() if e.tag=='{'+N+'}BibResource' and any(ch.tag=='{'+D+'}identifier' and (ch.text or '').strip()==needle and ch.attrib.get('{'+R+'}datatype')==N+'NDLBibID' for ch in e)]
    if len(resources)!=1:raise ValueError('Exact primary NDL BibResource absent or ambiguous')
    resource=resources[0];primary=[];parts=[];ids=[];creators=[];explicit=[];facts=[]
    def text(e):return ' '.join(t.strip() for t in e.itertext() if t.strip())
    for i,ch in enumerate(resource):
        local=ch.tag.split('}')[-1];locator='BibResource['+needle+']/'+ch.tag+'['+str(i)+']'
        if ch.tag in ('{'+D+'}title','{'+C+'}title'):
            value=text(ch)
            if value and value not in primary:primary.append(value)
        elif ch.tag=='{'+N+'}partInformation':
            for e in ch.iter():
                if e.tag in ('{'+D+'}title','{'+C+'}title') and text(e):parts.append(text(e))
        elif ch.tag=='{'+D+'}identifier':ids.append({'value':text(ch),'datatype':ch.attrib.get('{'+R+'}datatype'),'locator':locator})
        elif ch.tag=='{'+D+'}creator':
            for agent in ch:
                creators.append({'names':[text(e) for e in agent.iter() if e.tag=='{'+F+'}name'],'authority':agent.attrib.get('{'+R+'}about'),'role':'unassigned','locator':locator})
        elif ch.tag=='{'+C+'}creator':
            literal=text(ch);markers=[role for marker,role in [('著','author'),('訳','translator'),('編','editor'),('絵','illustrator')] if marker in literal]
            explicit.append({'literal':literal,'role_markers':markers,'locator':locator,'authority_assignment':'not inferred from generic creator'})
        elif local in ('language','originalLanguage','issued','volume','edition','publisher','extent'):
            facts.append({'tag':ch.tag,'value':text(ch),'locator':locator})
    return {'primary_titles':primary,'part_titles':parts,'identifiers':ids,'generic_creators':creators,'explicit_roles':explicit,'bibliographic_facts':facts,'original_work_authority_assigned':False}


def pair_binding(wd,wp,wd_raw,wp_raw,left,right):
    reason=None
    if wd['source']!='wikidata' or wp['source']!='wikipedia':reason='No explicit cross-provider authority identity'
    elif wd_raw['qid']!=wp_raw['qid']:reason='Exact raw Wikidata IDs differ; AI same is unaccepted'
    elif not left or not right or not left.get('resolved') or not right.get('resolved'):reason='Current canonical work mapping missing or unresolved'
    elif left['canonical_id']!=right['canonical_id']:reason='Explicit IDs match but current canonical bindings differ; no new merge authorized'
    binding=reason is None
    scope_reason=None if wd_raw['scope']=='work' else 'Source original-work scope unverified or edition/translation conflict; no scope inference from page binding'
    return {'status':'held' if reason else 'verified_existing_binding','reason':reason or 'Exact page/revision Wikidata binding and existing canonical mapping verified; binding-only','entity_binding_verified':binding,'work_scope_status':'held' if scope_reason else 'explicit_literary_work_class','work_scope_reason':scope_reason,'original_work_scope_status':'held' if scope_reason else 'explicit_literary_work_class','original_work_identity_verified':False,'authority_merge_approved':False,'human_verified':False}


def relationship_claims(metadata):
    """Preserve typed source assertions without assigning equivalence or target scope."""
    types={'P629':'edition_or_translation_of','P747':'has_edition_or_translation','P179':'member_of_series','P361':'part_of','P527':'has_part'}
    return [{'relation_type':kind,'property':prop,'assertion_locator':item['locator'],'assertion_json':item['statement'],'target_authority_id':item['value'].get('id') if isinstance(item['value'],dict) else None,'status':'held','reason':'Typed source relationship preserved; target original-work scope and existing binding not independently verified; not same-work equivalence','authority_merge_approved':False,'human_verified':False,'original_work_identity_verified':False} for prop,kind in types.items() for item in metadata['relations'][prop]]


def lineage(db,records,version):
    original=records[version];head=db.execute('select version_id from source_heads where source=? and source_id=?',(original['source'],original['source_id'])).fetchone()
    if not head:return None
    current=head[0];chain=[];v=current
    while v:
        if v in chain or v not in records:raise ValueError('Invalid source lineage')
        row=records[v]
        if (row['source'],row['source_id'])!=(original['source'],original['source_id']):raise ValueError('Source lineage crosses identity')
        chain.append(v)
        if v==version:break
        v=db.execute('select parent_version_id from source_records where version_id=?',(v,)).fetchone()[0]
    if version not in chain:return None
    nodes=db.execute('select work_id,canonical_id,resolved from works where source_version_id=?',(current,)).fetchall()
    if len(nodes)!=1:return None
    return {'work_id':nodes[0][0],'canonical_id':nodes[0][1],'resolved':nodes[0][2],'current_source_version_id':current,'source_lineage':chain}


def audit_next_resolution(database,scope_path,aggregate_path,inputs_path,output):
    database=Path(database).resolve();scope_path=Path(scope_path).resolve();aggregate_path=Path(aggregate_path).resolve();inputs_path=Path(inputs_path).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('New absent namespace required')
    started_at=datetime.now(timezone.utc).isoformat()
    root=Path(__file__).resolve().parents[2]
    protected={str(p):digest(p.read_bytes()) for p in [database,scope_path,aggregate_path,inputs_path,Path(__file__).resolve(),root/'src/recommend_book/autonomous_resolution.py',root/'src/recommend_book/autonomous_resolution_audit.py',root/'src/recommend_book/normalization.py',root/'scripts/audit_next_resolution.py',root/'tests/test_next_resolution.py']}
    if any(output==Path(p).parent or output.is_relative_to(Path(p).parent) for p in protected):raise ValueError('Output enters original source/code tree')
    db=sqlite3.connect('file:'+database.as_posix()+'?mode=ro&immutable=1',uri=True);db.execute('pragma query_only=on')
    records={v:strict_json(r) for v,r in db.execute('select version_id,record_json from source_records')};cases={c['case_id']:c for c in map(strict_json,inputs_path.read_bytes().splitlines())};aggregate=strict_json(aggregate_path.read_bytes());scope=strict_json(scope_path.read_bytes());raw_cache={}
    def raw_record(record):
        path=Path(record['raw_path']).resolve();expected=record['raw_sha256']
        if str(path) not in protected:
            value=path.read_bytes()
            if digest(value)!=expected:raise ValueError('Original raw SHA differs')
            protected[str(path)]=expected;raw_cache[str(path)]=value
        if str(path) not in raw_cache:raw_cache[str(path)]=path.read_bytes()
        return raw_cache[str(path)]
    def common(record,version,node):
        return {'subject_work_id':node['canonical_id'] if node else None,'subject_source_version_id':version,'source':record['source'],'source_id':record['source_id'],'raw_path':record['raw_path'],'raw_sha256':record['raw_sha256'],'source_url':record['source_url'],'rights_policy_id':record['rights_policy_id'],'target_work_id':None,'target_source_version_id':None,'authority_merge_approved':False,'human_verified':False,'operation_scope':'Saved bibliographic identity metadata only; no article prose, new acquisition, external LLM, display or redistribution permission claimed'}
    relations=[];held=[];same_results=[];unresolved_results=[];scope_results=[]
    for original in [c for c in aggregate['cases'] if c['audited_decision']=='same']:
        case=cases[original['case_id']];sides=[(s['version_id'],records[s['version_id']]) for s in case['sides']];wd=[x for x in sides if x[1]['source']=='wikidata'];wp=[x for x in sides if x[1]['source']=='wikipedia'];proofs=[]
        bibliographies=[]
        for version,r in wd:
            metadata=wd_metadata(strict_json(raw_record(r)),r['source_id']);node=lineage(db,records,version)
            typed=[common(r,version,node)|item|{'case_id':case['case_id']} for item in relationship_claims(metadata)]
            relations.extend(typed)
            bibliographies.append({'source_version_id':version,'source_id':r['source_id'],'raw_path':r['raw_path'],'raw_sha256':r['raw_sha256'],'scope':metadata['scope'],'original_work_identity_verified':False,'claims':metadata['relations'],'typed_relationships':typed})
        if len(wd)==1 and len(wp)==1:
            av,ar=wd[0];bv,br=wp[0];a=wd_metadata(strict_json(raw_record(ar)),ar['source_id']);b=exact_wikipedia_page(strict_json(raw_record(br)),br);an=lineage(db,records,av);bn=lineage(db,records,bv);decision=pair_binding(ar,br,a,b,an,bn)
            proof=common(br,bv,bn)|decision|{'target_work_id':an['canonical_id'] if an else None,'target_source_version_id':av,'relation_type':'page_revision_describes_wikidata_entity','assertion_locator':b['assertion_locator'],'assertion_json':b['assertion_json'],'subject_lineage':bn,'target_lineage':an,'target_raw_path':ar['raw_path'],'target_raw_sha256':ar['raw_sha256'],'target_assertion_locator':'entities.'+ar['source_id']+'.id','target_assertion_json':ar['source_id'],'case_id':case['case_id']};relations.append(proof);proofs.append(proof)
        else:
            for version,r in sides:raw_record(r)
            decision={'status':'held','reason':'Distinct Wikidata QIDs have no supplied explicit equivalence; title/author/AI same cannot authorize merge','entity_binding_verified':False,'original_work_identity_verified':False,'work_scope_status':'held','work_scope_reason':'No original-work identity verified across distinct authorities','authority_merge_approved':False,'human_verified':False}
        result={'case_id':case['case_id'],'ai_proposal':'same','authority_assigned_to_ai':False,**decision,'side_versions':[v for v,r in sides],'proofs':proofs,'bibliographic_metadata':bibliographies};same_results.append(result)
        if decision['status']=='held':held.append({'case_id':case['case_id'],'category':'same_candidate','reason':decision['reason']})
    for entry in scope['quarantines']:
        version=entry['target_source_version_id'];r=records[version];value=wd_metadata(strict_json(raw_record(r)),r['source_id']);node=lineage(db,records,version)
        if value['scope']!='edition_or_translation':raise ValueError('Original quarantine class claim not reproduced')
        proof=common(r,version,node)|{'relation_type':'source_declares_edition_translation_scope','assertion_locator':'entities.'+r['source_id']+'.claims.P31','assertion_json':[x['statement'] for x in value['relations']['P31']],'status':'held','reason':'source_work_scope_conflict_hold; canonical_is_edition not inferred','work_form_assertions':value['relations']['P7937'],'explicit_original_work_refs':value['relations']['P629'],'other_relationship_assertions':{k:value['relations'][k] for k in ['P747','P179','P361','P527']}}
        relations.append(proof);ndl_parts=[]
        for edition in entry.get('ndl_edition_evidence',[]):
            rr=edition['record'];bib=ndl_bibliography(raw_record(rr),rr['source_id']);ndl_parts.append({'source_id':rr['source_id'],'raw_path':rr['raw_path'],'raw_sha256':rr['raw_sha256'],'bibliography':bib,'identity_assessment':edition.get('identity_assessment'),'status':'held','reason':'Edition ISBN/author evidence does not bind a missing original work; part titles do not identify story entities'})
        scope_results.append({'work_id':entry['work_id'],'source_id':r['source_id'],'status':'held','reason':proof['reason'],'assertion':proof,'additional_ndl_edition_metadata':ndl_parts});held.append({'work_id':entry['work_id'],'category':'scope_conflict','reason':proof['reason']})
    unresolved=db.execute('select work_id,source_version_id from works where resolved=0 order by work_id').fetchall()
    for wid,version in unresolved:
        r=records[version];bib=ndl_bibliography(raw_record(r),r['source_id']);node=lineage(db,records,version)
        proof=common(r,version,node)|{'relation_type':'edition_bibliography_without_original_work_authority','assertion_locator':'BibResource['+r['source_id'].split('-I')[-1]+']','assertion_json':bib,'status':'held','reason':'Missing explicit original-work authority identity; generic creator roles remain unassigned; volume/translation/part-title metadata is not a work merge'};relations.append(proof)
        isbns={normalize_isbn(i['value']) for i in r.get('identifiers',[]) if i.get('source')=='isbn'}-{None}
        other_versions=sorted(v for v,other in records.items() if other['source']!='ndl' and any(i.get('source')=='isbn' and normalize_isbn(i['value']) in isbns for i in other.get('identifiers',[])))
        result={'work_id':wid,'source_version_id':version,'status':'held','reason':proof['reason'],'bibliography':bib,'proof':proof,'edition_isbn_cross_provider_saved_source_record_versions':other_versions,'isbn_check_scope':'All saved SourceRecord identifier metadata; no external completeness claim and no original-work inference from edition ISBN'};unresolved_results.append(result);held.append({'work_id':wid,'category':'unresolved','reason':proof['reason']})
    db.close()
    if len(same_results)!=77 or len(scope_results)!=15 or len(unresolved_results)!=26:raise ValueError('All target counts required, no silent sampling')
    if any(digest(Path(p).read_bytes())!=sha for p,sha in protected.items()):raise ValueError('Protected source changed during audit')
    additional_scope=[{'case_id':r['case_id'],'category':'additional_original_work_scope_unverified','reason':r['work_scope_reason'],'status':'held','entity_binding_status':r['status']} for r in same_results if r['status']=='verified_existing_binding' and r['work_scope_status']=='held']
    report={'version':'next-resolution-v3-bibliographic-authority-audit-v2','actor':'mechanical_metadata_audit','started_at':started_at,'completed_at':datetime.now(timezone.utc).isoformat(),'same_candidates':same_results,'scope_holds':scope_results,'additional_original_work_scope_holds':additional_scope,'unresolved_holds':unresolved_results,'authority_metadata_relations':relations,'held_records':held,'source_sha256':protected,'all_source_snapshot_sha256':digest(canonical(protected).encode()),'source_snapshot_scope':'Source/code files observed before audit and verified unchanged before output; not historical prelaunch evidence for earlier AI runs','same_candidate_status_counts':dict(Counter(r['status'] for r in same_results)),'scope_hold_count':len(scope_results),'additional_scope_hold_count':len(additional_scope),'unresolved_hold_count':len(unresolved_results),'new_merges':0,'ai_same_promoted_to_authority':0,'human_gold':0,'external_acquisitions':0,'llm_calls':0}
    output.mkdir(parents=True)
    for name,value in [('audit.json',report),('authority-metadata-relations.json',relations),('held-records.json',held),('accepted-merge-decisions.json',[])]:
        with (output/name).open('xb') as file:file.write((canonical(value)+'\n').encode())
    manifest={'version':'next-resolution-v3-artifact-manifest-v2','source_sha256':protected,'all_source_snapshot_sha256':report['all_source_snapshot_sha256'],'source_snapshot_scope':report['source_snapshot_scope'],'output_sha256':{p.name:digest(p.read_bytes()) for p in output.iterdir() if p.is_file()},'source_database_sha256':protected[str(database)],'same_candidate_status_counts':report['same_candidate_status_counts'],'scope_holds':15,'additional_scope_holds':len(additional_scope),'unresolved_holds':26,'new_merges':0,'human_gold':0,'llm_calls':0}
    with (output/'manifest.json').open('xb') as file:file.write((canonical(manifest)+'\n').encode())
    return manifest
