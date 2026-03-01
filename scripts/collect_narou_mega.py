"""
なろう小説API メガ収集スクリプト。

既存55,215件に加え、多角的な収集戦略で150,000-250,000件のユニーク作品を目指す。
APIの st パラメータ上限=2000 のため、1クエリ条件あたり最大 2,000件(4ページ x 500件)。
ジャンル・ソート順・フラグ・文字数範囲・キーワードを組み合わせてカバー範囲を最大化。

Phase 1: 全ジャンル x 全ソート順 (20ジャンル x 12ソート x 4ページ)
Phase 2: ジャンル x 作品タイプ x ソート (20ジャンル x 3タイプ x 4ソート x 4ページ)
Phase 3: フラグ組み合わせ x ジャンル
Phase 4: 文字数範囲 x 大ジャンル
Phase 5: キーワード検索
"""

import json
import sys
import time
import urllib.request
import urllib.parse
import gzip
import io
from pathlib import Path
from datetime import datetime
from collections import Counter


def log(msg=""):
    """バッファリングなしでprintする。"""
    print(msg, flush=True)

# ----------------------------- 設定 -----------------------------

API_URL = "https://api.syosetu.com/novelapi/api/"

# 取得フィールド (拡張版)
OF_FIELDS = "t-n-s-k-g-bg-gl-ga-gf-l-gp-dp-wp-mp-f-w-nt-e-imp-r-a-ah-its-iti"

LIMIT = 500          # 1リクエストあたりの最大取得件数
MAX_ST = 1501        # st上限（2000件取得 = st=1,501,1001,1501）
PAGES_PER_QUERY = 4  # 1クエリあたりの最大ページ数
SLEEP_SEC = 1.5      # リクエスト間のスリープ（秒）
RETRY_SLEEP = 10     # エラー時のリトライ待ち（秒）
MAX_RETRIES = 3      # 最大リトライ回数
SAVE_INTERVAL = 500  # 中間保存間隔（リクエスト数）
PROGRESS_INTERVAL = 100  # 進捗表示間隔

# 大ジャンルコード
BIGGENRE_MAP = {
    1: "恋愛",
    2: "ファンタジー",
    3: "文芸",
    4: "SF",
    98: "ノンジャンル",
    99: "その他",
}

# ジャンルコード
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

ALL_GENRES = [101, 102, 201, 202, 301, 302, 303, 304, 305, 306, 307,
              401, 402, 403, 404, 9901, 9902, 9903, 9904, 9999]

# 出力先
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EXISTING_FILE = DATA_DIR / "narou_novels_full.json"
OUTPUT_FILE = DATA_DIR / "narou_novels_mega.json"
CHECKPOINT_FILE = DATA_DIR / "narou_novels_mega_checkpoint.json"

# ----------------------------- 収集戦略定義 -----------------------------

ALL_SORTS = ["hyoka", "weeklypoint", "monthlypoint", "quarterpoint",
             "yearlypoint", "favnovelcnt", "impressioncnt", "hyokacnt",
             "dailypoint", "lengthdesc", "ncodedesc", "new"]

KEYWORDS = [
    "異世界", "転生", "チート", "悪役令嬢", "婚約破棄",
    "ざまぁ", "ダンジョン", "魔王", "勇者", "冒険者",
    "スキル", "レベル", "ステータス", "追放", "復讐",
    "最強", "無双", "聖女", "魔法", "錬金術",
    "ギルド", "従魔", "テイマー", "料理", "スローライフ",
    "領地経営", "内政", "学園", "幼馴染", "ツンデレ",
    "ハーレム", "主人公最強", "もふもふ", "女主人公", "男主人公",
    "ループ", "タイムリープ", "デスゲーム", "VRMMO", "配信",
    "推理", "探偵", "殺人", "ミステリー", "ホラー",
    "恋愛", "青春", "日常", "現代", "ファンタジー",
]


