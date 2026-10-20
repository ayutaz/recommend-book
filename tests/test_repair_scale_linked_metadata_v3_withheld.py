import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("repair_scale_linked_metadata_v3", ROOT / "scripts" / "repair_scale_linked_metadata_v3_withheld_v1.py")
withheld = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(withheld)


def _claim(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def test_strip_optional_keeps_contributors_descriptions_and_wp_narrative():
    row = {
        "source_id": "Q100", "raw_path": "raw", "raw_sha256": "sha",
        "contributors": [{"name": "Author", "identifiers": [{"scope": "agent", "value": "Q101"}]}],
        "region": ["Q29"], "series": "Fixture Series", "subjects": ["Genre"],
        "evidence": [
            {"evidence_id": "wd:subject", "kind": "subject", "llm_input_allowed": True, "rights_policy_id": "wikidata-cc0", "locator": "claims.P136;claims.P921"},
            {"evidence_id": "wd:description", "kind": "description", "llm_input_allowed": True, "rights_policy_id": "wikidata-cc0", "locator": "descriptions.en"},
            {"evidence_id": "wp:narrative", "kind": "synopsis", "llm_input_allowed": True, "rights_policy_id": "wikipedia-screened-prose", "locator": "revision:1;section:Plot"},
        ],
        "extra": {"claims": {
            "P495": [_claim("Q29")], "P179": [_claim("Q102")],
            "P136": [_claim("Q103")], "P921": [_claim("Q104")],
            "subject_qids": ["Q103", "Q104"], "series_qids": ["Q102"],
        }},
    }
    candidate, withheld_rows, _ = withheld.strip_optional(row, {"Q29", "Q102", "Q103", "Q104"}, {"Q101": {"label": "Author"}})

    assert candidate["region"] == []
    assert candidate["series"] is None
    assert candidate["subjects"] == []
    assert {item["evidence_id"] for item in candidate["evidence"]} == {"wd:description", "wp:narrative"}
    assert candidate["contributors"] == row["contributors"]
    assert candidate["extra"]["subject_qids"] == []
    assert candidate["extra"]["series_qids"] == []
    assert {item["field"] for item in withheld_rows} == {"region", "series", "subjects"}


def test_canonicalize_uses_saved_labels_before_withholding(tmp_path):
    import hashlib
    import json

    raw = {"entities": {"Q100": {
        "id": "Q100", "labels": {"en": {"language": "en", "value": "Fixture"}},
        "descriptions": {"en": {"language": "en", "value": "fixture novel"}},
        "claims": {
            "P31": [_claim("Q8261")], "P7937": [_claim("Q8261")],
            "P50": [_claim("Q101")], "P98": [_claim("Q102")],
            "P495": [_claim("Q29")], "P179": [_claim("Q104")],
            "P136": [_claim("Q103")],
        },
    }, **{
        qid: {"id": qid, "labels": {"en": {"language": "en", "value": label}}}
        for qid, label in (("Q101", "Author One"), ("Q102", "Editor Two"), ("Q29", "Spain"), ("Q103", "Novel"))
    }}}
    raw_path = tmp_path / "work.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    row = {
        "source": "wikidata", "source_id": "Q100", "source_url": "https://www.wikidata.org/wiki/Q100",
        "retrieved_at": "2026-01-01T00:00:00+00:00", "provider_id": "wikidata", "record_kind": "work",
        "titles": [{"value": "Fixture", "language": "en", "kind": "preferred"}],
        "contributors": [
            {"name": "Q101", "role": "author", "identifiers": [{"source": "wikidata", "value": "Q101", "scope": "agent"}]},
            {"name": "Q102", "role": "editor", "identifiers": [{"source": "wikidata", "value": "Q102", "scope": "agent"}]},
        ],
        "region": ["Q29"], "series": "Q104", "subjects": ["Q103"],
        "evidence": [
            {"evidence_id": "wd:subject", "kind": "subject", "text": "Q103", "source_url": "https://www.wikidata.org/wiki/Q100", "rights_policy_id": "wikidata-cc0", "llm_input_allowed": True, "locator": "claims.P136;claims.P921"},
            {"evidence_id": "wd:description", "kind": "description", "text": "fixture novel", "source_url": "https://www.wikidata.org/wiki/Q100", "rights_policy_id": "wikidata-cc0", "llm_input_allowed": True, "language": "en", "locator": "descriptions.en"},
            {"evidence_id": "wp:narrative", "kind": "synopsis", "text": "Narrative", "source_url": "https://en.wikipedia.org/wiki/Fixture", "rights_policy_id": "wikipedia-screened-prose", "llm_input_allowed": True, "locator": "revision:1;section:Plot"},
        ],
        "raw_sha256": raw_sha, "raw_path": str(raw_path), "rights_policy_id": "wikidata-cc0",
        "extra": {"claims": raw["entities"]["Q100"]["claims"]},
    }
    proofs = {qid: {"label": label, "raw_path": str(raw_path), "raw_sha256": raw_sha, "locator": f"entities.{qid}.labels.en"}
              for qid, label in (("Q101", "Author One"), ("Q102", "Editor Two"), ("Q29", "Spain"), ("Q103", "Novel"))}
    normalized = withheld.canonicalize(row, proofs)
    candidate, held, _ = withheld.strip_optional(normalized, {"Q104"}, proofs)
    names = {item["identifiers"][0]["value"]: item["name"] for item in candidate["contributors"]}
    assert names == {"Q101": "Author One", "Q102": "Editor Two"}
    assert candidate["series"] is None
    assert any(item["kind"] == "subject" and item["text"] == "Novel" for item in candidate["evidence"])
    assert any(item["kind"] == "synopsis" for item in candidate["evidence"])
    assert {item["field"] for item in held} == {"series"}


def test_existing_output_namespace_is_not_rewritten(tmp_path, monkeypatch):
    output = tmp_path / "sealed-source"
    output.mkdir()
    record = output / "withheld-source-records.jsonl"
    record.write_text("original source bytes\n", encoding="utf-8")
    monkeypatch.setattr(withheld, "OUTPUT", output)
    with pytest.raises(FileExistsError):
        withheld.main()
    assert record.read_text(encoding="utf-8") == "original source bytes\n"
