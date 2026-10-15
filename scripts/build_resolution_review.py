"""Freeze real, challenging bibliographic pairs for subsequent human adjudication.

Read-only input; never imports decisions into the database or manufactures gold.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from hashlib import sha256
from html import escape
from itertools import combinations
import json
from pathlib import Path
import sqlite3

from recommend_book.models import SourceRecord
from recommend_book.normalization import normalize_text
from recommend_book.resolution import compare_records, work_identifiers


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return sha256(data).hexdigest()


def authors(record):
    return {normalize_text(c.name) for c in record.contributors if c.role == "author"}


def author_keys(record):
    return {f"{i.source}:{i.value}" for c in record.contributors if c.role == "author"
            for i in c.identifiers if i.scope == "agent"} or authors(record)


def title_keys(record):
    return {normalize_text(t.value) for t in record.titles}


def pair_strata(a, b, triggers):
    flags = set()
    shared = work_identifiers(a) & work_identifiers(b)
    if shared:
        if title_keys(a) != title_keys(b):
            flags.add("shared_id_title_variation")
        if authors(a) != authors(b):
            flags.add("shared_id_author_variation")
        if a.language != b.language:
            flags.add("shared_id_language_variation")
        if a.record_kind != b.record_kind:
            flags.add("work_edition_boundary")
        if a.work_kind != b.work_kind:
            flags.add("shared_id_work_kind_variation")
        if a.volume != b.volume:
            flags.add("shared_id_volume_variation")
    if "title" in triggers and not shared:
        flags.add("same_title_distinct_identity")
        if authors(a) != authors(b):
            flags.add("same_title_author_difference")
    if "series" in triggers and not shared and not title_keys(a) & title_keys(b):
        flags.add("series_sibling_different_title")
    if "near_title_author" in triggers and not shared:
        flags.add("same_author_near_title")
    if len(authors(a)) != len(authors(b)) and flags:
        flags.add("coauthor_set_difference")
    if a.volume != b.volume and flags:
        flags.add("volume_difference_or_missing")
    if {a.work_kind, b.work_kind} == {"short_story", "collection"} and flags:
        flags.add("short_story_collection_boundary")
    return sorted(flags)


def candidate_pairs(records):
    indexes = {name: defaultdict(list) for name in ("identity", "title", "series", "author")}
    for idx, (_, r, _) in enumerate(records):
        for key in work_identifiers(r):
            indexes["identity"][key].append(idx)
        for key in title_keys(r):
            indexes["title"][key].append(idx)
        if r.series:
            indexes["series"][normalize_text(r.series)].append(idx)
        for key in author_keys(r):
            indexes["author"][key].append(idx)
    pairs = defaultdict(set)
    for kind in ("identity", "title", "series"):
        for key, values in sorted(indexes[kind].items(), key=lambda item: str(item[0])):
            for a, b in combinations(sorted(set(values)), 2):
                pairs[a, b].add(kind)
    for values in indexes["author"].values():
        for a, b in combinations(sorted(set(values)), 2):
            ra, rb = records[a][1], records[b][1]
            if work_identifiers(ra) & work_identifiers(rb):
                continue
            if any(0.68 <= SequenceMatcher(None, x, y).ratio() < 1
                   for x in title_keys(ra) for y in title_keys(rb)
                   if min(len(x), len(y)) >= 5):
                pairs[a, b].add("near_title_author")
    return pairs


def build(db_path: Path, output: Path, limit=400):
    if not 300 <= limit <= 500:
        raise ValueError("limit must be between 300 and 500")
    # A transaction freezes all reads despite concurrent single-writer updates.
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    rows = connection.execute("SELECT r.* FROM source_heads h JOIN source_records r "
                              "ON h.version_id=r.version_id ORDER BY r.version_id").fetchall()
    assertions = defaultdict(list)
    for row in connection.execute("SELECT * FROM field_assertions ORDER BY assertion_id"):
        assertions[row["source_version_id"]].append(dict(row))
    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
    connection.close()
    records = [(row["version_id"], SourceRecord.model_validate_json(row["record_json"]), dict(row))
               for row in rows if json.loads(row["record_json"])["record_kind"] != "agent"]
    candidates = []
    for (a, b), triggers in candidate_pairs(records).items():
        left, right = records[a], records[b]
        strata = pair_strata(left[1], right[1], triggers)
        if not strata:
            continue  # ordinary same-ID agreement is not a hard-example filler
        priority = 0 if any("difference" in s or "boundary" in s or "variation" in s and "title" not in s for s in strata) else 1 if "same_title_distinct_identity" in strata else 2 if "same_author_near_title" in strata else 3 if "shared_id_title_variation" in strata else 4
        candidates.append((priority, left[0], right[0], strata, sorted(triggers), left, right))
    candidates.sort(key=lambda item: item[:3])
    selected, series_counts = [], Counter()
    for item in candidates:
        _, _, _, strata, triggers, left, right = item
        series = normalize_text(left[1].series or "")
        # Bound correlated sibling pairs even when extra coauthor/title flags apply.
        if "series_sibling_different_title" in strata:
            if series_counts[series] >= 15 or series_counts["__total__"] >= 250:
                continue
            series_counts[series] += 1
            series_counts["__total__"] += 1
        proposal = compare_records(left[1], right[1])
        case_id = "pair_" + digest(serialized([left[0], right[0]]).encode())[:24]
        case = {"case_id": case_id, "strata": strata, "candidate_triggers": triggers,
                "source_version_ids": [left[0], right[0]],
                "rule_proposal": {"status": proposal.status, "reason": proposal.reason,
                                  "method": "current_compare_records", "is_gold": False},
                "human_adjudication": None, "sides": []}
        for version, record, row in (left, right):
            # Complete normalized SourceRecord and source field assertions retain provenance.
            case["sides"].append({"version_id": version, "source": record.source,
                "source_id": record.source_id, "record": record.model_dump(mode="json"),
                "provenance": {key: row[key] for key in ("source_url", "provider_id", "retrieved_at", "rights_policy_id", "payload_sha256", "raw_sha256", "raw_path")},
                "field_assertions": assertions[version]})
        selected.append(case)
        if len(selected) >= limit:
            break
    output.mkdir(parents=True, exist_ok=True)
    sample = "".join(serialized(case) + "\n" for case in selected).encode("utf-8")
    sample_sha = digest(sample)
    template = {"sample_sha256": sample_sha, "adjudications": []}
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
              "additionalProperties": False, "required": ["sample_sha256", "adjudications"],
              "properties": {"sample_sha256": {"const": sample_sha}, "adjudications": {
                  "type": "array", "items": {"type": "object", "additionalProperties": False,
                  "required": ["case_id", "reviewer", "decision", "reason"], "properties": {
                      "case_id": {"enum": [case["case_id"] for case in selected]},
                      "reviewer": {"type": "string", "minLength": 1, "pattern": "\\S"},
                      "decision": {"enum": ["same", "different", "uncertain"]},
                      "reason": {"type": "string", "minLength": 1, "pattern": "\\S"}}}}}}
    html = ["<!doctype html><html lang='ja'><meta charset='utf-8'><title>名寄せ 人手レビュー</title>",
            "<style>body{font-family:system-ui;margin:2rem}pre{white-space:pre-wrap;overflow-wrap:anywhere}.sides{display:grid;grid-template-columns:1fr 1fr;gap:1rem}article{border-top:2px solid #888;padding:1rem 0}</style>",
            "<h1>名寄せ 人手レビュー候補</h1><p>全件未裁定。ルール提案は正解ではありません。JSON templateにreviewer/decision/reasonを記入してください。</p>",
            f"<p>Sample SHA256: {sample_sha} / {len(selected)} pairs</p>"]
    for case in selected:
        html.append(f"<article id='{case['case_id']}'><h2>{case['case_id']}</h2><p>{escape(', '.join(case['strata']))}</p><div class='sides'>")
        for side in case["sides"]:
            bibliographic = {key: side["record"][key] for key in (
                "record_kind", "titles", "contributors", "identifiers", "work_refs", "original_language",
                "language", "first_publication_year", "publication_year", "publisher", "series", "volume",
                "work_kind", "contents")}
            view = {"version_id": side["version_id"], "source": side["source"], "source_id": side["source_id"],
                    "record": bibliographic, "provenance": side["provenance"]}
            displayed_assertions = [item for item in side["field_assertions"] if item["field_name"] in bibliographic]
            html.append("<section><pre>" + escape(json.dumps(view, ensure_ascii=False, indent=2)) + "</pre><details><summary>書誌項目assertions（完全SourceRecordと全assertionsはpairs.jsonl）</summary><pre>" + escape(json.dumps(displayed_assertions, ensure_ascii=False, indent=2)) + "</pre></details></section>")
        html.append("</div><details><summary>非正解のルール提案（先に資料を確認）</summary><pre>" + escape(serialized(case["rule_proposal"])) + "</pre></details></article>")
    html.append("</html>")
    artifacts = {"pairs.jsonl": sample, "review.html": "\n".join(html).encode("utf-8"),
                 "adjudications.template.json": (serialized(template) + "\n").encode(),
                 "adjudications.schema.json": (serialized(schema) + "\n").encode()}
    manifest = {"format_version": 1, "input_database": str(db_path), "schema_version": schema_version,
                "code_sha256": {str(path.relative_to(Path(__file__).resolve().parents[1])): digest(path.read_bytes())
                    for path in (Path(__file__).resolve(),
                                 Path(__file__).resolve().parents[1] / "src/recommend_book/resolution.py",
                                 Path(__file__).resolve().parents[1] / "src/recommend_book/normalization.py")},
                "input_source_heads": len(rows), "input_snapshot_sha256": digest(serialized([dict(row) for row in rows]).encode()),
                "requested_pairs": limit, "selected_pairs": len(selected), "minimum_target": 300,
                "minimum_shortfall": max(0, 300 - len(selected)), "eligible_pairs_before_caps": len(candidates),
                "strata_counts": dict(sorted(Counter(s for c in selected for s in c["strata"]).items())),
                "selection": "deterministic priority then source-version IDs; max 15 sibling pairs per series and 250 overall including overlapping flags; no random negatives",
                "human_adjudicated_count": 0, "gold_count": 0, "precision": None,
                "sample_sha256": sample_sha,
                "artifacts": {name: {"sha256": digest(data), "bytes": len(data)} for name, data in artifacts.items()}}
    artifacts["manifest.json"] = (serialized(manifest) + "\n").encode()
    # An existing frozen sample must never be silently replaced.
    for name, data in artifacts.items():
        path = output / name
        if path.exists() and path.read_bytes() != data:
            raise FileExistsError(f"Frozen artifact differs: {path}; use a new output directory")
    for name, data in artifacts.items():
        path = output / name
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/poc-v1/dataset.sqlite"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=400)
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.output, args.limit), ensure_ascii=True, indent=2))
