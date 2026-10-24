from copy import deepcopy

import pytest

from recommend_book.light_novel_catalogue_reference_match_v1 import (
    match_catalogue_reference, reference_hold_reason,
)

OWN = 'records[1]/recordData/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}'
DC = '/{http://purl.org/dc/elements/1.1/}'


def reference(provider='gc', *, label='アルファポリス'):
    result = {'publisher_reference_id':'gc-product:123','source_unit':'publisher_product_reference',
        'source_url':'https://gcnovels.jp/book/123','title':'完全題３ 副題',
        'credits':[{'role':'author','role_literal':'小説','name':'著者'},
            {'role':'illustrator','role_literal':'イラスト','name':'絵担当'}],
        'isbn':None,'publisher':None,'form':None,'volume':None,'label':None,'label_literals':[],
        'official_lightnovel_tag':None,'story_series_asserted':False,
        'metadata_conflict':False,'bibliographic_variants':[],
        'source_evidence':[{'source_url':'https://gcnovels.jp/search?amount=120',
            'raw_sha256':'a'*64,'locator':'ul.book-list > li[1] h3/a; ul.author/li'}],
        'source_frame_scope':{'index_pages_complete':False,'pages_verified':5,'publisher_reported_total':627,
            'references_verified':600,'unacquired_references':27,'unacquired_http_status':500}}
    if provider == 'alphapolis':
        brand = 'regina' if label == 'レジーナブックス' else 'alphapolis'
        entry = 'https://www.alphapolis.co.jp/book/' + brand + ('/tankou' if brand=='regina' else '/tankou/100')
        result.update(publisher_reference_id='alphapolis-commercial-book:100/200',
            source_url='https://www.alphapolis.co.jp/book/detail/100/200',
            label=label,label_literals=[label],credits=[{'role':'author','role_display':'著','name':'著者'}],
            official_novel_category=brand=='alphapolis',source_scope_membership={
                'source_unit':'publisher_commercial_book_index_membership','brand':brand,'entry_url':entry,
                'index_scope':'regina_tankou' if brand=='regina' else 'alphapolis_novel_tankou_category_100',
                'membership_basis':'explicit_book_card_and_own_label_tag_in_fixed_index',
                'label_context_literal':label,'individual_lightnovel_classification':'unknown',
                'full_historical_catalogue_complete':False})
        result.pop('source_frame_scope')
        result['source_evidence'][0]['source_url']=entry
    return result


