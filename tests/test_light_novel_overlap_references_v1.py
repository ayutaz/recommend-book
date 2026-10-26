import hashlib
import json

import pytest

from recommend_book.light_novel_overlap_references_v1 import verify_overlap_references


START = 'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat=BNK'
LABEL = 'オーバーラップ文庫'
ROUTE = 'https://over-lap.co.jp/bunko/category/0/BNK/'
QUERY = 'pid=&vid=&swrd=&img=1&sort=11&cicon=&min=&max=&udns=0&dosp=&sellfrom_y=&sellfrom_m=&sellto_y=&sellto_m=&hasq=&pno='


def url(page):
    return ROUTE + '?' + QUERY + str(page)


def pin(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    pages = []
    for page in range(1, 35):
        count = 30 if page < 34 else 29
        cards = ''.join(f'<div class="product-card"><a href="/title/product/0/id{(page-1)*30+i}/?cat=BNK&amp;swrd=">'
            f'<b class="bnk__badge">{LABEL}</b><div class="product-card__title">完全題{(page-1)*30+i}　巻３【合本】</div>'
            '<img src="PRIVATE_IMAGE"><p>PRIVATE_PROSE</p></a></div>' for i in range(1, count+1))
        document = f'<title>{LABEL}｜OVERLAP</title><div class="product-cards">{cards}</div>'
        document += f'<li class="on">{page}</li>'
        if page < 34:
            document += f'<li class="next_on"><a href="{url(page+1)}">次</a></li><a href="{url(34)}">34</a>'
        else:
            document += f'<li class="next_off">次の30件</li><a href="{url(33)}">33</a>'
        document += '<input type="hidden" value="PRIVATE_HIDDEN"><script>PRIVATE_SCRIPT</script>'
        target = tmp_path / f'{page}.bin'
        target.write_bytes(document.encode('utf8'))
        receipt = {'source_url':START if page==1 else url(page), 'http_status':200,
            'content_type':'text/html; charset=utf-8', 'raw_path':target.name,
            'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'byte_length':target.stat().st_size}
        pages.append({'page':page,'source_snapshot':receipt,'card_count':count,
            'next_explicit_urls':[url(page+1)] if page<34 else [],'last_numbered_page':34})
    manifest = {'schema_version':'overlap-bunko-product-frame-v1','status':'terminal_fixed34_pages',
        'label':LABEL,'source_url':START,'declared_finite_pages':34,'source_unit':'publisher_product_reference_not_story_series_or_edition',
        'pages':pages,'processed_pages':34,'index_pages_complete':True,'observed_card_occurrences':1019,
        'unique_product_references':1019,'duplicate_reference_ids':[],'conflicting_reference_ids':[],
        'roster_path':'forged.json'}
    pin(tmp_path/'forged.json',[{'title':'FORGED_ROSTER'}])
    env={'root':tmp_path,'manifest':manifest,'path':tmp_path/'manifest.json'}
    save(env)
    return env


def save(env):
    env['sha']=pin(env['path'],env['manifest'])


def change(env, old, new, page=1):
    r=env['manifest']['pages'][page-1]['source_snapshot']
    p=env['root']/r['raw_path']
    text=p.read_text(encoding='utf8')
    assert old in text
    p.write_text(text.replace(old,new,1),encoding='utf8')
    r.update(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),byte_length=p.stat().st_size)
    save(env)


def verify(env):
    return verify_overlap_references(env['path'],env['sha'])


def test_raw_replay_preserves_unknowns_and_ignores_prose_and_private_roster(tmp_path):
    env=fixture(tmp_path); rows=verify(env)
    assert len(rows)==1019
    r=rows[0]
    assert r['publisher_reference_id']=='overlap-product:id1'
    assert r['title']=='完全題1　巻３【合本】'
    assert r['label_literals']==[LABEL] and r['label']==LABEL
    assert all(r[k] is None for k in ['publisher','isbn','form','official_lightnovel_tag'])
    assert r['credits']==[] and r['bibliographic_variants']==[]
    assert r['story_series_asserted'] is False and r['metadata_conflict'] is False
    assert 'PRIVATE_' not in json.dumps(rows) and 'FORGED_ROSTER' not in json.dumps(rows)
    (env['root']/'forged.json').unlink()
    assert verify(env)==rows


@pytest.mark.parametrize('key,value',[('http_status',302),('http_status',503),('final_url','https://evil.invalid/'),
    ('redirects_followed',True),('location','/other'),('error','failed'),('byte_length',1),('content_type','image/png'),
    ('raw_path','../escape'),('raw_path','C:/escape'),('sha256','0'*64)])
def test_rejects_receipt_tamper(tmp_path,key,value):
    env=fixture(tmp_path);env['manifest']['pages'][0]['source_snapshot'][key]=value;save(env)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('old,new,page',[
    ('オーバーラップ文庫｜OVERLAP','別label｜OVERLAP',1),
    ('class="bnk__badge">オーバーラップ文庫','class="bnk__badge">別label',1),
    ('id1/?cat=BNK','id2/?cat=BNK',1),('id31/?cat=BNK','id1/?cat=BNK',2),
    ('?cat=BNK&amp;swrd=','?cat=NOV&amp;swrd=',1),
    (url(2),url(3),1),('sort=11','sort=12',1),
    ('class="next_off"','class="next_on"',34),('class="on">34','class="on">33',34),
    ('class="product-card"','class="unrelated"',34),
    ('class="product-cards"','class="unrelated"',1),
    ('class="product-card__title"','class="unrelated"',1)])
def test_rejects_source_scope_count_identity_or_terminal_tamper(tmp_path,old,new,page):
    env=fixture(tmp_path);change(env,old,new,page)
    with pytest.raises(ValueError):verify(env)


@pytest.mark.parametrize('key,value',[('processed_pages',33),('status','running'),('index_pages_complete',False),
    ('unique_product_references',1020),('declared_finite_pages',35)])
def test_rejects_incomplete_or_changed_frame(tmp_path,key,value):
    env=fixture(tmp_path);env['manifest'][key]=value;save(env)
    with pytest.raises(ValueError):verify(env)


def test_manifest_sha_raw_sha_and_size_are_checked(tmp_path):
    env=fixture(tmp_path)
    with pytest.raises(ValueError):verify_overlap_references(env['path'],'0'*64)
    p=env['root']/'1.bin';p.write_bytes(b'x'*5_000_001)
    with pytest.raises(ValueError):verify(env)


def test_pinned_projection_is_not_authority(tmp_path):
    env=fixture(tmp_path)
    env['manifest']['pages'][0].update(page_projection_path='forged.json',page_projection_sha256='0'*64)
    save(env)
    assert verify(env)[0]['title']=='完全題1　巻３【合本】'
