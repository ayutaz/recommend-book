import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "materialize_scale_100000_content_window_v1",
    ROOT / "scripts/materialize_scale_100000_content_window_v1.py",
)
materializer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(materializer)

WORKER_SPEC = importlib.util.spec_from_file_location(
    "run_scale_100000_content_worker_v1", ROOT / "scripts/run_scale_100000_content_worker_v1.py"
)
worker = importlib.util.module_from_spec(WORKER_SPEC)
assert WORKER_SPEC.loader is not None
WORKER_SPEC.loader.exec_module(worker)


def _candidate(tmp_path: Path, qid: str = "Q1", *, verified: str | None = None) -> tuple[dict, Path]:
    raw_path = tmp_path / f"wp-{qid}.json"
    raw = {"query": {"pages": [{"pageid": 10, "revisions": [{"revid": 20}]}]}}
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    evidence = {
        "evidence_id": "wp:en:20:Plot:screened-paragraphs-v1",
        "kind": "synopsis",
        "text": "A saved plot.",
        "source_url": "https://en.wikipedia.org/w/index.php?oldid=20",
        "rights_policy_id": "wikipedia-screened-prose",
        "llm_input_allowed": True,
        "language": "en",
        "locator": "revision:20;section:Plot",
        "license": "CC-BY-SA-4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": {"credit": "Wikipedia contributors"},
        "identity_proof": {
            "wikidata_work_id": qid,
            "pageprops_wikibase_item": qid,
            "page_id": 10,
            "revision_id": 20,
        },
    }
    return {
        "source": "wikipedia",
        "source_id": "en:20:screened-paragraphs-v1",
        "source_url": evidence["source_url"],
        "retrieved_at": "2026-10-21T00:00:00+00:00",
        "provider_id": "wikipedia",
        "record_kind": "work",
        "titles": [{"kind": "preferred", "language": "en", "value": qid}],
        "identifiers": [{"source": "wikidata", "scope": "work", "value": qid}],
        "evidence": [evidence],
        "raw_path": str(raw_path),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "rights_policy_id": "wikipedia-screened-prose",
        "extra": {"wikibase_item_verified": verified if verified is not None else qid,
                  "article_language": "en", "page_id": 10, "revision_id": 20},
        "_source_file_sha256": "source-file-sha",
    }, raw_path


def _authority(tmp_path: Path, qid: str = "Q1") -> dict:
    raw_path = tmp_path / f"wd-{qid}.json"
    raw_path.write_text(json.dumps({"entities": {qid: {"id": qid}}}), encoding="utf-8")
    return {
        "source": "wikidata", "source_id": qid, "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "retrieved_at": "2026-10-20T00:00:00+00:00", "provider_id": "wikidata", "record_kind": "work",
        "titles": [{"kind": "preferred", "language": "en", "value": "A Novel"}],
        "identifiers": [{"source": "wikidata", "scope": "work", "value": qid}],
        "evidence": [{"evidence_id": f"wd:{qid}:description:en", "kind": "description", "text": "novel",
                      "source_url": f"https://www.wikidata.org/wiki/{qid}", "rights_policy_id": "wikidata-cc0",
                      "llm_input_allowed": True, "language": "en", "attribution": {}, "inherited_restrictions": []}],
        "extra": {}, "raw_path": str(raw_path), "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "rights_policy_id": "wikidata-cc0", "contributors": [], "subjects": [], "region": [], "work_refs": [],
        "language": None, "original_language": None, "first_publication_year": None,
        "publication_year": None, "publisher": None, "series": None, "volume": None, "work_kind": "novel",
    }


def test_materializer_requires_exact_pageprops_and_preserves_raw_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(materializer, "rel", lambda path: str(Path(path).resolve()))
    candidate, _ = _candidate(tmp_path)
    full, (minimal, ordered) = materializer.enrich(
        _authority(tmp_path), candidate, ROOT / "tests/fixture-records.jsonl", 1, "wd:Q1:v1", "authority-sha"
    )
    assert minimal["source"] == "wikidata"
    assert minimal["source_id"] == "Q1"
    assert minimal["extra"]["scale_100000_content_v1"]["content_raw_sha256"] == candidate["raw_sha256"]
    assert ordered["qid"] == "Q1"
    assert any(e["kind"] == "synopsis" for e in full["evidence"])

    bad, _ = _candidate(tmp_path, verified="Q999")
    with pytest.raises(ValueError, match="pageprops proof"):
        materializer.enrich(_authority(tmp_path), bad, ROOT / "tests/fixture-records.jsonl", 1, "wd:Q1:v1", "authority-sha")


def test_materializer_rejects_changed_content_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(materializer, "rel", lambda path: str(Path(path).resolve()))
    candidate, raw_path = _candidate(tmp_path)
    raw_path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="content raw changed"):
        materializer.enrich(_authority(tmp_path), candidate, ROOT / "tests/fixture-records.jsonl", 1, "wd:Q1:v1", "authority-sha")


def test_materializer_deduplicates_qids_across_windows():
    def row(qid: str):
        return Path(f"{qid}.jsonl"), 1, {"identifiers": [{"source": "wikidata", "scope": "work", "value": qid}]}

    selected = materializer.select_new_rows([row("Q1"), row("Q2"), row("Q2"), row("Q3")], {"Q1"}, 2)
    assert [materializer.qid_of(item[2]) for item in selected] == ["Q2", "Q3"]


def test_worker_module_import_has_no_collection_side_effect():
    assert callable(worker.main)


def test_worker_does_not_skip_partial_window(tmp_path):
    partial = tmp_path / "content-window-10001-10100"
    partial.mkdir()
    assert worker.completed_output(partial) is False
    (partial / "manifest.json").write_text(json.dumps({
        "version": "scale-content-acquisition-window-v1",
        "requested": 100,
        "selected_authorities": 100,
        "accepted_records": 100,
        "status_counts": {"accepted": 100},
        "http_max_attempts": 1,
        "old_sources_modified": False,
    }), encoding="utf-8")
    assert worker.completed_output(partial) is True
