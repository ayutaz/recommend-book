"""One-candidate public scope assessment; never an admission/permission shortcut.

The root's primary-source review identifies Q104213567 as light novel series.
Its label/hierarchy is not claimed to occur in the separately fetched work raw.
This deliberately bounded assessment cannot admit any new runtime shape.
"""
from __future__ import annotations

import hashlib
import json

from recommend_book.ingestion.wikidata import claim_values


def _all_item_ids(entity, prop):
    return sorted({statement["mainsnak"]["datavalue"]["value"]["id"]
        for statement in entity.get("claims", {}).get(prop, [])
        if statement.get("rank") != "deprecated"
        and statement.get("mainsnak", {}).get("property") == prop
        and statement.get("mainsnak", {}).get("snaktype") == "value"
        and isinstance(statement.get("mainsnak", {}).get("datavalue", {}).get("value"), dict)
        and statement["mainsnak"]["datavalue"]["value"].get("entity-type") == "item"
        and isinstance(statement["mainsnak"]["datavalue"]["value"].get("id"), str)})


def assess_candidate(wikidata_raw, wikidata_sha256, article_raw, proposal):
    """Reproduce the exact entity/article subject and retain positive scope holds.

    Exact sitelink equality proves subject correspondence, not individual-work
    granularity, source permission, textual equivalence or genre transfer.
    Missing statements remain unknown; P136 reference facts remain assertions.
    """
    if hashlib.sha256(wikidata_raw).hexdigest() != wikidata_sha256:
        raise ValueError("Wikidata source SHA differs")
    if hashlib.sha256(article_raw).hexdigest() != proposal["source_raw_sha256"]:
        raise ValueError("Article source SHA differs")
    if (proposal["work_id"] != "public:wd:Q24873892" or proposal["public_pageid"] != 3414534
        or proposal["source_scope_candidate"] != "abstract_work"
        or proposal["recipe_to_reproduce"] != "screened-infobox-genre-v1"):
        raise ValueError("Exactly the one fixed conditional proposal is required")
    entity = json.loads(wikidata_raw)["entities"]["Q24873892"]
    if entity.get("id") != "Q24873892" or "missing" in entity:
        raise ValueError("Exact separate target entity absent")
    pages = json.loads(article_raw)["query"]["pages"]
    pages = list(pages.values()) if isinstance(pages, dict) else pages
    selected = [page for page in pages if page.get("pageid") == 3414534]
    if len(selected) != 1:
        raise ValueError("Exact article subject absent or ambiguous")
    page = selected[0]
    if (page.get("ns") != 0 or page.get("pageprops", {}).get("wikibase_item") != entity["id"]
        or entity.get("sitelinks", {}).get("jawiki", {}).get("title") != page["title"]):
        raise ValueError("Exact main-namespace page/QID/sitelink correspondence differs")
    revisions = page.get("revisions", [])
    if len(revisions) != 1 or revisions[0]["revid"] != 110997726:
        raise ValueError("Exact single article revision differs")
    text = revisions[0]["slots"]["main"]["content"]
    p31, p7937 = _all_item_ids(entity, "P31"), _all_item_ids(entity, "P7937")
    forms = sorted(set(p31 + p7937) & {"Q8261", "Q747381"})
    series = "Q104213567" in p31
    version_passages, offset = [], 0
    for line in text.splitlines(keepends=True):
        passage = line.rstrip("\r\n")
        if "加筆改稿" in passage and "オンライン小説版は続き" in passage:
            version_passages.append({"locator": f"revision:110997726;wikitext-codepoints:{offset}:{offset + len(passage)}",
                "offset_unit": "Unicode codepoints; start inclusive, end exclusive", "start": offset,
                "end": offset + len(passage), "raw_wikitext": passage,
                "text_sha256": hashlib.sha256(passage.encode()).hexdigest(),
                "scope": "Original serial was revised for commercial publication and continued after the books; no same-text claim."})
        offset += len(line)
    revised = bool(version_passages)
    holds = []
    if series:
        holds.append("wikidata_entity_is_light_novel_series")
    if not forms:
        holds.append("individual_novel_form_not_reproduced")
    if revised:
        holds.append("revised_commercial_version_not_same_serial_text")
    return {
        "version": "single-public-content-scope-assessment-v1",
        "public_work_id": proposal["work_id"], "public_pageid": 3414534,
        "exact_entity_present": True, "exact_jawiki_sitelink_match": True,
        "wikidata_raw_sha256": wikidata_sha256,
        "article_raw_sha256": proposal["source_raw_sha256"], "article_revision_id": 110997726,
        "nondeprecated_P31_qids": p31, "nondeprecated_P7937_qids": p7937,
        "P31_assertion_proofs": [{"locator": f"entities.Q24873892.claims.P31[{index}]",
            "statement_id": statement.get("id"), "raw_statement": statement}
            for index, statement in enumerate(entity.get("claims", {}).get("P31", []))
            if statement.get("rank") != "deprecated"],
        "direct_individual_novel_form_qids": forms,
        "nondeprecated_P136_assertions": [statement for statement in entity.get("claims", {}).get("P136", [])
                                          if statement.get("rank") != "deprecated"],
        "positive_version_distinction_passages": version_passages,
        "selected_P136_qids": [value["id"] for value in claim_values(entity, "P136")
                               if isinstance(value, dict) and "id" in value],
        "P136_scope": "source assertions only; imported Wikipedia reference is not independent genre corroboration",
        "series_type_interpretation": {
            "qid": "Q104213567", "label": "light novel series",
            "primary_source_url": "https://www.wikidata.org/wiki/Q104213567",
            "root_reviewed_revision": 2548737860,
            "review_basis": "root primary web review; not a type-entity acquisition in this pilot",
            "P279_reviewed_labels": ["novel series", "light novel"],
            "type_ontology_raw_acquired_here": False,
        },
        "source_scope_status": ("held_series_and_unproved_cross_version_scope" if series
                                else "held_requires_new_individual_work_scope_review"),
        "hold_codes": holds,
        "scope_decision": "Exact series/article correspondence now proved. No individual work or original-Web genre transfer admitted.",
        "declared_genre_path": "not executed: current recipe requires individual novel/light_novel form and explicit series scope clearance",
        "runtime_admitted": False, "work_inputs_constructed_n": 0,
        "usage_guard_receipts_created_n": 0, "new_permissions_claimed_n": 0,
        "model_calls": 0, "new_native_classifications": 0, "new_adopted_features": 0,
        "new_private_bindings": 0, "profile_updates": 0,
        "comparison_reruns": 0,
        "comparison_decision": "No admitted feature/profile input changed; preserve the fixed five-fold comparison without rerun or retuning.",
        "unknown_converted_to_absent": False, "original_web_specific_genre_claim": False,
        "private_history_externalized": False, "main_database_accesses": 0,
        "quality_verified": False, "human_review_claimed": False,
    }
