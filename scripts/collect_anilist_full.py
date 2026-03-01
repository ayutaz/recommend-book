"""
AniList GraphQL API からライトノベル全件 + マンガ上位 + ONE_SHOT を収集するスクリプト。

収集戦略:
  1. ライトノベル全件: type=MANGA, format=NOVEL, sort=POPULARITY_DESC
     → hasNextPage=false になるまで全ページ取得
  2. マンガ上位: type=MANGA, format=MANGA, sort=POPULARITY_DESC
     → 最大100ページ (5,000件)
  3. ONE_SHOT: type=MANGA, format=ONE_SHOT, sort=POPULARITY_DESC
     → 最大20ページ (1,000件)

レートリミット対策:
  - リクエスト間 0.7秒スリープ (90req/min 以内)
  - 429エラー時 30秒待機してリトライ (最大5回)
  - User-Agent はブラウザ互換 (Cloudflare対策)

出力: data/anilist_media_full.json
"""

import json
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

ANILIST_URL = "https://graphql.anilist.co"
PER_PAGE = 50
SLEEP_SEC = 0.7
OUTPUT_FILENAME = "anilist_media_full.json"

# Browser-compatible User-Agent for Cloudflare
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# GraphQL query template - %(filter)s is replaced per category
QUERY = """
query ($page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
    }
    media(%(filter)s, sort: POPULARITY_DESC) {
      id
      title {
        romaji
        native
        english
      }
      description
      genres
      tags {
        name
        rank
      }
      averageScore
      popularity
      format
      status
      startDate {
        year
        month
      }
      countryOfOrigin
      isAdult
      relations {
        edges {
          relationType
          node {
            id
            title {
              romaji
              native
            }
            type
            format
          }
        }
      }
    }
  }
}
"""

# Category definitions: (label, GraphQL filter, max_pages or None for unlimited)
CATEGORIES = [
    ("light_novel", "type: MANGA, format: NOVEL", None),      # 全件
    ("manga",       "type: MANGA, format: MANGA", 100),       # 上位5000件
    ("one_shot",    "type: MANGA, format: ONE_SHOT", 20),      # 上位1000件
]


