"""Re-screen saved immutable article API responses locally without new requests."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from enrich_wikipedia_poc import create_page_record
from recommend_book.ingestion.core import RawArtifact, read_jsonl, write_jsonl
from recommend_book.rights import RightsRegistry
from recommend_book.wikipedia_prose import SCREEN_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--raw-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    works = {w.source_id: w for w in read_jsonl(args.input)}
    pages, seen = {}, set()
    for line in args.raw_manifest.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["source"] != "wikipedia" or row.get("metadata", {}).get("status_code") != 200 or row["sha256"] in seen:
            continue
        seen.add(row["sha256"])
        payload_bytes = Path(row["path"]).read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != row["sha256"]:
            raise ValueError("Saved article raw hash mismatch")
        language = urlparse(row["source_url"]).hostname.split(".")[0]
        artifact = RawArtifact(**{key: row[key] for key in RawArtifact.__dataclass_fields__})
        for page in json.loads(payload_bytes).get("query", {}).get("pages", []):
            qid = page.get("pageprops", {}).get("wikibase_item")
            if qid in works and page.get("revisions"):
                pages.setdefault(qid, []).append((language, page, artifact))
    rows, outcomes = [], {}
    registry = RightsRegistry.default()
    for qid, candidates in pages.items():
        work = works[qid]
        if not any(c.role == "author" for c in work.contributors):
            continue
        preferences = list(dict.fromkeys(["en", work.original_language, "ja", "fr", "de", "es"]))
        language, page, artifact = min(candidates, key=lambda candidate: preferences.index(candidate[0]) if candidate[0] in preferences else len(preferences))
        record, outcome = create_page_record(work, page, artifact, language, registry)
        outcomes[qid] = outcome
        if record:
            rows.append(record)
    write_jsonl(rows, args.output / "records.jsonl")
    manifest = {"status": "local_rescreen_complete", "screen_version": SCREEN_VERSION, "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(), "raw_manifest_sha256": hashlib.sha256(args.raw_manifest.read_bytes()).hexdigest(), "verified_raw_responses": len(seen), "available_article_works": len(pages), "records": len(rows), "allowed_evidence": sum(e.llm_input_allowed for r in rows for e in r.evidence), "article_languages": dict(Counter(r.extra["article_language"] for r in rows)), "network_requests": 0, "human_reviewed": False, "outcomes": outcomes}
    (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "outcomes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
