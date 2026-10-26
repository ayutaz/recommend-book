import hashlib
import json
from pathlib import Path

import pytest

from test_light_novel_catalogue_v1 import setup_catalogue,edition_bundle,publisher_edition_v2,write
from recommend_book.light_novel_kadokawa_promotions_v1 import book_links
from recommend_book.recommendation.scale_site_v1 import public_work

URL='https://promo.kadokawa.co.jp/dengekibunko/special/example/'


def page(title):
    return f'''<title>{title} | 特設ページ | 電撃文庫</title>
    <nav><a href="https://www.kadokawa.co.jp/product/111111111111/">別作品</a></nav>
    <div class="series"><h2 id="books">シリーズ一覧</h2>
    <a href="https://www.kadokawa.co.jp/product/123456789012/">第3巻</a>
    <a href="https://example.com/product/222222222222/">外部</a></div>
    <div class="series"><h2 id="books">短編集</h2>
    <a href="https://www.kadokawa.co.jp/product/333333333333/">短編集</a></div>'''


def test_only_explicit_main_novel_book_list_identifiers_are_returned():
    links=book_links(page('作品甲'),URL,'作品甲')
    assert set(links)=={'kadokawa:123456789012'}
    assert '第3巻' not in json.dumps(links,ensure_ascii=False)


@pytest.mark.parametrize('change', ['title','container','host'])
def test_wrong_title_container_or_source_host_rejected(change):
    text=page('作品甲');url=URL
    if change=='title':text=text.replace('<title>作品甲','<title>別作品')
    if change=='container':text=text.replace('シリーズ一覧','関連記事')
    if change=='host':url=url.replace('promo.kadokawa.co.jp','example.com')
    with pytest.raises(ValueError):book_links(text,url,'作品甲')


@pytest.fixture
def promotion_bundle(setup_catalogue,publisher_edition_v2,tmp_path,request):
    cat,records=setup_catalogue
    parent=records[0];title=parent['title']
    volume=getattr(request,'param','3')
    cat,edition,book=publisher_edition_v2(title=title+' : 副書名',volume=volume,official_title=title+volume+' 副書名',
        imprint='電撃文庫',label='電撃文庫',label_id=330)
    raw=Path(parent['provenance']['raw_path']);payload=json.loads(raw.read_bytes())
    payload['entities']['Q1']['claims']['P856']=[{'mainsnak':{'snaktype':'value','datavalue':{'value':URL}}}]
    parent['provenance'].update(raw_sha256=write(raw,payload),conditions_sha256=edition['provenance']['conditions_sha256'])
    assert cat.ingest([parent,edition])['failed']==0
    publisher=tmp_path/'books.json'
    publisher_pin=write(publisher,{'count_raw':{'book':1},'result':{'book':[
        {'itemCode':'123456789012','title':book['title'],'isbn':book['isbn'],'label_id':330,
         'product_form_id':3,'disp_author_name':'<li>著者 著者</li>'}]}})
    books_manifest=tmp_path/'book-manifest.json'
    books_pin=write(books_manifest,{'schema_version':'kadokawa-publisher-books-v1','rights_scope':'reference_only_bibliography',
        'pages':[{'source_url':'https://www.kadokawa.co.jp/product/search/','final_url':'https://www.kadokawa.co.jp/product/search/',
            'http_status':200,'http_method':'POST','request_data':{'label':'330','releaseDate':'1','size':'20','pageno':'1','pageno_book':'1','pageno_media':'1'},
            'raw_path':publisher.name,'sha256':publisher_pin}]})
    confirmed=cat.confirm_publisher_editions('kadokawa',books_manifest,books_pin)
    assert confirmed['confirmed']==1,confirmed
    source=tmp_path/'promotion.html';source.write_text(page(title),encoding='utf-8')
    manifest=tmp_path/'promotion-manifest.json'
    value={'version':'light-novel-kadokawa-series-promotions-v1','series':[{
        'series_entry_id':'lns:wd:Q1','title':title,'official_series_url':URL,'receipt':{
            'status':'success','http_status':200,'artifact':{'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'size':len(source.read_bytes()),'source_url':URL},
            'response':{'url':URL,'status':200,'location':None,'content_type':'text/html; charset=utf-8'}}}]}
    return cat,parent,edition,manifest,write(manifest,value),source,value


def test_confirmed_book_and_exact_official_url_bind_volume_and_expire_on_source_change(promotion_bundle):
    cat,_,_,manifest,pin,source,_=promotion_bundle
    identity='lne:ndl:R100000002-I1'
    assert cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])['linked']==1
    row=cat.work_detail(identity)
    assert row['series_entry_id']=='lns:wd:Q1'
    assert row['series_membership_evidence']['volume_number']==3
    assert row['series_membership_evidence']['person_identity_asserted'] is False
    shown=public_work(row)
    assert shown['membership_source']['source_url']==URL
    assert shown['membership_source']['source_kind']=='publisher_official_series'
    assert 'proof_sha256' not in json.dumps(shown)
    source.write_text(source.read_text(encoding='utf-8')+'changed',encoding='utf-8')
    row=cat.work_detail(identity)
    assert 'series_entry_id' not in row and row['edition_bibliography_confirmed'] is True


