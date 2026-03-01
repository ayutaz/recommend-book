"""
なろう小説APIからジャンル別の人気小説データを収集するスクリプト。

収集対象:
  - ファンタジー (biggenre=2): 500件
  - 恋愛 (biggenre=1): 500件
  - 文芸 (biggenre=3): 500件
  - SF (biggenre=4): 500件

ソート: 総合評価ポイント順 (order=hyoka)
出力: data/narou_novels.json
"""

import json
import time
import urllib.request
import urllib.parse
import gzip
import io
from pathlib import Path
from datetime import datetime

# ----------------------------- 設定 -----------------------------

API_URL = "https://api.syosetu.com/novelapi/api/"

# 取得フィールド (of パラメータ)
# ユーザー指定に基づく。APIドキュメントに照らして正しいコードを使用。
#   t  = title           タイトル
#   n  = ncode           Nコード
#   s  = story           あらすじ
#   k  = keyword         キーワード（タグ）
#   g  = genre           ジャンル
#   bg = biggenre        大ジャンル
#   gl = general_lastup  最終掲載日
#   ga = general_all_no  エピソード数(話数)
#   gf = general_firstup 初回掲載日
#   l  = length          文字数
#   gp = global_point    総合ポイント
#   dp = daily_point     日間ポイント
#   wp = weekly_point    週間ポイント
#   mp = monthly_point   月間ポイント
#   qp = quarterpoint    四半期ポイント (ユーザー指定の ep は年間ポイントだが
#                         APIには yearly_point のofコードが不明なため四半期を代用)
#   f  = fav_novel_cnt   お気に入り数
OF_FIELDS = "t-n-s-k-g-bg-gl-ga-gf-l-gp-dp-wp-mp-f"

# 収集対象ジャンル
GENRES = [
    {"biggenre": 2, "name": "ファンタジー"},
    {"biggenre": 1, "name": "恋愛"},
    {"biggenre": 3, "name": "文芸"},
    {"biggenre": 4, "name": "SF"},
]

LIMIT = 500          # 1リクエストあたりの最大取得件数
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

# 出力先
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "narou_novels.json"


def fetch_novels(biggenre: int, limit: int = LIMIT) -> list[dict]:
    """指定した大ジャンルの小説データを取得する。"""
    params = {
        "out": "json",
        "lim": limit,
        "of": OF_FIELDS,
        "order": "hyoka",
        "biggenre": biggenre,
        "gzip": 5,
    }

    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    print(f"  リクエスト: {url[:120]}...")

    req = urllib.request.Request(url)
    req.add_header("Accept-Encoding", "gzip")
    req.add_header("User-Agent", "NarouCollector/1.0 (book-recommend-project)")

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        # gzip 圧縮されている場合は展開
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        else:
            # gzip パラメータ指定時は本文自体が gzip の場合がある
            try:
                raw = gzip.decompress(raw)
            except (gzip.BadGzipFile, OSError):
                pass  # 圧縮されていなければそのまま

        data = json.loads(raw.decode("utf-8"))

    # 先頭要素は allcount メタデータ
    if not data:
        return []

    allcount_info = data[0]
    novels = data[1:]
    print(f"  該当総数: {allcount_info.get('allcount', '不明')}, 取得件数: {len(novels)}")
    return novels


def main():
    print("=" * 60)
    print("なろう小説API データ収集スクリプト")
    print(f"開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_novels = []
    genre_summary = {}

    for i, genre_info in enumerate(GENRES):
        biggenre = genre_info["biggenre"]
        name = genre_info["name"]
        print(f"\n[{i+1}/{len(GENRES)}] {name} (biggenre={biggenre}) を取得中...")

        try:
            novels = fetch_novels(biggenre)
            # 大ジャンル名を付与
            for novel in novels:
                novel["biggenre_name"] = BIGGENRE_MAP.get(
                    novel.get("biggenre", biggenre), "不明"
                )
            all_novels.extend(novels)
            genre_summary[name] = len(novels)
            print(f"  -> {len(novels)} 件取得完了")
        except Exception as e:
            print(f"  [エラー] {name} の取得に失敗: {e}")
            genre_summary[name] = 0

        # 最後のリクエスト以外はスリープ
        if i < len(GENRES) - 1:
            print(f"  {SLEEP_SEC}秒待機中...")
            time.sleep(SLEEP_SEC)

    # 出力ディレクトリ作成
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # JSON 出力
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_novels, f, ensure_ascii=False, indent=2)

    # サマリー表示
    print("\n" + "=" * 60)
    print("収集結果サマリー")
    print("=" * 60)
    print(f"出力ファイル: {OUTPUT_FILE}")
    print(f"合計件数:     {len(all_novels)} 件")
    print()
    print("ジャンル別内訳:")
    for name, count in genre_summary.items():
        print(f"  {name:12s}: {count:5d} 件")
    print()

    # データの品質チェック
    titles_with_story = sum(1 for n in all_novels if n.get("story"))
    titles_with_keyword = sum(1 for n in all_novels if n.get("keyword"))
    max_gp = max((n.get("global_point", 0) for n in all_novels), default=0)
    min_gp = min((n.get("global_point", 0) for n in all_novels), default=0)

    print("データ品質:")
    print(f"  あらすじあり: {titles_with_story}/{len(all_novels)} "
          f"({titles_with_story/len(all_novels)*100:.1f}%)" if all_novels else "  データなし")
    print(f"  キーワードあり: {titles_with_keyword}/{len(all_novels)} "
          f"({titles_with_keyword/len(all_novels)*100:.1f}%)" if all_novels else "")
    print(f"  総合ポイント範囲: {min_gp:,} ~ {max_gp:,}")

    # 上位5件を表示
    if all_novels:
        sorted_novels = sorted(all_novels, key=lambda x: x.get("global_point", 0), reverse=True)
        print("\n総合ポイント上位5作品:")
        for i, novel in enumerate(sorted_novels[:5], 1):
            title = novel.get("title", "不明")
            gp = novel.get("global_point", 0)
            genre_name = novel.get("biggenre_name", "不明")
            print(f"  {i}. [{genre_name}] {title} ({gp:,}pt)")

    print(f"\n完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
