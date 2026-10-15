"""Scope conflicts must stop new candidate preparation before any model call."""
import importlib.util
from pathlib import Path

from recommend_book.models import Contributor, SourceRecord, Title

spec = importlib.util.spec_from_file_location("prepare_world_pilot_v1", Path(__file__).parents[1] / "scripts/prepare_world_pilot_v1.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def statement(qid, rank="normal"):
    return {"rank": rank, "mainsnak": {"datavalue": {"value": {"id": qid}}}}


def novel():
    return SourceRecord(source="wikidata", source_id="Q100", source_url="https://www.wikidata.org/wiki/Q100",
        retrieved_at="2026-10-15T00:00:00+00:00", provider_id="wikidata", record_kind="work",
        titles=[Title(value="A novel")], contributors=[Contributor(name="An author")], work_kind="novel",
        raw_sha256="0" * 64, raw_path="unused", rights_policy_id="wikidata-cc0")


def test_normal_edition_conflict_is_not_hidden_by_preferred_novel():
    entity = {"claims": {"P31": [statement("Q8261", "preferred"), statement("Q3331189")]}}
    assert "edition_assertion" in module.scope_issues(novel(), entity)


def test_normal_version_relation_and_series_scope_are_held():
    entity = {"claims": {"P31": [statement("Q8261")], "P629": [statement("Q200")]}}
    assert "edition_assertion" in module.scope_issues(novel(), entity)
    entity["claims"].pop("P629")
    entity["claims"]["P31"].append(statement("Q1667921"))
    assert "short_story_or_series_assertion" in module.scope_issues(novel(), entity)


def test_verified_form_does_not_promote_unlabelled_author_or_title():
    record = novel()
    record.contributors = [Contributor(name="Q123")]
    record.titles = [Title(value="Q100")]
    issues = module.scope_issues(record, {"claims": {"P7937": [statement("Q8261")]}})
    assert "human_title_absent" in issues
    assert "author_name_not_available" in issues
