"""Focused offline checks for the v20 read-only content inventory."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "inventory_scale20k_content_v1", ROOT / "scripts/inventory_scale20k_content_v1.py"
)
inventory = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(inventory)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "catalogue.sqlite"
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"raw")
    raw_sha = _sha(raw)
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT NOT NULL, title TEXT NOT NULL,
          search_text TEXT NOT NULL, language TEXT, year INTEGER, recommendable INTEGER NOT NULL,
          display_allowed INTEGER NOT NULL, row_json TEXT NOT NULL);
        CREATE TABLE native_classifications(qid TEXT PRIMARY KEY, manifest_path TEXT NOT NULL,
          manifest_sha256 TEXT NOT NULL, labels_json TEXT NOT NULL, provenance_json TEXT NOT NULL);
        CREATE TABLE source_versions(version_id TEXT PRIMARY KEY, qid TEXT NOT NULL,
          record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL, raw_path TEXT NOT NULL,
          raw_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL, conditions_sha256 TEXT NOT NULL);
        CREATE TABLE work_genres(qid TEXT, genre_qid TEXT, label TEXT);
        """
    )
    conditions_sha = "c" * 64
    source_rows = []
    for qid, lang, year, recommendable in (
        ("Q1", "ja", 2020, 1),
        ("Q2", "en", 1900, 1),
        ("Q3", "en", 1950, 1),
        ("Q4", "ja", 1930, 1),
        ("Q5", "en", 2000, 0),
    ):
        version = f"wd:{qid}:v1"
        record = {
            "source": "wikidata", "source_id": qid, "record_kind": "work",
            "raw_path": str(raw), "raw_sha256": raw_sha, "rights_policy_id": "wikidata-cc0",
            "evidence": [{"kind": "description", "llm_input_allowed": True}],
        }
        raw_record = json.dumps(record, sort_keys=True)
        record_sha = hashlib.sha256(raw_record.encode()).hexdigest()
        db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)",
                   (qid, version, qid, qid, lang, year, recommendable, 1, "{}"))
        source_rows.append((version, qid, record_sha, raw_record, str(raw), raw_sha,
                            "2026-10-20T00:00:00+00:00", conditions_sha))
    db.executemany("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", source_rows)
    db.execute("INSERT INTO native_classifications VALUES (?,?,?,?,?)", ("Q1", "m", "h", "[]", "{}"))
    db.executemany("INSERT INTO work_genres VALUES (?,?,?)", [("Q1", "g", "light novel"), ("Q4", "g", "mystery fiction")])
    db.commit(); db.close()
    v19 = tmp_path / "v19"
    (v19 / "acquisition").mkdir(parents=True)
    (v19 / "attempt-reservations").mkdir()
    (v19 / "native-exclusions-v1.json").write_text(json.dumps({"qids": ["Q2"], "input_hashes": ["old"]}), encoding="utf-8")
    (v19 / "attempt-reservations" / "r.json").write_text(json.dumps({"qids": ["Q3"], "input_hashes": ["h3"]}), encoding="utf-8")
    (v19 / "acquisition" / "source-conditions-v19.json").write_text(json.dumps({
        "version": "test", "source": "wikidata", "policy_id": "wikidata-cc0",
        "retrieved_at": "2026-10-20T00:00:00+00:00",
        "usages": {key: "allowed" for key in ("acquire", "local_storage", "external_llm", "display", "redistribution", "commercial")},
        "metadataonly_scope": True, "scope": "structured metadata only",
    }), encoding="utf-8")
    return db_path, v19


def test_inventory_excludes_native_and_old_reservations_and_prioritizes_real_axes(tmp_path):
    db_path, v19 = _fixture(tmp_path)
    result = inventory.build_inventory(db_path=db_path, v19_root=v19, output_root=tmp_path / "out",
                                       now=datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert result["catalogue"]["works_observed"] == 5
    assert result["catalogue"]["native_classifications"] == 1
    assert result["available_supply"]["eligible_unprocessed_recommendable"] == 1
    rows = [json.loads(line) for line in (tmp_path / "out" / inventory.SUPPLY_NAME).read_text(encoding="utf-8").splitlines()]
    assert [row["qid"] for row in rows] == ["Q4"]
    assert rows[0]["priority_dimensions"] == ["japanese", "mystery", "period:1900-1945"]
    assert result["exclusions"]["union_excluded_qids"] == 3
    assert result["gap_counts"]["eligible_unprocessed_recommendable"]["source"] == 1
    assert result["gap_counts"]["eligible_unprocessed_recommendable"].get("period:1990-2009", 0) == 0
    assert result["gap_counts"]["language_counts"]["eligible_unprocessed_recommendable"] == {"ja": 1}


def test_inventory_requires_current_source_identity_and_createonly(tmp_path):
    db_path, v19 = _fixture(tmp_path)
    out = tmp_path / "out"
    inventory.build_inventory(db_path=db_path, v19_root=v19, output_root=out)
    with pytest.raises(FileExistsError):
        inventory.build_inventory(db_path=db_path, v19_root=v19, output_root=out)
    before = (out / inventory.SUPPLY_NAME).read_bytes()
    assert before
    # A tampered source identity cannot become eligible on a subsequent scope.
    db = sqlite3.connect(db_path)
    db.execute("UPDATE source_versions SET record_json=? WHERE qid='Q4'", (json.dumps({"source": "wikidata", "source_id": "Q999", "evidence": [{"llm_input_allowed": True}]}),))
    db.commit(); db.close()
    assert (out / inventory.SUPPLY_NAME).read_bytes() == before


def test_inventory_v2_separates_recommendable_dimensions_from_authority_scope(tmp_path):
    source = tmp_path / "v1"
    source.mkdir()
    summary = {
        "catalogue": {"works_observed": 10}, "exclusions": {},
        "gap_counts": {"all_current_works": {"japanese": 9}, "language_counts": {"all_current_works": {"ja": 9}, "eligible_unprocessed_recommendable": {"ja": 8}}},
        "available_supply": {"eligible_unprocessed_source_records": 20}, "source_interface": {}, "rights": {},
    }
    (source / "inventory-v1.json").write_text(json.dumps(summary), encoding="utf-8")
    (source / "eligible-supply-v1.jsonl").write_text("\n".join([
        json.dumps({"qid": "Q1", "language": "ja", "period": "2020-plus", "priority_dimensions": ["japanese", "fantasy"]}),
        json.dumps({"qid": "Q2", "language": "Q999", "period": "unknown", "priority_dimensions": []}),
    ]) + "\n", encoding="utf-8")
    result = inventory.build_inventory_v2_from_v1(source_root=source, output_root=tmp_path / "v2")
    assert result["available_supply"]["eligible_unprocessed_recommendable"] == 2
    assert result["gap_counts"]["eligible_unprocessed_recommendable"]["japanese"] == 1
    assert result["gap_counts"]["language_counts"]["eligible_unprocessed_recommendable"]["unknown"] == 1
    assert result["gap_counts"]["country_region"]["unassessed"] == 2
