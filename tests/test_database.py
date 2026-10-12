import json
import sqlite3

import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Contributor, Evidence, Identifier, Title
from test_resolution import record


def sql(db, query):
    with sqlite3.connect(db.path) as connection:
        return connection.execute(query).fetchall()


def test_idempotence_raw_versions_provenance_and_rights(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    original = record("a", titles=[Title(value="Ｓｎｏｗ　２")])
    assert db.ingest([original])["inserted"] == 1
    assert db.ingest([original])["unchanged"] == 1
    assert db.stats()["works"] == 1
    assert db.stats()["source_records"] == 1
    assert json.loads(sql(db, "SELECT record_json FROM source_records")[0][0])["titles"][0]["value"] == "Ｓｎｏｗ　２"
    assert sql(db, "SELECT rights_policy_id, source_url FROM field_assertions")
    with sqlite3.connect(db.path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE source_records SET record_json='{}'")


def test_work_expression_edition_and_translator_separation(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    ref = Identifier(source="ol", value="W1", scope="work")
    original = record("work", identifiers=[ref])
    editions = [record(f"edition-{name}", record_kind="edition", work_refs=[ref], language="ja", contributors=[Contributor(name="Author"), Contributor(name=name, role="translator")], identifiers=[Identifier(source="isbn", value=isbn, scope="edition")]) for name, isbn in [("T1", "9780306406157"), ("T2", "9780804429573")]]
    db.ingest([original, *editions])
    assert db.stats()["works"] == 1
    assert db.stats()["editions"] == 2
    assert db.stats()["expressions"] == 3
    assert len(sql(db, "SELECT * FROM edition_contents")) == 2
    assert set(sql(db, "SELECT DISTINCT scope FROM external_ids")) == {("work",), ("edition",)}


def test_isbn_and_title_never_merge_works(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    isbn = Identifier(source="isbn", value="9780306406157", scope="edition")
    db.ingest([record("a", identifiers=[isbn]), record("b", identifiers=[isbn]), record("c", contributors=[Contributor(name="Other")])])
    assert db.stats()["works"] == 3
    assert db.review_queue()


def test_short_story_anthology_contents_and_series_volumes(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    story_id = Identifier(source="ol", value="story", scope="work")
    anthology_id = Identifier(source="ol", value="anthology", scope="work")
    story = record("story", work_kind="short_story", identifiers=[story_id])
    anthology = record("anthology", work_kind="anthology", identifiers=[anthology_id])
    edition = record("edition", record_kind="edition", work_kind="anthology", work_refs=[anthology_id], contents=[story_id], language="en")
    db.ingest([story, anthology, edition, record("v1", series="Series", volume="1"), record("v2", series="Series", volume="2")])
    assert db.stats()["works"] == 4
    assert len(sql(db, "SELECT * FROM edition_contents")) == 2
    assert len(sql(db, "SELECT * FROM work_relations WHERE relation_type='series_member'")) == 2


def test_conflicting_external_id_creates_review_and_does_not_merge(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    identifier = Identifier(source="ol", value="W1", scope="work")
    db.ingest([record("a", identifiers=[identifier]), record("b", identifiers=[identifier], contributors=[Contributor(name="Other")])])
    assert db.stats()["works"] == 2
    assert any("author" in row["reason"] for row in db.review_queue())


def test_record_update_and_undo_preserve_immutable_versions(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    db.ingest([record("a")])
    db.ingest([record("a", titles=[Title(value="Changed")], raw_sha256="new")])
    assert db.stats()["works"] == 1
    assert db.stats()["source_records"] == 2
    version = sql(db, "SELECT version_id FROM source_heads")[0][0]
    assert db.work_inputs()[0].title == "Changed"
    db.undo_ingest(version)
    assert db.work_inputs()[0].title == "Snow"
    assert db.stats()["source_records"] == 2


def test_merge_undo_rebuilds_without_deleting_originals(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    db.ingest([record("a"), record("b")])
    ids = [item.work_id for item in db.work_inputs()]
    decision = db.merge_work(ids[0], ids[1], reason="Human confirmed source attribution", reviewer="reviewer")
    assert db.stats()["works"] == 1
    db.undo(decision)
    assert db.stats()["works"] == 2
    assert db.stats()["source_records"] == 2
    assert len(sql(db, "SELECT * FROM works")) == 2


def test_undo_auto_merge_prevents_silent_remerge(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    identifier = Identifier(source="ol", value="W1", scope="work")
    db.ingest([record("a", identifiers=[identifier]), record("b", identifiers=[identifier])])
    assert db.stats()["works"] == 1
    decision = sql(db, "SELECT decision_id FROM resolution_decisions WHERE status='same' AND active=1")[0][0]
    db.undo(decision)
    assert db.stats()["works"] == 2
    db.ingest([record("c", titles=[Title(value="Unrelated")])])
    assert db.stats()["works"] == 3


def test_evidence_permissions_and_expression_scope_retained(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    evidence = [Evidence(evidence_id=key, kind="synopsis", text=key, source_url="https://example.test/e", rights_policy_id="policy", llm_input_allowed=allowed) for key, allowed in [("allowed", True), ("unresolved", False)]]
    db.ingest([record("a", evidence=evidence)])
    assert [item.evidence_id for item in db.work_inputs()[0].evidence] == ["allowed"]
    snapshot = tmp_path / "snapshot.json"
    db.export(snapshot)
    assert "unresolved" in snapshot.read_text(encoding="utf-8")


def test_unlinked_edition_is_retained_but_not_classification_ready(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    db.ingest([record("a", record_kind="edition", original_language=None)])
    assert db.stats()["unresolved_works"] == 1
    assert db.work_inputs() == []
    assert db.review_queue()


def test_refresh_timestamp_does_not_duplicate_content_version(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    db.ingest([record("a")])
    result = db.ingest([record("a", retrieved_at="2026-10-13")])
    assert result["unchanged"] == 1
    assert db.stats()["source_records"] == 1


def test_replayed_old_content_undo_restores_previous_head(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    a = record("a")
    b = record("a", titles=[Title(value="Changed")], raw_sha256="new")
    db.ingest([a])
    db.ingest([b])
    db.ingest([a])
    version = sql(db, "SELECT version_id FROM source_heads")[0][0]
    db.undo_ingest(version)
    assert db.work_inputs()[0].title == "Changed"


def test_transitive_merge_undo_keeps_rejected_pair_separate(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    identifier = Identifier(source="ol", value="W1", scope="work")
    db.ingest([record(key, identifiers=[identifier]) for key in ("a", "b", "c")])
    decision, left, right = sql(db, "SELECT decision_id,left_work_id,right_work_id FROM resolution_decisions WHERE status='same' LIMIT 1")[0]
    db.undo(decision)
    canonical = dict(sql(db, "SELECT work_id,canonical_id FROM works"))
    assert canonical[left] != canonical[right]


def test_update_conflicting_identity_splits_then_undo_rejoins(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    identifier = Identifier(source="ol", value="W1", scope="work")
    db.ingest([record("a", identifiers=[identifier]), record("b", identifiers=[identifier])])
    db.ingest([record("b", identifiers=[identifier], contributors=[Contributor(name="Other")], raw_sha256="new")])
    assert db.stats()["works"] == 2
    version = sql(db, "SELECT version_id FROM source_heads WHERE source_id='b'")[0][0]
    db.undo_ingest(version)
    assert db.stats()["works"] == 1


def test_unknown_expression_ref_is_reviewed_and_not_sent_to_classifier(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    evidence = Evidence(evidence_id="style", kind="excerpt", text="text", source_url="https://example.test/e", rights_policy_id="policy", llm_input_allowed=True, expression_ref="unknown-expression")
    db.ingest([record("a", evidence=[evidence])])
    assert all(not item.evidence for item in db.work_inputs())
    assert any("expression" in item["reason"] for item in db.review_queue())


def test_agent_authority_aliases_corroborate_cross_source_work_ids(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    wiki_author = Identifier(source="wikidata", value="QAUTHOR", scope="agent")
    ol_author = Identifier(source="openlibrary", value="/authors/OL1A", scope="agent")
    work_ref = Identifier(source="openlibrary", value="/works/OL1W", scope="work")
    authority = record("QAUTHOR", record_kind="agent", titles=[Title(value="Author")], identifiers=[wiki_author, ol_author], contributors=[])
    wiki = record("wiki", identifiers=[work_ref], contributors=[Contributor(name="Author", identifiers=[wiki_author])])
    ol = record("ol", identifiers=[work_ref], contributors=[Contributor(name="/authors/OL1A", identifiers=[ol_author])])
    db.ingest([wiki, ol, authority])
    assert db.stats()["works"] == 1
    assert db.stats()["agents"] == 1
    assert db.work_inputs()[0].authors == ["Author"]
    assert sql(db, "SELECT * FROM field_assertions WHERE source_version_id=(SELECT version_id FROM source_heads WHERE source_id='QAUTHOR')")


def test_same_author_name_different_authority_ids_does_not_merge(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    work_ref = Identifier(source="ol", value="W1", scope="work")
    db.ingest([record(key, identifiers=[work_ref], contributors=[Contributor(name="Same name", identifiers=[Identifier(source="authority", value=key, scope="agent")])]) for key in ("a", "b")])
    assert db.stats()["works"] == 2


def test_crosslink_bridge_cannot_hide_conflicting_work_authority_ids(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    common = Identifier(source="ol", value="W1", scope="work")
    a = record("a", identifiers=[common, Identifier(source="wikidata", value="Q1", scope="work")])
    b = record("b", identifiers=[common])
    c = record("c", identifiers=[common, Identifier(source="wikidata", value="Q2", scope="work")])
    db.ingest([a, b, c])
    assert db.stats()["works"] == 2


def test_migration_three_indexes_work_evidence_and_preserves_history(tmp_path):
    path = tmp_path / "books.sqlite"
    db = DatasetDB(path)
    db.ingest([record("a")])
    db.ingest([record("a", titles=[Title(value="Changed")], raw_sha256="new")])
    versions = sql(db, "SELECT version_id,record_json FROM source_records ORDER BY version_id")
    # Simulate a saved v2 DB, including immutable history and replay events.
    with db.connection:
        db.connection.execute("DROP INDEX IF EXISTS evidence_work_scope_order")
        db.connection.execute("DROP INDEX IF EXISTS works_resolved_canonical")
        db.connection.execute("DELETE FROM schema_migrations WHERE version=3")
        db.connection.execute("PRAGMA user_version=2")
    db.close()
    with DatasetDB(path) as reopened:
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 3
        plans = [row[3] for row in reopened.connection.execute("EXPLAIN QUERY PLAN SELECT expression_id,evidence_json FROM evidence WHERE work_id=? AND scope_valid=1 ORDER BY evidence_key", ("work",))]
        assert any("SEARCH" in plan and "evidence_work_scope_order" in plan for plan in plans)
        assert not any("TEMP B-TREE" in plan for plan in plans)
        assert sql(reopened, "SELECT version_id,record_json FROM source_records ORDER BY version_id") == versions
        active = reopened.connection.execute("SELECT version_id FROM source_heads").fetchone()[0]
        reopened.undo_ingest(active)
        assert reopened.work_inputs()[0].title == "Snow"


def test_stats_does_not_build_work_inputs_to_count_ready_works(tmp_path, monkeypatch):
    db = DatasetDB(tmp_path / "books.sqlite")
    allowed = Evidence(evidence_id="allowed", kind="subject", text="subject", source_url="https://example.test", rights_policy_id="policy", llm_input_allowed=True)
    denied = allowed.model_copy(update={"evidence_id": "denied", "llm_input_allowed": False})
    invalid_scope = allowed.model_copy(update={"evidence_id": "bad", "expression_ref": "unknown-expression"})
    db.ingest([record("a", evidence=[allowed]), record("b", evidence=[denied]), record("c", evidence=[invalid_scope]), record("d", record_kind="edition", evidence=[allowed])])
    ready = len({item.work_id for item in db.work_inputs() if item.evidence})
    assert ready == 1

    def forbidden_load():
        raise AssertionError("stats must not construct all WorkInputs")

    monkeypatch.setattr(db, "work_inputs", forbidden_load)
    assert db.stats()["classification_ready_works"] == ready


def test_work_inputs_reads_evidence_once_for_multiple_work_ids(tmp_path):
    db = DatasetDB(tmp_path / "books.sqlite")
    allowed = Evidence(evidence_id="allowed", kind="subject", text="subject", source_url="https://example.test", rights_policy_id="policy", llm_input_allowed=True)
    db.ingest([record(str(index), titles=[Title(value=f"Work {index}")], evidence=[allowed]) for index in range(6)])
    queries = []
    db.connection.set_trace_callback(queries.append)
    try:
        inputs = db.work_inputs()
    finally:
        db.connection.set_trace_callback(None)
    assert len(inputs) == 6
    evidence_queries = [query for query in queries if query.lstrip().upper().startswith("SELECT") and "FROM evidence" in query]
    assert len(evidence_queries) == 1
