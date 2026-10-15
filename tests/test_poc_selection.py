from recommend_book.models import Contributor, SourceRecord, Title
from recommend_book.poc import select_works, render_linked_evidence


def record(n, language, year):
    return SourceRecord(source="wikidata", source_id=f"Q{n}", source_url=f"https://www.wikidata.org/wiki/Q{n}", retrieved_at="2026-10-12T00:00:00Z", record_kind="work", provider_id="wikidata", titles=[Title(value=f"Book {n}")], contributors=[Contributor(name=f"Author {n}")], original_language=language, first_publication_year=year, rights_policy_id="wikidata-cc0", raw_sha256="a" * 64, raw_path="fixture")


def test_selection_is_deterministic_and_original_language_stratified():
    records = [record(n, ["ja", "en", "fr", "es"][n % 4], 1800 + n % 230) for n in range(1, 101)]
    selected, report = select_works(records, target=20, seed=123)
    repeated, _ = select_works(list(reversed(records)), target=20, seed=123)
    assert [r.source_id for r in selected] == [r.source_id for r in repeated]
    assert len(selected) == 20
    assert report["original_language_groups"] == {"ja": 7, "en": 7, "other": 6}
    assert len({r.source_id for r in selected}) == 20


def test_insufficient_stratum_is_disclosed_instead_of_relabelled():
    rows = [record(n, "en", 1900) for n in range(1, 11)]
    selected, report = select_works(rows, target=10)
    assert len(selected) == 10
    assert report["deficits_before_redistribution"]["ja"] > 0
    assert report["deficits_before_redistribution"]["other"] > 0
    assert all(r.original_language == "en" for r in selected)


def test_duplicate_authority_records_do_not_increase_work_count():
    a = record(1, "ja", 1900)
    selected, report = select_works([a, a.model_copy(deep=True)], target=10)
    assert len(selected) == 1
    assert report["candidate_duplicate_ids"] == 1
