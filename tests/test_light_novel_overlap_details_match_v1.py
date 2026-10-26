from copy import deepcopy
from hashlib import sha256
import json
from urllib.parse import quote

import pytest

from recommend_book.light_novel_overlap_details_match_v1 import match_overlap_details, reference_hold_reason

OWN='records[1]/recordData/dcndl:BibResource'
DCT='/{http://purl.org/dc/terms/}'
DC='/{http://purl.org/dc/elements/1.1/}'
DCNDL='/{http://ndl.go.jp/dcndl/terms/}'
BRANDS={'nvl':'オーバーラップノベルス','nvf':'オーバーラップノベルスf','nvr':'ロサージュノベルス'}


def reference(brand='nvl'):
    url=f'https://over-lap.co.jp/complete-title/product/0/9784824019318/?cat={brand.upper()}&swrd='
    fields=[{'field_key':'ISBN','label_literal':'ISBN','value_literal':'978-4-8240-1931-8',
        'locator':'(.sidebar-buy__spec-row)[1] .sidebar-buy__spec-label / .sidebar-buy__spec-value'}]
    return {'publisher_product_reference_id':f'overlap-{brand}-product:9784824019318',
        'source_unit':'publisher_product_detail_bibliographic_reference_not_confirmed_edition',
        'source_url':url,'title':'完全題３ 副題','owncard_title_literal':'完全題３ 副題',
        'isbn':'9784824019318','isbn_source_literal':'978-4-8240-1931-8',
        'isbn_source_basis':'explicit_ISBN_label_value_not_URL',
        'credits':[{'role':'unknown','name':'人物','role_literal':None,
            'role_basis':'unmarked_creator_name_retained_unknown_not_inferred_from_position_or_css'},
            {'role':'illustrator','name':'画家','role_literal':'イラスト','role_marker_literal':'イラスト／',
            'role_basis':'explicit_role_marker_in_own_creator_field'}],
        'creator_field_literal':'人物\u2003イラスト／画家','label':BRANDS[brand],
        'label_source_scope':'own_card_label_only_detail_own_label_not_observed','detail_label':None,
        'publisher':None,'form':'B6判','bibliographic_field_literals':fields,
        'source_evidence':[{'source_url':url,'raw_sha256':'a'*64,'locator':locator} for locator in (
            '.goods-explanation__body h1.goods-explanation__title',
            '.goods-explanation__body p.goods-explanation__author',fields[0]['locator'])],
        'owncard_source_evidence':{'source_url':f'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat={brand.upper()}',
            'raw_sha256':'c'*64,'selector':'.product-cards > .product-card[1] a / .product-card__title / .badge'},
        'edition_identity_asserted':False,'original_work_identity_asserted':False,'story_series_identity_asserted':False}


