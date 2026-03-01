#!/usr/bin/env python3
"""
国立国会図書館サーチ（NDLサーチ）OpenSearch APIから大量の書誌データを収集するスクリプト。

NDC分類・年代別分割、タイトルキーワード検索、出版社ベース検索を組み合わせ、
1検索あたり500件上限の制約を回避して大量データを取得する。
重複除去後にJSONファイルとして出力する。

目標: 5,000件以上の日本語小説書誌データ
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime


# --- 設定 ---
BASE_URL = "https://ndlsearch.ndl.go.jp/api/opensearch"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "ndl_books_full.json")
SLEEP_SECONDS = 2
MAX_RETRIES = 3
RETRY_SLEEP = 5

# XML名前空間
NS = {
    "rss": "http://purl.org/rss/1.0/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcndl": "http://ndl.go.jp/dcndl/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "openSearch": "http://a9.com/-/spec/opensearchrss/1.0/",
}

# =============================================================================
# 検索条件リスト
# =============================================================================

# NDC 913.6 (日本の小説) 年代別分割
NDC_YEAR_QUERIES = [
    {
        "label": "NDC 913.6 / 2024年以降",
        "params": {"ndc": "913.6", "from": "2024", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2023年",
        "params": {"ndc": "913.6", "from": "2023", "until": "2023", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2022年",
        "params": {"ndc": "913.6", "from": "2022", "until": "2022", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2021年",
        "params": {"ndc": "913.6", "from": "2021", "until": "2021", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2020年",
        "params": {"ndc": "913.6", "from": "2020", "until": "2020", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2018-2019年",
        "params": {"ndc": "913.6", "from": "2018", "until": "2019", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2016-2017年",
        "params": {"ndc": "913.6", "from": "2016", "until": "2017", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2014-2015年",
        "params": {"ndc": "913.6", "from": "2014", "until": "2015", "cnt": "500"},
    },
    {
        "label": "NDC 913.6 / 2010-2013年",
        "params": {"ndc": "913.6", "from": "2010", "until": "2013", "cnt": "500"},
    },
]

# タイトルキーワード検索
TITLE_KEYWORD_QUERIES = [
    {"label": "タイトル「異世界」", "params": {"title": "異世界", "cnt": "500"}},
    {"label": "タイトル「転生」", "params": {"title": "転生", "cnt": "500"}},
    {"label": "タイトル「魔法」", "params": {"title": "魔法", "cnt": "500"}},
    {"label": "タイトル「勇者」", "params": {"title": "勇者", "cnt": "500"}},
    {"label": "タイトル「ダンジョン」", "params": {"title": "ダンジョン", "cnt": "500"}},
    {"label": "タイトル「推理」", "params": {"title": "推理", "cnt": "500"}},
    {"label": "タイトル「ミステリー」", "params": {"title": "ミステリー", "cnt": "500"}},
    {"label": "タイトル「探偵」", "params": {"title": "探偵", "cnt": "500"}},
    {"label": "タイトル「殺人」", "params": {"title": "殺人", "cnt": "500"}},
    {"label": "タイトル「ライトノベル」", "params": {"title": "ライトノベル", "cnt": "500"}},
    {"label": "タイトル「ファンタジー」", "params": {"title": "ファンタジー", "cnt": "500"}},
    {"label": "タイトル「恋愛」", "params": {"title": "恋愛", "cnt": "500"}},
    {"label": "タイトル「令嬢」", "params": {"title": "令嬢", "cnt": "500"}},
    {"label": "タイトル「冒険」", "params": {"title": "冒険", "cnt": "500"}},
]

# 出版社ベース検索（ラノベ・小説主要出版社）
PUBLISHER_QUERIES = [
    {
        "label": "出版社 KADOKAWA / NDC 913",
        "params": {"publisher": "KADOKAWA", "ndc": "913", "cnt": "500"},
    },
    {
        "label": "出版社 講談社 / NDC 913",
        "params": {"publisher": "講談社", "ndc": "913", "cnt": "500"},
    },
    {
        "label": "出版社 集英社 / NDC 913",
        "params": {"publisher": "集英社", "ndc": "913", "cnt": "500"},
    },
    {
        "label": "出版社 小学館 / NDC 913",
        "params": {"publisher": "小学館", "ndc": "913", "cnt": "500"},
    },
]

# 全クエリを統合
ALL_QUERIES = NDC_YEAR_QUERIES + TITLE_KEYWORD_QUERIES + PUBLISHER_QUERIES


# =============================================================================
# ユーティリティ関数
# =============================================================================

def build_url(params: dict) -> str:
    """検索パラメータからURLを組み立てる。"""
    query_string = urllib.parse.urlencode(params, encoding="utf-8")
    return f"{BASE_URL}?{query_string}"


def fetch_xml(url: str) -> bytes:
    """URLからXMLデータを取得する。リトライ付き。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NDL-BookCollector/2.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            if attempt < MAX_RETRIES:
                print(f"    リトライ {attempt}/{MAX_RETRIES} ({e})")
                time.sleep(RETRY_SLEEP)
            else:
                raise


