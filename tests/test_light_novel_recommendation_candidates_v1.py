from copy import deepcopy

import pytest

from test_light_novel_catalogue_v1 import setup_catalogue, edition_bundle, membership_bundle
from recommend_book.recommendation.scale_site_v1 import public_work


@pytest.mark.parametrize('liked_edition', [False, True])
def test_unlinked_editions_are_projected_only_if_liked(edition_bundle, monkeypatch, liked_edition):
    cat, records, edition=edition_bundle
    records=deepcopy(records)
    for record in records:
        record['provenance']['conditions_sha256']=edition['provenance']['conditions_sha256']
    assert cat.ingest(records)['inserted']==3
    assert cat.import_editions([edition])['inserted']==1
    identity='lne:ndl:'+edition['id']
    liked=[identity] if liked_edition else ['lns:wd:Q1']
    rows=cat._rows
    with monkeypatch.context() as old:
        def full(**kwargs):
            kwargs.pop('recommendation_liked_ids',None)
            return rows(**kwargs)
        old.setattr(cat,'_rows',full)
        before=cat.recommend_preferences(liked_entry_ids=liked)
    projected=[]
    project=cat._project
    def observe(record,cache):
        projected.append(record.get('id'))
        return project(record,cache)
    monkeypatch.setattr(cat,'_project',observe)
    assert cat.recommend_preferences(liked_entry_ids=liked)==before
    assert (edition['id'] in projected) is liked_edition


def test_linked_read_edition_excludes_parent_and_preserves_full_result(membership_bundle, monkeypatch):
    cat,series,edition,manifest,pin=membership_bundle
    assert cat.resolve_edition_series_memberships(manifest,pin)['linked']==1
    identity='lne:ndl:'+edition['id']
    cat.set_read_status(identity,'read')
    rows=cat._rows
    selected=rows(include_features=False,recommendation_liked_ids=[])
    assert {r['entry_id'] for r in selected}=={'lns:wd:Q1',identity}
    assert next(r for r in selected if r['entry_id']==identity)['series_entry_id']=='lns:wd:Q1'
    before=cat.recommend_preferences(preferred_features=['genre.fantasy'])
    def full(**kwargs):
        kwargs.pop('recommendation_liked_ids',None)
        return rows(**kwargs)
    monkeypatch.setattr(cat,'_rows',full)
    assert cat.recommend_preferences(preferred_features=['genre.fantasy'])==before
    assert before['items']==[]


def test_public_series_content_mode_is_preserved_and_unknown_modes_rejected(setup_catalogue):
    cat,records=setup_catalogue
    cat.ingest(records)
    row=cat.work_detail('lns:wd:Q1')
    assert public_work({**row,'recommendation_mode':'series_content_preferences'})['recommendation_mode']=='series_content_preferences'
    assert public_work({**row,'recommendation_mode':'untrusted-mode'})['recommendation_mode']=='insufficient_evidence'
