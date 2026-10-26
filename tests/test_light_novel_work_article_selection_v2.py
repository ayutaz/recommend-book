from copy import deepcopy
import hashlib
import json

import pytest

from test_light_novel_admission_v3 import source, claim
from test_light_novel_work_admission_v3 import work_source
from test_light_novel_work_sources_v1 import work_bundle
from recommend_book.light_novel_work_article_selection_v2 import (
    RECIPE, select_authenticated_page, validate_authenticated_selection,
)


@pytest.fixture
def batch(work_source, tmp_path):
    metadata = work_source(lambda e: e.update(sitelinks={'jawiki': {'title': '黄龍の耳'}}))
    page = {'pageid': 2184617, 'ns': 0, 'title': '黄龍の耳',
            'pageprops': {'wikibase_item': 'Q1'},
            'revisions': [{'revid': 109628378, 'slots': {'main': {
                'contentmodel': 'wikitext', 'content': '== あらすじ ==\n{{不十分なあらすじ}}\n短い本文。'}}}]}
    other = {'pageid': 7, 'ns': 0, 'title': '別作品', 'pageprops': {'wikibase_item': 'Q7'}}
    def write(pages):
        raw = json.dumps({'query': {'pages': pages}}, ensure_ascii=False).encode()
        path = tmp_path / 'full-response.json'
        path.write_bytes(raw)
        return {'path': str(path.resolve()), 'sha256': hashlib.sha256(raw).hexdigest()}
    return metadata, page, other, write


@pytest.mark.parametrize('native_dict', [False, True])
def test_native_batch_preserves_full_body_and_exact_pointer(batch, native_dict):
    metadata, page, other, write = batch
    artifact = write({'7': other, '2184617': page} if native_dict else [other, page])
    selected, receipt = select_authenticated_page(metadata, artifact, 'ja')
    assert selected == page
    assert receipt == {'recipe': RECIPE, 'raw_path': artifact['path'], 'raw_sha256': artifact['sha256'],
        'raw_page_count': 2, 'work_id': 'lnw:wd:Q1', 'page_id': 2184617,
        'page_title': '黄龍の耳', 'wikibase_item': 'Q1', 'revision_id': 109628378,
        'raw_page_locator': '/query/pages/' + ('2184617' if native_dict else '1'),
        'raw_revision_locator': '/query/pages/' + ('2184617' if native_dict else '1') + '/revisions/0'}
    assert validate_authenticated_selection(metadata, page, artifact, 'ja', receipt) == page


def test_sole_page_keeps_v1_selection_absent(batch):
    metadata, page, _, write = batch
    artifact = write([page])
    assert select_authenticated_page(metadata, artifact, 'ja') == (page, None)
    validate_authenticated_selection(metadata, page, artifact, 'ja', None)


def test_v1_sole_page_evidence_and_receipts_remain_exact(work_bundle):
    from recommend_book.light_novel_work_sources_v1 import build_article_evidence
    # The existing source fixture contains a genuinely eligible, single-page Plot.
    metadata, page, artifact, *_ = work_bundle
    before = build_article_evidence(metadata, page, artifact, 'en')
    selected, selection = select_authenticated_page(metadata, artifact, 'en')
    assert selection is None
    after = build_article_evidence(metadata, selected, artifact, 'en')
    assert before == after
    assert before[0] and all('page_selection' not in r for r in before[1])


@pytest.mark.parametrize('key', ['recipe', 'raw_path', 'raw_sha256', 'raw_page_count', 'work_id',
    'page_id', 'page_title', 'wikibase_item', 'revision_id', 'raw_page_locator', 'raw_revision_locator'])
def test_persisted_selector_cannot_be_forged(batch, key):
    metadata, page, other, write = batch
    artifact = write([other, page])
    _, receipt = select_authenticated_page(metadata, artifact, 'ja')
    receipt[key] = 'forged'
    with pytest.raises(ValueError):
        validate_authenticated_selection(metadata, page, artifact, 'ja', receipt)


@pytest.mark.parametrize('case', ['same_title', 'same_owner', 'duplicate_pageid', 'wrong_qid',
    'wrong_title', 'bool_id', 'bool_ns', 'bool_revision', 'disambiguation', 'missing', 'redirect',
    'two_revisions', 'bad_slot', 'dict_key', 'raw_mutation', 'provided_page'])
def test_ambiguous_identity_and_raw_mutation_hold(batch, case):
    metadata, page, other, write = batch
    page, other = deepcopy(page), deepcopy(other)
    if case == 'same_title': other['title'] = page['title']
    elif case == 'same_owner': other['pageprops']['wikibase_item'] = 'Q1'
    elif case == 'duplicate_pageid': other['pageid'] = page['pageid']
    elif case == 'wrong_qid': page['pageprops']['wikibase_item'] = 'Q9'
    elif case == 'wrong_title': page['title'] = '改題'
    elif case == 'bool_id': page['pageid'] = True
    elif case == 'bool_ns': page['ns'] = False
    elif case == 'bool_revision': page['revisions'][0]['revid'] = True
    elif case == 'disambiguation': page['pageprops']['disambiguation'] = ''
    elif case in ('missing', 'redirect'): page[case] = True
    elif case == 'two_revisions': page['revisions'] *= 2
    elif case == 'bad_slot': page['revisions'][0]['slots'] = []
    artifact = write({'7': other, '999': page} if case == 'dict_key' else [other, page])
    if case == 'raw_mutation':
        from pathlib import Path
        Path(artifact['path']).write_bytes(b'{}')
    with pytest.raises(ValueError):
        if case == 'provided_page':
            _, receipt = select_authenticated_page(metadata, artifact, 'ja')
            page['revisions'][0]['slots']['main']['content'] = 'invented plot'
            validate_authenticated_selection(metadata, page, artifact, 'ja', receipt)
        else:
            select_authenticated_page(metadata, artifact, 'ja')
