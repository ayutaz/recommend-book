"""Acquire a separate light-novel frame; raw metadata only, no catalogue writes.

Each exact URL/parameter input has one durable request checkpoint. Failed or
interrupted inputs are held by default. An explicit, bounded recovery preserves
the former receipt; rerunning alone never retries.
Wikidata's discovery index is not an admission assertion. Current entities must
assert a literary type and Japanese original language before metadata admission.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

from recommend_book.ingestion.core import HTTPFetcher, RawStore

ROOT=Path('data/light-novels-v1/acquisition')
ENTITY_URL='https://www.wikidata.org/w/api.php'
QLEVER_URL='https://qlever.dev/api/wikidata'
SERIES_TYPES={'Q104213567'}
WORK_TYPES={'Q747381'}
# TV/anime/manga entities are not novel-series entities even when co-typed.
CONFLICT_TYPES={'Q5398426','Q639669','Q21198342','Q8274','Q202866','Q4167410','Q4167836',
                'Q11424','Q24856','Q11346433','Q136747113'}


def now(): return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); tmp.replace(path)
def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(data): return hashlib.sha256(data).hexdigest()
def rows(path):
    if Path(path).exists(): return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]
    return []
def jsonl(path,values):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in values),encoding='utf-8');tmp.replace(path)


@contextmanager
def collection_lock(root):
    path = Path(root) / 'ndl-collector.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'started_at': now(), 'purpose': 'single NDL acquisition/index writer'}, stream)
    except FileExistsError as exc:
        raise RuntimeError(f'NDL collection lock exists; inspect its process before starting another collector: {path}') from exc
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


class CheckpointFetcher:
    """Persist intent before IO, preserve raw success/error, never repeat input."""
    def __init__(self,root,fetcher):self.root=Path(root);self.fetcher=fetcher
    def get(self,url,*,source,params=None,headers=None):
        spec={'url':url,'params':params or {},'headers':headers or {},'source':source}
        key=sha(json.dumps(spec,sort_keys=True,separators=(',',':')).encode())
        path=self.root/'requests'/f'{key}.json'
        if path.exists():
            receipt=load(path)
            if receipt['status']!='success':
                if not receipt.get('recovery_authorized'):return None
                archive=self.root/'request-history'/f"{key}-attempt-{receipt.get('attempt',1)}.json"
                save(archive,receipt)
            else:
                raw=Path(receipt['raw_path']).read_bytes()
                if sha(raw)!=receipt['raw_sha256']:raise ValueError('raw SHA mismatch')
                return raw,receipt
            prior=receipt
        else:prior={}
        receipt={**spec,'request_sha256':key,'started_at':now(),'status':'attempted','attempt':prior.get('attempt',0)+1,'recovery_of':prior.get('request_sha256')}
        save(path,receipt)
        if isinstance(self.fetcher,HTTPFetcher):
            # One lock per host retains cross-process NDL serialization without
            # letting an unrelated index's pacing block NDL for minutes.
            gate=self.root/'raw'/('http-rate-gate-'+urlsplit(url).hostname.replace('.','-')+'.sqlite3')
            with sqlite3.connect(gate,timeout=120) as db:
                db.execute('CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY,last_request REAL NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS cooldowns (host TEXT PRIMARY KEY,not_before REAL NOT NULL)')
            self.fetcher.gate_path=gate
        result=self.fetcher.get(url,source=source,params=params,headers=headers)
        receipt.update(status=result.status,completed_at=now(),error=result.error,status_code=result.status_code)
        if result.status_code == 429 and isinstance(self.fetcher, HTTPFetcher):
            # A server backpressure response without Retry-After still requires
            # a pause before any *different* input. This does not retry input.
            not_before = time.time() + 120
            with sqlite3.connect(self.fetcher.gate_path, timeout=120) as db:
                db.execute('INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET not_before=MAX(not_before,excluded.not_before)', (urlsplit(url).hostname, not_before))
            receipt['backpressure_cooldown_until_epoch'] = not_before
        if result.artifact:receipt.update(raw_path=result.artifact.path,raw_sha256=result.artifact.sha256)
        save(path,receipt)
        return (result.content,receipt) if result.status=='success' else None



def discovery_query(*,limit=100,offset=0):
    if not 1<=limit<=100 or offset<0:raise ValueError('limit 1..100 and offset >=0 required')
    return ('PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> '
      'SELECT DISTINCT ?work WHERE { { ?work wdt:P31 wd:Q104213567. } '
      'UNION { ?work wdt:P7937 wd:Q104213567. } '
      'UNION { ?work wdt:P31 wd:Q747381. } UNION { ?work wdt:P7937 wd:Q747381. } '
      f'}} ORDER BY ?work LIMIT {limit} OFFSET {offset}')


def item_values(entity,prop):
    out=[]
    for st in entity.get('claims',{}).get(prop,[]):
        snak=st.get('mainsnak',{});value=snak.get('datavalue',{}).get('value')
        if st.get('rank')!='deprecated' and snak.get('snaktype')=='value' and isinstance(value,dict) and re.fullmatch(r'Q[1-9][0-9]*',value.get('id','')):out.append(value['id'])
    return list(dict.fromkeys(out))


def normalize_entity(entity,linked,provenance,conditions_path):
    def label(e):
        labels=e.get('labels',{})
        return next((labels[k]['value'] for k in ('ja','en') if k in labels),e.get('id',''))
    def named(prop):return [{'id':q,'label':label(linked.get(q,{'id':q}))} for q in item_values(entity,prop)]
    qid=entity['id'];types=set(item_values(entity,'P31')+item_values(entity,'P7937'))
    languages=item_values(entity,'P407');reasons=[]
    kind='series' if types & SERIES_TYPES else 'work' if types & WORK_TYPES else 'unknown'
    if kind=='unknown':reasons.append('light_novel_form_unproved')
    if types & CONFLICT_TYPES:reasons.append('conflicting_adaptation_type')
    if item_values(entity,'P629'):reasons.append('edition_not_original_work')
    if 'Q5287' not in languages:reasons.append('japanese_original_language_unproved')
    if set(languages)-{'Q5287'}:reasons.append('multiple_original_languages_require_review')
    dates=[s.get('mainsnak',{}).get('datavalue',{}).get('value',{}).get('time') for s in entity.get('claims',{}).get('P577',[]) if s.get('rank')!='deprecated']
    return {'schema_version':'light-novel-metadata-v1','id':qid,'source':'wikidata','kind':kind,'title':label(entity),
      'aliases':list(dict.fromkeys([v['value'] for v in entity.get('labels',{}).values()]+[v['value'] for a in entity.get('aliases',{}).values() for v in a])),
      'authors':named('P50'),'original_languages':languages,'countries':item_values(entity,'P495'),
      'types':sorted(types),'genres':named('P136'),'subjects':named('P921'),'publishers':named('P123'),
      'publication_dates':[d for d in dates if d], 'sitelinks':entity.get('sitelinks',{}),
      'relations':{p:item_values(entity,p) for p in ('P179','P527','P629','P155','P156','P144')},
      'provenance':{'url':f'https://www.wikidata.org/wiki/{qid}','entity_revision':entity.get('lastrevid'),**provenance,'conditions_path':conditions_path,'conditions_sha256':sha(Path(conditions_path).read_bytes()) if conditions_path and Path(conditions_path).exists() else None,'linked_provenance':{q:{k:v for k,v in linked.get(q,{}).get('_provenance',{}).items()} for prop in ('P50','P136','P921','P123') for q in item_values(entity,prop)}},
      'admission':{'accepted':not reasons,'reasons':reasons,'evidence':{'type_properties':['P31','P7937'],'language_property':'P407','original_language_inference_from_label':False}}}


def make_fetcher(root):
    return CheckpointFetcher(root,HTTPFetcher(RawStore(root/'raw'),max_attempts=1,min_interval=1.1,
      host_intervals={'qlever.dev':10,'ndlsearch.ndl.go.jp':20},user_agent='recommend-book/0.1 (https://github.com/ayutaz/recommend-book) light-novel-metadata-v1'))


def conditions(root,fetcher):
    path=root/'source-conditions.json'
    if path.exists():return path
    specs=[('wikidata','https://www.wikidata.org/wiki/Wikidata:Licensing'),('ndl','https://www.ndl.go.jp/data/data_service/quickguide'),('ndl','https://ndlsearch.ndl.go.jp/help/api')]
    snapshots=[]
    for source,url in specs:
        result=fetcher.get(url,source=source)
        if result is None:raise RuntimeError('source conditions snapshot held: '+url)
        snapshots.append(result[1])
    save(path,{'version':'light-novel-source-conditions-v1','reviewed_at':now(),'snapshots':snapshots,
      'wikidata':{'scope':'structured metadata only','license':'CC0','usages':{'local_storage':True,'metadata_display':True,'metadata_recommendation':True,'llm_input':False},'prose_excluded':True},
      'ndl':{'scope':'NDL-created bibliographic metadata only, provider iss-ndl-opac; no prose','usages':{'local_storage':True,'metadata_display':True,'metadata_recommendation':True,'llm_input':False},'provider':'iss-ndl-opac','serial_requests':True,'automatic_retries':0,'credit':'NDLサーチAPIの国立国会図書館書誌データを加工して作成','condition_urls':[u for s,u in specs if s=='ndl']}})
    return path


def collect(root=ROOT,*,max_pages=100,entity_limit=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);fetcher=make_fetcher(root);cond=conditions(root,fetcher)
    state_path=root/'discovery.json';state=load(state_path) if state_path.exists() else {'ids':[],'offset':0,'complete':False,'pages':[]}
    ids=state['ids']
    for _ in range(max_pages):
        if state['complete'] or state.get('held'):break
        offset=state['offset'];result=fetcher.get(QLEVER_URL,source='qlever',params={'query':discovery_query(limit=100,offset=offset),'format':'json'},headers={'Accept':'application/sparql-results+json'})
        if result is None:state['held']=True;save(state_path,state);break
        raw,receipt=result
        try:found=[r['work']['value'].rsplit('/',1)[-1] for r in json.loads(raw)['results']['bindings']]
        except (ValueError,KeyError,TypeError):
            state['held']=True;state['parse_error']='discovery response invalid';save(state_path,state);break
        ids[:]=list(dict.fromkeys(ids+found));state.update(offset=offset+100,complete=len(found)<100)
        state['pages'].append({'offset':offset,'count':len(found),'raw_path':receipt['raw_path'],'raw_sha256':receipt['raw_sha256']});save(state_path,state)
        print(json.dumps({'phase':'discover','ids':len(ids),'complete':state['complete']}),flush=True)
    # Mandatory minimal entity probe precedes all bulk entity requests.
    entity_index_path=root/'entities-index.json';index=load(entity_index_path) if entity_index_path.exists() else {}
    batches=[['Q5357270','Q104213567','Q747381']]
    selected=ids[:entity_limit] if entity_limit else ids
    selected=[q for q in selected if (q not in index or not index[q].get('entity_revision')) and q not in batches[0]]
    batches.extend(selected[i:i+50] for i in range(0,len(selected),50))
    for batch in batches:
        if all(q in index and index[q].get('entity_revision') for q in batch):continue
        result=fetcher.get(ENTITY_URL,source='wikidata',params={'action':'wbgetentities','ids':'|'.join(batch),'props':'info|labels|aliases|claims|sitelinks','format':'json'})
        if result is None:continue
        raw,receipt=result;payload=json.loads(raw)
        for q,e in payload.get('entities',{}).items():
            if 'missing' not in e:index[q]={'raw_path':receipt['raw_path'],'raw_sha256':receipt['raw_sha256'],'entity_revision':e.get('lastrevid')}
        save(entity_index_path,index)
        print(json.dumps({'phase':'entities','entities':len(index)}),flush=True)
    entities={}
    raw_payloads = {}
    for q,p in index.items():
        if p['raw_path'] not in raw_payloads:
            content=Path(p['raw_path']).read_bytes()
            if sha(content)!=p['raw_sha256']:raise ValueError('entity raw mismatch')
            raw_payloads[p['raw_path']] = json.loads(content)
        entities[q]=raw_payloads[p['raw_path']]['entities'][q]
    wanted=sorted({ref for e in entities.values() for prop in ('P50','P136','P921','P123') for ref in item_values(e,prop)})
    linked_path=root/'linked-index.json';linked_index=load(linked_path) if linked_path.exists() else {}
    pending=[q for q in wanted if q not in linked_index]
    for start in range(0,len(pending),50):
        batch=pending[start:start+50];result=fetcher.get(ENTITY_URL,source='wikidata',params={'action':'wbgetentities','ids':'|'.join(batch),'props':'labels','languages':'ja|en','format':'json'})
        if result is None:continue
        content,receipt=result
        for q,e in json.loads(content).get('entities',{}).items():linked_index[q]={'entity':e,'raw_path':receipt['raw_path'],'raw_sha256':receipt['raw_sha256']}
        save(linked_path,linked_index)
    linked={q:{**x['entity'],'_provenance':{'raw_path':x['raw_path'],'raw_sha256':x['raw_sha256']}} for q,x in linked_index.items()}
    metadata=[normalize_entity(e,linked,index[q],str(cond)) for q,e in entities.items() if q not in {'Q104213567','Q747381'}]
    accepted=[r for r in metadata if r['admission']['accepted']]
    jsonl(root/'metadata.jsonl',accepted);jsonl(root/'held-metadata.jsonl',[r for r in metadata if not r['admission']['accepted']])
    report={'version':'light-novel-acquisition-v1','updated_at':now(),'discovery_complete':state['complete'],'discovered':len(ids),'fetched':len(metadata),'accepted':len(accepted),'kinds':dict(Counter(r['kind'] for r in accepted)),
      'held_reasons':dict(Counter(reason for r in metadata for reason in r['admission']['reasons'])),'population':'Wikidata explicit light novel / light novel series entities only; not all Japanese publications','japanese_national_coverage_proved':False,'mandatory_mahoka_present':any(r['id']=='Q5357270' for r in accepted),'entities_index':str(entity_index_path),'metadata':str(root/'metadata.jsonl')}
    save(root/'manifest.json',report);return report



def ndl_label_query(term, year=None, *, search_index='anywhere'):
    if not term or any(c in term for c in ('"', '\\', '\n', '\r')):raise ValueError('unsafe NDL literal')
    if search_index not in {'title', 'anywhere'}:
        raise ValueError('unsupported NDL search index')
    query=f'{search_index}="{term}"'
    if year is not None:
        if not re.fullmatch(r'[12][0-9]{3}(?:-[01][0-9])?',str(year)):raise ValueError('year/month required')
        if len(str(year))==4:start,end=f'{year}-01-01',f'{year}-12-31'
        else:
            import calendar
            yr,mo=map(int,str(year).split('-'));start=f'{year}-01';end=f'{year}-{calendar.monthrange(yr,mo)[1]:02d}'
        query+=f' AND from="{start}" AND until="{end}"'
    return query+' AND dpid="iss-ndl-opac"'


def ndl_year_partitions(total, facets):
    if not facets or sum(facets.values())!=total or not all(re.fullmatch(r'[12][0-9]{3}',k) for k in facets):return None
    return sorted(facets.items())


def edition_projection(record, sources, conditions_path):
    r = record
    cond = Path(conditions_path)
    labelmatch = [x for x in sources if r.get('series') and x['query_term'] in r['series']]
    return {'schema_version': 'light-novel-edition-v1', 'source': 'ndl', 'provider': 'iss-ndl-opac', 'id': r['source_id'],
        'title': r['titles'][0]['value'] if r['titles'] else '', 'volume': r.get('volume'),
        'authors': [c['name'] for c in r['contributors'] if c['role'] == 'author'], 'publisher': r.get('publisher'),
        'date': r.get('publication_year'), 'isbn': [i['value'] for i in r['identifiers'] if i['source'] == 'isbn'],
        'languages': [r['language']] if r['language'] else [], 'label': r.get('series'), 'source_url': r['source_url'],
        'source_record': r, 'label_evidence': sources, 'label_candidate': bool(labelmatch), 'ndc': r['extra'].get('ndc_codes', []),
        'admission': {'light_novel_confirmed': False, 'reason': 'Label roster discovery is bibliographic candidate evidence, not a story series or genre assertion.'},
        'provenance': {'url': r['source_url'], 'raw_path': r['raw_path'], 'raw_sha256': r['raw_sha256'], 'conditions_path': str(cond), 'conditions_sha256': sha(cond.read_bytes())}}


def collect_ndl(root=ROOT, *, roster=Path('data/light-novels-v1/coverage/label-roster-v1.json'), label_limit=None, search_index='anywhere'):
    import xml.etree.ElementTree as ET
    from recommend_book.ingestion.ndl import NDLAdapter, SRU_URL, SRU
    from recommend_book.ingestion.core import RawArtifact
    root=Path(root);fetcher=make_fetcher(root);cond=conditions(root,fetcher)
    data=load(roster);labels=data if isinstance(data,list) else data.get('labels',data.get('records',[]))
    if label_limit:labels=labels[:label_limit]
    ndlroot=root/'ndl';ndlroot.mkdir(parents=True,exist_ok=True)
    index_path=ndlroot/'index.json';index=load(index_path) if index_path.exists() else {'version':'light-novel-ndl-acquisition-v1','labels':{},'roster_sha256':sha(Path(roster).read_bytes()), 'search_index': search_index}
    if index.get('search_index', 'title') != search_index:
        prior_index = index.get('search_index', 'title')
        save(ndlroot / f'index-{prior_index}-v1.json', index)
        index = {'version': 'light-novel-ndl-acquisition-v1', 'labels': {}, 'roster_sha256': sha(Path(roster).read_bytes()), 'search_index': search_index, 'supersedes_index': f'index-{prior_index}-v1.json'}
    record_path=ndlroot/'source-records.jsonl';record_map={r['source_id']:r for r in rows(record_path)}
    prior_memberships = {r['id']: r.get('label_evidence', []) for r in rows(root / 'edition-metadata.jsonl')}
    query_path=ndlroot/'query-receipts.jsonl';queries=rows(query_path);querymap={r['query_sha256']:r for r in queries}
    adapter=NDLAdapter(None,approved_providers={'iss-ndl-opac'})
    def page(term,partition,expected=None):
        query=ndl_label_query(term,partition,search_index=search_index)
        params={'operation':'searchRetrieve','version':'1.2','recordSchema':'dcndl','recordPacking':'xml','query':query,'maximumRecords':500,'startRecord':1}
        query_sha=sha(json.dumps(params,sort_keys=True,ensure_ascii=False).encode())
        if query_sha in querymap and querymap[query_sha].get('parser_version')==2 and querymap[query_sha].get('status') == 'success':return querymap[query_sha]
        out={'query_sha256':query_sha,'query_term':term,'partition':partition,'expected_total':expected,'requested_position':1,'returned_record_ids':[],'complete':False,'query':query,'provider':'iss-ndl-opac'}
        result=fetcher.get(SRU_URL,source='ndl',params=params)
        if result is None:
            out.update(status='held_request', error='request failed or prior incomplete request held; source total remains unknown')
            request_spec = {'url': SRU_URL, 'params': params, 'headers': {}, 'source': 'ndl'}
            request_key = sha(json.dumps(request_spec, sort_keys=True, separators=(',', ':')).encode())
            request_path = root / 'requests' / f'{request_key}.json'
            if request_path.exists():
                failure = load(request_path)
                out['request_receipt_path'] = str(request_path)
                out['error'] = failure.get('error') or f"prior request status {failure.get('status')}; source total remains unknown"
            querymap[query_sha] = out
            jsonl(query_path, querymap.values())
            return out
        raw,receipt=result;out['receipt']=receipt
        try:
            xml=ET.fromstring(raw)
            diagnostics=[x.text for x in xml.iter() if x.tag.rsplit('}',1)[-1]=='message']
            if diagnostics:raise ValueError(';'.join(str(x) for x in diagnostics))
            total=int(xml.findtext(f'{{{SRU}}}numberOfRecords','-1'))
            if total<0:raise ValueError('missing total')
            facets={x.get('name'):int(x.text) for node in xml.iter() if node.tag.rsplit('}',1)[-1]=='lst' and node.get('name')=='ISSUED_DATE' for x in node if x.tag.rsplit('}',1)[-1]=='int'}
            artifact=RawArtifact(receipt['raw_sha256'],receipt['raw_path'],len(raw),receipt['completed_at'],receipt['url'])
            rejected=[]
            for i,node in enumerate(xml.findall(f'.//{{{SRU}}}recordData')):
                if len(node)==0 and node.text:node=ET.fromstring(node.text)
                try:r=adapter._normalize(node,artifact,'iss-ndl-opac',locator=f'records[{i+1}]/recordData')
                except (ValueError,TypeError) as exc:rejected.append({'index':i+1,'error':str(exc)});continue
                record_map[r.source_id]=r.model_dump(mode='json');out['returned_record_ids'].append(r.source_id)
            out.update(parser_version=2,status='success',expected_total=total,year_facets=facets,rejected_records=rejected,complete=total<=500 and len(set(out['returned_record_ids']))==total and not rejected)
            if expected is not None and total!=expected:out['expected_changed_from_parent']=expected;out['complete']=False
        except (ValueError,ET.ParseError,TypeError) as exc:out.update(status='held_parse',error=str(exc))
        querymap[query_sha]=out;jsonl(query_path,querymap.values());jsonl(record_path,record_map.values())
        return out
    for label in labels:
        key=label['id'];entry=index['labels'].setdefault(key,{'label_id':key,'label':label['label'],'scope':label.get('scope'),'terms':{},'complete':False})
        for term in label.get('query_terms',[label['label']]):
            if entry['terms'].get(term,{}).get('complete'):continue
            allpage=page(term,None);termstate={'query_term':term,'root':allpage,'partitions':[],'complete':allpage['complete']}
            if (allpage.get('expected_total') or 0)>500:
                parts=ndl_year_partitions(allpage['expected_total'],allpage.get('year_facets',{}))
                if parts is None:termstate['hold']='year_facets_do_not_cover_root_total'
                else:
                    for year,expected in parts:
                        yp=page(term,year,expected)
                        if (yp.get('expected_total') or 0)>500:
                            termstate.setdefault('year_parent_pages', []).append(yp)
                            monthly = [page(term, f'{year}-{month:02d}') for month in range(1, 13)]
                            termstate['partitions'].extend(monthly)
                            month_ids = {q for mp in monthly for q in mp['returned_record_ids']}
                            if not all(mp['complete'] for mp in monthly) or sum(mp.get('expected_total') or 0 for mp in monthly) != yp['expected_total'] or len(month_ids) != yp['expected_total']:
                                termstate['hold'] = 'month_partitions_do_not_cover_year_total'
                        else:
                            termstate['partitions'].append(yp)
                        entry['terms'][term]=termstate;save(index_path,index)
                        print(json.dumps({'phase':'ndl','label':label['label'],'partition':year,'expected':expected,'returned':len(yp['returned_record_ids']),'complete':yp['complete']},ensure_ascii=True),flush=True)
                    ids={q for p in termstate['partitions'] for q in p['returned_record_ids']}
                    termstate['complete']=all(p['complete'] for p in termstate['partitions']) and len(ids)==allpage['expected_total'] and not termstate.get('hold')
            entry['terms'][term]=termstate;save(index_path,index)
        entry['complete']=all(v['complete'] for v in entry['terms'].values());save(index_path,index)
        print(json.dumps({'phase':'ndl_label','label':label['label'],'complete':entry['complete'],'records':len(record_map)},ensure_ascii=True),flush=True)
        # Materialize metadata after every label so readers can make incremental progress.
        memberships = {q: list(values) for q, values in prior_memberships.items()}
        for lk, le in index['labels'].items():
            for term, ts in le['terms'].items():
                record_ids = {q for pg in [ts['root']] + ts.get('partitions', []) for q in pg['returned_record_ids']}
                for q in record_ids:
                    membership = {'label_id': lk, 'query_term': term, 'scope': le['scope']}
                    if membership not in memberships.setdefault(q, []):
                        memberships[q].append(membership)
        editions = [edition_projection(r, memberships.get(r['source_id'], []), cond) for r in record_map.values()]
        jsonl(root/'edition-metadata.jsonl',editions)
    index.update(updated_at=now(),records=len(record_map),complete=len(index['labels'])==len(labels) and all(x['complete'] for x in index['labels'].values()))
    save(index_path,index)
    return {'labels':len(index['labels']),'records':len(record_map),'complete':index['complete']}

def description_origin(text):
    """Explicit Japanese literary category; never infer the original language."""
    return bool(re.match(r'^Japanese (?:(?:fantasy|science fiction|romance|mystery|comedy|romantic comedy|isekai|adventure) )?(?:light novel|novel)(?: series)?(?:$|[ ,.;])', text or '', re.I))


def resolve_original_languages(root=ROOT):
    root = Path(root)
    fetcher = make_fetcher(root)
    cond = conditions(root, fetcher)
    prior = rows(root / 'metadata.jsonl')
    held = rows(root / 'held-metadata.jsonl')
    supplemental_path = root / 'series-form-entity-receipt-v1.json'
    supplemental = {}
    if supplemental_path.exists():
        p = load(supplemental_path)
        raw = Path(p['raw_path']).read_bytes()
        if sha(raw) != p['raw_sha256']:
            raise ValueError('supplemental series form raw SHA mismatch')
        known = {r['id'] for r in prior + held}
        for q, entity in json.loads(raw).get('entities', {}).items():
            if q in known:
                continue
            provenance = {'raw_path': p['raw_path'], 'raw_sha256': p['raw_sha256'], 'entity_revision': entity.get('lastrevid')}
            held.append(normalize_entity(entity, {}, provenance, str(cond)))
            supplemental[q] = provenance
    candidates = [r for r in held if r['admission']['reasons'] == ['japanese_original_language_unproved'] and not r['original_languages']]
    index_path = root / 'original-language-resolution-index-v2.json'
    index = load(index_path) if index_path.exists() else {}
    index.update(supplemental)
    save(index_path, index)
    pending = [r['id'] for r in candidates if r['id'] not in index]
    for start in range(0, len(pending), 50):
        batch = pending[start:start + 50]
        result = fetcher.get(ENTITY_URL, source='wikidata', params={'action': 'wbgetentities', 'ids': '|'.join(batch), 'props': 'info|labels|aliases|claims|descriptions|sitelinks', 'format': 'json'})
        if result is None:
            continue
        raw, receipt = result
        for q, e in json.loads(raw).get('entities', {}).items():
            if 'missing' not in e:
                index[q] = {'raw_path': receipt['raw_path'], 'raw_sha256': receipt['raw_sha256'], 'entity_revision': e.get('lastrevid')}
        save(index_path, index)
    linked_index = load(root / 'linked-index.json')
    linked = {q: {**x['entity'], '_provenance': {'raw_path': x['raw_path'], 'raw_sha256': x['raw_sha256']}} for q, x in linked_index.items()}
    resolved, unresolved = [], []
    for prior_record in candidates:
        q = prior_record['id']
        if q not in index:
            unresolved.append({'id': q, 'reason': 'source_acquisition_held'})
            continue
        p = index[q]
        raw = Path(p['raw_path']).read_bytes()
        if sha(raw) != p['raw_sha256']:
            raise ValueError('resolution raw SHA mismatch')
        e = json.loads(raw)['entities'][q]
        record = normalize_entity(e, linked, p, str(cond))
        text = e.get('descriptions', {}).get('en', {}).get('value', '')
        if record['admission']['accepted']:
            resolved.append(record)
            continue
        if record['admission']['reasons'] != ['japanese_original_language_unproved'] or not description_origin(text):
            unresolved.append({'id': q, 'reason': 'explicit_japanese_literary_description_absent_or_conflict', 'description': text, 'provenance': p})
            continue
        resolution = {'method': 'wikidata_description_explicit_japanese_literary_original_v1', 'language': None, 'source_url': f'https://www.wikidata.org/wiki/{q}', **p, 'locator': f'entities.{q}.descriptions.en.value', 'text': text, 'assertion_scope': 'Japanese literary category; original language remains unknown'}
        record['schema_version'] = 'light-novel-metadata-v2'
        record['original_language_resolution'] = resolution
        record['admission'].update(accepted=True, reasons=[], version='light-novel-admission-v2')
        record['admission']['evidence'].update(resolution_method=resolution['method'], language_property=None)
        resolved.append(record)
    jsonl(root / 'metadata-resolved-v2.jsonl', prior + resolved)
    jsonl(root / 'original-language-unresolved-v2.jsonl', unresolved)
    report = {'version': 'light-novel-japanese-category-resolution-v2', 'created_at': now(), 'candidates': len(candidates), 'resolved': len(resolved), 'metadata_total': len(prior + resolved), 'unresolved': len(unresolved), 'original_language_inferred': False}
    save(root / 'original-language-resolution-report-v2.json', report)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, default=ROOT)
    p.add_argument('--max-pages', type=int, default=100)
    p.add_argument('--entity-limit', type=int)
    p.add_argument('--source', choices=['wikidata', 'ndl', 'resolve'], default='wikidata')
    p.add_argument('--label-limit', type=int)
    p.add_argument('--roster', type=Path, default=Path('data/light-novels-v1/coverage/label-roster-v1.json'))
    a = p.parse_args()
    if a.source == 'resolve':
        result = resolve_original_languages(a.output)
    elif a.source == 'ndl':
        with collection_lock(a.output):
            result = collect_ndl(a.output, roster=a.roster, label_limit=a.label_limit)
    else:
        result = collect(a.output, max_pages=a.max_pages, entity_limit=a.entity_limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
