"""
なろう小説APIから大量データを収集するスクリプト。

APIの st パラメータ上限が 2000 であることを踏まえ、
各クエリ条件あたり最大 2,000 件（4ページ x 500件）を取得する。
サブジャンル・ソート順・フラグを組み合わせて多数のクエリを発行し、
ncodeベースの重複除去で 20,000 件以上のユニークデータ収集を目指す。

収集戦略:
  A) 全ジャンル横断: hyoka, weeklypoint, favnovelcnt の3ソート x 4ページ
  B) サブジャンル別 (genre): 全19ジャンル x hyoka x 4ページ
  C) 異世界転生フラグ: istensei=1 x hyoka x 4ページ
  D) 異世界転移フラグ: istenni=1 x hyoka x 4ページ
  E) 完結済み: type=er x hyoka x 4ページ
  F) 短編: type=t x hyoka x 4ページ
  G) ブックマーク順: favnovelcnt x 各biggenre x 4ページ
  H) 月間ポイント順: monthlypoint x 4ページ

重複除去: ncodeをキーに実施
出力: data/narou_novels_full.json
"""

import json
import time
import urllib.request
import urllib.parse
import gzip
from pathlib import Path
from datetime import datetime
from collections import Counter

# ----------------------------- 設定 -----------------------------

API_URL = "https://api.syosetu.com/novelapi/api/"

# 取得フィールド (of パラメータ)
OF_FIELDS = "t-n-s-k-g-bg-gl-ga-gf-l-gp-dp-wp-mp-f"

LIMIT = 500          # 1リクエストあたりの最大取得件数
MAX_ST = 1501        # st上限（2000件取得 = st=1,501,1001,1501）
PAGES_PER_QUERY = 4  # 1クエリあたりの最大ページ数
SLEEP_SEC = 2        # リクエスト間のスリープ（秒）

# 大ジャンルコードの名前マッピング
BIGGENRE_MAP = {
    1: "恋愛",
    2: "ファンタジー",
    3: "文芸",
    4: "SF",
    98: "ノンジャンル",
    99: "その他",
}

# ジャンルコードの名前マッピング
GENRE_MAP = {
    101: "異世界〔恋愛〕",
    102: "現実世界〔恋愛〕",
    201: "ハイファンタジー",
    202: "ローファンタジー",
    301: "純文学",
    302: "ヒューマンドラマ",
    303: "歴史",
    304: "推理",
    305: "ホラー",
    306: "アクション",
    307: "コメディー",
    401: "VRゲーム〔SF〕",
    402: "宇宙〔SF〕",
    403: "空想科学〔SF〕",
    404: "パニック〔SF〕",
    9901: "童話",
    9902: "詩",
    9903: "エッセイ",
    9904: "リプレイ",
    9999: "その他",
}

# 出力先
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "narou_novels_full.json"

# ----------------------------- 収集戦略定義 -----------------------------

# サブジャンルコード一覧
ALL_GENRES = [101, 102, 201, 202, 301, 302, 303, 304, 305, 306, 307,
              401, 402, 403, 404, 9901, 9902, 9903, 9904, 9999]

