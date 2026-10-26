import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from test_light_novel_catalogue_v1 import setup_catalogue, edition_bundle, membership_bundle, write
from test_light_novel_dre_series_v1 import DETAIL
from recommend_book.ingestion.core import RawArtifact
from recommend_book.ingestion.ndl import NDLAdapter


@pytest.fixture
def dre_membership(membership_bundle,tmp_path):
    cat,series,edition,_,_=membership_bundle
    raw=Path(series['provenance']['raw_path'])
    payload=json.loads(raw.read_bytes())
    payload['entities']['Q600']['labels']['ja']['value']='ドリコム'
    series['provenance']['raw_sha256']=write(raw,payload)
    series['publishers']=[{'id':'Q600','label':'ドリコム'}]
    edition_raw=Path(edition['provenance']['raw_path'])
    xml=edition_raw.read_text(encoding='utf-8').replace('ラノベ文庫','DREノベルス').replace('出版社','ドリコム')
    edition_raw.write_text(xml,encoding='utf-8')
    pin=hashlib.sha256(edition_raw.read_bytes()).hexdigest()
    source=NDLAdapter(None)._normalize(ET.fromstring(xml),RawArtifact(pin,str(edition_raw),len(edition_raw.read_bytes()),
        '2026-10-26T00:00:00Z','https://ndlsearch.ndl.go.jp/api/sru'),'iss-ndl-opac',locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source,publisher=source['publisher'],label=source['series'])
    edition['label_evidence']=[{'query_term':'DREノベルス'}]
    edition['provenance']['raw_sha256']=pin
    assert cat.ingest([series])['failed']==0
    imported=cat.import_editions([edition])
    assert not imported['failures'],imported['failures']
    detail=tmp_path/'dre-detail.html'
    detail.write_text(DETAIL.replace('シリーズ甲',series['title']).replace('著者甲','著者'),encoding='utf-8')
    frames=tmp_path/'dre-frames.json'
    frames_pin=write(frames,[{'id':'dre-novels-series','publisher':'ドリコム','label':'DREノベルス','pages':[
        {'source_url':'https://drecom-media.jp/drenovels/product/21','raw_path':detail.name,
         'sha256':hashlib.sha256(detail.read_bytes()).hexdigest(),'http_status':200,'content_type':'text/html; charset=utf-8'}]}])
    manifest=tmp_path/'dre-authorities.json'
    value={'version':'light-novel-author-authorities-v1','authors':[
        {'author_qid':'Q500','raw_path':str(raw),'raw_sha256':series['provenance']['raw_sha256']}],
        'dre_series_proof':{'frames_path':str(frames),'frames_sha256':frames_pin,'source_root':str(tmp_path)}}
    return cat,manifest,write(manifest,value),detail,value


def test_official_imprint_and_author_authority_connect_existing_series(dre_membership):
    cat,manifest,pin,detail,_=dre_membership
    identity='lne:ndl:R100000002-I1'
    assert cat.resolve_dre_edition_memberships(manifest,pin,edition_ids=[identity])['linked']==1
    row=cat.work_detail(identity)
    assert row['series_entry_id']=='lns:wd:Q1'
    assert row['series_membership_evidence']['rule_version']=='dre-publisher-wikidata-edition-membership-v1'
    assert cat.resolve_dre_edition_memberships(manifest,pin,edition_ids=[identity])['unchanged']==1
    detail.write_text(detail.read_text(encoding='utf-8')+'changed',encoding='utf-8')
    assert 'series_entry_id' not in cat.work_detail(identity)