def parts(brand='nvl', *, authority=True):
    return {'source':{'source_id':'R100000002-I1','source_url':'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'record_kind':'edition','provider_id':'iss-ndl-opac-national','language':'jpn',
        'publisher':'オーバーラップ','series':BRANDS[brand],
        'identifiers':[{'source':'isbn','value':'9784824019318'}],'extra':{'ndc_codes':['913.6']},
        'contributors':[{'name':'人物','role':'author','identifiers':[
            {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/01234567'}] if authority else []}]},
        'source_raw_sha256':'b'*64,'titles':[{'value':'完全題 : 副題. 3','locator':OWN+DCT+'title[1]'}],
        'volume':{'value':'3','locator':OWN+DCNDL+'volume[2]'},'edition_statements':[],
        'publisher_agents':[{'name':'オーバーラップ','locator':OWN+DCT+'publisher[3]',
            'structured_agent':True,'description_values':[],'description_locators':[]}],
        'literal_authors':[{'name':'人物','literal':'人物 著','locator':OWN+DC+'creator[4]'}]}


@pytest.mark.parametrize('brand',BRANDS)
@pytest.mark.parametrize('authority',[True,False])
def test_same_isbn_complete_bibliography_does_not_infer_source_writer_or_identities(brand,authority):
    ref,own=reference(brand),parts(brand,authority=authority)
    before=deepcopy((ref,own))
    assert reference_hold_reason(ref) is None
    proof=match_overlap_details(ref,own,same_isbn_parts=[own])
    assert proof['scope']=='publisher_reference_to_ndl_bibliographic_record'
    assert proof['source_author_role_status']=='unknown' and proof['source_writer_role_asserted'] is False
    assert proof['official_credits']==ref['credits'] and proof['official_label']==BRANDS[brand]
    assert proof['official_label_scope']=='own_card_label_only_detail_own_label_not_observed'
    assert proof['official_publisher'] is None and proof['official_lightnovel_tag'] is None
    assert proof['light_novel_status']=='unknown' and proof['illustrator_confirmation']=='unassessed'
    binding=proof['unmarked_creator_own_writer_matches'][0]
    assert binding['source_unmarked_creator']=='人物' and 'official_writer' not in binding
    assert binding['authority_status']==('known' if authority else 'unknown')
    assert proof['title_match']['volume']=='3'
    assert proof['candidate_raw_sha256s']==['b'*64]
    for flag in ('author_identity_asserted','edition_identity_asserted','same_edition_identity_asserted','product_sku_identity_asserted',
        'original_work_identity_asserted','story_series_identity_asserted','global_author_identity_asserted'):
        assert proof[flag] is False
    assert 'authors' not in proof and 'source_receipt' not in proof
    assert (ref,own)==before


@pytest.mark.parametrize('change',['url_isbn_only','invalid_isbn','conflicting_isbn','missing_isbn_witness',
    'id','host','query_brand','source_brand','card_brand','card_sha','card_title','no_detail_sha',
    'source_author_role','unmarked_role_literal','illustrator_as_unknown','no_unknown_creator',
    'duplicate_unknown','source_publisher','detail_label','source_tag','edition_claim','conflict',
    'missing_subtitle','different_volume','missing_volume','sku_word'])
def test_source_contract_and_all_title_parts_are_required(change):
    ref=reference();own=parts()
    if change=='url_isbn_only':ref['isbn_source_basis']='URL'
    elif change=='invalid_isbn':ref['isbn']='9784824019319'
    elif change=='conflicting_isbn':ref['bibliographic_field_literals'].append({**ref['bibliographic_field_literals'][0],'value_literal':'978-4-8240-1936-3'})
    elif change=='missing_isbn_witness':ref['bibliographic_field_literals']=[]
    elif change=='id':ref['publisher_product_reference_id']+='x'
    elif change=='host':ref['source_url']=ref['source_url'].replace('over-lap.co.jp','example.com')
    elif change=='query_brand':ref['source_url']=ref['source_url'].replace('cat=NVL','cat=NVF')
    elif change=='source_brand':ref['label']='別文庫'
    elif change=='card_brand':ref['owncard_source_evidence']['source_url']=ref['owncard_source_evidence']['source_url'].replace('NVL','NVF')
    elif change=='card_sha':ref['owncard_source_evidence']['raw_sha256']=''
    elif change=='card_title':ref['owncard_title_literal']='別題３ 副題'
    elif change=='no_detail_sha':ref['source_evidence'][0]['raw_sha256']=''
    elif change=='source_author_role':ref['credits'][0]['role']='author'
    elif change=='unmarked_role_literal':ref['credits'][0]['role_literal']='著'
    elif change=='illustrator_as_unknown':ref['credits'][1]['role']='unknown'
    elif change=='no_unknown_creator':ref['credits']=ref['credits'][1:]
    elif change=='duplicate_unknown':ref['credits'].append(deepcopy(ref['credits'][0]))
    elif change=='source_publisher':ref['publisher']='オーバーラップ'
    elif change=='detail_label':ref['detail_label']=ref['label']
    elif change=='source_tag':ref['official_lightnovel_tag']=True
    elif change=='edition_claim':ref['edition_identity_asserted']=True
    elif change=='conflict':ref['metadata_conflict']=True
    elif change=='missing_subtitle':ref['title']=ref['owncard_title_literal']='完全題３'
    elif change=='different_volume':ref['title']=ref['owncard_title_literal']='完全題２ 副題'
    elif change=='missing_volume':ref['title']=ref['owncard_title_literal']='完全題 副題'
    else:ref['title']=ref['owncard_title_literal']='完全題３ 副題 特装版'
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


@pytest.mark.parametrize('change',['isbn','publisher','second_publisher','ndc','mixed_ndc','language','provider',
    'work','unknown_imprint','two_imprints','wrong_imprint','nested_title','nested_publisher','nested_volume',
    'title_namespace','unknown_writer','wrong_writer','illustrator','nested_writer','second_unknown'])
def test_own_isbn_publisher_classification_imprint_and_writer_gates(change):
    ref=reference();own=parts(authority=False)
    if change=='isbn':own['source']['identifiers'][0]['value']='9784824019363'
    elif change=='publisher':own['source']['publisher']=own['publisher_agents'][0]['name']='別社'
    elif change=='second_publisher':
        own['publisher_agents'].append({**deepcopy(own['publisher_agents'][0]),'name':'別社'})
        own['source']['publisher']+='; 別社'
    elif change=='ndc':own['source']['extra']['ndc_codes']=['726.1']
    elif change=='mixed_ndc':own['source']['extra']['ndc_codes'].append('726.1')
    elif change=='language':own['source']['language']='eng'
    elif change=='provider':own['source']['provider_id']='external'
    elif change=='work':own['source']['record_kind']='work'
    elif change=='unknown_imprint':own['source']['series']=None
    elif change=='two_imprints':own['source']['series']+=' = ロサージュノベルス'
    elif change=='wrong_imprint':own['source']['series']='オーバーラップノベルスf'
    elif change=='nested_title':own['titles'][0]['locator']=OWN+'/contents[1]'+DCT+'title[1]'
    elif change=='nested_publisher':own['publisher_agents'][0]['locator']=OWN+'/contents[1]'+DCT+'publisher[3]'
    elif change=='nested_volume':own['volume']['locator']=OWN+'/contents[1]'+DCNDL+'volume[2]'
    elif change=='title_namespace':own['titles'][0]['locator']=OWN+'/{https://example.com/}title[1]'
    elif change=='unknown_writer':own['literal_authors']=[]
    elif change=='wrong_writer':own['source']['contributors'][0]['name']='別人'
    elif change=='illustrator':own['source']['contributors'][0]['role']='illustrator'
    elif change=='nested_writer':own['literal_authors'][0]['locator']=OWN+'/contents[1]'+DC+'creator[4]'
    else:ref['credits'].insert(1,{**ref['credits'][0],'name':'別の人物'})
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


def second_record(own):
    other=deepcopy(own)
    other['source'].update(source_id='R100000002-I2',source_url='https://ndlsearch.ndl.go.jp/books/R100000002-I2')
    other['source_raw_sha256']='d'*64
    return other


def test_all_compatible_same_isbn_records_are_bound_and_order_is_stable():
    own=parts();other=second_record(own)
    proof=match_overlap_details(reference(),own,same_isbn_parts=[other,own])
    assert proof['candidate_raw_sha256s']==['b'*64,'d'*64]
    assert [r['source_id'] for r in proof['same_isbn_candidates']]==['R100000002-I1','R100000002-I2']
    assert proof==match_overlap_details(reference(),own,same_isbn_parts=[own,other])


@pytest.mark.parametrize('change',['title','volume','edition','publisher','language','ndc','imprint','authority','duplicate_id'])
def test_any_contradictory_same_isbn_record_holds_the_entire_correspondence(change):
    own=parts();other=second_record(own)
    if change=='title':other['titles'][0]['value']='別題 : 副題. 3'
    elif change=='volume':other['volume']['value']='2'
    elif change=='edition':other['edition_statements']=[{'value':'改訂版','locator':OWN+DCNDL+'edition[5]'}]
    elif change=='publisher':other['source']['publisher']=other['publisher_agents'][0]['name']='別社'
    elif change=='language':other['source']['language']='eng'
    elif change=='ndc':other['source']['extra']['ndc_codes']=['913']
    elif change=='imprint':other['source']['series']='ロサージュノベルス'
    elif change=='authority':other['source']['contributors'][0]['identifiers'][0]['value']='http://id.ndl.go.jp/auth/entity/09999999'
    else:other['source']['source_id']=own['source']['source_id'];other['source']['source_url']=own['source']['source_url']
    assert match_overlap_details(reference(),own,same_isbn_parts=[own,other]) is None


@pytest.mark.parametrize('candidates',[[],None])
def test_explicit_complete_same_isbn_inventory_is_required(candidates):
    assert match_overlap_details(reference(),parts(),same_isbn_parts=candidates) is None


def test_primary_must_be_in_inventory_and_matching_performs_no_io(monkeypatch):
    own=parts();other=second_record(own)
    assert match_overlap_details(reference(),own,same_isbn_parts=[other]) is None
    monkeypatch.setattr('builtins.open',lambda *a,**k:pytest.fail('unexpected I/O'))
    assert match_overlap_details(reference(),own,same_isbn_parts=[own]) is not None


def test_formal_source_id_alias_must_equal_the_original_product_reference_id():
    ref=reference();own=parts()
    ref['publisher_reference_id']=ref['publisher_product_reference_id']
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is not None
    ref['publisher_reference_id']='overlap-nvf-product:9784824019318'
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


def test_source_unknown_is_not_promoted_by_a_non_national_saved_provider():
    own=parts()
    own['source']['provider_id']='iss-ndl-opac'
    assert match_overlap_details(reference(),own,same_isbn_parts=[own]) is None


def test_every_unmarked_creator_can_correspond_to_own_writers_without_source_role_inference():
    ref=reference();own=parts()
    ref['credits'].insert(1,{**ref['credits'][0],'name':'別の人物'})
    ref['creator_field_literal']='人物 別の人物 イラスト／画家'
    own['source']['contributors'].append({'name':'別の人物','role':'author','identifiers':[
        {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/02222222'}]})
    proof=match_overlap_details(ref,own,same_isbn_parts=[own])
    assert [r['source_unmarked_creator'] for r in proof['unmarked_creator_own_writer_matches']]==['人物','別の人物']
    assert all(r['source_writer_role_asserted'] is False for r in proof['unmarked_creator_own_writer_matches'])


@pytest.mark.parametrize('role',['発売','頒布'])
def test_additional_explicit_sales_or_distribution_agent_preserves_sole_publisher(role):
    own=parts()
    locator=OWN+DCT+'publisher[6]'
    own['publisher_agents'].append({'name':'販売社','locator':locator,'structured_agent':True,
        'description_values':[role],'description_locators':[locator+'/{http://xmlns.com/foaf/0.1/}Agent[1]'+DCT+'description[1]']})
    own['source']['publisher']+='; 販売社'
    proof=match_overlap_details(reference(),own,same_isbn_parts=[own])
    assert proof['publisher_match']['publisher_agent']['name']=='オーバーラップ'
    assert proof['publisher_match']['distribution_agents'][0]['role']==role
    own['publisher_agents'][-1]['description_values']=['不明']
    assert match_overlap_details(reference(),own,same_isbn_parts=[own]) is None


def test_nested_edition_statement_is_not_an_own_primary_witness():
    own=parts()
    own['edition_statements']=[{'value':'第2版','locator':OWN+'/contents[1]'+DCNDL+'edition[5]'}]
    assert match_overlap_details(reference(),own,same_isbn_parts=[own]) is None


def card_page_url(brand, page):
    return ('https://over-lap.co.jp/'+quote(BRANDS[brand])+'/category/0/'+brand.upper()
        +'/?pid=&vid=&swrd=&img=1&sort=11&cicon=&min=&max=&udns=0&dosp='
        +'&sellfrom_y=&sellfrom_m=&sellto_y=&sellto_m=&hasq=&pno='+str(page))


@pytest.mark.parametrize('brand,page',[('nvl',2),('nvl',15),('nvf',2),('nvf',7)])
def test_authenticated_later_and_terminal_card_pages_preserve_their_source_url(brand,page):
    ref=reference(brand);own=parts(brand)
    ref['owncard_source_evidence']['source_url']=card_page_url(brand,page)
    assert reference_hold_reason(ref) is None
    proof=match_overlap_details(ref,own,same_isbn_parts=[own])
    assert proof['owncard_source_evidence']==ref['owncard_source_evidence']
    assert proof['source_author_role_status']=='unknown' and proof['edition_identity_asserted'] is False


@pytest.mark.parametrize('brand,page',[('nvl',0),('nvl',1),('nvl',16),('nvf',8),('nvr',2)])
def test_pagination_requires_exact_start_for_page_one_and_brand_specific_terminal_bound(brand,page):
    ref=reference(brand);own=parts(brand)
    ref['owncard_source_evidence']['source_url']=card_page_url(brand,page)
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


@pytest.mark.parametrize('change',['wrong_brand','query_changed','query_missing','query_extra',
    'duplicate_pno','duplicate_query','leading_zero','negative','noninteger','missing_pno',
    'foreign_host','http','fragment','wrong_route','empty_slug','start_query','path_parameters'])
def test_paginated_card_url_keeps_the_exact_verified_filter_and_brand_route(change):
    ref=reference();own=parts();url=card_page_url('nvl',2)
    if change=='wrong_brand':url=url.replace('/NVL/','/NVF/')
    elif change=='query_changed':url=url.replace('sort=11','sort=12')
    elif change=='query_missing':url=url.replace('pid=&','')
    elif change=='query_extra':url+='&unverified=1'
    elif change=='duplicate_pno':url+='&pno=2'
    elif change=='duplicate_query':url+='&pid='
    elif change=='leading_zero':url=url.replace('pno=2','pno=02')
    elif change=='negative':url=url.replace('pno=2','pno=-2')
    elif change=='noninteger':url=url.replace('pno=2','pno=2.0')
    elif change=='missing_pno':url=url.replace('&pno=2','')
    elif change=='foreign_host':url=url.replace('over-lap.co.jp','example.com')
    elif change=='http':url=url.replace('https:','http:')
    elif change=='fragment':url+='#part'
    elif change=='wrong_route':url=url.replace('/category/0/','/category/1/')
    elif change=='empty_slug':url=url.replace(quote(BRANDS['nvl']), '')
    elif change=='start_query':url=reference()['owncard_source_evidence']['source_url']+'&pno=2'
    else:url=url.replace('/NVL/?','/NVL/;extra?')
    ref['owncard_source_evidence']['source_url']=url
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


@pytest.mark.parametrize('brand,digest',[
    ('nvl','929097c8e43dcb89ac98cc8c5a4f579583d8f3997694032c1a13d493503e40ef'),
    ('nvf','0d3574e2a69083b14b1e78d3da6c1743a78dddbab2c33786d2eae3fee2493208'),
    ('nvr','a9cd7b380b04cb75231f213b1dc4e4d3c51aead464d8b634819afe7cdb14780c'),
])
def test_existing_first_page_complete_proof_is_unchanged(brand,digest):
    own=parts(brand)
    proof=match_overlap_details(reference(brand),own,same_isbn_parts=[own])
    assert sha256(json.dumps(proof,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==digest


def paired_imprint_case(brand='nvl'):
    # Synthetic evidence carrying the authenticated example's actual structure.
    ref=reference(brand);own=parts(brand)
    old_isbn,new_isbn='9784824019318','9784865540512'
    ref['publisher_product_reference_id']=ref['publisher_product_reference_id'].replace(old_isbn,new_isbn)
    ref['source_url']=ref['source_url'].replace(old_isbn,new_isbn)
    for evidence in ref['source_evidence']:
        evidence['source_url']=ref['source_url']
    ref.update(title='蘇りの魔王１',owncard_title_literal='蘇りの魔王１',isbn=new_isbn,
        isbn_source_literal='978-4-86554-051-2',creator_field_literal='丘野 優 イラスト／shri')
    ref['bibliographic_field_literals'][0]['value_literal']=ref['isbn_source_literal']
    ref['credits'][0]['name']='丘野 優';ref['credits'][1]['name']='shri'
    own['source'].update(source_id='R100000002-I026372995',
        source_url='https://ndlsearch.ndl.go.jp/books/R100000002-I026372995',series='OVERLAP NOVELS')
    own['source']['identifiers'][0]['value']=new_isbn
    own['source']['contributors'][0].update(name='丘野, 優',identifiers=[
        {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/001163655'}])
    own['titles']=[{'value':'蘇りの魔王 = Revived the Devil. 1','locator':OWN+DCT+'title[8]'},
        {'value':'蘇りの魔王','locator':OWN+DC+'title[9]'}]
    own['volume']['value']='1'
    own['literal_authors']=[{'name':'丘野優','literal':'丘野優 著','locator':OWN+DC+'creator[14]'}]
    return ref,own


def test_paired_imprint_is_bound_to_same_isbn_own_record_without_global_alias_or_title_parser_change():
    ref,own=paired_imprint_case()
    proof=match_overlap_details(ref,own,same_isbn_parts=[own])
    assert proof['rule_version']=='overlap-detail-ndl-bibliographic-correspondence-v2'
    pair=proof['imprint_match']
    assert pair['scope']=='same_isbn_authenticated_own_record_only'
    assert pair['official_card_brand']=='オーバーラップノベルス' and pair['source_value']=='OVERLAP NOVELS'
    assert pair['isbn13']==ref['isbn'] and pair['source_id']==own['source']['source_id']
    assert pair['source_raw_sha256']==own['source_raw_sha256'] and pair['own_primary_resource_locator']==OWN
    assert pair['global_imprint_alias_asserted'] is False and pair['detail_individual_imprint_asserted'] is False
    assert proof['title_match']['fallback_kind']=='explicit_parallel_title'
    assert proof['title_match']['source_value']=='蘇りの魔王 = Revived the Devil. 1'
    assert proof['title_match']['volume']=='1'
    assert proof['source_author_role_status']=='unknown' and proof['edition_identity_asserted'] is False


@pytest.mark.parametrize('brand',['nvf','nvr'])
def test_paired_imprint_does_not_create_unobserved_other_brand_english_pairs(brand):
    ref,own=paired_imprint_case(brand)
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


@pytest.mark.parametrize('imprint',['OVERLAP NOVEL','OVERLAP NOVELS = 別文庫','OVERLAP NOVELS ; 1'])
def test_paired_imprint_requires_the_complete_observed_literal_without_annotation_or_approximation(imprint):
    ref,own=paired_imprint_case();own['source']['series']=imprint
    assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None


def test_paired_imprint_never_bypasses_existing_isbn_full_title_volume_writer_or_publisher_gates():
    for change in ('isbn','title','volume','writer','publisher','language','ndc'):
        ref,own=paired_imprint_case()
        if change=='isbn':own['source']['identifiers'][0]['value']='9784824019318'
        elif change=='title':ref['title']=ref['owncard_title_literal']='蘇りの１'
        elif change=='volume':own['volume']['value']='2'
        elif change=='writer':own['source']['contributors'][0]['name']='別人';own['literal_authors']=[]
        elif change=='publisher':own['source']['publisher']=own['publisher_agents'][0]['name']='別社'
        elif change=='language':own['source']['language']='eng'
        else:own['source']['extra']['ndc_codes']=['726.1']
        assert match_overlap_details(ref,own,same_isbn_parts=[own]) is None,change


def test_paired_imprint_still_holds_a_contradictory_second_same_isbn_record():
    ref,own=paired_imprint_case();other=second_record(own)
    other['source']['series']='OVERLAP NOVELS ; 別文庫'
    assert match_overlap_details(ref,own,same_isbn_parts=[own,other]) is None
