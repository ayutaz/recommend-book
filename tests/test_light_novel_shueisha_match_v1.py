from copy import deepcopy

import pytest

from recommend_book.light_novel_shueisha_match_v1 import match_shueisha_bibliographic_edition

OWN='records[1]/recordData/dcndl:BibResource'
DCT='/{http://purl.org/dc/terms/}'
DC='/{http://purl.org/dc/elements/1.1/}'
DCNDL='/{http://ndl.go.jp/dcndl/terms/}'
BOOK_ID='978-4-08-631001-7'
ISBN='9784086310017'


def book():
    return {'kind':'book','publisher_book_id':BOOK_ID,'isbn_as_publisher_url':BOOK_ID,'isbn':ISBN,
        'source_url':'https://dash.shueisha.co.jp/bookDetail/index/'+BOOK_ID,
        'title':'完全題３ 副題','publisher':'集英社','label':'スーパーダッシュ文庫',
        'credits':[{'role':'著者','name':'架空花子'},{'role':'イラスト','name':'絵担当'}],
        'story_series_identity':'unassessed_no_title_stripping',
        'source_evidence':{'source_url':'https://dash.shueisha.co.jp/library/index/2/',
            'source_sha256':'a'*64,'selector':'section.result_box:nth-of-type(2) h4 / span.boldText',
            'raw_path':'private/page.bin','retrieved_at':'2026-10-25T00:00:00Z'}}


