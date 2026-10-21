"""Acquire one bounded Wikipedia content window for new scale works.

The route starts from saved Wikidata authority records, resolves an exact
language sitelink, fetches at most one Wikipedia article per work, and emits
only per-work content records and pins.  It does not copy the catalogue or
start an LLM.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from recommend_book.ingestion.core import HTTPFetcher, RawStore
from recommend_book.models import Identifier, SourceRecord, Title
from recommend_book.wikipedia_paragraphs import build_evidence

LANGUAGES = ("en", "ja", "fr", "de", "es", "ru", "zh", "ko", "it", "pt", "ar", "hi", "bn")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _qids_from_jsonl(path: Path) -> set[str]:
    qids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return qids
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            qid = row.get("qid")
            if isinstance(qid, str) and qid.startswith("Q"):
                qids.add(qid)
            for identifier in row.get("identifiers", []):
                if identifier.get("source") == "wikidata" and identifier.get("scope") == "work":
                    value = identifier.get("value")
                    if isinstance(value, str) and value.startswith("Q"):
                        qids.add(value)
    return qids


def _qids_from_json(path: Path) -> set[str]:
    qids: set[str] = set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return qids
    rows = value if isinstance(value, list) else value.get("rows", []) if isinstance(value, dict) else []
    if isinstance(value, dict) and isinstance(value.get("selections"), list):
        rows = value["selections"]
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        qid = row.get("qid")
        if isinstance(qid, str) and qid.startswith("Q"):
            qids.add(qid)
    return qids


def existing_content_qids(root: Path) -> set[str]:
    """Return every known or attempted work, including held and no-sitelink rows.

    Window outcomes are deliberately part of this ledger: a held work must not be
    selected again merely because it produced no content.  New windows also persist
    selections before any network call, covering the no-sitelink/started boundary.
    """
    qids: set[str] = set()
    for path in root.glob("content-v*/manifest.json"):
        try:
            qids.update(json.loads(path.read_text(encoding="utf-8")).get("qids", []))
        except (OSError, ValueError):
            continue
    for path in root.glob("content-v*/content-inputs.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                qids.add(json.loads(line)["work_id"].split(":", 1)[1])
    for path in root.glob("content-multilingual-v1/adapted-v1/records.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                qids.add(json.loads(line)["source_id"])
    for path in root.glob("content-window-*/records.jsonl"):
        qids.update(_qids_from_jsonl(path))
    for path in root.glob("content-window-*/source-pins.jsonl"):
        qids.update(_qids_from_jsonl(path))
    for path in root.glob("content-window-*/outcomes.json"):
        qids.update(_qids_from_json(path))
    for path in root.glob("content-window-*/selections.json"):
        qids.update(_qids_from_json(path))
    return qids


def select_authorities(catalogue: Path, root: Path, limit: int) -> list[SourceRecord]:
    excluded = existing_content_qids(root)
    with sqlite3.connect(catalogue.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        native = {row[0] for row in db.execute("SELECT qid FROM native_classifications")}
        # Keep the catalogue scan cheap: record_json can contain a large raw
        # authority payload.  First scan only the indexed identity columns,
        # then fetch JSON for candidates that survived the ledger/native guard.
        rows = db.execute(
            "SELECT w.qid,w.version_id FROM works w "
            "WHERE w.recommendable=1 AND w.display_allowed=1 ORDER BY CAST(substr(w.qid,2) AS INTEGER)"
        ).fetchall()
        selected = []
        for qid, version_id in rows:
            if qid in native or qid in excluded:
                continue
            payload_row = db.execute(
                "SELECT record_json FROM source_versions WHERE version_id=?", (version_id,)
            ).fetchone()
            if payload_row is None:
                continue
            payload = payload_row[0]
            record = SourceRecord.model_validate_json(payload)
            if record.source != "wikidata" or record.source_id != qid or record.work_kind not in ("novel", "light_novel"):
                continue
            # A no-sitelink work cannot produce a Wikipedia content record.  Exclude it
            # before filling the bounded window so it is never selected repeatedly.
            if choose_sitelink(record) is None:
                continue
            selected.append(record)
            if len(selected) >= limit:
                break
    if len(selected) < limit:
        raise ValueError(f"only {len(selected)} new authority records available, requested {limit}")
    return selected


def choose_sitelink(record: SourceRecord) -> tuple[str, str] | None:
    links = record.extra.get("sitelinks", {})
    preferred = list(dict.fromkeys([record.original_language, *LANGUAGES]))
    return next(((language, links[language + "wiki"]["title"]) for language in preferred
                 if language in LANGUAGES and language + "wiki" in links), None)


def content_record(authority: SourceRecord, page: dict, artifact, language: str, evidence: list) -> SourceRecord:
    revision = page["revisions"][0]
    revid = revision["revid"]
    url = f"https://{language}.wikipedia.org/w/index.php?oldid={revid}"
    extra = {
        "revision_id": revid, "revision_timestamp": revision.get("timestamp"),
        "page_id": page["pageid"], "article_title": page["title"],
        "article_language": language, "wikibase_item_verified": authority.source_id,
        "imported_bibliography_from": {"source_id": authority.source_id, "raw_sha256": authority.raw_sha256, "raw_path": authority.raw_path},
        "screening": {e.locator: e.attribution.get("paragraph_selection") for e in evidence},
        "human_reviewed": False,
        "screening_limitation": "Automatic screening is not human review or legal certification; unmarked third-party problems may remain.",
        "fulfilled_obligations": ["attribution", "mark_changes", "share_alike", "third_party_text_screened"],
        "text_license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": {"credit": f"Wikipedia contributors: {page['title']}", "permalink": url,
                        "changes": "selected complete screened paragraphs; rejected paragraphs omitted; references removed; wiki markup converted to plain text"},
        "inherited_text_restrictions": ["attribution", "mark_changes", "share_alike"],
        "license_scope": "article prose and adaptations, not CC0; quoted/imported/fair-use content excluded",
    }
    return SourceRecord(source="wikipedia", source_id=f"{language}:{revid}:screened-paragraphs-v1",
        source_url=url, retrieved_at=artifact.retrieved_at, provider_id="wikipedia", record_kind="work",
        titles=[*authority.titles, Title(value=page["title"], language=language, kind="alternative")],
        contributors=authority.contributors, identifiers=[Identifier(source="wikidata", value=authority.source_id, scope="work")],
        original_language=authority.original_language, first_publication_year=authority.first_publication_year,
        work_kind=authority.work_kind, region=authority.region, series=authority.series, volume=authority.volume,
        evidence=evidence, raw_sha256=artifact.sha256, raw_path=artifact.path,
        rights_policy_id="wikipedia-screened-prose", extra=extra)


def collect(*, catalogue: Path, output: Path, limit: int) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    authorities = select_authorities(catalogue, output.parent, limit)
    selections = []
    groups: dict[str, list[tuple[str, SourceRecord]]] = {}
    for authority in authorities:
        selected = choose_sitelink(authority)
        if selected is None:
            selections.append({"qid": authority.source_id, "status": "held", "reason": "no_supported_sitelink"})
            continue
        language, title = selected
        selections.append({"qid": authority.source_id, "language": language, "title": title, "status": "selected"})
        groups.setdefault(language, []).append((title, authority))
    selections_path = output / "selections.json"
    write_json(selections_path, selections)
    shared_store = RawStore(output.parent / "shared-content-fetch-v1")
    fetcher = HTTPFetcher(shared_store, min_interval=1.1, max_attempts=1,
                          user_agent="recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)")
    records, pins, outcomes = [], [], []
    network_requests = 0
    for language, candidates in groups.items():
        for start in range(0, len(candidates), 50):
            batch = candidates[start:start + 50]
            response = fetcher.get(f"https://{language}.wikipedia.org/w/api.php", source="wikipedia", params={
                "action": "query", "titles": "|".join(title for title, _ in batch),
                "prop": "pageprops|revisions", "ppprop": "wikibase_item", "rvprop": "ids|timestamp|content",
                "rvslots": "main", "format": "json", "formatversion": 2, "maxlag": 5})
            network_requests += response.attempts
            if response.status != "success":
                outcomes.extend({"qid": authority.source_id, "status": "held", "reason": str(response.error)} for _, authority in batch)
                continue
            pages = json.loads(response.content).get("query", {}).get("pages", [])
            by_qid = {page.get("pageprops", {}).get("wikibase_item"): page for page in pages}
            for title, authority in batch:
                page = by_qid.get(authority.source_id)
                if not page or not page.get("revisions"):
                    outcomes.append({"qid": authority.source_id, "status": "held", "reason": "missing_exact_page_or_revision"})
                    continue
                try:
                    evidence, ledger, sections = build_evidence(authority, page, response.artifact, language)
                    narrative = [e for e in evidence if e.kind in ("synopsis", "excerpt")]
                    if not narrative:
                        outcomes.append({"qid": authority.source_id, "status": "held", "reason": "no_screened_narrative", "sections": sections})
                        continue
                    record = content_record(authority, page, response.artifact, language, evidence)
                    records.append(record.model_dump(mode="json"))
                    pins.append({"qid": authority.source_id, "work_id": "wd:" + authority.source_id,
                                 "authority": {"raw_path": authority.raw_path, "raw_sha256": authority.raw_sha256},
                                 "content": {"raw_path": response.artifact.path, "raw_sha256": response.artifact.sha256,
                                              "source_url": record.source_url, "source_id": record.source_id},
                                 "evidence_ids": [e.evidence_id for e in evidence], "ledger": ledger})
                    outcomes.append({"qid": authority.source_id, "status": "accepted", "evidence_count": len(evidence),
                                     "narrative_count": len(narrative), "content_raw_sha256": response.artifact.sha256})
                except (ValueError, KeyError, TypeError) as exc:
                    outcomes.append({"qid": authority.source_id, "status": "held", "reason": str(exc)})
            print(json.dumps({"language": language, "processed": len(outcomes), "accepted": len(records), "network_requests": response.attempts}), flush=True)
    write_jsonl(output / "records.jsonl", records)
    write_jsonl(output / "source-pins.jsonl", pins)
    write_json(output / "outcomes.json", outcomes)
    manifest = {"version": "scale-content-acquisition-window-v1", "created_at": datetime.now(timezone.utc).isoformat(),
                "source_route": "saved_wikidata_authority_to_wikipedia_sitelink", "requested": limit,
                "selected_authorities": len(authorities), "accepted_records": len(records),
                "status_counts": dict(Counter(x["status"] for x in outcomes)), "network_requests": network_requests,
                "model_calls": 0, "catalogue_path": str(catalogue.resolve()),
                "selection_path": str(selections_path.resolve()), "selection_sha256": sha(selections_path),
                "shared_raw_store": str(shared_store.root), "http_max_attempts": 1,
                "rights_conditions_path": str((catalogue.parent.parent / "scale-30000-v1/current-content-conditions-v1/official-conditions.json").resolve()),
                "old_sources_modified": False}
    write_json(output / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalogue", type=Path, default=Path("data/autonomous-next-v3/scale-v1/catalogue.sqlite"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(collect(catalogue=args.catalogue, output=args.output, limit=args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
