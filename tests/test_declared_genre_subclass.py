"""Bounded, raw-reproduced ontology paths are declarations, not quality gold."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from test_declared_genre import prepared, statement
from recommend_book.recommendation.declared_genre_subclass import (
    OntologySource, project_declared_genre_subclasses,
)


def ontology(qid, *targets, statements=None, extra_claims=None, edit=None):
    claims = {"P279": statements if statements is not None else [
        {**statement("P279", target, suffix=str(i)), "id": f"{qid}$edge-{i}"}
        for i, target in enumerate(targets)]}
    claims.update(extra_claims or {})
    entity = {"id": qid, "type": "item", "claims": claims,
              "labels": {"en": {"value": "Names never participate"}}}
    if edit:
        edit(entity)
    raw = json.dumps({"entities": {qid: entity}}, sort_keys=True).encode()
    return OntologySource(qid, f"https://www.wikidata.org/wiki/{qid}",
                          f"cached/{qid}.json", hashlib.sha256(raw).hexdigest(), raw)


def project(args, *sources):
    return project_declared_genre_subclasses(*args, sources)


def test_two_hops_preserve_exact_assertions_all_source_provenance_and_no_mutation():
    reference = {"hash": "reference", "snaks": {"P248": [{"snaktype": "somevalue"}]}}
    args = prepared(statement("P136", "Q116762519", references=[reference]))
    edge = {**statement("P279", "Q132311", references=[reference]),
            "id": "q326439$C5EE2588-977B-4272-8935-0C0699DF1FE7"}
    sources = [ontology("Q116762519", "Q326439"), ontology("Q326439", statements=[edge])]
    before = deepcopy((args, sources))
    result = project(args, *sources)
    assert result["version"] == "source-declared-genre-subclass-projection-v1"
    assert result["basis"] == "source_declared_P136_P279_max2_fixed13_v1"
    assert result["provisional_recommendation_features"] == {"genre.fantasy": 1.0}
    path = result["subclass_paths"][0]
    assert path["node_qids"] == ["Q116762519", "Q326439", "Q132311"]
    assert path["work_assertion"] == result["source_declared_genres"][0]
    assert path["ontology_assertions"][1]["raw_statement"] == edge
    assert path["ontology_assertions"][1]["statement_owner_normalized"] == "Q326439"
    assert path["ontology_assertions"][1]["statement_id"].startswith("q326439$")
    artifacts = result["provenance"]["source_artifacts"]
    assert {a["entity_id"] for a in artifacts} == {"Q100", "Q116762519", "Q326439"}
    assert all(a["raw_sha256"] and a["raw_path"] and a["source_url"] for a in artifacts)
    assert path["ontology_assertions"][1]["references"] == [reference]
    assert result["quality_verified"] is False and result["llm_generated"] is False
    assert result["llm_input_allowed"] is False
    assert result["raw_confidence"] is None and result["calibrated_confidence"] is None
    assert "allowed" not in result and "usage_receipt" not in result
    assert (args, sources) == before
    path["ontology_assertions"][1]["references"][0]["hash"] = "changed output"
    assert (args, sources) == before


def test_direct13_preserved_and_multiple_paths_retained_with_deduplicated_presence():
    args = prepared(statement("P136", "Q132311"), statement("P136", "Q192239", suffix="other"))
    result = project(args, ontology("Q192239", "Q136472"), ontology("Q136472", "Q1196408"))
    assert result["provisional_recommendation_features"] == {"genre.fantasy": 1.0, "genre.historical": 1.0}
    assert len(result["subclass_paths"]) == 2
    assert len(result["feature_provenance"]["genre.historical"]) == 2
    assert result["source_declared_genres"][1]["projection_status"] == "unsupported_qid"
    assert result["feature_provenance"]["genre.fantasy"][0]["basis"].endswith("correspondence_v1")


@pytest.mark.parametrize("change", ["bytes", "sha", "entity", "url", "foreign", "property", "numeric", "duplicate"])
def test_raw_identity_property_statement_and_hash_tampering_denied(change):
    source = ontology("Q200", "Q132311")
    if change == "bytes":
        source = replace(source, raw_bytes=source.raw_bytes + b" ")
    elif change == "sha":
        source = replace(source, raw_sha256="0" * 64)
    elif change == "entity":
        source = ontology("Q200", "Q132311", edit=lambda e: e.update(id="Q999"))
    elif change == "url":
        source = replace(source, source_url="https://example.org/Q200")
    else:
        def edit(entity):
            edge = entity["claims"]["P279"][0]
            if change == "foreign":
                edge["id"] = "q999$edge"
            elif change == "property":
                edge["mainsnak"]["property"] = "P31"
            elif change == "numeric":
                edge["mainsnak"]["datavalue"]["value"]["numeric-id"] = 99
        source = ontology("Q200", "Q132311", edit=edit)
    with pytest.raises(ValueError):
        project(prepared(statement("P136", "Q200")), *([source, source] if change == "duplicate" else [source]))


def test_best_rank_qualifiers_deprecation_and_unknown_are_not_silently_skipped():
    edges = [
        {**statement("P279", "Q132311"), "id": "Q200$normal"},
        {**statement("P279", "Q182015", rank="preferred", qualifiers={"P518": []},
                     **{"qualifiers-order": ["P518"]}), "id": "Q200$preferred"},
        {**statement("P279", "Q16575965", rank="deprecated"), "id": "Q200$deprecated"},
    ]
    result = project(prepared(statement("P136", "Q200")), ontology("Q200", statements=edges))
    assert result["provisional_recommendation_features"] == {}
    audited = result["ontology_reviews"][0]["assertions"]
    assert [a["selection_status"] for a in audited] == ["superseded_by_preferred", "held_qualifiers", "deprecated"]
    assert audited[1]["qualifiers_order"] == ["P518"]
    edges[1]["mainsnak"] = {"property": "P279", "snaktype": "somevalue"}
    edges[1].pop("qualifiers")
    result = project(prepared(statement("P136", "Q200")), ontology("Q200", statements=edges))
    assert result["provisional_recommendation_features"] == {}
    assert result["ontology_reviews"][0]["assertions"][1]["selection_status"] == "unknown_value"
    edges[1]["rank"] = "invented"
    with pytest.raises(ValueError):
        project(prepared(statement("P136", "Q200")), ontology("Q200", statements=edges))


def test_nonselected_and_qualified_work_p136_do_not_start_paths():
    args = prepared(statement("P136", "Q200"), statement("P136", "Q300", rank="preferred", suffix="p",
                     qualifiers={"P518": []}))
    result = project(args, ontology("Q200", "Q132311"), ontology("Q300", "Q182015"))
    assert result["provisional_recommendation_features"] == {}
    assert result["subclass_paths"] == []


def test_missing_p31_only_cycles_and_more_than_two_hops_never_create_features():
    args = prepared(statement("P136", "Q200"))
    cases = [(),
             (ontology("Q200", extra_claims={"P31": [statement("P31", "Q132311")]}),),
             (ontology("Q200", "Q300"), ontology("Q300", "Q200")),
             (ontology("Q200", "Q300"), ontology("Q300", "Q400"), ontology("Q400", "Q132311"))]
    for sources in cases:
        result = project(args, *sources)
        assert result["provisional_recommendation_features"] == {}
        assert result["subclass_paths"] == []
    assert project(args)["path_holds"][0]["reason"] == "missing_ontology_source"
    assert any(h["reason"] == "cycle" for h in project(args, *cases[2])["path_holds"])
    assert any(h["reason"] == "max_hops" for h in project(args, *cases[3])["path_holds"])


@pytest.mark.parametrize("qid", ["Q53911753", "Q119429258", "Q15637301"])
def test_forbidden_isekai_and_medium_nodes_hold_even_if_explicit_subclasses(qid):
    result = project(prepared(statement("P136", qid)), ontology(qid, "Q132311"))
    assert result["provisional_recommendation_features"] == {}
    assert any(h["reason"] == "excluded_isekai_or_medium" for h in result["path_holds"])
    result = project(prepared(statement("P136", "Q200")), ontology("Q200", qid), ontology(qid, "Q132311"))
    assert result["provisional_recommendation_features"] == {}


def test_scope_edition_title_work_raw_and_work_foreign_statement_still_rejected():
    args = prepared(statement("P136", "Q200"))
    args[2]["holds"] = ["adaptation unresolved"]
    with pytest.raises(ValueError, match="scope"):
        project(args, ontology("Q200", "Q132311"))
    with pytest.raises(ValueError, match="scope"):
        project(prepared(statement("P136", "Q200"), extra_claims={"P629": [statement("P629", "Q500")]}))
    args = prepared(statement("P136", "Q200"))
    args[1].titles[0].value = "Unverified title"
    with pytest.raises(ValueError, match="title"):
        project(args)
    raw, record, scope = prepared(statement("P136", "Q200"))
    with pytest.raises(ValueError, match="SHA256"):
        project((raw + b" ", record, scope))
    bad = statement("P136", "Q200")
    bad["id"] = "Q999$foreign"
    with pytest.raises(ValueError, match="identity"):
        project(prepared(bad))


def test_preferred_edge_wins_and_preserves_nonselected_raw_claims():
    normal = {**statement("P279", "Q132311"), "id": "Q200$normal"}
    preferred = {**statement("P279", "Q182015", rank="preferred"), "id": "Q200$preferred"}
    result = project(prepared(statement("P136", "Q200")), ontology("Q200", statements=[normal, preferred]))
    assert result["provisional_recommendation_features"] == {"genre.thriller": 1.0}
    assert result["ontology_reviews"][0]["assertions"][0]["raw_statement"] == normal
    assert result["subclass_paths"][0]["ontology_assertions"][0]["raw_statement"] == preferred


def test_duplicate_statement_identity_and_malformed_raw_structures_are_rejected():
    edge = {**statement("P279", "Q132311"), "id": "Q200$duplicate"}
    with pytest.raises(ValueError, match="duplicate"):
        project(prepared(statement("P136", "Q200")), ontology("Q200", statements=[edge, deepcopy(edge)]))
    source = ontology("Q200")
    for payload in [[], {"entities": []}, {"entities": {"Q200": {"id": "Q200", "claims": []}}}]:
        raw = json.dumps(payload).encode()
        bad = replace(source, raw_bytes=raw, raw_sha256=hashlib.sha256(raw).hexdigest())
        with pytest.raises(ValueError):
            project(prepared(statement("P136", "Q200")), bad)


def test_item_numeric_binding_applies_to_work_p136_as_well_as_ontology():
    edge = statement("P136", "Q200")
    edge["mainsnak"]["datavalue"]["value"]["numeric-id"] = 201
    with pytest.raises(ValueError, match="numeric-id"):
        project(prepared(edge), ontology("Q200", "Q132311"))
