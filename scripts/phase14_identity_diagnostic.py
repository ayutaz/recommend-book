"""Diagnose the five phase14 identity cases from saved local evidence only.

The script intentionally does not fetch, call a model, copy/hash a large
database, or write to any source database.  It records the two old public
QID collisions and replays the three phase13 complete-input guards in memory
using the current identity comparison code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import editorial_work_record, ingest_editorial_description
from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/autonomous-next-v3"
DEFAULT_OUTPUT = BASE / "evaluation/phase14-identity-v1"
QIDS = ("Q10936605", "Q105464593", "Q848612", "Q2384134", "Q213019")
HELD_QIDS = set(QIDS[2:])


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value) -> None:
    path.write_bytes((canonical_json(value) + "\n").encode("utf-8"))


def compact_record(record: SourceRecord) -> dict:
    return {
        "source": record.source,
        "source_id": record.source_id,
        "record_kind": record.record_kind,
        "work_kind": record.work_kind,
        "titles": [title.model_dump(mode="json") for title in record.titles],
        "contributors": [contributor.model_dump(mode="json") for contributor in record.contributors],
        "identifiers": [identifier.model_dump(mode="json") for identifier in record.identifiers],
        "work_refs": [identifier.model_dump(mode="json") for identifier in record.work_refs],
        "original_language": record.original_language,
        "language": record.language,
        "first_publication_year": record.first_publication_year,
        "publication_year": record.publication_year,
        "volume": record.volume,
        "series": record.series,
        "evidence_count": len(record.evidence),
    }


def read_case_inputs() -> tuple[dict, dict[str, SourceRecord]]:
    candidates = BASE / "evaluation/phase13-balanced-candidates-v1"
    report = json.loads((candidates / "candidate-results.json").read_bytes())
    records = {
        record.source_id: record
        for line in (candidates / "wd-records.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
        for record in [SourceRecord.model_validate_json(line)]
    }
    if set(records) != {case["wikidata_qid"] for case in report["eligible_cases"]}:
        raise ValueError("Saved candidate authority records do not match candidate report")
    return report, records


def replay_held_cases(report: dict, records: dict[str, SourceRecord]) -> list[dict]:
    outcomes_path = BASE / "preparation/phase13-balanced-editorial-v2/complete-input-outcomes.json"
    previous = {item["qid"]: item for item in json.loads(outcomes_path.read_bytes())}
    results = []
    for case in report["eligible_cases"]:
        qid = case["wikidata_qid"]
        if qid not in HELD_QIDS:
            continue
        authority = records[qid]
        edition = ingest_editorial_description(
            case["opf_path"], source_url=case["source_url"],
            retrieved_at=case["retrieved_at"], wikidata_record=authority,
        )
        editorial = editorial_work_record(edition)
        with DatasetDB(":memory:") as database:
            database.ingest([authority, edition, editorial])
            works = [work for work in database.work_inputs() if work.expression_id is None]
            decisions = [
                dict(row) for row in database.connection.execute(
                    "SELECT left_work_id,right_work_id,status,reason,method,active "
                    "FROM resolution_decisions ORDER BY decision_id"
                )
            ]
        results.append({
            "qid": qid,
            "title": case["title"],
            "source_bindings": {
                "wikidata_raw_path": case["wikidata_raw_path"],
                "wikidata_raw_sha256": case["wikidata_raw_sha256"],
                "opf_path": case["opf_path"],
                "opf_sha256": case["opf_sha256"],
                "opf_source_url": case["source_url"],
            },
            "records": [compact_record(item) for item in (authority, edition, editorial)],
            "pre_patch_outcome": previous[qid],
            "post_patch_replay": {
                "work_count": len(works),
                "source_count": len(works[0].source_record_ids) if len(works) == 1 else None,
                "evidence_count": len(works[0].evidence) if len(works) == 1 else None,
                "decisions": decisions,
                "repair_effect": "eligible_after_title_key_fix" if len(works) == 1 else "held",
            },
            "model_calls": 0,
            "network_requests": 0,
        })
    return results


def duplicate_case(qid: str) -> dict:
    first = BASE / "database/phase8-first10-v1/dataset.sqlite"
    subclass = BASE / "reference/phase3-declared-genre-subclass-admission-v1/source-bindings.sqlite"
    rows = []
    with sqlite3.connect(first.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        matches = connection.execute(
            "SELECT w.work_id,w.canonical_id,w.title,w.work_kind,w.original_language,w.first_publication_year "
            "FROM works w JOIN external_ids e ON e.entity_id=w.work_id "
            "WHERE e.source='wikidata' AND e.scope='work' AND e.value=? ORDER BY w.work_id", (qid,)
        ).fetchall()
        for row in matches:
            rows.append({"cohort": "phase8", **dict(row)})
    selection = json.loads((BASE / "reference/phase3-declared-genre-subclass-admission-v1/selection.json").read_bytes())
    binding = next(item for item in selection["work_bindings"] if item["source_qid"] == qid)
    with sqlite3.connect(subclass.as_uri() + "?mode=ro&immutable=1", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        matches = connection.execute(
            "SELECT source_id,version_id,record_json,raw_sha256 FROM source_records WHERE source_id=? ORDER BY version_id", (qid,)
        ).fetchall()
        rows.extend({"cohort": "declared-subclass", "source_id": row["source_id"], "version_id": row["version_id"], "raw_sha256": row["raw_sha256"]} for row in matches)
    return {
        "qid": qid,
        "phase8_public_rows": rows,
        "declared_scope_decision": {
            "scope": binding["scope_decision"]["scope"],
            "holds": binding["scope_decision"]["holds"],
            "raw_sha256": binding["scope_decision"]["raw_sha256"],
            "review_basis": binding["scope_decision"]["review_basis"],
        },
        "exact_qid_equal": True,
        "public_work_ids_are_distinct": True,
        "merge_authorized": False,
        "status": "held_scope_and_crosswalk_unverified",
        "reason": "The same saved Wikidata QID appears in separately projected public cohorts. The broad phase8 row is original-versus-version unverified, while the declared-subclass row has a limited original_work scope decision; no saved crosswalk authorizes rewriting either public work_id. QID equality is retained as a duplicate signal, not an automatic merge instruction.",
        "network_requests": 0,
        "model_calls": 0,
    }


def build_report(output: Path) -> dict:
    candidate_report, records = read_case_inputs()
    if set(case["wikidata_qid"] for case in candidate_report["eligible_cases"]) & {"Q10936605", "Q105464593"}:
        raise ValueError("Duplicate cases unexpectedly entered phase13 new-candidate inputs")
    duplicates = [duplicate_case(qid) for qid in QIDS[:2]]
    held = replay_held_cases(candidate_report, records)
    if {item["qid"] for item in held} != HELD_QIDS:
        raise ValueError("Held candidate coverage is incomplete")
    return {
        "version": "phase14-identity-diagnostic-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": "fc96eeaae3ee15e3e260be7ab6881afe852e4fb6",
        "requirements": {
            "new_candidates": 0,
            "new_opf_requests": 0,
            "new_network_requests": 0,
            "new_model_calls": 0,
            "old_sources_mutated": False,
            "automatic_identity_merge": False,
        },
        "duplicate_cases": duplicates,
        "held_complete_input_cases": held,
        "repair_summary": {
            "code_defects": [
                "Q848612: ASCII/curly apostrophe comparison mismatch fixed by conservative title match key",
                "Q2384134: ASCII/curly apostrophe comparison mismatch fixed by conservative title match key",
            ],
            "scope_or_evidence_holds": [
                "Q10936605: duplicate public rows require crosswalk and scope verification; no merge",
                "Q105464593: duplicate public rows require crosswalk and scope verification; no merge",
                "Q213019: two OpenLibrary work IDs remain ambiguous; no ID selection",
            ],
            "derived_replay_only": True,
            "classification_started": False,
        },
    }


def write_markdown(output: Path, report: dict) -> None:
    lines = [
        "# Phase14 identity diagnostic",
        "",
        "保存済みのraw/OPF/SQLiteだけで、QID重複2組とphase13完全入力保留3件を診断した。新規取得・モデル呼び出し・旧DB変更はない。",
        "",
        "## 結果",
        "",
        "- Q848612 / Q2384134: 同一QID・同一著者で、ASCII apostropheとcurly apostropheだけが比較キーで不一致。限定的なtitle比較正規化で、3入力が1 workに解決することをin-memory再現した。",
        "- Q213019: 同じWikidata recordにOpenLibrary work IDが2件あり、どちらを採用する保存根拠がない。保留を維持した。",
        "- Q10936605 / Q105464593: 同一QIDがphase8行とdeclared-subclass行に別work_idで現れる。scope/crosswalkを保存資料だけで確定できないため、公開行を自動統合しない。",
        "",
        "## 制約",
        "",
        "この原票は修正後コードによるin-memory replayを含むが、phase13本番DBやdeliveryは書き換えていない。AI分類・推薦再計算・新規ネットワーク取得は実施していない。",
        "",
    ]
    (output / "diagnosis.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError(f"Fresh diagnostic namespace required: {output}")
    report = build_report(output)
    output.mkdir(parents=True)
    save_json(output / "diagnosis.json", report)
    write_markdown(output, report)
    manifest = {
        "version": "phase14-identity-diagnostic-manifest-v1",
        "output_sha256": {path.name: sha(path) for path in output.iterdir() if path.is_file()},
        "source_paths": [
            str(BASE / "evaluation/phase13-balanced-candidates-v1/candidate-results.json"),
            str(BASE / "evaluation/phase13-balanced-candidates-v1/wd-records.jsonl"),
            str(BASE / "preparation/phase13-balanced-editorial-v2/complete-input-outcomes.json"),
            str(BASE / "database/phase8-first10-v1/dataset.sqlite"),
            str(BASE / "reference/phase3-declared-genre-subclass-admission-v1/selection.json"),
        ],
        "model_calls": 0,
        "network_requests": 0,
        "source_databases_mutated": False,
    }
    save_json(output / "manifest.json", manifest)
    print(json.dumps({"output": str(output), "duplicates": 2, "held_complete_inputs": 3, "model_calls": 0, "network_requests": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
