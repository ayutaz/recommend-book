"""Series labels never become volume/work labels or calibrated estimates."""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

from recommend_book.light_novel_classification_v1 import (
    SeriesInput, input_hash, result_schema, validate_result, validate_reviews,
    project_reviewed_result, check_gate, build_prompt, reserve_inputs,
    build_series_article_evidence, series_source_policy, SOURCE_VERSION,
    TERMS_URL, WIKIDATA_TERMS, LICENSE_URL, save, sha, digest, validate_source_admission,
    run_window, verify_feature_export,
    freeze_runtime,
    build_series_metadata_evidence, augment_unattempted_source,
    cli_argv, equivalent_cli_argv,
)
from recommend_book.classification.validation import load_taxonomy


@pytest.fixture
def series():
    return SeriesInput(series_id="lns:wd:Q1", title="テストシリーズ", authors=["著者"], source_manifest_sha256="a" * 64,
        source_record_ids=["wikidata:Q1"], evidence=[{
            "evidence_id": "e1", "kind": "synopsis", "text": "魔法の学校を舞台に、主人公が事件を解決する。",
            "source_url": "https://ja.wikipedia.org/w/index.php?oldid=1", "rights_policy_id": "wikipedia-screened-prose",
            "llm_input_allowed": True, "language": "ja", "locator": "revision:1;paragraph:1",
            "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": {"revision_id": 1}}])


def native(series):
    taxonomy = load_taxonomy()
    return {"series_id": series.series_id, "entity_kind": "light_novel_series", "scope": "series",
        "taxonomy_version": taxonomy["version"], "prompt_version": "light-novel-series-1.0",
        "model": "gpt-6.1-sol", "cli_version": "test-cli", "input_hash": input_hash(series),
        "labels": [{"label_id": "genre.fantasy", "axis": "genre", "decision": "present", "scope": "series",
            "relevance": .9, "raw_confidence": .9, "calibrated_confidence": None,
            "calibration_status": "uncalibrated", "calibration_version": None,
            "evidence": [{"evidence_id": "e1", "locator": "revision:1;paragraph:1", "quote": None,
                "summary": "学校での魔法が物語の前提である", "directness": "direct"}],
            "missing_reason": None, "spoiler": "premise"}],
        "missing_axes": [{"axis": axis, "decision": "unknown", "reason": "資料不足"}
            for axis in ("theme", "structure", "protagonist", "style", "atmosphere")],
        "conflicts": [], "needs_human_review": False}


def test_series_schema_and_native_validate(series):
    value = native(series)
    assert result_schema()["properties"]["scope"] == {"const": "series"}
    assert validate_result(value, series) == value
    wrong = deepcopy(value)
    wrong["labels"][0]["scope"] = "work"
    with pytest.raises(ValueError):
        validate_result(wrong, series)


def test_identity_evidence_and_unknown_are_strict(series):
    for path, replacement in (("input_hash", "other"), ("series_id", "ln:wd:Q2")):
        wrong = native(series)
        wrong[path] = replacement
        with pytest.raises(ValueError):
            validate_result(wrong, series)
    for field, replacement in (("evidence_id", "foreign"), ("locator", "different"), ("quote", "not literal")):
        wrong = native(series)
        wrong["labels"][0]["evidence"][0][field] = replacement
        with pytest.raises(ValueError):
            validate_result(wrong, series)
    wrong = native(series)
    wrong["missing_axes"] = []
    with pytest.raises(ValueError):
        validate_result(wrong, series)


def test_style_is_unknown_for_encyclopedia_series_sources(series):
    wrong = native(series)
    style_id = next(label["label_id"] for label in load_taxonomy()["labels"] if label["axis"] == "style")
    wrong["labels"][0].update(label_id=style_id, axis="style")
    with pytest.raises(ValueError, match="cannot establish prose style"):
        validate_result(wrong, series)


def test_review_covers_every_label_and_holds_uncertain(series):
    value = native(series)
    review = {"series_id": series.series_id, "reviewed_label_ids": ["genre.fantasy"],
        "summary": "確認済み", "findings": []}
    assert validate_reviews({"reviews": [review]}, [(series, value)])
    adopted = project_reviewed_result(series, value, review)
    assert adopted["scope"] == "series" and adopted["content_features_adopted"]
    assert adopted["uncalibrated"] and not adopted["human_quality_verified"]
    held = deepcopy(review)
    held["findings"] = [{"label_ids": ["genre.fantasy"], "reason_code": "semantic_entailment_uncertain", "reason": "根拠不足"}]
    assert not project_reviewed_result(series, value, held)["admitted_labels"]
    with pytest.raises(ValueError):
        validate_reviews({"reviews": [review, review]}, [(series, value)])
    review["reviewed_label_ids"] = []
    with pytest.raises(ValueError):
        validate_reviews({"reviews": [review]}, [(series, value)])


