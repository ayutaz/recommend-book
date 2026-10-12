import json
import subprocess
import sys


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "recommend_book", *map(str, args)], capture_output=True, text=True, encoding="utf-8")


def test_help_describes_pipeline():
    result = run_cli("--help")
    assert result.returncode == 0, result.stderr
    assert "classify" in result.stdout and "evaluate" in result.stdout


def test_ingest_and_stats_preserve_source_record(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"source": "wikidata", "source_id": "QTEST", "source_url": "https://www.wikidata.org/wiki/QTEST", "retrieved_at": "2026-10-12T00:00:00Z", "provider_id": "wikidata", "record_kind": "work", "titles": [{"value": "Fixture work"}], "contributors": [{"name": "Fixture author"}], "identifiers": [{"source": "wikidata", "value": "QTEST", "scope": "work"}], "raw_sha256": "0" * 64, "raw_path": "fixture.json", "rights_policy_id": "wikidata-cc0"}) + "\n", encoding="utf-8")
    database = tmp_path / "dataset.sqlite"
    result = run_cli("ingest", "--input", source, "--database", database)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["inserted"] == 1
    again = run_cli("ingest", "--input", source, "--database", database)
    assert again.returncode == 0, again.stderr
    assert json.loads(again.stdout)["unchanged"] == 1
    stats = run_cli("stats", "--database", database)
    assert stats.returncode == 0, stats.stderr
    assert json.loads(stats.stdout)["works"] == 1
