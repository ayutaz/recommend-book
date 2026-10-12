"""Conservative bibliographic identity decisions, separate from classification."""
from dataclasses import dataclass
from typing import Literal

from .models import Contributor, SourceRecord
from .normalization import normalize_text


@dataclass(frozen=True)
class ResolutionResult:
    status: Literal["same", "different", "uncertain"]
    reason: str
    confidence: float | None = None  # rule outcome, not a calibrated probability


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
