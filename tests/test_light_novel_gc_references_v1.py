import hashlib
import json

import pytest

from recommend_book.light_novel_gc_references_v1 import verify_gc_references


START = 'https://gcnovels.jp/search?keyword=&title=&author=&isbn=&search_series=&amount=120&order=release_date_desc'


def url(page):
    return f'https://gcnovels.jp/search?amount=120&order=release_date_desc&page={page}'


def pin(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(env):
    env['proof']['stopped_frame_sha256'] = pin(env['root']/'stop.json', env['stop'])
    env['proof']['partial_roster_sha256'] = pin(env['root']/'roster.json', env['roster'])
    env['sha'] = pin(env['path'], env['proof'])


def fixture(tmp_path):
    pages, roster = [], []
    for page in range(1, 6):
        cards=[]
        for i in range(1, 121):
            number=(page-1)*120+i
            title=f'完全題{number}　巻３【特装版】'
            product=f'https://gcnovels.jp/book/{number}'
            cards.append(f'<li class="book-list-item-root"><div class="book-title"><h3><a href="{product}">{title}</a></h3>'
                '<ul class="author"><li>小説<a href="/author/1">架空著者</a></li><li>イラスト<a href="/author/2">架空絵師</a></li></ul></div>'
                '<img src="PRIVATE_IMAGE"><p>PRIVATE_PROSE</p></li>')
            roster.append({'publisher_reference_id':f'gc-product:{number}','official_product_id':str(number),
                'source_unit':'publisher_product_reference','title':title,'source_url':product,
                'credits':[{'role':'author','role_literal':'小説','name':'架空著者'},
                    {'role':'illustrator','role_literal':'イラスト','name':'架空絵師'}],
                'publisher':None,'isbn':None,'label':None,'label_literals':[],'form':None,'volume':None,
                'official_lightnovel_tag':None,'story_series_asserted':False,'metadata_conflict':False,'bibliographic_variants':[]})
        numbers=sorted(set([max(1,page-1),page+1]))
        links=''.join(f'<li><a href="{url(n)}">{n}</a></li>' for n in numbers)
        document=f'<div class="paging">{(page-1)*120+1}~{page*120}件（全627件）</div><ul class="book-list">'+''.join(cards)+'</ul>'
        document+=f'<ul class="pagination book-pagination"><li class="active">{page}</li>{links}<li><a rel="next" href="{url(page+1)}">次</a></li></ul>'
        document+='<input type="hidden" value="PRIVATE_HIDDEN"><script>PRIVATE_SCRIPT</script>'
        raw=document.encode('utf8');target=tmp_path/f'{page}.bin';target.write_bytes(raw)
        receipt={'source_url':START if page==1 else url(page),'http_status':200,
            'content_type':'text/html; charset=utf-8','raw_path':target.name,
            'sha256':hashlib.sha256(raw).hexdigest(),'byte_length':len(raw)}
        pages.append({'page':page,'source_receipt':receipt,'card_count':120,'next_url':url(page+1),
            'publisher_total_window':f'{(page-1)*120+1}~{page*120}件（全627件）','linked_page_numbers':numbers})
    failed={'source_url':url(6),'final_url':url(6),'http_status':500,'error':'HTTPError','raw_path':None,'sha256':None}
    stop={'version':1,'frame_id':'gc-common-search-product-index-v1','status':'stopped_source_hold_no_retry',
        'source_url':START,'fixed_reported_total':627,'fixed_amount':120,'fixed_pages':6,
        'pages':pages,'processed_pages':5,'observed_references':600,'stopped_page':6,
        'index_pages_complete':False,'receipt':failed}
    proof={'version':1,'status':'partial_source_http500_hold_no_retry','site_brand_scope':['GCノベルズ','GCN文庫'],
        'per_product_brand_assignment':'unknown','publisher_reported_total':627,'finite_pages_from_total':6,
        'pages_acquired':5,'index_pages_complete':False,'reference_occurrences_acquired':600,
        'unique_product_references_acquired':600,'duplicate_ids':[],'conflicting_ids':[],
        'unacquired_final_page':6,'unacquired_expected_references':27,'failed_receipt':failed,
        'failed_url_resend_count':0,'alternate_route_workaround_requests':0,
        'stopped_frame_path':'stop.json','partial_roster_path':'roster.json'}
    env={'root':tmp_path,'path':tmp_path/'proof.json','stop':stop,'proof':proof,'roster':roster}
    save(env);return env


def verify(env):
    return verify_gc_references(env['path'],env['sha'])


def change_raw(env,old,new,page=1):
    r=env['stop']['pages'][page-1]['source_receipt'];p=env['root']/r['raw_path']
    text=p.read_text(encoding='utf8');assert old in text
    p.write_text(text.replace(old,new,1),encoding='utf8')
    r.update(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),byte_length=p.stat().st_size)
    save(env)


