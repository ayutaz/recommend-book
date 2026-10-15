"""Bounded public source feasibility; these fixtures never establish permission."""
from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.personal_content_pilot_v1 import assess_candidate


def packed(value):
    return json.dumps(value, ensure_ascii=False).encode()


def fixture():
    statement = {"rank": "normal", "mainsnak": {"property": "P31", "snaktype": "value",
        "datavalue": {"value": {"entity-type": "item", "id": "Q104213567"}}}}
    entity = {"id": "Q24873892", "sitelinks": {"jawiki": {"title": "Fixture"}},
        "claims": {"P31": [statement], "P136": []}}
    article = {"query": {"pages": [{"pageid": 3414534, "ns": 0, "title": "Fixture",
        "pageprops": {"wikibase_item": "Q24873892"}, "revisions": [{"revid": 110997726,
            "slots": {"main": {"content": "同名同作を加筆改稿して書籍化。書籍終了後もオンライン小説版は続き、2017年まで連載された。"}}}]}]}}
    wd_raw, wp_raw = packed({"entities": {"Q24873892": entity}}), packed(article)
    proposal = {"work_id": "public:wd:Q24873892", "public_pageid": 3414534,
        "source_raw_sha256": hashlib.sha256(wp_raw).hexdigest(),
        "source_scope_candidate": "abstract_work", "recipe_to_reproduce": "screened-infobox-genre-v1"}
    return wd_raw, hashlib.sha256(wd_raw).hexdigest(), wp_raw, proposal


def test_series_and_revised_versions_hold_despite_exact_sitelink():
    args = fixture()
    result = assess_candidate(*args)
    assert result["exact_jawiki_sitelink_match"] is True
    assert result["nondeprecated_P31_qids"] == ["Q104213567"]
    assert result["source_scope_status"] == "held_series_and_unproved_cross_version_scope"
    assert result["hold_codes"] == ["wikidata_entity_is_light_novel_series", "individual_novel_form_not_reproduced", "revised_commercial_version_not_same_serial_text"]
    assert result["runtime_admitted"] is False
    assert result["comparison_reruns"] == 0
    assert result["unknown_converted_to_absent"] is False
    passage = result["positive_version_distinction_passages"][0]
    article = json.loads(args[2])["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    assert article[passage["start"]:passage["end"]] == passage["raw_wikitext"]


def test_separate_unrelated_lines_do_not_establish_version_context():
    args = list(fixture())
    data = json.loads(args[2])
    data["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = "引用された別の話を加筆改稿。\n別作品のオンライン小説版は続きました。"
    args[2] = packed(data)
    args[3]["source_raw_sha256"] = hashlib.sha256(args[2]).hexdigest()
    result = assess_candidate(*args)
    assert result["positive_version_distinction_passages"] == []
    assert "revised_commercial_version_not_same_serial_text" not in result["hold_codes"]


def test_deprecated_genre_is_not_reported_as_nondeprecated():
    args = list(fixture())
    data = json.loads(args[0])
    data["entities"]["Q24873892"]["claims"]["P136"] = [{"rank": "deprecated"}]
    args[0] = packed(data)
    args[1] = hashlib.sha256(args[0]).hexdigest()
    result = assess_candidate(*args)
    assert result["nondeprecated_P136_assertions"] == []


@pytest.mark.parametrize("position", [0, 2])
def test_tampered_raw_is_rejected(position):
    args = list(fixture())
    args[position] += b" "
    with pytest.raises(ValueError, match="SHA"):
        assess_candidate(*args)


def test_sitelink_mismatch_cannot_be_admitted_by_title_or_genre():
    args = list(fixture())
    data = json.loads(args[0])
    data["entities"]["Q24873892"]["sitelinks"]["jawiki"]["title"] = "Other work"
    args[0] = packed(data)
    args[1] = hashlib.sha256(args[0]).hexdigest()
    with pytest.raises(ValueError, match="sitelink"):
        assess_candidate(*args)


def test_deprecated_type_cannot_supply_positive_identity():
    args = list(fixture())
    data = json.loads(args[0])
    claims = data["entities"]["Q24873892"]["claims"]
    novel = deepcopy(claims["P31"][0])
    novel["rank"] = "deprecated"
    novel["mainsnak"]["datavalue"]["value"]["id"] = "Q8261"
    claims["P31"].append(novel)
    args[0] = packed(data)
    args[1] = hashlib.sha256(args[0]).hexdigest()
    result = assess_candidate(*args)
    assert result["direct_individual_novel_form_qids"] == []
    assert result["runtime_admitted"] is False


def test_even_direct_novel_type_does_not_override_positive_series_assertion():
    args = list(fixture())
    data = json.loads(args[0])
    claims = data["entities"]["Q24873892"]["claims"]
    novel = deepcopy(claims["P31"][0])
    novel["mainsnak"]["datavalue"]["value"]["id"] = "Q8261"
    claims["P31"].append(novel)
    args[0] = packed(data)
    args[1] = hashlib.sha256(args[0]).hexdigest()
    result = assess_candidate(*args)
    assert result["direct_individual_novel_form_qids"] == ["Q8261"]
    assert result["runtime_admitted"] is False


def test_new_unreviewed_work_shape_does_not_inherit_series_assessment():
    args = list(fixture())
    data = json.loads(args[0])
    data["entities"]["Q24873892"]["claims"]["P31"][0]["mainsnak"]["datavalue"]["value"]["id"] = "Q8261"
    args[0] = packed(data)
    args[1] = hashlib.sha256(args[0]).hexdigest()
    result = assess_candidate(*args)
    assert result["source_scope_status"] == "held_requires_new_individual_work_scope_review"
    assert result["runtime_admitted"] is False
