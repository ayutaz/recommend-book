import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
from recommend_book.models import Contributor,Evidence,Identifier,SourceRecord,Title,WorkInput


def script():
    spec=importlib.util.spec_from_file_location('integrate_prose_poc',Path(__file__).parents[1]/'scripts/integrate_prose_poc.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def snapshot(tmp_path, *, language='en', mismatched_sha=False, uncertain=False):
    prose=tmp_path/'prose';resolution=tmp_path/'resolution';prose.mkdir();resolution.mkdir()
    def save(name,payload):
        path=tmp_path/name
        body=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
        path.write_bytes(body)
        return str(path),hashlib.sha256(body).hexdigest()
    author_path,author_sha=save('author.json',{'parse':{'revid':1,'text':'Jane Austen (1775–1817)'}})
    records=[]
    for index in range(2):
        captures=[];evidences=[]
        for chapter in range(2):
            rev=3+index*10+chapter;text=('A family gathered in the old house. '*80).strip()
            path,sha=save(f'body-{rev}.txt',text.encode())
            raw_path,raw_sha=save(f'render-{rev}.json',{'parse':{'revid':rev,'text':'<p>'+text+'</p>'}})
            captures.append({'chapter':f'Example {index}/Chapter {chapter+1}','revision_id':rev,'render_raw_path':raw_path,'render_raw_sha256':raw_sha,'normalized_path':path,'normalized_sha256':sha})
            evidences.append(Evidence(evidence_id=f'ws:{rev}',kind='excerpt',text=text[:1600],source_url=f'https://en.wikisource.org/w/index.php?oldid={rev}',rights_policy_id='wikisource-reviewed-original',llm_input_allowed=True,language='en',locator=f'revision:{rev};normalized-sha256:{sha};chars:0-1600',license='CC-BY-SA-4.0',license_url='https://creativecommons.org/licenses/by-sa/4.0/',attribution={'credit':'Wikisource contributors','history_url':'https://example.test/history','changes':'paragraph excerpt'},inherited_restrictions=['per_work_original_rights_checked','attribution','share_alike','mark_changes']))
        records.append(SourceRecord(source='wikisource',source_id=str(3+index*10),source_url='https://en.wikisource.org/w/index.php?oldid=2',retrieved_at='2026-10-12',provider_id='wikisource',record_kind='work',titles=[Title(value=f'Example {index}',language='en')],contributors=[Contributor(name='Jane Austen',role='author')],identifiers=[Identifier(source='wikisource',value=f'ws:en:{123+index}',scope='work')],original_language='en',language='en',first_publication_year=1813,publication_year=1813,evidence=evidences,raw_path=raw_path,raw_sha256=raw_sha,rights_policy_id='wikisource-reviewed-original',extra={'edition_page_id':123+index,'chapters':captures,'author_fact_raw_path':author_path,'author_fact_raw_sha256':author_sha,'fulfilled_obligations':['per_work_original_rights_checked','attribution','share_alike','mark_changes'],'original_rights_review':{'author':'Jane Austen','author_death_year':1817,'first_publication_year':1813,'edition_year':1813,'original_language':'en','language':'en','jurisdictions':['JP','US'],'author_revision_url':'https://en.wikisource.org/w/index.php?oldid=1','author_fact_quote':'1775–1817','edition_revision_url':'https://en.wikisource.org/w/index.php?oldid=2','edition_fact_quote':'year = 1813','no_translation':True,'no_modern_annotations':True}}))
    def claim(value):return {'rank':'normal','mainsnak':{'snaktype':'value','datavalue':{'value':value}}}
    def q(qid):return {'id':qid}
    lang_q='Q7979' if language=='en-GB' else 'Q1860'
    entities={'Q100':{'id':'Q100','claims':{'P31':[claim(q('Q3331189'))],'P629':[claim(q('Q200'))],'P577':[claim({'time':'+1813-01-01T00:00:00Z'})]},'sitelinks':{'enwikisource':{'title':'Example 0'}}},'Q200':{'id':'Q200','labels':{'en':{'value':'Example 0'}},'claims':{'P50':[claim(q('Q300'))],'P407':[claim(q(lang_q))],'P577':[claim({'time':'+1813-01-01T00:00:00Z'})]}},'Q300':{'id':'Q300','claims':{'P570':[claim({'time':'+1817-07-18T00:00:00Z'})]}}}
    raw_path,raw_sha=save('wikidata.json',{'entities':entities})
    wd=SourceRecord(source='wikidata',source_id='Q200',source_url='https://www.wikidata.org/wiki/Q200',retrieved_at='2026-10-12',provider_id='wikidata',record_kind='work',titles=[Title(value='Example 0',language='en')],contributors=[Contributor(name='Jane Austen',role='author',identifiers=[Identifier(source='wikidata',value='Q300',scope='agent')])],identifiers=[Identifier(source='wikidata',value='Q200',scope='work')],original_language=language,first_publication_year=1813,raw_path=raw_path,raw_sha256=raw_sha,rights_policy_id='wikidata-cc0')
    edition=wd.model_copy(update={'source_id':'Q100','record_kind':'edition','contributors':[],'identifiers':[Identifier(source='wikidata',value='Q100',scope='edition')],'work_refs':[Identifier(source='wikidata',value='Q200',scope='work')],'original_language':None,'language':'en','first_publication_year':None,'publication_year':1813})
    proofs={qid:{'path':raw_path,'sha256':'0'*64 if mismatched_sha else raw_sha} for qid in entities}
    matched={'candidate_id':'ws:en:123','title':'Example 0','edition_url':records[0].source_url,'wikisource_title':'Example 0','author':'Jane Austen','first_publication_year':1813,'edition_year':1813,'author_wikidata_candidate':'Q300','status':'uncertain' if uncertain else 'matched','human_verified':False,'independent_identity_confirmation':False,'integration_ready':not uncertain,'text_rights_changed':False,'entity_qid':'Q100','entity_role':'edition_or_version','edition_qid':'Q100','work_qid':'Q200','P629':['Q200'],'original_author_qids':['Q300'],'original_language_qids':[lang_q],'work_publication_years':[1813],'entity_publication_years':[1813],'reasons':[] if not uncertain else ['not independently identified'],'raw_evidence':proofs}
    rows=[matched,{'candidate_id':'ws:en:124','status':'uncertain','integration_ready':False,'reasons':['missing edition identity']}]
    (prose/'source-records.jsonl').write_text(''.join(r.model_dump_json()+'\n' for r in records),encoding='utf8')
    inputs=[WorkInput(work_id=r.identifiers[0].value,title=r.titles[0].value,authors=['Jane Austen'],original_language='en',first_publication_year=1813,evidence=r.evidence,source_record_ids=['wikisource:'+r.source_id]) for r in records]
    (prose/'work-inputs.jsonl').write_text(''.join(w.model_dump_json()+'\n' for w in inputs),encoding='utf8')
    metadata={'pages':{'Author:Jane Austen':{'pageprops':{'wikibase_item':'Q300'},'revisions':[{'revid':1}]}}}
    (prose/'metadata.json').write_text(json.dumps(metadata),encoding='utf8')
    (resolution/'source-records.jsonl').write_text(wd.model_dump_json()+'\n'+edition.model_dump_json()+'\n',encoding='utf8')
    (resolution/'identity-map.json').write_text(json.dumps(rows),encoding='utf8')
    (resolution/'manifest.json').write_text(json.dumps({'metadata_sha256':hashlib.sha256((prose/'metadata.json').read_bytes()).hexdigest(),'output_sha256':{name:hashlib.sha256((resolution/name).read_bytes()).hexdigest() for name in ('source-records.jsonl','identity-map.json')}}),encoding='utf8')
    return prose,resolution


def test_verified_connection_versions_preserve_originals_and_rights(tmp_path):
    prose,resolution=snapshot(tmp_path);before=(prose/'source-records.jsonl').read_bytes()
    output=tmp_path/'out';result=script().integrate(prose,resolution,output,old_results=None)
    assert result['integrated']==1 and result['held']==1
    assert (prose/'source-records.jsonl').read_bytes()==before
    assert (output/'original-source-records.jsonl').read_bytes()==before
    audit=json.loads((output/'audit.json').read_text());assert audit['unexpected_canonical_work_count']==0
    assert audit['canonical_works']==2 and audit['editions']==1 and audit['prose_evidence_rights_fulfilled']==4
    assert audit['edition_work_links_verified']==1
    assert audit['edition_relationship_violations']==[]
    assert audit['original_prose_scope_violations']==[]
    with sqlite3.connect(output/'dataset.sqlite') as db:
        old,new=db.execute("SELECT version_id,parent_version_id FROM source_records WHERE source='wikisource' AND source_id='3' ORDER BY parent_version_id IS NULL DESC").fetchall()
        assert new[1]==old[0]
        payload=json.loads(db.execute('SELECT record_json FROM source_records WHERE version_id=?',(new[0],)).fetchone()[0])
        assert payload['work_refs']==[{'source':'wikidata','value':'Q200','scope':'work'}]
        assert payload['record_kind']=='edition' and payload['rights_policy_id']=='wikisource-reviewed-original'
        assert payload['evidence'][0]['license']=='CC-BY-SA-4.0'
        assert not payload['extra']['prose_identity_integration']['human_verified']
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('DELETE FROM source_records WHERE version_id=?',(old[0],))
    crosswalk=json.loads((output/'id-crosswalk.json').read_text())
    assert crosswalk[0]['original_source_version_id']==old[0]
    assert crosswalk[0]['canonical_work_id']!=crosswalk[0]['old_style_work_id']
    assert not crosswalk[0]['old_classification_valid_for_new_input']


@pytest.mark.parametrize('options,reason',[({'mismatched_sha':True},'SHA'),({'language':'en-GB'},'language precision'),({'uncertain':True},'not independently')])
def test_mismatch_or_uncertain_is_held_without_importing_authority(tmp_path,options,reason):
    prose,resolution=snapshot(tmp_path,**options);result=script().integrate(prose,resolution,tmp_path/'out',old_results=None)
    assert result['integrated']==0 and result['held']==2
    holds=json.loads((tmp_path/'out/held.json').read_text())
    assert reason in holds[0]['reason']
    audit=json.loads((tmp_path/'out/audit.json').read_text())
    assert audit['canonical_works']==2 and audit['editions']==0
    assert audit['unexpected_canonical_work_count']==0
    if options.get('language')=='en-GB':
        diagnostic=json.loads((tmp_path/'out/hold-diagnostics.json').read_text())[0]
        assert diagnostic['nondeprecated_raw_language_qids']==['Q7979']
        assert diagnostic['identity_map_original_language_qids']==['Q7979']
        assert not diagnostic['human_verified']


def test_new_snapshot_never_overwrites_existing_output(tmp_path):
    prose,resolution=snapshot(tmp_path);output=tmp_path/'out';output.mkdir();(output/'keep.txt').write_text('preserve')
    with pytest.raises(ValueError,match='new or empty'):
        script().integrate(prose,resolution,output,old_results=None)
    assert (output/'keep.txt').read_text()=='preserve'


def test_readonly_audit_detects_semantically_wrong_edition_target(tmp_path):
    prose,resolution=snapshot(tmp_path);output=tmp_path/'out';module=script()
    module.integrate(prose,resolution,output,old_results=None)
    with sqlite3.connect(output/'dataset.sqlite') as db:
        wrong=db.execute("SELECT w.canonical_id FROM works w JOIN source_records r ON r.version_id=w.source_version_id WHERE r.source='wikisource' AND r.source_id='13'").fetchone()[0]
        db.execute('UPDATE edition_contents SET work_id=?',(wrong,))
    before=(output/'dataset.sqlite').read_bytes()
    audit=module.audit_readonly(output/'dataset.sqlite',2,1)
    assert audit['foreign_key_violations']==0
    assert audit['edition_work_links_verified']==0
    assert audit['edition_relationship_violations'][0]['reason']=='edition target does not equal raw-proof work authority canonical ID'
    assert (output/'dataset.sqlite').read_bytes()==before