def test_replays_partial_source_with_unknown_brand_and_no_prose(tmp_path):
    env=fixture(tmp_path);rows=verify(env);r=rows[0]
    assert len(rows)==600 and r['title']=='完全題1　巻３【特装版】'
    assert r['publisher_reference_id']=='gc-product:1' and r['source_url']=='https://gcnovels.jp/book/1'
    assert r['credits']==env['roster'][0]['credits']
    assert all(r[k] is None for k in ['isbn','publisher','form','label','official_lightnovel_tag'])
    assert r['label_literals']==[] and r['story_series_asserted'] is False
    assert r['source_frame_scope']=={'index_pages_complete':False,'pages_verified':5,
        'publisher_reported_total':627,'references_verified':600,'unacquired_references':27,'unacquired_http_status':500}
    assert r['source_evidence'][0]['raw_sha256']==env['stop']['pages'][0]['source_receipt']['sha256']
    assert 'PRIVATE_' not in json.dumps(rows)


@pytest.mark.parametrize('key,value',[('status','terminal'),('index_pages_complete',True),('pages_acquired',6),
    ('unacquired_expected_references',0),('publisher_reported_total',600),('failed_url_resend_count',1)])
def test_rejects_changed_or_false_completion_scope(tmp_path,key,value):
    env=fixture(tmp_path);env['proof'][key]=value;save(env)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('key,value',[('http_status',200),('source_url','https://gcnovels.jp/search?page=7'),
    ('raw_path','some.bin'),('sha256','0'*64),('location','https://evil.invalid/')])
def test_rejects_failed_receipt_scope_tamper(tmp_path,key,value):
    env=fixture(tmp_path);env['stop']['receipt'][key]=value;save(env)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('key,value',[('http_status',500),('final_url','https://evil.invalid/'),('redirects_followed',True),
    ('raw_path','../escape'),('raw_path','C:/escape'),('sha256','0'*64),('byte_length',1),('content_type','image/png')])
def test_rejects_acquired_receipt_tamper(tmp_path,key,value):
    env=fixture(tmp_path);env['stop']['pages'][0]['source_receipt'][key]=value;save(env)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('old,new,page',[(url(2),url(3),1),('全627件','全600件',1),('1~120件','2~121件',1),
    ('class="active">5','class="active">4',5),('class="book-list-item-root"','class="unrelated"',1),
    ('/book/1"','/book/2"',1),('/book/121"','/book/1"',2),
    ('<h3>','<h4>',1),('rel="next"','rel="prev"',5),('order=release_date_desc','order=title_asc',1)])
def test_rejects_raw_count_boundary_chain_or_identity_tamper(tmp_path,old,new,page):
    env=fixture(tmp_path);change_raw(env,old,new,page)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('field,value',[('title','FORGED_TITLE'),('label','GCN文庫'),('isbn','9784758047678'),
    ('credits',[{'role':'author','role_literal':'小説','name':'FORGED_AUTHOR'}])])
def test_pinned_roster_tamper_is_not_authority(tmp_path,field,value):
    env=fixture(tmp_path);env['roster'][0][field]=value;save(env)
    with pytest.raises(ValueError):verify(env)


def test_unknown_role_not_promoted_to_author(tmp_path):
    env=fixture(tmp_path);change_raw(env,'小説<a','未知役割<a')
    env['roster'][0]['credits'][0].update(role='unknown',role_literal='未知役割');save(env)
    assert verify(env)[0]['credits'][0]['role']=='unknown'


def test_manifest_stopped_sha_and_bounded_size(tmp_path):
    env=fixture(tmp_path)
    with pytest.raises(ValueError):verify_gc_references(env['path'],'0'*64)
    (env['root']/'stop.json').write_text('{}',encoding='utf8')
    with pytest.raises(ValueError):verify(env)
    save(env);(env['root']/'1.bin').write_bytes(b'x'*5_000_001)
    with pytest.raises(ValueError):verify(env)
