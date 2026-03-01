"""
openBD APIから書誌データを収集するスクリプト。

収集手順:
  1. 手動リスト（有名ラノベ・推理小説・マンガ等）30件のISBNを使用
  2. openBD API の GET エンドポイントで一括取得
  3. レスポンスからsummary情報・ONIX詳細情報・版元ドットコム独自情報を抽出

出力: data/openbd_books.json
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ----------------------------- 設定 -----------------------------

API_URL = "https://api.openbd.jp/v1/get"

# 手動ISBNリスト（有名作品）
KNOWN_ISBNS = [
    "9784048677608",  # SAO 1
    "9784040638782",  # リゼロ
    "9784040683539",  # このすば
    "9784040680903",  # 盾の勇者
    "9784040673141",  # 無職転生
    "9784062748681",  # 容疑者Xの献身
    "9784167110116",  # 白夜行
    "9784101349138",  # ガリレオ
    "9784101312118",  # 模倣犯
    "9784065179024",  # 薬屋のひとりごと 1
    "9784049130034",  # 転スラ 1
    "9784040723464",  # 幼女戦記 1
    "9784048935005",  # パソコン自作
    "9784041047248",  # 本好きの下剋上
    "9784047352902",  # オーバーロード 1
    "9784088725093",  # 鬼滅の刃 1
    "9784088804026",  # ONE PIECE 1
    "9784088510347",  # NARUTO 1
    "9784088718576",  # 進撃の巨人 1 (集英社ではなく講談社?)
    "9784063842760",  # 進撃の巨人 1
    "9784592143154",  # フルーツバスケット 1
    "9784091227102",  # 名探偵コナン 1
    "9784840222259",  # 涼宮ハルヒの憂鬱
    "9784840229371",  # 灼眼のシャナ
    "9784044292010",  # スレイヤーズ 1
    "9784094510362",  # ビブリア古書堂の事件手帖
    "9784101002712",  # 人間失格
    "9784101010014",  # こころ
    "9784003107010",  # 羅生門
    "9784101050010",  # 坊ちゃん
]

# 出力先
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "openbd_books.json"


def extract_synopsis(onix: dict) -> str | None:
    """ONIXデータからあらすじ（TextType=03: 内容紹介）を抽出する。"""
    if not onix:
        return None
    collateral = onix.get("CollateralDetail", {})
    text_contents = collateral.get("TextContent", [])
    if not text_contents:
        return None
    for tc in text_contents:
        # TextType "03" = Description / 内容紹介
        if tc.get("TextType") == "03":
            return tc.get("Text", "").strip() or None
    # TextType=03 がなければ最初のテキストを返す
    for tc in text_contents:
        text = tc.get("Text", "").strip()
        if text:
            return text
    return None


def extract_cover_url(onix: dict) -> str | None:
    """ONIXデータから書影画像URLを抽出する。"""
    if not onix:
        return None
    collateral = onix.get("CollateralDetail", {})
    resources = collateral.get("SupportingResource", [])
    if not resources:
        return None
    for res in resources:
        versions = res.get("ResourceVersion", [])
        for ver in versions:
            link = ver.get("ResourceLink", "").strip()
            if link:
                return link
    return None


def extract_subjects(onix: dict) -> list[dict]:
    """ONIXデータからCコード等のSubject情報を抽出する。"""
    if not onix:
        return []
    descriptive = onix.get("DescriptiveDetail", {})
    subjects = descriptive.get("Subject", [])
    result = []
    for subj in subjects:
        entry = {
            "scheme": subj.get("SubjectSchemeIdentifier", ""),
            "code": subj.get("SubjectCode", ""),
            "heading": subj.get("SubjectHeadingText", ""),
        }
        result.append(entry)
    return result


def extract_series_info(onix: dict) -> str | None:
    """ONIXデータからシリーズ名/レーベル名を抽出する。"""
    if not onix:
        return None
    descriptive = onix.get("DescriptiveDetail", {})
    collection = descriptive.get("Collection", {})
    title_detail = collection.get("TitleDetail", {})
    if not title_detail:
        return None
    # TitleDetail がリストの場合
    if isinstance(title_detail, list):
        for td in title_detail:
            elements = td.get("TitleElement", [])
            if isinstance(elements, list):
                for elem in elements:
                    title_text = elem.get("TitleText", {})
                    content = title_text.get("content", "").strip()
                    if content:
                        return content
            elif isinstance(elements, dict):
                title_text = elements.get("TitleText", {})
                content = title_text.get("content", "").strip()
                if content:
                    return content
    elif isinstance(title_detail, dict):
        elements = title_detail.get("TitleElement", [])
        if isinstance(elements, list):
            for elem in elements:
                title_text = elem.get("TitleText", {})
                content = title_text.get("content", "").strip()
                if content:
                    return content
        elif isinstance(elements, dict):
            title_text = elements.get("TitleText", {})
            content = title_text.get("content", "").strip()
            if content:
                return content
    return None


def extract_contributors(onix: dict) -> list[dict]:
    """ONIXデータから著者情報を抽出する。"""
    if not onix:
        return []
    descriptive = onix.get("DescriptiveDetail", {})
    contributors = descriptive.get("Contributor", [])
    result = []
    for contrib in contributors:
        person_name = contrib.get("PersonName", {})
        entry = {
            "name": person_name.get("content", "").strip() if isinstance(person_name, dict) else str(person_name).strip(),
            "role": contrib.get("ContributorRole", []),
            "biography": contrib.get("BiographicalNote", "").strip() if contrib.get("BiographicalNote") else None,
        }
        if entry["name"]:
            result.append(entry)
    return result


def extract_hanmoto_info(hanmoto: dict) -> dict:
    """版元ドットコム独自情報を抽出する。"""
    if not hanmoto:
        return {}
    return {
        "lanove": hanmoto.get("lanove"),
        "ndc_code": hanmoto.get("ndccode"),
        "genre_code_trc": hanmoto.get("genrecodetrc"),
        "award_info": hanmoto.get("jushoujouhou"),
        "obi_content": hanmoto.get("obinaiyou"),
        "synopsis_105w": hanmoto.get("kaisetsu105w"),
        "reviews": hanmoto.get("reviews", []),
        "reprint_info": hanmoto.get("jyuhan", []),
        "stock_status": hanmoto.get("zaiko"),
    }


def fetch_books(isbns: list[str]) -> list[dict | None]:
    """openBD APIでISBNリストの書誌情報を一括取得する。"""
    isbn_str = ",".join(isbns)
    url = f"{API_URL}?isbn={isbn_str}"

    print(f"  リクエスト: {url[:120]}...")
    print(f"  ISBN数: {len(isbns)}")

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "OpenBDCollector/1.0 (book-recommend-project)")

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        data = json.loads(raw.decode("utf-8"))

    return data


def parse_book(item: dict) -> dict:
    """APIレスポンスの1件を整理された辞書に変換する。"""
    summary = item.get("summary", {})
    onix = item.get("onix", {})
    hanmoto = item.get("hanmoto", {})

    # ONIX から詳細情報を抽出
    synopsis = extract_synopsis(onix)
    cover_onix = extract_cover_url(onix)
    subjects = extract_subjects(onix)
    series = extract_series_info(onix)
    contributors = extract_contributors(onix)
    hanmoto_info = extract_hanmoto_info(hanmoto)

    # summary の cover フィールド
    cover_summary = summary.get("cover", "").strip() or None

    # 書影URL: ONIX優先、なければsummary
    cover = cover_onix or cover_summary

    return {
        "isbn": summary.get("isbn", ""),
        "title": summary.get("title", ""),
        "author": summary.get("author", ""),
        "publisher": summary.get("publisher", ""),
        "pubdate": summary.get("pubdate", ""),
        "cover": cover,
        "volume": summary.get("volume", ""),
        "series": summary.get("series", "") or series,
        "synopsis": synopsis,
        "synopsis_105w": hanmoto_info.get("synopsis_105w"),
        "subjects": subjects,
        "contributors": contributors,
        "hanmoto": hanmoto_info,
    }


def main():
    print("=" * 60)
    print("openBD API データ収集スクリプト")
    print(f"開始時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # --- Step 1: ISBNリストの準備 ---
    all_isbns = list(KNOWN_ISBNS)
    # 重複除去（順序保持）
    seen = set()
    unique_isbns = []
    for isbn in all_isbns:
        if isbn not in seen:
            seen.add(isbn)
            unique_isbns.append(isbn)
    all_isbns = unique_isbns

    print(f"\n対象ISBN数: {len(all_isbns)}")

    # --- Step 2: openBD API でデータ取得 ---
    print(f"\nopenBD APIからデータ取得中...")

    try:
        raw_data = fetch_books(all_isbns)
    except urllib.error.URLError as e:
        print(f"[エラー] APIリクエスト失敗: {e}")
        return
    except Exception as e:
        print(f"[エラー] 予期しないエラー: {e}")
        return

    if not raw_data:
        print("[エラー] レスポンスが空です")
        return

    print(f"  レスポンス要素数: {len(raw_data)}")

    # --- Step 3: データ整形 ---
    books = []
    null_count = 0
    for i, item in enumerate(raw_data):
        if item is None:
            null_count += 1
            print(f"  [未収録] ISBN: {all_isbns[i]}")
            continue
        book = parse_book(item)
        books.append(book)

    print(f"\n取得成功: {len(books)} / {len(all_isbns)} 件")
    if null_count > 0:
        print(f"未収録ISBN: {null_count} 件")

    # --- 出力 ---
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=2)

    # --- サマリー表示 ---
    print("\n" + "=" * 60)
    print("収集結果サマリー")
    print("=" * 60)
    print(f"出力ファイル: {OUTPUT_FILE}")
    print(f"合計取得件数: {len(books)} / {len(all_isbns)} 件")
    print()

    # データ充実度チェック
    has_synopsis = sum(1 for b in books if b.get("synopsis"))
    has_synopsis_105w = sum(1 for b in books if b.get("synopsis_105w"))
    has_any_synopsis = sum(
        1 for b in books
        if b.get("synopsis") or b.get("synopsis_105w")
    )
    has_cover = sum(1 for b in books if b.get("cover"))
    has_series = sum(1 for b in books if b.get("series"))
    has_subjects = sum(1 for b in books if b.get("subjects"))
    has_lanove = sum(
        1 for b in books
        if b.get("hanmoto", {}).get("lanove")
    )
    has_award = sum(
        1 for b in books
        if b.get("hanmoto", {}).get("award_info")
    )
    has_obi = sum(
        1 for b in books
        if b.get("hanmoto", {}).get("obi_content")
    )

    total = len(books)
    pct = lambda n: f"{n}/{total} ({n/total*100:.1f}%)" if total > 0 else "N/A"

    print("データ充実度:")
    print(f"  あらすじ (ONIX TextContent):      {pct(has_synopsis)}")
    print(f"  あらすじ (版元105字):              {pct(has_synopsis_105w)}")
    print(f"  あらすじ (いずれか):               {pct(has_any_synopsis)}")
    print(f"  書影URL:                           {pct(has_cover)}")
    print(f"  シリーズ/レーベル名:               {pct(has_series)}")
    print(f"  Cコード/ジャンルコード:            {pct(has_subjects)}")
    print(f"  ライトノベルフラグ (lanove):       {pct(has_lanove)}")
    print(f"  受賞情報:                          {pct(has_award)}")
    print(f"  帯の内容:                          {pct(has_obi)}")

    # 出版社別集計
    publishers = {}
    for b in books:
        pub = b.get("publisher", "不明")
        publishers[pub] = publishers.get(pub, 0) + 1

    print("\n出版社別内訳:")
    for pub, count in sorted(publishers.items(), key=lambda x: -x[1]):
        print(f"  {pub:20s}: {count:3d} 件")

    # あらすじがある作品を表示
    print("\nあらすじが取得できた作品:")
    for b in books:
        synopsis = b.get("synopsis") or b.get("synopsis_105w")
        if synopsis:
            truncated = synopsis[:80].replace("\n", " ")
            if len(synopsis) > 80:
                truncated += "..."
            print(f"  [{b['isbn']}] {b['title']}")
            print(f"    -> {truncated}")

    # あらすじが無い作品
    no_synopsis = [b for b in books if not b.get("synopsis") and not b.get("synopsis_105w")]
    if no_synopsis:
        print(f"\nあらすじが取得できなかった作品 ({len(no_synopsis)}件):")
        for b in no_synopsis:
            print(f"  [{b['isbn']}] {b['title']} ({b['publisher']})")

    # 全作品一覧
    print("\n" + "-" * 60)
    print("取得データ一覧:")
    print("-" * 60)
    for i, b in enumerate(books, 1):
        lanove_flag = ""
        if b.get("hanmoto", {}).get("lanove"):
            lanove_flag = " [ラノベ]"
        synopsis_flag = ""
        if b.get("synopsis"):
            synopsis_flag = " [あらすじあり]"
        elif b.get("synopsis_105w"):
            synopsis_flag = " [105字あり]"
        cover_flag = " [書影あり]" if b.get("cover") else ""

        print(f"  {i:2d}. {b['title']}")
        print(f"      著者: {b['author']} / 出版社: {b['publisher']} / 発行日: {b['pubdate']}")
        print(f"      フラグ:{lanove_flag}{synopsis_flag}{cover_flag}")

    print(f"\n完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
