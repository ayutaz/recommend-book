import importlib.util
import json
from pathlib import Path
import hashlib
import sqlite3
from types import SimpleNamespace

from recommend_book.models import Evidence, Identifier, SourceRecord, Title


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "collect_scale_content_window_v1", ROOT / "scripts/collect_scale_content_window_v1.py"
)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(collector)

AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_scale_content_windows_v1", ROOT / "scripts/audit_scale_content_windows_v1.py"
)
audit_module = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC.loader is not None
AUDIT_SPEC.loader.exec_module(audit_module)


def test_existing_qid_ledger_includes_held_started_and_records(tmp_path):
    root = tmp_path / "scale"
    window = root / "content-window-0001-0100"
    window.mkdir(parents=True)
    (window / "outcomes.json").write_text(
        json.dumps([
            {"qid": "Q1", "status": "held", "reason": "no_screened_narrative"},
            {"qid": "Q2", "status": "accepted"},
        ]), encoding="utf-8"
    )
    (window / "selections.json").write_text(
        json.dumps([{"qid": "Q3", "status": "selected"}, {"qid": "Q4", "status": "held", "reason": "no_supported_sitelink"}]),
        encoding="utf-8",
    )
    (window / "records.jsonl").write_text(
        json.dumps({"identifiers": [{"source": "wikidata", "scope": "work", "value": "Q5"}]}),
        encoding="utf-8",
    )
    assert collector.existing_content_qids(root) == {"Q1", "Q2", "Q3", "Q4", "Q5"}


def test_audit_reports_duplicate_outcomes_and_missing_legacy_selection_artifact(tmp_path):
    root = tmp_path / "scale"
    window = root / "content-window-0001-0100"
    window.mkdir(parents=True)
    (window / "manifest.json").write_text(json.dumps({
        "selected_authorities": 3, "accepted_records": 1, "network_requests": 2
    }), encoding="utf-8")
    (window / "outcomes.json").write_text(json.dumps([
        {"qid": "Q1", "status": "accepted"},
        {"qid": "Q1", "status": "held", "reason": "retry"},
    ]), encoding="utf-8")
    result = audit_module.audit(root)
    assert result["outcome_rows"] == 2
    assert result["outcome_distinct_qids"] == 1
    assert result["outcome_duplicate_qids"] == 1
    assert result["outcome_max_repeat"] == 2
    assert result["manifest_network_requests"] == 2
    assert result["selection_artifacts_missing"] == 1
    assert result["selection_artifact_gaps"][0]["unrecorded_selection_count"] == 1


def _authority(qid: str, *, title: str, language: str | None = "en") -> SourceRecord:
    sitelinks = {} if language is None else {f"{language}wiki": {"site": f"{language}wiki", "title": title}}
    return SourceRecord(
        source="wikidata", source_id=qid, source_url=f"https://www.wikidata.org/wiki/{qid}",
        retrieved_at="2026-10-20T00:00:00+00:00", provider_id="wikidata", record_kind="work",
        titles=[Title(value=title, language=language)],
        identifiers=[Identifier(source="wikidata", value=qid, scope="work")],
        original_language=language, first_publication_year=2000, work_kind="novel",
        raw_sha256=hashlib.sha256(qid.encode()).hexdigest(), raw_path=f"authority-{qid}.json",
        rights_policy_id="wikidata-cc0", extra={"sitelinks": sitelinks},
    )