def build_all_tasks():
    """全Phaseの収集タスクを構築する。"""
    tasks = []

    # ===== Phase 1: 全ジャンル x 全ソート順 =====
    for genre_code in ALL_GENRES:
        genre_name = GENRE_MAP.get(genre_code, str(genre_code))
        for sort_order in ALL_SORTS:
            tasks.append({
                "phase": 1,
                "label": "P1: {} x {}".format(genre_name, sort_order),
                "params": {"genre": genre_code, "order": sort_order},
            })

    # ===== Phase 2: ジャンル x 作品タイプ x ソート =====
    type_map = {"t": "短編", "r": "連載中", "er": "完結済み"}
    phase2_sorts = ["hyoka", "favnovelcnt", "ncodedesc", "new"]
    for genre_code in ALL_GENRES:
        genre_name = GENRE_MAP.get(genre_code, str(genre_code))
        for type_code, type_name in type_map.items():
            for sort_order in phase2_sorts:
                tasks.append({
                    "phase": 2,
                    "label": "P2: {} x {} x {}".format(genre_name, type_name, sort_order),
                    "params": {"genre": genre_code, "type": type_code, "order": sort_order},
                })

    # ===== Phase 3: フラグ組み合わせ x ジャンル =====
    # 異世界転生 x 大ジャンル x ソート
    for bg_code in [1, 2, 3, 4, 98, 99]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        for sort_order in ["hyoka", "favnovelcnt", "new"]:
            tasks.append({
                "phase": 3,
                "label": "P3: 転生 x {} x {}".format(bg_name, sort_order),
                "params": {"istensei": 1, "biggenre": bg_code, "order": sort_order},
            })

    # 異世界転移 x 大ジャンル x ソート
    for bg_code in [1, 2, 3, 4, 98, 99]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        for sort_order in ["hyoka", "favnovelcnt", "new"]:
            tasks.append({
                "phase": 3,
                "label": "P3: 転移 x {} x {}".format(bg_name, sort_order),
                "params": {"istenni": 1, "biggenre": bg_code, "order": sort_order},
            })

    # R15 x 大ジャンル x ソート
    for bg_code in [1, 2, 3, 4, 98, 99]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        for sort_order in ["hyoka", "new"]:
            tasks.append({
                "phase": 3,
                "label": "P3: R15 x {} x {}".format(bg_name, sort_order),
                "params": {"isr15": 1, "biggenre": bg_code, "order": sort_order},
            })

    # BL x ソート
    for sort_order in ["hyoka", "new"]:
        tasks.append({
            "phase": 3,
            "label": "P3: BL x {}".format(sort_order),
            "params": {"isbl": 1, "order": sort_order},
        })

    # GL x ソート
    for sort_order in ["hyoka", "new"]:
        tasks.append({
            "phase": 3,
            "label": "P3: GL x {}".format(sort_order),
            "params": {"isgl": 1, "order": sort_order},
        })

    # ===== Phase 4: 文字数範囲 x 大ジャンル =====
    length_ranges = [
        (1, 10000, "~1万字"),
        (10001, 50000, "1-5万字"),
        (50001, 200000, "5-20万字"),
        (200001, None, "20万字~"),
    ]
    phase4_sorts = ["hyoka", "favnovelcnt", "new"]
    for bg_code in [1, 2, 3, 4]:
        bg_name = BIGGENRE_MAP.get(bg_code, str(bg_code))
        for minlen, maxlen, range_label in length_ranges:
            for sort_order in phase4_sorts:
                params = {"biggenre": bg_code, "order": sort_order, "minlen": minlen}
                if maxlen is not None:
                    params["maxlen"] = maxlen
                tasks.append({
                    "phase": 4,
                    "label": "P4: {} x {} x {}".format(bg_name, range_label, sort_order),
                    "params": params,
                })

    # ===== Phase 5: キーワード検索 =====
    for keyword in KEYWORDS:
        for sort_order in ["hyoka", "new"]:
            tasks.append({
                "phase": 5,
                "label": "P5: KW「{}」x {}".format(keyword, sort_order),
                "params": {"word": keyword, "order": sort_order},
            })

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
    req.add_header("User-Agent", "NarouMegaCollector/1.0 (book-recommend-project)")

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                # gzip圧縮の展開
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

        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                if attempt < MAX_RETRIES - 1:
                    log("      [HTTP {}] リトライ待ち {}秒...".format(e.code, RETRY_SLEEP))
                    time.sleep(RETRY_SLEEP)
                    continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < MAX_RETRIES - 1:
                print("      [接続エラー] リトライ待ち {}秒... ({})".format(RETRY_SLEEP, e))
                time.sleep(RETRY_SLEEP)
                continue
            raise

    return 0, []