@pytest.mark.parametrize('change',['missing_confirmation','forged_confirmation','wrong_official_url','deprecated_official_url'])
def test_unproved_or_stale_cross_source_binding_never_links(promotion_bundle,change):
    cat,parent,_,manifest,pin,_,_=promotion_bundle
    identity='lne:ndl:R100000002-I1'
    if change=='missing_confirmation':
        with cat._connect() as db:db.execute('DELETE FROM edition_book_confirmations')
    elif change=='forged_confirmation':
        with cat._connect() as db:
            row=db.execute('SELECT proof_json FROM edition_book_confirmations').fetchone()
            proof=json.loads(row[0]);proof['match']['title_match']['volume']='99'
            text=json.dumps(proof,ensure_ascii=False)
            db.execute('UPDATE edition_book_confirmations SET proof_json=?,proof_sha256=?',(text,hashlib.sha256(text.encode()).hexdigest()))
    else:
        raw=Path(parent['provenance']['raw_path']);payload=json.loads(raw.read_bytes())
        claim=payload['entities']['Q1']['claims']['P856'][0]
        if change=='wrong_official_url':claim['mainsnak']['datavalue']['value']=URL.replace('example/','other/')
        else:claim['rank']='deprecated'
        parent['provenance']['raw_sha256']=write(raw,payload)
        assert cat.ingest([parent])['failed']==0
    result=cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])
    assert result['linked']==0 and result['unresolved_editions']==1


def test_book_source_is_replayed_once_per_operation_and_expiry_removes_only_relation(promotion_bundle,tmp_path,monkeypatch):
    cat,_,_,manifest,pin,_,_=promotion_bundle
    identity='lne:ndl:R100000002-I1'
    assert cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])['linked']==1
    replay=cat._publisher_books;calls=[]
    def observe(*args):
        calls.append(args)
        return replay(*args)
    monkeypatch.setattr(cat,'_publisher_books',observe)
    assert cat.work_detail(identity)['series_entry_id']=='lns:wd:Q1'
    assert len(calls)==1
    (tmp_path/'books.json').write_text('{}',encoding='utf-8')
    row=cat.work_detail(identity)
    assert len(calls)==2
    assert 'series_entry_id' not in row and not row.get('edition_bibliography_confirmed')
    assert row['entry_kind']=='edition' and row['title']


@pytest.mark.parametrize('url',['https://example.com/series/','javascript:alert(1)'])
def test_public_membership_source_rejects_untrusted_urls(promotion_bundle,url):
    cat,_,_,_,_,_,_=promotion_bundle
    row=cat.work_detail('lne:ndl:R100000002-I1')
    row.update(relation_resolution='edition_series_membership_verified',series_membership_evidence={
        'rule_version':'kadokawa-official-promotion-edition-membership-v1','official_series_url':url})
    assert 'membership_source' not in public_work(row)


def test_read_confirmed_promotion_volume_excludes_its_parent_from_recommendations(promotion_bundle):
    cat,_,_,manifest,pin,_,_=promotion_bundle
    identity='lne:ndl:R100000002-I1'
    assert cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])['linked']==1
    genres=[{'qid':'Q100','label':'ファンタジー','scope':'work','source_url':'https://www.wikidata.org/wiki/Q100'}]
    cat.set_read_status(identity,'read')
    assert cat.recommend_preferences(reference_genres=genres,exclude_read=True)['items']==[]
    assert [r['entry_id'] for r in cat.recommend_preferences(reference_genres=genres,exclude_read=False)['items']]==['lns:wd:Q1']


def test_selective_promotion_manifest_cannot_hide_competing_series(promotion_bundle,tmp_path):
    from copy import deepcopy
    cat,parent,_,manifest,pin,_,_=promotion_bundle
    other=deepcopy(parent);payload=json.loads(Path(parent['provenance']['raw_path']).read_bytes())
    entity=payload['entities'].pop('Q1');entity['id']='Q9';payload['entities']['Q9']=entity
    raw=tmp_path/'competing-series.json'
    other['id']='Q9';other['provenance'].update(url='https://www.wikidata.org/wiki/Q9',raw_path=str(raw),raw_sha256=write(raw,payload))
    assert cat.ingest([other])['inserted']==1
    result=cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=['lne:ndl:R100000002-I1'])
    assert result['candidate_series']==2 and result['linked']==0


@pytest.mark.parametrize('promotion_bundle,expected',[('その3',1),('DX.3',0),('上',0)],indirect=['promotion_bundle'])
def test_explicit_japanese_volume_marker_is_supported_without_guessing_subseries_or_parts(promotion_bundle,expected):
    cat,_,_,manifest,pin,_,_=promotion_bundle
    result=cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked']==expected
    if expected:
        evidence=cat.work_detail('lne:ndl:R100000002-I1')['series_membership_evidence']
        assert evidence['volume_number']==3 and evidence['volume']['value']=='その3'
