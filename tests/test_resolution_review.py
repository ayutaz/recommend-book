import hashlib
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Contributor, Identifier, SourceRecord, Title

spec = importlib.util.spec_from_file_location("build_resolution_review", Path(__file__).parents[1] / "scripts/build_resolution_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def record(source_id, title, author="Author", shared="Q1", **fields):
    return SourceRecord(source="test", source_id=source_id,
        source_url="https://example.org/" + source_id,
        retrieved_at="2026-10-12T00:00:00Z", provider_id="test", record_kind="work",
        titles=[Title(value=title)], contributors=[Contributor(name=author)],
        identifiers=[Identifier(source="wikidata", value=shared, scope="work")],
        raw_sha256="a" * 64, raw_path="raw/test.json", rights_policy_id="test", **fields)


def test_shared_identity_does_not_create_gold_or_easy_filler():
    a = record("one", "Title")
    assert review.pair_strata(a, record("two", "Title"), {"identity"}) == []
    b = record("two", "Other title", author="Other")
    assert "shared_id_author_variation" in review.pair_strata(a, b, {"identity"})
    c = record("three", "Title", author="Other", shared="Q2")
    assert "same_title_author_difference" in review.pair_strata(a, c, {"title"})


def test_frozen_sample_escaped_provenance_and_human_schema(tmp_path):
    path = tmp_path / "dataset.sqlite"
    with DatasetDB(path) as db:
        db.ingest([record("one", "<script>alert(1)</script>"), record("two", "Other title")])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    out = tmp_path / "review"
    manifest = review.build(path, out, 300)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert manifest["selected_pairs"] == 1
    assert manifest["minimum_shortfall"] == 299
    assert manifest["gold_count"] == 0 and manifest["precision"] is None
    data = (out / "pairs.jsonl").read_bytes()
    assert hashlib.sha256(data).hexdigest() == manifest["sample_sha256"]
    pair = json.loads(data)
    assert pair["human_adjudication"] is None
    assert pair["rule_proposal"]["is_gold"] is False
    assert all(side["field_assertions"] and side["provenance"]["raw_sha256"] for side in pair["sides"])
    html = (out / "review.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    schema = json.loads((out / "adjudications.schema.json").read_text())
    template = json.loads((out / "adjudications.template.json").read_text())
    jsonschema.validate(template, schema)
    template["adjudications"] = [{"case_id": pair["case_id"], "reviewer": "Reviewer A", "decision": "uncertain", "reason": "Needs source inspection"}]
    jsonschema.validate(template, schema)
    template["adjudications"][0]["reviewer"] = " "
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(template, schema)
    assert review.build(path, out, 300) == manifest
    (out / "pairs.jsonl").write_text("tampered", encoding="utf-8")
    with pytest.raises(FileExistsError):
        review.build(path, out, 300)
