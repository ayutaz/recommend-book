"""
Jikan API (MyAnimeList非公式API) からライトノベル・マンガデータを大規模収集するスクリプト。

収集対象:
  - ライトノベル (type=lightnovel): has_next_page=falseまで全件取得（order_by=members）
  - マンガ上位 (type=manga): 80ページ = 2,000件（order_by=score）

エンドポイント: https://api.jikan.moe/v4/manga
認証: 不要
レートリミット: 60リクエスト/分、2リクエスト/秒
出力: data/jikan_manga_full.json
"""

import json
import time
import signal
import sys
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
from collections import Counter

# ----------------------------- 設定 -----------------------------

API_BASE = "https://api.jikan.moe/v4"
SLEEP_SEC = 3          # リクエスト間のスリープ（秒）— Jikanは429が出やすいため余裕を持つ
RETRY_WAIT = 10        # 429エラー時のリトライ待機（秒）
MAX_RETRIES = 5        # 最大リトライ回数
PER_PAGE = 25          # 1ページあたりの取得件数（API最大値）
CONSECUTIVE_429_LIMIT = 3  # 連続429回数の上限（これを超えたらスリープを増やす）

# 収集カテゴリ定義
CATEGORIES = [
    {
        "type": "lightnovel",
        "name": "ライトノベル",
        "order_by": "members",
        "sort": "desc",
        "max_pages": None,  # has_next_page=falseまで取得
    },
    {
        "type": "manga",
        "name": "マンガ上位",
        "order_by": "score",
        "sort": "desc",
        "max_pages": 80,  # 80ページ = 2,000件
    },
]

# 出力先
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "jikan_manga_full.json"
INTERIM_FILE = OUTPUT_DIR / "jikan_manga_full_interim.json"

# グローバル状態（シグナルハンドラ用）
_all_data = []
_interrupted = False


def signal_handler(signum, frame):
    """Ctrl+Cで中断された場合、収集済みデータを保存して終了する。"""
    global _interrupted
    _interrupted = True
    print(f"\n\n[中断] シグナル {signum} を受信しました。")
    print(f"収集済みデータ {len(_all_data)} 件を保存して終了します...")
    save_data(_all_data, interrupted=True)
    sys.exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


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


