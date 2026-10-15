"""Source assertions are provisional recommendation references, never quality gold."""
from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.models import Contributor, SourceRecord, Title
from recommend_book.recommendation.declared_genre import (
    DECLARED_GENRE_MAPPING,
    project_declared_genres,
)


def statement(prop, qid=None, *, rank="normal", suffix="1", **extra):
    snak = {"property": prop, "snaktype": "value", "datatype": "wikibase-item",
            "datavalue": {"type": "wikibase-entityid", "value": {"entity-type": "item", "id": qid}}}
    return {"id": f"Q100${prop}-{suffix}", "type": "statement", "rank": rank,
            "mainsnak": snak, **extra}


def prepared(*genres, extra_claims=None, entity_edit=None):
    entity = {"id": "Q100", "labels": {"en": {"language": "en", "value": "Public novel"}},
              "aliases": {}, "claims": {"P31": [statement("P31", "Q8261")],
                                          "P136": list(genres or [statement("P136", "Q132311")])}}
    entity["claims"].update(extra_claims or {})
    if entity_edit:
        entity_edit(entity)
    raw = json.dumps({"entities": {"Q100": entity}}, sort_keys=True).encode()
    sha = hashlib.sha256(raw).hexdigest()
    record = SourceRecord(source="wikidata", provider_id="wikidata", source_id="Q100",
                          source_url="https://www.wikidata.org/wiki/Q100", record_kind="work",
                          retrieved_at="2026-10-15T00:00:00Z", work_kind="novel",
                          titles=[Title(value="Public novel", language="en")],
                          raw_path="unused-local-path.json", raw_sha256=sha,
                          rights_policy_id="wikidata-cc0", extra={"claims": deepcopy(entity["claims"])})
    scope = {"source_qid": "Q100", "raw_sha256": sha, "work_kind": "novel",
             "scope": "original_work", "holds": [], "review_basis": "Fixture scope review only",
             "reviewed_scopes": {key: "clear" for key in ("version", "series_entity", "collection", "adaptation")}}
    return raw, record, scope


def project(args, mapping=DECLARED_GENRE_MAPPING):
    raw, record, scope = args
    return project_declared_genres(raw, record, mapping, scope)


def test_projection_is_traceable_not_quality_or_permission_and_inputs_unchanged():
    reference = {"hash": "source-reference", "snaks": {"P248": [{"snaktype": "somevalue"}]}}
    args = prepared(statement("P136", "Q132311", references=[reference]))
    before = deepcopy(args)
    result = project(args)
    assert result["provisional_recommendation_features"] == {"genre.fantasy": 1.0}
    assertion = result["source_declared_genres"][0]
    assert assertion["genre_qid"] == "Q132311"
    assert assertion["statement_id"] == "Q100$P136-1"
    assert assertion["locator"] == "entities.Q100.claims.P136[0]"
    assert assertion["references"] == [reference]
    assert result["quality_verified"] is False and result["llm_generated"] is False
    assert result["raw_confidence"] is None and result["calibrated_confidence"] is None
    assert result["basis"] == "source_declared_P136_qid_name_correspondence_v1"
    assert result["provenance"]["raw_sha256"] == args[1].raw_sha256
    assert result["provenance"]["scope_decision"] == args[2]
    assert "allowed" not in result and "usage_receipt" not in result
    assert args == before
    assertion["references"][0]["hash"] = "changed output"
    assert args == before


def test_modified_raw_fails_before_projection():
    raw, record, scope = prepared()
    with pytest.raises(ValueError, match="raw SHA256"):
        project((raw + b" ", record, scope))


