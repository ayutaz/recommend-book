"""
AniList GraphQL API からライトノベル・マンガ・アニメデータを収集するスクリプト。

収集対象:
  - ライトノベル: type=MANGA, format=NOVEL, sort=POPULARITY_DESC (500件)
  - マンガ: type=MANGA, format=MANGA, sort=POPULARITY_DESC (500件)
  - アニメ: type=ANIME, sort=POPULARITY_DESC (500件)

出力: data/anilist_media.json
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

ANILIST_URL = "https://graphql.anilist.co"
PER_PAGE = 50
TOTAL_PAGES = 10  # 10 pages x 50 = 500 items per category
SLEEP_SEC = 1.0

# GraphQL query template
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

# Category definitions: (label, GraphQL filter fragment)
CATEGORIES = [
    ("light_novel", "type: MANGA, format: NOVEL"),
    ("manga", "type: MANGA, format: MANGA"),
    ("anime", "type: ANIME"),
]


def run_query(query: str, variables: dict) -> dict:
    """Execute a GraphQL query against the AniList API."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        ANILIST_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        },
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited (429). Waiting {wait}s before retry {attempt + 1}/{max_retries}...")
                time.sleep(wait)
                continue
            else:
                raise RuntimeError(f"HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  Network error: {e.reason}. Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise

    raise RuntimeError("Max retries exceeded")


def collect_category(label: str, filter_fragment: str) -> list[dict]:
    """Collect all pages for a given category."""
    query = QUERY % {"filter": filter_fragment}
    items = []

    for page_num in range(1, TOTAL_PAGES + 1):
        variables = {"page": page_num, "perPage": PER_PAGE}
        print(f"  [{label}] page {page_num}/{TOTAL_PAGES} ...", end=" ", flush=True)

        result = run_query(query, variables)
        page_data = result.get("data", {}).get("Page", {})
        media_list = page_data.get("media", [])
        page_info = page_data.get("pageInfo", {})

        items.extend(media_list)
        print(f"got {len(media_list)} items (total so far: {len(items)})")

        # Stop early if no more pages
        if not page_info.get("hasNextPage", False):
            print(f"  [{label}] No more pages available. Stopping at page {page_num}.")
            break

        # Sleep between requests (skip after last page)
        if page_num < TOTAL_PAGES:
            time.sleep(SLEEP_SEC)

    return items


def main():
    output_dir = Path(__file__).resolve().parent.parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "anilist_media.json"

    all_data = {}
    total_count = 0

    print("=" * 60)
    print("AniList API Data Collection")
    print("=" * 60)

    for label, filter_fragment in CATEGORIES:
        print(f"\n--- Collecting: {label} ---")
        items = collect_category(label, filter_fragment)
        all_data[label] = items
        total_count += len(items)
        print(f"  [{label}] Collected {len(items)} items.")
        # Sleep between categories
        time.sleep(SLEEP_SEC)

    # Save to JSON
    output = {
        "metadata": {
            "source": "AniList GraphQL API",
            "endpoint": ANILIST_URL,
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "categories": {
                label: len(all_data[label]) for label, _ in CATEGORIES
            },
            "total_items": total_count,
        },
        "data": all_data,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print("Collection Summary")
    print(f"{'=' * 60}")
    for label, _ in CATEGORIES:
        count = len(all_data[label])
        print(f"  {label:20s}: {count:5d} items")
    print(f"  {'TOTAL':20s}: {total_count:5d} items")
    print(f"\nData saved to: {output_path}")
    print(f"File size: {output_path.stat().st_size / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
