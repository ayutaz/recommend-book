"""Batched revision-pinned, screened article summaries with license provenance.

No source prose is described as CC0 or human verified. Paragraph exclusions and
the inherited CC BY-SA obligations travel with each selected section.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from recommend_book.ingestion.core import HTTPFetcher, RawStore, read_jsonl, write_jsonl
from recommend_book.ingestion.wikipedia import section_text
from recommend_book.models import Evidence, Identifier, SourceRecord, Title
from recommend_book.rights import RightsRegistry
from recommend_book.wikipedia_prose import SCREEN_VERSION, screen_section

SECTIONS = {
    "en": ["lead", "Plot", "Plot summary", "Synopsis", "Summary"],
    "ja": ["lead", "あらすじ", "ストーリー", "物語"],
    "fr": ["lead", "Résumé", "Intrigue"],
    "de": ["lead", "Handlung", "Inhalt"],
    "es": ["lead", "Argumento", "Sinopsis"],
}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def create_page_record(work, page, artifact, language, policies):
    qid = work.source_id
    if page.get("pageprops", {}).get("wikibase_item") != qid:
        raise ValueError("Wikipedia article does not match target work")
    fulfilled = {"attribution", "share_alike", "mark_changes", "third_party_text_screened"}
    new_record = None
    revision = page["revisions"][0]
    revid = revision["revid"]
    text = revision["slots"]["main"].get("content", "")
    evidence, screens = [], {}
    url = f"https://{language}.wikipedia.org/w/index.php?oldid={revid}"
    for section in SECTIONS[language]:
        try:
            section_source = section_text(text, section)
        except ValueError:
            continue
        screened = screen_section(section_source, article_wikitext=text, factual_titles=[page["title"], *(t.value for t in work.titles)])
        screens[section] = asdict(screened)
        if not screened.text:
            continue
        # This is authored article prose, never a novel's prose-style excerpt.
        kind = "note" if section == "lead" else "synopsis"
        allowed = policies.permits("wikipedia-screened-prose", kind, "external_llm", fulfilled)
        evidence.append(Evidence(evidence_id=f"wp:{language}:{revid}:{section}:{SCREEN_VERSION}", kind=kind, text=screened.text, source_url=url, rights_policy_id="wikipedia-screened-prose", llm_input_allowed=allowed, language=language, locator=f"revision:{revid};section:{section};screen:{SCREEN_VERSION}", license="CC-BY-SA-4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0/", attribution={"credit": f"Wikipedia contributors: {page['title']}", "permalink": url, "history_url": f"https://{language}.wikipedia.org/w/index.php?title={quote(page['title'], safe='')}&action=history", "changes": "selected section; citations and unsuitable paragraphs excluded; plain text conversion"}, inherited_restrictions=["attribution", "share_alike", "mark_changes"]))
    outcome = {"revision_id": revid, "language": language, "sections": list(screens), "evidence_count": len(evidence), "llm_allowed": sum(e.llm_input_allowed for e in evidence)}
    if evidence:
        new_record = SourceRecord(source="wikipedia", source_id=f"{language}:{revid}:{SCREEN_VERSION}", source_url=url, retrieved_at=artifact.retrieved_at, provider_id="wikipedia", record_kind="work", titles=[*work.titles, Title(value=page["title"], language=language, kind="alternative")], contributors=work.contributors, identifiers=[Identifier(source="wikidata", value=qid, scope="work")], original_language=work.original_language, first_publication_year=work.first_publication_year, work_kind=work.work_kind, region=work.region, series=work.series, volume=work.volume, evidence=evidence, raw_sha256=artifact.sha256, raw_path=artifact.path, rights_policy_id="wikipedia-screened-prose", extra={"revision_id": revid, "revision_timestamp": revision["timestamp"], "page_id": page["pageid"], "article_title": page["title"], "article_language": language, "wikibase_item_verified": qid, "imported_bibliography_from": {"source_id": work.source_id, "raw_sha256": work.raw_sha256, "raw_path": work.raw_path}, "screening": screens, "human_reviewed": False, "screening_limitation": "Automatic exclusions cannot detect all unmarked copyright problems; this is not human review or legal certification.", "fulfilled_obligations": sorted(fulfilled), "text_license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": {"permalink": url, "history_url": f"https://{language}.wikipedia.org/w/index.php?title={quote(page['title'], safe='')}&action=history", "credit": f"Wikipedia contributors: {page['title']}", "changes": "selected sections extracted; citations and unsuitable paragraphs excluded; markup converted to plain text"}, "inherited_text_restrictions": ["attribution", "share_alike", "mark_changes"], "license_scope": "article prose and adaptations, not CC0; quoted/imported/fair-use content excluded"})
    return new_record, outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("data/wikipedia-poc"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    records = list(read_jsonl(args.input))
    if args.limit:
        records = records[:args.limit]
    groups: dict[str, list] = {}
    selections = []
    for work in records:
        links = work.extra.get("sitelinks", {})
        preferred = list(dict.fromkeys(["en", work.original_language, "ja", "fr", "de", "es"]))
        found = next(((language, links[language + "wiki"]["title"]) for language in preferred if language in SECTIONS and language + "wiki" in links), None)
        if found and any(c.role == "author" for c in work.contributors):
            language, title = found
            groups.setdefault(language, []).append((title, work))
        else:
            selections.append({"work_id": work.source_id, "status": "withheld", "reason": "supported_article_or_author_identity_missing"})
    output = args.root / "records.jsonl"
    rows = list(read_jsonl(output)) if output.exists() else []
    checkpoint_path = args.root / "checkpoint.json"
    input_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    expected_context = {"input_sha256": input_hash, "limit": args.limit, "screen_version": SCREEN_VERSION,
                        "rights_sha256": hashlib.sha256(json.dumps(RightsRegistry.default().policies["wikipedia-screened-prose"].model_dump(), sort_keys=True).encode()).hexdigest()}
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {"context": expected_context, "done": {}, "selection": []}
    if checkpoint.get("context") != expected_context:
        raise ValueError("Source, policy, limit or screen configuration changed; use a new output root")
    done = checkpoint["done"]
    fetcher = HTTPFetcher(RawStore(args.root / "raw"), user_agent="recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)", min_interval=1.1)
    policies = RightsRegistry.default()
    fulfilled = {"attribution", "share_alike", "mark_changes", "third_party_text_screened"}
    for language, candidates in groups.items():
        remaining = [(title, work) for title, work in candidates if work.source_id not in done]
        for start in range(0, len(remaining), 50):
            batch = remaining[start:start + 50]
            fetched = fetcher.get(f"https://{language}.wikipedia.org/w/api.php", source="wikipedia", params={"action": "query", "titles": "|".join(title for title, _ in batch), "prop": "pageprops|revisions", "ppprop": "wikibase_item", "rvprop": "ids|timestamp|content", "rvslots": "main", "format": "json", "formatversion": 2, "maxlag": 5})
            if fetched.status != "success":
                atomic_json(args.root / "failure.json", {"error": fetched.error, "language": language, "start": start})
                return 1
            payload = json.loads(fetched.content)
            if "error" in payload:
                atomic_json(args.root / "failure.json", payload)
                return 1
            expected = {work.source_id: work for _, work in batch}
            returned = set()
            for page in payload.get("query", {}).get("pages", []):
                qid = page.get("pageprops", {}).get("wikibase_item")
                if qid not in expected or not page.get("revisions"):
                    continue
                work = expected[qid]
                returned.add(qid)
                new_record, outcome = create_page_record(work, page, fetched.artifact, language, policies)
                done[qid] = outcome
                if new_record:
                    rows = [r for r in rows if r.source_id != new_record.source_id]
                    rows.append(new_record)
            for _, work in batch:
                if work.source_id not in returned:
                    done[work.source_id] = {"status": "withheld", "reason": "missing_revision_or_wikibase_item_mismatch"}
            write_jsonl(rows, output)
            checkpoint["selection"] = selections
            atomic_json(checkpoint_path, checkpoint)
            print(json.dumps({"language": language, "processed": len(done), "records": len(rows), "allowed_evidence": sum(e.llm_input_allowed for r in rows for e in r.evidence)}), flush=True)
    print(json.dumps({"status": "complete", "records": len(rows), "withheld": len(selections) + sum(v.get("status") == "withheld" for v in done.values()), "output": str(output)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