def parts():
    return {'source':{'source_id':'R100000002-I1','source_url':'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'provider_id':'iss-ndl-opac-national','record_kind':'edition','language':'jpn','publisher':'集英社',
        'series':'集英社スーパーダッシュ文庫 ; 3','extra':{'ndc_codes':['913.6']},
        'identifiers':[{'source':'isbn','value':ISBN}],
        'contributors':[{'name':'架空花子','role':'author','identifiers':[
            {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/01234567'}]}]},
        'source_raw_sha256':'b'*64,'titles':[{'value':'完全題 : 副題. 3','locator':OWN+DCT+'title[1]'}],
        'volume':{'value':'3','locator':OWN+DCNDL+'volume[2]'},'edition_statements':[],
        'publisher_agents':[{'name':'集英社','locator':OWN+DCT+'publisher[3]','structured_agent':True,
            'description_values':[],'description_locators':[]}],
        'literal_authors':[{'name':'架空花子','literal':'架空花子 著','locator':OWN+DC+'creator[4]'}]}


def test_complete_title_volume_and_observed_superdash_pair_are_record_bound_not_printed_isbn_evidence():
    ref,own=book(),parts();before=deepcopy((ref,own))
    proof=match_shueisha_bibliographic_edition(ref,own)
    assert proof['scope']=='edition_only' and proof['confirmed_scope']=='contained_bibliographic_edition_only'
    assert proof['publisher_book_id']==BOOK_ID and proof['isbn_as_publisher_url']==BOOK_ID
    assert proof['isbn_lookup_basis']=='publisher_url_identifier_to_own_primary_isbn'
    assert proof['official_printed_ISBN_asserted'] is False and proof['isbn13']==ISBN
    assert proof['official_publisher_asserted'] is False and proof['individual_publisher_agreement']=='unknown'
    assert proof['title_match']['volume']=='3' and proof['title_match']['subtitle']=='副題'
    pair=proof['imprint_match']
    assert pair['rule']=='same_record_superdash_literal_pair'
    assert pair['official_label']=='スーパーダッシュ文庫' and pair['source_value']==own['source']['series']
    assert pair['global_imprint_alias_asserted'] is False and pair['source_raw_sha256']=='b'*64
    assert 'raw_path' not in proof['publisher_source_evidence']
    for key in ('same_edition_identity_asserted','product_sku_identity_asserted','original_work_identity_asserted',
        'story_series_identity_asserted','global_author_identity_asserted','global_author_alias_asserted'):
        assert proof[key] is False
    assert (ref,own)==before


def test_existing_parallel_title_fallback_retains_english_literal_and_explicit_volume():
    ref,own=book(),parts()
    ref['title']='完全題３'
    own['titles']=[{'value':'完全題 = Complete title. 3','locator':OWN+DCT+'title[1]'},
        {'value':'完全題','locator':OWN+DC+'title[2]'}]
    proof=match_shueisha_bibliographic_edition(ref,own)
    assert proof['title_match']['fallback_kind']=='explicit_parallel_title'
    assert proof['title_match']['source_value']=='完全題 = Complete title. 3'


def test_dated_own_authority_needs_same_own_explicit_writer_literal_and_no_series_author_claim():
    own=parts()
    own['source']['contributors'][0]['name']='架空, 花子, 1971-'
    proof=match_shueisha_bibliographic_edition(book(),own)
    assert proof['authors'][0]['binding_rule']=='dated_own_heading_and_explicit_writer_literal'
    bound=proof['authors'][0]['contributors'][0]['edition_bound_corroboration']
    assert bound['authority_heading']=='架空, 花子, 1971-'
    assert bound['official_book_writer']=='架空花子' and 'official_series_author' not in bound
    own['literal_authors'][0]['locator']=OWN+'/contents[1]'+DC+'creator[4]'
    assert match_shueisha_bibliographic_edition(book(),own) is None


def test_explicit_novel_writer_is_distinct_from_original_story_script_and_illustration():
    ref=book();ref['credits']=[{'role':'原作','name':'原作担当'},{'role':'脚本','name':'脚本担当'},
        {'role':'小説','name':'架空花子'},{'role':'イラスト','name':'絵担当'}]
    proof=match_shueisha_bibliographic_edition(ref,parts())
    assert proof['official_book_writer_credits']==[{'role':'小説','name':'架空花子'}]
    assert proof['official_credits']==ref['credits']
    ref['credits']=[{'role':'原作','name':'架空花子'}]
    assert match_shueisha_bibliographic_edition(ref,parts()) is None


@pytest.mark.parametrize('label,imprint',[
    ('ダッシュエックス文庫','ダッシュエックス文庫 ; 1'),
    ('ダッシュエックス文庫DIGITAL','ダッシュエックス文庫DIGITAL'),
    ('Dノベル／ダッシュエックスノベル','ダッシュエックスノベル'),
    ('Dノベルf／ダッシュエックスノベルエフ','Dノベルf'),
])
def test_exact_label_or_explicit_source_component_is_supported_without_new_alias(label,imprint):
    ref,own=book(),parts();ref['label']=label;own['source']['series']=imprint
    assert match_shueisha_bibliographic_edition(ref,own)['imprint_match']['rule']=='exact_official_label_or_component'


def test_source_url_id_role_and_bibliographic_scope_must_reproduce_the_formal_row():
    for change in ('url','id','url_id','isbn','label','kind','publisher','sha','page','selector','author'):
        ref=book()
        if change=='url':ref['source_url']+='?isbn='+ISBN
        elif change=='id':ref['publisher_book_id']='978-4-08-631003-1'
        elif change=='url_id':ref['isbn_as_publisher_url']='978-4-08-631003-1'
        elif change=='isbn':ref['isbn']='9784086310031'
        elif change=='label':ref['label']='ダッシュエックスコミックス'
        elif change=='kind':ref['kind']='series'
        elif change=='publisher':ref['publisher']='別社'
        elif change=='sha':ref['source_evidence']['source_sha256']=''
        elif change=='page':ref['source_evidence']['source_url']='https://dash.shueisha.co.jp/bookDetail/index/'+BOOK_ID
        elif change=='selector':ref['source_evidence']['selector']=''
        else:ref['credits']=[{'role':'原作','name':'架空花子'}]
        assert match_shueisha_bibliographic_edition(ref,parts()) is None,change


def test_all_own_record_title_volume_isbn_writer_publisher_and_classification_gates_remain_required():
    for change in ('isbn','title','volume','writer','second_writer','publisher','mixed_publisher',
        'language','ndc','mixed_ndc','id','provider','raw_sha','nested_title','nested_volume','nested_publisher'):
        ref,own=book(),parts()
        if change=='isbn':own['source']['identifiers'][0]['value']='9784086310031'
        elif change=='title':ref['title']='完全題３'
        elif change=='volume':own['volume']['value']='2'
        elif change=='writer':own['source']['contributors'][0]['name']='別人';own['literal_authors']=[]
        elif change=='second_writer':ref['credits'].append({'role':'著者','name':'未対応著者'})
        elif change=='publisher':own['source']['publisher']=own['publisher_agents'][0]['name']='別社'
        elif change=='mixed_publisher':
            own['source']['publisher']+='; 別社'
            own['publisher_agents'].append({**deepcopy(own['publisher_agents'][0]),'name':'別社'})
        elif change=='language':own['source']['language']='eng'
        elif change=='ndc':own['source']['extra']['ndc_codes']=['726.1']
        elif change=='mixed_ndc':own['source']['extra']['ndc_codes'].append('726.1')
        elif change=='id':own['source']['source_id']='R100000009-X'
        elif change=='provider':own['source']['provider_id']='external'
        elif change=='raw_sha':own['source_raw_sha256']=''
        elif change=='nested_title':own['titles'][0]['locator']=OWN+'/contents[1]'+DCT+'title[1]'
        elif change=='nested_volume':own['volume']['locator']=OWN+'/contents[1]'+DCNDL+'volume[2]'
        else:own['publisher_agents'][0]['locator']=OWN+'/contents[1]'+DCT+'publisher[3]'
        assert match_shueisha_bibliographic_edition(ref,own) is None,change


def test_superdash_pair_is_only_observed_literal_and_single_eligible_imprint():
    for imprint in ('集英社スーパーダッシュ文庫外伝','集英社スーパーダッシュ文庫 ; 別文庫',
        '集英社スーパーダッシュ文庫 = 別文庫','SUPER DASH','ダッシュエックス文庫'):
        own=parts();own['source']['series']=imprint
        assert match_shueisha_bibliographic_edition(book(),own) is None,imprint
    ref=book();ref['label']='ダッシュエックス文庫'
    assert match_shueisha_bibliographic_edition(ref,parts()) is None


def test_pure_matcher_retains_unknown_own_authority_with_explicit_writer_literal(monkeypatch):
    own=parts();own['source']['contributors'][0]['identifiers']=[]
    monkeypatch.setattr('builtins.open',lambda *a,**k:pytest.fail('unexpected I/O'))
    assert match_shueisha_bibliographic_edition(book(),own)['authors'][0]['authority_status']=='unknown'
    own['literal_authors']=[]
    assert match_shueisha_bibliographic_edition(book(),own) is None