def run_query(query: str, variables: dict) -> dict:
    """Execute a GraphQL query against the AniList API with retry logic."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        ANILIST_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"    Rate limited (429). Waiting {wait}s ... (retry {attempt + 1}/{max_retries})")
                time.sleep(wait)
                # Rebuild the request object (consumed after read)
                req = urllib.request.Request(
                    ANILIST_URL,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                )
                continue
            elif e.code >= 500:
                wait = 10 * (attempt + 1)
                print(f"    Server error ({e.code}). Waiting {wait}s ... (retry {attempt + 1}/{max_retries})")
                time.sleep(wait)
                req = urllib.request.Request(
                    ANILIST_URL,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                )
                continue
            else:
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"    Network error: {e.reason}. Retrying in {wait}s ...")
                time.sleep(wait)
                req = urllib.request.Request(
                    ANILIST_URL,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                )
                continue
            raise

    raise RuntimeError("Max retries exceeded")


def collect_category(label: str, filter_fragment: str, max_pages: Optional[int]) -> List[dict]:
    """Collect pages for a given category.

    If max_pages is None, collects all pages until hasNextPage=false.
    Otherwise, stops at max_pages or when hasNextPage=false (whichever comes first).
    """
    query = QUERY % {"filter": filter_fragment}
    items = []
    page_num = 0
    limit_desc = "ALL" if max_pages is None else str(max_pages)

    while True:
        page_num += 1

        # Check page limit
        if max_pages is not None and page_num > max_pages:
            print(f"  [{label}] Reached max page limit ({max_pages}). Stopping.")
            break

        print(
            f"  [{label}] page {page_num}/{limit_desc} ...",
            end=" ",
            flush=True,
        )

        variables = {"page": page_num, "perPage": PER_PAGE}
        result = run_query(query, variables)

        # Check for errors in response
        if "errors" in result:
            print(f"GraphQL errors: {result['errors']}")
            break

        page_data = result.get("data", {}).get("Page", {})
        media_list = page_data.get("media", [])
        page_info = page_data.get("pageInfo", {})

        items.extend(media_list)
        total_api = page_info.get("total", "?")
        print(f"got {len(media_list)} items (total: {len(items)}/{total_api})")

        # Stop if no more pages
        if not page_info.get("hasNextPage", False):
            print(f"  [{label}] No more pages (hasNextPage=false). Finished at page {page_num}.")
            break

        # Stop if we got an empty page
        if len(media_list) == 0:
            print(f"  [{label}] Empty page received. Stopping.")
            break

        # Rate limit sleep
        time.sleep(SLEEP_SEC)

    return items


def print_summary(all_data: Dict[str, List[dict]], total_count: int) -> None:
    """Print a detailed summary of collected data."""
    print(f"\n{'=' * 70}")
    print("COLLECTION SUMMARY")
    print(f"{'=' * 70}")

    # Category counts
    print(f"\n--- Category Breakdown ---")
    for label in all_data:
        count = len(all_data[label])
        print(f"  {label:20s}: {count:6d} items")
    print(f"  {'TOTAL':20s}: {total_count:6d} items")

    # Deduplicate by ID across all categories
    all_ids = set()
    duplicates = 0
    for label in all_data:
        for item in all_data[label]:
            mid = item.get("id")
            if mid in all_ids:
                duplicates += 1
            else:
                all_ids.add(mid)
    print(f"\n  Unique IDs: {len(all_ids)}, Duplicates across categories: {duplicates}")

    # Merge all items for analysis
    all_items = []
    for label in all_data:
        all_items.extend(all_data[label])

    # Country breakdown
    country_counter = Counter()
    for item in all_items:
        country = item.get("countryOfOrigin", "Unknown")
        country_counter[country] += 1
    print(f"\n--- Country of Origin ---")
    for country, count in country_counter.most_common(10):
        print(f"  {country or 'Unknown':10s}: {count:6d}")

    # Format breakdown
    format_counter = Counter()
    for item in all_items:
        fmt = item.get("format", "Unknown")
        format_counter[fmt] += 1
    print(f"\n--- Format ---")
    for fmt, count in format_counter.most_common():
        print(f"  {fmt or 'Unknown':15s}: {count:6d}")

    # Status breakdown
    status_counter = Counter()
    for item in all_items:
        status = item.get("status", "Unknown")
        status_counter[status] += 1
    print(f"\n--- Status ---")
    for status, count in status_counter.most_common():
        print(f"  {status or 'Unknown':20s}: {count:6d}")

    # isAdult breakdown
    adult_count = sum(1 for item in all_items if item.get("isAdult"))
    print(f"\n--- Adult Content ---")
    print(f"  Adult:     {adult_count:6d}")
    print(f"  Non-adult: {len(all_items) - adult_count:6d}")

    # Unique tags
    tag_counter = Counter()
    for item in all_items:
        for tag in item.get("tags", []):
            tag_counter[tag["name"]] += 1
    print(f"\n--- Tags ---")
    print(f"  Unique tags: {len(tag_counter)}")
    print(f"  Top 20 tags:")
    for tag_name, count in tag_counter.most_common(20):
        print(f"    {tag_name:30s}: {count:5d}")

    # Unique genres
    genre_counter = Counter()
    for item in all_items:
        for genre in item.get("genres", []):
            genre_counter[genre] += 1
    print(f"\n--- Genres ---")
    print(f"  Unique genres: {len(genre_counter)}")
    for genre_name, count in genre_counter.most_common():
        print(f"    {genre_name:25s}: {count:5d}")

    # Description coverage
    with_desc = sum(1 for item in all_items if item.get("description"))
    print(f"\n--- Data Coverage ---")
    print(f"  With description: {with_desc:6d} ({100*with_desc/max(len(all_items),1):.1f}%)")
    with_score = sum(1 for item in all_items if item.get("averageScore") is not None)
    print(f"  With score:       {with_score:6d} ({100*with_score/max(len(all_items),1):.1f}%)")
    with_tags = sum(1 for item in all_items if item.get("tags"))
    print(f"  With tags:        {with_tags:6d} ({100*with_tags/max(len(all_items),1):.1f}%)")
    with_relations = sum(1 for item in all_items if item.get("relations", {}).get("edges"))
    print(f"  With relations:   {with_relations:6d} ({100*with_relations/max(len(all_items),1):.1f}%)")

    # Year distribution (top 10)
    year_counter = Counter()
    for item in all_items:
        year = (item.get("startDate") or {}).get("year")
        if year:
            year_counter[year] += 1
    print(f"\n--- Start Year (Top 15) ---")
    for year, count in sorted(year_counter.most_common(15), key=lambda x: x[0], reverse=True):
        bar = "#" * (count // 20)
        print(f"  {year}: {count:5d} {bar}")


def main():
    output_dir = Path(__file__).resolve().parent.parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME

    all_data = {}  # type: Dict[str, List[dict]]
    total_count = 0
    start_time = time.time()

    print("=" * 70)
    print("AniList API Full Data Collection")
    print("=" * 70)
    print(f"Endpoint: {ANILIST_URL}")
    print(f"Per page: {PER_PAGE}")
    print(f"Sleep:    {SLEEP_SEC}s between requests")
    print(f"Categories:")
    for label, filt, max_p in CATEGORIES:
        limit = "ALL pages" if max_p is None else f"max {max_p} pages ({max_p * PER_PAGE} items)"
        print(f"  - {label}: {filt} [{limit}]")
    print()

    for label, filter_fragment, max_pages in CATEGORIES:
        cat_start = time.time()
        print(f"\n{'─' * 70}")
        print(f"Collecting: {label}")
        print(f"Filter:     {filter_fragment}")
        print(f"Max pages:  {'unlimited' if max_pages is None else max_pages}")
        print(f"{'─' * 70}")

        items = collect_category(label, filter_fragment, max_pages)
        all_data[label] = items
        total_count += len(items)

        cat_elapsed = time.time() - cat_start
        print(f"  [{label}] Done: {len(items)} items in {cat_elapsed:.1f}s")

        # Sleep between categories
        time.sleep(1.0)

    elapsed = time.time() - start_time

    # Build output with metadata
    output = {
        "metadata": {
            "source": "AniList GraphQL API",
            "endpoint": ANILIST_URL,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "collection_duration_sec": round(elapsed, 1),
            "categories": {label: len(all_data[label]) for label, _, _ in CATEGORIES},
            "total_items": total_count,
            "per_page": PER_PAGE,
            "strategy": {
                "light_novel": "ALL pages (hasNextPage=false)",
                "manga": "Top 5000 (100 pages)",
                "one_shot": "Top 1000 (20 pages)",
            },
        },
        "data": all_data,
    }

    # Save
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Print summary
    print_summary(all_data, total_count)

    print(f"\n{'=' * 70}")
    print(f"Data saved to: {output_path}")
    print(f"File size:     {output_path.stat().st_size / 1024 / 1024:.2f} MB")
    print(f"Total time:    {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