def get_text(elem, tag: str, ns_prefix: str = None) -> str | None:
    """要素からテキストを取得する。"""
    if ns_prefix and ns_prefix in NS:
        full_tag = f"{{{NS[ns_prefix]}}}{tag}"
    else:
        full_tag = tag

    child = elem.find(full_tag)
    if child is not None and child.text:
        return child.text.strip()
    return None


def get_all_text(elem, tag: str, ns_prefix: str) -> list[str]:
    """要素から同名タグの全テキストを取得する。"""
    results = []
    full_tag = f"{{{NS[ns_prefix]}}}{tag}"
    for child in elem.findall(full_tag):
        if child.text:
            results.append(child.text.strip())
    return results


def extract_isbn(item_elem) -> str | None:
    """
    dc:identifier の中からISBNを抽出する。
    xsi:type="dcndl:ISBN" 属性を持つものがISBN。
    属性がない場合でも、ISBN形式（10桁 or 13桁の数字）であれば採用する。
    """
    isbn = None
    xsi_type_attr = f"{{{NS['xsi']}}}type"

    for ident in item_elem.findall(f"{{{NS['dc']}}}identifier"):
        attr_type = ident.get(xsi_type_attr, "")
        text = (ident.text or "").strip()

        if "ISBN" in attr_type.upper():
            isbn = text
            break

        # 属性がなくてもISBN形式ならば採用
        cleaned = text.replace("-", "")
        if cleaned.isdigit() and len(cleaned) in (10, 13):
            isbn = text

    return isbn


def extract_ndc(item_elem) -> str | None:
    """dc:subject からNDC分類を抽出する。"""
    xsi_type_attr = f"{{{NS['xsi']}}}type"
    for subj in item_elem.findall(f"{{{NS['dc']}}}subject"):
        attr_type = subj.get(xsi_type_attr, "")
        if "NDC" in attr_type.upper():
            if subj.text:
                return subj.text.strip()
    return None


def extract_record(item_elem) -> dict | None:
    """item要素から書誌レコードを抽出する。"""
    title = get_text(item_elem, "title", "dc")
    if not title:
        # RSSのtitleタグ（名前空間なし）も試す
        title = get_text(item_elem, "title")
    if not title:
        return None

    creator = get_text(item_elem, "creator", "dc")
    publisher = get_text(item_elem, "publisher", "dc")

    # 出版日: dcterms:issued を優先、なければ dc:date
    issued = get_text(item_elem, "issued", "dcterms")
    if not issued:
        issued = get_text(item_elem, "date", "dc")

    isbn = extract_isbn(item_elem)
    ndc = extract_ndc(item_elem)

    series_title = get_text(item_elem, "seriesTitle", "dcndl")
    volume = get_text(item_elem, "volume", "dcndl")

    # タイトル読み
    title_transcription = get_text(item_elem, "titleTranscription", "dcndl")
    # 著者読み
    creator_transcription = get_text(item_elem, "creatorTranscription", "dcndl")

    record = {
        "title": title,
        "creator": creator,
        "publisher": publisher,
        "issued": issued,
        "isbn": isbn,
        "ndc": ndc,
        "series_title": series_title,
        "volume": volume,
        "title_transcription": title_transcription,
        "creator_transcription": creator_transcription,
    }

    return record


def parse_items(xml_bytes: bytes) -> list[dict]:
    """
    OpenSearchレスポンスXMLをパースし、書誌情報のリストを返す。
    RSS 2.0形式: channel/item 構造
    """
    items = []
    root = ET.fromstring(xml_bytes)

    channel = root.find("channel")
    if channel is None:
        return items

    for item_elem in channel.findall("item"):
        record = extract_record(item_elem)
        if record:
            items.append(record)

    return items


def deduplicate(records: list[dict]) -> list[dict]:
    """
    ISBN + (タイトル+著者) の両方で重複除去する。
    ISBNがあるレコードはISBNで重複チェック。
    ISBNがないレコードはタイトル+著者のペアで重複チェック。
    """
    seen_isbns: set[str] = set()
    seen_title_creator: set[tuple[str, str]] = set()
    unique: list[dict] = []

    for rec in records:
        isbn = rec.get("isbn")
        if isbn:
            cleaned = isbn.replace("-", "")
            if cleaned in seen_isbns:
                continue
            seen_isbns.add(cleaned)
        else:
            key = (rec.get("title", ""), rec.get("creator", ""))
            if key in seen_title_creator:
                continue
            seen_title_creator.add(key)

        unique.append(rec)

    return unique


def extract_year(issued: str | None) -> str | None:
    """出版日文字列から年を抽出する。"""
    if not issued:
        return None
    # "2023.5" "2023-05" "2023" etc.
    year_str = issued[:4]
    if year_str.isdigit():
        return year_str
    return None


