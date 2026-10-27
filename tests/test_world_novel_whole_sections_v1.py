import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recommend_book.autonomous_enrichment import SECTIONS, wikipedia_evidence
from recommend_book.database import DatasetDB
from recommend_book.models import SourceRecord, Title
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_world_novel_whole_sections_v1.py"
spec = importlib.util.spec_from_file_location("prepare_world_novel_whole_sections_v1", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


CLEAN = "An orphan returns home and discovers that a missing sibling is alive."


def fixture(tmp_path, *, body=CLEAN, qid="Q1", revision_id=123):
    wd = tmp_path / "wd.json"
    wd.write_text(json.dumps({"entities": {qid: {
        "id": qid,
        "labels": {"en": {"value": "Book"}},
        "sitelinks": {"enwiki": {"title": "Book"}},
    }}}, ensure_ascii=False), encoding="utf-8")
    work = SourceRecord(
        source="wikidata", source_id=qid,
        source_url=f"https://www.wikidata.org/wiki/{qid}",
        retrieved_at="2026-10-26", provider_id="wikidata", record_kind="work",
        titles=[Title(value="Book")], raw_path=str(wd),
        raw_sha256=hashlib.sha256(wd.read_bytes()).hexdigest(),
        rights_policy_id="wikidata-cc0",
    )
    page = {"pageid": 7, "ns": 0, "title": "Book",
            "pageprops": {"wikibase_item": qid},
            "revisions": [{"revid": revision_id,
                           "slots": {"main": {"contentmodel": "wikitext", "content": body}}}]}
    wp = tmp_path / "wp.json"
    wp.write_text(json.dumps({"query": {"pages": [page]}}, ensure_ascii=False), encoding="utf-8")
    artifact = type("Artifact", (), {
        "path": str(wp), "sha256": hashlib.sha256(wp.read_bytes()).hexdigest(),
        "source_url": "https://en.wikipedia.org/w/api.php",
    })()
    return work, page, artifact


def prepare(tmp_path, **kwargs):
    work, page, artifact = fixture(tmp_path, **kwargs)
    return work, page, artifact, module.prepare_whole_clean_evidence(
        work, page, artifact, "en", {"en": ["lead", "Story"]},
        old_version="authority-v1", canonical="canonical-1", phase="test")


def test_explicit_story_map_adds_only_whole_clean_story_and_keeps_old_map(tmp_path):
    work, page, artifact, (evidence, ledger, outcomes, meta) = prepare(
        tmp_path, body="Lead bibliographic sentence.\n\n== Story ==\n" + CLEAN)
    assert [item.kind for item in evidence] == ["note", "synopsis"]
    assert evidence[1].text == CLEAN
    assert ledger[1]["target_old_version_id"] == "authority-v1"
    assert ledger[1]["producer_recipe_version"] == module.PRODUCER_RECIPE_VERSION
    assert meta["section_map_sha256"] == module.section_map_sha256({"en": ["lead", "Story"]})
    assert meta["producer_code_sha256"] == module.sha256_file(SCRIPT)
    assert "Story" not in SECTIONS["en"]

    old, _, old_outcomes = wikipedia_evidence(
        work, "authority-v1", "canonical-1", page, artifact, "en", set(), set(), "test")
    assert not any(item.locator and ";section:Story;" in item.locator for item in old)
    assert "Story" not in old_outcomes
    assert "Story" not in SECTIONS["en"]


def test_rejected_paragraph_is_held_as_a_whole(tmp_path):
    _, _, _, (evidence, ledger, outcomes, _) = prepare(
        tmp_path, body=CLEAN + "\n\n== Story ==\n" + CLEAN + "\n\n{{quote|external words}}")
    assert [item.kind for item in evidence] == ["note"]
    assert ledger[0]["locator"].endswith("whole-section-clean:autonomous-enrichment-v1")
    assert outcomes["Story"]["reason"] == "section_contains_rejected_paragraphs"


def test_article_notice_is_held_even_outside_story_section(tmp_path):
    _, _, _, (evidence, _, outcomes, _) = prepare(
        tmp_path, body="Lead sentence.\n\n== Story ==\n" + CLEAN + "\n\n== References ==\n{{CC-notice|source}}")
    assert evidence == []
    assert outcomes["Story"]["reason"] == "additional_attribution_or_import_notice"


@pytest.mark.parametrize("mutation", ["qid", "revision"])
def test_page_identity_and_revision_are_pinned(tmp_path, mutation):
    work, page, artifact = fixture(tmp_path, body="== Story ==\n" + CLEAN)
    if mutation == "qid":
        page["pageprops"]["wikibase_item"] = "Q999"
    else:
        page["revisions"][0]["revid"] = 999
    with pytest.raises(ValueError):
        module.prepare_whole_clean_evidence(
            work, page, artifact, "en", {"en": ["Story"]},
            old_version="authority-v1", canonical="canonical-1")


def test_existing_usage_guard_reproduces_whole_clean_evidence(tmp_path):
    work, page, artifact, (evidence, _, _, _) = prepare(
        tmp_path, body="Lead sentence.\n\n== Story ==\n" + CLEAN)
    story = next(item for item in evidence if item.kind == "synopsis")
    UsageGuard(tmp_path / "unused-ledger.json")._wikipedia(story, [work], [])

    work_with_evidence = work.model_copy(update={"evidence": [story]})
    database = DatasetDB(tmp_path / "sources.sqlite")
    database.ingest([work_with_evidence])
    bound_work = database.work_inputs()[0]
    bindings = {ref: source_binding(tmp_path / "sources.sqlite", ref)
                for ref in bound_work.source_record_ids}
    registry = RightsRegistry.default()
    official = tmp_path / "official.txt"
    official.write_text("CC0 attribution sharealike", encoding="utf-8")
    policies = ("wikidata-cc0", "wikipedia-screened-prose")
    ledger = {
        "version": "usage-ledger-v3",
        "registry_sha256": model_hash([p.model_dump(mode="json") for p in registry.policies.values()]),
        "official_conditions": {
            name: [{"raw_path": str(official),
                    "raw_sha256": hashlib.sha256(official.read_bytes()).hexdigest(),
                    "source_url": registry.policies[name].evidence_urls[0],
                    "verified_at": datetime.now(timezone.utc).isoformat()}]
            for name in policies
        },
        "inputs": {model_hash(bound_work): build_input_entry(bound_work, bindings)},
        "revoked_input_hashes": [], "private_origins": {},
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(ledger), encoding="utf-8")
    decision = UsageGuard(path).check_input(bound_work)
    assert decision.allowed, decision.reasons


def test_native_handoff_preserves_authority_wp_raw_identity_and_proofs(tmp_path):
    work, _, _, (evidence, ledger, _, _) = prepare(
        tmp_path, body="Lead sentence.\n\n== Story ==\n" + CLEAN)
    handoff = module.prepare_native_handoff(
        work, evidence, authority_version_id="authority-v1",
        authority_record_sha256="authority-record-sha", ledger=ledger,
        producer_manifest_sha256="producer-manifest-sha")
    assert handoff["authority_version_id"] == "authority-v1"
    assert handoff["authority_raw_path"] == work.raw_path
    assert handoff["wp_raw"][0]["raw_sha256"] == ledger[0]["raw_sha256"]
    assert handoff["evidence_proofs"] == ledger
    assert handoff["records"][0]["source_id"] == work.source_id


def test_manifest_pins_recipe_map_source_and_zero_side_effects(tmp_path):
    source = tmp_path / "saved-wp.json"
    source.write_text("saved", encoding="utf-8")
    manifest = module.producer_manifest(
        section_map={"en": ["Story"]}, source_path=source,
        authority_version_id="authority-v7", authority_record_sha256="record-sha")
    assert manifest["producer_recipe_version"] == module.PRODUCER_RECIPE_VERSION
    assert manifest["section_map_sha256"] == module.section_map_sha256({"en": ["Story"]})
    assert manifest["model_calls"] == manifest["network_requests"] == manifest["catalogue_writes"] == 0


def test_handoff_rejects_stale_authority_version(tmp_path):
    work, _, _, (evidence, ledger, _, _) = prepare(tmp_path)
    with pytest.raises(ValueError, match="authority version"):
        module.prepare_native_handoff(
            work, evidence, authority_version_id="different-authority-version",
            authority_record_sha256="record-sha", ledger=ledger)


def test_handoff_rejects_non_evidence_record_changes(tmp_path):
    work, _, _, (evidence, ledger, _, _) = prepare(tmp_path)
    changed = work.model_copy(update={
        "titles": [Title(value="Different book")], "evidence": evidence})
    with pytest.raises(ValueError, match="evidence-only"):
        module.build_native_handoff(
            work, changed, authority_version_id="authority-v1",
            authority_record_sha256="record-sha", ledger=ledger)


def test_handoff_requires_proof_for_every_added_evidence(tmp_path):
    work, _, _, (evidence, _, _, _) = prepare(tmp_path)
    with pytest.raises(ValueError, match="proofs"):
        module.prepare_native_handoff(
            work, evidence, authority_version_id="authority-v1",
            authority_record_sha256="record-sha", ledger=[])
