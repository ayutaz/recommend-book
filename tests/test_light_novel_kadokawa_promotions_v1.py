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
    config=volume if isinstance(volume,dict) else None
    if config:
        volume=config.get('volume','2')
        if config.get('canonical_digit'):
            title+=' 2';parent['title']=title
        complete=title+('2' if config.get('adjacent') else ' 2')
        if config.get('canonical_digit') or config.get('legacy'):complete=title
        extra='<d:title>矛盾する別作品 2</d:title>' if config.get('contradiction') else ''
        cat,edition,book=publisher_edition_v2(title=title,volume=volume,dct_title=complete,
            official_title=complete,imprint='ファンタジア文庫' if config.get('legacy') else '富士見ファンタジア文庫',label='ファンタジア文庫',label_id=344,extra=extra)
    else:
        cat,edition,book=publisher_edition_v2(title=title+' : 副書名',volume=volume,official_title=title+volume+' 副書名',
            imprint='電撃文庫',label='電撃文庫',label_id=330)
    raw=Path(parent['provenance']['raw_path']);payload=json.loads(raw.read_bytes())
    payload['entities']['Q1']['claims']['P856']=[{'mainsnak':{'snaktype':'value','datavalue':{'value':URL}}}]
    if config and config.get('canonical_digit'):
        entity=payload['entities']['Q1'];entity['labels']['ja']['value']=title
        parent['aliases']=list(dict.fromkeys([v['value'] for v in entity['labels'].values()]+
            [v['value'] for group in entity.get('aliases',{}).values() for v in group]))
    parent['provenance'].update(raw_sha256=write(raw,payload),conditions_sha256=edition['provenance']['conditions_sha256'])
    imported=cat.ingest([parent,edition])
    assert imported['failed']==0,imported
    publisher=tmp_path/'books.json'
    publisher_pin=write(publisher,{**({'product_form': {'3': {'name': '文庫'}}} if config and config.get('legacy') else {}), 'count_raw':{'book':1},'result':{'book':[
        {'itemCode':'123456789012','title':book['title'],'isbn':book['isbn'],'label_id':book['official_label_id'],
         'product_form_id':3,'disp_author_name':'<li>著者 著者</li>'}]}})
    books_manifest=tmp_path/'book-manifest.json'
    books_pin=write(books_manifest,{'schema_version':'kadokawa-publisher-books-v1','rights_scope':'reference_only_bibliography',
        'pages':[{'source_url':'https://www.kadokawa.co.jp/product/search/','final_url':'https://www.kadokawa.co.jp/product/search/',
            'http_status':200,'http_method':'POST','request_data':{'label':str(book['official_label_id']),'releaseDate':'1','size':'20','pageno':'1','pageno_book':'1','pageno_media':'1'},
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


@pytest.fixture
def relocated_bundle(promotion_bundle,tmp_path):
    cat,parent,edition,manifest,_,source,value=promotion_bundle
    old='https://dengekibunko.jp/special/example/'
    raw=Path(parent['provenance']['raw_path']);payload=json.loads(raw.read_bytes())
    payload['entities']['Q1']['claims']['P856'][0]['mainsnak']['datavalue']['value']=old
    parent['provenance']['raw_sha256']=write(raw,payload)
    assert cat.ingest([parent])['failed']==0
    redirect_raw=tmp_path/'redirect.bin';redirect_raw.write_bytes(b'')
    receipt_path=tmp_path/'redirect.json'
    receipt={'requested_url':old,'final_url':old,'status':'failed','http_status':301,
        'attempts':1,'automatic_redirects':False,
        'artifact':{'path':str(redirect_raw),'sha256':hashlib.sha256(b'').hexdigest(),'size':0,'source_url':old},
        'response':{'url':old,'status':301,'headers':{'location':URL}}}
    value['version']='light-novel-kadokawa-series-promotions-v2'
    value['series'][0].update(wikidata_source_url=old,redirect_receipt={
        'path':str(receipt_path),'sha256':write(receipt_path,receipt)})
    return cat,parent,manifest,source,value,receipt_path,receipt,redirect_raw


def test_direct_relocation_uses_exact_old_p856_and_public_final_source(relocated_bundle):
    cat,_,manifest,_,value,_,_,redirect_raw=relocated_bundle
    identity='lne:ndl:R100000002-I1'
    assert cat.resolve_kadokawa_promotion_memberships(manifest,write(manifest,value),edition_ids=[identity])['linked']==1
    row=cat.work_detail(identity);proof=row['series_membership_evidence']
    assert proof['rule_version']=='kadokawa-official-promotion-edition-membership-v2'
    assert proof['wikidata_source_url']=='https://dengekibunko.jp/special/example/'
    assert proof['official_series_url']==URL and proof['redirect_evidence']['raw_sha256']==hashlib.sha256(b'').hexdigest()
    assert public_work(row)['membership_source']['source_url']==URL
    unknown=dict(row);unknown['series_membership_evidence']={**proof,'rule_version':'unrecognized-promotion-v99'}
    assert 'membership_source' not in public_work(unknown)
    redirect_raw.write_bytes(b'changed')
    row=cat.work_detail(identity)
    assert 'series_entry_id' not in row and row['edition_bibliography_confirmed'] is True


@pytest.mark.parametrize('change',['location','old_source','generic_label','external','relative','second_hop',
    'raw_hash','receipt_hash','source_outside','retry','auto_redirect','port','credentials','query','fragment'])
def test_forged_or_out_of_scope_relocation_never_verifies(relocated_bundle,tmp_path,change):
    from recommend_book.light_novel_kadokawa_promotions_v1 import verify_promotions
    _,_,manifest,_,value,receipt_path,receipt,redirect_raw=relocated_bundle
    entry=value['series'][0]
    if change=='location':receipt['response']['headers']['location']=URL.replace('example/','other/')
    elif change=='old_source':receipt['artifact']['source_url']=URL
    elif change=='generic_label':receipt['response']['headers']['location']='https://www.kadokawa.co.jp/label/dengekibunko/'
    elif change=='external':receipt['response']['headers']['location']='https://example.com/special/example/'
    elif change=='relative':receipt['response']['headers']['location']='/dengekibunko/special/example/'
    elif change=='second_hop':receipt['final_url']=URL
    elif change=='raw_hash':redirect_raw.write_bytes(b'changed')
    elif change=='source_outside':
        outside=tmp_path.parent/'outside-redirect.bin';outside.write_bytes(b'');receipt['artifact']['path']=str(outside)
    elif change=='retry':receipt['attempts']=2
    elif change=='auto_redirect':receipt['automatic_redirects']=True
    elif change=='port':entry['wikidata_source_url']='https://dengekibunko.jp:443/special/example/'
    elif change=='credentials':entry['wikidata_source_url']='https://user@dengekibunko.jp/special/example/'
    elif change=='query':entry['wikidata_source_url']+='?x=1'
    elif change=='fragment':entry['wikidata_source_url']+='#book'
    entry['redirect_receipt']['sha256']=write(receipt_path,receipt)
    if change=='receipt_hash':entry['redirect_receipt']['sha256']='0'*64
    with pytest.raises(ValueError):verify_promotions(manifest,write(manifest,value))


@pytest.mark.parametrize('change',['wrong_p856','deprecated_p856','final_p856_only','final_source_expiry'])
def test_relocated_promotion_retains_raw_p856_and_final_source_gates(relocated_bundle,change):
    cat,parent,manifest,source,value,_,_,_=relocated_bundle
    if change=='final_source_expiry':
        source.write_text('changed',encoding='utf8')
        with pytest.raises(ValueError):cat.resolve_kadokawa_promotion_memberships(manifest,write(manifest,value),edition_ids=['lne:ndl:R100000002-I1'])
        return
    raw=Path(parent['provenance']['raw_path']);payload=json.loads(raw.read_bytes());claim=payload['entities']['Q1']['claims']['P856'][0]
    if change=='deprecated_p856':claim['rank']='deprecated'
    else:claim['mainsnak']['datavalue']['value']=URL if change=='final_p856_only' else 'https://dengekibunko.jp/special/other/'
    parent['provenance']['raw_sha256']=write(raw,payload);assert cat.ingest([parent])['failed']==0
    assert cat.resolve_kadokawa_promotion_memberships(manifest,write(manifest,value),edition_ids=['lne:ndl:R100000002-I1'])['linked']==0


def test_v1_assertion_and_proof_fields_remain_unchanged(promotion_bundle):
    from recommend_book.light_novel_kadokawa_promotions_v1 import verify_promotions
    cat,_,_,manifest,pin,source,_=promotion_bundle
    assertion=verify_promotions(manifest,pin)['lns:wd:Q1']
    assert set(assertion)=={'series_entry_id','title','source_url','raw_sha256','books','prose_ai_input_allowed'}
    assert cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=['lne:ndl:R100000002-I1'])['linked']==1
    proof=cat.work_detail('lne:ndl:R100000002-I1')['series_membership_evidence']
    assert proof['rule_version']=='kadokawa-official-promotion-edition-membership-v1'
    assert 'wikidata_source_url' not in proof and 'redirect_evidence' not in proof


@pytest.mark.parametrize('promotion_bundle,expected',[
    ({'volume':'2'},1),
    ({'volume':'3'},0),
    ({'volume':'2','canonical_digit':True},0),
    ({'volume':'2','adjacent':True},0),
    ({'volume':'2','contradiction':True},0),
],indirect=['promotion_bundle'])
def test_exact_space_integer_volume_requires_all_own_main_titles(promotion_bundle,expected):
    cat,_,_,manifest,pin,_,_=promotion_bundle
    identity='lne:ndl:R100000002-I1'
    assert cat.work_detail(identity)['edition_bibliography_confirmed'] is True
    result=cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])
    assert result['linked']==expected
    if expected:
        proof=cat.work_detail(identity)['series_membership_evidence']
        assert proof['volume_number']==2 and proof['volume']['value']=='2'
        assert proof['main_titles'][0]['raw_value'].endswith(' 2')
        assert proof['main_titles'][0]['main']==cat.work_detail('lns:wd:Q1')['title']


@pytest.mark.parametrize('promotion_bundle',[{'volume':'2'}],indirect=['promotion_bundle'])
def test_space_integer_relation_replays_after_original_source_restored(promotion_bundle):
    cat,_,_,manifest,pin,source,_=promotion_bundle
    identity='lne:ndl:R100000002-I1';original=source.read_bytes()
    assert cat.resolve_kadokawa_promotion_memberships(manifest,pin,edition_ids=[identity])['linked']==1
    before=cat.work_detail(identity)['series_membership_evidence']
    source.write_bytes(b'changed')
    assert 'series_entry_id' not in cat.work_detail(identity)
    source.write_bytes(original)
    assert cat.work_detail(identity)['series_membership_evidence']==before


@pytest.mark.parametrize('promotion_bundle', [{'volume': '2', 'legacy': True}], indirect=True)
def test_legacy_v1_explicit_paper_witness_preserves_binding_and_expires(promotion_bundle):
    cat, _, _, manifest, pin, source, _ = promotion_bundle
    identity = 'lne:ndl:R100000002-I1'
    with cat._connect() as db:
        before = tuple(db.execute('SELECT * FROM edition_book_confirmations WHERE edition_id=?', (identity,)).fetchone())
    legacy = json.loads(before[2])
    assert legacy['match']['rule_version'] == 'publisher-ndl-edition-confirmation-v1'
    assert 'official_product_form_id' not in legacy['match']
    result = cat.resolve_kadokawa_promotion_memberships(manifest, pin, edition_ids=[identity])
    assert result['linked'] == 1
    row = cat.work_detail(identity)
    proof = row['series_membership_evidence']
    assert proof['rule_version'] == 'kadokawa-official-promotion-edition-membership-v3'
    witness = proof['legacy_paper_form_witness']
    assert witness['product_form_id'] == 3 and witness['product_form_id_locator'] == '/result/book/0/product_form_id'
    assert witness['product_form_name_locator'] == '/product_form/3/name'
    assert witness['publisher_confirmation_proof_sha256'] == before[3]
    assert row['edition_confirmation_evidence'] == legacy['match']
    assert public_work(row)['membership_source']['source_url'] == URL
    with cat._connect() as db:
        assert tuple(db.execute('SELECT * FROM edition_book_confirmations WHERE edition_id=?', (identity,)).fetchone()) == before
    Path(witness['raw_path']).write_text('{}', encoding='utf8')
    assert 'series_entry_id' not in cat.work_detail(identity)


@pytest.mark.parametrize('promotion_bundle', [{'volume': '2', 'legacy': True}], indirect=True)
@pytest.mark.parametrize('tamper', ['missing_form', 'bool_form', 'non3', 'wrong_name', 'wrong_book', 'wrong_isbn', 'hash', 'receipt', 'manifest_hash', 'binding_hash', 'explicit_non3', 'redirect', 'receipt_size'])
def test_legacy_paper_witness_fails_closed_without_rewriting_binding(promotion_bundle, tamper):
    cat, _, _, manifest, pin, _, _ = promotion_bundle
    identity = 'lne:ndl:R100000002-I1'
    with cat._connect() as db:
        binding = dict(db.execute('SELECT * FROM edition_book_confirmations WHERE edition_id=?', (identity,)).fetchone())
    confirmation = json.loads(binding['proof_json'])
    cache = {}
    with cat._connect() as db:
        stored = dict(db.execute('SELECT * FROM entries WHERE entry_id=?', (identity,)).fetchone())
    projected = cat._project(json.loads(stored['record_json']), cache)
    cat._attach_book_confirmations([projected], [stored], cache)
    assert projected['edition_bibliography_confirmed']
    witness = cat._legacy_kadokawa_paper_form_witness(binding, confirmation, cache)
    assert witness is not None
    # A new operation must authenticate source facts afresh.
    cache = {('book_confirmation_documents',): cache[('book_confirmation_documents',)]}
    path = Path(confirmation['manifest_path']); value = json.loads(path.read_bytes()); receipt = value['pages'][0]
    raw = path.parent / receipt['raw_path']; payload = json.loads(raw.read_bytes()); item = payload['result']['book'][0]
    if tamper == 'missing_form': item.pop('product_form_id')
    elif tamper == 'bool_form': item['product_form_id'] = True
    elif tamper == 'non3': item['product_form_id'] = 13
    elif tamper == 'wrong_name': payload['product_form']['3']['name'] = 'コミック'
    elif tamper == 'wrong_book': item['itemCode'] = '111111111111'
    elif tamper == 'wrong_isbn': item['isbn'] = '9784049142143'
    elif tamper == 'hash': raw.write_text('{}', encoding='utf8')
    elif tamper == 'receipt': receipt['http_status'] = 503
    elif tamper == 'redirect': receipt['location'] = '/other'
    elif tamper == 'receipt_size': receipt['byte_length'] = 1
    elif tamper == 'manifest_hash': confirmation['manifest_sha256'] = '0' * 64
    elif tamper == 'binding_hash': binding['proof_sha256'] = '0' * 64
    elif tamper == 'explicit_non3': confirmation['match']['official_product_form_id'] = 13
    if tamper in {'missing_form', 'bool_form', 'non3', 'wrong_name', 'wrong_book', 'wrong_isbn'}:
        receipt['sha256'] = write(raw, payload)
    if tamper not in {'hash', 'binding_hash', 'explicit_non3', 'manifest_hash'}:
        confirmation['manifest_sha256'] = write(path, value)
    if tamper not in {'binding_hash'}:
        binding['proof_json'] = json.dumps(confirmation, ensure_ascii=False, sort_keys=True)
        binding['proof_sha256'] = hashlib.sha256(binding['proof_json'].encode()).hexdigest()
    if tamper in {'missing_form', 'bool_form', 'non3', 'wrong_name', 'wrong_book', 'wrong_isbn', 'redirect', 'receipt_size'}:
        replayed = cat._publisher_books('kadokawa', path, confirmation['manifest_sha256'])
        cache = {('book_confirmation_documents',): {('kadokawa', confirmation['manifest_path'], confirmation['manifest_sha256']):
            {book['publisher_book_id']: book for book in replayed}}}
    assert cat._legacy_kadokawa_paper_form_witness(binding, confirmation, cache) is None