def parts(provider='gc', *, imprint='GC NOVELS', authority=True):
    publisher='マイクロマガジン社' if provider=='gc' else 'アルファポリス'
    return {'source':{'source_id':'R100000002-I1','source_url':'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'record_kind':'edition','provider_id':'iss-ndl-opac-national','language':'jpn',
        'publisher':publisher,'series':imprint,'extra':{'ndc_codes':['913.6']},
        'contributors':[{'name':'著者','role':'author','identifiers':[
            {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/01234567'}] if authority else []}]},
        'source_raw_sha256':'b'*64,'titles':[{'value':'完全題 : 副題. 3','locator':OWN+DCT+'title[1]'}],
        'volume':{'value':'3','locator':OWN+'/{http://ndl.go.jp/dcndl/terms/}volume[2]'},
        'publisher_agents':[{'name':publisher,'locator':OWN+DCT+'publisher[3]',
            'structured_agent':True,'description_values':[],'description_locators':[]}],
        'literal_authors':[{'name':'著者','literal':'著者 著','locator':OWN+DC+'creator[4]'}]}


@pytest.mark.parametrize('provider,label,imprint',[
    ('gc',None,'GC NOVELS'),('gc',None,'GCNOVELS ; 123'),('gc',None,'GCN文庫 ; あ-01-01'),
    ('alphapolis','アルファポリス',None),('alphapolis','アルファポリス','アルファポリス'),
    ('alphapolis','レジーナブックス',None),('alphapolis','レジーナブックス','レジーナブックス ; 1'),
])
def test_correspondence_preserves_unknown_isbn_brand_classification_and_all_identity_boundaries(provider,label,imprint):
    ref,own=reference(provider,label=label),parts(provider,imprint=imprint)
    before=deepcopy((ref,own))
    assert reference_hold_reason(provider,ref) is None
    proof=match_catalogue_reference(provider,ref,own)
    assert proof['scope']=='publisher_reference_to_ndl_bibliographic_record'
    assert proof['light_novel_status']=='unknown' and proof['official_lightnovel_tag'] is None
    assert proof['official_isbn'] is None and proof['official_publisher'] is None
    assert proof['official_individual_imprint_asserted'] is False
    assert proof['own_imprint']==imprint
    assert proof['official_brand_context_literal']==label
    for field in ('same_edition_identity_asserted','product_sku_identity_asserted',
        'original_work_identity_asserted','story_series_identity_asserted','global_author_alias_asserted'):
        assert proof[field] is False
    assert proof['title_match']['volume']=='3'
    assert 'isbn13' not in proof
    assert (ref,own)==before


def test_partial_gc_frame_is_retained_without_becoming_a_complete_or_brand_specific_source():
    proof=match_catalogue_reference('gc',reference(),parts())
    assert proof['official_label'] is None
    assert proof['source_frame_scope']==reference()['source_frame_scope']
    assert proof['imprint_match']['source_individual_brand_asserted'] is False
    assert proof['imprint_match']['matched_own_imprint']=='GCNOVELS'
    assert proof['catalogue_brand_context']['labels']==['GCノベルズ','GCN文庫']
    assert proof['catalogue_brand_context']['individual_brand_assignment']=='unknown'


@pytest.mark.parametrize('imprint',[None,'別ノベル','GCNコミックス','GCNOVELS外伝',
    'GCNOVELS ; GCN文庫','GCNOVELS = GCN文庫','GCNOVELS ; 別文庫','GCNOVELS = 別文庫'])
def test_gc_requires_one_eligible_own_imprint_without_inventing_a_source_brand(imprint):
    assert match_catalogue_reference('gc',reference(),parts(imprint=imprint)) is None


@pytest.mark.parametrize('label,imprint',[
    ('レジーナブックス','別文庫'),('レジーナブックス','アルファポリス'),
    ('レジーナブックス','レジーナコミックス'),('アルファポリス','レジーナブックス'),
    ('レジーナブックス','レジーナブックス ; アルファポリス'),
])
def test_available_alpha_own_imprint_cannot_contradict_card_brand_context(label,imprint):
    assert match_catalogue_reference('alphapolis',reference('alphapolis',label=label),parts('alphapolis',imprint=imprint)) is None


@pytest.mark.parametrize('provider',['gc','alphapolis'])
@pytest.mark.parametrize('change',['unknown_writer','writer_role_literal','title_other_volume','missing_subtitle',
    'extra_edition_marker','bad_id','bad_unit','known_isbn','known_publisher','known_volume',
    'source_ln_tag','metadata_conflict','missing_sha','foreign_evidence_host'])
def test_reference_gates_reject_inferences_and_incomplete_or_mismatched_full_titles(provider,change):
    ref=reference(provider);own=parts(provider,imprint=None if provider=='alphapolis' else 'GCNOVELS')
    if change=='unknown_writer':ref['credits']=[{'role':'unknown','name':'著者'}]
    elif change=='writer_role_literal':ref['credits'][0]['role_literal' if provider=='gc' else 'role_display']='イラスト'
    elif change=='title_other_volume':ref['title']='完全題２ 副題'
    elif change=='missing_subtitle':ref['title']='完全題３'
    elif change=='extra_edition_marker':ref['title']='完全題３ 副題 特装版'
    elif change=='bad_id':ref['publisher_reference_id']+='wrong'
    elif change=='bad_unit':ref['source_unit']='series'
    elif change=='known_isbn':ref['isbn']='9784798613161'
    elif change=='known_publisher':ref['publisher']='推定出版社'
    elif change=='known_volume':ref['volume']='3'
    elif change=='source_ln_tag':ref['official_lightnovel_tag']=True
    elif change=='metadata_conflict':ref['metadata_conflict']=True
    elif change=='missing_sha':ref['source_evidence'][0]['raw_sha256']=''
    else:ref['source_evidence'][0]['source_url']='https://example.com/index'
    assert match_catalogue_reference(provider,ref,own) is None
    if change=='unknown_writer':assert reference_hold_reason(provider,ref)=='official_writer_role_unknown'


@pytest.mark.parametrize('change',['publisher','two_publishers','unknown_distribution','nonfiction','mixed_ndc',
    'language','external_provider','work_not_edition','nested_title','nested_publisher','namespace',
    'foreign_volume','no_writer_evidence','illustrator','wrong_author'])
def test_own_primary_record_gate_cannot_be_replaced_by_contents_or_other_sources(change):
    own=parts()
    if change=='publisher':own['publisher_agents'][0]['name']=own['source']['publisher']='別社'
    elif change=='two_publishers':
        own['publisher_agents'].append({**deepcopy(own['publisher_agents'][0]),'name':'別社'})
        own['source']['publisher']+='; 別社'
    elif change=='unknown_distribution':own['publisher_agents'][0]['description_values']=['不明']
    elif change=='nonfiction':own['source']['extra']['ndc_codes']=['726.1']
    elif change=='mixed_ndc':own['source']['extra']['ndc_codes'].append('726.1')
    elif change=='language':own['source']['language']='eng'
    elif change=='external_provider':own['source']['provider_id']='external'
    elif change=='work_not_edition':own['source']['record_kind']='work'
    elif change=='nested_title':own['titles'][0]['locator']=OWN+'/contents[1]'+DCT+'title[1]'
    elif change=='nested_publisher':own['publisher_agents'][0]['locator']=OWN+'/contents[1]'+DCT+'publisher[3]'
    elif change=='namespace':own['titles'][0]['locator']=OWN+'/{https://example.com/}title[1]'
    elif change=='foreign_volume':own['volume']['locator']=OWN+'/contents[1]/{http://ndl.go.jp/dcndl/terms/}volume[2]'
    elif change=='no_writer_evidence':own['source']['contributors'][0]['identifiers']=[];own['literal_authors']=[]
    elif change=='illustrator':own['source']['contributors'][0]['role']='illustrator'
    else:own['source']['contributors'][0]['name']='別の著者'
    assert match_catalogue_reference('gc',reference(),own) is None


@pytest.mark.parametrize('role',['発売','頒布'])
def test_reuses_explicit_sales_distribution_and_own_writer_literal_without_authority_inference(role):
    own=parts(authority=False)
    locator=OWN+DCT+'publisher[5]'
    own['publisher_agents'].append({'name':'別販売社','locator':locator,'structured_agent':True,
        'description_values':[role],'description_locators':[locator+'/{http://xmlns.com/foaf/0.1/}Agent[1]'+DCT+'description[2]']})
    own['source']['publisher']+='; 別販売社'
    proof=match_catalogue_reference('gc',reference(),own)
    assert proof['authors'][0]['authority_status']=='unknown'
    assert proof['publisher_match']['distribution_agents'][0]['role']==role
    own['literal_authors'][0]['literal']='著者 編'
    assert match_catalogue_reference('gc',reference(),own) is None


def test_shared_parallel_title_fallback_and_purity(monkeypatch):
    own=parts()
    own['titles']=[{'value':'完全題 = Complete title : 副題. 3','locator':OWN+DCT+'title[1]'},
        {'value':'完全題 : 副題','locator':OWN+DC+'title[2]'}]
    monkeypatch.setattr('builtins.open',lambda *args,**kwargs:pytest.fail('unexpected I/O'))
    assert match_catalogue_reference('gc',reference(),own)['title_match']['fallback_kind']=='explicit_parallel_title'
    assert reference_hold_reason('unknown',reference())=='unsupported_catalogue_reference_provider'


@pytest.mark.parametrize('change',[{'brand':'regina'},{'individual_lightnovel_classification':'confirmed'}])
def test_alpha_context_metadata_cannot_swap_brand_or_promote_unassessed_classification(change):
    ref=reference('alphapolis')
    ref['source_scope_membership'].update(change)
    assert match_catalogue_reference('alphapolis',ref,parts('alphapolis',imprint=None)) is None