def load_existing_data():
    """既存データファイルからncodeセットとnovels辞書を読み込む。"""
    novels_dict = {}

    if EXISTING_FILE.exists():
        print("既存データ読み込み: {}".format(EXISTING_FILE))
        with open(EXISTING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict) and "novels" in data:
            for novel in data["novels"]:
                ncode = novel.get("ncode", "")
                if ncode:
                    novels_dict[ncode] = novel
        elif isinstance(data, list):
            for novel in data:
                ncode = novel.get("ncode", "")
                if ncode:
                    novels_dict[ncode] = novel

        print("  既存ユニーク件数: {:,}".format(len(novels_dict)))

    # チェックポイントがあればそちらも読み込む
    if CHECKPOINT_FILE.exists():
        print("チェックポイント読み込み: {}".format(CHECKPOINT_FILE))
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            cp_data = json.load(f)

        if isinstance(cp_data, dict) and "novels" in cp_data:
            cp_count = 0
            for novel in cp_data["novels"]:
                ncode = novel.get("ncode", "")
                if ncode and ncode not in novels_dict:
                    novels_dict[ncode] = novel
                    cp_count += 1
            print("  チェックポイントから追加: {:,}件".format(cp_count))

    return novels_dict


def save_checkpoint(novels_dict, metadata, phase_stats):
    """チェックポイント保存。"""
    output_data = {
        "metadata": metadata,
        "phase_stats": phase_stats,
        "novels": list(novels_dict.values()),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False)
    size_mb = CHECKPOINT_FILE.stat().st_size / (1024 * 1024)
    print("  [チェックポイント保存] {:,}件, {:.1f}MB".format(len(novels_dict), size_mb))


