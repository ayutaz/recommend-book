"""Read-only network probes; save response evidence without downloading bulk dumps."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import httpx


def main() -> None:
    target = Path("data/probes")
    target.mkdir(parents=True, exist_ok=True)
    query = "SELECT DISTINCT ?work WHERE { ?work wdt:P31 wd:Q8261 } LIMIT 20"
    probes = [
        ("wikidata-novels", "https://query.wikidata.org/sparql", {"query": query, "format": "json"}, "GET"),
        ("openlibrary-works", "https://openlibrary.org/data/ol_dump_works_latest.txt.gz", None, "HEAD"),
        ("openlibrary-authors", "https://openlibrary.org/data/ol_dump_authors_latest.txt.gz", None, "HEAD"),
        ("wikipedia-api", "https://en.wikipedia.org/w/api.php", {"action": "query", "format": "json", "titles": "The Murder of Roger Ackroyd", "prop": "info"}, "GET"),
    ]
    report = []
    with httpx.Client(timeout=75, follow_redirects=True, headers={"User-Agent": "recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)", "Accept": "application/json"}) as client:
        for name, url, params, method in probes:
            try:
                response = client.request(method, url, params=params)
                item = {"name": name, "requested_url": url, "final_url": str(response.url), "status": response.status_code, "content_type": response.headers.get("content-type"), "content_length": response.headers.get("content-length"), "etag": response.headers.get("etag"), "checked_at": datetime.now(timezone.utc).isoformat()}
                if method == "GET":
                    (target / f"{name}.response").write_bytes(response.content)
                    item["response_bytes"] = len(response.content)
            except httpx.HTTPError as error:
                item = {"name": name, "status": "failed", "error": str(error)}
            report.append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
    (target / "sources.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
