"""Resume bounded multilingual novel metadata collection, never retry held requests.

Uses structured Wikidata data only. Checkpoints keep query/raw provenance;
normalization replays saved payloads, never individual work HTTP requests.
"""
from __future__ import annotations
import argparse,hashlib,json,re,sqlite3,time
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from recommend_book.ingestion.core import HTTPFetcher,RawStore,read_jsonl,write_jsonl
from recommend_book.ingestion.wikidata import WikidataAdapter,claim_values,QUERY_URL,ENTITY_URL

def save(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.tmp');t.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');t.replace(p)
def read(p):return json.loads(Path(p).read_bytes())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()

def rate_limit_active(fetcher,error,started_at):
    if not re.search(r'\b429\b',str(error).replace('HTTP429','429')):return False
    gate=getattr(fetcher,'gate_path',None)
    if gate is None:return True
    with sqlite3.connect(gate) as db:
        row=db.execute('SELECT not_before FROM cooldowns WHERE host=?',('query.wikidata.org',)).fetchone()
    # An explicit server Retry-After deadline is the release condition. Without
    # one, never probe for release. The failed query itself stays held.
    started=datetime.fromisoformat(started_at).timestamp()
    return row is None or row[0]<started or time.time()<row[0]

def discovery_query(languages,*,limit,offset):
    if not 1<=limit<=500 or offset<0 or any(not re.fullmatch(r'Q[1-9][0-9]*',q) for q in languages):raise ValueError('invalid discovery frame')
    language=('VALUES ?language { '+' '.join('wd:'+q for q in languages)+' } ?work wdt:P407 ?language. ') if languages else ''
    return ('SELECT DISTINCT ?work WHERE { VALUES ?type { wd:Q8261 wd:Q747381 } '
        '{ ?work wdt:P7937 ?type. } UNION { ?work wdt:P31 ?type. } '
        +language+'?work wdt:P136 ?genre. '
        'FILTER NOT EXISTS { ?work wdt:P629 ?editionWork. } '
        'FILTER NOT EXISTS { ?work wdt:P31 wd:Q3331189. } } ORDER BY ?work LIMIT '+str(limit)+' OFFSET '+str(offset))

def discover(output,fetcher,*,groups,target):
    output=Path(output);output.mkdir(parents=True,exist_ok=True);p=output/'selection.json'
    context={'groups':groups,'target':target,'forms':['Q8261','Q747381'],'requires_P136':True,'page_size':500}
    state=read(p) if p.exists() else {'frozen_at':now(),'frame':context,'pages':{},'ids':[]}
    if json.dumps(state['frame'],sort_keys=True)!=json.dumps(context,sort_keys=True):raise ValueError('Discovery frame changed')
    save(p,state);seen=set(state['ids'])
    for name,languages,count in groups:
        for offset in range(0,count,500):
            key=f'{name}:{offset}'
            if key in state['pages']:
                page=state['pages'][key]
                if page['status']!='success':
                    if rate_limit_active(fetcher,page.get('error',''),page['started_at']):return state['ids']
                    continue
                if page['count']<min(500,count-offset):break
                continue
            if len(seen)>=target:return state['ids']
            limit=min(500,count-offset)
            query=discovery_query(languages,limit=limit,offset=offset)
            row={'query':query,'started_at':now(),'status':'started','count':0}
            state['pages'][key]=row;save(p,state)
            r=fetcher.get(QUERY_URL,source='wikidata',params={'query':query,'format':'json'},headers={'Accept':'application/sparql-results+json'})
            if r.status!='success':
                row.update(status='held',error=r.error);save(p,state);print(json.dumps({'page':key,'held':r.error}),flush=True)
                if getattr(r,'status_code',None)==429 or re.search(r'\b429\b',str(r.error).replace('HTTP429','429')):return state['ids']
                continue
            payload=json.loads(r.content);ids=[x['work']['value'].rsplit('/',1)[1] for x in payload['results']['bindings']]
            if any(not re.fullmatch(r'Q[1-9][0-9]*',q) for q in ids):raise ValueError('Invalid discovery identity')
            for q in ids:
                if q not in seen:seen.add(q);state['ids'].append(q)
            row.update(status='success',count=len(ids),raw_path=r.artifact.path,raw_sha256=r.artifact.sha256,completed_at=now())
            save(p,state);print(json.dumps({'page':key,'unique':len(seen)}),flush=True)
            if len(ids)<limit:break
    return state['ids']