def test_entity_source_and_cached_claims_must_match():
    for field, value in [("source_id", "Q999"), ("source", "other"), ("provider_id", "other")]:
        raw, record, scope = prepared()
        setattr(record, field, value)
        with pytest.raises(ValueError):
            project((raw, record, scope))
    raw, record, scope = prepared()
    record.extra["claims"]["P136"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q24925"
    with pytest.raises(ValueError, match="claims"):
        project((raw, record, scope))
    args = prepared(entity_edit=lambda entity: entity.update(id="Q999"))
    with pytest.raises(ValueError, match="entity"):
        project(args)


def test_rank_selection_retains_superseded_assertions_and_preferred_unknown():
    args = prepared(statement("P136", "Q24925", suffix="normal"),
                    statement("P136", "Q132311", rank="preferred", suffix="preferred"),
                    statement("P136", "Q182015", rank="deprecated", suffix="deprecated"))
    result = project(args)
    assert result["provisional_recommendation_features"] == {"genre.fantasy": 1.0}
    assert [a["selected_by_rank"] for a in result["source_declared_genres"]] == [False, True, False]
    unknown = statement("P136", rank="preferred", suffix="unknown")
    unknown["mainsnak"] = {"property": "P136", "snaktype": "somevalue"}
    result = project(prepared(statement("P136", "Q132311"), unknown))
    assert result["provisional_recommendation_features"] == {}
    assert result["source_declared_genres"][1]["snaktype"] == "somevalue"
    for invalid in [statement("P136", "Q132311", rank="invented"),
                    statement("P921", "Q132311")]:
        with pytest.raises(ValueError, match="property or rank"):
            project(prepared(invalid))


def test_p921_is_never_a_genre_even_if_mapped_and_subject_names_are_ignored():
    args = prepared(statement("P136", "Q999999"), extra_claims={"P921": [statement("P921", "Q132311")]})
    args[1].subjects = ["fantasy", "science fiction"]
    result = project(args)
    assert result["provisional_recommendation_features"] == {}
    assert [a["genre_qid"] for a in result["source_declared_genres"]] == ["Q999999"]


def test_qualifiers_and_unknown_values_are_preserved_without_unconditional_features():
    qualifiers = {"P518": [{"property": "P518", "snaktype": "somevalue"}]}
    unknown = statement("P136", suffix="unknown")
    unknown["mainsnak"] = {"property": "P136", "snaktype": "novalue"}
    result = project(prepared(statement("P136", "Q132311", qualifiers=qualifiers,
                                        **{"qualifiers-order": ["P518"]}), unknown))
    assert result["provisional_recommendation_features"] == {}
    first, second = result["source_declared_genres"]
    assert first["qualifiers"] == qualifiers and first["qualifiers_order"] == ["P518"]
    assert first["projection_status"] == "held_qualifiers"
    assert second["projection_status"] == "unknown_value" and second["genre_qid"] is None


def test_scope_must_be_bound_explicit_and_free_of_holds():
    for key, value in [("source_qid", "Q2"), ("raw_sha256", "0" * 64),
                       ("scope", "series"), ("holds", ["version pending"]),
                       ("work_kind", "light_novel"), ("review_basis", "")]:
        args = prepared()
        args[2][key] = value
        with pytest.raises(ValueError, match="scope"):
            project(args)
    for dimension in ("version", "series_entity", "collection", "adaptation"):
        args = prepared()
        args[2]["reviewed_scopes"][dimension] = "unknown"
        with pytest.raises(ValueError, match="scope"):
            project(args)


def test_raw_version_or_edition_cannot_be_cleared_by_scope_receipt():
    for claims in [{"P629": [statement("P629", "Q200")]},
                   {"P31": [statement("P31", "Q8261", rank="preferred"),
                             statement("P31", "Q3331189", suffix="edition")]}]:
        with pytest.raises(ValueError, match="scope"):
            project(prepared(extra_claims=claims))


def test_every_title_reproduces_from_labels_aliases_or_p1476():
    def edit(entity):
        entity["aliases"] = {"ja": [{"language": "ja", "value": "Alias"}]}
        title = statement("P1476")
        title["mainsnak"]["datavalue"] = {"type": "monolingualtext", "value": {"text": "Published", "language": "en"}}
        entity["claims"]["P1476"] = [title]
    args = prepared(entity_edit=edit)
    args[1].titles.extend([Title(value="Alias", language="ja"), Title(value="Published", language="en")])
    assert project(args)["metadata"]["title"] == "Public novel"
    args[1].titles.append(Title(value="Invented name", language="en"))
    with pytest.raises(ValueError, match="title"):
        project(args)


def test_only_frozen_map_and_unambiguous_qids_project():
    assert len(DECLARED_GENRE_MAPPING) == 13
    result = project(prepared(statement("P136", "Q738473"), statement("P136", "Q999999", suffix="unsupported")))
    assert result["provisional_recommendation_features"] == {}
    assert [a["projection_status"] for a in result["source_declared_genres"]] == ["ambiguous_qid", "unsupported_qid"]
    changed = dict(DECLARED_GENRE_MAPPING, Q738473="genre.romance")
    with pytest.raises(ValueError, match="mapping"):
        project(prepared(), changed)
    with pytest.raises(TypeError):
        DECLARED_GENRE_MAPPING["Q738473"] = "genre.romance"


def test_light_novel_supported_but_source_kind_and_raw_form_must_agree():
    args = prepared(extra_claims={"P31": [statement("P31", "Q747381")]})
    args[1].work_kind = args[2]["work_kind"] = "light_novel"
    assert project(args)["metadata"]["work_kind"] == "light_novel"
    args[1].record_kind = "edition"
    with pytest.raises(ValueError):
        project(args)
    with pytest.raises(ValueError, match="form"):
        project(prepared(extra_claims={"P31": [statement("P31", "Q49084")]}))


def test_metadata_author_ids_use_raw_rank_not_contributor_names_and_membership_is_not_entity_scope():
    args = prepared(extra_claims={"P50": [statement("P50", "Q10"),
                                        statement("P50", "Q20", rank="preferred", suffix="preferred")],
                                  "P179": [statement("P179", "Q30")],
                                  "P747": [statement("P747", "Q40")]})
    args[1].contributors = [Contributor(name="Unverified Author Name")]
    result = project(args)
    assert result["metadata"]["author_ids"] == ["Q20"]
    assert "authors" not in result["metadata"]
    assert result["provisional_recommendation_features"] == {"genre.fantasy": 1.0}
    assert "Unverified Author Name" not in json.dumps(result)
