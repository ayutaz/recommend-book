from copy import deepcopy
from hashlib import sha256

import pytest

from recommend_book.light_novel_gagaga_bibliographic_match_v1 import (
    match_gagaga_reference, reference_hold_reason,
)

OWN = 'records[1]/recordData/dcndl:BibResource'
DCT = '/{http://purl.org/dc/terms/}'
DC = '/{http://purl.org/dc/elements/1.1/}'


def reference(*, release=False):
    url = 'https://gagagabunko.jp/' + ('release/index.html#01' if release else 'lineup/202609.html#01')
    raw_sha = 'a' * 64
    identity = ('publisher-lineup-snapshot:' + sha256((url+'\n'+raw_sha).encode()).hexdigest()[:24]
        if release else 'publisher-lineup:' + sha256(url.encode()).hexdigest()[:24])
    return {'publisher_reference_id':identity,'source_unit':'publisher_lineup_item_reference',
        'source_url':url,'title':'完全題３ 副題','isbn':'9784094533040' if release else None,
        'credits':[{'role':'著','name':'著者'},{'role':'イラスト','name':'絵担当'}],
        'publisher':'小学館','label':None,'label_literals':[],'form':None,
        'official_lightnovel_tag':None,'story_series_asserted':False,
        'metadata_conflict':False,'bibliographic_variants':[],
        'source_evidence':[{'source_url':'https://gagagabunko.jp/'+('release/index.html' if release else 'lineup/index.html'),
            'raw_sha256':raw_sha,'locator':'section#01 div#title h3/span.textsize14/ISBN' if release else 'li[1] a/p and explicit credit roles'}]}


