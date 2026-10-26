"""Prepare explicitly named, whole-clean Wikipedia story sections.

This producer is deliberately separate from the historical autonomous-enrichment
module.  It uses the historical whole-section recipe contract, but never changes
its global section map: callers must pass an explicit map for the target language.
Only a section for which the complete screened text is clean is returned.  A
rejected paragraph is a hold, rather than an invitation to select a subset.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from urllib.parse import quote, urlparse
import re

from recommend_book.autonomous_enrichment import FULFILLED, text_sha, verified_entity
from recommend_book.ingestion.wikipedia import section_text
from recommend_book.models import Evidence, SourceRecord
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.wikipedia_prose import SCREEN_VERSION, screen_section


PRODUCER_RECIPE_VERSION = "world-whole-narrative-sections-v1"
LEGACY_RECIPE_VERSION = "autonomous-enrichment-v1"
HANDOFF_VERSION = "world-whole-narrative-native-handoff-v1"
CHANGES = "selected complete clean section; references removed; wiki markup converted to plain text"
SCREENING_LIMITATION = (
    "Automatic screening is not human review or legal certification; "
    "unmarked third-party problems may remain."
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def section_map_sha256(section_map: Mapping[str, Sequence[str]]) -> str:
    """Hash the explicit language/section map, independent of dict ordering."""
    normalized = {}
    for language, sections in section_map.items():
        if isinstance(sections, (str, bytes)):
            raise ValueError("explicit story section map value must be a sequence")
        normalized[str(language)] = [str(section) for section in sections]
    return sha256_bytes(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8"))


def _sections_for(section_map: Mapping[str, Sequence[str]], language: str) -> list[str]:
    if not isinstance(section_map, Mapping) or language not in section_map:
        raise ValueError("explicit story section map missing language")
    if isinstance(section_map[language], (str, bytes)):
        raise ValueError("explicit story section map value must be a sequence")
    sections = list(section_map[language])
    if not sections or any(not isinstance(section, str) or not section for section in sections):
        raise ValueError("explicit story section map has invalid sections")
    if len(set(sections)) != len(sections):
        raise ValueError("explicit story section map contains duplicate sections")
    return sections


def _raw_pages(artifact, language: str):
    payload = Path(artifact.path).read_bytes()
    if sha256_bytes(payload) != artifact.sha256:
        raise ValueError("Wikipedia raw hash mismatch")
    if urlparse(artifact.source_url).hostname != language + ".wikipedia.org":
        raise ValueError("Wikipedia raw host identity mismatch")
    data = json.loads(payload)
    pages = data.get("query", {}).get("pages", [])
    return payload, list(pages.values()) if isinstance(pages, dict) else pages


def _notice_in_article(text: str) -> bool:
    notices = re.compile(
        r"cc[ _-]?notice|source[ _-]?attribution|attribution|copyright|copyvio|"
        r"copypaste|copy[ _-]?paste|1911|eb1911|dnb|public[ _-]?domain|"
        r"pd[ _-]?notice|imported[ _-]?text|incorporates[ _-]?text|"
        r"text[ _-]?incorporated",
        re.I,
    )
    import mwparserfromhell
    return any(notices.search(str(template.name))
               for template in mwparserfromhell.parse(text).filter_templates(recursive=True))


def _identity(work: SourceRecord, page: dict, revision_id: int, language: str) -> dict:
    return {
        "wikidata_work_id": work.source_id,
        "pageprops_wikibase_item": page["pageprops"]["wikibase_item"],
        "page_id": page["pageid"],
        "revision_id": revision_id,
        "language": language,
        "target_raw_path": work.raw_path,
        "target_raw_sha256": work.raw_sha256,
        "target_raw_locator": "entities." + work.source_id,
    }


def prepare_whole_clean_evidence(
    work: SourceRecord,
    page: dict,
    artifact,
    language: str,
    section_map: Mapping[str, Sequence[str]],
    *,
    old_version: str,
    canonical: str,
    denied: Iterable[str] = (),
    existing: Iterable[str] = (),
    phase: str = "world-whole-narrative-sections-v1",
):
    """Build proposals from a saved page, with no I/O other than pinned raw reads.

    The evidence ID and locator intentionally use the existing
    ``autonomous-enrichment-v1`` whole-clean contract.  The independent producer
    version and map digest are returned in the result metadata, never encoded by
    changing the old evidence recipe or global section map.
    """
    entity = verified_entity(work)
    sections = _sections_for(section_map, language)
    if page.get("pageprops", {}).get("wikibase_item") != work.source_id:
        raise ValueError("Wikipedia raw identity mismatch")
    if page.get("ns") != 0 or "disambiguation" in page.get("pageprops", {}):
        raise ValueError("Wikipedia raw identity mismatch or disambiguation")
    sitelink = entity.get("sitelinks", {}).get(language + "wiki", {}).get("title")
    if sitelink != page.get("title"):
        raise ValueError("Wikipedia target sitelink identity missing")
    payload, pages = _raw_pages(artifact, language)
    if page not in pages:
        raise ValueError("Wikipedia page does not equal saved raw page")
    revisions = page.get("revisions", [])
    if len(revisions) != 1 or not isinstance(revisions[0].get("revid"), int) or revisions[0]["revid"] <= 0:
        raise ValueError("immutable revision identity missing")
    revision = revisions[0]
    slot = revision.get("slots", {}).get("main", {})
    if slot.get("contentmodel", "wikitext") != "wikitext":
        raise ValueError("unsupported article content model")
    article_text = slot.get("content", slot.get("*", ""))
    imported = _notice_in_article(article_text)
    revision_id = revision["revid"]
    url = f"https://{language}.wikipedia.org/w/index.php?oldid={revision_id}"
    history = f"https://{language}.wikipedia.org/w/index.php?title={quote(page['title'], safe='')}&action=history"
    registry = RightsRegistry.default()
    policy = registry.policies["wikipedia-screened-prose"]
    identity = _identity(work, page, revision_id, language)
    denied, existing = set(denied), set(existing)
    evidence, ledger, outcomes = [], [], {}
    factual_titles = [page["title"], *(title.value for title in work.titles)]
    for section in sections:
        try:
            original = section_text(article_text, section)
        except ValueError:
            outcomes[section] = {"status": "held", "reason": "section_missing"}
            continue
        screening = screen_section(original, article_wikitext=article_text, factual_titles=factual_titles)
        outcome = {"screening": asdict(screening), "status": "held"}
        if imported:
            outcome["reason"] = "additional_attribution_or_import_notice"
        elif screening.article_blocked:
            outcome["reason"] = "article_rights_notice"
        elif screening.rejected_paragraphs:
            outcome["reason"] = "section_contains_rejected_paragraphs"
        elif not screening.text or len(screening.text) < 20:
            outcome["reason"] = "empty_or_insufficient_section"
        elif text_sha(screening.text) in denied:
            outcome["reason"] = "previously_denied_text"
        elif text_sha(screening.text) in existing:
            outcome["reason"] = "already_in_baseline"
        else:
            kind = "note" if section == "lead" else "synopsis"
            permitted = {usage: registry.permits(policy.policy_id, kind, usage, set(FULFILLED))
                         for usage in USAGES}
            if not all(permitted.values()):
                outcome["reason"] = "usage_conditions_unfulfilled"
            else:
                locator = f"revision:{revision_id};section:{section};screen:{SCREEN_VERSION};whole-section-clean:{LEGACY_RECIPE_VERSION}"
                attribution = {
                    "credit": f"Wikipedia contributors: {page['title']}",
                    "permalink": url,
                    "history_url": history,
                    "changes": CHANGES,
                    "raw_path": artifact.path,
                    "raw_sha256": artifact.sha256,
                    "page_id": page["pageid"],
                    "revision_id": revision_id,
                    "identity_proof": identity,
                    "fulfilled_obligations": FULFILLED,
                    "screening_limitation": SCREENING_LIMITATION,
                }
                ev = Evidence(
                    evidence_id=f"wp:{language}:{revision_id}:{section}:{LEGACY_RECIPE_VERSION}",
                    kind=kind,
                    text=screening.text,
                    source_url=url,
                    rights_policy_id=policy.policy_id,
                    llm_input_allowed=True,
                    language=language,
                    locator=locator,
                    license=policy.license,
                    license_url="https://creativecommons.org/licenses/by-sa/4.0/",
                    attribution=attribution,
                    inherited_restrictions=FULFILLED,
                )
                evidence.append(ev)
                ledger.append({
                    "work_id": canonical,
                    "evidence_id": ev.evidence_id,
                    "source_url": url,
                    "raw_path": artifact.path,
                    "raw_sha256": artifact.sha256,
                    "locator": locator,
                    "target_source": work.source,
                    "target_source_id": work.source_id,
                    "target_old_version_id": old_version,
                    "rights_policy_id": policy.policy_id,
                    "usages": policy.usages,
                    "fulfilled_obligations": FULFILLED,
                    "identity_proof": identity,
                    "screen_version": SCREEN_VERSION,
                    "screening": asdict(screening),
                    "usage_permitted": permitted,
                    "license": ev.license,
                    "license_url": ev.license_url,
                    "evidence_text_sha256": text_sha(ev.text),
                    "candidate_phase": phase,
                    "producer_recipe_version": PRODUCER_RECIPE_VERSION,
                    "section_map_sha256": section_map_sha256(section_map),
                })
                outcome.update(status="accepted", evidence_id=ev.evidence_id,
                               producer_recipe_version=PRODUCER_RECIPE_VERSION)
        outcomes[section] = outcome
    metadata = {
        "producer_recipe_version": PRODUCER_RECIPE_VERSION,
        "producer_code_sha256": sha256_file(__file__),
        "legacy_recipe_version": LEGACY_RECIPE_VERSION,
        "section_map_sha256": section_map_sha256(section_map),
        "source_raw_sha256": sha256_bytes(payload),
        "wp_identity": identity,
        "evidence_proofs": ledger,
    }
    return evidence, ledger, outcomes, metadata


def build_native_handoff(
    authority: SourceRecord,
    enriched: SourceRecord,
    *,
    authority_version_id: str,
    authority_record_sha256: str,
    ledger: Sequence[dict],
    producer_manifest_sha256: str | None = None,
) -> dict:
    """Return a DB-writer-neutral handoff preserving authority and WP proofs."""
    if authority.source != "wikidata" or authority.record_kind != "work":
        raise ValueError("native handoff requires current Wikidata work authority")
    if enriched.source != authority.source or enriched.source_id != authority.source_id:
        raise ValueError("handoff authority identity differs")
    if (enriched.model_dump(mode="json", exclude={"evidence"}) !=
            authority.model_dump(mode="json", exclude={"evidence"}) or
            enriched.evidence[:len(authority.evidence)] != authority.evidence):
        raise ValueError("handoff requires evidence-only append to authority")
    evidence_ids = [item.evidence_id for item in enriched.evidence]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("handoff contains duplicate evidence IDs")
    added_ids = set(evidence_ids[len(authority.evidence):])
    rows = list(ledger)
    row_ids = [row.get("evidence_id") for row in rows]
    evidence_by_id = {item.evidence_id: item for item in enriched.evidence}
    if len(set(row_ids)) != len(row_ids) or set(row_ids) != added_ids:
        raise ValueError("handoff evidence proofs do not bind to enriched evidence")
    if any(row.get("target_old_version_id") != authority_version_id or
           row.get("target_source") != authority.source or
           row.get("target_source_id") != authority.source_id for row in rows):
        raise ValueError("handoff authority version or source differs from evidence proofs")
    if any(evidence_by_id[row["evidence_id"]].source_url != row.get("source_url") or
           evidence_by_id[row["evidence_id"]].locator != row.get("locator") or
           evidence_by_id[row["evidence_id"]].attribution.get("raw_sha256") != row.get("raw_sha256")
           for row in rows):
        raise ValueError("handoff evidence proof differs")
    wp = [{"raw_path": row["raw_path"], "raw_sha256": row["raw_sha256"],
           "identity_proof": row["identity_proof"], "evidence_id": row["evidence_id"]}
          for row in rows]
    return {
        "version": HANDOFF_VERSION,
        "authority_version_id": authority_version_id,
        "authority_record_sha256": authority_record_sha256,
        "authority_source_id": authority.source_id,
        "authority_raw_path": authority.raw_path,
        "authority_raw_sha256": authority.raw_sha256,
        "records": [enriched.model_dump(mode="json")],
        "wp_raw": wp,
        "evidence_proofs": rows,
        "producer_recipe_version": PRODUCER_RECIPE_VERSION,
        "producer_manifest_sha256": producer_manifest_sha256,
        "model_calls": 0,
        "network_requests": 0,
        "catalogue_writes": 0,
    }


def prepare_native_handoff(
    authority: SourceRecord,
    evidence: Sequence[Evidence],
    *,
    authority_version_id: str,
    authority_record_sha256: str,
    ledger: Sequence[dict],
    producer_manifest_sha256: str | None = None,
) -> dict:
    """Append only evidence to the authority and emit the neutral handoff."""
    enriched = authority.model_copy(
        update={"evidence": [*authority.evidence, *list(evidence)]}, deep=True
    )
    return build_native_handoff(
        authority,
        enriched,
        authority_version_id=authority_version_id,
        authority_record_sha256=authority_record_sha256,
        ledger=ledger,
        producer_manifest_sha256=producer_manifest_sha256,
    )


def producer_manifest(*, section_map: Mapping[str, Sequence[str]], source_path: str | Path,
                      authority_version_id: str, authority_record_sha256: str) -> dict:
    """Make the sidecar pin without reading or mutating a database."""
    source_path = Path(source_path).resolve(strict=True)
    code_path = Path(__file__).resolve()
    return {
        "version": PRODUCER_RECIPE_VERSION,
        "producer_recipe_version": PRODUCER_RECIPE_VERSION,
        "recipe_path": str(code_path),
        "recipe_sha256": sha256_file(code_path),
        "section_map_sha256": section_map_sha256(section_map),
        "source_path": str(source_path),
        "source_sha256": sha256_file(source_path),
        "authority_version_id": authority_version_id,
        "authority_record_sha256": authority_record_sha256,
        "model_calls": 0,
        "network_requests": 0,
        "catalogue_writes": 0,
    }


__all__ = [
    "CHANGES", "HANDOFF_VERSION", "LEGACY_RECIPE_VERSION",
    "PRODUCER_RECIPE_VERSION", "build_native_handoff", "prepare_native_handoff",
    "prepare_whole_clean_evidence",
    "producer_manifest", "section_map_sha256",
]
