"""Operational snapshot tests; these generated fixtures are not annotation gold."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import sqlite3

import pytest

from recommend_book.models import WorkInput


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "freeze_poc_inputs.py"
SPEC = importlib.util.spec_from_file_location("freeze_poc_inputs", SCRIPT)
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


def works(count, language, offset=0):
    return [WorkInput(work_id=f"fixture-{i}", title=f"Fixture {i}",
                      original_language=language, first_publication_year=1850 + i % 170)
            for i in range(offset, offset + count)]


def test_pilot_preserves_other_language_coverage_before_filling_year_cells():
    languages = ['ar', 'bn', 'de', 'es', 'fr', 'hi', 'it', 'ko', 'pt', 'ru', 'zh', None]
    inputs = works(70, 'ja') + works(70, 'en', 100)
    for index, language in enumerate(languages):
        years = [1850, 1930, 1980, 2020, None]
        inputs.extend(work.model_copy(update={'first_publication_year': years[n % 5]})
                      for n, work in enumerate(works(40, language, 1000 + index * 100)))
    pilot, deficits = freeze.pilot_inputs(inputs)
    assert len(pilot) == 100
    assert all(value == 0 for value in deficits.values())
    assert {work.original_language for work in pilot} == {'en', 'ja', *languages}


def identity_database():
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE works(work_id TEXT, canonical_id TEXT);
        CREATE TABLE contributions(agent_id TEXT, work_id TEXT, role TEXT, source_version_id TEXT);
        CREATE TABLE work_relations(work_id TEXT, series_id TEXT, relation_type TEXT, source_version_id TEXT);
        CREATE TABLE external_ids(source TEXT, value TEXT, normalized_value TEXT, scope TEXT, entity_id TEXT, source_version_id TEXT);
    """)
    return connection


def test_identity_sidecar_uses_author_series_and_external_links():
    connection = identity_database()
    connection.executemany("INSERT INTO works VALUES(?,?)", [("source-a", "a"), ("b", "b"), ("c", "c")])
    connection.executemany("INSERT INTO contributions VALUES(?,?,?,?)", [
        ("agent-a", "source-a", "author", "va"), ("agent-b", "b", "author", "vb"),
        ("shared-editor", "c", "editor", "vc"), ("shared-translator", "b", "translator", "vb")])
    connection.executemany("INSERT INTO work_relations VALUES(?,?,?,?)", [
        ("b", "series-b", "series_member", "vb"), ("c", "series-c", "series_member", "vc")])
    connection.executemany("INSERT INTO external_ids VALUES(?,?,?,?,?,?)", [
        ("wikidata", "Q-author", "Q-author", "agent", "agent-a", "va"),
        ("wikidata", "Q-author", "Q-author", "agent", "agent-b", "vb"),
        ("wikidata", "Q-series", "Q-series", "series", "b", "vb"),
        ("wikidata", "Q-series", "Q-series", "series", "series-c", "vc"),
        ("wikidata", "Q-unrelated", "Q-unrelated", "work", "b", "vb")])
    identities = freeze.group_identity_tokens(connection, ["a", "b", "c"])
    assert "author:db:agent-a" in identities["a"]
    assert "series:db:series-b" in identities["b"]
    assert set(identities["a"]) & set(identities["b"])
    assert set(identities["b"]) & set(identities["c"])
    assert not any("shared-editor" in token or "shared-translator" in token or "Q-unrelated" in token
                   for values in identities.values() for token in values)
    assert all(values == sorted(set(values)) for values in identities.values())
    connection.close()


def test_pilot_has_100_distinct_works_and_order_independent_sampling():
    inputs = works(70, "ja") + works(70, "en", 70) + works(60, "fr", 140)
    selected, deficits = freeze.pilot_inputs(inputs)
    again, _ = freeze.pilot_inputs(list(reversed(inputs)))
    ids = [work.work_id for work in selected]
    assert len(ids) == len(set(ids)) == 100
    assert ids == [work.work_id for work in again]
    assert {lang: sum(work.original_language == lang for work in selected)
            for lang in ("ja", "en", "fr")} == {"ja": 35, "en": 35, "fr": 30}
    assert deficits == {"ja": 0, "en": 0, "other": 0}
    with pytest.raises(ValueError, match="distinct"):
        freeze.pilot_inputs(inputs + [inputs[0]])


def test_quota_deficit_is_redistributed_without_duplicates():
    inputs = works(2, "ja") + works(98, "en", 2) + works(10, "fr", 100)
    selected, deficits = freeze.pilot_inputs(inputs)
    assert len(selected) == len({work.work_id for work in selected}) == 100
    assert sum(work.original_language == "ja" for work in selected) == 2
    assert deficits == {"ja": 33, "en": 0, "other": 20}
    assert {work.work_id for work in selected} <= {work.work_id for work in inputs}


@pytest.mark.parametrize("size", [0, -1])
def test_pilot_rejects_nonpositive_size(size):
    with pytest.raises(ValueError, match="positive"):
        freeze.pilot_inputs(works(100, "en"), size=size)


def test_freeze_excludes_expression_rows_and_preserves_existing_snapshot(tmp_path, monkeypatch):
    base = works(120, "en")
    expression = base[0].model_copy(update={"expression_id": "fixture-translation"})
    with pytest.raises(ValueError, match="expression-specific"):
        freeze.pilot_inputs([expression, *base[1:]])

    class DatabaseFixture:
        def __init__(self, path):
            self.path = path
            self.connection = identity_database()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.connection.close()
            return False

        def work_inputs(self):
            return [*base, expression]

        def stats(self):
            return {"test_fixture": True}

    output = tmp_path / "frozen"
    monkeypatch.setattr(freeze, "DatasetDB", DatabaseFixture)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--database", str(tmp_path / "fixture.sqlite"),
                                      "--output", str(output)])
    freeze.main()
    snapshot = {file.name: file.read_bytes() for file in output.iterdir()}
    rows = [json.loads(line) for line in snapshot["work-inputs.jsonl"].splitlines()]
    pilot = [json.loads(line) for line in snapshot["pilot100.jsonl"].splitlines()]
    manifest = json.loads(snapshot["manifest.json"])
    assert len(rows) == 120 and all(row["expression_id"] is None for row in rows)
    assert len(pilot) == len({row["work_id"] for row in pilot}) == 100
    assert {row["work_id"] for row in pilot} <= {row["work_id"] for row in rows}
    assert manifest["work_inputs_sha256"] == hashlib.sha256(snapshot["work-inputs.jsonl"]).hexdigest()
    assert manifest["pilot_inputs_sha256"] == hashlib.sha256(snapshot["pilot100.jsonl"]).hexdigest()
    assert manifest["group_identities_sha256"] == hashlib.sha256(snapshot["group-identities.json"]).hexdigest()
    assert set(json.loads(snapshot["group-identities.json"])) == {row["work_id"] for row in rows}
    assert manifest["human_quality_verified"] is False

    def unexpected_database_open(*args):
        pytest.fail("Existing snapshot must be rejected before opening the database")

    monkeypatch.setattr(freeze, "DatasetDB", unexpected_database_open)
    with pytest.raises(ValueError, match="immutable"):
        freeze.main()
    assert {file.name: file.read_bytes() for file in output.iterdir()} == snapshot
