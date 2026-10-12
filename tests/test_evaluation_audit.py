import hashlib
import json
from pathlib import Path
import pytest
from recommend_book.database import DatasetDB
from recommend_book.models import SourceRecord, Title, Contributor, Identifier, Evidence
from recommend_book.evaluation.audit import audit_dataset, write_audit_report, review_preconditions


def make_database(tmp_path):
    raw=tmp_path/'raw.json';raw.write_text('{}',encoding='utf-8')
    common=dict(source='wikidata',source_url='https://www.wikidata.org/',retrieved_at='2026-10-12T00:00:00Z',provider_id='wikidata',record_kind='work',raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),raw_path=str(raw),rights_policy_id='wikidata-cc0')
    first=SourceRecord(source_id='Q1',titles=[Title(value='English translated title')],contributors=[Contributor(name='Author A')],identifiers=[Identifier(source='wikidata',value='Q1',scope='work')],work_kind='novel',evidence=[Evidence(evidence_id='ok',kind='subject',text='detective fiction',source_url='https://www.wikidata.org/Q1',rights_policy_id='wikidata-cc0',llm_input_allowed=True)],**common)
    second=SourceRecord(source_id='Q2',titles=[Title(value='<script>alert(1)</script>')],original_language='ja',first_publication_year=1990,region=['Japan'],work_kind='short_story',evidence=[Evidence(evidence_id='wrong',kind='excerpt',text='unreviewed prose',source_url='https://www.wikidata.org/Q2',rights_policy_id='missing-policy',llm_input_allowed=True)],**common)
    db=DatasetDB(tmp_path/'dataset.sqlite');db.ingest([first,second])
    return db


def test_counts_are_distinct_works_and_no_language_inference(tmp_path):
    db=make_database(tmp_path);inputs=db.work_inputs()
    inputs.append(inputs[0].model_copy(update={'expression_id':'translated-expression'}))
    report=audit_dataset(db.path,inputs=inputs,results=[],root_dir=tmp_path)
    assert report['counts']['resolved_works']==2
    assert report['counts']['input_rows']==3
    assert report['counts']['distinct_input_works']==2
    assert report['distributions']['original_language']=={'ja':1,'unknown':1}
    assert report['missingness']['first_publication_year']['n']==1
    assert report['quality']['status']=='human_validation_pending'
    assert report['human_requirements']['classification']['target_works']==400
    assert report['review_preconditions']['human400']['work_count_ready'] is False


def test_invalid_policy_is_not_100_percent_rights(tmp_path):
    db=make_database(tmp_path)
    report=audit_dataset(db.path,inputs=db.work_inputs(),root_dir=tmp_path)
    assert report['provenance']['source_records']['valid_rate']==1
    assert report['provenance']['evidence']['valid_rate']==.5
    assert report['provenance']['all_required_references_100_percent'] is False
    assert report['evidence_coverage']['declared_llm_evidence_works']==2
    assert report['evidence_coverage']['audited_llm_evidence_works']==1
    assert any(v['code']=='unknown_evidence_policy' for v in report['findings'])


def test_audit_is_readonly_and_html_escapes_content(tmp_path):
    db=make_database(tmp_path);db.close()
    before=db.path.read_bytes()
    report=audit_dataset(db.path,root_dir=tmp_path)
    assert db.path.read_bytes()==before
    paths=write_audit_report(report,tmp_path/'report')
    html=Path(paths['html']).read_text('utf-8')
    assert '<script>alert(1)</script>' not in html
    assert '人手評価未実施' in html
    assert 'JSON' in html


def test_human400_preflight_detects_unbreakable_author_group():
    inputs=[{'work_id':str(i),'title':str(i),'authors':['One author'],'evidence':[]} for i in range(400)]
    preflight=review_preconditions(inputs)
    assert preflight['human400']['work_count_ready'] is True
    assert preflight['human400']['exact_grouped_split_ready'] is False
    assert preflight['human400']['largest_group_works']==400

def test_unknown_gold_is_excluded_from_actual_human_counts(tmp_path):
    db=make_database(tmp_path)
    gold=[{'work_id':'not-in-db','provenance':'human','reviewer_id':'r','truth_level':'evidence','partition':'holdout','labels':[{'label_id':'genre.mystery','decision':'present'}]}]
    report=audit_dataset(db.path,gold=gold,root_dir=tmp_path)
    assert report['quality']['human_annotation_works']==0
    assert report['quality']['status']=='human_validation_pending'
    assert report['quality']['excluded_gold'][0]['reason']=='Unknown canonical DB work'


def test_input_evidence_not_in_db_is_not_trusted(tmp_path):
    db=make_database(tmp_path);inputs=db.work_inputs()
    inputs[0]=inputs[0].model_copy(update={'evidence':[Evidence(evidence_id='fabricated',kind='subject',text='unknown',source_url='https://www.wikidata.org/Q1',rights_policy_id='wikidata-cc0',llm_input_allowed=True)]})
    report=audit_dataset(db.path,inputs=inputs,root_dir=tmp_path)
    assert any(f['code']=='input_evidence_not_in_db' for f in report['findings'])
    assert report['review_preconditions']['input_snapshot_matches_db'] is False


