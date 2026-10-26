from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from recommend_book.light_novel_work_sources_v1 import (
    WorkInput, build_article_evidence, validate_source_admission, metadata_pins,
    digest, sha, reserve_inputs, SOURCE_VERSION, TERMS_URL, WIKIDATA_TERMS, LICENSE_URL,
)


@pytest.fixture
def work_bundle(work_source, tmp_path):
    metadata = work_source(lambda e: e.update(sitelinks={'enwiki': {'title': 'Novel Alpha'}}))
    page = {'pageid': 42, 'ns': 0, 'title': 'Novel Alpha', 'pageprops': {'wikibase_item': 'Q1'},
        'revisions': [{'revid': 91, 'slots': {'main': {'contentmodel': 'wikitext',
            'content': 'Lead paragraph that should never become model evidence.\n\n== Plot ==\nA young traveler enters a remote town to discover why its inhabitants have disappeared. She follows a trail through the forest, learns that her brother hid the villagers, and helps them return home after confronting the soldiers who drove them away.'}}}]}
    path = tmp_path / 'article.json';path.write_text(json.dumps({'query': {'pages': [page]}}), encoding='utf8')
    artifact = {'path': str(path), 'sha256': sha(path)}
    evidence, receipts, outcomes = build_article_evidence(metadata, page, artifact, 'en')
    meta = tmp_path / 'metadata.jsonl';meta.write_text(json.dumps(metadata)+'\n', encoding='utf8')
    pins = {**metadata_pins(metadata), str(path): sha(path), str(meta): sha(meta)}
    terms = {}
    for index, url in enumerate((TERMS_URL, WIKIDATA_TERMS, LICENSE_URL)):
        p = tmp_path / f'terms-{index}.html';p.write_text('Saved official terms fixture', encoding='utf8')
        terms[url] = {'path': str(p), 'sha256': sha(p)};pins[str(p)] = sha(p)
    manifest = {'version': SOURCE_VERSION, 'scope': 'work', 'metadata_path': str(meta),
        'artifact_sha256': pins, 'conditions': terms, 'works': [{'work_id': 'lnw:wd:Q1',
            'metadata_row_sha256': digest(metadata), 'articles': [{'language': 'en',
                'raw_path': str(path), 'raw_sha256': sha(path), 'outcomes': outcomes}],
            'evidence_receipts': receipts}]}
    mp = tmp_path / 'source-manifest.json';mp.write_text(json.dumps(manifest), encoding='utf8')
    work = WorkInput(work_id='lnw:wd:Q1', title=metadata['title'], authors=['著者甲'], evidence=evidence,
        source_record_ids=['wikidata:Q1'], source_manifest_sha256=sha(mp))
    return metadata,page,artifact,work,manifest,mp


def test_plot_only_work_source_replays_complete_identity_and_attribution(work_bundle):
    metadata,page,artifact,work,manifest,mp = work_bundle
    assert len(work.evidence) == 1 and work.evidence[0].kind == 'synopsis'
    item = work.evidence[0]
    assert 'Lead paragraph' not in item.text and item.attribution['scope'] == 'work'
    assert item.attribution['work_id'] == 'lnw:wd:Q1'
    assert 'series_id' not in item.attribution and item.license == 'CC-BY-SA-4.0'
    assert 'oldid=91' in item.source_url and 'action=history' in item.attribution['history_url']
    validate_source_admission([work], mp, sha(mp))


@pytest.mark.parametrize('tamper', ['pageprops', 'title', 'publisher_prose', 'lead_only', 'raw_hash',
    'structured_pin_omitted', 'evidence_text', 'scope_series', 'duplicate_work'])
def test_source_proofs_cannot_be_replaced_by_lead_publisher_or_series(work_bundle, tamper):
    metadata,page,artifact,work,manifest,mp = work_bundle
    if tamper in ('pageprops', 'title', 'raw_hash'):
        if tamper == 'pageprops':page['pageprops']['wikibase_item'] = 'Q9'
        elif tamper == 'title':page['title'] = 'Another novel'
        else:artifact['sha256'] = '0'*64
        with pytest.raises(ValueError):build_article_evidence(metadata,page,artifact,'en')
        return
    if tamper == 'lead_only':
        page['revisions'][0]['slots']['main']['content'] = 'An encyclopedia lead without a Plot section.'
        path=Path(artifact['path']);path.write_text(json.dumps({'query':{'pages':[page]}}),encoding='utf8');artifact['sha256']=sha(path)
        assert build_article_evidence(metadata,page,artifact,'en')[0] == []
        return
    if tamper == 'publisher_prose':work=work.model_copy(update={'evidence':[work.evidence[0].model_copy(update={'source_url':'https://publisher.example/product/1'})]})
    elif tamper == 'structured_pin_omitted':manifest['artifact_sha256'].pop(metadata['work_admission_proof']['type_authorities']['Q8261']['raw_path'])
    elif tamper == 'evidence_text':work=work.model_copy(update={'evidence':[work.evidence[0].model_copy(update={'text':'invented plot'})]})
    elif tamper == 'scope_series':manifest['scope']='series'
    mp.write_text(json.dumps(manifest),encoding='utf8');work=work.model_copy(update={'source_manifest_sha256':sha(mp)})
    with pytest.raises(ValueError):validate_source_admission([work,work] if tamper=='duplicate_work' else [work],mp,sha(mp))


def test_new_manifest_does_not_reset_work_reservation(work_bundle,tmp_path):
    work=work_bundle[3];ledger=tmp_path/'work-attempts'
    reserve_inputs([work],ledger,'first',prior_classification_ids=[])
    assert len(list(ledger.glob('*.json')))==1
    with pytest.raises(ValueError,match='no automatic repeat'):
        reserve_inputs([work.model_copy(update={'source_manifest_sha256':'f'*64})],ledger,'second',prior_classification_ids=[])
    assert len(list(ledger.glob('*.json')))==1
    with pytest.raises(ValueError):WorkInput(**{**work.model_dump(),'work_id':'lns:wd:Q1'})


@pytest.mark.parametrize('prior',['Q1','wd:Q1','lnw:wd:Q1','lns:wd:Q1'])
def test_world_or_ln_classification_history_blocks_fresh_work_reservation(work_bundle,tmp_path,prior):
    ledger=tmp_path/'fresh-work-attempts'
    with pytest.raises(ValueError,match='never resubmit'):
        reserve_inputs([work_bundle[3]],ledger,'new',prior_classification_ids=[prior])
    assert not list(ledger.glob('*.json'))
    # Independent history coverage is mandatory, rather than defaulting empty.
    with pytest.raises(TypeError):reserve_inputs([work_bundle[3]],ledger,'new')
