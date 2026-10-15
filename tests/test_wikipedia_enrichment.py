import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from recommend_book.ingestion.core import RawArtifact, read_jsonl, write_jsonl
from recommend_book.models import Contributor, Identifier, SourceRecord, Title


def load_script():
    spec = importlib.util.spec_from_file_location("wikipedia_enrichment", Path(__file__).resolve().parents[1] / "scripts/enrich_wikipedia_poc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(tmp_path, monkeypatch, returned_qid="Q1"):
    module = load_script()
    source = tmp_path / "input.jsonl"
    work = SourceRecord(source="wikidata", source_id="Q1", source_url="https://www.wikidata.org/wiki/Q1", retrieved_at="2026-10-12T00:00:00Z", provider_id="wikidata", record_kind="work", titles=[Title(value="Book", language="en")], contributors=[Contributor(name="Author", identifiers=[Identifier(source="wikidata", value="Q2", scope="agent")])], identifiers=[Identifier(source="wikidata", value="Q1", scope="work")], original_language="en", raw_sha256="a" * 64, raw_path="fixture", rights_policy_id="wikidata-cc0", extra={"sitelinks": {"enwiki": {"title": "Book"}}})
    write_jsonl([work], source)
    calls = []
    payload = {"query": {"pages": [{"pageid": 42, "title": "Book", "pageprops": {"wikibase_item": returned_qid}, "revisions": [{"revid": 123, "timestamp": "2026-10-12T00:00:00Z", "slots": {"main": {"content": "A detective novel.\n\n==Plot==\nAda solves a murder.\n\nThe novel says \"a protected quotation\"."}}}]}]}}
    class Fetcher:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, url, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status="success", content=json.dumps(payload).encode(), artifact=RawArtifact("b" * 64, "fixture-article", 100, "2026-10-12T00:00:00Z", url))
    monkeypatch.setattr(module, "HTTPFetcher", Fetcher)
    root = tmp_path / "out"
    monkeypatch.setattr("sys.argv", ["script", "--input", str(source), "--root", str(root)])
    return module, root, source, calls


def test_article_qid_mismatch_cannot_bind_evidence_to_another_work(tmp_path, monkeypatch):
    module, root, _, _ = prepare(tmp_path, monkeypatch, returned_qid="Q999")
    assert module.main() == 0
    assert list(read_jsonl(root / "records.jsonl")) == []
    done = json.loads((root / "checkpoint.json").read_text())["done"]["Q1"]
    assert done["status"] == "withheld"


def test_revision_attribution_and_resume_preserve_identity_and_license(tmp_path, monkeypatch):
    module, root, _, calls = prepare(tmp_path, monkeypatch)
    assert module.main() == 0
    records = list(read_jsonl(root / "records.jsonl"))
    assert len(records) == 1
    record = records[0]
    assert record.extra["revision_id"] == 123
    assert record.extra["human_reviewed"] is False
    assert record.extra["inherited_text_restrictions"] == ["attribution", "share_alike", "mark_changes"]
    assert "oldid=123" in record.extra["attribution"]["permalink"]
    assert all(e.llm_input_allowed for e in record.evidence)
    assert all(e.license == "CC-BY-SA-4.0" and e.attribution["permalink"].endswith("oldid=123") for e in record.evidence)
    assert [e.kind for e in record.evidence] == ["note", "synopsis"]
    assert "protected quotation" not in record.evidence[-1].text
    original = (root / "records.jsonl").read_bytes()
    assert module.main() == 0
    assert len(calls) == 1
    assert (root / "records.jsonl").read_bytes() == original


def test_input_change_rejects_reusing_incompatible_checkpoint(tmp_path, monkeypatch):
    import pytest
    module, root, source, _ = prepare(tmp_path, monkeypatch)
    module.main()
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="configuration changed"):
        module.main()
