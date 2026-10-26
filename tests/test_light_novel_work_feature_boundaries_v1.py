"""Shared boundary integration: temporary catalogue and offline fixture CLI only."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_light_novel_admission_v3 import source,claim
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_sources_v1 import work_bundle
from test_light_novel_work_runner_v1 import history,completed
from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
from recommend_book.recommendation.scale_site_v1 import public_work


def test_verified_native_work_export_reaches_work_display_stats_and_reasons(completed,work_bundle,tmp_path):
    result,_,_,_=completed
    cat=LightNovelCatalogue(tmp_path/'feature-catalogue.sqlite')
    assert cat.ingest([work_bundle[0]])['inserted']==1
    assert cat.import_features(result['manifest_path'],result['manifest_sha256'])=={'imported':1,'held':0}
    row=cat.work_detail('lnw:wd:Q1')
    assert row['entry_kind']=='work' and row['content_features_adopted'] is True
    assert row['recommendation_mode']=='work_content_preferences'
    assert row['native_features'] and all(l['scope']=='work' for l in row['native_features'])
    shown=public_work(row);text=json.dumps(shown,ensure_ascii=False)
    assert shown['recommendation_mode']=='work_content_preferences'
    assert len(shown['content_sources'])==1
    citation=shown['content_sources'][0]
    original=work_bundle[3].evidence[0]
    assert citation['source_url']==citation['permalink']==original.source_url
    assert citation['history_url']==original.attribution['history_url']
    assert citation['license']==citation['output_license']=='CC-BY-SA-4.0'
    assert citation['license_url']==citation['output_license_url']=='https://creativecommons.org/licenses/by-sa/4.0/'
    assert citation['attribution']==original.attribution['credit']
    assert row['content_sources'][0]['display_allowed'] is True
    assert row['content_sources'][0]['quote_allowed'] is False
    assert 'raw_path' not in text and str(tmp_path) not in text and 'work_admission_proof' not in text
    assert work_bundle[3].evidence[0].text not in text
    stats=cat.catalogue_stats()
    assert stats['content_feature_works']==1 and stats['content_feature_series']==0
    ranked=cat.recommend_preferences(preferred_features=['genre.fantasy'],exclude_read=False)
    selected=next(r for r in ranked['items'] if r['entry_id']=='lnw:wd:Q1')
    assert selected['score_kind']=='uncalibrated_work_similarity'
    reason=next(r for r in selected['reasons'] if r['label_id']=='genre.fantasy')
    assert reason['kind']=='work_content_feature' and reason['scope']=='work'
    assert public_work(selected)['reasons'][0]['scope']=='work'
    assert ranked['classification_scope']=='work'


def test_old_series_apply_mode_and_new_cross_unit_hold_are_preserved():
    base={'entry_id':'lns:wd:Q9','entry_kind':'series','genres':[], 'recommendation_mode':'source_similarity'}
    feature={'scope':'series','entity_kind':'light_novel_series','labels':[{'label_id':'genre.fantasy','label':'Fantasy','axis':'genre','decision':'present','scope':'series','evidence':[{'evidence_id':'e1','locator':'paragraph1','quote':'kept raw quote'}]}], 'source_attributions':[{'evidence_id':'e1','kind':'synopsis','source_url':'https://en.wikipedia.org/w/index.php?oldid=1','license':'CC-BY-SA-4.0'}], 'content_features_adopted':True}
    LightNovelCatalogue._apply_feature(base,feature)
    assert base['recommendation_mode']=='series_content_preferences'
    assert base['native_features'][0]['scope']=='series' and base['native_features'][0]['evidence'][0]['quote'] is None
    work={'entry_id':'lnw:wd:Q1','entry_kind':'work','genres':[],'native_features':[],'recommendation_mode':'source_similarity'}
    original=deepcopy(work);LightNovelCatalogue._apply_feature(work,feature)
    assert work==original


def test_archived_mixed_input_context_is_explicit_and_never_fresh_plot_claim(work_bundle):
    meta=work_bundle[0];row={'entry_id':'lnw:wd:Q1','entry_kind':'work','qid':'Q1','title':meta['title'],'authors':[],'genres':[],'recommendation_mode':'source_similarity'}
    feature={'entry_id':'lnw:wd:Q1','work_id':'lnw:wd:Q1','entity_kind':'light_novel_work','scope':'work','archive_context':'historical_mixed_metadata_lead_and_plot','fresh_classification':False,'labels':[],'content_features_adopted':False}
    LightNovelCatalogue._apply_feature(row,feature)
    shown=public_work(row)
    assert shown['work_feature_context']=='historical_mixed_metadata_lead_and_plot'
    assert 'fresh_plot_only' not in json.dumps(shown)


def archived_source_fixture(work_bundle):
    source=deepcopy(work_bundle[3].evidence[0].model_dump(mode='json'))
    source['rights_policy_id']='wikipedia-screened-prose'
    source['text']='PRIVATE_RAW_NOVEL_PROSE'
    source['attribution'].pop('work_id');source['attribution'].pop('scope')
    source['attribution']['identity_proof']={'wikidata_work_id':'Q1','pageprops_wikibase_item':'Q1',
        'language':'en','page_id':42,'revision_id':91,'target_raw_path':'C:/PRIVATE/target.bin'}
    row={'entry_id':'lnw:wd:Q1','entry_kind':'work','qid':'Q1','title':'作品甲','authors':[],
        'genres':[],'recommendation_mode':'source_similarity'}
    feature={'entry_id':row['entry_id'],'work_id':row['entry_id'],'entity_kind':'light_novel_work',
        'scope':'work','archive_context':'historical_mixed_metadata_lead_and_plot',
        'fresh_classification':False,'source_attributions':[source],
        'labels':[{'label_id':'genre.fantasy','label':'Fantasy','axis':'genre','scope':'work',
            'decision':'present','evidence':[{'evidence_id':source['evidence_id'],'locator':source['locator'],
                'quote':'PRIVATE_RAW_NOVEL_PROSE','summary':'Reviewed generated summary'}]}],
        'content_features_adopted':True}
    return source,row,feature


def test_archived_exact_work_citation_displays_credit_without_private_prose(work_bundle):
    source,row,feature=archived_source_fixture(work_bundle)
    LightNovelCatalogue._apply_feature(row,feature)
    shown=public_work(row);text=json.dumps(shown,ensure_ascii=False)
    assert row['content_sources'][0]['display_allowed'] is True
    assert row['content_sources'][0]['quote_allowed'] is False
    assert len(shown['content_sources'])==1
    citation=shown['content_sources'][0]
    assert citation['source_url']==citation['permalink']==source['source_url']
    assert citation['history_url']==source['attribution']['history_url']
    assert citation['attribution']==source['attribution']['credit']
    assert citation['license']==citation['output_license']=='CC-BY-SA-4.0'
    assert shown['work_feature_context']=='historical_mixed_metadata_lead_and_plot'
    assert 'PRIVATE_RAW_NOVEL_PROSE' not in text and 'PRIVATE/target' not in text
    assert 'raw_path' not in text and 'identity_proof' not in text
    assert shown['llm_features'][0]['evidence']==''


@pytest.mark.parametrize('tamper',['target_qid','pageprops_qid','page_id','revision_id','language',
    'no_identity','wrong_context','fresh_flag','unknown_policy','series_policy','wrong_license',
    'wrong_license_url','wrong_permalink','no_obligations'])
def test_generic_archive_policy_never_grants_work_citation_without_exact_profile(work_bundle,tamper):
    source,row,feature=archived_source_fixture(work_bundle)
    proof=source['attribution']['identity_proof']
    if tamper=='target_qid':proof['wikidata_work_id']='Q9'
    elif tamper=='pageprops_qid':proof['pageprops_wikibase_item']='Q9'
    elif tamper=='page_id':proof['page_id']=99
    elif tamper=='revision_id':proof['revision_id']=99
    elif tamper=='language':proof['language']='ja'
    elif tamper=='no_identity':source['attribution'].pop('identity_proof')
    elif tamper=='wrong_context':feature['archive_context']='unknown'
    elif tamper=='fresh_flag':feature['fresh_classification']=True
    elif tamper=='unknown_policy':source['rights_policy_id']='unknown-policy'
    elif tamper=='series_policy':source['rights_policy_id']='light-novel-series-wikipedia-screened-v1'
    elif tamper=='wrong_license':source['license']='unknown'
    elif tamper=='wrong_license_url':source['license_url']='https://example.com/license'
    elif tamper=='wrong_permalink':source['attribution']['permalink']='https://example.com/article'
    else:source['inherited_restrictions']=[]
    LightNovelCatalogue._apply_feature(row,feature)
    assert row['content_sources'][0]['display_allowed'] is False
    assert row['content_sources'][0]['quote_allowed'] is False
    shown=public_work(row)
    assert shown['content_sources']==[]
    assert shown['llm_features'][0]['evidence']=='' and shown['llm_features'][0]['evidence_summary']==''


@pytest.mark.parametrize('tamper',['work_id','scope','unknown_policy','generic_policy','archive_context'])
def test_fresh_work_policy_requires_its_exact_work_attribution(work_bundle,tamper):
    source=deepcopy(work_bundle[3].evidence[0].model_dump(mode='json'))
    row={'entry_id':'lnw:wd:Q1','entry_kind':'work','qid':'Q1','title':'作品甲','authors':[],
        'genres':[],'recommendation_mode':'source_similarity'}
    feature={'entry_id':'lnw:wd:Q1','work_id':'lnw:wd:Q1','entity_kind':'light_novel_work',
        'scope':'work','labels':[],'source_attributions':[source],'content_features_adopted':False}
    if tamper=='work_id':source['attribution']['work_id']='lnw:wd:Q9'
    elif tamper=='scope':source['attribution']['scope']='series'
    elif tamper=='unknown_policy':source['rights_policy_id']='unknown'
    elif tamper=='generic_policy':source['rights_policy_id']='wikipedia-screened-prose'
    else:feature['archive_context']='historical_mixed_metadata_lead_and_plot'
    LightNovelCatalogue._apply_feature(row,feature)
    assert row['content_sources'][0]['display_allowed'] is False
    assert public_work(row)['content_sources']==[]
