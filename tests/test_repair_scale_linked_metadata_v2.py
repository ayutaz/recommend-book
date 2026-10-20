import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("repair_scale_linked_metadata_v2", ROOT / "scripts" / "repair_scale_linked_metadata_v2.py")
repair = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(repair)


def _claim(qid):
    return {"mainsnak": {"datavalue": {"value": {"id": qid}}}}


def test_required_ids_match_native_scope_and_exclude_unused_claims():
    row = {
        "contributors": [{"identifiers": [{"scope": "agent", "value": "Q101"}]}],
        "region": ["Q29"],
        "series": "Series",
        "evidence": [
            {"evidence_id": "subject", "kind": "subject", "llm_input_allowed": True, "locator": "claims.P136;claims.P921"},
            {"evidence_id": "description", "kind": "description", "llm_input_allowed": True, "language": "en"},
            {"evidence_id": "blocked-subject", "kind": "subject", "llm_input_allowed": False, "locator": "claims.P136;claims.P921"},
        ],
        "extra": {"claims": {
            "P136": [_claim("Q102")], "P921": [_claim("Q103")],
            "P495": [_claim("Q29")], "P179": [_claim("Q104")],
            "P674": [_claim("Q105")], "P840": [_claim("Q106")],
        }},
    }

    assert repair.required_ids(row) == {"Q101", "Q29", "Q104", "Q102", "Q103"}


def test_build_preserves_author_and_editor_labels_with_tmp_raw(tmp_path, monkeypatch):
    raw_path = tmp_path / "Q100.json"
    raw = {
        "entities": {"Q100": {
            "id": "Q100",
            "labels": {"en": {"language": "en", "value": "Fixture Novel"}},
            "descriptions": {"en": {"language": "en", "value": "a fixture novel"}},
            "claims": {
                "P31": [_claim("Q8261")], "P7937": [_claim("Q8261")],
                "P50": [_claim("Q101")], "P98": [_claim("Q102")],
                "P495": [_claim("Q29")], "P136": [_claim("Q103")],
            },
        }, **{
            qid: {"id": qid, "labels": {"en": {"language": "en", "value": label}}}
            for qid, label in (("Q101", "Author One"), ("Q102", "Editor Two"), ("Q29", "Spain"), ("Q103", "Novel"))
        }}}
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    import hashlib
    raw_sha = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    original = {
        "source": "wikidata", "source_id": "Q100", "source_url": "https://www.wikidata.org/wiki/Q100",
        "retrieved_at": "2026-01-01T00:00:00+00:00", "provider_id": "wikidata", "record_kind": "work",
        "titles": [{"value": "Fixture Novel", "language": "en", "kind": "preferred"}],
        "contributors": [
            {"name": "Author One", "role": "author", "identifiers": [{"source": "wikidata", "value": "Q101", "scope": "agent"}]},
            {"name": "Editor Two", "role": "editor", "identifiers": [{"source": "wikidata", "value": "Q102", "scope": "agent"}]},
        ],
        "region": ["Q29"], "series": None, "subjects": ["Genre"], "evidence": [
            {"evidence_id": "wd:Q100:subjects:old", "kind": "subject", "text": "genre", "source_url": "https://www.wikidata.org/wiki/Q100", "rights_policy_id": "wikidata-cc0", "llm_input_allowed": True, "locator": "claims.P136;claims.P921"},
        ],
        "raw_sha256": raw_sha, "raw_path": str(raw_path), "rights_policy_id": "wikidata-cc0",
        "extra": {"claims": raw["entities"]["Q100"]["claims"]},
    }
    proofs = {qid: {"label": label, "raw_path": str(raw_path), "raw_sha256": raw_sha, "locator": f"entities.{qid}.labels.en"}
              for qid, label in (("Q101", "Author One"), ("Q102", "Editor Two"), ("Q29", "Spain"), ("Q103", "Novel"))}
    output = tmp_path / "out"
    output.mkdir()
    monkeypatch.setattr(repair.base, "OUTPUT", output)
    monkeypatch.setattr(repair.base, "required_ids", repair.required_ids)
    report = repair.base.build([original], proofs, {"completed": [], "held": [], "errors": [], "request_batches": 0})
    row = json.loads((output / "repaired-source-records.jsonl").read_text(encoding="utf-8"))
    names = {item["identifiers"][0]["value"]: item["name"] for item in row["contributors"]}
    assert names == {"Q101": "Author One", "Q102": "Editor Two"}
    assert set(names) <= set(row["extra"]["linked_label_provenance"])
    assert report["repaired_work_count"] == 1
