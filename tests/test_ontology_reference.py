from copy import deepcopy
from datetime import datetime, timezone
import hashlib

import pytest

from test_declared_genre_subclass import ontology
from recommend_book.recommendation.ontology_reference import (
    OFFICIAL_URL, STRUCTURED_CLAUSE, admit_ontology_reference,
)
from recommend_book.rights import RightsRegistry


def prepared():
    raw = (f'<p>{STRUCTURED_CLAUSE[:-1]}.</p>'.replace(
        'Creative Commons Zero', '<a href="https://creativecommons.org/publicdomain/zero/1.0/">Creative Commons Zero</a>')).encode()
    entry = {"source_url": OFFICIAL_URL, "final_url": OFFICIAL_URL, "status_code": 200,
             "verified_at": "2026-10-15T00:00:00Z", "raw_path": "official.html",
             "raw_sha256": hashlib.sha256(raw).hexdigest()}
    return ontology("Q326439", "Q132311"), RightsRegistry.default().policies["wikidata-cc0"], [entry], {"official.html": raw}


def admit(args, usage="display", now=datetime(2026, 10, 15, 1, tzinfo=timezone.utc)):
    return admit_ontology_reference(args[0], usage, *args[1:], now=now)


def test_three_purposes_have_distinct_reference_receipts_and_complete_bound_statements():
    args = prepared()
    before = deepcopy(args)
    for usage in ("display", "local_storage", "commercial"):
        receipt = admit(args, usage)
        assert receipt["reference_material_admitted"] is True
        assert receipt["actual_usage_guard_decision"] is False
        assert receipt["source_kind"] == "ontology_reference" and receipt["catalog_work"] is False
        assert receipt["feature_adoption_authorized"] is False
        assert receipt["P279_assertions"][0]["raw_statement"]["id"] == "Q326439$edge-0"
        assert receipt["source_artifact"]["raw_sha256"] == args[0].raw_sha256
        assert receipt["official_conditions"][0]["official_entry"] == args[2][0]
    assert args == before


@pytest.mark.parametrize("change", ["hash", "url", "final_url", "status", "future", "stale", "clause", "comment", "script", "link", "policy", "purpose"])
def test_missing_or_tampered_source_rights_proof_never_receives_reference_receipt(change):
    source, policy, entries, payloads = prepared()
    entry = entries[0]
    usage = "display"
    if change == "hash":
        payloads["official.html"] += b" "
    elif change in ("url", "final_url"):
        entry["source_url" if change == "url" else "final_url"] = "https://example.org/"
    elif change == "status":
        entry["status_code"] = 404
    elif change in ("future", "stale"):
        entry["verified_at"] = "2026-10-16T00:00:00Z" if change == "future" else "2026-10-07T00:00:00Z"
    elif change == "policy":
        policy.usages["display"] = "unresolved"
    elif change == "purpose":
        usage = "external_llm"
    else:
        raw = payloads["official.html"]
        if change == "clause":
            raw = raw.replace(b"All structured data", b"Some data")
        elif change == "comment":
            raw = b"<!--" + raw + b"-->"
        elif change == "script":
            raw = b"<script>" + raw + b"</script>"
        else:
            raw = raw.replace(b"https://creativecommons.org/publicdomain/zero/1.0/", b"https://example.org/")
        payloads["official.html"] = raw
        entry["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        admit((source, policy, entries, payloads), usage)
