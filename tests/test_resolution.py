import hashlib
import json

import pytest

from recommend_book.models import Contributor, Identifier, SourceRecord, Title
from recommend_book.resolution import compare_records, compare_source_mapping


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


def source_mapping_pair(tmp_path):
    wd_path, wp_path = tmp_path / "wd.json", tmp_path / "wp.json"
    wd_path.write_text(json.dumps({"entities": {"Q1": {"id": "Q1"}}}), encoding="utf-8")
    wp_path.write_text(json.dumps({"query": {"pages": [{"pageid": 10, "title": "Snow", "pageprops": {"wikibase_item": "Q1"}, "revisions": [{"revid": 20}]}]}}), encoding="utf-8")
    wd_sha, wp_sha = (hashlib.sha256(path.read_bytes()).hexdigest() for path in (wd_path, wp_path))
    wd = record("Q1", source="wikidata", identifiers=[Identifier(source="wikidata", value="Q1", scope="work"), Identifier(source="openlibrary", value="/works/OL1W", scope="work"), Identifier(source="openlibrary", value="/works/OL2W", scope="work")], raw_path=str(wd_path), raw_sha256=wd_sha)
    wp = record("en:20:fixture", source="wikipedia", identifiers=[Identifier(source="wikidata", value="Q1", scope="work")], raw_path=str(wp_path), raw_sha256=wp_sha, extra={"page_id": 10, "revision_id": 20, "article_title": "Snow", "imported_bibliography_from": {"source_id": "Q1", "raw_path": str(wd_path), "raw_sha256": wd_sha}})
    return wd, wp


def test_derived_source_mapping_verifies_scope_without_general_guard_bypass(tmp_path):
    wd, wp = source_mapping_pair(tmp_path)
    assert compare_records(wd, wp).status == "uncertain"
    decision = compare_source_mapping(wd, wp)
    assert decision.status == "same"
    assert decision.method == "source_mapping"
    assert decision.confidence is None


@pytest.mark.parametrize("field,value", [("raw_sha256", "0" * 64), ("source_id", "Q2"), ("raw_path", "missing.json")])
def test_derived_mapping_requires_exact_immutable_bibliography_lineage(tmp_path, field, value):
    wd, wp = source_mapping_pair(tmp_path)
    lineage = {**wp.extra["imported_bibliography_from"], field: value}
    wp = wp.model_copy(update={"extra": {**wp.extra, "imported_bibliography_from": lineage}})
    assert compare_source_mapping(wd, wp).status == "uncertain"


def test_pageprops_claim_requires_correct_raw_page_and_revision(tmp_path):
    wd, wp = source_mapping_pair(tmp_path)
    payload = json.loads((tmp_path / "wp.json").read_bytes())
    payload["query"]["pages"][0]["pageprops"]["wikibase_item"] = "Q2"
    (tmp_path / "wp.json").write_text(json.dumps(payload), encoding="utf-8")
    wp = wp.model_copy(update={"raw_sha256": hashlib.sha256((tmp_path / "wp.json").read_bytes()).hexdigest(), "extra": {**wp.extra, "wikibase_item_verified": "Q1"}})
    assert compare_source_mapping(wd, wp).status == "uncertain"
    wd, wp = source_mapping_pair(tmp_path)
    assert compare_source_mapping(wd, wp.model_copy(update={"extra": {**wp.extra, "revision_id": 21}})).status == "uncertain"


@pytest.mark.parametrize("changes", [{"contributors": [Contributor(name="Other")]}, {"original_language": "ja"}, {"work_kind": "anthology"}, {"volume": "2"}])
def test_derived_mapping_preserves_bibliographic_conflict_hold(tmp_path, changes):
    wd, wp = source_mapping_pair(tmp_path)
    assert compare_source_mapping(wd, wp.model_copy(update=changes)).status != "same"


def test_derived_mapping_rejects_multiple_wikidata_work_ids(tmp_path):
    wd, wp = source_mapping_pair(tmp_path)
    ambiguous = wp.model_copy(update={"identifiers": [*wp.identifiers, Identifier(source="wikidata", value="Q2", scope="work")]})
    assert compare_source_mapping(wd, ambiguous).status == "uncertain"


def test_derived_mapping_fails_when_raw_bytes_do_not_match_hash(tmp_path):
    wd, wp = source_mapping_pair(tmp_path)
    (tmp_path / "wp.json").write_bytes(b"{}")
    assert compare_source_mapping(wd, wp).status == "uncertain"


def test_wikipedia_copy_without_lineage_is_not_an_identity_confirmation(tmp_path):
    wd, wp = source_mapping_pair(tmp_path)
    wd = wd.model_copy(update={"identifiers": [wd.identifiers[0]]})
    wp = wp.model_copy(update={"extra": {"page_id": 10, "revision_id": 20}})
    assert compare_source_mapping(wd, wp).status == "uncertain"
