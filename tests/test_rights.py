from recommend_book.rights import RightsRegistry


def test_unresolved_prose_never_becomes_allowed():
    registry = RightsRegistry.default()
    assert not registry.permits("openlibrary-prose", "description", "external_llm")
    assert not registry.permits("unknown", "synopsis", "external_llm")


def test_ccby_requires_attribution_and_wikipedia_requires_review_and_sharealike():
    registry = RightsRegistry.default()
    assert not registry.permits("ndl-iss-ndl-opac", "subject", "external_llm")
    assert registry.permits("ndl-iss-ndl-opac", "subject", "external_llm", {"attribution"})
    assert not registry.permits("ndl-iss-ndl-opac", "synopsis", "external_llm", {"attribution"})
    assert not registry.permits("wikipedia-text", "note", "external_llm", {"attribution"})
    assert registry.permits("wikipedia-text", "note", "external_llm", {"attribution", "share_alike", "reviewed_no_third_party_text"})


def test_wikidata_description_is_allowed_structured_data():
    assert RightsRegistry.default().permits("wikidata-cc0", "description", "external_llm")


def test_screened_wikipedia_is_distinct_from_human_review_policy():
    registry = RightsRegistry.default()
    automated = {"attribution", "share_alike", "mark_changes", "third_party_text_screened"}
    assert registry.permits("wikipedia-screened-prose", "synopsis", "external_llm", automated)
    assert not registry.permits("wikipedia-text", "synopsis", "external_llm", automated)
    assert not registry.permits("wikipedia-screened-prose", "synopsis", "redistribution", automated - {"mark_changes"})
    assert "human review" in registry.policies["wikipedia-screened-prose"].scope
