"""Full cached envelopes reach only exact work Plot evidence and frozen replay."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_sources_v1 import work_bundle
from test_light_novel_work_runner_v1 import history, FAKE_CLI
from recommend_book.light_novel_work_article_selection_v2 import select_authenticated_page
from recommend_book.light_novel_work_sources_v1 import (
    BATCH_SOURCE_VERSION, SOURCE_VERSION, build_article_evidence, validate_source_admission, sha,
)
import recommend_book.light_novel_work_classification_v1 as runner


@pytest.fixture
def batch_source(work_bundle):
    metadata,page,artifact,work,manifest,mp=work_bundle
    unrelated=deepcopy(page);unrelated.update(pageid=999,title='Unrelated article',pageprops={'wikibase_item':'Q9'})
    path=Path(artifact['path']);path.write_text(json.dumps({'query':{'pages':[unrelated,page]}}),encoding='utf8')
    artifact['sha256']=sha(path)
    selected,selection=select_authenticated_page(metadata,artifact,'en')
    evidence,receipts,outcomes=build_article_evidence(metadata,selected,artifact,'en')
    manifest['version']=BATCH_SOURCE_VERSION
    manifest['artifact_sha256'][str(path)]=sha(path)
    manifest['works'][0]['articles'][0].update(raw_sha256=sha(path),outcomes=outcomes,page_selection=selection)
    manifest['works'][0]['evidence_receipts']=receipts
    mp.write_text(json.dumps(manifest),encoding='utf8')
    work=work.model_copy(update={'evidence':evidence,'source_manifest_sha256':sha(mp)})
    return metadata,selected,artifact,work,manifest,mp,selection


def test_v2_bundle_preserves_complete_batch_and_selects_only_exact_novel(batch_source):
    metadata,page,artifact,work,manifest,mp,selection=batch_source
    assert selection['raw_page_locator']=='/query/pages/1' and selection['raw_page_count']==2
    assert selection['page_id']==page['pageid'] and selection['wikibase_item']=='Q1'
    assert len(json.loads(Path(artifact['path']).read_bytes())['query']['pages'])==2
    assert work.evidence[0].attribution['page_selection']==selection
    assert manifest['works'][0]['evidence_receipts'][0]['page_selection']==selection
    assert 'Unrelated article' not in work.evidence[0].text
    validate_source_admission([work],mp,sha(mp))


@pytest.mark.parametrize('tamper',['v1_manifest','missing_article_selector','forged_pointer',
    'forged_revision','omitted_receipt_selector','invented_selected_prose'])
def test_batch_recipe_and_all_persisted_selection_layers_are_replayed(batch_source,tamper):
    metadata,page,artifact,work,manifest,mp,selection=batch_source
    article=manifest['works'][0]['articles'][0]
    if tamper=='v1_manifest':manifest['version']=SOURCE_VERSION
    elif tamper=='missing_article_selector':article.pop('page_selection')
    elif tamper=='forged_pointer':article['page_selection']={**selection,'raw_page_locator':'/query/pages/0'}
    elif tamper=='forged_revision':article['page_selection']={**selection,'revision_id':999}
    elif tamper=='omitted_receipt_selector':manifest['works'][0]['evidence_receipts'][0].pop('page_selection')
    else:work=work.model_copy(update={'evidence':[work.evidence[0].model_copy(update={'text':'Invented novel plot'})]})
    mp.write_text(json.dumps(manifest),encoding='utf8');work=work.model_copy(update={'source_manifest_sha256':sha(mp)})
    with pytest.raises(ValueError):validate_source_admission([work],mp,sha(mp))


def test_v2_source_executes_only_fake_cli_and_replays_archived_selector(batch_source,history,tmp_path,monkeypatch):
    work=batch_source[3];manifest=batch_source[5];_,_,runs,ledger,capture=history;hp,hpin,_=capture()
    archive=runner.freeze_runtime(tmp_path/'runtime')
    assert 'src/recommend_book/light_novel_work_article_selection_v2.py' in json.loads(
        (archive/'light-novel-work-runtime-snapshot.json').read_bytes())['files']
    monkeypatch.setattr(runner,'ROOT',archive)
    executable=tmp_path/'fake-cli.py';executable.write_text(FAKE_CLI,encoding='utf8')
    monkeypatch.setattr(runner,'resolve_executable',lambda value:[sys.executable,str(executable)])
    result=runner.run_window([work],runs/'batch-window',source_manifest=manifest,source_sha256=sha(manifest),
        history_manifest=hp,history_sha256=hpin,reservations=ledger,timeout=30)
    monkeypatch.setattr(runner,'ROOT',Path(__file__).resolve().parents[1])
    verified=runner.verify_feature_export(result['manifest_path'],result['manifest_sha256'])
    assert verified['rows'][0]['scope']=='work'
    assert verified['rows'][0]['source_attributions'][0]['attribution']['page_selection']['raw_page_count']==2
    from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
    from recommend_book.recommendation.scale_site_v1 import public_work
    catalogue=LightNovelCatalogue(tmp_path/'batch-features.sqlite')
    assert catalogue.ingest([batch_source[0]])['inserted']==1
    assert catalogue.import_features(result['manifest_path'],result['manifest_sha256'])['imported']==1
    shown=public_work(catalogue.work_detail('lnw:wd:Q1'))
    text=json.dumps(shown,ensure_ascii=False)
    assert shown['entry_kind']=='work' and shown['recommendation_mode']=='work_content_preferences'
    assert 'raw_path' not in text and str(tmp_path) not in text and 'page_selection' not in text
    assert work.evidence[0].text not in text
