"""Typed synthetic inputs: context fallback performs no DB, raw or network I/O."""
import pytest

from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue


BASE = 'records[1]/recordData/dcndl:BibResource[@rdf:about=https://ndlsearch.ndl.go.jp/books/R100000002-I1#material]'
DCT = BASE + '/{http://purl.org/dc/terms/}title[1]'
DC = BASE + '/{http://purl.org/dc/elements/1.1/}title[2]'


def fixture(monkeypatch, *, dc='主題', dct='主題 : 副題. 3', volume='3', official='主題３ 副題'):
    source = {'language':'jpn','publisher':'メディアファクトリー','series':'MF文庫J',
        'extra':{'ndc_codes':['913.6']},'identifiers':[{'source':'isbn','value':'9784798613161'}],
        'titles':[{'value':dct},{'value':dc}], 'source_url':'https://ndlsearch.ndl.go.jp/books/R100000002-I1',
        'contributors':[{'name':'著者','role':'author','identifiers':[
            {'source':'ndl-authority','value':'http://id.ndl.go.jp/auth/entity/01234567'}]}]}
    parts = {'source':source,'titles':[{'value':dct,'locator':DCT},{'value':dc,'locator':DC}],
        'volume':{'value':volume,'locator':BASE+'/{http://ndl.go.jp/dcndl/terms/}volume[3]'},
        'publisher_agents':[{'name':'メディアファクトリー','locator':BASE+'/{http://purl.org/dc/terms/}publisher[4]',
            'description_values':[],'description_locators':[],'structured_agent':False}],
        'literal_authors':[],'source_raw_sha256':'c'*64}
    book = {'publisher_book_id':'kadokawa:123456789012','source_url':'https://www.kadokawa.co.jp/product/123456789012/',
        'publisher':'KADOKAWA','catalogue_owner':'KADOKAWA','publisher_field_basis':'catalogue_owner_context',
        'individual_publisher':None,'product_form_id':3,'label':'MF文庫J','official_label_id':55,
        'title':official,'isbn':'9784798613161','credits':[{'role':'著者','name':'著者'}],
        'evidence':{'source_url':'https://www.kadokawa.co.jp/product/search/','source_sha256':'e'*64,'json_pointer':'/result/book/0'}}
    cat = object.__new__(LightNovelCatalogue)
    monkeypatch.setattr(cat,'_source_with_title_parts',lambda *args:source)
    monkeypatch.setattr(cat,'_edition_primary_bibliographic_parts',lambda *args:parts)
    return cat,book


@pytest.mark.parametrize('dc',['主題','主題 : 副題'])
def test_v5_accepts_own_dc_main_or_complete_title_only_when_match_uses_complete_dct(monkeypatch,dc):
    cat,book=fixture(monkeypatch,dc=dc)
    proof=cat._publisher_edition_match('kadokawa',book,{}, {})
    assert proof['rule_version']=='publisher-ndl-edition-confirmation-v5'
    assert proof['title_match']['source_value']=='主題 : 副題. 3'
    assert proof['title_match']['locators'][0]==DCT
    assert proof['title_match']['main']=='主題' and proof['title_match']['subtitle']=='副題'
    assert proof['title_match']['volume']=='3'


@pytest.mark.parametrize('change',[
    {'official':'主題３'}, {'official':'主題３ 別副題'}, {'dc':'別主題'},
    {'dc':'主題 : 別副題'}, {'dc':'主'}, {'dct':'主題 : 副題. 2'},
])
def test_v5_rejects_missing_changed_subtitle_unrelated_dc_and_disagreeing_volume(monkeypatch,change):
    cat,book=fixture(monkeypatch,**change)
    assert cat._publisher_edition_match('kadokawa',book,{}, {}) is None