def fetch_page(manga_type: str, order_by: str, sort: str, page: int,
               current_sleep: float) -> tuple:
    """
    指定ページのデータを取得する。

    Returns:
        (データリスト, 次ページが存在するか, 新しいスリープ秒数)
    """
    params = {
        "type": manga_type,
        "order_by": order_by,
        "sort": sort,
        "page": page,
        "limit": PER_PAGE,
        "sfw": "true",
    }

    url = f"{API_BASE}/manga?{urllib.parse.urlencode(params)}"
    sleep_sec = current_sleep

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent",
                           "JikanFullCollector/1.0 (book-recommend-project)")
            req.add_header("Accept", "application/json")

            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            items = data.get("data", [])
            pagination = data.get("pagination", {})
            has_next = pagination.get("has_next_page", False)
            total = pagination.get("last_visible_page", "?")

            return items, has_next, sleep_sec, total

        except urllib.error.HTTPError as e:
            if e.code == 429:
                if attempt < MAX_RETRIES:
                    wait = RETRY_WAIT * (attempt + 1)
                    print(f"    [429] レートリミット。{wait}秒待機してリトライ "
                          f"({attempt + 1}/{MAX_RETRIES})...")
                    time.sleep(wait)
                    # 429が出たらスリープを増やす
                    if sleep_sec < 5:
                        sleep_sec = 5
                        print(f"    [調整] リクエスト間スリープを {sleep_sec}秒 に増加")
                    continue
                else:
                    print(f"    [429] リトライ上限到達。このページをスキップします。")
                    return [], False, sleep_sec, "?"
            elif e.code == 400:
                # ページ範囲外の可能性（Jikanのページネーション上限）
                print(f"    [400] Bad Request — ページネーション上限の可能性。収集を終了します。")
                return [], False, sleep_sec, "?"
            else:
                print(f"    [HTTPError {e.code}] {e.reason}")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT)
                    continue
                return [], False, sleep_sec, "?"

        except Exception as e:
            print(f"    [Error] {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_WAIT)
                continue
            return [], False, sleep_sec, "?"

    return [], False, sleep_sec, "?"


def save_data(all_data: list, interrupted: bool = False):
    """データをJSONファイルに保存する。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # mal_idで重複除去
    seen = set()
    deduped = []
    for item in all_data:
        mal_id = item.get("mal_id")
        if mal_id and mal_id not in seen:
            seen.add(mal_id)
            deduped.append(item)

    output_path = OUTPUT_FILE if not interrupted else INTERIM_FILE
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    print(f"  -> {len(deduped)} 件を {output_path} に保存しました。")
    return deduped


def collect_category(cat: dict) -> list:
    """指定カテゴリのデータをページネーションで収集する。"""
    global _all_data

    manga_type = cat["type"]
    name = cat["name"]
    order_by = cat["order_by"]
    sort = cat["sort"]
    max_pages = cat.get("max_pages")

    all_items = []
    page = 1
    sleep_sec = SLEEP_SEC
    consecutive_empty = 0

    while True:
        # ページ上限チェック
        if max_pages and page > max_pages:
            print(f"  ページ上限 ({max_pages}) に到達。収集を終了します。")
            break

        page_label = f"{page}" if not max_pages else f"{page}/{max_pages}"
        print(f"  ページ {page_label} を取得中...")

        items, has_next, sleep_sec, total_pages = fetch_page(
            manga_type, order_by, sort, page, sleep_sec
        )

        if not items:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                print(f"  3回連続でデータなし。収集を終了します。")
                break
            print(f"  ページ {page}: データなし（連続{consecutive_empty}回）。"
                  f"次ページを試みます...")
            page += 1
            time.sleep(sleep_sec)
            continue
        else:
            consecutive_empty = 0

        extracted = [extract_entry(item) for item in items]
        # カテゴリ情報を付与
        for item in extracted:
            item["category"] = name
        all_items.extend(extracted)

        print(f"    -> {len(items)} 件取得（累計: {len(all_items)} 件, "
              f"API総ページ: {total_pages}）")

        # 100ページごとに途中保存
        if page % 100 == 0:
            print(f"\n  [途中保存] {len(_all_data) + len(all_items)} 件を保存中...")
            save_data(_all_data + all_items, interrupted=True)
            print()

        # 次のページがない場合
        if not has_next:
            print(f"  has_next_page=false。全ページ取得完了。")
            break

        page += 1
        time.sleep(sleep_sec)

    return all_items


def print_summary(all_data: list, category_summary: dict):
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
            bar = "#" * (count // 10) if count > 0 else ""
            print(f"    {bracket:10s}: {count:5d} 件  {bar}")
    print()

    # ジャンル集計
    genre_counter = Counter()
    theme_counter = Counter()
    demo_counter = Counter()
    for d in all_data:
        for g in d.get("genres", []):
            genre_counter[g] += 1
        for t in d.get("themes", []):
            theme_counter[t] += 1
        for dem in d.get("demographics", []):
            demo_counter[dem] += 1

    if genre_counter:
        print("ジャンル上位15:")
        for genre, count in genre_counter.most_common(15):
            print(f"  {genre:25s}: {count:5d} 件")
    print()

    if theme_counter:
        print("テーマ上位15:")
        for theme, count in theme_counter.most_common(15):
            print(f"  {theme:25s}: {count:5d} 件")
    print()

    if demo_counter:
        print("デモグラフィック:")
        for dem, count in demo_counter.most_common():
            print(f"  {dem:25s}: {count:5d} 件")
    print()

    # データ品質
    synopsis_count = sum(1 for d in all_data if d.get("synopsis"))
    authors_count = sum(1 for d in all_data if d.get("authors"))
    score_count = sum(1 for d in all_data if d.get("score") is not None)
    jp_title_count = sum(1 for d in all_data if d.get("title_japanese"))

    if all_data:
        n = len(all_data)
        print("データ品質:")
        print(f"  あらすじあり:     {synopsis_count:5d}/{n} ({synopsis_count/n*100:.1f}%)")
        print(f"  日本語タイトル:   {jp_title_count:5d}/{n} ({jp_title_count/n*100:.1f}%)")
        print(f"  スコアあり:       {score_count:5d}/{n} ({score_count/n*100:.1f}%)")
        print(f"  著者情報あり:     {authors_count:5d}/{n} ({authors_count/n*100:.1f}%)")
    print()

    # タイプ別内訳
    type_counter = Counter(d.get("type", "不明") for d in all_data)
    print("タイプ別内訳:")
    for t, count in type_counter.most_common():
        print(f"  {t:25s}: {count:5d} 件")
    print()

    # 上位10件表示
    scored_items = [d for d in all_data if d.get("score") is not None]
    if scored_items:
        top10 = sorted(scored_items, key=lambda x: x["score"], reverse=True)[:10]
        print("スコア上位10作品:")
        for i, item in enumerate(top10, 1):
            title = item.get("title", "不明")
            title_jp = item.get("title_japanese", "")
            score = item.get("score", 0)
            item_type = item.get("type", "不明")
            genres = ", ".join(item.get("genres", [])[:3])
            jp_display = f" / {title_jp}" if title_jp else ""
            print(f"  {i:2d}. [{item_type}] {title}{jp_display}")
            print(f"      スコア: {score:.2f}  ジャンル: {genres}")


def main():
    global _all_data

    print("=" * 60)
    print("Jikan API (MyAnimeList) 大規模データ収集スクリプト")
    print(f"開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print(f"\n収集計画:")
    for cat in CATEGORIES:
        pages_desc = "全ページ" if cat.get("max_pages") is None else f"{cat['max_pages']}ページ"
        print(f"  - {cat['name']} (type={cat['type']}, "
              f"order_by={cat['order_by']}, {pages_desc})")
    print(f"\nリクエスト間スリープ: {SLEEP_SEC}秒")
    print(f"429リトライ待機: {RETRY_WAIT}秒 × 最大{MAX_RETRIES}回")
    print()

    category_summary = {}

    for i, cat in enumerate(CATEGORIES):
        name = cat["name"]
        print(f"\n{'=' * 50}")
        print(f"[{i+1}/{len(CATEGORIES)}] {name} (type={cat['type']}) を収集中...")
        print(f"  ソート: {cat['order_by']} {cat['sort']}")
        pages_desc = "全ページ" if cat.get("max_pages") is None else f"最大{cat['max_pages']}ページ"
        print(f"  ページ: {pages_desc}")
        print("=" * 50)

        try:
            items = collect_category(cat)
            _all_data.extend(items)
            category_summary[name] = len(items)
            print(f"\n  -> {name}: {len(items)} 件取得完了")
        except Exception as e:
            print(f"  [エラー] {name} の収集に失敗: {e}")
            category_summary[name] = 0
            # エラーが発生しても途中データは保存
            if _all_data:
                print(f"  [途中保存] {len(_all_data)} 件を保存します...")
                save_data(_all_data, interrupted=True)

        # カテゴリ間のスリープ
        if i < len(CATEGORIES) - 1:
            print(f"\n  次のカテゴリまで5秒待機中...")
            time.sleep(5)

    # 重複除去して最終保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    seen = set()
    deduped = []
    for item in _all_data:
        mal_id = item.get("mal_id")
        if mal_id and mal_id not in seen:
            seen.add(mal_id)
            deduped.append(item)

    dup_count = len(_all_data) - len(deduped)
    if dup_count > 0:
        print(f"\n重複除去: {dup_count} 件の重複を除去しました。")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"\n最終データを {OUTPUT_FILE} に保存しました。（{len(deduped)} 件）")

    # interimファイルがあれば削除
    if INTERIM_FILE.exists():
        INTERIM_FILE.unlink()
        print(f"途中保存ファイル {INTERIM_FILE} を削除しました。")

    # サマリー表示
    print_summary(deduped, category_summary)

    print(f"\n完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
