import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path


def module():
    path = Path(__file__).parents[1] / "scripts/prepare_scale_content_v1.py"
    spec = importlib.util.spec_from_file_location("prepare_scale_content_v1", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def write_json(path, value):
    path.write_bytes(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")


def fixture(tmp_path):
    m = module()
    authority_raw = tmp_path / "authority.bin"
    authority_raw.write_bytes(b"saved wikidata entity bytes")
    records = []
    source_versions = []
    works = []
    for number in (1, 2):
        qid = f"Q{number}"
        raw = tmp_path / f"article-{number}.json"
        raw.write_text(json.dumps({"query": {"pages": [{
            "pageid": 1000 + number, "title": f"Book {number}",
            "pageprops": {"wikibase_item": qid},
            "revisions": [{"revid": 1230 + number}]
        }]}}, ensure_ascii=False), encoding="utf-8")
        record = {
            "source": "wikipedia", "source_id": f"en:123{number}:wikipedia-prose-v2",
            "source_url": f"https://en.wikipedia.org/w/index.php?oldid=123{number}",
            "retrieved_at": "2026-10-20T00:00:00+00:00", "provider_id": "wikipedia", "record_kind": "work",
            "titles": [{"value": f"Book {number}", "language": "en", "kind": "preferred"}],
            "contributors": [{"name": f"Author {number}", "role": "author", "identifiers": []}],
            "identifiers": [{"source": "wikidata", "value": qid, "scope": "work"}],
            "work_refs": [], "original_language": "en", "language": None,
            "first_publication_year": 1900 + number, "publication_year": None, "publisher": None,
            "series": None, "volume": None, "work_kind": "novel", "region": [], "subjects": [],
            "evidence": [{"evidence_id": f"wp:{number}", "kind": "synopsis", "text": "A detailed plot summary with a central character and a sustained setting. The account follows a conflict, its consequences, and the protagonist's changing choices across the narrative.",
                          "source_url": f"https://en.wikipedia.org/w/index.php?oldid=123{number}",
                          "rights_policy_id": "wikipedia-screened-prose", "llm_input_allowed": True,
                          "language": "en", "locator": f"revision:123{number};section:Plot summary;screen:wikipedia-prose-v2",
                          "license": None, "license_url": None, "attribution": {}}],
            "contents": [], "raw_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(), "raw_path": str(raw),
            "rights_policy_id": "wikipedia-screened-prose", "extra": {
                "page_id": 1000 + number, "revision_id": 1230 + number,
                "article_title": f"Book {number}",
                "text_license": "CC-BY-SA-4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "attribution": {"credit": "Wikipedia contributors"},
                "fulfilled_obligations": ["attribution", "share_alike", "mark_changes"]
            }
        }
        records.append(record)
        authority = {"source": "wikidata", "source_id": qid, "source_url": f"https://www.wikidata.org/wiki/{qid}",
                     "retrieved_at": "2026-10-20T00:00:00+00:00", "provider_id": "wikidata", "record_kind": "work",
                     "titles": [{"value": f"Book {number}", "language": "en", "kind": "preferred"}], "contributors": [],
                     "identifiers": [{"source": "wikidata", "value": qid, "scope": "work"}], "work_refs": [],
                     "raw_sha256": hashlib.sha256(authority_raw.read_bytes()).hexdigest(), "raw_path": str(authority_raw),
                     "rights_policy_id": "wikidata-cc0"}
        source_versions.append((f"wd:{qid}:record", qid, m.sha(tmp_path / "authority.bin"), json.dumps(authority), str(authority_raw), m.sha(authority_raw)))
        works.append((qid, json.dumps({"qid": qid, "title": f"Book {number}", "authors": [{"qid": f"A{number}", "name": f"Author {number}"}], "original_language": "en", "year": 1900 + number, "regions": []})))
    source = tmp_path / "records.jsonl"; source.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in records))
    catalog = tmp_path / "catalogue.sqlite"
    with sqlite3.connect(catalog) as db:
        db.executescript("CREATE TABLE works(qid TEXT PRIMARY KEY,row_json TEXT,recommendable INTEGER,display_allowed INTEGER); CREATE TABLE source_versions(version_id TEXT PRIMARY KEY,qid TEXT,record_sha256 TEXT,record_json TEXT,raw_path TEXT,raw_sha256 TEXT,retrieved_at TEXT,conditions_sha256 TEXT); CREATE TABLE native_classifications(qid TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO works VALUES(?,?,1,1)", works)
        db.executemany("INSERT INTO source_versions VALUES(?,?,?,?,?,?,?,?)", [(v, q, rs, rj, rp, rh, "2026-10-20", "cond") for v,q,rs,rj,rp,rh in source_versions])
    cfg = tmp_path / "sources.json"
    write_json(cfg, {"rights_policies": [{"policy_id": "wikipedia-screened-prose", "source": "wikipedia", "provider_id": "wikipedia", "fields": ["synopsis"], "license": "CC-BY-SA-4.0", "usages": {"external_llm": "conditional"}}]})
    official = tmp_path / "official.html"; official.write_text("official condition", encoding="utf8")
    ledger = tmp_path / "ledger.json"
    write_json(ledger, {"official_conditions": {"wikipedia-screened-prose": [{"source_url": "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use", "final_url": "https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use", "raw_path": str(official), "raw_sha256": m.sha(official), "verified_at": "2026-10-20"}]}})
    return m, catalog, source, cfg, ledger


def test_prepares_qid_bound_chunks_and_conditional_rights(tmp_path):
    m, catalog, source, cfg, ledger = fixture(tmp_path)
    report = m.prepare(catalogue_path=catalog, source_path=source, output=tmp_path / "out", limit=2, chunk_size=1, rights_path=ledger, config_path=cfg)
    assert report["selected_count"] == 2 and report["chunk_count"] == 2
    assert report["execution_state"] == "prepared_not_started" and report["network_requests"] == 0
    inputs = [json.loads(line) for line in (tmp_path / "out/content-inputs.jsonl").read_text().splitlines()]
    assert [row["work_id"] for row in inputs] == ["wd:Q1", "wd:Q2"]
    assert all(row["evidence"][0]["rights_policy_id"] == "wikipedia-screened-prose" for row in inputs)
    assert all(row["evidence"][0]["license"] == "CC-BY-SA-4.0" for row in inputs)
    assert all(row["evidence"][0]["attribution"]["credit"] == "Wikipedia contributors" for row in inputs)
    assert all(row["evidence"][0]["attribution"]["identity_proof"]["wikidata_work_id"] in {"Q1", "Q2"} for row in inputs)
    receipt = json.loads((tmp_path / "out/rights-receipt.json").read_text())
    assert receipt["usage_decisions"]["external_llm"].startswith("conditional_allowed")
    assert receipt["usage_decisions"]["display"].startswith("conditional_hold")


def test_rejects_changed_saved_raw_bytes(tmp_path):
    m, catalog, source, cfg, ledger = fixture(tmp_path)
    raw = tmp_path / "article-1.json"; raw.write_bytes(b"changed")
    try:
        m.prepare(catalogue_path=catalog, source_path=source, output=tmp_path / "out", limit=2, rights_path=ledger, config_path=cfg)
    except ValueError as exc:
        assert "admitted content works" in str(exc)
    else:
        raise AssertionError("changed content raw bytes must not be admitted")