def build_collection_tasks():
    """収集タスクリストを構築する。"""
    tasks = []

    # A) 全ジャンル横断、複数ソート順
    for order in ["hyoka", "weeklypoint", "favnovelcnt", "monthlypoint",
                  "yearlypoint", "impressioncnt"]:
        tasks.append((
            f"全ジャンル・{order}",
            {"order": order},
            PAGES_PER_QUERY,
        ))

    # B) サブジャンル別 (genre) x hyoka
    for genre_code in ALL_GENRES:
        genre_name = GENRE_MAP.get(genre_code, str(genre_code))
        tasks.append((
            f"ジャンル:{genre_name}・hyoka",
            {"order": "hyoka", "genre": genre_code},
            PAGES_PER_QUERY,
        ))

    # C) 各大ジャンル x weeklypoint
    for bg_code in [1, 2, 3, 4, 98, 99]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        tasks.append((
            f"大ジャンル:{bg_name}・weeklypoint",
            {"order": "weeklypoint", "biggenre": bg_code},
            PAGES_PER_QUERY,
        ))

    # D) 各大ジャンル x favnovelcnt
    for bg_code in [1, 2, 3, 4]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        tasks.append((
            f"大ジャンル:{bg_name}・favnovelcnt",
            {"order": "favnovelcnt", "biggenre": bg_code},
            PAGES_PER_QUERY,
        ))

    # E) 異世界転生フラグ付き
    tasks.append((
        "異世界転生・hyoka",
        {"order": "hyoka", "istensei": 1},
        PAGES_PER_QUERY,
    ))
    tasks.append((
        "異世界転生・weeklypoint",
        {"order": "weeklypoint", "istensei": 1},
        PAGES_PER_QUERY,
    ))

    # F) 異世界転移フラグ付き
    tasks.append((
        "異世界転移・hyoka",
        {"order": "hyoka", "istenni": 1},
        PAGES_PER_QUERY,
    ))
    tasks.append((
        "異世界転移・weeklypoint",
        {"order": "weeklypoint", "istenni": 1},
        PAGES_PER_QUERY,
    ))

    # G) 完結済み x 各大ジャンル
    for bg_code in [1, 2, 3, 4]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        tasks.append((
            f"完結済み:{bg_name}・hyoka",
            {"order": "hyoka", "type": "er", "biggenre": bg_code},
            PAGES_PER_QUERY,
        ))

    # H) 短編 x 各大ジャンル
    for bg_code in [1, 2, 3, 4]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        tasks.append((
            f"短編:{bg_name}・hyoka",
            {"order": "hyoka", "type": "t", "biggenre": bg_code},
            PAGES_PER_QUERY,
        ))

    # I) 評価者数順
    tasks.append((
        "全ジャンル・hyokacnt",
        {"order": "hyokacnt"},
        PAGES_PER_QUERY,
    ))

    # J) 文字数多い順（大作）
    tasks.append((
        "全ジャンル・lengthdesc",
        {"order": "lengthdesc"},
        PAGES_PER_QUERY,
    ))

    return tasks


