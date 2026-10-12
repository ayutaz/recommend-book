from recommend_book.models import Contributor, Identifier, SourceRecord, Title
from recommend_book.resolution import compare_records


def record(key, **kwargs):
    values = dict(source="fixture", source_id=key, source_url=f"https://example.test/{key}", retrieved_at="2026-10-12", provider_id="fixture", record_kind="work", titles=[Title(value="Snow")], contributors=[Contributor(name="Author")], original_language="en", work_kind="novel", raw_sha256=key, raw_path=f"raw/{key}.json", rights_policy_id="metadata")
    values.update(kwargs)
    return SourceRecord(**values)


def test_title_and_author_are_candidates_not_identity():
    assert compare_records(record("a"), record("b")).status == "uncertain"


def test_same_title_different_author_is_different():
    assert compare_records(record("a"), record("b", contributors=[Contributor(name="Other")])).status == "different"


def test_coauthors_require_full_agreement():
    identifier = Identifier(source="ol", value="W1", scope="work")
    a = record("a", identifiers=[identifier], contributors=[Contributor(name="A"), Contributor(name="B")])
    b = record("b", identifiers=[identifier], contributors=[Contributor(name="A")])
    assert compare_records(a, b).status == "uncertain"


def test_explicit_ref_and_corroborated_authors_accept_translation():
    identifier = Identifier(source="ol", value="W1", scope="work")
    a = record("a", identifiers=[identifier])
    b = record("b", record_kind="edition", work_refs=[identifier], titles=[Title(value="雪")], language="ja", contributors=[Contributor(name="Author"), Contributor(name="Translator", role="translator")])
    assert compare_records(a, b).status == "same"


def test_work_identifier_does_not_override_units_or_language():
    identifier = Identifier(source="ol", value="W1", scope="work")
    a = record("a", identifiers=[identifier], volume="1")
    assert compare_records(a, record("b", identifiers=[identifier], volume="2")).status == "different"
    assert compare_records(a, record("b", identifiers=[identifier], volume="1", original_language="ja")).status == "different"
    assert compare_records(a, record("b", identifiers=[identifier], volume="1", work_kind="anthology")).status == "different"


def test_missing_authors_require_review_even_with_work_id():
    identifier = Identifier(source="ol", value="W1", scope="work")
    assert compare_records(record("a", identifiers=[identifier]), record("b", identifiers=[identifier], contributors=[])).status == "uncertain"


def test_shared_identifier_does_not_hide_conflicting_authority_ids():
    common = Identifier(source="ol", value="W1", scope="work")
    a = record("a", identifiers=[common, Identifier(source="wikidata", value="Q1", scope="work")])
    b = record("b", identifiers=[common, Identifier(source="wikidata", value="Q2", scope="work")])
    assert compare_records(a, b).status == "uncertain"


def test_multiple_work_refs_in_same_namespace_do_not_bridge_works():
    a = record("a", identifiers=[Identifier(source="ol", value="W1", scope="work")])
    b = record("b", record_kind="edition", work_refs=[Identifier(source="ol", value="W1", scope="work"), Identifier(source="ol", value="W2", scope="work")])
    assert compare_records(a, b).status == "uncertain"


def test_distinct_original_titles_require_review_despite_external_id():
    common = Identifier(source="ol", value="W1", scope="work")
    assert compare_records(record("a", identifiers=[common]), record("b", identifiers=[common], titles=[Title(value="A different novel")])).status == "uncertain"