def test_generic_review_request_is_resolved_by_complete_separate_review(series):
    value = native(series)
    value["needs_human_review"] = True
    review = {"series_id": series.series_id, "reviewed_label_ids": ["genre.fantasy"], "summary": "Reviewed", "findings": []}
    projection = project_reviewed_result(series, value, review)
    assert len(projection["admitted_labels"]) == 1
    assert projection["native_review_requested"] and projection["separate_internal_review_completed"]
    value["conflicts"] = ["Concrete unresolved edition scope conflict"]
    assert not project_reviewed_result(series, value, review)["admitted_labels"]


def test_unknown_label_is_never_adopted_even_after_positive_review(series):
    value = native(series)
    value["labels"][0].update(decision="unknown", relevance=None, raw_confidence=None, evidence=[], missing_reason="Insufficient evidence")
    value["missing_axes"].append({"axis": "genre", "decision": "unknown", "reason": "Insufficient evidence"})
    review = {"series_id": series.series_id, "reviewed_label_ids": ["genre.fantasy"], "summary": "Uncertainty retained", "findings": []}
    row = project_reviewed_result(series, value, review)
    assert not row["admitted_labels"] and len(row["unknown_axes"]) == 6


def test_prompt_uses_series_identity_and_requires_unknown_style(series):
    prompt = build_prompt(series, cli_version="test-cli")
    assert '"series_id": "lns:wd:Q1"' in prompt
    assert "Never copy series features" in prompt
    assert "style must remain unknown" in prompt


def test_no_unchanged_input_resubmission_even_in_new_window(series, tmp_path):
    reserve_inputs([series], tmp_path, "first")
    with pytest.raises(ValueError, match="already reserved"):
        reserve_inputs([series], tmp_path, "second")
    with pytest.raises(ValueError, match="already reserved"):
        reserve_inputs([series.model_copy(update={"source_manifest_sha256": "b" * 64})], tmp_path, "renamed-source")


def test_gates_use_all_selected_not_only_successes():
    assert check_gate(selected=100, failed=5, pending=0, adopted=70)["passed"]
    assert not check_gate(selected=100, failed=6, pending=0, adopted=70)["passed"]
    assert not check_gate(selected=100, failed=0, pending=0, adopted=69)["passed"]
    assert not check_gate(selected=100, failed=0, pending=1, adopted=70)["passed"]