def collect(output,target,phase='all'):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True);started=time.monotonic()
    raw_name='raw-query' if phase=='discover' else 'raw-entities'
    fetcher=HTTPFetcher(RawStore(output/raw_name),user_agent='recommend-book/0.1 (https://github.com/ayutaz/recommend-book) novel catalogue',min_interval=1.1,max_attempts=1)
    groups=[('en',['Q1860'],3000),('ja',['Q5287'],2000),('fr',['Q150'],1500),('de',['Q188'],1000),('es',['Q1321'],1000),('ru',['Q7737'],750),('zh',['Q7850'],750),('it',['Q652'],500),('pt',['Q5146'],500),('ko',['Q9176'],500),('other',['Q9610','Q1568','Q13955'],500),('global',[],target)]
    ids=read(output/'selection.json')['ids'] if phase=='enrich' else discover(output,fetcher,groups=groups,target=target)
    if phase=='discover':return {'discovered':len(ids)}
    state_path=output/'entity-state.json';state=read(state_path) if state_path.exists() else {'completed':[],'held':[],'records':[]}
    shard_root=output/'record-shards';shard_root.mkdir(exist_ok=True)
    records=[r for p in sorted(shard_root.glob('*.jsonl')) for r in read_jsonl(p)]
    if not records and (output/'works.jsonl').exists():records=list(read_jsonl(output/'works.jsonl'))
    done=set(state['completed'])|{q for r in records for q in [r.source_id]}|set(state['held'])
    adapter=WikidataAdapter(fetcher);languages=('en','ja','fr','de','es','ru','zh','ko','it','pt','ar','hi','bn')
    pending=[q for q in ids if q not in done]
    for start in range(0,len(pending),50):
        batch=pending[start:start+50]
        # Mark before HTTP. Interrupted or held requests remain held on restart.
        state['held'].extend(batch);save(state_path,state)
        result=adapter.fetch_entities(batch,languages=languages)
        successful={r.source_id for r in result.records};records.extend(result.records)
        state['completed'].extend(sorted(successful));state['held']=[q for q in state['held'] if q not in successful]
        if result.errors:state.setdefault('errors',[]).extend(result.errors)
        write_jsonl(result.records,shard_root/(batch[0]+'.jsonl'));save(state_path,state)
        if start%1000==0 or start+50>=len(pending):print(json.dumps({'entity_records':len(records),'held':len(state['held'])}),flush=True)
        if any('429' in str(x) for x in result.errors):break
    references=set()
    for record in records:
        for prop in ('P50','P655','P98','P110','P136','P495','P179','P921','P674','P840'):
            references.update(v['id'] for v in claim_values({'claims':record.extra['claims']},prop) if isinstance(v,dict) and 'id' in v)
    linked_path=output/'linked-entities.json';linked=read(linked_path) if linked_path.exists() else {}
    # Only labels are consumed here. Keep the original HTTP bytes immutable,
    # but avoid rewriting unrelated linked-entity claims in every checkpoint.
    for item in linked.values():
        entity=item['entity']
        item['entity']={'id':entity.get('id'),'labels':entity.get('labels',{})}
    linked_state_path=output/'linked-state.json';linked_state=read(linked_state_path) if linked_state_path.exists() else {'held':[]}
    missing=sorted(references-set(linked)-set(linked_state['held']))
    for start in range(0,len(missing),50):
        batch=missing[start:start+50];linked_state['held'].extend(batch);save(linked_state_path,linked_state)
        r=fetcher.get(ENTITY_URL,source='wikidata',params={'action':'wbgetentities','ids':'|'.join(batch),'props':'labels','languages':'|'.join(languages),'format':'json','maxlag':5})
        if r.status!='success':
            save(output/'linked-failure.json',{'at':now(),'error':r.error,'new_requests_on_resume':False})
            if r.status_code==429:break
            continue
        payload=json.loads(r.content)
        if 'error' in payload:continue
        for q,e in payload['entities'].items():
            linked[q]={'entity':{'id':e.get('id'),'labels':e.get('labels',{})},'raw_path':r.artifact.path,'raw_sha256':r.artifact.sha256}
        linked_state['held']=[q for q in linked_state['held'] if q not in linked]
        save(linked_path,linked);save(linked_state_path,linked_state)
        if start%1000==0:print(json.dumps({'linked_entities':len(linked)}),flush=True)
    labels={};proofs={}
    for q,item in linked.items():
        ls=item['entity'].get('labels',{});language=next((l for l in languages if l in ls),next(iter(ls),None))
        if language is None:continue
        labels[q]=ls[language]['value'];proofs[q]={'label':labels[q],'raw_path':item['raw_path'],'raw_sha256':item['raw_sha256'],'locator':f'entities.{q}.labels.{language}'}
    grouped={}
    for record in records:grouped.setdefault((record.raw_path,record.raw_sha256,record.retrieved_at),[]).append(record.source_id)
    normalized=[]
    for (path,digest,retrieved),qids in grouped.items():
        if sha(path)!=digest:raise ValueError('Saved entity raw changed')
        class SavedFetcher:
            def get(self,*args,**kwargs):return SimpleNamespace(status='success',content=Path(path).read_bytes(),artifact=SimpleNamespace(path=path,sha256=digest,retrieved_at=retrieved))
        result=WikidataAdapter(SavedFetcher()).fetch_entities(qids,languages=languages,linked_labels=labels)
        for record in result.records:
            used={v['id'] for prop in ('P50','P655','P98','P110','P136','P495','P179','P921','P674','P840') for v in claim_values({'claims':record.extra['claims']},prop) if isinstance(v,dict) and 'id' in v}
            record.extra['linked_label_provenance']={q:proofs[q] for q in used if q in proofs}
            normalized.append(record)
    write_jsonl(normalized,output/'works-enriched.jsonl')
    report={'version':'scale-acquisition-v1','records':len(normalized),'discovered_unique':len(set(ids)),'held_entities':len(state['held']),'linked_entities':len(linked),'held_linked_entities':len(linked_state['held']),'selection_sha256':sha(output/'selection.json'),'records_sha256':sha(output/'works-enriched.jsonl'),'elapsed_seconds':time.monotonic()-started,'automatic_retries':0,'completed_at':now(),'rights_scope':'structuredWikidatametadataonly','genre_presence_selection_bias':True}
    save(output/'manifest.json',report);print(json.dumps(report),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--target',type=int,default=12000);p.add_argument('--phase',choices=['all','discover','enrich'],default='all');a=p.parse_args()
    if not 1000<=a.target<=150000:p.error('target must be1000..150000')
    collect(a.output,a.target,a.phase)