def _catalogue(path: Path, records: list[SourceRecord], native: set[str] = set()):
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE source_versions(
                version_id TEXT PRIMARY KEY,qid TEXT NOT NULL,record_sha256 TEXT NOT NULL,
                record_json TEXT NOT NULL,raw_path TEXT NOT NULL,raw_sha256 TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,conditions_sha256 TEXT NOT NULL);
            CREATE TABLE works(
                qid TEXT PRIMARY KEY,version_id TEXT NOT NULL,title TEXT NOT NULL,
                search_text TEXT NOT NULL,language TEXT,year INTEGER,
                recommendable INTEGER NOT NULL,display_allowed INTEGER NOT NULL,row_json TEXT NOT NULL);
            CREATE TABLE native_classifications(qid TEXT PRIMARY KEY,manifest_path TEXT NOT NULL,
                manifest_sha256 TEXT NOT NULL,labels_json TEXT NOT NULL,provenance_json TEXT NOT NULL);
        """)
        for record in records:
            version = f"wd:{record.source_id}:v1"
            payload = record.model_dump_json()
            db.execute("INSERT INTO source_versions VALUES (?,?,?,?,?,?,?,?)", (
                version, record.source_id, hashlib.sha256(payload.encode()).hexdigest(), payload,
                record.raw_path, record.raw_sha256, record.retrieved_at, "conditions"))
            db.execute("INSERT INTO works VALUES (?,?,?,?,?,?,?,?,?)", (
                record.source_id, version, record.titles[0].value, record.titles[0].value,
                record.original_language, record.first_publication_year, 1, 1, payload))
        for qid in native:
            db.execute("INSERT INTO native_classifications VALUES (?,?,?,?,?)", (qid, "native.json", "sha", "{}", "{}"))


def test_collect_path_excludes_held_started_native_and_no_sitelink_and_uses_shared_single_attempt_store(tmp_path, monkeypatch):
    records = [
        _authority("Q1", title="Held Novel"), _authority("Q2", title="Started Novel"),
        _authority("Q3", title="Native Novel"), _authority("Q4", title="No Sitelink", language=None),
        _authority("Q5", title="Fresh Novel"), _authority("Q6", title="Fresh Japanese", language="ja"),
        _authority("Q7", title="Second Fresh Novel"),
    ]
    catalogue = tmp_path / "catalogue.sqlite"
    _catalogue(catalogue, records, native={"Q3"})
    # Excluded rows must not force loading/parsing their large authority JSON.
    # If select_authorities regresses to a full record_json scan, this causes a
    # deterministic validation error before the fresh candidates are reached.
    with sqlite3.connect(catalogue) as db:
        db.execute("UPDATE source_versions SET record_json='not-json' WHERE qid IN ('Q1','Q2','Q3')")
    root = tmp_path / "scale"
    old = root / "content-window-old"
    old.mkdir(parents=True)
    (old / "outcomes.json").write_text(json.dumps([{"qid": "Q1", "status": "held"}]), encoding="utf-8")
    (old / "selections.json").write_text(json.dumps([{"qid": "Q2", "status": "selected"}]), encoding="utf-8")

    selected = collector.select_authorities(catalogue, root, 2)
    assert [record.source_id for record in selected] == ["Q5", "Q6"]

    calls = []

    def fake_build_evidence(authority, page, artifact, language):
        evidence = Evidence(
            evidence_id=f"wp:{authority.source_id}:plot", kind="synopsis", text=f"plot {authority.source_id}",
            source_url=f"https://{language}.wikipedia.org/w/index.php?oldid={page['revisions'][0]['revid']}",
            rights_policy_id="wikipedia-screened-prose", llm_input_allowed=True, language=language,
            locator="Plot", license="CC-BY-SA-4.0", license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution={"credit": f"Wikipedia contributors: {page['title']}"},
        )
        return [evidence], {"admitted": True}, {"Plot": {"text": evidence.text}}

    class FakeFetcher:
        stores = []

        def __init__(self, store, **kwargs):
            self.store = store
            self.kwargs = kwargs
            FakeFetcher.stores.append(self)

        def get(self, url, *, source, params):
            assert (root / "content-window-1" / "selections.json").exists()
            titles = params["titles"].split("|")
            pages = []
            for index, title in enumerate(titles, start=1):
                qid = next(record.source_id for record in records if any(t.value == title for t in record.titles))
                pages.append({"pageid": index, "title": title, "pageprops": {"wikibase_item": qid},
                              "revisions": [{"revid": 1000 + index, "timestamp": "2026-10-20T00:00:00Z", "slots": {"main": {"content": "plot"}}}]})
            payload = json.dumps({"query": {"pages": pages}}).encode()
            digest = hashlib.sha256(payload).hexdigest()
            artifact = SimpleNamespace(sha256=digest, path=str(self.store.root / "wikipedia" / f"{digest}.bin"),
                                       size=len(payload), retrieved_at="2026-10-20T00:00:00+00:00", source_url=url)
            calls.append((url, params, self.store.root))
            return SimpleNamespace(status="success", attempts=1, artifact=artifact, content=payload, error=None)

    monkeypatch.setattr(collector, "HTTPFetcher", FakeFetcher)
    monkeypatch.setattr(collector, "build_evidence", fake_build_evidence)
    first = root / "content-window-1"
    manifest = collector.collect(catalogue=catalogue, output=first, limit=2)
    assert manifest["accepted_records"] == 2
    assert manifest["http_max_attempts"] == 1
    assert manifest["shared_raw_store"].endswith("shared-content-fetch-v1")
    assert FakeFetcher.stores[0].kwargs["max_attempts"] == 1
    assert len(calls) == 2
    assert all(root / "content-window-1" / "selections.json" for _ in calls)
    assert {json.loads(line)["identifiers"][0]["value"] for line in (first / "records.jsonl").read_text(encoding="utf-8").splitlines()} == {"Q5", "Q6"}

    second = root / "content-window-2"
    collector.collect(catalogue=catalogue, output=second, limit=1)
    assert len(FakeFetcher.stores) == 2
    assert FakeFetcher.stores[0].store.root == FakeFetcher.stores[1].store.root
    assert FakeFetcher.stores[1].kwargs["max_attempts"] == 1
