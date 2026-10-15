import pytest

from recommend_book.bounded_scope_repair import entity_metadata, page_metadata, cited_evidence


def test_exact_entity_is_required_in_multi_entity_cache():
    raw = {"entities": {"Q1": {"id": "Q2", "claims": {}}}}
    with pytest.raises(ValueError, match="Exact entity"):
        entity_metadata(raw, "Q1")


def test_revision_selection_does_not_use_first_page_or_parent_revision():
    raw = {"query": {"pages": [
        {"pageid": 1, "pageprops": {"wikibase_item": "Q1"},
         "revisions": [{"revid": 10, "parentid": 20}]},
        {"pageid": 2, "pageprops": {"wikibase_item": "Q2"},
         "revisions": [{"revid": 20, "slots": {"main": {"*": "NO PROSE EXPORT"}}}]},
    ]}}
    out = page_metadata(raw, "20")
    assert out["qid"] == "Q2" and out["page_id"] == 2
    assert out["locator"] == "query.pages[1].pageprops.wikibase_item"
    assert "NO PROSE EXPORT" not in str(out)


def test_duplicate_revision_is_ambiguous_not_authority():
    raw = {"query": {"pages": [{"revisions": [{"revid": 20}]},
                                 {"revisions": [{"revid": 20}]}]}}
    with pytest.raises(ValueError, match="Exact revision"):
        page_metadata(raw, "20")


def test_compound_subject_locator_keeps_each_claim_id_rank_and_property():
    raw = {"entities": {"Q1": {"id": "Q1", "claims": {"P136": [
        {"id": "Q1$claim", "rank": "deprecated", "mainsnak": {
            "datavalue": {"value": {"id": "Q15637301"}}}}], "P921": [
        {"id": "Q1$topic", "rank": "normal", "mainsnak": {
            "datavalue": {"value": {"id": "Q2"}}}}]}}}}
    out = entity_metadata(raw, "Q1")
    assert out["claims"]["P136"][0]["rank"] == "deprecated"
    assert out["claims"]["P921"][0]["target"] == "Q2"
    assert out["claims"]["P136"][0]["locator"] == "entities.Q1.claims.P136[0]"


def test_unbound_or_locator_changed_citation_fails_closed():
    packet = {"evidence_refs": [{"evidence_id": "e1", "locator": "A"}],
              "labels": [{"label_id": "genre.fantasy", "evidence_refs": [
                  {"evidence_id": "e1", "locator": "B"}]}]}
    with pytest.raises(ValueError, match="Citation"):
        cited_evidence(packet)
