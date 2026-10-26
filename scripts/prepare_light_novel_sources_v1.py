"""Prepare revision-pinned, screened series prose; never invoke an LLM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from recommend_book.ingestion.core import HTTPFetcher, RawStore
from recommend_book.light_novel_classification_v1 import (
    SeriesInput, SOURCE_VERSION, TERMS_URL, WIKIDATA_TERMS, LICENSE_URL,
    series_source_policy, build_series_article_evidence, save, sha, digest,
    validate_source_admission, build_series_metadata_evidence,
)


def prepare(metadata_path, output, *, qids=None, limit=100, languages=("ja", "en"), fetcher=None):
    metadata_path = Path(metadata_path).resolve(strict=True)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Wikimedia's User-Agent policy requires descriptive client/contact details;
    # identify this actual public project, never impersonate a browser.
    fetcher = fetcher or HTTPFetcher(RawStore(output.parent / "source-raw"), min_interval=1, max_attempts=2,
        user_agent="recommend-book-series-bot/0.1 (https://github.com/ayutaz/recommend-book) httpx")
    records = [json.loads(line) for line in metadata_path.read_bytes().splitlines() if line.strip()]
    records = [row for row in records if row.get("kind") == "series" and row.get("admission", {}).get("accepted")
        and (qids is None or row["id"] in qids)][:limit]
    # Acquisition can keep extending independently; freeze only the selected
    # authority rows here instead of pinning its mutable aggregate file.
    metadata_path = output / "metadata-snapshot.jsonl"
    with metadata_path.open("x", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    policy_path = output / "series-source-policy.json"
    save(policy_path, series_source_policy())
    pins = {str(metadata_path): sha(metadata_path), str(policy_path): sha(policy_path)}
    conditions = {}
    # A bundle refers to immutable official terms snapshots, not to "latest"
    # legal text. Reuse the already verified snapshots during this expansion;
    # preserve their exact origin/hash instead of requesting them for each 100.
    for previous in sorted(output.parent.glob("source-*/source-manifest.json")):
        document = json.loads(previous.read_bytes())
        candidate = document.get("conditions", {})
        if set(candidate) != {TERMS_URL, WIKIDATA_TERMS, LICENSE_URL}:
            continue
        if all(Path(pin["path"]).is_file() and sha(pin["path"]) == pin["sha256"] for pin in candidate.values()):
            conditions = candidate
            pins.update({pin["path"]: pin["sha256"] for pin in conditions.values()})
            break
    for url in (TERMS_URL, WIKIDATA_TERMS, LICENSE_URL):
        if url in conditions:
            continue
        response = fetcher.get(url, source="series-conditions")
        if response.status != "success":
            raise ValueError("Official series source/output terms unavailable; no model calls")
        artifact = response.artifact
        pins[artifact.path] = artifact.sha256
        conditions[url] = {"path": artifact.path, "sha256": artifact.sha256}
    entries, drafts, held = [], [], []
    for record in records:
        qid = record["id"]
        evidence, receipts, articles = [], [], []
        provenance = record["provenance"]
        for pin in [provenance, *provenance.get("linked_provenance", {}).values()]:
            pins[str(Path(pin["raw_path"]).resolve())] = pin["raw_sha256"]
        conditions_path = Path(provenance["conditions_path"]).resolve()
        pins[str(conditions_path)] = provenance["conditions_sha256"]
        for language in languages:
            title = record.get("sitelinks", {}).get(language + "wiki", {}).get("title")
            if not title:
                continue
            response = fetcher.get(f"https://{language}.wikipedia.org/w/api.php", source="series-wikipedia", params={
                "action": "query", "prop": "revisions|pageprops", "titles": title, "rvprop": "ids|timestamp|content",
                "rvslots": "main", "ppprop": "wikibase_item|disambiguation", "format": "json", "formatversion": 2})
            if response.status != "success":
                held.append({"id": qid, "language": language, "reason": "wikipedia_http_failure"})
                continue
            artifact = response.artifact
            pins[artifact.path] = artifact.sha256
            payload = json.loads(response.content)
            pages = payload.get("query", {}).get("pages", [])
            if isinstance(pages, dict):
                pages = list(pages.values())
            if len(pages) != 1:
                held.append({"id": qid, "language": language, "reason": "wikipedia_exact_page_missing"})
                continue
            page = pages[0]
            try:
                proposed, proposed_receipts, outcomes = build_series_article_evidence(record, page,
                    {"path": artifact.path, "sha256": artifact.sha256}, language)
            except ValueError as exc:
                held.append({"id": qid, "language": language, "reason": str(exc)})
                continue
            if proposed:
                evidence.extend(proposed)
                receipts.extend(proposed_receipts)
                articles.append({"raw_path": artifact.path, "raw_sha256": artifact.sha256,
                    "language": language, "page_id": page["pageid"], "revision_id": page["revisions"][0]["revid"], "outcomes": outcomes})
                # One complete article language is sufficient. Fall back to a
                # second language only when the first yields no usable prose.
                break
        if not evidence:
            held.append({"id": qid, "reason": "no_eligible_screened_series_prose"})
            continue
        series_id = "lns:wd:" + qid
        metadata_evidence, metadata_receipts = build_series_metadata_evidence(record)
        evidence = metadata_evidence + evidence
        receipts = metadata_receipts + receipts
        entries.append({"series_id": series_id, "metadata_row_sha256": digest(record),
            "articles": articles, "evidence_receipts": receipts, "structured_metadata_evidence": True})
        drafts.append({"series_id": series_id, "title": record["title"], "authors": [row["label"] for row in record["authors"]],
            "original_language": "ja", "evidence": [item.model_dump(mode="json") for item in evidence],
            "source_record_ids": ["wikidata:" + qid]})
    manifest = {"version": SOURCE_VERSION, "policy_path": str(policy_path), "metadata_path": str(metadata_path),
        "conditions": conditions, "series": entries, "held": held, "artifact_sha256": pins,
        "model_calls": 0, "series_admitted": len(entries), "series_selected": len(records),
        "scope": "series", "unknown_style_required": True, "automated_screening_only": True}
    manifest_path = output / "source-manifest.json"
    save(manifest_path, manifest)
    inputs = [SeriesInput(**row, source_manifest_sha256=sha(manifest_path)) for row in drafts]
    with (output / "inputs.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for series in inputs:
            stream.write(series.model_dump_json() + "\n")
    if inputs:
        validate_source_admission(inputs, manifest_path, sha(manifest_path))
    return {"source_manifest": str(manifest_path), "source_sha256": sha(manifest_path),
        "inputs": str(output / "inputs.jsonl"), "admitted_series": len(inputs), "selected_series": len(records), "held": held, "model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--qids", nargs="*")
    parser.add_argument("--languages", nargs="+", default=["ja", "en"])
    args = parser.parse_args()
    if not 1 <= args.limit <= 1500 or any(language not in ("ja", "en", "fr", "de", "zh", "es", "it", "ko", "ru") for language in args.languages):
        raise ValueError("Bounded series preparation and explicit supported languages required")
    print(json.dumps(prepare(args.metadata, args.output, qids=args.qids, limit=args.limit, languages=args.languages), ensure_ascii=False))


if __name__ == "__main__":
    main()