def print_summary(unique_records: list[dict], total_before: int, query_stats: list[dict]):
    """収集結果のサマリーを表示する。"""
    total_after = len(unique_records)
    duplicates_removed = total_before - total_after
    isbn_total = sum(1 for r in unique_records if r.get("isbn"))

    print("\n" + "=" * 70)
    print("収集結果サマリー")
    print("=" * 70)
    print(f"検索条件数:       {len(query_stats)}")
    print(f"総取得件数(生):   {total_before} 件")
    print(f"重複除去後:       {total_after} 件 (除去: {duplicates_removed} 件)")
    if total_after > 0:
        print(f"ISBN取得率:       {isbn_total}/{total_after} ({isbn_total / total_after * 100:.1f}%)")
    else:
        print("ISBN取得率:       N/A")

    # --- 検索条件別内訳 ---
    print("\n--- 検索条件別内訳 ---")
    for stat in query_stats:
        error_note = f" [ERROR: {stat.get('error', '')}]" if "error" in stat else ""
        print(f"  {stat['label']}: {stat['count']} 件 (ISBN: {stat['isbn_count']}){error_note}")

    # --- 出版社トップ20 ---
    print("\n--- 出版社トップ20 ---")
    publisher_counts = Counter(rec.get("publisher") or "(不明)" for rec in unique_records)
    for pub, cnt in publisher_counts.most_common(20):
        print(f"  {pub}: {cnt} 件")

    # --- 年代分布 ---
    print("\n--- 年代分布 ---")
    year_counts: Counter = Counter()
    unknown_year = 0
    for rec in unique_records:
        year = extract_year(rec.get("issued"))
        if year:
            year_counts[year] += 1
        else:
            unknown_year += 1
    for year in sorted(year_counts.keys(), reverse=True):
        print(f"  {year}年: {year_counts[year]} 件")
    if unknown_year:
        print(f"  (年不明): {unknown_year} 件")

    print(f"\n出力ファイル: {OUTPUT_FILE}")
    print("=" * 70)


# =============================================================================
# メイン処理
# =============================================================================

def main():
    print("=" * 70)
    print("国立国会図書館サーチ OpenSearch API 大量データ収集")
    print(f"実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"検索条件数: {len(ALL_QUERIES)}")
    print(f"理論上限: {len(ALL_QUERIES) * 500} 件 (重複含む)")
    print("=" * 70)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_records: list[dict] = []
    query_stats: list[dict] = []

    for i, query in enumerate(ALL_QUERIES):
        label = query["label"]
        params = query["params"]
        url = build_url(params)

        print(f"\n[{i + 1}/{len(ALL_QUERIES)}] {label}")
        print(f"  URL: {url}")

        try:
            xml_bytes = fetch_xml(url)
            items = parse_items(xml_bytes)
            count = len(items)
            isbn_count = sum(1 for item in items if item.get("isbn"))

            print(f"  取得件数: {count} 件 (ISBN付き: {isbn_count} 件)")
            query_stats.append({
                "label": label,
                "count": count,
                "isbn_count": isbn_count,
            })
            all_records.extend(items)

        except Exception as e:
            print(f"  エラー: {e}", file=sys.stderr)
            query_stats.append({
                "label": label,
                "count": 0,
                "isbn_count": 0,
                "error": str(e),
            })

        # 最後のリクエスト以外はスリープ
        if i < len(ALL_QUERIES) - 1:
            print(f"  {SLEEP_SECONDS}秒待機中...")
            time.sleep(SLEEP_SECONDS)

    # 重複除去
    total_before = len(all_records)
    unique_records = deduplicate(all_records)
    total_after = len(unique_records)
    duplicates_removed = total_before - total_after

    # ISBN取得率
    isbn_total = sum(1 for r in unique_records if r.get("isbn"))

    # 出版社分布
    publisher_counts = Counter(rec.get("publisher") or "(不明)" for rec in unique_records)

    # 年代分布
    year_counts: Counter = Counter()
    for rec in unique_records:
        year = extract_year(rec.get("issued"))
        if year:
            year_counts[year] += 1

    # JSON出力
    output_data = {
        "metadata": {
            "source": "国立国会図書館サーチ OpenSearch API",
            "endpoint": BASE_URL,
            "collected_at": datetime.now().isoformat(),
            "total_records": total_after,
            "total_raw_records": total_before,
            "duplicates_removed": duplicates_removed,
            "isbn_count": isbn_total,
            "isbn_rate": round(isbn_total / total_after * 100, 1) if total_after > 0 else 0,
            "query_count": len(ALL_QUERIES),
            "publisher_distribution": dict(publisher_counts.most_common(30)),
            "year_distribution": dict(sorted(year_counts.items(), reverse=True)),
        },
        "query_stats": query_stats,
        "books": unique_records,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # サマリー表示
    print_summary(unique_records, total_before, query_stats)

    print("\n収集完了!")
    return total_after


if __name__ == "__main__":
    total = main()
    if total < 5000:
        print(f"\n[注意] 目標5,000件に対して {total} 件でした。")
    else:
        print(f"\n[達成] 目標5,000件以上を達成: {total} 件")
