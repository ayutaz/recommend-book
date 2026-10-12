"""Conservative bibliographic identity decisions, separate from classification."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from .models import Contributor, SourceRecord
from .normalization import normalize_text


@dataclass(frozen=True)
class ResolutionResult:
    status: Literal["same", "different", "uncertain"]
    reason: str
    confidence: float | None = None  # rule outcome, not a calibrated probability
    method: Literal["rule", "source_mapping"] = "rule"


def work_identifiers(record: SourceRecord) -> set[tuple[str, str]]:
    return {(item.source, item.value) for item in [*record.identifiers, *record.work_refs] if item.scope == "work"}


def _author_matches(left: Contributor, right: Contributor) -> bool:
    l_ids = {(item.source, item.value) for item in left.identifiers if item.scope == "agent"}
    r_ids = {(item.source, item.value) for item in right.identifiers if item.scope == "agent"}
    shared_sources = {source for source, _ in l_ids} & {source for source, _ in r_ids}
    if shared_sources:
        return all({value for source, value in l_ids if source == namespace} & {value for source, value in r_ids if source == namespace} for namespace in shared_sources)
    return normalize_text(left.name) == normalize_text(right.name)


def compare_records(left: SourceRecord, right: SourceRecord) -> ResolutionResult:
    """A shared scoped work ID needs consistent authors and bibliographic units."""
    identities_a, identities_b = work_identifiers(left), work_identifiers(right)
    namespaces = {source for source, _ in identities_a | identities_b}
    for namespace in namespaces:
        a = {value for source, value in identities_a if source == namespace}
        b = {value for source, value in identities_b if source == namespace}
        if len(a) > 1 or len(b) > 1:
            return ResolutionResult("uncertain", "multiple work identities in one authority namespace")
        if a and b and not a & b:
            return ResolutionResult("uncertain", "conflicting external work identities")
    for field in ("original_language", "volume", "work_kind"):
        a, b = getattr(left, field), getattr(right, field)
        if a is not None and b is not None and normalize_text(a) != normalize_text(b):
            return ResolutionResult("different", f"conflicting {field}")
    authors_a = [item for item in left.contributors if item.role == "author"]
    authors_b = [item for item in right.contributors if item.role == "author"]
    if not authors_a or not authors_b:
        return ResolutionResult("uncertain", "missing author attribution")
    # Complete coauthor sets, with authoritative identities preferred over names.
    if len(authors_a) != len(authors_b):
        return ResolutionResult("uncertain", "incomplete or conflicting coauthor set")
    remaining = list(authors_b)
    for author in authors_a:
        match = next((item for item in remaining if _author_matches(author, item)), None)
        if match is None:
            return ResolutionResult("different", "conflicting author attribution")
        remaining.remove(match)
    if (left.volume is None) != (right.volume is None):
        return ResolutionResult("uncertain", "missing volume unit")
    if identities_a & identities_b:
        if left.record_kind == right.record_kind == "work":
            # Translation labels in different languages are compatible; distinct
            # original titles in the same language need an alias/reviewer.
            title_languages_a = {title.language or left.original_language for title in left.titles}
            title_languages_b = {title.language or right.original_language for title in right.titles}
            shared_languages = title_languages_a & title_languages_b
            if shared_languages and not any(normalize_text(a.value) == normalize_text(b.value) for a in left.titles for b in right.titles if (a.language or left.original_language) == (b.language or right.original_language)):
                return ResolutionResult("uncertain", "conflicting work titles in the same language")
        return ResolutionResult("same", "explicit work identity corroborated by complete author set and compatible language and unit")
    return ResolutionResult("uncertain", "title or author similarity is insufficient work identity")


def compare_source_mapping(left: SourceRecord, right: SourceRecord, *, raw_cache: dict | None = None) -> ResolutionResult | None:
    """Verify a derived Wikipedia source's exact immutable Wikidata lineage.

    This links dependent source evidence; it is neither an independent identity
    confirmation nor a human decision. Other source pairs retain ordinary rules.
    """
    pair = {left.source: left, right.source: right}
    if set(pair) != {"wikidata", "wikipedia"}:
        return None
    original, derived = pair["wikidata"], pair["wikipedia"]
    lineage = derived.extra.get("imported_bibliography_from")

    def hold(reason):
        return ResolutionResult("uncertain", "derived source mapping: " + reason, method="source_mapping")

    if not lineage:
        return hold("missing inherited bibliography lineage")
    if not isinstance(lineage, dict) or original.record_kind != "work" or derived.record_kind != "work":
        return hold("invalid work bibliography lineage")
    original_ids = {value for namespace, value in work_identifiers(original) if namespace == "wikidata"}
    derived_ids = {value for namespace, value in work_identifiers(derived) if namespace == "wikidata"}
    if len(original_ids) != 1 or original_ids != derived_ids:
        return hold("requires one identical Wikidata work ID")
    qid = next(iter(original_ids))
    if original.source_id != qid or lineage.get("source_id") != qid or lineage.get("raw_sha256") != original.raw_sha256:
        return hold("source ID or immutable source SHA does not match lineage")
    if not isinstance(lineage.get("raw_path"), str) or Path(lineage["raw_path"]).resolve() != Path(original.raw_path).resolve():
        return hold("source raw path does not match lineage")
    cache = raw_cache if raw_cache is not None else {}

    def verified_payload(record):
        path = str(Path(record.raw_path).resolve())
        if path not in cache:
            content = Path(path).read_bytes()
            cache[path] = (sha256(content).hexdigest(), json.loads(content))
        digest, payload = cache[path]
        if digest != record.raw_sha256:
            raise ValueError("raw bytes do not match immutable source SHA")
        return payload

    try:
        wd_payload, wp_payload = verified_payload(original), verified_payload(derived)
        if wd_payload.get("entities", {}).get(qid, {}).get("id") != qid:
            return hold("original raw entity does not contain the stated work")
        pages = wp_payload.get("query", {}).get("pages", [])
        if isinstance(pages, dict):
            pages = list(pages.values())
        page_id, revision_id = derived.extra.get("page_id"), derived.extra.get("revision_id")
        if page_id is None or revision_id is None:
            return hold("missing immutable page and revision locator")
        source_parts = derived.source_id.split(":")
        if len(source_parts) < 2 or source_parts[1] != str(revision_id):
            return hold("source record ID does not match raw revision locator")
        matches = [page for page in pages if page.get("pageid") == page_id and any(revision.get("revid") == revision_id for revision in page.get("revisions", []))]
        if len(matches) != 1 or matches[0].get("pageprops", {}).get("wikibase_item") != qid:
            return hold("raw pageprops or revision scope does not match work")
        if derived.extra.get("article_title") and matches[0].get("title") != derived.extra["article_title"]:
            return hold("article title does not match raw page locator")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return hold(f"raw source verification failed: {type(exc).__name__}: {exc}")
    # Only this proven inheritance path may disregard unrelated OL alias counts.
    # It still uses full author, original-language, unit and title consistency.
    def scoped(record):
        return record.model_copy(update={"identifiers": [item for item in record.identifiers if item.scope != "work" or item.source == "wikidata"], "work_refs": [item for item in record.work_refs if item.scope != "work" or item.source == "wikidata"]})

    consistency = compare_records(scoped(original), scoped(derived))
    if consistency.status != "same":
        return ResolutionResult(consistency.status, "derived source mapping: " + consistency.reason, method="source_mapping")
    return ResolutionResult("same", "verified Wikipedia page scope and inherited immutable Wikidata bibliography; dependent source mapping", method="source_mapping")