def parts(*, imprint='ガガガ文庫', authority=True):
    return {'source':{'source_id':'R100000002-I1','source_url':'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'record_kind':'edition','provider_id':'iss-ndl-opac-national','language':'jpn',
        'publisher':'小学館','series':imprint,'extra':{'ndc_codes':['913.6']},
        'contributors':[{'name':'著者','role':'author','identifiers':[
            {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/01234567'}] if authority else []}]},
        'source_raw_sha256':'b'*64,'titles':[{'value':'完全題 : 副題. 3','locator':OWN+DCT+'title[1]'}],
        'volume':{'value':'3','locator':OWN+'/{http://ndl.go.jp/dcndl/terms/}volume[2]'},
        'publisher_agents':[{'name':'小学館','locator':OWN+DCT+'publisher[3]',
            'structured_agent':True,'description_values':[],'description_locators':[]}],
        'literal_authors':[{'name':'著者','literal':'著者 著','locator':OWN+DC+'creator[4]'}]}


@pytest.mark.parametrize('imprint',['ガガガ文庫','ガガガ文庫 ; あ-01-01','ガガガブックス','ガガガブックスf'])
@pytest.mark.parametrize('release',[False,True])
def test_authenticated_row_shape_yields_only_bibliographic_correspondence(imprint, release):
    ref, own = reference(release=release), parts(imprint=imprint)
    before = deepcopy((ref,own))
    assert reference_hold_reason(ref) is None
    proof = match_gagaga_reference(ref,own)
    assert proof['provider']=='gagaga' and proof['scope']=='publisher_reference_to_ndl_bibliographic_record'
    assert proof['official_isbn']==ref['isbn']
    assert proof['official_label'] is None and proof['official_publisher'] is None
    assert proof['official_lightnovel_tag'] is None and proof['light_novel_status']=='unknown'
    assert proof['catalogue_owner_context']['literal']=='小学館'
    assert proof['imprint_match']['matched_own_imprint']==imprint.split(' ; ')[0]
    assert proof['official_individual_imprint_asserted'] is False
    assert proof['official_publisher_asserted'] is False
    assert proof['title_match']['volume']=='3'
    assert proof['official_writer_credits']==[{'role':'著','name':'著者'}]
    for flag in ('same_edition_identity_asserted','product_sku_identity_asserted',
        'original_work_identity_asserted','story_series_identity_asserted',
        'global_author_alias_asserted','global_author_identity_asserted'):
        assert proof[flag] is False
    assert (ref,own)==before


@pytest.mark.parametrize('change',['original_only','normalized_role','empty_writer','duplicate_writer',
    'bad_id','bad_unit','foreign_host','fake_month','query','foreign_evidence','bad_sha','no_locator',
    'source_tag','source_publisher','metadata_conflict','variants','form','invalid_isbn',
    'missing_subtitle','wrong_volume','missing_volume','missing_title_word'])
def test_reference_scope_explicit_writer_and_complete_title_gates(change):
    ref=reference();own=parts()
    if change=='original_only':ref['credits']=[{'role':'原作','name':'著者'}]
    elif change=='normalized_role':ref['credits'][0]['role']='author'
    elif change=='empty_writer':ref['credits'][0]['name']=''
    elif change=='duplicate_writer':ref['credits'].append({'role':'著','name':'著 者'})
    elif change=='bad_id':ref['publisher_reference_id']+='0'
    elif change=='bad_unit':ref['source_unit']='publisher_product_reference'
    elif change=='foreign_host':ref['source_url']=ref['source_url'].replace('gagagabunko.jp','example.com')
    elif change=='fake_month':ref['source_url']='https://gagagabunko.jp/lineup/2026.html#01'
    elif change=='query':ref['source_url']+='?x=1'
    elif change=='foreign_evidence':ref['source_evidence'][0]['source_url']='https://gagagabunko.jp/unverified.html'
    elif change=='bad_sha':ref['source_evidence'][0]['raw_sha256']='x'*64
    elif change=='no_locator':ref['source_evidence'][0]['locator']=''
    elif change=='source_tag':ref['official_lightnovel_tag']=True
    elif change=='source_publisher':ref['publisher']='別社'
    elif change=='metadata_conflict':ref['metadata_conflict']=True
    elif change=='variants':ref['bibliographic_variants']=[{'title':'別題'}]
    elif change=='form':ref['form']='画集'
    elif change=='invalid_isbn':ref['isbn']='9784094533041'
    elif change=='missing_subtitle':ref['title']='完全題３'
    elif change=='wrong_volume':ref['title']='完全題２ 副題'
    elif change=='missing_volume':ref['title']='完全題 副題'
    else:ref['title']='完全３ 副題'
    assert match_gagaga_reference(ref,own) is None
    if change=='original_only':assert reference_hold_reason(ref)=='official_writer_role_unknown'


def test_release_snapshot_id_is_bound_to_its_explicit_release_page_sha():
    ref=reference(release=True)
    ref['source_evidence'][0]['raw_sha256']='c'*64
    assert match_gagaga_reference(ref,parts()) is None
    ref=reference(release=True)
    ref['source_evidence'][0]['source_url']='https://gagagabunko.jp/lineup/index.html'
    assert match_gagaga_reference(ref,parts()) is None


@pytest.mark.parametrize('imprint',[None,'別文庫','ガガガコミックス','ガガガ文庫外伝',
    'ガガガ文庫 = ガガガブックス','ガガガ文庫 ; 別文庫','ガガガブックス ; ガガガ文庫'])
def test_only_one_own_eligible_imprint_without_inferred_source_label(imprint):
    assert match_gagaga_reference(reference(),parts(imprint=imprint)) is None


def test_explicit_individual_source_label_must_not_contradict_own_imprint():
    ref=reference(release=True)
    ref.update(label='ガガガブックスf',label_literals=['ガガガブックスf'])
    assert match_gagaga_reference(ref,parts(imprint='ガガガブックスf'))['official_label']=='ガガガブックスf'
    assert match_gagaga_reference(ref,parts()) is None
    ref['label_literals']=[]
    assert match_gagaga_reference(ref,parts(imprint='ガガガブックスf')) is None


def test_own_gagaga_bunko_imprint_is_not_an_observed_source_individual_label():
    ref=reference(release=True)
    ref.update(label='ガガガ文庫',label_literals=['ガガガ文庫'])
    assert match_gagaga_reference(ref,parts()) is None


def test_conflicting_second_own_complete_title_is_not_ignored():
    own=parts()
    own['titles'].append({'value':'完全題 : 別副題. 3','locator':OWN+DCT+'title[2]'})
    assert match_gagaga_reference(reference(),own) is None


@pytest.mark.parametrize('change',['publisher','two_publishers','unknown_distribution','nonfiction','mixed_ndc',
    'language','external','work','raw_sha','nested_title','namespace','nested_publisher',
    'nested_volume','different_author','illustrator','no_writer_witness','second_unmatched_writer'])
def test_own_resource_gates_and_all_explicit_writers(change):
    ref=reference();own=parts()
    if change=='publisher':own['source']['publisher']=own['publisher_agents'][0]['name']='別社'
    elif change=='two_publishers':
        own['publisher_agents'].append({**deepcopy(own['publisher_agents'][0]),'name':'別社'})
        own['source']['publisher']+='; 別社'
    elif change=='unknown_distribution':own['publisher_agents'][0]['description_values']=['不明']
    elif change=='nonfiction':own['source']['extra']['ndc_codes']=['726.1']
    elif change=='mixed_ndc':own['source']['extra']['ndc_codes'].append('726.1')
    elif change=='language':own['source']['language']='eng'
    elif change=='external':own['source']['provider_id']='external'
    elif change=='work':own['source']['record_kind']='work'
    elif change=='raw_sha':own['source_raw_sha256']=''
    elif change=='nested_title':own['titles'][0]['locator']=OWN+'/contents[1]'+DCT+'title[1]'
    elif change=='namespace':own['titles'][0]['locator']=OWN+'/{https://example.com/}title[1]'
    elif change=='nested_publisher':own['publisher_agents'][0]['locator']=OWN+'/contents[1]'+DCT+'publisher[3]'
    elif change=='nested_volume':own['volume']['locator']=OWN+'/contents[1]/{http://ndl.go.jp/dcndl/terms/}volume[2]'
    elif change=='different_author':own['source']['contributors'][0]['name']='別著者'
    elif change=='illustrator':own['source']['contributors'][0]['role']='illustrator'
    elif change=='no_writer_witness':own['source']['contributors'][0]['identifiers']=[];own['literal_authors']=[]
    else:ref['credits'].append({'role':'著','name':'もう一人'})
    assert match_gagaga_reference(ref,own) is None


@pytest.mark.parametrize('role',['発売','頒布'])
def test_explicit_distribution_and_edition_bound_literal_author_reuse(role):
    own=parts(authority=False)
    locator=OWN+DCT+'publisher[5]'
    own['publisher_agents'].append({'name':'販売社','locator':locator,'structured_agent':True,
        'description_values':[role],'description_locators':[locator+'/{http://xmlns.com/foaf/0.1/}Agent[1]'+DCT+'description[2]']})
    own['source']['publisher']+='; 販売社'
    proof=match_gagaga_reference(reference(),own)
    assert proof['authors'][0]['authority_status']=='unknown'
    assert proof['publisher_match']['distribution_agents'][0]['role']==role
    own['publisher_agents'][-1]['description_locators'][0]=OWN+'/contents[1]'+DCT+'description[2]'
    assert match_gagaga_reference(reference(),own) is None


def test_pure_shared_parallel_title_fallback_retains_all_subtitle_words(monkeypatch):
    own=parts()
    own['titles']=[{'value':'完全題 = Complete title : 副題. 3','locator':OWN+DCT+'title[1]'},
        {'value':'完全題 : 副題','locator':OWN+DC+'title[2]'}]
    monkeypatch.setattr('builtins.open',lambda *args,**kwargs:pytest.fail('unexpected I/O'))
    assert match_gagaga_reference(reference(),own)['title_match']['fallback_kind']=='explicit_parallel_title'
    own['titles'][1]['value']='完全題 : 別副題'
    assert match_gagaga_reference(reference(),own) is None


@pytest.mark.parametrize('serial', ['ガお1-11', 'ガえ3-2', 'ガわ3-33', 'ガお-1-11', '1-11'])
def test_serial_prefix_gagaga_accepts_attached_letters_without_dropping_any_bibliographic_gate(serial):
    imprint = 'ガガガ文庫 ; ' + serial
    ref, own = reference(), parts(imprint=imprint)
    before = deepcopy((ref, own))
    proof = match_gagaga_reference(ref, own)
    assert proof is not None
    assert proof['own_imprint'] == proof['imprint_match']['source_value'] == imprint
    assert proof['light_novel_status'] == 'unknown' and proof['official_label'] is None
    assert proof['title_match']['volume'] == '3'
    assert proof['publisher_match']['publisher_agent']['name'] == '小学館'
    assert proof['authors'][0]['authority_ids'] == ['http://id.ndl.go.jp/auth/entity/01234567']
    assert (ref, own) == before


@pytest.mark.parametrize('suffix', ['ガお', 'ガお-', 'ガお1-11 (新装版)', 'ガお1-11 ; ガガガブックス'])
def test_serial_prefix_gagaga_still_rejects_multiple_imprints_annotations_and_prefix_only(suffix):
    assert match_gagaga_reference(reference(), parts(imprint='ガガガ文庫 ; ' + suffix)) is None


def test_serial_prefix_gagaga_old_valid_complete_proof_dicts_equal_the_previous_parser(monkeypatch):
    import re
    from recommend_book import light_novel_gagaga_bibliographic_match_v1 as matcher
    pairs = []
    for serial in ('123', 'ガお-1-11', 'ab-1.2'):
        ref, own = reference(), parts(imprint='ガガガ文庫 ; ' + serial)
        proof = match_gagaga_reference(ref, own)
        assert proof is not None
        pairs.append((ref, own, proof))
    monkeypatch.setattr(matcher, '_SERIAL', re.compile(r'(?:[ぁ-ゖァ-ヺA-Za-z]{1,4}-)?[0-9]+(?:[-.][0-9]+)*'))
    for ref, own, proof in pairs:
        assert match_gagaga_reference(ref, own) == proof
