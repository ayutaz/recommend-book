"""
openBD APIからNDLサーチ収集済みISBNリスト＋coverageサンプリングで一括データ取得するスクリプト。

収集手順:
  1. data/ndl_books.json からISBNを抽出（ハイフン除去、10桁/13桁のみ）
  2. openBD coverage APIで全収録ISBN一覧を取得し、日本語書籍(978-4-)をランダム3,000件サンプリング
  3. NDL ISBN + サンプリングISBNをopenBD GET/POSTで一括取得（1,000件ずつバッチ）
  4. データ整形・サマリー表示・保存

出力: data/openbd_books_full.json
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

# ----------------------------- 設定 -----------------------------

API_BASE = "https://api.openbd.jp/v1"
API_GET = f"{API_BASE}/get"
API_COVERAGE = f"{API_BASE}/coverage"

BATCH_SIZE = 1000  # 1リクエストあたりの最大ISBN数
COVERAGE_SAMPLE_SIZE = 3000  # coverageからのサンプリング件数
BATCH_DELAY = 0.5  # バッチ間の待機秒数

# ファイルパス
PROJECT_DIR = Path(__file__).resolve().parent.parent
NDL_FILE = PROJECT_DIR / "data" / "ndl_books.json"
OUTPUT_DIR = PROJECT_DIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "openbd_books_full.json"

USER_AGENT = "OpenBDFullCollector/1.0 (book-recommend-project)"


# ----------------------------- ISBN操作 -----------------------------

def normalize_isbn(raw: str) -> str | None:
    """ISBNを正規化する（ハイフン除去、10桁/13桁チェック）。"""
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9Xx]", "", raw)
    if len(cleaned) == 10 or len(cleaned) == 13:
        return cleaned
    return None


def isbn10_to_isbn13(isbn10: str) -> str:
    """ISBN-10をISBN-13に変換する。"""
    if len(isbn10) != 10:
        return isbn10
    base = "978" + isbn10[:9]
    total = 0
    for i, ch in enumerate(base):
        weight = 1 if i % 2 == 0 else 3
        total += int(ch) * weight
    check = (10 - total % 10) % 10
    return base + str(check)


# ----------------------------- ISBN抽出 (NDL) -----------------------------

def extract_ndl_isbns(ndl_file: Path) -> list[str]:
    """ndl_books.jsonからISBNを抽出する。"""
    print(f"\n[Step 1] NDLデータからISBN抽出: {ndl_file}")

    with open(ndl_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    books = data.get("books", [])
    print(f"  NDLレコード数: {len(books)}")

    isbns = []
    skipped = 0
    for book in books:
        raw_isbn = book.get("isbn")
        if not raw_isbn:
            skipped += 1
            continue
        normalized = normalize_isbn(raw_isbn)
        if normalized is None:
            skipped += 1
            continue
        # 10桁の場合は13桁に変換
        if len(normalized) == 10:
            normalized = isbn10_to_isbn13(normalized)
        isbns.append(normalized)

    # 重複除去（順序保持）
    seen = set()
    unique = []
    for isbn in isbns:
        if isbn not in seen:
            seen.add(isbn)
            unique.append(isbn)

    print(f"  ISBN抽出数: {len(unique)} (ISBNなし/不正: {skipped}件スキップ)")
    return unique


# ----------------------------- Coverage API -----------------------------

def fetch_coverage_isbns(ndl_isbn_set: set[str]) -> list[str]:
    """openBD coverage APIから日本語書籍ISBNを取得し、ランダムサンプリングする。"""
    print(f"\n[Step 2] openBD coverage APIから追加ISBNを取得")
    print(f"  エンドポイント: {API_COVERAGE}")
    print(f"  (大容量データのダウンロード中... 数分かかる場合があります)")

    req = urllib.request.Request(API_COVERAGE)
    req.add_header("User-Agent", USER_AGENT)

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
            print(f"  ダウンロードサイズ: {len(raw) / 1024 / 1024:.1f} MB")
            all_isbns = json.loads(raw.decode("utf-8"))

        print(f"  openBD収録ISBN総数: {len(all_isbns):,}")

        # フィルタリング: 978-4- で始まるもの（日本語書籍）
        jp_isbns = [isbn for isbn in all_isbns if isbn.startswith("9784")]
        print(f"  日本語書籍 (978-4-): {len(jp_isbns):,}")

        # NDLと重複しないもの
        candidates = [isbn for isbn in jp_isbns if isbn not in ndl_isbn_set]
        print(f"  NDL非重複: {len(candidates):,}")

        # ランダムサンプリング
        sample_size = min(COVERAGE_SAMPLE_SIZE, len(candidates))
        random.seed(42)  # 再現性のため
        sampled = random.sample(candidates, sample_size)
        print(f"  サンプリング: {sample_size}件")

        return sampled

    except urllib.error.URLError as e:
        print(f"  [警告] coverage API取得失敗: {e}")
        print(f"  → NDLのISBNのみで続行します")
        return []
    except json.JSONDecodeError as e:
        print(f"  [警告] coverage APIレスポンスのJSON解析失敗: {e}")
        print(f"  → NDLのISBNのみで続行します")
        return []
    except Exception as e:
        print(f"  [警告] coverage API予期しないエラー: {e}")
        print(f"  → NDLのISBNのみで続行します")
        return []


# ----------------------------- openBDデータ取得 -----------------------------

def fetch_batch(isbns: list[str]) -> list[dict | None]:
    """openBD APIで1バッチ分のISBNを取得する。"""
    isbn_str = ",".join(isbns)

    # 1000件以下ならGET、それ以上ならPOST
    if len(isbns) <= 100:
        url = f"{API_GET}?isbn={isbn_str}"
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            API_GET,
            data=f"isbn={isbn_str}".encode("utf-8"),
            method="POST",
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

    req.add_header("User-Agent", USER_AGENT)

    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        data = json.loads(raw.decode("utf-8"))

    return data


def fetch_all_books(all_isbns: list[str]) -> list[tuple[str, dict | None]]:
    """全ISBNをバッチに分割してopenBDから取得する。"""
    print(f"\n[Step 3] openBD APIからデータ一括取得")
    print(f"  対象ISBN数: {len(all_isbns):,}")
    print(f"  バッチサイズ: {BATCH_SIZE}")

    num_batches = (len(all_isbns) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"  バッチ数: {num_batches}")

    results = []
    for i in range(0, len(all_isbns), BATCH_SIZE):
        batch_isbns = all_isbns[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\n  バッチ {batch_num}/{num_batches}: {len(batch_isbns)}件...")

        try:
            raw_data = fetch_batch(batch_isbns)

            if raw_data is None:
                print(f"    [エラー] レスポンスがNullです")
                for isbn in batch_isbns:
                    results.append((isbn, None))
                continue

            for j, item in enumerate(raw_data):
                isbn = batch_isbns[j] if j < len(batch_isbns) else "unknown"
                results.append((isbn, item))

            hit_count = sum(1 for _, item in results[i:] if item is not None)
            print(f"    取得: {hit_count}/{len(batch_isbns)}件")

        except urllib.error.URLError as e:
            print(f"    [エラー] バッチ {batch_num} 失敗: {e}")
            for isbn in batch_isbns:
                results.append((isbn, None))
        except Exception as e:
            print(f"    [エラー] バッチ {batch_num} 予期しないエラー: {e}")
            for isbn in batch_isbns:
                results.append((isbn, None))

        # バッチ間ウェイト
        if i + BATCH_SIZE < len(all_isbns):
            time.sleep(BATCH_DELAY)

    return results


# ----------------------------- データ解析 -----------------------------

def extract_synopsis(onix: dict) -> str | None:
    """ONIXデータからあらすじ（TextType=03: 内容紹介）を抽出する。"""
    if not onix:
        return None
    collateral = onix.get("CollateralDetail", {})
    text_contents = collateral.get("TextContent", [])
    if not text_contents:
        return None
    for tc in text_contents:
        if tc.get("TextType") == "03":
            return tc.get("Text", "").strip() or None
    # TextType=03 がなければ 02（短い説明）を試す
    for tc in text_contents:
        if tc.get("TextType") == "02":
            return tc.get("Text", "").strip() or None
    # それでもなければ最初のテキスト
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


def extract_c_code(subjects: list[dict]) -> str | None:
    """Subject情報からCコードを抽出する。"""
    for subj in subjects:
        code = subj.get("code", "")
        # Cコードは4桁の数字（例: 0093）
        if re.match(r"^\d{4}$", code):
            return code
    return None


def extract_series_info(onix: dict) -> str | None:
    """ONIXデータからシリーズ名/レーベル名を抽出する。"""
    if not onix:
        return None
    descriptive = onix.get("DescriptiveDetail", {})
    collection = descriptive.get("Collection", {})
    title_detail = collection.get("TitleDetail", {})
    if not title_detail:
        return None
    if isinstance(title_detail, list):
        for td in title_detail:
            elements = td.get("TitleElement", [])
            if isinstance(elements, list):
                for elem in elements:
                    title_text = elem.get("TitleText", {})
                    content = title_text.get("content", "").strip() if isinstance(title_text, dict) else ""
                    if content:
                        return content
            elif isinstance(elements, dict):
                title_text = elements.get("TitleText", {})
                content = title_text.get("content", "").strip() if isinstance(title_text, dict) else ""
                if content:
                    return content
    elif isinstance(title_detail, dict):
        elements = title_detail.get("TitleElement", [])
        if isinstance(elements, list):
            for elem in elements:
                title_text = elem.get("TitleText", {})
                content = title_text.get("content", "").strip() if isinstance(title_text, dict) else ""
                if content:
                    return content
        elif isinstance(elements, dict):
            title_text = elements.get("TitleText", {})
            content = title_text.get("content", "").strip() if isinstance(title_text, dict) else ""
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
        name = person_name.get("content", "").strip() if isinstance(person_name, dict) else str(person_name).strip()
        entry = {
            "name": name,
            "role": contrib.get("ContributorRole", []),
            "biography": (contrib.get("BiographicalNote", "") or "").strip() or None,
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
        "datemodified": hanmoto.get("datemodified"),
        "dateshuppan": hanmoto.get("dateshuppan"),
        "ndc_code": hanmoto.get("ndccode"),
        "genre_code_trc": hanmoto.get("genrecodetrc"),
        "award_info": hanmoto.get("jushoujouhou"),
        "obi_content": hanmoto.get("obinaiyou"),
        "synopsis_105w": hanmoto.get("kaisetsu105w"),
        "reviews": hanmoto.get("reviews", []),
        "reprint_info": hanmoto.get("jyuhan", []),
        "stock_status": hanmoto.get("zaiko"),
        "related_isbn": hanmoto.get("kanrenshoisbn"),
    }


def parse_book(item: dict, source: str = "unknown") -> dict:
    """APIレスポンスの1件を整理された辞書に変換する。"""
    summary = item.get("summary", {})
    onix = item.get("onix", {})
    hanmoto = item.get("hanmoto", {})

    synopsis = extract_synopsis(onix)
    cover_onix = extract_cover_url(onix)
    subjects = extract_subjects(onix)
    c_code = extract_c_code(subjects)
    series = extract_series_info(onix)
    contributors = extract_contributors(onix)
    hanmoto_info = extract_hanmoto_info(hanmoto)

    cover_summary = (summary.get("cover", "") or "").strip() or None
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
        "c_code": c_code,
        "subjects": subjects,
        "contributors": contributors,
        "hanmoto": hanmoto_info,
        "source": source,  # "ndl" or "coverage_sample"
    }


# ----------------------------- Cコード分析 -----------------------------

# Cコード販売対象（先頭1桁）
C_CODE_AUDIENCE = {
    "0": "一般",
    "1": "教養",
    "2": "実用",
    "3": "専門",
    "4": "検定教科書",
    "5": "婦人",
    "6": "学参I（小中）",
    "7": "学参II（高校）",
    "8": "児童",
    "9": "雑誌扱い",
}

# Cコード形態（2桁目）
C_CODE_FORM = {
    "0": "単行本",
    "1": "文庫",
    "2": "新書",
    "3": "全集・双書",
    "4": "ムック",
    "5": "事・辞典",
    "6": "図鑑",
    "7": "絵本",
    "8": "磁性媒体等",
    "9": "コミック",
}

# Cコード内容（末尾2桁）で小説・ライトノベル関連
C_CODE_FICTION_CONTENT = {
    "93": "日本文学、小説・物語",
    "94": "外国文学、小説・物語",
    "97": "日本文学、詩歌",
    "95": "日本文学、評論・随筆等",
}


def decode_c_code(c_code: str) -> dict | None:
    """Cコードを分解して可読情報にする。"""
    if not c_code or len(c_code) != 4:
        return None
    return {
        "raw": c_code,
        "audience": C_CODE_AUDIENCE.get(c_code[0], f"不明({c_code[0]})"),
        "form": C_CODE_FORM.get(c_code[1], f"不明({c_code[1]})"),
        "content_code": c_code[2:],
        "is_fiction": c_code[2:] in ("93", "94"),
    }


# ----------------------------- メイン -----------------------------

def main():
    started_at = datetime.now()
    print("=" * 70)
    print("openBD API フルデータ収集スクリプト")
    print(f"開始時刻: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ===== Step 1: NDLからISBN抽出 =====
    ndl_isbns = extract_ndl_isbns(NDL_FILE)
    ndl_isbn_set = set(ndl_isbns)

    # ===== Step 2: Coverage APIからサンプリング =====
    coverage_isbns = fetch_coverage_isbns(ndl_isbn_set)

    # ===== ISBNリスト統合 =====
    all_isbns = ndl_isbns + coverage_isbns
    # 重複除去
    seen = set()
    unique_isbns = []
    for isbn in all_isbns:
        if isbn not in seen:
            seen.add(isbn)
            unique_isbns.append(isbn)
    all_isbns = unique_isbns

    # ISBNソースのトラッキング
    isbn_source = {}
    for isbn in ndl_isbns:
        isbn_source[isbn] = "ndl"
    for isbn in coverage_isbns:
        if isbn not in isbn_source:
            isbn_source[isbn] = "coverage_sample"

    print(f"\n  統合後ISBN数: {len(all_isbns):,}")
    print(f"    NDL由来: {len(ndl_isbns):,}")
    print(f"    Coverage由来: {len(coverage_isbns):,}")

    # ===== Step 3: openBD一括取得 =====
    raw_results = fetch_all_books(all_isbns)

    # ===== Step 4: データ整形 =====
    print(f"\n[Step 4] データ整形・保存")

    books = []
    null_count = 0
    for isbn, item in raw_results:
        if item is None:
            null_count += 1
            continue
        source = isbn_source.get(isbn, "unknown")
        book = parse_book(item, source=source)
        books.append(book)

    total_requested = len(all_isbns)
    print(f"  取得成功: {len(books):,} / {total_requested:,} 件")
    print(f"  未収録ISBN: {null_count:,} 件")

    # NDL/Coverage別の取得数
    ndl_books = [b for b in books if b["source"] == "ndl"]
    cov_books = [b for b in books if b["source"] == "coverage_sample"]
    print(f"  NDL由来で取得: {len(ndl_books):,}")
    print(f"  Coverage由来で取得: {len(cov_books):,}")

    # ===== 保存 =====
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    output_data = {
        "metadata": {
            "source": "openBD API (https://api.openbd.jp/v1/)",
            "ndl_source_file": str(NDL_FILE),
            "collected_at": datetime.now().isoformat(),
            "total_requested": total_requested,
            "total_retrieved": len(books),
            "ndl_isbns": len(ndl_isbns),
            "coverage_sample_isbns": len(coverage_isbns),
            "ndl_retrieved": len(ndl_books),
            "coverage_retrieved": len(cov_books),
            "null_responses": null_count,
        },
        "books": books,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    print(f"  出力ファイル: {OUTPUT_FILE}")

    # ===== サマリー =====
    print_summary(books, started_at)


def print_summary(books: list[dict], started_at: datetime):
    """収集結果のサマリーを表示する。"""
    total = len(books)
    if total == 0:
        print("\n[サマリー] 取得データ 0件。終了します。")
        return

    pct = lambda n: f"{n:,}/{total:,} ({n / total * 100:.1f}%)"

    print("\n" + "=" * 70)
    print("収集結果サマリー")
    print("=" * 70)
    print(f"合計取得件数: {total:,}")

    # --- データ充実度 ---
    has_synopsis = sum(1 for b in books if b.get("synopsis"))
    has_synopsis_105w = sum(1 for b in books if b.get("synopsis_105w"))
    has_any_synopsis = sum(
        1 for b in books
        if b.get("synopsis") or b.get("synopsis_105w")
    )
    has_cover = sum(1 for b in books if b.get("cover"))
    has_series = sum(1 for b in books if b.get("series"))
    has_c_code = sum(1 for b in books if b.get("c_code"))
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

    print(f"\n--- データ充実度 ---")
    print(f"  あらすじ (ONIX TextContent):  {pct(has_synopsis)}")
    print(f"  あらすじ (版元105字):          {pct(has_synopsis_105w)}")
    print(f"  あらすじ (いずれか):           {pct(has_any_synopsis)}")
    print(f"  書影URL:                       {pct(has_cover)}")
    print(f"  シリーズ/レーベル名:           {pct(has_series)}")
    print(f"  Cコード:                       {pct(has_c_code)}")
    print(f"  Subject情報:                   {pct(has_subjects)}")
    print(f"  ライトノベルフラグ (lanove):   {pct(has_lanove)}")
    print(f"  受賞情報:                      {pct(has_award)}")
    print(f"  帯の内容:                      {pct(has_obi)}")

    # --- ソース別 ---
    print(f"\n--- ソース別内訳 ---")
    source_counts = Counter(b.get("source", "unknown") for b in books)
    for src, cnt in source_counts.most_common():
        src_books = [b for b in books if b.get("source") == src]
        src_synopsis = sum(1 for b in src_books if b.get("synopsis") or b.get("synopsis_105w"))
        src_cover = sum(1 for b in src_books if b.get("cover"))
        print(f"  {src}: {cnt:,}件 (あらすじ: {src_synopsis}, 書影: {src_cover})")

    # --- Cコード分布 ---
    print(f"\n--- Cコード分布 ---")
    c_codes = [b.get("c_code") for b in books if b.get("c_code")]
    if c_codes:
        c_counter = Counter(c_codes)
        print(f"  Cコード付き: {len(c_codes):,}件")
        print(f"  ユニークCコード数: {len(c_counter)}")

        # 上位20件
        print(f"\n  上位20件:")
        for code, count in c_counter.most_common(20):
            decoded = decode_c_code(code)
            if decoded:
                label = f"C{code} [{decoded['audience']}/{decoded['form']}/{decoded['content_code']}]"
                fiction = " ★小説" if decoded['is_fiction'] else ""
                print(f"    {label:45s}: {count:4d}件{fiction}")

        # 小説関連集計
        fiction_count = sum(
            cnt for code, cnt in c_counter.items()
            if len(code) == 4 and code[2:] in ("93", "94")
        )
        print(f"\n  小説関連 (末尾93/94): {fiction_count:,}件")
    else:
        print("  Cコード付きデータなし")

    # --- 出版社別 Top 20 ---
    print(f"\n--- 出版社別 Top 20 ---")
    publishers = Counter(b.get("publisher", "不明") for b in books)
    for pub, count in publishers.most_common(20):
        print(f"  {pub:30s}: {count:4d}件")

    # --- lanoveフラグ付き作品 ---
    lanove_books = [b for b in books if b.get("hanmoto", {}).get("lanove")]
    if lanove_books:
        print(f"\n--- ライトノベルフラグ付き作品 (全{len(lanove_books)}件, 先頭20件) ---")
        for b in lanove_books[:20]:
            print(f"  [{b['isbn']}] {b['title']} / {b['author']} ({b['publisher']})")
    else:
        print(f"\n--- ライトノベルフラグ付き作品: 0件 ---")

    elapsed = datetime.now() - started_at
    print(f"\n完了時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"所要時間: {elapsed.total_seconds():.1f}秒")
    print("=" * 70)


if __name__ == "__main__":
    main()
