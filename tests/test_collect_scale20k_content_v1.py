"""Read-only preflight tests for the v20 content collector."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("collect_scale20k_content_v1", ROOT / "scripts/collect_scale20k_content_v1.py")
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)


def test_preflight_selects_exact_current_sitelink_without_network(tmp_path, monkeypatch):
    db_path = tmp_path / "catalogue.sqlite"
    db = sqlite3.connect(db_path)
    db.executescript("""
      CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT NOT NULL, title TEXT NOT NULL,
        search_text TEXT NOT NULL, language TEXT, year INTEGER, recommendable INTEGER NOT NULL,
        display_allowed INTEGER NOT NULL, row_json TEXT NOT NULL);
      CREATE TABLE source_versions(version_id TEXT PRIMARY KEY, qid TEXT NOT NULL,
        record_sha256 TEXT NOT NULL, record_json TEXT NOT NULL, raw_path TEXT NOT NULL,
        raw_sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL, conditions_sha256 TEXT NOT NULL);
    """)
    raw = tmp_path / "authority.bin"
    raw.write_bytes(b"authority")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    record = {
        "source": "wikidata", "source_id": "Q1", "source_url": "https://www.wikidata.org/wiki/Q1", "retrieved_at": "2026-10-20", "provider_id": "wikidata", "record_kind": "work", "work_kind": "novel",
        "titles": [{"value": "Example", "kind": "preferred"}], "evidence": [{"evidence_id": "wd:Q1:description", "kind": "description", "text": "novel", "source_url": "https://www.wikidata.org/wiki/Q1", "rights_policy_id": "wikidata-cc0", "llm_input_allowed": True}],
        "identifiers": [{"source": "wikidata", "scope": "work", "value": "Q1"}], "extra": {"sitelinks": {"enwiki": {"title": "Example"}}},
        "raw_path": str(raw), "raw_sha256": raw_sha, "rights_policy_id": "wikidata-cc0",
    }
    payload = json.dumps(record, ensure_ascii=False)
    record_sha = hashlib.sha256(payload.encode()).hexdigest()
    db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)", ("Q1", "v1", "Example", "Example", "en", 2000, 1, 1, "{}"))
    db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", ("v1", "Q1", record_sha, payload, str(raw), raw_sha, "2026-10-20", "c"))
    db.commit(); db.close()
    inventory = tmp_path / "inventory.jsonl"
    inventory.write_text(json.dumps({"qid": "Q1", "title": "Example", "priority_score": 5, "record_sha256": record_sha, "raw_sha256": raw_sha}) + "\n", encoding="utf-8")
    wd_raw = tmp_path / "wikidata.html"
    wd_raw.write_bytes(b"wikidata")
    wp_raw = tmp_path / "tou.html"
    wp_raw.write_bytes(b"tou")
    cc_raw = tmp_path / "cc.html"
    cc_raw.write_bytes(b"cc")
    def receipt(url, path):
        return {"source_url": url, "final_url": url, "raw_path": str(path), "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "verified_at": "2026-10-20T00:00:00+00:00", "status_code": 200}
    conditions = tmp_path / "conditions.json"
    conditions.write_text(json.dumps({"wikidata-cc0": [receipt("https://www.wikidata.org/wiki/Wikidata:Licensing", wd_raw)], "wikipedia-screened-prose": [receipt("https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use", wp_raw), receipt("https://creativecommons.org/licenses/by-sa/4.0/", cc_raw)]}), encoding="utf-8")
    registry = tmp_path / "registry.json"
    policies = [{"policy_id": p, "usages": {u: "allowed" for u in ("acquire", "local_storage", "external_llm")}} for p in ("wikidata-cc0", "wikipedia-screened-prose")]
    registry.write_text(json.dumps({"rights_policies": policies}), encoding="utf-8")
    monkeypatch.setattr(collector, "_old_attempts", lambda: {"QOLD"})
    monkeypatch.setattr(collector, "REGISTRY", registry)
    document = collector.build_preflight(catalogue=db_path, inventory=inventory, conditions=conditions,
                                         output=tmp_path / "window", limit=1,
                                         now=datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert document["selected_qids"] == ["Q1"]
    assert document["model_calls"] == 0
    assert document["network_requests"] == 0
    assert document["http_policy"]["max_attempts"] == 1


def test_preflight_rejects_stale_rights_before_any_selection(tmp_path, monkeypatch):
    # Keep this guard independent from the ignored production registry and
    # receipts.  The fixture contains every required policy/URL, with only
    # the verification timestamp deliberately outside the seven-day window.
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"rights_policies": [
        {"policy_id": "wikidata-cc0", "usages": {purpose: "allowed" for purpose in ("acquire", "local_storage", "external_llm")}},
        {"policy_id": "wikipedia-screened-prose", "usages": {purpose: "allowed" for purpose in ("acquire", "local_storage", "external_llm")}},
    ]}), encoding="utf-8")
    receipts = {}
    for policy_id, urls in {
        "wikidata-cc0": ["https://www.wikidata.org/wiki/Wikidata:Licensing"],
        "wikipedia-screened-prose": [
            "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use",
            "https://creativecommons.org/licenses/by-sa/4.0/",
        ],
    }.items():
        rows = []
        for index, url in enumerate(urls):
            raw = tmp_path / f"{policy_id}-{index}.html"
            raw.write_bytes(f"fixture:{policy_id}:{index}".encode())
            rows.append({"source_url": url, "final_url": url,
                         "raw_path": str(raw),
                         "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                         "verified_at": "2026-10-01T00:00:00+00:00",
                         "status_code": 200})
        receipts[policy_id] = rows
    conditions = tmp_path / "conditions.json"
    conditions.write_text(json.dumps(receipts), encoding="utf-8")
    monkeypatch.setattr(collector, "REGISTRY", registry)
    with pytest.raises(ValueError, match="rights receipt is stale"):
        collector._rights_gate(conditions, datetime(2026, 10, 22, tzinfo=timezone.utc))


def test_unknown_source_genre_mode_allows_display_novel_without_recommendable(tmp_path, monkeypatch):
    db_path = tmp_path / "catalogue.sqlite"
    raw = tmp_path / "authority.bin"
    raw.write_bytes(b"authority")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    record = {
        "source": "wikidata", "source_id": "QUNKNOWN", "source_url": "https://www.wikidata.org/wiki/QUNKNOWN",
        "retrieved_at": "2026-10-20", "provider_id": "wikidata", "record_kind": "work", "work_kind": "novel",
        "titles": [{"value": "Unknown genre novel", "kind": "preferred"}],
        "identifiers": [{"source": "wikidata", "scope": "work", "value": "QUNKNOWN"}],
        "evidence": [], "extra": {"sitelinks": {"enwiki": {"title": "Unknown genre novel"}, "jawiki": {"title": "資料に対応する小説"}}},
        "raw_path": str(raw), "raw_sha256": raw_sha, "rights_policy_id": "wikidata-cc0",
    }
    payload = json.dumps(record, ensure_ascii=False)
    record_sha = hashlib.sha256(payload.encode()).hexdigest()
    db = sqlite3.connect(db_path)
    db.executescript("""
      CREATE TABLE works(qid TEXT PRIMARY KEY, version_id TEXT, title TEXT, search_text TEXT,
        language TEXT, year INTEGER, recommendable INTEGER, display_allowed INTEGER, row_json TEXT);
      CREATE TABLE source_versions(version_id TEXT PRIMARY KEY, qid TEXT, record_sha256 TEXT,
        record_json TEXT, raw_path TEXT, raw_sha256 TEXT, retrieved_at TEXT, conditions_sha256 TEXT);
    """)
    db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)", ("QUNKNOWN", "wd:QUNKNOWN:v1", "Unknown genre novel", "Unknown genre novel", "en", 1980, 0, 1, "{}"))
    db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", ("wd:QUNKNOWN:v1", "QUNKNOWN", record_sha, payload, str(raw), raw_sha, "2026-10-20", "c"))
    db.commit(); db.close()
    inventory = tmp_path / "unknown.jsonl"
    inventory.write_text(json.dumps({
        "qid": "QUNKNOWN", "source_version_id": "wd:QUNKNOWN:v1", "record_sha256": record_sha,
        "raw_sha256": raw_sha, "supported_sitelinks": {"ja": "資料に対応する小説"},
        "source_genre": "unknown", "source_genre_recommendable": False,
    }) + "\n", encoding="utf-8")
    selected, counts = collector.select_authorities(
        catalogue=db_path, inventory=inventory, limit=1, excluded=set(),
        inventory_mode="unknown-source-genre-content")
    assert [item.source_id for item in selected] == ["QUNKNOWN"]
    assert counts["selected"] == 1
    with pytest.raises(ValueError, match="only 0 eligible"):
        collector.select_authorities(catalogue=db_path, inventory=inventory, limit=1, excluded=set())
    calls = []
    class StubFetcher:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, url, **kwargs):
            from types import SimpleNamespace
            calls.append((url, kwargs['params']['titles']))
            return SimpleNamespace(status='failed', status_code=403, error='fixture-only stop', attempts=1)

    monkeypatch.setattr(collector, '_rights_gate', lambda *_args: {'fixture': True})
    monkeypatch.setattr(collector, '_old_attempts', lambda: set())
    monkeypatch.setattr(collector, 'SHARED_RAW_STORE', tmp_path / 'shared')
    monkeypatch.setattr('recommend_book.ingestion.core.HTTPFetcher', StubFetcher)
    result = collector.execute_window(catalogue=db_path, inventory=inventory, output=tmp_path / 'window',
                                      limit=1, inventory_mode='unknown-source-genre-content')
    assert calls == [('https://ja.wikipedia.org/w/api.php', '資料に対応する小説')]
    assert result['status'] == 'terminal_hold'
    assert result['network_requests'] == 1
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT recommendable FROM works').fetchone()[0] == 0


def test_old_attempts_reads_pretty_json_and_jsonl_across_v18_v20(tmp_path, monkeypatch):
    v19 = tmp_path / "v19"
    v20 = tmp_path / "v20"
    window = v19 / "content" / "content-window-0001-0100"
    window.mkdir(parents=True)
    (window / "selections.json").write_text(json.dumps({"selections": [{"qid": "Q10", "status": "selected"}, {"qid": "Q11", "status": "held"}]}, indent=2), encoding="utf-8")
    (window / "outcomes.json").write_text(json.dumps([{"qid": "Q12", "status": "held", "reason": "no_sitelink"}], indent=2), encoding="utf-8")
    (window / "source-pins.jsonl").write_text(json.dumps({"qid": "Q13"}) + "\n", encoding="utf-8")
    v20_window = v20 / "content-acquisition-v1" / "window-0001-0100"
    v20_window.mkdir(parents=True)
    (v20_window / "selections.json").write_text(json.dumps({"rows": [{"qid": "Q14", "status": "selected"}]}, indent=2), encoding="utf-8")
    monkeypatch.setattr(collector, "V19", v19)
    monkeypatch.setattr(collector, "V20", v20)
    assert {"Q10", "Q11", "Q12", "Q13", "Q14"}.issubset(collector._old_attempts())


def test_old_attempts_uses_fixed_union_then_only_incremental_v20_ledger(tmp_path, monkeypatch):
    v19 = tmp_path / "v19"
    v20 = tmp_path / "v20"
    (v20 / "content-acquisition-v1").mkdir(parents=True)
    (v20 / "content-acquisition-v1" / "old-attempt-index-v1.json").write_text(json.dumps({"qids": ["QFIX"]}), encoding="utf-8")
    window = v20 / "content-acquisition-v1" / "window-0001-0100"
    window.mkdir()
    (window / "selections.json").write_text(json.dumps({"selections": [{"qid": "QNEW"}]}), encoding="utf-8")
    old = v19 / "content-window-9999-0000"
    old.mkdir(parents=True)
    (old / "selections.json").write_text(json.dumps([{"qid": "QSHOULD_NOT_BE_RESCANNED"}]), encoding="utf-8")
    monkeypatch.setattr(collector, "V19", v19)
    monkeypatch.setattr(collector, "V20", v20)
    found = collector._old_attempts()
    assert {"QFIX", "QNEW"}.issubset(found)
    assert "QSHOULD_NOT_BE_RESCANNED" not in found


def test_native_projection_keeps_wikidata_authority_and_content_pins(tmp_path):
    pytest.importorskip("pydantic")
    db_path = tmp_path / "catalogue.sqlite"
    authority_raw = tmp_path / "authority.bin"
    content_raw = tmp_path / "article.bin"
    authority_raw.write_bytes(b"authority")
    content_raw.write_bytes(b"article")
    authority_sha = hashlib.sha256(authority_raw.read_bytes()).hexdigest()
    content_sha = hashlib.sha256(content_raw.read_bytes()).hexdigest()
    authority = {
        "source": "wikidata", "source_id": "Q1", "source_url": "https://www.wikidata.org/wiki/Q1",
        "retrieved_at": "2026-10-20", "provider_id": "wikidata", "record_kind": "work", "work_kind": "novel",
        "titles": [{"value": "Example", "kind": "preferred"}],
        "identifiers": [{"source": "wikidata", "scope": "work", "value": "Q1"}],
        "evidence": [{"evidence_id": "wd:Q1:description", "kind": "description", "text": "novel", "source_url": "https://www.wikidata.org/wiki/Q1", "rights_policy_id": "wikidata-cc0", "llm_input_allowed": True}],
        "raw_path": str(authority_raw), "raw_sha256": authority_sha, "rights_policy_id": "wikidata-cc0", "extra": {},
    }
    payload = json.dumps(authority, ensure_ascii=False)
    record_sha = hashlib.sha256(payload.encode()).hexdigest()
    db = sqlite3.connect(db_path)
    db.executescript("CREATE TABLE works(qid TEXT PRIMARY KEY,version_id TEXT,title TEXT,search_text TEXT,language TEXT,year INTEGER,recommendable INTEGER,display_allowed INTEGER,row_json TEXT); CREATE TABLE source_versions(version_id TEXT PRIMARY KEY,qid TEXT,record_sha256 TEXT,record_json TEXT,raw_path TEXT,raw_sha256 TEXT,retrieved_at TEXT,conditions_sha256 TEXT);")
    db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)", ("Q1", "v1", "Example", "Example", "en", 2000, 1, 1, "{}"))
    db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", ("v1", "Q1", record_sha, payload, str(authority_raw), authority_sha, "2026-10-20", "c"))
    db.commit(); db.close()
    content = {
        "source": "wikipedia", "source_id": "en:2:screened-paragraphs-v1", "source_url": "https://en.wikipedia.org/w/index.php?oldid=2",
        "retrieved_at": "2026-10-20", "provider_id": "wikipedia", "record_kind": "work", "work_kind": "novel",
        "titles": [{"value": "Example", "kind": "preferred"}], "identifiers": [{"source": "wikidata", "scope": "work", "value": "Q1"}],
        "evidence": [{"evidence_id": "wp:en:2:synopsis", "kind": "synopsis", "text": "Plot", "source_url": "https://en.wikipedia.org/", "rights_policy_id": "wikipedia-screened-prose", "llm_input_allowed": True, "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": {"permalink": "https://en.wikipedia.org/w/index.php?oldid=2"}}],
        "raw_path": str(content_raw), "raw_sha256": content_sha, "rights_policy_id": "wikipedia-screened-prose", "extra": {"revision_id": 2, "page_id": 3, "article_language": "en"},
    }
    records = tmp_path / "records.jsonl"
    records.write_text(json.dumps(content) + "\n", encoding="utf-8")
    (tmp_path / "source-pins.jsonl").write_text(json.dumps({"qid": "Q1", "ledger": [{"evidence_id": "wp:en:2:synopsis", "source_url": content["source_url"]}]}) + "\n", encoding="utf-8")
    out = tmp_path / "native"
    handoff = collector.build_native_records(content_paths=[records], catalogue=db_path, output_dir=out, limit=1, allowed_qids={"Q1"})
    projected = json.loads((out / "native-records.jsonl").read_text(encoding="utf-8"))
    assert projected["source"] == "wikidata" and projected["source_id"] == "Q1"
    assert any(item["evidence_id"] == "wp:en:2:synopsis" for item in projected["evidence"])
    assert projected["extra"]["scale_content_supported_v1"]["authority_version_id"] == "v1"
    assert projected["extra"]["scale_content_supported_v1"]["content_raw_sha256"] == content_sha
    assert handoff["selected"] == 1 and handoff["model_calls"] == 0