@pytest.mark.parametrize('wd_language,wd_year,ol_language,ol_year,expected_language,expected_period',[
    ('ja',1880,'en',2001,'ja','pre1900'),
    (None,None,'de',1905,'de','1900-1949'),
    (None,None,None,None,'unknown','unknown'),
])
def test_readonly_audit_matches_work_priority_and_work_only_fallback(
        tmp_path,wd_language,wd_year,ol_language,ol_year,expected_language,expected_period):
    from recommend_book.classification.validation import input_hash, load_taxonomy, target_language
    raw=tmp_path/'metadata-fixture.json';raw.write_text('{}',encoding='utf-8')
    common=dict(retrieved_at='2026-10-12T00:00:00Z',raw_path=str(raw),raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    author_id=Identifier(source='wikidata',value='Q-author',scope='agent')
    wd=SourceRecord(source='wikidata',source_id='Q-work',source_url='https://www.wikidata.org/Q-work',
        provider_id='wikidata',rights_policy_id='wikidata-cc0',record_kind='work',
        titles=[Title(value='Q-work'),Title(value='Original work title')],
        contributors=[Contributor(name='Raw author alias',identifiers=[author_id])],
        original_language=wd_language,first_publication_year=wd_year,
        region=['Japan'] if wd_language else [],series='Original series' if wd_language else None,
        work_kind='novel',**common)
    ol=SourceRecord(source='openlibrary',source_id='/works/OL-fixture-W',source_url='https://openlibrary.org/works/OL-fixture-W',
        provider_id='openlibrary',rights_policy_id='openlibrary-metadata',record_kind='work',
        titles=[Title(value='English translated work title')],contributors=[Contributor(name='Different author')],
        original_language=ol_language,first_publication_year=ol_year,region=['Germany'],series='OL series',work_kind='short_story',**common)
    edition=SourceRecord(source='openlibrary',source_id='/books/OL-fixture-M',source_url='https://openlibrary.org/books/OL-fixture-M',
        provider_id='openlibrary',rights_policy_id='openlibrary-metadata',record_kind='edition',
        titles=[Title(value='Translated edition')],contributors=[Contributor(name='Edition-only author')],
        language='en',publication_year=2025,original_language='fr',first_publication_year=2025,
        region=['Edition-only region'],series='Edition-only series',work_kind='edition',**common)
    authority=SourceRecord(source='wikidata',source_id='Q-author',source_url='https://www.wikidata.org/Q-author',
        provider_id='wikidata',rights_policy_id='wikidata-cc0',record_kind='agent',
        titles=[Title(value='Authority author label')],identifiers=[author_id],**common)
    db=DatasetDB(tmp_path/'metadata.sqlite');db.ingest([wd,ol,edition,authority])
    nodes={json.loads(row['record_json'])['source_id']:row['work_id'] for row in db.connection.execute(
        'SELECT w.work_id,s.record_json FROM works w JOIN source_records s ON s.version_id=w.source_version_id')}
    db.merge_work(nodes[ol.source_id],nodes[wd.source_id],reason='Explicit test fixture identity',reviewer='test-fixture')
    db.merge_work(nodes[edition.source_id],nodes[wd.source_id],reason='Explicit test fixture edition membership',reviewer='test-fixture')
    inputs=db.work_inputs();assert len(inputs)==1
    assert inputs[0].title=='Original work title'
    assert inputs[0].authors==['Authority author label']
    taxonomy=load_taxonomy()
    result=dict(work_id=inputs[0].work_id,expression_id=None,target_language=target_language(inputs[0]),
        taxonomy_version=taxonomy['version'],prompt_version='1.0',model='test-fixture',cli_version='test-fixture',
        input_hash=input_hash(inputs[0]),labels=[],missing_axes=[{'axis':axis['axis_id'],'reason':'Fixture has no classification evidence','decision':'unknown'} for axis in taxonomy['axes']],
        conflicts=[],needs_human_review=True)
    db.close();before=db.path.read_bytes()
    for supplied in (inputs,None):
        report=audit_dataset(db.path,inputs=supplied,results=[result],root_dir=tmp_path)
        assert report['distributions']['original_language']=={expected_language:1}
        assert report['distributions']['first_publication_period']=={expected_period:1}
        assert report['distributions']['region']=={'Japan' if wd_language else 'Germany':1}
        assert report['distributions']['work_kind']=={'novel':1}
        assert report['counts']['validated_result_rows']==1
        assert report['review_preconditions']['input_snapshot_matches_db'] is True
        assert not any(f['code']=='input_metadata_differs' for f in report['findings'])
        assert report['provenance']['source_records']['valid_rate']==1
        assert report['provenance']['raw_integrity']['valid_rate']==1
        assert report['quality']['status']=='human_validation_pending'
    assert db.path.read_bytes()==before
