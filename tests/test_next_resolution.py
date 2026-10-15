import pytest
from recommend_book.next_resolution import exact_wikipedia_page,ndl_bibliography,wd_metadata,pair_binding


def test_series_and_translation_claims_remain_metadata_not_work_equivalence():
    from recommend_book.next_resolution import relationship_claims
    statement={'mainsnak':{'snaktype':'value','datavalue':{'value':{'id':'Q2'}}},'qualifiers':{'P1545':[{'datavalue':{'value':'3'}}]}}
    raw={'entities':{'Q1':{'id':'Q1','claims':{'P179':[statement],'P629':[statement]}}}}
    claims=relationship_claims(wd_metadata(raw,'Q1'))
    assert [r['relation_type'] for r in claims]==['edition_or_translation_of','member_of_series']
    assert all(r['authority_merge_approved'] is False and r['status']=='held' for r in claims)
    assert claims[1]['assertion_json']['qualifiers']['P1545'][0]['datavalue']['value']=='3'


def test_batch_other_page_qid_cannot_establish_binding():
    record={'source_id':'en:10:wikipedia-prose-v2','extra':{'page_id':1,'revision_id':10}}
    raw={'query':{'pages':[{'pageid':1,'revisions':[{'revid':10}],'pageprops':{'wikibase_item':'Q1'}},{'pageid':2,'revisions':[{'revid':20}],'pageprops':{'wikibase_item':'Q2'}}]}}
    assert exact_wikipedia_page(raw,record)['qid']=='Q1'
    raw['query']['pages'].append(raw['query']['pages'][0])
    with pytest.raises(ValueError):exact_wikipedia_page(raw,record)


def test_map_page_locator_uses_raw_key():
    record={'source_id':'en:10:wikipedia-prose-v2','extra':{'page_id':1,'revision_id':10}}
    raw={'query':{'pages':{'1':{'pageid':1,'revisions':[{'revid':10}],'pageprops':{'wikibase_item':'Q1'}}}}}
    assert exact_wikipedia_page(raw,record)['assertion_locator']=='query.pages["1"].pageprops.wikibase_item'


def test_ndl_part_titles_and_generic_creators_are_not_primary_title_or_authors():
    raw=b'''<recordData xmlns:d="http://purl.org/dc/terms/" xmlns:n="http://ndl.go.jp/dcndl/terms/" xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:f="http://xmlns.com/foaf/0.1/"><r:RDF><n:BibResource><d:identifier r:datatype="http://ndl.go.jp/dcndl/terms/NDLBibID">123</d:identifier><d:title>Collection</d:title><n:partInformation><r:Description><d:title>Story</d:title></r:Description></n:partInformation><d:creator><f:Agent><f:name>Translator</f:name></f:Agent></d:creator></n:BibResource></r:RDF></recordData>'''
    value=ndl_bibliography(raw,'R100000002-I123')
    assert value['primary_titles']==['Collection']
    assert value['part_titles']==['Story']
    assert value['generic_creators'][0]['role']=='unassigned'
    assert value['explicit_roles']==[]


def test_same_title_author_and_ai_same_do_not_authorize_merge():
    wd={'source':'wikidata','source_id':'Q1'};wp={'source':'wikipedia','source_id':'en:10:wikipedia-prose-v2'}
    a={'canonical_id':'w1','resolved':1};b={'canonical_id':'w2','resolved':1}
    result=pair_binding(wd,wp,{'qid':'Q1','scope':'work'},{'qid':'Q1'},a,b)
    assert result['status']=='held' and result['authority_merge_approved'] is False
    assert 'canonical' in result['reason']
    b['canonical_id']='w1'
    assert pair_binding(wd,wp,{'qid':'Q1','scope':'work'},{'qid':'Q1'},a,b)['status']=='verified_existing_binding'
    edition=pair_binding(wd,wp,{'qid':'Q1','scope':'edition_or_translation'},{'qid':'Q1'},a,b)
    assert edition['status']=='verified_existing_binding' and edition['work_scope_status']=='held'


def test_entity_binding_and_original_work_scope_are_separate():
    wd={'source':'wikidata','source_id':'Q1'};wp={'source':'wikipedia','source_id':'en:10:wikipedia-prose-v2'}
    node={'canonical_id':'w1','resolved':1}
    result=pair_binding(wd,wp,{'qid':'Q1','scope':'unverified_work_scope'},{'qid':'Q1'},node,node)
    assert result['entity_binding_verified'] is True
    assert result['original_work_scope_status']=='held'
    assert result['authority_merge_approved'] is False


def test_wd_relations_preserve_scope_and_deprecated_claims_do_not_override():
    def claim(q,rank='normal'):return {'rank':rank,'mainsnak':{'snaktype':'value','datavalue':{'value':{'id':q}}}}
    raw={'entities':{'Q1':{'id':'Q1','claims':{'P31':[claim('Q3331189')],'P629':[claim('Q2')],'P179':[claim('Q3')],'P361':[claim('Q4','deprecated')]}}}}
    v=wd_metadata(raw,'Q1')
    assert v['scope']=='edition_or_translation' and v['relations']['P629'][0]['value']['id']=='Q2'
    assert v['relations']['P361']==[]