def source_bundle(tmp_path):
    """Only synthetic public-source-shaped fixtures, never private retained raw."""
    raw_entity = tmp_path / "entity.json"
    entity = {"id": "Q1", "labels": {"en": {"value": "Test Novel"}}, "sitelinks": {"enwiki": {"title": "Test Novel"}},
        "claims": {"P31": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q104213567"}}}}],
            "P407": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q5287"}}}}]}}
    save(raw_entity, {"entities": {"Q1": entity}})
    metadata = {"id": "Q1", "kind": "series", "title": "Test Novel", "authors": [],
        "original_languages": ["Q5287"], "genres": [], "subjects": [], "admission": {"accepted": True},
        "provenance": {"url": "https://www.wikidata.org/wiki/Q1", "raw_path": str(raw_entity), "raw_sha256": sha(raw_entity)}}
    metadata_path = tmp_path / "metadata.jsonl"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    page = {"pageid": 1, "ns": 0, "title": "Test Novel", "pageprops": {"wikibase_item": "Q1"},
        "revisions": [{"revid": 2, "slots": {"main": {"contentmodel": "wikitext", "content":
            "Test Novel is a Japanese light novel series about magic.\n\n== Plot ==\nA student discovers a hidden school for magicians and resolves mysteries."}}}]}
    raw_page = tmp_path / "article.json"
    save(raw_page, {"query": {"pages": [page]}})
    evidence, receipts, outcomes = build_series_article_evidence(metadata, page,
        {"path": str(raw_page), "sha256": sha(raw_page)}, "en")
    policy = tmp_path / "policy.json"
    save(policy, series_source_policy())
    pins = {str(p): sha(p) for p in (raw_entity, raw_page, metadata_path, policy)}
    conditions = {}
    for index, url in enumerate((TERMS_URL, WIKIDATA_TERMS, LICENSE_URL)):
        path = tmp_path / f"conditions-{index}.txt"
        path.write_text("synthetic official terms fixture", encoding="utf-8")
        pins[str(path)] = sha(path)
        conditions[url] = {"path": str(path), "sha256": sha(path)}
    manifest = tmp_path / "source-manifest.json"
    save(manifest, {"version": SOURCE_VERSION, "policy_path": str(policy), "metadata_path": str(metadata_path),
        "conditions": conditions, "artifact_sha256": pins, "series": [{"series_id": "lns:wd:Q1",
            "metadata_row_sha256": digest(metadata), "articles": [{"raw_path": str(raw_page), "raw_sha256": sha(raw_page),
                "language": "en", "page_id": 1}], "evidence_receipts": receipts}]})
    series = SeriesInput(series_id="lns:wd:Q1", title="Test Novel", authors=[], evidence=evidence,
        source_record_ids=["wikidata:Q1"], source_manifest_sha256=sha(manifest))
    return series, manifest, raw_page


def test_source_admission_reproduces_prose_rights_and_raw_identity(tmp_path):
    series, manifest, raw_page = source_bundle(tmp_path)
    assert validate_source_admission([series], manifest, sha(manifest))["allowed"]
    tampered = series.model_copy(deep=True)
    tampered.evidence[0].text = "Invented replacement prose"
    with pytest.raises(ValueError, match="exact reproduced"):
        validate_source_admission([tampered], manifest, sha(manifest))
    raw_page.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        validate_source_admission([series], manifest, sha(manifest))


def test_self_consistent_metadata_claim_does_not_replace_raw_title_authority(tmp_path):
    series, manifest, _ = source_bundle(tmp_path)
    document = json.loads(manifest.read_bytes())
    metadata_path = Path(document["metadata_path"])
    metadata = json.loads(metadata_path.read_bytes())
    metadata["title"] = "A wrong series that shares a familiar author"
    metadata_path.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
    document["artifact_sha256"][str(metadata_path)] = sha(metadata_path)
    document["series"][0]["metadata_row_sha256"] = digest(metadata)
    manifest.write_text(json.dumps(document), encoding="utf-8")
    series = series.model_copy(update={"title": metadata["title"], "source_manifest_sha256": sha(manifest)})
    with pytest.raises(ValueError, match="title or author identity"):
        validate_source_admission([series], manifest, sha(manifest))


def test_metadata_cannot_support_content_attributes(series):
    value = native(series)
    metadata = series.model_copy(deep=True)
    metadata.evidence[0].kind = "bibliographic"
    value["input_hash"] = input_hash(metadata)
    assert validate_result(value, metadata)
    atmosphere_id = next(label["label_id"] for label in load_taxonomy()["labels"] if label["axis"] == "atmosphere")
    value["labels"][0].update(label_id=atmosphere_id, axis="atmosphere")
    with pytest.raises(ValueError, match="Metadata cannot establish"):
        validate_result(value, metadata)


def test_declared_genres_reproduce_structured_raw_and_linked_label(tmp_path):
    _, source, _ = source_bundle(tmp_path)
    manifest = json.loads(source.read_bytes())
    metadata = json.loads(Path(manifest["metadata_path"]).read_bytes())
    entity_path = Path(metadata["provenance"]["raw_path"])
    raw = json.loads(entity_path.read_bytes())
    raw["entities"]["Q1"]["claims"]["P136"] = [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q2"}}}}]
    entity_path.write_text(json.dumps(raw), encoding="utf-8")
    linked = tmp_path / "linked.json"
    save(linked, {"entities": {"Q2": {"id": "Q2", "labels": {"en": {"value": "Fantasy"}}}}})
    metadata["genres"] = [{"id": "Q2", "label": "Fantasy"}]
    metadata["provenance"].update(raw_sha256=sha(entity_path), linked_provenance={"Q2": {"raw_path": str(linked), "raw_sha256": sha(linked)}})
    evidence, receipts = build_series_metadata_evidence(metadata)
    assert len(evidence) == 1 and evidence[0].kind == "bibliographic"
    assert "P136" in evidence[0].text and all(receipts[0]["purposes"].values())
    metadata["genres"][0]["label"] = "An unsupported theme"
    with pytest.raises(ValueError, match="structured raw"):
        build_series_metadata_evidence(metadata)


def test_source_augmentation_never_resends_an_attempted_input(tmp_path):
    series, source, _ = source_bundle(tmp_path)
    source.with_name("inputs.jsonl").write_text(series.model_dump_json() + "\n", encoding="utf-8")
    reserve_inputs([series], tmp_path / "reserved", "native-first")
    with pytest.raises(ValueError, match="attempted source input"):
        augment_unattempted_source(source, tmp_path / "new-source", reservations=tmp_path / "reserved")


def test_only_cli_path_serialization_can_differ(tmp_path):
    expected = cli_argv(["test-cli"], tmp_path / "workspace", tmp_path / "schema.json", tmp_path / "final.json")
    actual = list(expected)
    if os.name == "nt":
        for flag in ("--cd", "--output-schema", "-o"):
            index = actual.index(flag) + 1
            actual[index] = "\\\\?\\" + actual[index]
        assert equivalent_cli_argv(actual, expected)
    altered = list(actual)
    altered[altered.index("--model") + 1] = "some-other-model"
    assert not equivalent_cli_argv(altered, expected)
    altered = list(actual)
    altered[altered.index("-o") + 1] = str(tmp_path / "foreign.json")
    assert not equivalent_cli_argv(altered, expected)


def build_verified_export_fixture(tmp_path, monkeypatch, *, concurrency=4):
    """Exercise real child process witnesses with a deterministic local fake CLI."""
    series, source, _ = source_bundle(tmp_path)
    child = tmp_path / "fake_cli.py"
    child.write_text('''import json, sys
if "--version" in sys.argv:
    print("test-cli")
    raise SystemExit(0)
prompt = sys.stdin.read()
payload = json.loads(prompt.split("\\nLOCAL DATA\\n", 1)[1])
if "output_identity" in payload:
    value = dict(payload["output_identity"])
    evidence = payload["series"]["evidence"][0]
    value.update(labels=[dict(label_id="genre.fantasy",axis="genre",decision="present",scope="series",
        relevance=.8,raw_confidence=.9,calibrated_confidence=None,calibration_status="uncalibrated",calibration_version=None,
        evidence=[dict(evidence_id=evidence["evidence_id"],locator=evidence["locator"],quote=None,summary="Magic drives the setting",directness="direct")],
        missing_reason=None,spoiler="premise")],
        missing_axes=[dict(axis=a,decision="unknown",reason="No evidence") for a in ("theme","structure","protagonist","style","atmosphere")],
        conflicts=[],needs_human_review=False)
else:
    value = dict(reviews=[dict(series_id=r["input"]["series_id"],reviewed_label_ids=[l["label_id"] for l in r["native_result"]["labels"]],summary="Reviewed",findings=[]) for r in payload["series"]])
text = json.dumps(value)
open(sys.argv[sys.argv.index("-o")+1],"w",encoding="utf-8").write(text)
print(json.dumps(dict(type="item.completed",item=dict(type="agent_message",text=text))))
print(json.dumps(dict(type="turn.completed",usage=dict(input_tokens=1,output_tokens=1))))
''', encoding="utf-8")
    import recommend_book.light_novel_classification_v1 as module
    monkeypatch.setattr(module, "resolve_executable", lambda _: [sys.executable, str(child)])
    result = run_window([series], tmp_path / "native", source_manifest=source, source_sha256=sha(source),
        reservations=tmp_path / "reservations", concurrency=concurrency)
    return result


def test_export_verifies_both_native_processes_and_expires_on_mutation(tmp_path, monkeypatch):
    result = build_verified_export_fixture(tmp_path, monkeypatch)
    verified = verify_feature_export(result["manifest_path"], result["manifest_sha256"])
    assert verified["gate"]["passed"] and verified["content_features_adopted_series"] == 1
    assert verified["rows"][0]["scope"] == "series"
    final = Path(result["manifest_path"]).parent / "classification/000/final.json"
    final.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bytes changed"):
        verify_feature_export(result["manifest_path"], result["manifest_sha256"])


def test_finite_runtime_snapshot_and_archived_verifier_detect_source_mutation(tmp_path, monkeypatch):
    result = build_verified_export_fixture(tmp_path, monkeypatch)
    runtime = freeze_runtime(tmp_path / "archive")
    manifest_path = Path(result["manifest_path"])
    document = json.loads(manifest_path.read_bytes())
    document.update(runtime_root=str(runtime), runtime_manifest_sha256=sha(runtime / "light-novel-runtime-snapshot.json"))
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    expected = sha(manifest_path)
    assert verify_feature_export(manifest_path, expected)["gate"]["passed"]
    target = runtime / "src/recommend_book/light_novel_classification_v1.py"
    target.write_bytes(target.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="bytes changed"):
        verify_feature_export(manifest_path, expected)


def test_stopped_window_continues_only_never_started_inputs_and_first_reviews(tmp_path, monkeypatch):
    """Control-flow fixture; full source-rights reproduction has separate tests."""
    import recommend_book.light_novel_classification_v1 as module
    template = build_verified_export_fixture(tmp_path / "template", monkeypatch)
    template_dir = Path(template["manifest_path"]).parent
    series = SeriesInput.model_validate(json.loads((template_dir / "inputs.json").read_bytes())[0])
    child = tmp_path / "template/fake_cli.py"
    code = child.read_text(encoding="utf-8")
    code = code.replace('text = json.dumps(value)', 'if value.get("series_id") == "lns:wd:Q21": value["input_hash"] = "invalid-native-identity"\ntext = json.dumps(value)')
    child.write_text(code, encoding="utf-8")
    inputs = [series.model_copy(update={"series_id": "lns:wd:Q" + str(index)}) for index in range(1, 31)]
    monkeypatch.setattr(module, "validate_source_admission", lambda *args, **kwargs: {"allowed": True})
    actual_attempt = module._attempt
    def legacy_stop(*args, **kwargs):
        binding = actual_attempt(*args, **kwargs)
        if kwargs["kind"] == "classification" and binding["value"].get("series_id") == "lns:wd:Q21":
            raise ValueError("Legacy stop on the one invalid native identity")
        return binding
    monkeypatch.setattr(module, "_attempt", legacy_stop)
    source = tmp_path / "template/source-manifest.json"
    first = run_window(inputs, tmp_path / "stopped", source_manifest=source, source_sha256=sha(source),
        reservations=tmp_path / "reserved", concurrency=4)
    parent = json.loads(Path(first["manifest_path"]).read_bytes())
    assert len(parent["failures"]) == 1 and not parent["review_attempts"]
    assert parent["gate"]["actual_unstarted_series"] > 0
    monkeypatch.setattr(module, "_attempt", actual_attempt)
    continued = run_window(inputs, tmp_path / "continued", source_manifest=source, source_sha256=sha(source),
        reservations=tmp_path / "reserved", concurrency=4,
        continuation_manifest=first["manifest_path"], continuation_sha256=first["manifest_sha256"])
    verified = verify_feature_export(continued["manifest_path"], continued["manifest_sha256"])
    assert verified["gate"]["passed"] and verified["gate"]["actual_attempted_series"] == 30
    assert verified["gate"]["failed"] == 1 and verified["gate"]["pending"] == 0
    assert len(verified["rows"]) == 29
    assert verified["first_classification_calls_in_continuation"] == parent["gate"]["actual_unstarted_series"]
    hashes = [json.loads(path.read_bytes())["input_hashes"][0] for root in (tmp_path / "stopped", tmp_path / "continued")
        for path in root.glob("classification/*/request.json")]
    assert len(hashes) == len(set(hashes)) == 30


def test_failure_rate_uses_actual_calls_instead_of_all_planned_inputs():
    value = check_gate(selected=100, failed=1, pending=90, adopted=0, attempted=10)
    assert value["failure_rate"] == .1 and value["actual_unstarted_series"] == 90
    assert "failure_rate_above_5_percent" in value["reasons"]


@pytest.mark.parametrize("slots", [1, 2])
def test_reduced_parallelism_limits_both_classification_and_review(tmp_path, monkeypatch, slots):
    import recommend_book.light_novel_classification_v1 as module
    original = module.ThreadPoolExecutor
    scheduled = []

    def bounded_pool(*, max_workers):
        scheduled.append(max_workers)
        return original(max_workers=max_workers)

    monkeypatch.setattr(module, "ThreadPoolExecutor", bounded_pool)
    result = build_verified_export_fixture(tmp_path, monkeypatch, concurrency=slots)
    verified = verify_feature_export(result["manifest_path"], result["manifest_sha256"])
    assert scheduled == [slots, slots]
    assert verified["gate"]["passed"]
    assert verified["content_features_adopted_series"] == 1