def test_wrong_independent_author_authority_does_not_connect(dre_membership,tmp_path):
    cat,manifest,_,_,value=dre_membership
    raw=Path(value['authors'][0]['raw_path'])
    payload=json.loads(raw.read_bytes())
    payload['entities']['Q500']['claims']['P349'][0]['mainsnak']['datavalue']['value']='99999999'
    alternate=tmp_path/'alternate-author.json'
    value['authors'][0].update(raw_path=str(alternate),raw_sha256=write(alternate,payload))
    pin=write(manifest,value)
    result=cat.resolve_dre_edition_memberships(manifest,pin,edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked']==0 and result['unresolved_editions']==1


def test_new_official_series_uses_existing_ndl_admission_and_member_replay(dre_membership,tmp_path):
    cat,_,_,_,value=dre_membership
    with cat._connect() as db:
        db.execute('DELETE FROM entries WHERE entry_id=?',('lns:wd:Q1',))
    proof=value['dre_series_proof']
    reference='publisher-series:'+hashlib.sha256(b'https://drecom-media.jp/drenovels/series/example').hexdigest()[:24]
    result=cat.import_ndl_series_from_publishers(proof['frames_path'],proof['frames_sha256'],source_root=tmp_path,reference_ids=[reference])
    assert result['inserted']==1 and result['new_edition_memberships']==1
    row=cat.work_detail('lne:ndl:R100000002-I1')
    assert row['series_entry_id']=='lns:ndl:R100000002-I1'


@pytest.mark.parametrize('url,rank,linked',[
    ('https://drecom-media.jp/drenovels/series/example','normal',1),
    ('https://drecom-media.jp/drenovels/series/other','normal',0),
    ('https://example.com/drenovels/series/example','normal',0),
    ('https://drecom-media.jp/drenovels/series/example','deprecated',0),
])
def test_exact_official_series_identifier_can_bind_without_person_crosswalk(dre_membership,url,rank,linked):
    cat,manifest,_,_,value=dre_membership
    with cat._connect() as db:
        record=json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',('lns:wd:Q1',)).fetchone()[0])
    raw=Path(record['provenance']['raw_path']);payload=json.loads(raw.read_bytes())
    payload['entities']['Q500']['claims']['P349']=[]
    payload['entities']['Q1']['claims']['P856']=[{'rank':rank,'mainsnak':{'snaktype':'value','datavalue':{'value':url}}}]
    pin=write(raw,payload)
    record['provenance']['raw_sha256']=pin
    assert cat.ingest([record])['failed']==0
    value['authors'][0]['raw_sha256']=pin
    manifest_pin=write(manifest,value)
    result=cat.resolve_dre_edition_memberships(manifest,manifest_pin,edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked']==linked
    if linked:
        membership=cat.work_detail('lne:ndl:R100000002-I1')['series_membership_evidence']
        assert membership['series_identity_basis']=='exact_wikidata_P856_and_explicit_publisher_series'
        assert membership['author_matches']==[] and membership['person_identity_asserted'] is False


@pytest.mark.parametrize('member_isbn,member_volume,member_author,linked',[
    ('9784434311017','1','著者',1),
    ('9784434375699','1','著者',0),
    ('9784434311017','2','著者',0),
    ('9784434311017','1','別人',0),
])
def test_english_imprint_requires_exact_own_series_member_isbn_volume_and_author(dre_membership,member_isbn,member_volume,member_author,linked):
    cat,manifest,_,detail,value=dre_membership
    with cat._connect() as db:
        edition=json.loads(db.execute('SELECT record_json FROM entries WHERE entry_id=?',('lne:ndl:R100000002-I1',)).fetchone()[0])
    raw=Path(edition['provenance']['raw_path'])
    xml=raw.read_text(encoding='utf-8').replace('DREノベルス','DRE NOVELS').replace('</n:BibResource>',
        '<d:identifier rdf:datatype="ISBN">9784434311017</d:identifier></n:BibResource>')
    raw.write_text(xml,encoding='utf-8');pin=hashlib.sha256(raw.read_bytes()).hexdigest()
    source=NDLAdapter(None)._normalize(ET.fromstring(xml),RawArtifact(pin,str(raw),len(raw.read_bytes()),
        '2026-10-26T00:00:00Z','https://ndlsearch.ndl.go.jp/api/sru'),'iss-ndl-opac',locator='records[1]/recordData').model_dump(mode='json')
    edition.update(source_record=source,label=source['series'],isbn=['9784434311017'],label_evidence=[{'query_term':'DRE NOVELS'}])
    edition['provenance']['raw_sha256']=pin
    assert cat.import_editions([edition])['failed']==0
    title=source['titles'][0]['value']
    detail.write_text(detail.read_text(encoding='utf-8')+f'''<section><h2 id="series" class="productDetail__subTitle--novels">シリーズ既刊一覧</h2>
      <li class="bookColumn__item"><a href="https://drecom-media.jp/drenovels/product/21">
      <span class="bookColumn__title">{title}{member_volume}</span><span class="bookColumn__author">著 / {member_author}</span>
      <span class="productColumn__infoLabel">ISBN</span><span class="productColumn__infoValue">{member_isbn}</span></a></li></section>''',encoding='utf-8')
    frames=Path(value['dre_series_proof']['frames_path']);frame=json.loads(frames.read_bytes())
    frame[0]['pages'][0]['sha256']=hashlib.sha256(detail.read_bytes()).hexdigest()
    value['dre_series_proof']['frames_sha256']=write(frames,frame)
    result=cat.resolve_dre_edition_memberships(manifest,write(manifest,value),edition_ids=['lne:ndl:R100000002-I1'])
    assert result['linked']==linked
    if linked:
        membership=cat.work_detail('lne:ndl:R100000002-I1')['series_membership_evidence']
        assert membership['edition_label_witness']['edition_scoped_only'] is True