def fetch_page(extra_params, start, limit=LIMIT):
    """指定パラメータ・開始位置で1ページ分の小説データを取得する。

    Returns:
        (allcount, novels_list)
    """
    params = {
        "out": "json",
        "lim": limit,
        "st": start,
        "of": OF_FIELDS,
        "gzip": 5,
    }
    params.update(extra_params)

    url = "{}?{}".format(API_URL, urllib.parse.urlencode(params))

    req = urllib.request.Request(url)
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", "NarouCollector/2.0 (book-recommend-project)")

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        # gzip 圧縮されている場合は展開
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        else:
            try:
                raw = gzip.decompress(raw)
            except (gzip.BadGzipFile, OSError):
                pass

        data = json.loads(raw.decode("utf-8"))

    if not data:
        return 0, []

    allcount = data[0].get("allcount", 0)
    novels = data[1:]
    return allcount, novels


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("なろう小説API 大量データ収集スクリプト (v2)")
    print("目標: 20,000件以上のユニーク小説データ")
    print("開始時刻: {}".format(start_time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 70)

    # 収集タスク構築
    collection_tasks = build_collection_tasks()
    total_task_count = len(collection_tasks)
    total_page_count = sum(t[2] for t in collection_tasks)
    estimated_time = total_page_count * (SLEEP_SEC + 0.5)  # 概算

    print("収集タスク数: {}".format(total_task_count))
    print("総リクエスト数（最大）: {}".format(total_page_count))
    print("推定所要時間: {:.0f}秒 ({:.1f}分)".format(estimated_time, estimated_time / 60))

    # ncodeをキーとした重複除去用辞書
    novels_dict = {}
    # 収集ログ
    collection_log = []
    total_requests = 0
    total_fetched_raw = 0
    total_errors = 0

    for task_idx, (label, extra_params, num_pages) in enumerate(collection_tasks):
        print("\n[{}/{}] {} (最大{}件)".format(
            task_idx + 1, total_task_count, label, num_pages * LIMIT))

        task_fetched = 0
        task_new = 0

        for page in range(num_pages):
            start = page * LIMIT + 1
            total_requests += 1

            try:
                allcount, novels = fetch_page(extra_params, start)
                task_fetched += len(novels)
                total_fetched_raw += len(novels)

                # 重複除去しつつ格納
                new_count = 0
                for novel in novels:
                    ncode = novel.get("ncode", "")
                    if ncode and ncode not in novels_dict:
                        novel["biggenre_name"] = BIGGENRE_MAP.get(
                            novel.get("biggenre", 0), "不明"
                        )
                        novels_dict[ncode] = novel
                        new_count += 1
                task_new += new_count

                print("  p{} st={}: {}件取得, {}件新規 (累計: {:,})".format(
                    page + 1, start, len(novels), new_count, len(novels_dict)))

                # データ末尾に到達した場合は早期終了
                if len(novels) < LIMIT:
                    break

            except Exception as e:
                error_msg = str(e)
                # 404は st 上限超え（正常終了扱い）
                if "404" in error_msg:
                    print("  p{} st={}: st上限超え (スキップ)".format(page + 1, start))
                    break
                else:
                    print("  p{} st={}: [エラー] {}".format(page + 1, start, e))
                    total_errors += 1

            time.sleep(SLEEP_SEC)

        collection_log.append({
            "label": label,
            "params": {k: v for k, v in extra_params.items()},
            "pages_requested": num_pages,
            "fetched": task_fetched,
            "new_unique": task_new,
        })

        # 進捗表示
        if (task_idx + 1) % 10 == 0 or task_idx == total_task_count - 1:
            elapsed_so_far = (datetime.now() - start_time).total_seconds()
            print("\n  --- 進捗: {}/{} タスク完了, ユニーク {:,}件, "
                  "経過 {:.0f}秒 ---".format(
                      task_idx + 1, total_task_count,
                      len(novels_dict), elapsed_so_far))

    # 結果をリスト化
    all_novels = list(novels_dict.values())

    # ----------------------------- 出力 -----------------------------
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    # メタデータ付きJSON出力
    output_data = {
        "metadata": {
            "collected_at": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "collection_start": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_seconds": round(elapsed, 1),
            "total_requests": total_requests,
            "total_fetched_raw": total_fetched_raw,
            "total_unique": len(all_novels),
            "duplicates_removed": total_fetched_raw - len(all_novels),
            "total_errors": total_errors,
            "collection_tasks": collection_log,
            "api_endpoint": API_URL,
            "of_fields": OF_FIELDS,
            "script": "collect_narou_full.py",
        },
        "novels": all_novels,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    file_size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)

    # ----------------------------- サマリー表示 -----------------------------
    print("\n" + "=" * 70)
    print("収集結果サマリー")
    print("=" * 70)
    print("出力ファイル:       {}".format(OUTPUT_FILE))
    print("ファイルサイズ:     {:.1f} MB".format(file_size_mb))
    print("総リクエスト数:     {}".format(total_requests))
    print("エラー数:           {}".format(total_errors))
    print("取得件数（生）:     {:,}".format(total_fetched_raw))
    print("重複除去数:         {:,}".format(total_fetched_raw - len(all_novels)))
    print("最終ユニーク件数:   {:,}".format(len(all_novels)))
    print("所要時間:           {:.1f}秒 ({:.1f}分)".format(elapsed, elapsed / 60))

    # 目標達成判定
    if len(all_novels) >= 20000:
        print("\n*** 目標達成: 20,000件以上を収集しました! ***")
    else:
        print("\n注意: 目標 20,000件に対し {:,}件 (達成率 {:.1f}%)".format(
            len(all_novels), len(all_novels) / 20000 * 100))

    # 大ジャンル別内訳
    print("\n" + "-" * 40)
    print("大ジャンル別内訳:")
    biggenre_counts = Counter(n.get("biggenre", 0) for n in all_novels)
    for bg_code, count in sorted(biggenre_counts.items()):
        bg_name = BIGGENRE_MAP.get(bg_code, "不明({})".format(bg_code))
        print("  {:16s}: {:6,}件".format(bg_name, count))

    # ジャンル別内訳
    print("\nジャンル別内訳:")
    genre_counts = Counter(n.get("genre", 0) for n in all_novels)
    for g_code, count in sorted(genre_counts.items(), key=lambda x: -x[1]):
        g_name = GENRE_MAP.get(g_code, "不明({})".format(g_code))
        print("  {:24s}: {:6,}件".format(g_name, count))

    # ポイント分布
    print("\n" + "-" * 40)
    print("総合ポイント分布:")
    gp_values = [n.get("global_point", 0) for n in all_novels]
    if gp_values:
        gp_values_sorted = sorted(gp_values, reverse=True)
        print("  最大:   {:>12,}".format(max(gp_values)))
        print("  最小:   {:>12,}".format(min(gp_values)))
        mid = len(gp_values_sorted) // 2
        median = gp_values_sorted[mid]
        print("  中央値: {:>12,}".format(median))

        for pct in [10, 25, 50, 75, 90]:
            idx = int(len(gp_values_sorted) * pct / 100)
            val = gp_values_sorted[min(idx, len(gp_values_sorted) - 1)]
            print("  {}%ile: {:>12,}".format(pct, val))

        print("\n  ポイント区間分布:")
        thresholds = [0, 100, 1000, 5000, 10000, 50000, 100000, 500000, 1000000]
        for i in range(len(thresholds) - 1):
            lo, hi = thresholds[i], thresholds[i + 1]
            count = sum(1 for v in gp_values if lo <= v < hi)
            if count > 0:
                print("    {:>10,} - {:>10,}: {:6,}件".format(lo, hi, count))
        count_top = sum(1 for v in gp_values if v >= thresholds[-1])
        if count_top > 0:
            print("    {:>10,}+         : {:6,}件".format(thresholds[-1], count_top))

    # データ品質チェック
    print("\n" + "-" * 40)
    print("データ品質:")
    titles_with_story = sum(1 for n in all_novels if n.get("story"))
    titles_with_keyword = sum(1 for n in all_novels if n.get("keyword"))
    if all_novels:
        print("  あらすじあり:   {:,}/{:,} ({:.1f}%)".format(
            titles_with_story, len(all_novels),
            titles_with_story / len(all_novels) * 100))
        print("  キーワードあり: {:,}/{:,} ({:.1f}%)".format(
            titles_with_keyword, len(all_novels),
            titles_with_keyword / len(all_novels) * 100))

    # 上位10件表示
    if all_novels:
        sorted_novels = sorted(all_novels, key=lambda x: x.get("global_point", 0), reverse=True)
        print("\n" + "-" * 40)
        print("総合ポイント上位10作品:")
        for i, novel in enumerate(sorted_novels[:10], 1):
            title = novel.get("title", "不明")
            gp = novel.get("global_point", 0)
            bg_name = novel.get("biggenre_name", "不明")
            ncode = novel.get("ncode", "不明")
            print("  {:2d}. [{}] {}".format(i, bg_name, title))
            print("      ncode={}, {:,}pt".format(ncode, gp))

    print("\n完了時刻: {}".format(end_time.strftime("%Y-%m-%d %H:%M:%S")))
    print("所要時間: {:.1f}秒 ({:.1f}分)".format(elapsed, elapsed / 60))
    print("=" * 70)


if __name__ == "__main__":
    main()
