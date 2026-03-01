"""
Jikan API (MyAnimeList非公式API) からライトノベル・マンガデータを収集するスクリプト。

収集対象:
  - ライトノベル (type=lightnovel): 500件（スコア降順）
  - マンガ (type=manga): 500件（スコア降順）

エンドポイント: https://api.jikan.moe/v4/manga
認証: 不要
レートリミット: 60リクエスト/分、2リクエスト/秒
出力: data/jikan_manga.json
"""

import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from collections import Counter

# ----------------------------- 設定 -----------------------------

API_BASE = "https://api.jikan.moe/v4"
SLEEP_SEC = 2          # リクエスト間のスリープ（秒）
RETRY_WAIT = 5         # 429エラー時のリトライ待機（秒）
MAX_RETRIES = 3        # 最大リトライ回数
PER_PAGE = 25          # 1ページあたりの取得件数（API最大値）
TARGET_COUNT = 500     # カテゴリあたりの目標件数

# 収集カテゴリ
CATEGORIES = [
    {"type": "lightnovel", "name": "ライトノベル"},
    {"type": "manga", "name": "マンガ"},
]

# 出力先
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "jikan_manga.json"


def extract_entry(item: dict) -> dict:
    """APIレスポンスから必要なフィールドを抽出する。"""
    published = item.get("published", {})
    published_info = {
        "from": published.get("from"),
        "to": published.get("to"),
        "string": published.get("string"),
    } if published else None

    return {
        "mal_id": item.get("mal_id"),
        "title": item.get("title"),
        "title_japanese": item.get("title_japanese"),
        "synopsis": item.get("synopsis"),
        "score": item.get("score"),
        "scored_by": item.get("scored_by"),
        "rank": item.get("rank"),
        "popularity": item.get("popularity"),
        "genres": [g.get("name") for g in (item.get("genres") or [])],
        "themes": [t.get("name") for t in (item.get("themes") or [])],
        "demographics": [d.get("name") for d in (item.get("demographics") or [])],
        "authors": [
            {"name": a.get("name"), "mal_id": a.get("mal_id")}
            for a in (item.get("authors") or [])
        ],
        "type": item.get("type"),
        "status": item.get("status"),
        "published": published_info,
    }


