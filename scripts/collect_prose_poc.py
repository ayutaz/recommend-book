"""Collect a small reviewed Wikisource prose corpus; never starts an LLM.

Run: uv run python scripts/collect_prose_poc.py --output data/prose-poc
Checkpointed work files allow resume without re-requesting completed works.
Wikidata IDs on edition pages remain unresolved candidates until P629 is checked.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re

from recommend_book.ingestion.core import HTTPFetcher, RawStore, write_jsonl
from recommend_book.ingestion.wikisource import WikisourceAdapter, OriginalRightsReview
from recommend_book.models import SourceRecord, WorkInput

# Title/author associations and historic edition selection are explicit curation,
# not human annotation of classification labels.
BOOKS = [
 ('Sense and Sensibility','Sense and Sensibility','Jane Austen',1811,1811),
 ('Pride and Prejudice','Pride and Prejudice (1813)','Jane Austen',1813,1813),
 ('Mansfield Park','Mansfield Park','Jane Austen',1814,1814),
 ('Emma','Emma (Austen)','Jane Austen',1815,1816),
 ('Northanger Abbey','Northanger Abbey','Jane Austen',1817,1818),
 ('Persuasion','Persuasion','Jane Austen',1817,1818),
 ('A Tale of Two Cities','A Tale of Two Cities (1898)','Charles John Huffam Dickens',1859,1898),
 ('Great Expectations','Great Expectations (1st edition)','Charles John Huffam Dickens',1861,1861),
 ('Oliver Twist','Oliver Twist (1838)','Charles John Huffam Dickens',1837,1838),
 ('David Copperfield','Personal History of David Copperfield (1850)','Charles John Huffam Dickens',1849,1850),
 ('Frankenstein','Frankenstein, or the Modern Prometheus (First Edition, 1818)','Mary Wollstonecraft Shelley',1818,1818),
 ("Alice's Adventures in Wonderland","Alice's Adventures in Wonderland (1866)",'Charles Lutwidge Dodgson',1865,1866),
 ('Moby-Dick','Moby-Dick (1851) US edition','Herman Melville',1851,1851),
 ('The Picture of Dorian Gray','The Picture of Dorian Gray (1891)',"Oscar O'Flahertie Wills Wilde",1890,1891),
 ('Treasure Island','Treasure Island (1883)','Robert Louis Stevenson',1881,1883),
 ('Strange Case of Dr Jekyll and Mr Hyde','Strange Case of Dr Jekyll and Mr Hyde','Robert Louis Stevenson',1886,1886),
 ('Jane Eyre','Jane Eyre (1st edition)','Charlotte Brontë',1847,1847),
 ('Wuthering Heights','Wuthering Heights (1st edition)','Emily Jane Brontë',1847,1847),
 ('The Scarlet Letter','Scarlet Letter (1850) 2ed','Nathaniel Hawthorne',1850,1850),
 ('Kidnapped','Kidnapped (Stevenson, 1887)','Robert Louis Stevenson',1886,1887),
]


class Flat(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'): self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.skip=max(0,self.skip-1)
    def handle_data(self,data):
        if not self.skip: self.parts.append(data)
    def text(self): return re.sub(r'\s+',' ',' '.join(self.parts)).strip()


def collect(output: Path, limit: int = 20):
    output.mkdir(parents=True,exist_ok=True)
    checkpoints=output/'works'; checkpoints.mkdir(exist_ok=True)
    adapter=WikisourceAdapter(HTTPFetcher(RawStore(output/'raw'),user_agent='recommend-book-prose-poc/0.1 (https://github.com/ayutaz/recommend-book)',min_interval=1))
    cache_path=output/'metadata.json'
    if cache_path.exists():
        metadata=json.loads(cache_path.read_text(encoding='utf-8'))
    else:
        metadata={'pages':{},'authors':{}}
    required=[b[1] for b in BOOKS[:limit]]+['Author:'+b[2] for b in BOOKS[:limit]]
    missing=list(dict.fromkeys(t for t in required if t not in metadata['pages']))
    for start in range(0,len(missing),40):
        pages,_=adapter.pages(missing[start:start+40])
        for p in pages: metadata['pages'][p['title']]=p
    cache_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    errors=[]
    for number,(title,edition,author,first_year,edition_year) in enumerate(BOOKS[:limit]):
        checkpoint=checkpoints/f'{number+1:02d}.json'
        if checkpoint.exists():
            print(json.dumps({'title':title,'status':'resumed'},ensure_ascii=True),flush=True);continue
        try:
            page=metadata['pages'][edition]; author_page=metadata['pages']['Author:'+author]
            author_rev=author_page['revisions'][0]['revid']
            if author not in metadata['authors']:
                html,artifact,_=adapter.render(author_rev);flat=Flat();flat.feed(html)
                author_text=flat.text()
                match=re.search(r'\b((?:17|18)\d{2})[–—-]((?:18|19)\d{2})\b',author_text)
                if not match: raise ValueError('author lifetime not found in primary page')
                metadata['authors'][author]={'text':author_text,'quote':match[0],'death':int(match[2]),'raw_path':artifact.path,'raw_sha256':artifact.sha256}
                cache_path.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
            facts=metadata['authors'][author]
            edition_text=page['revisions'][0]['slots']['main']['content']
            year_match=re.search(r'\|\s*year\s*=\s*'+str(edition_year)+r'\b',edition_text)
            if title=="Alice's Adventures in Wonderland":
                year_match=re.search(r'edition used is 1866',edition_text)
            if not year_match: raise ValueError('selected edition-year fact missing; choose exact historic edition')
            review=OriginalRightsReview(author=author,author_death_year=facts['death'],first_publication_year=first_year,edition_year=edition_year,original_language='en',language='en',jurisdictions=['JP','US'],author_revision_url=f'https://en.wikisource.org/w/index.php?oldid={author_rev}',author_fact_quote=facts['quote'],edition_revision_url=f"https://en.wikisource.org/w/index.php?oldid={page['revisions'][0]['revid']}",edition_fact_quote=year_match[0],no_translation=True,no_modern_annotations=True)
            listing,_=adapter.query(action='query',list='allpages',apprefix=edition+'/',aplimit=500,apfilterredir='nonredirects')
            descendants=[p['title'] for p in listing['query']['allpages']]
            # Prefer numbered narrative chapters; front matter, prefaces and notes
            # never become evidence. Named Jekyll chapters are separately curated.
            chapters=[t for t in descendants if re.search(r'/Chapter\s+[0-9IVXLCDM]+$',t,re.I)]
            if title=='Strange Case of Dr Jekyll and Mr Hyde':
                chapters=[edition+'/Story of the Door',edition+'/The Carew Murder Case']
            if len(chapters)<2: raise ValueError('two narrative chapters not located')
            def chapter_order(t):
                match=re.search(r'/(?:Volume|Book)\s+(\d+)',t);volume=int(match[1]) if match else 1
                named_book=re.search(r'/Book the (First|Second|Third)',t)
                if named_book: volume={'First':1,'Second':2,'Third':3}[named_book[1]]
                leaf=t.rsplit('/',1)[-1].replace('Chapter ','')
                roman={'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
                values=[roman[c] for c in leaf] if leaf and all(c in roman for c in leaf) else []
                number=sum(-n if i+1<len(values) and n<values[i+1] else n for i,n in enumerate(values))
                return volume,int(leaf) if leaf.isdigit() else number or 99,t
            chapters.sort(key=chapter_order)
            selected=[chapters[0],chapters[max(1,len(chapters)//3)]]
            chapter_pages,_=adapter.pages(selected)
            by_title={p['title']:p for p in chapter_pages}
            work_id=f"ws:en:{page['pageid']}"
            record=adapter.record(title=title,work_id=work_id,review=review,chapter_pages=[by_title[t] for t in selected],author_text=facts['text'],edition_text=edition_text)
            record.extra.update({'author_fact_raw_path':facts['raw_path'],'author_fact_raw_sha256':facts['raw_sha256'],'edition_wikitext_sha256':hashlib.sha256(edition_text.encode()).hexdigest(),'edition_page_id':page['pageid'],'edition_wikidata_candidate':page.get('pageprops',{}).get('wikibase_item'),'work_id_resolution':'Independent Wikisource candidate; edition QID must not be treated as work QID until Wikidata P629/type verified.','first_publication_year_method':'Curated historic bibliographic chronology; classification label gold not provided.','sample_selection':'first narrative chapter and chapter near one-third; two 1600-character slices; not representative full-work coverage'})
            checkpoint.write_text(record.model_dump_json(indent=2),encoding='utf-8')
            print(json.dumps({'title':title,'status':'completed','work_id':work_id,'chapters':selected},ensure_ascii=True),flush=True)
        except (ValueError,KeyError,IndexError) as exc:
            errors.append({'title':title,'error':str(exc)});print(json.dumps(errors[-1],ensure_ascii=True),flush=True)
    records=[SourceRecord.model_validate_json(p.read_text(encoding='utf-8')) for p in sorted(checkpoints.glob('*.json'))]
    write_jsonl(records,output/'source-records.jsonl')
    inputs=[WorkInput(work_id=r.identifiers[0].value,title=r.titles[0].value,authors=[c.name for c in r.contributors],original_language=r.original_language,first_publication_year=r.first_publication_year,evidence=r.evidence,source_record_ids=['wikisource:'+r.source_id]) for r in records]
    (output/'work-inputs.jsonl').write_text(''.join(w.model_dump_json()+'\n' for w in inputs),encoding='utf-8')
    manifest={'requested':limit,'completed':len(records),'excerpt_count':sum(len(r.evidence) for r in records),'llm_input_allowed':sum(e.llm_input_allowed for r in records for e in r.evidence),'errors':errors,'human_gold':False,'llm_classification_started':False,'jurisdictions':['JP','US'],'worldwide_pd_guarantee':False,'work_id_resolution':'pending Wikidata work/edition verification'}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('data/prose-poc'));parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args()
    if not 1<=args.limit<=20: parser.error('limit must be 1..20')
    print(json.dumps(collect(args.output,args.limit),ensure_ascii=True))