def save_final(novels_dict, metadata, phase_stats, collection_log):
    """最終出力保存。"""
    all_novels = list(novels_dict.values())

    metadata["total_unique"] = len(all_novels)
    metadata["phase_stats"] = phase_stats
    metadata["collection_log_summary"] = {
        "total_tasks": len(collection_log),
        "tasks_with_new_data": sum(1 for t in collection_log if t["new_unique"] > 0),
    }

    output_data = {
        "metadata": metadata,
        "novels": all_novels,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    return size_mb


def main():
    start_time = datetime.now()
    print("=" * 70)
    print("なろう小説API メガ収集スクリプト")
    print("目標: 150,000-250,000件のユニーク小説データ")
    print("開始時刻: {}".format(start_time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 70)

    # 既存データ読み込み
    novels_dict = load_existing_data()
    initial_count = len(novels_dict)
    print("初期ユニーク件数: {:,}".format(initial_count))

    # 全タスク構築
    all_tasks = build_all_tasks()
    total_task_count = len(all_tasks)

    # Phase別タスク数をカウント
    phase_task_counts = Counter(t["phase"] for t in all_tasks)
    total_max_requests = total_task_count * PAGES_PER_QUERY
    estimated_time = total_max_requests * (SLEEP_SEC + 0.5)

    print("\n収集計画:")
    for phase_num in sorted(phase_task_counts.keys()):
        count = phase_task_counts[phase_num]
        print("  Phase {}: {}タスク (最大{}リクエスト)".format(
            phase_num, count, count * PAGES_PER_QUERY))
    print("  合計: {}タスク, 最大{}リクエスト".format(total_task_count, total_max_requests))
    print("  推定所要時間: {:.0f}分 (allcount=0スキップにより短縮見込み)".format(estimated_time / 60))

    # 統計
    total_requests = 0
    total_fetched_raw = 0
    total_errors = 0
    total_skipped = 0
    collection_log = []
    phase_stats = {}

    for task_idx, task in enumerate(all_tasks):
        phase = task["phase"]
        label = task["label"]
        extra_params = task["params"]

        if phase not in phase_stats:
            phase_stats[phase] = {
                "tasks": 0, "requests": 0, "fetched": 0,
                "new_unique": 0, "skipped": 0, "errors": 0,
            }

        phase_stats[phase]["tasks"] += 1
        task_fetched = 0
        task_new = 0

        for page in range(PAGES_PER_QUERY):
            start = page * LIMIT + 1
            total_requests += 1
            phase_stats[phase]["requests"] += 1

            try:
                allcount, novels = fetch_page(extra_params, start)

                # allcount=0 なら以降のページもスキップ
                if allcount == 0 and page == 0:
                    total_skipped += 1
                    phase_stats[phase]["skipped"] += 1
                    break

                task_fetched += len(novels)
                total_fetched_raw += len(novels)
                phase_stats[phase]["fetched"] += len(novels)

                # 重複除去しつつ格納
                new_count = 0
                for novel in novels:
                    ncode = novel.get("ncode", "")
                    if ncode and ncode not in novels_dict:
                        # 大ジャンル名を追加
                        novel["biggenre_name"] = BIGGENRE_MAP.get(
                            novel.get("biggenre", 0), "不明"
                        )
                        novels_dict[ncode] = novel
                        new_count += 1

                task_new += new_count
                phase_stats[phase]["new_unique"] += new_count

                # データ末尾に到達した場合は早期終了
                if len(novels) < LIMIT:
                    break

                # 次のページのstがallcountを超える場合もスキップ
                next_st = (page + 1) * LIMIT + 1
                if next_st > min(allcount, 2000):
                    break

            except Exception as e:
                error_msg = str(e)
                if "404" in error_msg:
                    break
                else:
                    print("  [エラー] {} - p{} st={}: {}".format(label, page + 1, start, e))
                    total_errors += 1
                    phase_stats[phase]["errors"] += 1

            time.sleep(SLEEP_SEC)

        collection_log.append({
            "label": label,
            "phase": phase,
            "params": {k: str(v) for k, v in extra_params.items()},
            "fetched": task_fetched,
            "new_unique": task_new,
        })

        # 進捗表示 (100リクエストごと)
        if total_requests % PROGRESS_INTERVAL < PAGES_PER_QUERY or task_idx == total_task_count - 1:
            if total_requests >= PROGRESS_INTERVAL and (total_requests // PROGRESS_INTERVAL) * PROGRESS_INTERVAL >= total_requests - PAGES_PER_QUERY:
                elapsed = (datetime.now() - start_time).total_seconds()
                new_count_total = len(novels_dict) - initial_count
                print("[進捗] タスク {}/{} | リクエスト {} | ユニーク {:,} (+{:,}) | "
                      "スキップ {} | エラー {} | 経過 {:.0f}分".format(
                    task_idx + 1, total_task_count, total_requests,
                    len(novels_dict), new_count_total,
                    total_skipped, total_errors, elapsed / 60))

        # 中間保存 (500リクエストごと)
        if total_requests % SAVE_INTERVAL < PAGES_PER_QUERY and total_requests >= SAVE_INTERVAL:
            if (total_requests // SAVE_INTERVAL) * SAVE_INTERVAL >= total_requests - PAGES_PER_QUERY:
                elapsed = (datetime.now() - start_time).total_seconds()
                meta = {
                    "checkpoint_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "total_requests": total_requests,
                    "total_unique": len(novels_dict),
                    "elapsed_seconds": round(elapsed, 1),
                }
                save_checkpoint(novels_dict, meta, phase_stats)

    # ----------------------------- 最終保存 -----------------------------
    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    metadata = {
        "collected_at": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "collection_start": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_seconds": round(elapsed, 1),
        "total_requests": total_requests,
        "total_fetched_raw": total_fetched_raw,
        "total_unique": len(novels_dict),
        "initial_count": initial_count,
        "new_unique": len(novels_dict) - initial_count,
        "duplicates_removed": total_fetched_raw - (len(novels_dict) - initial_count),
        "total_errors": total_errors,
        "total_skipped_empty": total_skipped,
        "api_endpoint": API_URL,
        "of_fields": OF_FIELDS,
        "script": "collect_narou_mega.py",
        "strategy": "5-phase mega collection",
    }

    file_size_mb = save_final(novels_dict, metadata, phase_stats, collection_log)

    # ----------------------------- サマリー表示 -----------------------------
    all_novels = list(novels_dict.values())

    print("\n" + "=" * 70)
    print("収集結果サマリー")
    print("=" * 70)
    print("出力ファイル:       {}".format(OUTPUT_FILE))
    print("ファイルサイズ:     {:.1f} MB".format(file_size_mb))
    print("総リクエスト数:     {:,}".format(total_requests))
    print("スキップ (allcount=0): {:,}".format(total_skipped))
    print("エラー数:           {:,}".format(total_errors))
    print("取得件数（生）:     {:,}".format(total_fetched_raw))
    print("初期件数:           {:,}".format(initial_count))
    print("新規ユニーク件数:   {:,}".format(len(novels_dict) - initial_count))
    print("最終ユニーク件数:   {:,}".format(len(novels_dict)))
    print("所要時間:           {:.1f}分".format(elapsed / 60))

    # Phase別実績
    print("\n" + "-" * 40)
    print("Phase別実績:")
    for phase_num in sorted(phase_stats.keys()):
        ps = phase_stats[phase_num]
        print("  Phase {}: タスク{} | リクエスト{} | 取得{:,} | 新規{:,} | スキップ{} | エラー{}".format(
            phase_num, ps["tasks"], ps["requests"], ps["fetched"],
            ps["new_unique"], ps["skipped"], ps["errors"]))

    # 大ジャンル別内訳
    print("\n" + "-" * 40)
    print("大ジャンル別内訳:")
    biggenre_counts = Counter(n.get("biggenre", 0) for n in all_novels)
    for bg_code, count in sorted(biggenre_counts.items()):
        bg_name = BIGGENRE_MAP.get(bg_code, "不明({})".format(bg_code))
        print("  {:16s}: {:>8,}件".format(bg_name, count))

    # ジャンル別内訳
    print("\nジャンル別内訳:")
    genre_counts = Counter(n.get("genre", 0) for n in all_novels)
    for g_code, count in sorted(genre_counts.items(), key=lambda x: -x[1]):
        g_name = GENRE_MAP.get(g_code, "不明({})".format(g_code))
        print("  {:24s}: {:>8,}件".format(g_name, count))

    # 総合ポイント分布
    print("\n" + "-" * 40)
    print("総合ポイント分布:")
    gp_values = [n.get("global_point", 0) for n in all_novels]
    if gp_values:
        gp_sorted = sorted(gp_values, reverse=True)
        print("  最大:   {:>12,}".format(max(gp_values)))
        print("  最小:   {:>12,}".format(min(gp_values)))
        mid = len(gp_sorted) // 2
        print("  中央値: {:>12,}".format(gp_sorted[mid]))

        for pct in [10, 25, 50, 75, 90]:
            idx = int(len(gp_sorted) * pct / 100)
            val = gp_sorted[min(idx, len(gp_sorted) - 1)]
            print("  {}%ile: {:>12,}".format(pct, val))

        print("\n  ポイント区間分布:")
        thresholds = [0, 10, 100, 1000, 5000, 10000, 50000, 100000, 500000, 1000000]
        for i in range(len(thresholds) - 1):
            lo, hi = thresholds[i], thresholds[i + 1]
            count = sum(1 for v in gp_values if lo <= v < hi)
            if count > 0:
                print("    {:>10,} - {:>10,}: {:>8,}件".format(lo, hi, count))
        count_top = sum(1 for v in gp_values if v >= thresholds[-1])
        if count_top > 0:
            print("    {:>10,}+         : {:>8,}件".format(thresholds[-1], count_top))

    # データ品質チェック
    print("\n" + "-" * 40)
    print("データ品質:")
    if all_novels:
        titles_with_story = sum(1 for n in all_novels if n.get("story"))
        titles_with_keyword = sum(1 for n in all_novels if n.get("keyword"))
        titles_with_writer = sum(1 for n in all_novels if n.get("writer"))
        titles_with_end = sum(1 for n in all_novels if n.get("end") is not None)
        print("  あらすじあり:   {:>8,}/{:>8,} ({:.1f}%)".format(
            titles_with_story, len(all_novels),
            titles_with_story / len(all_novels) * 100))
        print("  キーワードあり: {:>8,}/{:>8,} ({:.1f}%)".format(
            titles_with_keyword, len(all_novels),
            titles_with_keyword / len(all_novels) * 100))
        print("  作者名あり:     {:>8,}/{:>8,} ({:.1f}%)".format(
            titles_with_writer, len(all_novels),
            titles_with_writer / len(all_novels) * 100))
        print("  完結フラグあり: {:>8,}/{:>8,} ({:.1f}%)".format(
            titles_with_end, len(all_novels),
            titles_with_end / len(all_novels) * 100))

        # 転生・転移フラグ
        tensei_count = sum(1 for n in all_novels if n.get("istensei", 0) == 1)
        tenni_count = sum(1 for n in all_novels if n.get("istenni", 0) == 1)
        print("  転生フラグ:     {:>8,}件".format(tensei_count))
        print("  転移フラグ:     {:>8,}件".format(tenni_count))

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
            writer = novel.get("writer", "不明")
            print("  {:2d}. [{}] {}".format(i, bg_name, title))
            print("      ncode={}, {:,}pt, 作者: {}".format(ncode, gp, writer))

    print("\n完了時刻: {}".format(end_time.strftime("%Y-%m-%d %H:%M:%S")))
    print("所要時間: {:.1f}分".format(elapsed / 60))

    # 全作品数に対するカバレッジ
    total_estimated = 948569
    coverage = len(novels_dict) / total_estimated * 100
    print("推定カバレッジ: {:,}/{:,} ({:.1f}%)".format(
        len(novels_dict), total_estimated, coverage))
    print("=" * 70)

    # チェックポイントファイルを削除（成功時）
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("チェックポイントファイルを削除しました。")


if __name__ == "__main__":
    main()