def fetch_page(manga_type: str, page: int) -> tuple[list[dict], bool]:
    """
    指定ページのデータを取得する。

    Returns:
        (データリスト, 次ページが存在するか)
    """
    params = {
        "type": manga_type,
        "order_by": "score",
        "sort": "desc",
        "page": page,
        "limit": PER_PAGE,
        "sfw": "true",  # SFWフィルタ（安全なコンテンツのみ）
    }

    url = f"{API_BASE}/manga?{urllib.parse.urlencode(params)}"

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "JikanCollector/1.0 (book-recommend-project)")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("data", [])
            pagination = data.get("pagination", {})
            has_next = pagination.get("has_next_page", False)

            return items, has_next

        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt < MAX_RETRIES:
                    wait = RETRY_WAIT * (attempt + 1)
                    print(f"    [429] レートリミット。{wait}秒待機してリトライ "
                          f"({attempt + 1}/{MAX_RETRIES})...")
                    time.sleep(wait)
                    continue
                else:
                    print(f"    [429] リトライ上限到達。このページをスキップします。")
                    return [], False
            else:
                print(f"    [HTTPError {e.code}] {e.reason}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT)
                    continue
                return [], False

        except Exception as e:
            print(f"    [Error] {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT)
                continue
            return [], False

    return [], False


def collect_category(manga_type: str, name: str) -> list[dict]:
    """指定カテゴリのデータをページネーションで収集する。"""
    all_items = []
    total_pages = (TARGET_COUNT + PER_PAGE - 1) // PER_PAGE  # 切り上げ

    for page in range(1, total_pages + 1):
        print(f"  ページ {page}/{total_pages} を取得中...")
        items, has_next = fetch_page(manga_type, page)

        if not items:
            print(f"  ページ {page}: データなし。収集を終了します。")
            break

        extracted = [extract_entry(item) for item in items]
        all_items.extend(extracted)
        print(f"    -> {len(items)} 件取得（累計: {len(all_items)} 件）")

        # 目標件数に達したら終了
        if len(all_items) >= TARGET_COUNT:
            all_items = all_items[:TARGET_COUNT]
            break

        # 次のページがない場合
        if not has_next:
            print(f"  次のページなし。収集を終了します。")
            break

        # レートリミット対策: 2秒スリープ
        time.sleep(SLEEP_SEC)

    return all_items


def print_summary(all_data: list[dict], category_summary: dict):
    """収集結果のサマリーを表示する。"""
    print("\n" + "=" * 60)
    print("収集結果サマリー")
    print("=" * 60)
    print(f"出力ファイル: {OUTPUT_FILE}")
    print(f"合計件数:     {len(all_data)} 件")
    print()

    # カテゴリ別内訳
    print("カテゴリ別内訳:")
    for name, count in category_summary.items():
        print(f"  {name:16s}: {count:5d} 件")
    print()

    # スコア分布
    scores = [d["score"] for d in all_data if d.get("score") is not None]
    if scores:
        print("スコア分布:")
        print(f"  最高スコア: {max(scores):.2f}")
        print(f"  最低スコア: {min(scores):.2f}")
        print(f"  平均スコア: {sum(scores) / len(scores):.2f}")
        print(f"  スコアあり: {len(scores)}/{len(all_data)} 件")

        # スコア帯別分布
        brackets = {"9.0以上": 0, "8.0-8.99": 0, "7.0-7.99": 0,
                     "6.0-6.99": 0, "6.0未満": 0}
        for s in scores:
            if s >= 9.0:
                brackets["9.0以上"] += 1
            elif s >= 8.0:
                brackets["8.0-8.99"] += 1
            elif s >= 7.0:
                brackets["7.0-7.99"] += 1
            elif s >= 6.0:
                brackets["6.0-6.99"] += 1
            else:
                brackets["6.0未満"] += 1

        print("\n  スコア帯別:")
        for bracket, count in brackets.items():
            bar = "#" * (count // 5) if count > 0 else ""
            print(f"    {bracket:10s}: {count:4d} 件  {bar}")
    print()

    # ジャンル集計
    genre_counter = Counter()
    theme_counter = Counter()
    for d in all_data:
        for g in d.get("genres", []):
            genre_counter[g] += 1
        for t in d.get("themes", []):
            theme_counter[t] += 1

    if genre_counter:
        print("ジャンル上位10:")
        for genre, count in genre_counter.most_common(10):
            print(f"  {genre:25s}: {count:4d} 件")
    print()

    if theme_counter:
        print("テーマ上位10:")
        for theme, count in theme_counter.most_common(10):
            print(f"  {theme:25s}: {count:4d} 件")
    print()

    # データ品質
    synopsis_count = sum(1 for d in all_data if d.get("synopsis"))
    authors_count = sum(1 for d in all_data if d.get("authors"))
    print("データ品質:")
    print(f"  あらすじあり:   {synopsis_count}/{len(all_data)} "
          f"({synopsis_count/len(all_data)*100:.1f}%)" if all_data else "  データなし")
    print(f"  著者情報あり:   {authors_count}/{len(all_data)} "
          f"({authors_count/len(all_data)*100:.1f}%)" if all_data else "")
    print()

    # 上位5件表示
    scored_items = [d for d in all_data if d.get("score") is not None]
    if scored_items:
        top5 = sorted(scored_items, key=lambda x: x["score"], reverse=True)[:5]
        print("スコア上位5作品:")
        for i, item in enumerate(top5, 1):
            title = item.get("title", "不明")
            title_jp = item.get("title_japanese", "")
            score = item.get("score", 0)
            item_type = item.get("type", "不明")
            genres = ", ".join(item.get("genres", [])[:3])
            jp_display = f" / {title_jp}" if title_jp else ""
            print(f"  {i}. [{item_type}] {title}{jp_display}")
            print(f"     スコア: {score:.2f}  ジャンル: {genres}")


def main():
    print("=" * 60)
    print("Jikan API (MyAnimeList) データ収集スクリプト")
    print(f"開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_data = []
    category_summary = {}

    for i, cat in enumerate(CATEGORIES):
        manga_type = cat["type"]
        name = cat["name"]
        print(f"\n[{i+1}/{len(CATEGORIES)}] {name} (type={manga_type}) を収集中...")
        print(f"  目標: {TARGET_COUNT} 件（{PER_PAGE}件/ページ × "
              f"{(TARGET_COUNT + PER_PAGE - 1) // PER_PAGE}ページ）")

        try:
            items = collect_category(manga_type, name)
            # カテゴリ情報を付与
            for item in items:
                item["category"] = name
            all_data.extend(items)
            category_summary[name] = len(items)
            print(f"  -> {name}: {len(items)} 件取得完了")
        except Exception as e:
            print(f"  [エラー] {name} の収集に失敗: {e}")
            category_summary[name] = 0

        # カテゴリ間のスリープ
        if i < len(CATEGORIES) - 1:
            print(f"\n  次のカテゴリまで{SLEEP_SEC}秒待機中...")
            time.sleep(SLEEP_SEC)

    # 出力ディレクトリ作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON出力
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    # サマリー表示
    print_summary(all_data, category_summary)

    print(f"\n完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
