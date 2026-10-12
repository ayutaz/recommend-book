"""Reproducible convenience sampling and offline Wikidata linked-label rendering."""
from collections import Counter, defaultdict, deque
import hashlib

from recommend_book.ingestion.wikidata import claim_values
from recommend_book.models import SourceRecord


def year_group(year):
    if year is None:
        return "unknown"
    return "before1900" if year < 1900 else "1900-1949" if year < 1950 else "1950-1999" if year < 2000 else "2000plus"


def select_works(records: list[SourceRecord], *, target=2000, seed=1729) -> tuple[list[SourceRecord], dict]:
    if target < 1:
        raise ValueError("target must be positive")
    unique = {}
    for record in records:
        unique.setdefault((record.source, record.source_id), record)
    def order(record):
        return hashlib.sha256(f"{seed}:{record.source}:{record.source_id}".encode()).hexdigest()
    pools = defaultdict(lambda: defaultdict(list))
    for record in unique.values():
        language = record.original_language
        group = language if language in {"ja", "en"} else "other"
        pools[group][(language or "unknown", year_group(record.first_publication_year))].append(record)
    quotas = {"ja": round(target * .35), "en": round(target * .35)}
    quotas["other"] = target - sum(quotas.values())
    selected, remaining = [], []
    deficits = {}
    for group, quota in quotas.items():
        cells = {cell: deque(sorted(rows, key=order)) for cell, rows in sorted(pools[group].items())}
        group_rows = []
        # Cycle language/year cells rather than taking all recent/English records first.
        while len(group_rows) < quota and any(cells.values()):
            for queue in cells.values():
                if queue and len(group_rows) < quota:
                    group_rows.append(queue.popleft())
        deficits[group] = max(0, quota - len(group_rows))
        selected.extend(group_rows)
        remaining.extend(row for queue in cells.values() for row in queue)
    if len(selected) < target:
        selected.extend(sorted(remaining, key=order)[:target - len(selected)])
    selected.sort(key=order)
    counts = Counter(r.original_language if r.original_language in {"ja", "en"} else "other" for r in selected)
    report = {"seed": seed, "requested": target, "selected": len(selected), "candidate_unique": len(unique), "candidate_duplicate_ids": len(records) - len(unique), "original_language_groups": {group: counts[group] for group in quotas}, "original_languages": dict(Counter(r.original_language or "unknown" for r in selected)), "year_groups": dict(Counter(year_group(r.first_publication_year) for r in selected)), "work_kinds": dict(Counter(r.work_kind or "unknown" for r in selected)), "deficits_before_redistribution": deficits, "limitations": ["Convenience frame from bounded Wikidata discovery, not a world-population probability sample", "Unknown language/year values are preserved, never inferred from display titles", "Discovery by ordered QIDs and explicit work-form/language statements can omit works; within-frame stratification does not remove that bias"]}
    return selected, report


def render_linked_evidence(record: SourceRecord, labels: dict[str, str]) -> SourceRecord:
    """Render only claims present in saved source; linked labels are not new facts."""
    result = record.model_copy(deep=True)
    entity = {"claims": result.extra.get("claims", {})}
    form_labels = {"Q8261": "novel", "Q747381": "light novel", "Q49084": "short story", "Q7725634": "literary work"}
    def names(prop):
        return [labels.get(v["id"], form_labels.get(v["id"], v["id"])) for v in claim_values(entity, prop) if isinstance(v, dict) and "id" in v]
    result.subjects = names("P136") + names("P921")
    result.region = names("P495")
    result.series = "; ".join(names("P179")) or None
    for contributor in result.contributors:
        ids = [i.value for i in contributor.identifiers if i.source == "wikidata"]
        if ids:
            contributor.name = labels.get(ids[0], contributor.name)
    for evidence in result.evidence:
        if "claims.P136" in (evidence.locator or ""):
            evidence.text = "Genres: " + "; ".join(names("P136")) + "; Main subjects (not automatically themes): " + "; ".join(names("P921"))
        elif evidence.locator == "claims.P674":
            evidence.text = "Characters (not necessarily protagonists or narrators): " + "; ".join(names("P674"))
        elif evidence.locator == "claims.P840":
            evidence.text = "Narrative locations (not origin regions): " + "; ".join(names("P840"))
        elif evidence.locator == "claims.P179":
            evidence.text = "Series: " + "; ".join(names("P179"))
        elif evidence.locator and evidence.locator.startswith("labels;claims.P50"):
            evidence.text = f"Title: {result.titles[0].value}; authors: {'; '.join(c.name for c in result.contributors if c.role == 'author')}; instances: {'; '.join(names('P31'))}; creative work forms: {'; '.join(names('P7937'))}"
    return result
